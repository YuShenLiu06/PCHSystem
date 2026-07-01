# 交接说明 · Phase 0+1 身份登录闭环

> 2026-07-01。环境从 docker 不可用的 Windows 宿主，迁移到 docker 可用的虚拟器继续。

## 当前进度

分支 `main`（本地；远程默认分支仍是 `origin/feat/backend-phase0-foundation`，本地 `main` 未设 upstream）。本地领先 `origin/feat/backend-phase0-foundation` **5 个提交**（B5 代码 / B6 代码 + 3 docs），未 push：

| Task | 状态 | Commit |
|---|---|---|
| B1 后端骨架（pyproject + venv + 空 `__init__.py`） | ✅ | `8af2b93` |
| B2 Settings（pydantic-settings） | ✅ | `7c58671` |
| B3 数据库连接层（async engine + session） | ✅ | `ee8f889` |
| B4 FastAPI 入口 + `/healthz` | ✅ | `f646d3a` |
| B5 Step1：Player 模型 `Backend/app/models/user.py`（初版含 UUID 命名冲突 bug） | ✅（已在 B5 Step2 修复） | `b313e40` |
| B5 Step2-6：alembic.ini + env.py + 0001 迁移 + docker PG 验证（含 Player 模型 UUID 修复） | ✅ | `b324e50` |
| B6 Docker Compose（`Backend/Dockerfile` + `Backend/.dockerignore` + 根 `docker-compose.yml` + 根 `.env.example`） | ✅ | `bce60ce` |
| B7–B16 / M1-M2 / F1-F4 / V1 | 待做 | — |

**下一步**：**B7 Service Token 鉴权依赖**（`X-Service-Token` 头校验，FastAPI 依赖注入）。详见 `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md` 第 753 行起。

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
1. 读本文件 + `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`（B5/B6 段已标 ✅，可直接看 B7）
2. 重建 venv：`cd Backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`（仅本地裸跑 uvicorn 时需要；走 docker 可跳过）
3. 起完整栈（B6 产出）：根目录 `cp .env.example .env` → 编辑真实密码 → `docker compose up -d` → `docker compose exec backend alembic upgrade head`（卷持久化，首次或换机器才需要）→ `curl http://localhost:8000/healthz` 应返回 `{"status":"ok"}`
4. 从 **B7**（Service Token 鉴权依赖）继续；执行方式 `superpowers:subagent-driven-development`
5. 后端 B7-B16 串行跑完冻结 OpenAPI 契约（B16）后，MCDR(M1-M2) + 前端(F1-F4) 可并行

## review 策略（已采用）

- 纯配置/骨架任务（B1/F1 等）：implementer + 单次 spec review（含轻量质量检查）
- 有逻辑任务（B7/B8/B12/B14/B15 等）：implementer + spec review + code quality review 两阶段

## 关键红线（根 CLAUDE.md §3）

R-1 后端独占 DB；R-5 UUID 为身份锚；R-11 密钥进 `.env`；R-12 MCDR HTTP 走 `schedule_task`+超时+重试+回执；S-1 MCDR API 实现前联网核实（本文已核实）。
