# 运维手册（RUNBOOK）

> HTCMC PCHSystem · **dev / staging / 服主自部署 运维手册**，聚焦**部署 / 排错 / 回滚**流程。
> 日常高频**指令清单**见 [`Cheatsheets/dev-cheatsheet.md`](./Cheatsheets/dev-cheatsheet.md)；服主一键部署的**完整选项 / 边界**见 [`../Scripts/README.md`](../Scripts/README.md)（本文只摘要）。

---

## 1. 前置依赖

- **Docker + Docker Compose v2**（根编排 + TestServer 测试服；服主可由 `install.sh` 自动安装）
- Python 3.11+（后端本地调试 / MCDR 插件）
- Node.js 18+（前端本地开发；服主走 web 容器则免装）
- **Minecraft Fabric 服务端（Fabric + Create + Carpet，离线模式）** —— 仅联调 MCDR 时需要；离线模式是身份体系前提（R-5：UUID 由玩家名推导）

---

## 2. 拓扑与端口

| 组件 | 来源 | 端口 / 路径 | 备注 |
|---|---|---|---|
| backend（FastAPI） | 根 `docker-compose.yml` | `${BACKEND_PORT:-8000}:8000` | dev 源码挂载 + `uvicorn --reload`；生产 override 去 reload + 加 healthcheck |
| postgres:16 | 根 `docker-compose.yml` | `127.0.0.1:${PG_PORT:-5433}` → 5432 | 仅本机可达；数据落 `pgdata` volume |
| **web（nginx 托管前端）** | 根 `docker-compose.yml` | `${WEB_PORT:-5173}:80` | `profile=web` **默认启用**（`.env` `COMPOSE_PROFILES=web`）；托管 `Frontend/dist` + 反代 `/api/`→`backend:8000`；禁用改空 profile |
| 前端 dev server | 宿主机 `npm run dev` | `http://localhost:5173` | 仅本地开发；Vite `/api/*` 代理到 `:8000` |
| mc-test（MCDR + Fabric） | `TestServer/docker-compose.yml` | `25565`（MC）/ `25575`（RCON） | `container_name: pchsystem-mc-test-1`；以 external 网络加入 `pchsystem_default`，容器内访问 `http://pchsystem-backend-1:8000` |

> wiki.js **规划中**，尚未纳入 compose（见根 [`CLAUDE.md`](../CLAUDE.md) §2 / §7）。

---

## 3. 部署流程

### 3.1 服主 / 生产一键（推荐）

仓库内执行 `Scripts/install.sh`（首次）/ `Scripts/update.sh`（更新）。自动检测/安装 Docker、自适应国内网络（GitHub / Docker Hub / PyPI / npm 镜像）、智能重建镜像、token 双写校验。**完整选项与边界见 [`../Scripts/README.md`](../Scripts/README.md)。**

```bash
git clone https://github.com/YuShenLiu06/PCHSystem.git && cd PCHSystem
bash Scripts/install.sh          # 交互式首次安装（询问 MCDR 路径等）
bash Scripts/update.sh           # 之后日常更新
```

> 必须在仓库内执行；安装 Docker 需免密 sudo 或 root，装完 `newgrp docker` 后重跑（幂等）。

**`install.sh` 关键选项**：`--edge`（拉 main 最新，默认拉最新发版 tag）/ `--yes`（无人值守）/ `--mcdr-root DIR` / `--mcdr-api-url URL` / `--no-web`（禁用 web 容器，走宿主 nginx）/ `--no-mcdr` / `--no-sync`。
**做了什么**：装 Docker → 选镜像 → 同步版本 → 生成 `.env`（**已存在绝不覆盖**；新装用 `openssl rand` 填三密钥）→ 生产 override → 起容器等 `/healthz` 200 → `alembic upgrade head`（迁移前 `pg_dump`）→ 前端构建 → 拷 `pch_system` 插件并双写 token → 持久化 `.pchsystem.deploy.env` + 摘要。

**`update.sh` 智能重建矩阵**（按 `git diff` 路径决定，避免无谓 rebuild）：

| 变更 | 动作 |
|---|---|
| `Backend/Dockerfile` / `pyproject.toml` | `up -d --build backend` |
| 仅 `Backend/app/**` / `alembic/**` | `up -d --force-recreate backend`（秒级，源码已挂载） |
| `Frontend/**`（web 激活时） | 重建 `web` 镜像（dist 烘焙进镜像） |
| `docker-compose.yml` / override | `up -d`（自动 recreate） |
| 无 backend / compose / frontend 变更 | 跳过容器操作 |

选项：`--edge` / `--yes` / `--force`（接管非脚本部署 / 跳过 dirty 保护）/ `--frontend`（强制重建前端）/ `--no-mcdr`。

**安全保障（红线对齐）**：迁移前 `pg_dump`；迁移失败**绝不自动 downgrade**（`score_ledger` append-only，R-2），只给手动恢复指引；本地有跟踪文件改动时拒跑（dirty 保护）；健康检查失败**不自动回滚**。

### 3.2 本地 dev 栈（手工）

```bash
cp .env.example .env          # 至少改 POSTGRES_PASSWORD / JWT_SECRET / MCDR_SERVICE_TOKEN
docker compose up -d          # 起 postgres + backend（+ web，因 .env 默认 COMPOSE_PROFILES=web）
docker compose exec backend alembic upgrade head
curl -sS http://localhost:8000/healthz     # → {"status":"ok"}
```

**热重载速查**（详情见各子服务 CLAUDE.md §开发热重载工作流）：

| 改动 | 生效方式 |
|---|---|
| `Backend/app/**/*.py` | uvicorn `--reload` 自动重启 |
| `alembic/versions/*.py` | 手动 `alembic upgrade head` |
| `Backend/pyproject.toml` / `Dockerfile` | `docker compose up -d --build backend` |
| `Frontend/**`（web 容器） | `docker compose up -d --build web` |
| 前端 `.vue`/`.ts`（dev server） | Vite HMR |
| MCDR 插件 `.py` | 游戏内 `!!MCDR plugin reload pch_system` |

### 3.3 测试服（mc-test）

> ⚠ **必须先起根栈**（§3.2）——mc-test 以 `external: true` 引用 `pchsystem_default` 网络，根栈不先起会报 network not found。

```bash
docker compose -f TestServer/docker-compose.yml up -d   # 首次下 ~300MB MC 文件
docker logs pchsystem-mc-test-1 2>&1 | grep pch_system  # 确认插件加载
```

---

## 4. 健康检查

| 检查 | 命令 | 期望 |
|---|---|---|
| backend 存活 | `curl -sS http://localhost:8000/healthz` | `{"status":"ok"}` |
| backend 鉴权链路 | `curl -sS http://localhost:8000/me` | `401`（未带 JWT，证明路由在跑） |
| 迁移版本 | `docker compose exec backend alembic current` | 与 `Backend/alembic/versions/` 最新一致（当前 `0013_qty_per_unit_float`） |
| web 前端 | 浏览器开 `http://localhost:5173` | `/auth` 页可走 token 兑换 |
| mc-test 插件 | `docker logs pchsystem-mc-test-1 2>&1 \| grep pch_system` | 命令注册 + help message |

---

## 5. 排错

运维级排错见下表；**代码级调试**（reload 未触发、拦截器、插件语法等）见各子服务 CLAUDE.md。

| 症状 | 排查 / 修复 |
|---|---|
| backend `/healthz` 不通 | `docker compose logs --tail 80 backend`；postgres 未 healthy 会因 `depends_on` 拖住 backend |
| `Target database is not up to date` | `alembic current` → `alembic upgrade head` |
| 前端所有请求 502 / ECONNREFUSED | backend 没起 → `curl :8000/healthz` 确认 |
| `!!PCH login` 链接打不开 | 核对 `.env` `WEB_BASE_URL`（login 回链前缀）与前端实际地址 |
| token 401 | `.env` `MCDR_SERVICE_TOKEN` 与插件 `config/pch_system/config.json` `service_token` 必须同值（轮换流程见 [`../Scripts/README.md`](../Scripts/README.md) §8） |
| 空白 `MCDR_SERVICE_TOKEN` | 后端启动 fail-fast（`config.py` 校验，R-11）；`.env` 设非空值 |
| clone/pull 卡住 | 国内网络镜像自适应见 [`../Scripts/README.md`](../Scripts/README.md) §5；或挂代理后重跑 |

---

## 6. 回滚

### 6.1 代码

```bash
git revert <commit>
docker compose up -d --build backend     # 让容器跑回滚后的代码（web 同理 --build web）
```

### 6.2 迁移

```bash
docker compose exec backend alembic current
docker compose exec backend alembic downgrade -1     # 需迁移脚本实现了 downgrade 路径
```

> ⚠ **积分流水 append-only（R-2）**：`score_ledger` 禁止 UPDATE/DELETE；降级迁移仅用于 schema 结构，不得破坏已落库流水。`update.sh` 迁移前已 `pg_dump`，迁移失败**绝不自动 downgrade**。

### 6.3 数据（极端）

postgres 数据落 `pgdata` volume；整体恢复用 volume 备份，**禁止** `docker compose down -v`（`-v` 删 volume 清库）。

---

## 7. 监控与告警（当前状态）

- **当前无生产监控 / 告警通道**：alert-service 规划中（见 [`architecture/services/alert-service.md`](./architecture/services/alert-service.md)）。
- 开发期靠 `docker compose logs -f backend` + `docker logs pchsystem-mc-test-1` 人工观察。
- 通知投递链路：MCDR 轮询 `GET /notifications/pending` → `server.tell` → `POST /notifications/ack`（见 [`architecture/services/notification-service.md`](./architecture/services/notification-service.md)）。

---

## 8. 互链

| 文档 | 用途 |
|---|---|
| [`../Scripts/README.md`](../Scripts/README.md) | 服主一键 install/update 完整选项 / 网络 / token / 前端部署 / 边界 |
| [`Cheatsheets/dev-cheatsheet.md`](./Cheatsheets/dev-cheatsheet.md) | 日常高频指令速查 |
| [`../Backend/CLAUDE.md`](../Backend/CLAUDE.md) §5 | 后端热重载 / 排错 |
| [`../McdrPlugin/CLAUDE.md`](../McdrPlugin/CLAUDE.md) §7 | MCDR 插件热重载 / 排错 |
| [`../Frontend/CLAUDE.md`](../Frontend/CLAUDE.md) §6 | 前端热重载 / 排错 |
| [`architecture.md`](./architecture.md) | 三端架构 / 跨服务流程 |

---

*最后更新：2026-07-12（加入 Scripts 一键部署体系；补 web 容器拓扑；修正迁移版本与拓扑断言；精简与 cheatsheet/子服务 CLAUDE.md 的重复）*
