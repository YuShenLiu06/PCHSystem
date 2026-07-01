# Changelog

本项目所有显著变更记录于此文件。

格式遵循 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)。
各子组件（`Backend/` · `McdrPlugin/` · `Frontend/`）按根 `CLAUDE.md` §5 与
`CONTRIBUTING.md` 约定独立维护各自的 SemVer。

---

## [Unreleased]

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

#### Fixed

- `Backend/.env` 的 `POSTGRES_PASSWORD=pw`（B5 时 pch-pg 容器密码）与 docker compose 启动的 `pchsystem-postgres-1`（用根 `.env` 的 `change_me_strong_random` 初始化）不一致，导致本地 venv alembic 与 B10+ 测试 fixture 认证失败；改为 `change_me_strong_random` 对齐容器。`fc9596a`
- B10 spec 的 async `_truncate` autouse fixture 在 pytest-asyncio 1.4 + 同步测试混合跑时触发 `RuntimeError: Event loop is closed`（autouse async fixture 在同步测试周围也会创建/关闭 loop，asyncpg 池内连接泄漏到已关闭 loop）；控制器级修复改为同步 fixture 用独立 sync engine + 每次 dispose；同时 `app.core.db` 的 async engine 改用 `NullPool` 防止 async 测试跨 loop 复用池内连接。`fc9596a`

### McdrPlugin

#### Added

- MCDR 插件骨架：`mcdreforged.plugin.json`（id `htcmc_auth`、版本 `0.1.0`、依赖 `mcdreforged>=2.14.0` + `uuid_api_remake`）+ `requirements.txt`（`requests>=2.31`）+ `htcmc_auth/{__init__.py, config.py}`（`HtcmcAuthConfig` 4 字段：`api_url` / `service_token` / `http_timeout_seconds` / `http_retries`，`on_load` 加载配置 + 注册 `!!login` 占位命令）。`7a2bc88`
- `!!login` 实现链路：`htcmc_auth/client.py`（HTTP 客户端，超时 + 重试 + 429/403 哨兵字符串）+ `htcmc_auth/__init__.py` 完整 `_login`（`PlayerCommandSource` 校验 → `uuid_api_remake.get_uuid` 推导 → `server.schedule_task` 内异步调后端 → `RText.c(RAction.open_url, url)` 可点击链接回显；红线 R-12 落实，红线 S-1 API 经 HANDOFF 联网核实）。`1d14082`

### Frontend

#### Added

- Vue3 + TypeScript + Vite 脚手架：`Frontend/`（npm 模板 + 自定义 `vite.config.ts` 含 `/api` 代理到后端 8000 与 jsdom 测试环境；`src/main.ts` 注册 Pinia + Element Plus + 路由；`src/router/index.ts` stub 等 F3 替换）；依赖 `element-plus / pinia / vue-router / axios` + dev `vitest / @vue/test-utils / jsdom`。`9259afa`
- axios 拦截器与 auth store：`src/utils/http.ts`（请求拦截注入 `Authorization: Bearer`、响应拦截 401 时 `auth.clear()` + hash 跳 `#/auth` 兜底）+ `src/stores/auth.ts`（Pinia store，`accessToken` / `refreshToken` / `player` 持久化到 localStorage、`isAuthenticated` getter、`set/clear` actions）+ `src/utils/qty.ts`（Phase 2 占位）。`ca016f6`
- 路由守卫与 token 兑换页：`src/router/index.ts` 3 路由（`/auth` `meta.public` / `/me` / `/` redirect `/me`）+ `beforeEach` 守卫未认证跳 `/auth`；`src/views/AuthExchange.vue` `onMounted` 读 query token → POST `/auth/exchange` → `auth.set` → `router.replace('/me')`，错误用 `el-result` + 后端 `detail`；`src/views/Me.vue` stub（F4 替换）。`da78fa3`
- `/me` 身份页：`src/views/Me.vue` 完整实现（`onMounted` 调 `http.get<Me>('/me')`，`el-card` 展示 UUID / 名称 / 角色；401 由 F2 拦截器统一处理跳 `/auth`）。`6b66c47`

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

### 项目级

#### Added

- 根 `.gitignore`：忽略 `.env` · `.venv/` · `__pycache__/` · `node_modules/` · `.vite/` 等。`407f7d7`
- `service-claude-md` skill：子服务 `CLAUDE.md` 唯一维护入口（位于 `.claude/skills/service-claude-md/`）。`407f7d7`

---

## 版本化策略（待启动）

首次发版前，各组件按以下节奏独立打 tag：

- `backend-v0.1.0`：B1-B16 跑完、OpenAPI 契约冻结（B16）
- `mcdr-plugin-v0.1.0`：M1-M2 完成、可发布到 MCDR 插件仓库
- `frontend-v0.1.0`：F1-F4 完成、首屏可访问

打 tag 时把对应 `[Unreleased]` 段落固化为 `## [backend-v0.1.0] - YYYY-MM-DD` 等具名版本段，并重置 `[Unreleased]`。
