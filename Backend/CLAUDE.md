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
| Alembic 数据库迁移 · 投影/蓝图解析（litemapy / nbtlib）+ 中文翻译 | .litematic / .nbt 文件存档（仅即时解析、不持久化） |

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
| **RS-8** | 双通道 `get_current_player`（api/deps.py） | Web 走 `Authorization: Bearer <jwt>`；MCDR 走 `X-Service-Token` + `X-Player-UUID` 代理（`secrets.compare_digest` 校验后查 Player 注入）。**业务层零改动**——RBAC 基于 `Player`，与凭证来源无关。`/sheets/export` 与 `/notifications/*` 仍独占 `require_service_token`（无身份）。**H-2**：Authorization 头存在（即便非 Bearer/过期/非法）只走 JWT 通道报 401，**绝不静默降级**到 service-token。代理命中后落 `service_token_proxy` 审计日志（H-1'，不含 token）。 |
| **RS-9** | notification-service 契约入口（services/notification_service.py） | 发通知统一走 `notify(session, ...)`，**必须在调用方写端点同一事务的同一 session 内调用**（R-10：业务改库 + 记通知原子，回滚则通知不落库）。`category` 用 String 按 `<domain>_<event>` 扩展；`Notifier` Protocol 预留 Webhook/Discord 扩展点。**C-1**：`mark_delivered/mark_read` 必须带 `recipient_uuid`，SQL WHERE 限定归属，防越权 ack/read 他人；**M-2/M-3**：入口对 title(≤200)/body(≤500) 限长 + 控制字符清洗、payload 序列化 >8KB 截断。详见 [`Docs/architecture/services/notification-service.md`](../Docs/architecture/services/notification-service.md)。 |
| **RS-10** | sheet 项目阶段生命周期（迁移 0009） | `sheets.sheets.status` ∈ collecting/constructing/archived；**archived = 终态只读**——repo `_assert_writable(session, sheet_id)` 是所有写操作的统一守卫入口（archived → `SheetArchived` → api 409），含 advance/行级 upsert/claim/delivery/contribute/release/reject/progress/删行/删表。`advance_sheet` 用 `SELECT ... FOR UPDATE` 锁行 + 状态机校验（合法：collecting→constructing / collecting→archived / constructing→archived；幂等 `to==当前` → `SheetRowConflict`）。归档经 `services/archive/archive_sheet()`：渲染 md → matplotlib 渲染 `contributions.png`（PNG 贡献占比饼图，CJK 字体 **Noto Sans CJK SC**，需容器装此字体；≤5 人全显 / >5 人 top5+其他）→ `write_atomic` 原子写盘（**事务外**）→ DB 置 archived 三字段 + `notify(category="sheet_archived")` → 内部 commit；**失败 cleanup 孤儿文件 + rollback**。**归档产物 = 每项目独立文件夹** `ARCHIVE_ROOT/projects/{id}/`：`index.md`（归档正文，去逐行材料清单，section 含 📦/🏆/📊/📅 + footer）+ `contributions.png`；`archived_path` 存相对 `ARCHIVE_ROOT` 的 POSIX 路径 `projects/{id}/index.md`。**wiki git publisher**（默认 off，best-effort）：归档成功后 wiki-service 把 `projects/<id>/` 整目录 `git commit + push` 到独立 wiki 内容 git 仓（subprocess git，token 内嵌 push URL 不落盘；失败仅 `notify(category="wiki_publish_failed")`，不抛、不回滚 DB——业务库是权威源）。config 加 `WIKI_GIT_REMOTE_URL`/`WIKI_GIT_BRANCH`/`WIKI_GIT_TOKEN`/`WIKI_GIT_AUTHOR_NAME`/`WIKI_GIT_AUTHOR_EMAIL`，空 `REMOTE_URL` = 不推送。**asset 端点** `GET /sheets/{id}/archive/assets/{filename}` 读 `projects/{id}/{filename}` 返 `image/png`（basename 白名单 + 路径穿越守卫 → 404；鉴权 `get_current_player`）。`archived_path` 是 wiki-service 同步入口（R-8 重写后为 git 双向）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §4.1 / §5.2、[`data-model.md`](../Docs/architecture/data-model.md) §10.4、[`services/wiki-service.md`](../Docs/architecture/services/wiki-service.md)。 |
| **RS-11** | markdown_render Route C 抽象（services/markdown_render/） | 通用结构化 markdown 渲染：`SectionRenderer` Protocol（`@runtime_checkable`，与 `Notifier` 同范式）+ `TemplateSection`/`FunctionSection`（`@dataclass(frozen=True)`）+ `MarkdownDocument`（frozen；`register` 返回新对象 + 同名 override + 按 order 有序聚合，`render` 过滤空白）。**不可变**（项目编码规范）。零依赖（不引 Jinja2）。保留 PromptStore 的「不可变 register + 有序聚合」风格，**抛弃** template 调度 / dispatch / WILD_CARD / body-fallback / `{placeholder}` 引擎（结构化 md 渲染无对应用例）。首个消费者是 sheet 归档服务；未来榜单/报表注册不同 section 集合即可复用。详见 [`services/markdown-service.md`](../Docs/architecture/services/markdown-service.md)。 |

---

## 4. 关键要素

### 入口与结构
- 入口：`app/main.py`（FastAPI app + 路由挂载）
- 路由：`app/api/*.py`（`auth` / `me` / `sheets` / `notifications` / `parsing`）
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
| `GET /notifications/pending` | MCDR 轮询拉取未投递通知（service-token，query `player_uuid`） |
| `POST /notifications/ack` | 批量标**该 player_uuid 名下**通知投递（service-token，body `{player_uuid, ids:[…]}`，C-1 防越权） |
| `POST /notifications/{id}/read` | 标已读（service-token，query `player_uuid` 归属校验，跨玩家 404；L-2 同步幂等置 delivered_at） |
| `POST /parsing/litematic` | Web 上传 `.litematic` → litemapy 解析 + 中文翻译 → 分组预览（不落库）。详见 [`api/parsing.md`](../Docs/architecture/api/parsing.md) |
| `POST /parsing/nbt` | Web 上传 `.nbt`（Create 蓝图 / 原版 structure）→ nbtlib 解析 + 中文翻译 → 分组预览（不落库）。详见 [`api/parsing.md`](../Docs/architecture/api/parsing.md) |
| `POST /sheets/from-items` | 一次性建表 + 批量行（`mode` 默认 lock），用于「投影解析→生成表格」 |
| `POST /sheets/{id}/advance?to=` | 项目阶段流转（owner/admin，缺省按状态机推进；`to=archived` 走归档服务写盘+通知）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §5.2 |
| `GET /sheets/{id}/archive` | 读归档 markdown（`text/markdown`；未归档/文件缺失 → 404） |
| `GET /sheets/{id}/archive/assets/{filename}` | 读归档资产（如 `contributions.png` 贡献占比饼图，`image/png`；basename 白名单 + 路径穿越守卫 → 404；任意登录玩家可读） |
| sheets CRUD + 协作 | `GET/POST/PATCH/DELETE /sheets*`（`GET` 支持 `?status=collecting\|constructing\|archived\|active` 过滤）+ 行级 `claim`/`delivery`/`contribute`/`release`/`reject`/`progress`（JWT 或 service-token+UUID 代玩家写）—— 全套端点见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) |

### 数据表（users schema）
- `players`：玩家主身（UUID + current_name + role + whitelist_state）
- `auth_tokens`：一次性登录 token（含 `expires_at` / `used_at` / `revoked_at`）
- `jwt_revocations`：JWT 吊销表（refresh 接入待办）

### 数据表（notifications schema）
- `notifications`：统一通知记录（recipient_uuid FK→users.players.uuid ON DELETE CASCADE / category / title / body / payload jsonb / created_at / delivered_at / read_at；索引 `(recipient_uuid, delivered_at)`）

### 数据表（sheets schema）
- `sheets`：表格主表（owner_uuid FK / title / `status` collecting\|constructing\|archived（迁移 0009）/ archived_path / archived_at / created_at / updated_at；双 CHECK `ck_sheets_status_*` + `ix_sheets_status`）
- `sheet_rows`：行（sheet_id FK CASCADE / item_name / need_qty / `mode` 0=lock|1=progress / `status` open|claimed|done / claimant_uuid / delivered_qty / sort_order；`UNIQUE(sheet_id, item_name)`）
- `sheet_row_contributors`：progress 行贡献者聚合（row_id FK CASCADE / player_uuid FK / joined_at / contributed_qty；`UNIQUE(row_id, player_uuid)`；迁移 0007/0008）

> 完整 DDL 见 [`Docs/architecture/data-model.md`](../Docs/architecture/data-model.md) §2（users）/ §10（sheets，含 §10.4 项目阶段状态机 + 归档产物结构）/ §11（notifications）。归档产物落盘 `ARCHIVE_ROOT/projects/{id}/`（`index.md` + `contributions.png`，每项目独立文件夹；config `archive_root` + `WIKI_GIT_*`），渲染见 [`services/markdown-service.md`](../Docs/architecture/services/markdown-service.md)，wiki 推送见 [`services/wiki-service.md`](../Docs/architecture/services/wiki-service.md)。

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

*最后更新：2026-07-03（归档升级：贡献者聚合含 lock + 去材料清单 + 每项目独立文件夹 `projects/{id}/` + matplotlib 贡献占比饼图（Noto Sans CJK SC）+ asset 端点 + wiki git publisher 默认 off/best-effort + R-8 重写为 git 双向；RS-10 更新）*

*增量（2026-07-07）：§4 端点表补 `POST /parsing/nbt`（Create 蓝图解析，commit f16a00a 遗漏，借前端 .nbt 支持 #5 一并补齐）；§2 职责泛化 litemapy / nbtlib。*
