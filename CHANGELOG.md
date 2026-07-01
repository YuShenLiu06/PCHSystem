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

#### Fixed

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
