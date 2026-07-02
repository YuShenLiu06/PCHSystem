# Changelog

本项目所有显著变更记录于此文件。

格式遵循 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)。
各子组件（`Backend/` · `McdrPlugin/` · `Frontend/`）按根 `CLAUDE.md` §5 与
`CONTRIBUTING.md` 约定独立维护各自的 SemVer。

---

## [Unreleased]

> 下一版本待归档。新增条目按组件 × Added/Changed/Fixed/Security 分类补在此处，发版时固化为 `## [<component>-vX.Y.Z] - YYYY-MM-DD` 段并重置本段（详见底部「版本化策略」）。

### Backend

#### Added

- _（待补）_

### McdrPlugin

#### Added

- _（待补）_

### Frontend

#### Added

- _（待补）_

### 文档与计划

#### Added

- _（待补）_

---

## [backend-v0.3.0] - 2026-07-03

### Added

- **投影解析模块**（`app/services/parsing/`）：`MaterialParser` / `ItemTranslator` 双 ABC（可扩展 `.schem/.nbt` 与其它翻译源）；`LitematicParser` 基于 [`litemapy`](https://github.com/SmylerMC/litemapy)（逐体素 `.id` 计数，R-6 天然满足），容器读 vanilla `Items`；`LangJsonTranslator` 加载内置 `lang/*.zh_cn.json` 按 `block.→item.` 候选查表。`7d1f618`
- `POST /parsing/litematic`：multipart 上传 `.litematic` → 解析 + 中文翻译 → 分组预览（不落库；`asyncio.to_thread` 不阻塞事件循环）。`7d1f618`
- `POST /sheets/from-items`：一次性建表 + 批量行（`mode` 默认 lock，items ≤2000），零迁移复用 sheets schema。`7d1f618`
- 内置 vanilla（MC 1.20.1）+ Create（6.0.8/1.20.1）中文语言文件（SHA1 校验）；config 加 `litematic_max_upload_bytes`；openapi.json 重新生成。`7d1f618`
- **sheets MC 代玩家写通道 + 统一通知抽象层**：迁移 `0006_notifications`（schema/表/FK CASCADE/索引，可逆）；notification 模型/repo/service，`notify(session,…)` 同事务原子（R-10），`Notifier` Protocol 预留 Webhook/Discord 扩展点。`e55f1c6`
- **双通道鉴权 `get_current_player`**（`api/deps.py`）：Bearer JWT 优先，否则 `X-Service-Token` + `X-Player-UUID` 代玩家加载 Player 注入；Authorization 头存在只走 JWT 通道、不静默降级（H-2）；复用现有 RBAC，业务层零改动。`e55f1c6`
- notifications API `pending`/`ack`/`read`：`recipient_uuid` 归属校验防越权 ack/read（C-1）；`limit ≤ 50`。`e55f1c6`
- sheets 各写端点同事务挂钩 `notify`（认领/交付/打回/解除/数量变更/删除 → 认领人或拥有者；`actor == recipient` 跳过）。`e55f1c6`
- **sheets progress 多人贡献者**：迁移 `0007_sheet_row_contributors`（建贡献者表）+ `0008_sheet_rows_contributed_qty`（加 `contributed_qty`，按贡献量排序显示）；progress 行不再单人锁定，改为多人贡献者列表（聚合众筹）。`093e6af`
- `POST /sheets/rows/{id}/contribute`（增量上交，任意玩家）+ `PATCH /sheets/rows/{id}/progress`（owner 设绝对值，不动贡献者）；`need=0` 视为无目标永不 done；`claim`/`delivery`/`reject` 对 progress 行返 409。`093e6af`

### Changed

- `pyproject.toml` 显式标注 `litemapy>=0.11.0b0`（PyPI 仅有 pre-release，`pip` 默认不匹配 `>=0.11`）。`fdef446`

### Fixed

- `litemapy>=0.11` 版本约束在 PyPI 仅有 pre-release（`0.11.0b0`）时 `pip` 默认不接受，导致安装失败；显式标注 `>=0.11.0b0` 修复。`fdef446`

### Security

- **service-token 代玩家写加固**（`MCDR_SERVICE_TOKEN` 等同「全员 root 凭据」）：① 后端对每次代玩家写记 `service_token_proxy` 审计日志（`{timestamp, player_uuid, http_path, client_ip}`，**不含 token 本身**）；② `POST /notifications/ack` body 加 `player_uuid` 归属校验，防越权 ack 他人通知；③ `POST /notifications/{id}/read` 加 `player_uuid` query 归属校验，防越权 read；④ `notify()` 对 title(≤200)/body(≤500) 限长清洗 + payload 设 8KB 上限；⑤ `GET /notifications/pending` 的 `limit` 上限收紧至 50（原 100）；⑥ `MCDR_SERVICE_TOKEN` 启动 fail-fast 校验。引用根 CLAUDE.md R-11；运维缓解见 [`Docs/architecture/services/notification-service.md`](Docs/architecture/services/notification-service.md) §7。`e55f1c6`

---

## [mcdr-v0.3.0] - 2026-07-03

### Added

- **`!!PCH sheet` 命令树 + 通知轮询**（14 子命令）：`sheet_client`（sheets + notifications HTTP，`X-Service-Token` + `X-Player-UUID` 双头，重试 + 哨兵）；`sheet_commands`（`@new_thread` + `server.tell` 回执，403/404/409 中文文案）；`notifier`（在线字典 `on_player_joined`/`on_player_left` + rcon list 初始化 + `@new_thread` 轮询 + ack + 离线补推）；事件监听 + 线程生命周期（reload 新 `Event` 防双循环）；config 加 `notify_poll_interval_seconds`/`notify_max_per_poll`。`490d17d`
- **deliver 按 mode 分流 + 贡献者显示**：progress→`contribute`（增量，不要求认领），lock→`delivery`（绝对值）；view 按钮 progress·open 显 `[交付]`、done 显 `[解除]`（不显打回）；`format_row_line` progress 行显示贡献者前两位 + 省略号（按 `contributed_qty` desc）；`sheet_client` 加 `contribute_row`/`set_row_progress`。`9a7c0fa`
- **sheet 可点击按钮 helper**（`messages.py`）：`rtext_button`（`RAction.suggest_command` 点击填命令 + `RText.h` 悬停提示）；`format_row_clickable`（状态×模式×拥有者尾部工具栏——open→`[认领]` / claimed(lock)→`[标备齐][解除]` / claimed(progress)→`[交付][标备齐][解除]` / done→`[打回]`，拥有者追加 `[删行]`）；`format_owner_footer`（拥有者底部 `[新增物品][改标题][删表]`）；统一色板遵循 `McdrPlugin/CLAUDE.md` §6。`d57cf0c`

### Changed

- **sheet view 特权按钮按查看者身份显隐**（UUID 为主、名字兜底），对齐后端 RBAC：lock claimed `[标备齐]` 仅认领人；`[解除]` 认领人 or owner；lock done `[打回]` 认领人 or owner；progress 协作按钮任意；`is_owner` 升级为 `owner_uuid` 匹配。`acdd348`
- **`!!PCH` / `!!PCH sheet` 菜单重做**：gold 标题 / aqua 已上线命令等宽对齐 + 可点击 suggest / gray 开发中折叠 / yellow hover 参数签名；移除淹没的 8 条黄色 stub；统一色板（标题 gold+bold / 命令名 aqua / 描述 gray / 强调 yellow）。`f269fa4` · `0976216`

### Fixed

- `f269fa4` 的 `sheet_commands.py` 已 import `rtext_button`/`format_row_clickable`/`format_owner_footer` 但函数定义未随提交进入 `messages.py`（HEAD 加载即 `ImportError: cannot import name 'rtext_button'`）；`d57cf0c` 补齐三函数定义 + `tests/_stubs.py` RAction 补 `suggest_command`/`run_command`、`RText.c` 改 `(action, value)` 签名 + 新增 `h()` hover stub + `tests/test_messages.py` +19 用例，修复插件加载。
- **`!!PCH sheet add/set` 的 mode/sort 改为可选**（默认 lock/0）：原命令树把 `_sheet_upsert` 只挂在 `lock|progress` 字面量及其 sort 子节点，玩家必须输入 mode 字面量；否则解析在 `Integer("need")` 节点终止、无回调触发，MCDR 回显「未知命令」。改为给 `add`/`set` 两块的 `Integer("need")` 挂 `.runs(_sheet_upsert)`；handler 已有默认（mode=lock/sort=0），现有合法输入零回归。S-1 联网核实命令解析「在某节点终止即触发该节点回调」（<https://docs.mcdreforged.com/en/latest/plugin_dev/command.html>）。`4b7f99a`
- **通知体验修复**（4 项）：① 7 个通知模板全部加 `{sheet_title}`，「B 认领了 [铁锭]」→「B 认领了 [清单] 的 [铁锭]」；② `ack_notifications` body 从 `{ids}` → `{player_uuid, ids}`（后端 `NotificationAckRequest` 必填，防越权 ack；曾因缺 `player_uuid` 致 422 → `delivered_at` 永不置位 → 通知刷屏）；③ ack 结果判定从 `is None` → `not isinstance(dict)`（`HttpError(422)` 等也算失败留 warning）；④ `_sheet_list` 空表单追加 `[建表]`、`_sheet_view` 空行也渲染 `format_owner_footer`（拥有者空表可见 `[新增物品]`）。`588e249`
- **通知轮询延迟 ~12s**（预期 2s）：`config.json.example` 的 `notify_poll_interval_seconds` 误写 `15.0`，与 `config.py` 默认 `2.0` + 5 处架构文档矛盾；运行时从 example 复制 → 实际生效 15s。改回 `2.0`；`on_load` 追加日志打印生效 interval/max_per_poll（防漂移被静默吞掉）；新增 `tests/test_config.py` 一致性测试断言 example 的 4 个行为参数与 `HtcmcAuthConfig` 默认一致（CI 拦截漂移）。`4a098c0` · `588e249`

---

## [frontend-v0.3.0] - 2026-07-03

### Added

- **投影解析上传页**（`views/parsing/LitematicImport.vue`）：`el-upload` → 两组 `el-table` 预览（方块/容器，可编辑数量/勾选）→ 按组生成 Sheet（mode=lock，sort_order=索引）；`api/parsing.ts` `previewLitematic`（multipart）+ 类型；`sheets.ts` 加 `createSheetFromItems`；router 加 `/parsing/litematic` + App.vue 导航；vitest 33 用例。`7381d20`
- **sheets 列表/详情轮询自动刷新**：`usePolling` composable（递归 setTimeout、Page Visibility 后台暂停、连续失败指数退避上限 60s、卸载清理、in-flight 重入保护）；`SheetList` 10s 轮询 list；`SheetEditor` 1s 轮询 + `silentRefresh`（只换展示数据不动 `rowDrafts`/`titleDraft`，保护正在编辑的草稿）；附 `usePolling` 单元测试 6 例。`0886e64`
- **progress 上交材料/调整进度 + 草稿补齐**：progress 行无认领改「上交材料」（增量）；owner 新增「调整进度」（设绝对值）替代「解除锁定」；`silentRefresh` 补初始化新行草稿（修复 MCDR 创建的行在 web 不可编辑）。`8b93357`

### Changed

- `vite.config.ts` `server.allowedHosts` 加 `dev-git.u3071783.nyat.app`，允许经反代/tunnel 域名访问 dev server（Vite 默认拦截防 DNS rebinding；仅 dev，生产走 `vite build` 静态产物不受影响）。`c1922ca`

### Fixed

- 拥有者看自己表的 claimed 行原本「放弃」与「解除锁定」都绑 `onRelease`、重复且「放弃」对拥有者语义不对；认领人的「放弃」按钮加 `v-if="!canEdit"`，拥有者只留「解除锁定」，非拥有者认领人仍显示「放弃」。RBAC 仍以后端为准（R-9，仅可见性）。`442520b`

---

### 文档与计划（v0.3.0 跨组件）

#### Added

- `Docs/superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md`：sheets ↔ MCDR 对接 + 统一通知抽象层权威设计（鉴权双通道 / 通知抽象层契约 / 触发规则表 7 类 category / `!!PCH sheet` 命令映射 / 轮询投递与离线补推 / 红线遵循 / MCDR API 依据 URL）。`9203283`
- `Docs/architecture/services/notification-service.md`：notification-service 通知抽象层**可复用契约**文档（`notify(session,…)` 同事务语义 + `category` 枚举注册表与扩展规约 + 数据模型 `notifications.notifications` + `Notifier` Protocol 首期 `DbNotifier` 预留 Webhook/Discord + pending/ack/read 端点契约 + MCDR 投递契约 + 与 alert-service 关系 + §7 service-token 安全运维要点）。`9203283`
- `Docs/architecture/api/parsing.md`：投影解析 API 权威参考（端点 + ABC 架构 + 翻译数据来源 S-1 + 限制）；`sheets.md` §5.1 追加 `POST /sheets/from-items`；`Backend/CLAUDE.md` §2/§4 更新（parsing 纳入后端职责 + 端点表）。`3207ae1`
- `Docs/Cheatsheets/dev-cheatsheet.md`：补「后端 FastAPI」段（compose 启停 / 端口表 / 根 `.env` 雷点 / `alembic upgrade head`·`current` / `/healthz`·`/me` 健康检查 / 日志与 pytest）+「前端 Vue3（Vite）」段（`npm run dev/build/preview`·`npx vitest` / `/api` 代理剥离前缀 / 联调依赖 backend / `WEB_BASE_URL` / `allowedHosts` 雷点）+「环境变量」段（12 变量 AUTO-GENERATED 表）。`c1922ca` · `8f1ffb6`
- `Docs/RUNBOOK.md`：dev/staging 运维手册（拓扑 / 部署 / 健康检查 / 排错 / 回滚）。`8f1ffb6`

#### Changed

- `Docs/architecture.md`：§2.1 三端流程图 `MCDR → API` 边加注「（写：service-token + X-Player-UUID 代玩家）」；§5 服务地图 mermaid 加 `notification-service` 节点 + 服务表加一行；§7 新增「7.5 sheets 协作（Web ↔ MC 对等 + 通知流转）」sequenceDiagram。`9203283`
- `Docs/architecture/api/sheets.md`：§2 鉴权表新增「Service Token + X-Player-UUID（代玩家）」通道；§5 端点表每个写端点说明列补「MCDR 可经 service-token+`X-Player-UUID` 代玩家调用」；§10 迁移表加 `0006_notifications`；新增 §11「MCDR `!!PCH sheet` 命令映射表」+ §12「通知端点」。`9203283`
- `Docs/architecture/services/mcdr-plugin.md`：命令表加 `!!PCH sheet …` 全套；§3.6 修正 `schedule_task` 卸载阻塞的过时描述（与 RS-6 冲突）；新增 §3.6.1「service-token + `X-Player-UUID` 代玩家写」、§3.7「sheets 命令树」、§3.8「通知轮询」；§4 依赖服务表加 sheets/notifications；§5 配置项加 `notify_poll_interval_seconds`/`notify_max_per_poll`；§6 风险表加阻塞误用与轮询延迟两项。`9203283` · `4a098c0`
- README / 根 CLAUDE.md / CONTRIBUTING.md：compose 实际仅 postgres+backend（wiki.js 标注规划中）；三端去「规划中」标实现进度，补 TestServer/CHANGELOG；根 CLAUDE.md §7 任务状态刷新到 07-02；CONTRIBUTING.md §3 PR 自检改为贴合现实；根 CLAUDE.md `litemapy` 链接 Spindust→SmylerMC 修正（S-1）。`8f1ffb6` · `3207ae1`

---

## [v0.2.0] - 2026-07-02

### Backend

#### Added

- 迁移 `0005_sheets_collab`：`sheets.sheet_rows` 加 `mode`(smallint 0=lock/1=progress) · `status`(text open/claimed/done) · `claimant_uuid`(FK→users.players.uuid 可空) · `delivered_qty`；旧 `done_flag=1`→`status='done'` 后删 `done_flag`；加 `ix_sheet_rows_sheet_status`，可逆已验证。`510abec`
- sheets 行认领协作 + 名称显示：`SheetRepository` 加 `claim_row`/`set_row_delivery`/`release_row`/`reject_row`（`select...with_for_update()` 行锁 + 状态机不变量，非法转移 raise `SheetRowConflict`→409）；`list_sheets`/`get_sheet` inner join、`list_rows`/`get_row` left join `users.players` 取 `owner_name`/`claimant_name`；upsert 改 need 时 delivered 按新 need 封顶并自动判 done/回落。4 新端点（claim 任意玩家 / delivery 认领人 / release 认领人|owner / reject 认领人|owner）；schema 去 done_flag 加 mode + `RowDeliveryRequest`。`f638f3c`
- sheets 协作测试 + OpenAPI 同步：`sheet_repo` 20 用例（状态机全分支 + 不变量 + upsert 封顶）+ `sheets_api` 33 用例（403/404/409 + owner_name=游戏名 + RowDetail 新字段）；`openapi.json` 重导出含 4 新端点，`test_openapi_freeze` 追加 4 路径断言。`80ec623`

### Frontend

#### Added

- sheets 认领/进度协作 UI + 名称显示：`sheets.ts` 类型对齐新契约（+`owner_name`；RowDetail 去 done_flag 加 mode/status/claimant_uuid/claimant_name/delivered_qty；RowUpsertRequest 加 mode）+ 4 新 API 函数（claimRow/setRowDelivery/releaseRow/rejectRow）；`SheetList.vue` 所有者列显游戏名；`SheetEditor.vue` 协作控件（模式/认领者/状态 el-tag/交付进度 el-progress/动作按角色×状态渲染，progress 上报交付 ElMessageBox.prompt，R-9 旁观者只读；认领人「取消备齐」与拥有者「打回」效果一致，合并为单一「打回」按钮）。`b66916f`
- sheets 协作前端测试：4 新端点「以正确参数调用」断言 + 新契约 fixture；vitest 26 通过 + `npm run build`（vue-tsc）通过。`2c82ef8`

### 文档与计划

#### Added

- `Docs/Plans/superpowers/2026-07-02-sheets-collaboration.md`：sheets 协作改进 TDD 实现计划（后端 B1-B6 + 前端 F1-F4 + 文档 D1-D2，Teammates 并行）。`205c850`
- `Docs/architecture/api/sheets.md`：后端 sheets API 权威参考（端点 / 鉴权 / 行状态机 / 权限矩阵 / 错误码 / CSV 列 / 迁移）；根 `CLAUDE.md` §5 加「API 参考」索引；`Docs/Plans/HANDOFF.md` 追加 sheets 协作改进段。`未提交`

---

## [v0.1.0] - 2026-07-02

### Backend

#### Added

- 后端项目骨架与依赖：`Backend/pyproject.toml`（FastAPI · SQLAlchemy[asyncio] · asyncpg · alembic · psycopg2-binary · PyJWT · pydantic-settings · httpx）+ dev extras（pytest · pytest-asyncio · anyio）。`8af2b93`
- `Settings` 配置类（pydantic-settings，从 `Backend/.env` 读取）：暴露 `postgres_dsn`（asyncpg）与 `postgres_dsn_sync`（psycopg2，供 Alembic 用）属性，含 JWT / AUTH_TOKEN / MCDR / WEB_BASE_URL 等字段。`7c58671`
- 异步数据库连接层 `Backend/app/core/db.py`：`create_async_engine` + `async_sessionmaker` + `get_session` FastAPI 依赖；定义 `Base = DeclarativeBase`。`ee8f889`
- FastAPI 入口与 `GET /healthz` 健康检查端点。`f646d3a`
- `Player` 模型（`users.players` 表）：UUID 主键 + `current_name` · `role` · `whitelist_state` + `first_seen_at` / `last_seen_at`（含时区与 `now()` 默认值）。`b313e40`
- Alembic 配置：`alembic.ini` + `alembic/env.py`（同步走 psycopg2，从 `Settings.postgres_dsn_sync` 注入 DSN，`compare_type=True` 为 B9 autogenerate 铺路）。`b324e50`
- 首个迁移 `0001_users_players`：`CREATE SCHEMA users` + `CREATE TABLE users.players`，可逆（downgrade 删除表与 schema）。`b324e50`
- `Backend/Dockerfile`：`python:3.11-slim` 基础镜像，setuptools 打包 `app` 包 + uvicorn 启动；COPY 拆 4 条（`pyproject.toml` / `app/` / `alembic.ini` / `alembic/`）避免文件与目录混拷到同一路径非法；MVP 不做 multi-stage / 非 root user。`bce60ce`
- `Backend/.dockerignore`：11 条目（`.env` / `.env.local` / `.venv/` / `__pycache__/` / `*.pyc` / `.pytest_cache/` / `*.egg-info/` / `build/` / `dist/` / `tests/` / `.git/`），关键挡 `.env` 防止 secrets 进镜像 layer（R-11）。`bce60ce`
- 根 `docker-compose.yml`：`postgres:16` + `backend`；显式 `environment:` + `${VAR}` 插值（非 `env_file:`，文档化后端实际依赖变量且防意外注入）；`POSTGRES_HOST=postgres` / `POSTGRES_PORT=5432` 硬编码为容器网络视角；PG 端口 `127.0.0.1:5433:5432`（宿主 5432 被 `pf-postgres` 占用 + 绑 loopback 防公网暴露）；`pgdata` named volume 持久化；PG healthcheck `pg_isready` + backend `depends_on: service_healthy`；MVP 不加 backend 自身 healthcheck、不进 alembic entrypoint。`bce60ce`
- 根 `.env.example`：compose 模板（`POSTGRES_HOST=postgres` / `POSTGRES_PORT=5432` 锁定容器视角），用户拷贝为真实 `.env`（gitignored，不进库）。`bce60ce`
- `X-Service-Token` 鉴权依赖 `require_service_token`（`Backend/app/api/deps.py`）：`secrets.compare_digest` 常数时间比较防时序侧信道、空值短路防 None 误比较、`X-Service-Token` 头自动映射参数名。`fef686a`
- JWT 工具 `Backend/app/core/jwt.py`：`create_access_token` / `create_refresh_token`（返回 `(token, jti)` 供 B15 吊销追踪）/ `decode_token`，HS256 算法常量、`algorithms=[_ALGO]` 防 alg-confusion 攻击、payload 含 `sub/role/type/iat/exp/jti`、TTL 走 `Settings.jwt_*_ttl_seconds`。`d08d297`
- `auth_tokens` + `jwt_revocations` 表迁移 `0002_auth_jwt`：FK 到 `users.players.uuid`、`(player_uuid, expires_at)` 复合索引便于清理、`server_default=now()` DB 端时间戳、可逆 downgrade。`f7b5253`
- `AuthToken` / `JwtRevocation` ORM 模型追加到 `app/models/user.py`：`token`/`jti` 主键 UUID、`used_at` 可空支持一次性语义、`issued_ip`/`exchanged_ip` 审计列。`f7b5253`
- pytest 异步 fixture 与测试库清理 `Backend/tests/conftest.py`：`_truncate_db` autouse 同步 truncate（避免 pytest-asyncio 1.4 + 同步测试混合跑的 `RuntimeError: Event loop is closed`）、`client` fixture 用 httpx `AsyncClient` + `ASGITransport`。`fc9596a`
- players repository `Backend/app/repositories/player_repo.py`：`get_or_create`（首次创建走 `flush()` 不 commit 由调用方控事务；已存在则更新 `current_name` + `last_seen_at`）、`get_by_uuid` 读助手。`3c5e939`
- auth_tokens repository `Backend/app/repositories/auth_token_repo.py`：`issue`（创建带 TTL 与可选 `issued_ip` 的 token）、`exchange`（`with_for_update()` 行锁防并发重放、三护栏返回 None：未找到/`used_at` 已设/`expires_at < now`、返回 Player 而非 AuthToken）。`86f1b06`
- 限频 + 白名单服务 `Backend/app/services/auth_service.py`：`RateLimiter` 内存滑窗（`time.monotonic` + `threading.Lock`，docstring 标注多 worker 需 Redis）、`rate_limiter` 模块单例、`check_whitelist` 仅投影 `whitelist_state` 列返回 `state != "removed"` 前向兼容未来状态。`4b9d390`
- Pydantic schemas `Backend/app/schemas/auth.py`：7 个模型覆盖 issue/exchange/refresh/me 全流程，B15 直接复用。`d3741cc`
- `POST /auth/token` 端点（MCDR 调用入口）：限频 → `get_or_create` → 白名单 → `issue` → `commit` → 拼接 `web_base_url/auth?token=<uuid>`；429/403 错误码；`X-Service-Token` 头校验（B7 依赖）；`issued_ip` 从 `request.client.host` 取并 None 防御。`d3741cc`
- `POST /auth/exchange` 端点（前端一次性 token 换 JWT）：调用 B12 `exchange_token`，None 返回 401，成功 commit 后签发 access + refresh JWT pair。`ba8b1ff`
- `POST /auth/refresh` 端点：解码 refresh JWT 校验 `type=="refresh"` 后重签 pair（MVP 未接 `jwt_revocations` 吊销表，源码注释标注后续扩展点）。`ba8b1ff`
- `GET /me` 端点（JWT 持有者信息）：挂在 `top_router`（无 `/auth` 前缀），通过 `Depends(get_current_player)` 校验 Bearer token。`ba8b1ff`
- `get_current_player` + `require_role` 依赖（`Backend/app/api/deps.py` 追加）：Bearer 解析 + JWT 解码 + `type=="access"` 校验 + Player 回查，四种 401 路径；`require_role` 工厂返回闭包，`owner` 角色绕过 RBAC（为后续管理类端点预留）。`ba8b1ff`
- OpenAPI 契约冻结：`Backend/tests/test_openapi_freeze.py` 校验 5 端点路径不可移除；`Backend/openapi.json` 工件导出（OpenAPI 3.1.0，`ensure_ascii=False` 保中文 description），供前端/MCDR 团队桩测。`48bc1f3`
- 迁移 `0003_auth_tokens_revoked_at`：`users.auth_tokens` 增 `revoked_at`（带时区、可空）列 + 部分索引 `ix_auth_tokens_player_active`（`WHERE used_at IS NULL AND revoked_at IS NULL`），支撑 token 软吊销审计与「活跃 token」快速查找。`未提交`
- 迁移 `0004_sheets`：`CREATE SCHEMA sheets` + `sheets.sheets`（`owner_uuid` FK→`users.players.uuid`，R-5）+ `sheets.sheet_rows`（`sheet_id` FK `ON DELETE CASCADE`、`UNIQUE(sheet_id,item_name)` 兼作 upsert 锁点、`ix_sheet_rows_sheet_id` 索引），可逆已验证。`aa072bb`
- `Sheet` / `SheetRow` ORM（`app/models/sheet.py`，镜像 user.py 风格：PG_UUID/Mapped/DateTime(timezone)/server_default text("now")）；`alembic/env.py` 注册模型。`aa072bb`
- sheets API 契约冻结：`app/schemas/sheet.py`（6 Pydantic 模型，`done_flag`∈{0,1}/`need_qty`≥0 校验）+ `app/api/sheets.py` 8 端点签名+response_model 桩（`_can_edit` helper、`/export` 注册在 `/{id}` 前避免被动态路径吞）；OpenAPI 重新导出，`/sheets` 全路径就位。`1937bdb`
- `format_qty` 纯函数 `app/core/qty.py`（STACK=64/SHULKER=1728，三档个/组/盒，`:g` 去尾零）；API 只返原始 int，不附带换算串（D-4）。`57a065c`
- `SheetRepository` `app/repositories/sheet_repo.py`（函数式，镜像 auth_token_repo：`create_sheet`/`get_sheet`/`list_sheets`/`list_rows`/`upsert_row`/`delete_row`/`delete_sheet`/`export_csv`/`export_all_csv`；upsert 用 `select...with_for_update()` 在则改/不在则 insert，并发同名 insert 触发 IntegrityError 上抛；conftest truncate 追加 `sheets.sheet_rows, sheets.sheets`）。`dd2ff24`
- sheets CRUD API 真实实现（替换 501 桩）：`_can_edit(sheet,player)` 权限 helper（owner 或 admin/owner 角色，D-3）；commit 在 api 层；upsert `IntegrityError`→409；`?format=csv` 单表 + `/export` 全量 CSV（service token，外部读取硬约束 MVP §4）；21 集成测试覆盖全路径+权限分支（建表/列表含他人表/owner=me 过滤/详情/CSV 单表/改标题 owner✓&非 owner 403&admin✓/删表级联/upsert 新建+更新/删行/404/全量 export/未登录 401）。`3411480`

#### Changed

- `auth_token_repo.issue` 改为先 `revoke_active_tokens`（软吊销同 UUID 下 `used_at IS NULL AND revoked_at IS NULL` 的旧 token，置 `revoked_at = now()`）再签发新 token，返回 `(token, revoked_count)`；`POST /auth/token` 响应 schema 增 `previous_tokens_revoked: int` 字段（`TokenIssueResponse`）。`models/user.py`（`AuthToken.revoked_at`）/ `schemas/auth.py` / `api/auth.py` / `tests/test_auth_api.py` 同步。`未提交`

#### Fixed

- `Backend/.env` 的 `POSTGRES_PASSWORD=pw`（B5 时 pch-pg 容器密码）与 docker compose 启动的 `pchsystem-postgres-1`（用根 `.env` 的 `change_me_strong_random` 初始化）不一致，导致本地 venv alembic 与 B10+ 测试 fixture 认证失败；改为 `change_me_strong_random` 对齐容器。`fc9596a`
- B10 spec 的 async `_truncate` autouse fixture 在 pytest-asyncio 1.4 + 同步测试混合跑时触发 `RuntimeError: Event loop is closed`（autouse async fixture 在同步测试周围也会创建/关闭 loop，asyncpg 池内连接泄漏到已关闭 loop）；控制器级修复改为同步 fixture 用独立 sync engine + 每次 dispose；同时 `app.core.db` 的 async engine 改用 `NullPool` 防止 async 测试跨 loop 复用池内连接。`fc9596a`
- `auth_token_repo.issue` 已改为返回 `tuple[AuthToken, int]`（软失效改造，对应 `previous_tokens_revoked`），但 `tests/test_auth_token_repo.py` 仍用旧签名（`tok = await issue(...)` 直接取 `.token`/`.expires_at`）导致 3 个 case 失败；同步为 `tok, _ = await issue(...)`。`e74ee19`

### McdrPlugin

#### Added

- MCDR 插件骨架：`mcdreforged.plugin.json`（id `htcmc_auth`、版本 `0.1.0`、依赖 `mcdreforged>=2.14.0` + `uuid_api_remake`）+ `requirements.txt`（`requests>=2.31`）+ `htcmc_auth/{__init__.py, config.py}`（`HtcmcAuthConfig` 4 字段：`api_url` / `service_token` / `http_timeout_seconds` / `http_retries`，`on_load` 加载配置 + 注册 `!!login` 占位命令）。`7a2bc88`
- `!!login` 实现链路：`htcmc_auth/client.py`（HTTP 客户端，超时 + 重试 + 429/403 哨兵字符串）+ `htcmc_auth/__init__.py` 完整 `_login`（`PlayerCommandSource` 校验 → `uuid_api_remake.get_uuid` 推导 → `server.schedule_task` 内异步调后端 → `RText.c(RAction.open_url, url)` 可点击链接回显；红线 R-12 落实，红线 S-1 API 经 HANDOFF 联网核实）。`1d14082`
- `htcmc_auth/commands.py`：`!!PCH` 命令回调集合（`_pch_root` 帮助树 / `_login` 登录链路 / `_not_impl` 占位工厂）；`htcmc_auth/messages.py`：集中消息/色彩常量（`§` 码前缀）+ RText 构造器（`rtext_link` / `rtext_info` / `rtext_warn` / `rtext_error`）。`未提交`
- 登录链路回显「§c上一个登录链接已失效」红色提示：依赖后端 `previous_tokens_revoked > 0`（同 UUID 重复申请 token 时旧 token 被软吊销）。`未提交`
- `McdrPlugin/CLAUDE.md` §6：MCDR 色彩代码使用标准（`§` 码 ↔ `RColor/RStyle` 双轨 + 语义用途表 + 样式码表 + 使用规则 + 双写对照示例）。`未提交`

#### Changed

- 插件目录重构为 `htcmc_auth/htcmc_auth/` Python 包（`mcdreforged.plugin.json` 同步移到包上层）；`__init__.py` / `client.py` / `config.py` 进包内。`未提交`
- 命令树重构：注册 `!!PCH` 父节点（`.runs(_pch_root)` 显示帮助）+ `login / bind / submit [hand|<project> x y z] / project [list|<id> info] / score / rank / title [list|<id> set] / info` 子节点（基于 `Literal` / `Text` / `Integer`，API 已联网核实）；废弃单一 `!!login` 字面量。`未提交`

#### Fixed

- `!!PCH login` 阻塞 MCDR 主线程导致卡顿：`_login` 原用 `server.schedule_task(_do)` 包裹阻塞式 `requests.post`，而 `schedule_task` 同步回调跑在 task executor = 主线程，卡住整个主循环（命令/事件/server 输出解析全停滞）。改为 `@new_thread('htcmc_auth login')` 把 `_do` 卸载到 daemon 线程（`server.tell` 线程安全，S-1 联网核实 PluginServerInterface 文档）。`未提交`
- 项目文档并发模型勘误：`Docs/McdrPlugin/mcdr-api-cheatsheet.md` §8 HTTP 模板、根 `CLAUDE.md` R-12、`McdrPlugin/CLAUDE.md` RS-6 原说「耗时调用放 `schedule_task`」（错误，会卡主线程），统一改为「阻塞调用放 `@new_thread`；`schedule_task` 仅用于协程 / 延迟到主线程下一 tick / 从后台线程回主线程」。`未提交`

### Frontend

#### Added

- Vue3 + TypeScript + Vite 脚手架：`Frontend/`（npm 模板 + 自定义 `vite.config.ts` 含 `/api` 代理到后端 8000 与 jsdom 测试环境；`src/main.ts` 注册 Pinia + Element Plus + 路由；`src/router/index.ts` stub 等 F3 替换）；依赖 `element-plus / pinia / vue-router / axios` + dev `vitest / @vue/test-utils / jsdom`。`9259afa`
- axios 拦截器与 auth store：`src/utils/http.ts`（请求拦截注入 `Authorization: Bearer`、响应拦截 401 时 `auth.clear()` + hash 跳 `#/auth` 兜底）+ `src/stores/auth.ts`（Pinia store，`accessToken` / `refreshToken` / `player` 持久化到 localStorage、`isAuthenticated` getter、`set/clear` actions）+ `src/utils/qty.ts`（Phase 2 占位）。`ca016f6`
- 路由守卫与 token 兑换页：`src/router/index.ts` 3 路由（`/auth` `meta.public` / `/me` / `/` redirect `/me`）+ `beforeEach` 守卫未认证跳 `/auth`；`src/views/AuthExchange.vue` `onMounted` 读 query token → POST `/auth/exchange` → `auth.set` → `router.replace('/me')`，错误用 `el-result` + 后端 `detail`；`src/views/Me.vue` stub（F4 替换）。`da78fa3`
- `/me` 身份页：`src/views/Me.vue` 完整实现（`onMounted` 调 `http.get<Me>('/me')`，`el-card` 展示 UUID / 名称 / 角色；401 由 F2 拦截器统一处理跳 `/auth`）。`6b66c47`
- Phase 3 sheets 表格端：`src/api/sheets.ts`（TS 类型与后端 Pydantic 对齐 + 9 个 axios 封装，复用 `utils/http.ts`）、`src/utils/qty.ts` `formatQty` 真实现（替换占位，与后端 `format_qty` 完全对齐，STACK=64/SHULKER=1728，去尾零等价 Python `:g`）、`src/views/sheets/SheetList.vue`（el-table 列表 + 新建对话框 + 点行跳详情）、`src/views/sheets/SheetEditor.vue`（el-table 行内编辑 + formatQty 换算列 + 备齐 toggle el-tag 绿/灰 + 增行 PUT upsert/删行/改标题 PATCH/删表二次确认；R-9 非 owner/admin 隐藏所有编辑控件只读）、`router/index.ts` 加 `/sheets` 与 `/sheets/:id` 路由（走现有 requiresAuth 守卫）+ `App.vue` 导航、vitest 22 测试（qty 边界 + sheets 客户端 mock）。`afe8ac8` · `e90c909` · `43a17b1` · `5d0a6da` · `7d86d04` · `72f2471`

- `Player.uuid` 字段名遮蔽 `uuid` 模块导致 SQLAlchemy 2.0 延迟解析 `Mapped[...]` 注解时抛 `AttributeError: 'MappedColumn' object has no attribute 'UUID'`。改为 `from uuid import UUID` + `from sqlalchemy.dialects.postgresql import UUID as PG_UUID`，注解用 `Mapped[UUID]`、列定义用 `PG_UUID(as_uuid=True)`。`b324e50`

### 文档与计划

#### Added

- 根 `CLAUDE.md`：项目级规范（命名规范表 · 12 条红线 R-1..R-12 · 特殊约束 S-1 MCDR 联网验证 · S-2 中文输出 · 分布式 CLAUDE.md 体系 · 文档索引）。`407f7d7`
- `CONTRIBUTING.md`：分支模型 · Conventional Commits · 各组件独立 SemVer · MCDR 插件发布（参考 MCDR 标准）。`407f7d7`
- `Docs/architecture.md`：三端架构 · 技术栈 · ADR · 风险矩阵 · 跨服务流程。`407f7d7`
- `Docs/architecture/data-model.md`：全局表结构 · 约束 · 索引 · ER 图。`407f7d7`
- `Docs/architecture/frontend.md`：Vue3 后台模块 · 鉴权 · 构建。`407f7d7`
- `Docs/architecture/services/*.md`：mcdr-plugin · user-service · project-service · scoring-service · title-service · wiki-service · alert-service 各服务文档。`407f7d7`
- `Docs/guied.md`：玩法设计（黄皮子积分体系 · 项目管理 · 荣誉激励 · 风控）。`407f7d7`
- `Docs/Plans/MVP-第一阶段计划.md`：Phase 0-5 高层路线图。`b313e40`
- `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`：23 任务 TDD 细粒度计划，B5 段已标记 ✅ 完成（commit `b324e50`）。`b313e40`，更新于 `36d399b`
- `Docs/Plans/HANDOFF.md`：交接入口（进度 · 环境 · 已联网核实 API · 继续方式）。`b313e40`，更新于 `36d399b`
- `Docs/Plans/无感鉴权方案讨论.md`：鉴权讨论稿（MVP 不实现）。`b313e40`
- 根 `README.md`：项目入口（三端架构图 + 快速启动 + 端口/密钥/网络说明）。`未提交`
- `Docs/McdrPlugin/mcdr-api-cheatsheet.md`：MCDR API 速查（命令节点树 · RText 色彩系统 · `@new_thread`/`schedule_task` 并发模型 · 事件 · RCON；含 §8「常见误区」勘误）。`未提交`
- 分布式子服务 `CLAUDE.md`：`Backend/CLAUDE.md` · `Frontend/CLAUDE.md` · `McdrPlugin/CLAUDE.md`（各服务雷点 + 关键要素 + 文档索引，由 `service-claude-md` skill 生成）。`未提交`

### 项目级

#### Added

- 根 `.gitignore`：忽略 `.env` · `.venv/` · `__pycache__/` · `node_modules/` · `.vite/` 等。`407f7d7`
- `service-claude-md` skill：子服务 `CLAUDE.md` 唯一维护入口（位于 `.claude/skills/service-claude-md/`）。`407f7d7`
- `TestServer/`：V1 端到端验收环境（MC 1.20.1 Fabric 离线模式 + MCDReforged + htcmc_auth → backend → frontend 三端联通）。含 `Dockerfile`（python3.11-slim + openjdk17 + `mcdreforged>=2.14,<3`）、`docker-compose.yml`（`stdin_open + tty` 供 `docker attach` 调试、加入 `pchsystem_default` 外部网络访问 backend）、`entrypoint.sh`（下载 Fabric launcher · 生成 eula · `exec mcdreforged start --auto-init` 前台启动）、`config/mcdr_config.yml`（vanilla_handler + UTF-8 + `advanced_console: false`）、`config/htcmc_auth_config.json`、`plugins/uuid_api_remake.mcdr`、`.gitignore`。`未提交`
- 根 `docker-compose.yml`：调整为与 TestServer 共享 `pchsystem_default` 外部网络；backend 容器名固定 `pchsystem-backend-1` 供 MCDR 解析。`未提交`

---

## 版本化策略

**v0.3.0 已发布**（2026-07-03，三组件分别打 tag：`backend-v0.3.0` / `mcdr-v0.3.0` / `frontend-v0.3.0`，首个真正打 git tag 的版本）。历史 `[v0.1.0]` / `[v0.2.0]` 段为文档预归档（git tag 由开发机补打）。

后续按 [`CONTRIBUTING.md`](./CONTRIBUTING.md) §4 维护：三端各自独立 SemVer，tag 形如 `<component>-vX.Y.Z`，发版时把对应 `[Unreleased]` 段固化为 `## [<component>-vX.Y.Z] - YYYY-MM-DD` 并重置 `[Unreleased]`。
