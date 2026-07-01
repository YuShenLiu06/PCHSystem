# 交接说明 · Phase 0+1 身份登录闭环

> 2026-07-01。环境从 docker 不可用的 Windows 宿主，迁移到 docker 可用的虚拟器继续。

## 当前进度

分支 `feat/backend-phase0-foundation`（本地领先 origin 1 个提交，未 push）：

| Task | 状态 | Commit |
|---|---|---|
| B1 后端骨架（pyproject + venv + 空 `__init__.py`） | ✅ | `8af2b93` |
| B2 Settings（pydantic-settings） | ✅ | `7c58671` |
| B3 数据库连接层（async engine + session） | ✅ | `ee8f889` |
| B4 FastAPI 入口 + `/healthz` | ✅ | `f646d3a` |
| B5 Step1：Player 模型 `Backend/app/models/user.py`（初版含 UUID 命名冲突 bug） | ✅（已在 B5 Step2 修复） | `b313e40` |
| B5 Step2-6：alembic.ini + env.py + 0001 迁移 + docker PG 验证（含 Player 模型 UUID 修复） | ✅（本次） | `b324e50` |
| B6–B16 / M1-M2 / F1-F4 / V1 | 待做 | — |

**下一步**：**B6 Docker Compose**（postgres + backend）+ `Backend/Dockerfile`。详见 `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md` 第 620 行起。

## 计划与参考文件

- 完整可执行计划（23 任务 TDD 细粒度）：`Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`
- 原 MVP 路线图：`Docs/Plans/MVP-第一阶段计划.md`
- 鉴权方案（讨论稿，MVP 不实现）：`Docs/Plans/无感鉴权方案讨论.md`

## 环境

- **当前虚拟器（Linux 6.17.0-35-generic）**：docker 29.1.3 可用、Python 3.12.3、bash。后端 venv 路径 `Backend/.venv/bin/`（**非 Windows 的 `.venv/Scripts/`**），命令一律 `.venv/bin/python -m pytest` / `.venv/bin/alembic ...`。
- 已起 docker 容器 `pch-pg`（postgres:16）映射宿主端口 **5433**（5432 被别项目 `pf-postgres` 占用），PG 用户 `pch` / 密码 `pw` / 库 `pchsystem`。`Backend/.env` 已配 `POSTGRES_PORT=5433`（gitignored）。
- B6 写 docker-compose 时 PG 主机名内部网络仍用 `postgres` + 5432，不受宿主端口冲突影响。
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
1. 读本文件 + `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`（B5 段已标 ✅，可直接看 B6）
2. 重建 venv：`cd Backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
3. 起 PG（若 pch-pg 容器不在）：见上方"环境"段；记得 5432 占用时改 5433
4. 从 **B6**（docker-compose + Dockerfile）继续；执行方式 `superpowers:subagent-driven-development`
5. 后端 B6-B16 串行跑完冻结 OpenAPI 契约（B16）后，MCDR(M1-M2) + 前端(F1-F4) 可并行

## review 策略（已采用）

- 纯配置/骨架任务（B1/F1 等）：implementer + 单次 spec review（含轻量质量检查）
- 有逻辑任务（B7/B8/B12/B14/B15 等）：implementer + spec review + code quality review 两阶段

## 关键红线（根 CLAUDE.md §3）

R-1 后端独占 DB；R-5 UUID 为身份锚；R-11 密钥进 `.env`；R-12 MCDR HTTP 走 `schedule_task`+超时+重试+回执；S-1 MCDR API 实现前联网核实（本文已核实）。
