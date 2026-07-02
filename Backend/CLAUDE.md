# 后端 FastAPI 模块化单体 · CLAUDE.md

> 本文件是后端整体导航。待拆分为 `user_service/` 等子目录后，各子服务 CLAUDE.md 由 `service-claude-md` skill 接管（根 [`CLAUDE.md`](../CLAUDE.md) §4 已规划）。
> 全局统一规范见根 [`CLAUDE.md`](../CLAUDE.md)。

---

## 1. 服务定位

FastAPI 模块化单体：单库单服务，内部按 schema 隔离（`users / projects / scoring / titles / wiki / alerts`）。是 PCHSystem 的**唯一业务数据拥有者**（根红线 R-1），所有业务读写集中于此。

> 完整架构：[`Docs/architecture/`](../Docs/architecture/)（各服务 `services/*.md`）

---

## 2. 职责边界

| 管 | 不管 |
|---|---|
| 全部业务数据读写（PostgreSQL 独占） | 游戏内命令交互（MCDR 管） |
| RBAC 权限判定（真实权限源） | 前端展示逻辑（前端只控可见性，R-9） |
| JWT 签发与校验、一次性 token 软失效 | wiki.js 内容存储（只单向同步，R-8） |
| Alembic 数据库迁移 | 投影解析（litemapy 在调用方） |

---

## 3. 雷点·红线（服务特有）

> 全局红线见根 CLAUDE.md §3（R-1~R-12）。此处只列**本服务特有**或对本服务**特别需要强调**的约束。

| # | 红线 | 说明 |
|---|---|---|
| **RS-1** | 遵守 R-1 数据唯一拥有者 | 后端独占 PostgreSQL 读写；MCDR / 前端只走 HTTP API，不直连数据库。 |
| **RS-2** | 遵守 R-2 积分流水 append-only | `score_ledger` 禁止 UPDATE/DELETE；任何积分变动记一条含 `balance_after`。 |
| **RS-3** | 遵守 R-10 模块化单体 | 单一 FastAPI 服务，schema 隔离，不拆独立子服务；跨表事务用单库事务。 |
| **RS-4** | 遵守 R-11 密钥经环境变量 | `POSTGRES_*` / `JWT_SECRET` / `MCDR_SERVICE_TOKEN` 经 `.env` 注入，不进代码库。 |
| **RS-5** | 一次性 token 软失效（soft revoke） | `issue()` 签发新 token 前先 revoke 同 UUID 未使用旧 token（`revoked_at` 置位）；兑换校验 `revoked_at is null`。保留审计痕迹，不硬删。 |
| **RS-6** | RateLimiter 单进程内存实现（MVP） | 当前 `auth_service.rate_limiter` 是进程内字典，多 worker 下失效；生产前需迁 Redis。 |
| **RS-7** | 异步一致性 | SQLAlchemy 2.x async + `pytest-asyncio`；阻塞 IO（如调外部 API）必放 `asyncio` 任务或线程池，不阻塞事件循环。 |

---

## 4. 关键要素

### 入口与结构
- 入口：`app/main.py`（FastAPI app + 路由挂载）
- 路由：`app/api/*.py`（`auth` / `me` 已实现）
- 数据层：`app/models/`（SQLAlchemy 2.x）+ `app/repositories/`（repo 函数，不返回 ORM 对象给路由层）
- 配置：`app/core/config.py`（pydantic-settings，`auth_token_ttl_seconds` 等）
- 迁移：`alembic/versions/`
- 测试：`tests/`（pytest，AAA 结构）

### 关键接口（已实现）
| 端点 | 用途 |
|---|---|
| `POST /auth/token` | MCDR 调用（带 `X-Service-Token`），签发一次性登录 token；响应含 `login_url` / `expires_in` / `previous_tokens_revoked` |
| `POST /auth/exchange` | 前端调用，一次性 token → JWT pair（access + refresh） |
| `POST /auth/refresh` | refresh token 续签 access |
| `GET /me` | 当前身份（需 Bearer JWT） |

### 数据表（users schema）
- `players`：玩家主身（UUID + current_name + role + whitelist_state）
- `auth_tokens`：一次性登录 token（含 `expires_at` / `used_at` / `revoked_at`）
- `jwt_revocations`：JWT 吊销表（refresh 接入待办）

> 完整 DDL 见 [`Docs/architecture/data-model.md`](../Docs/architecture/data-model.md)。

---

## 5. 开发热重载工作流

> 容器编排见根 [`docker-compose.yml`](../docker-compose.yml)（postgres + backend）。
> **backend 容器已配置源码挂载 + `uvicorn --reload`，改 `.py` 文件无需 rebuild 镜像。**

| 改动类型 | 操作 | 生效方式 |
|---|---|---|
| `app/**/*.py` 源码 | 保存即可 | uvicorn `--reload` 自动重启（docker-compose 挂载 `./Backend/app:/app/app`，监听 `/app/app`） |
| `alembic/versions/*.py` 迁移 | `docker compose exec backend alembic upgrade head` | 手动执行（新增迁移后必跑） |
| `pyproject.toml` 加依赖 | `docker compose build backend && docker compose up -d backend` | rebuild 镜像（仅依赖变更才需要） |
| 跑测试 | `cd Backend && pytest tests/ -v` | 宿主机直接跑（依赖本地 venv）或 `docker compose exec backend pytest` |

### 首次启动 / 配置变更后
```bash
docker compose up -d                                # 启动 postgres + backend
docker compose exec backend alembic upgrade head    # 跑迁移到最新
```

### 验证 backend 健康
```bash
curl -sS http://localhost:8000/me                   # 应返回 401（未带 JWT，证明服务在跑）
```

### 常见排错
- **改了代码但行为没变**：确认 `pchsystem-backend-1` 容器在跑且未挂；`docker logs pchsystem-backend-1 --tail 30` 看 uvicorn 是否收到 reload 事件
- **响应字段缺失（如 `previous_tokens_revoked` 不存在）**：说明容器跑的是旧镜像，需 `docker compose up -d backend --force-recreate` 重建
- **迁移报错 `Target database is not up to date`**：先 `alembic current` 看版本，再 `alembic upgrade head`

---

## 6. 文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| 工程架构总览 | [`../Docs/architecture.md`](../Docs/architecture.md) | 三端架构、ADR、跨服务流程 |
| 数据模型 | [`../Docs/architecture/data-model.md`](../Docs/architecture/data-model.md) | 全部表结构与约束 |
| 各服务架构 | [`../Docs/architecture/services/`](../Docs/architecture/services/) | user/project/scoring/title/wiki/alert 各服务文档 |
| 根规范 | [`../CLAUDE.md`](../CLAUDE.md) | 统一命名 / 红线 / 索引 |

---

## 7. 与根规范的关系

- 遵守根 [`CLAUDE.md`](../CLAUDE.md) 的命名分层（§1：变量/方法 snake_case、类 PascalCase、SQL 表列 snake_case）与全局红线（§3 R-1~R-12）。
- 本文件的 RS-x 红线是**服务特有**补充，不覆写全局红线。
- 待后端拆分为 `user_service/` 等子目录后，本文件职责下放给各子服务 CLAUDE.md（由 `service-claude-md` skill 生成）。

---

*最后更新：2026-07-02（后端整体导航；热重载工作流基于根 docker-compose.yml 源码挂载 + uvicorn --reload）*
