# 交接说明 · Phase 0+1 身份登录闭环

> 2026-07-01。环境从 docker 不可用的 Windows 宿主，迁移到 docker 可用的虚拟器继续。

## 当前进度

分支 `main`（本地；远程默认分支仍是 `origin/feat/backend-phase0-foundation`，本地 `main` 未设 upstream）。本地领先 `origin/feat/backend-phase0-foundation` **22 个提交**（B5 代码 / B6 代码 + B7-B16 代码 + M1-M2 代码 + F1-F4 代码 + 5 docs），未 push：

| Task | 状态 | Commit |
|---|---|---|
| B1 后端骨架（pyproject + venv + 空 `__init__.py`） | ✅ | `8af2b93` |
| B2 Settings（pydantic-settings） | ✅ | `7c58671` |
| B3 数据库连接层（async engine + session） | ✅ | `ee8f889` |
| B4 FastAPI 入口 + `/healthz` | ✅ | `f646d3a` |
| B5 Step1：Player 模型 `Backend/app/models/user.py`（初版含 UUID 命名冲突 bug） | ✅（已在 B5 Step2 修复） | `b313e40` |
| B5 Step2-6：alembic.ini + env.py + 0001 迁移 + docker PG 验证（含 Player 模型 UUID 修复） | ✅（本次） | `b324e50` |
| B6 Docker Compose（Dockerfile + .dockerignore + compose + .env.example） | ✅ | `bce60ce` |
| B7 Service Token 鉴权依赖（`X-Service-Token` + `secrets.compare_digest`） | ✅ | `fef686a` |
| B8 JWT 工具（HS256 + access/refresh + jti） | ✅ | `d08d297` |
| B9 auth_tokens + jwt_revocations 表迁移 + ORM 模型 | ✅ | `f7b5253` |
| B10 pytest fixture（conftest 含同步 truncate + NullPool 修复） | ✅ | `fc9596a` |
| B11 players repository（`get_or_create` / `get_by_uuid`） | ✅ | `3c5e939` |
| B12 auth_tokens repository（一次性 + 过期 + `with_for_update`） | ✅ | `86f1b06` |
| B13 限频（`RateLimiter` 内存滑窗）+ 白名单（`check_whitelist`） | ✅ | `4b9d390` |
| B14 Pydantic schemas + `POST /auth/token`（MCDR 入口） | ✅ | `d3741cc` |
| B15 `POST /auth/exchange` + `POST /auth/refresh` + `GET /me` + `get_current_player`/`require_role` | ✅ | `ba8b1ff` |
| B16 OpenAPI 契约冻结（5 端点测试 + `openapi.json` 工件导出） | ✅ | `48bc1f3` |
| M1 MCDR 插件骨架（`mcdreforged.plugin.json` + `htcmc_auth/{__init__,config}.py` + `requirements.txt`） | ✅ | `7a2bc88` |
| M2 `!!login` 实现（UUID → 后端 → 可点击 RText URL，R-12 + S-1 落实） | ✅ | `1d14082` |
| F1 前端脚手架（Vue3 + Vite + TS + Element Plus + Pinia + Router + axios） | ✅ | `9259afa` |
| F2 axios 拦截器 + auth store（Bearer 注入 + 401 跳 `/auth` + localStorage 持久化） | ✅ | `ca016f6` |
| F3 路由守卫 + `/auth` token 兑换页（AuthExchange.vue + 真 router + Me.vue stub） | ✅ | `da78fa3` |
| F4 `/me` 身份页（el-card 展示 UUID/名称/角色） | ✅ | `6b66c47` |
| V1 端到端联调验收 | 部分（后端 curl 链路 + 前端 build 通过；MC 游戏内 + 浏览器流待手测） | — |

**下一步**：**V1 端到端验收**（MCDR + MC 服务端 + 浏览器流，需部署环境）→ **首次发版**（`backend-v0.1.0` / `mcdr-plugin-v0.1.0` / `frontend-v0.1.0` 三个独立 tag）。详见 `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md` 第 2358 行起（V1）。

## 计划与参考文件

- 完整可执行计划（23 任务 TDD 细粒度）：`Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`
- 原 MVP 路线图：`Docs/Plans/MVP-第一阶段计划.md`
- 鉴权方案（讨论稿，MVP 不实现）：`Docs/Plans/无感鉴权方案讨论.md`

## 环境

- **当前虚拟器（Linux 6.17.0-35-generic）**：docker 29.1.3 可用、Python 3.12.3、bash。后端 venv 路径 `Backend/.venv/bin/`（**非 Windows 的 `.venv/Scripts/`**），命令一律 `.venv/bin/python -m pytest` / `.venv/bin/alembic ...`。
- B6 后：根目录 `docker compose up -d` 即起 `pchsystem-postgres-1`（postgres:16，宿主端口 `127.0.0.1:5433`）+ `pchsystem-backend-1`（uvicorn :8000）。卷 `pchsystem_pgdata` 持久化（B5 跑过的 `users.players` 表已保留，无需重跑迁移；如换机器需 `docker compose exec backend alembic upgrade head`）。compose 内部网络 backend 走 `postgres:5432`；宿主端口 5433 仅本机调试用（5432 被别项目 `pf-postgres` 占用）。
- 根 `.env`（gitignored）从 `.env.example` 拷贝，提供 `POSTGRES_USER/PASSWORD/DB` + `JWT_SECRET` + `MCDR_SERVICE_TOKEN` + `WEB_BASE_URL` 真实值。`Backend/.env`（也 gitignored）仅本地裸跑 uvicorn 时用，与根 `.env` 字段重叠但 `POSTGRES_HOST=localhost` / `POSTGRES_PORT=5433`（宿主视角）。
- 旧的独立容器 `pch-pg`（B5 时手起）已 `docker stop` 未删除；如需彻底清理 `docker rm pch-pg`，或继续用也行（与 compose 项目互不干扰，但占 5433 端口会与 compose 冲突，故保持停止状态）。
- 旧 Windows 宿主已弃用（docker daemon 500、无裸机 PG、`psql` 未装）。

### 本机已就绪状态（同机器新会话直接复用，不要重建）

| 资源 | 状态 | 验证命令 |
|---|---|---|
| `Backend/.venv/` | ✅ 已装 `pip install -e ".[dev]"`，alembic 1.18.5、pytest 9.1.1、pytest-asyncio 1.4.0 | `Backend/.venv/bin/alembic --version` |
| docker compose 项目 | ✅ `pchsystem-postgres-1` (postgres:16, 端口 `127.0.0.1:5433`) + `pchsystem-backend-1` (uvicorn :8000) 运行中 | `docker compose -f /home/yushen/opt/PCHSystem/docker-compose.yml ps` |
| 根 `.env` | ✅ 已配（`POSTGRES_PASSWORD=change_me_strong_random` 与容器初始化一致；gitignored） | `grep POSTGRES_PASSWORD .env` |
| `Backend/.env` | ✅ 已配（`POSTGRES_HOST=localhost` / `POSTGRES_PORT=5433` / `POSTGRES_PASSWORD=change_me_strong_random` 与根 .env 一致，本地 venv 跑 alembic 与 pytest 用） | `grep POSTGRES_PASSWORD Backend/.env` |
| 数据库迁移 | ✅ 已 `alembic upgrade head`（`0002_auth_jwt` head；3 表：players / auth_tokens / jwt_revocations） | `cd Backend && .venv/bin/alembic current` |
| 测试套件 | ✅ 21 个测试全绿（`JWT_SECRET=test_secret_for_pytest .venv/bin/pytest -v`） | `cd Backend && JWT_SECRET=test_secret_for_pytest .venv/bin/pytest -v` |
| OpenAPI 工件 | ✅ `Backend/openapi.json` 已导出（5 端点：`/healthz` `/auth/token` `/auth/exchange` `/auth/refresh` `/me`） | `.venv/bin/python -c "import json; print(list(json.load(open('Backend/openapi.json'))['paths'].keys()))"` |
| 前端构建 | ✅ `npm run build` 通过（~310ms，仅第三方 @vueuse PURE 注解警告） | `cd Frontend && npm run build` |
| MCDR 插件语法 | ✅ 4 文件 `py_compile` + JSON parse 通过（mcdreforged/uuid_api_remake 为运行时依赖，本地未装） | `cd McdrPlugin && python3 -m py_compile htcmc_auth/*.py && python3 -c "import json; json.load(open('mcdreforged.plugin.json'))"` |

> **若换机器**：按下方"继续方式"段重建 venv + 起 pch-pg + 配 `.env` + 跑 `alembic upgrade head`。

## 已联网核实（红线 S-1，无需重复核实）

| 用途 | API | 来源 |
|---|---|---|
| 取 UUID | `import uuid_api_remake; uuid_api_remake.get_uuid(name)` | [插件 README](https://mcdreforged.com/zh-CN/plugin/uuid_api_remake/readme) |
| 命令回调 | `cb(source: CommandSource, context: CommandContext)` | [command.html](https://docs.mcdreforged.com/en/latest/code_references/command.html) |
| 玩家名 | `PlayerCommandSource.player` → str | [genindex](https://docs.mcdreforged.com/en/latest/genindex.html) |
| 发消息/异步/在线玩家 | `server.tell` / `schedule_task` / `get_online_players` | [ServerInterface](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html) |
| 注册命令/入口 | `server.register_command(...)` / `on_load(server)` | [PluginServerInterface](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html) |
| 可点击 URL | `RText(t).c(RAction.open_url, url)`（`open_url` 不受 1.19+ 签名聊天限制） | [minecraft_tools](https://docs.mcdreforged.com/zh-cn/latest/code_references/minecraft_tools.html) |

## 继续方式（新会话）

```bash
git clone https://github.com/YuShenLiu06/PCHSystem.git
cd PCHSystem
git checkout feat/backend-phase0-foundation
```

然后：
1. 读本文件 + `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`（B5/B6/B7-B16/M1-M2/F1-F4 段已标 ✅，可直接看 V1）
2. 重建 venv（可选）：`cd Backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`（走 docker 可跳过）
3. 起完整栈（B6 产出，**B7-B16 后需重建镜像**）：根目录 `cp .env.example .env` → 编辑真实密码 → `docker compose build backend` → `docker compose up -d` → `docker compose exec backend alembic upgrade head`（卷持久化，首次或换机器才需要）→ `curl http://localhost:8000/healthz` 应返回 `{"status":"ok"}`
4. 起前端：`cd Frontend && npm install && npm run dev` → 访问 `http://localhost:5173`
5. **V1 端到端验收**（计划 2358 行起）：需 MC + MCDR + 浏览器联合手测；本地可做后端 curl 链路 + 前端 build
6. V1 通过后进入首次发版（3 个独立 tag）

## review 策略（已采用）

- 纯配置/骨架任务（B1/F1 等）：implementer + 单次 spec review（含轻量质量检查）
- 有逻辑任务（B7/B8/B12/B14/B15 等）：implementer + spec review + code quality review 两阶段

## 关键红线（根 CLAUDE.md §3）

R-1 后端独占 DB；R-5 UUID 为身份锚；R-11 密钥进 `.env`；R-12 MCDR HTTP 走 `schedule_task`+超时+重试+回执；S-1 MCDR API 实现前联网核实（本文已核实）。
