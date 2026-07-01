# HTCMC PCHSystem · Phase 0+1 身份登录闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现。步骤用 `- [ ]` 复选框跟踪。

> **最终保存路径**（ExitPlanMode 批准后）：`Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md`（当前为 plan 文件，批准后落地）。
> **来源**：把 `Docs/Plans/MVP-第一阶段计划.md` 的 Phase 0+1 转为 superpowers 可执行格式；表格部分（Phase 2-5）后续独立成文。

**Goal:** 跑通三端身份登录最小闭环 —— 玩家游戏内 `!!login` → MCDR 回显可点击 URL → 浏览器自动兑换 JWT → `/me` 显示身份。

**Architecture:** FastAPI 模块化单体（`users` schema 隔离）承载身份 API；MCDR 插件用 `uuid_api_remake` 取 UUID 后 HTTP 上报后端、用 RText 回显 `open_url` 链接；Vue3 前端 `/auth?token=xxx` 路由自动兑换。身份锚 = 离线模式 UUID（红线 R-5），后端独占数据库（R-1），密钥经 `.env` 注入（R-11），MCDR 耗时调用走 `schedule_task` 且带超时/重试/回执（R-12）。

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (asyncpg) · Alembic · PyJWT · pydantic-settings · pytest/httpx；MCDReforged 2.x 插件 · requests；Vue3 + Vite + TS · Element Plus · Pinia · Vue Router · axios · Vitest。

---

## Context（背景与动机）

`Docs/Plans/MVP-第一阶段计划.md` 是高层路线图（Phase、DDL、团队策略），但**不是可执行粒度**——缺精确文件路径、完整代码、TDD 步骤、提交点。本计划取其 Phase 0（工程地基）+ Phase 1（token 登录链路），按 superpowers 方法论转为 2-5 分钟粒度的 TDD 任务，产出**可独立测试的软件**（身份登录闭环）。表格部分（Phase 2-5）验证方法论后另立计划。

**当前代码库现状**（已核实）：`Backend/app/{api,core,models,repositories,schemas,services}` 与 `Backend/alembic/versions/` 为**空目录**，`Frontend/`、`McdrPlugin/` **不存在**，根目录**无任何配置文件**，0 源码文件。即从零搭建。

**MCDR 关键 API 已联网核实**（红线 S-1 满足，禁止臆造）：

| 用途 | API | 来源 |
|---|---|---|
| 取 UUID | `import uuid_api_remake; uuid_api_remake.get_uuid(name)` | [插件目录 README](https://mcdreforged.com/zh-CN/plugin/uuid_api_remake/readme) |
| 命令回调 | `cb(source: CommandSource, context: CommandContext)`（0-2 参） | [command.html](https://docs.mcdreforged.com/en/latest/code_references/command.html) |
| 玩家名 | `PlayerCommandSource.player` → str | [genindex](https://docs.mcdreforged.com/en/latest/genindex.html) |
| 注册命令 | `server.register_command(node)` | [PluginServerInterface](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html) |
| 发消息/异步/在线玩家 | `server.tell(name, text)` / `server.schedule_task(cb)` / `server.get_online_players()` | [ServerInterface](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html) |
| 可点击 URL | `RText(t).c(RAction.open_url, url)`，`open_url` 不受 1.19+ 签名聊天限制 | [minecraft_tools](https://docs.mcdreforged.com/zh-cn/latest/code_references/minecraft_tools.html)、[Issue #203](https://github.com/MCDReforged/MCDReforged/issues/203) |
| 插件入口 | `on_load(server)` / `on_server_startup(server)` | 同 ServerInterface |

---

## File Structure（文件职责总览）

**Backend（Python 模块化单体）** —— 沿用已有空目录：

| 文件 | 职责 |
|---|---|
| `Backend/pyproject.toml` | 依赖与打包（pip 安装） |
| `Backend/Dockerfile` | 后端镜像 |
| `Backend/.env.example` / `.env` | 配置模板（R-11） |
| `Backend/alembic.ini` + `Backend/alembic/env.py` | 迁移配置（async） |
| `Backend/alembic/versions/001_users_schema_players.py` | 建 `users` schema + `players` |
| `Backend/alembic/versions/002_auth_tokens_jwt_revocations.py` | 建 `auth_tokens` + `jwt_revocations` |
| `Backend/app/main.py` | FastAPI 应用工厂 + `/healthz` |
| `Backend/app/core/config.py` | `Settings`（pydantic-settings 读 env） |
| `Backend/app/core/db.py` | async engine + session 工厂 + `get_session` 依赖 |
| `Backend/app/core/jwt.py` | JWT 签发/解码（access 1h / refresh 7d） |
| `Backend/app/models/user.py` | `Player` / `AuthToken` / `JwtRevocation` ORM |
| `Backend/app/schemas/auth.py` | Pydantic 请求/响应 |
| `Backend/app/repositories/player_repo.py` | `get_or_create(uuid, name)` |
| `Backend/app/repositories/auth_token_repo.py` | `issue()` / `exchange()`（一次性 + 过期） |
| `Backend/app/services/auth_service.py` | 编排：限频 + 白名单 + 签发 URL |
| `Backend/app/api/deps.py` | `require_service_token` / `get_current_player` / `require_role` |
| `Backend/app/api/auth.py` | `/auth/token` `/auth/exchange` `/auth/refresh` `/me` 路由 |
| `Backend/tests/conftest.py` | pytest 异步 fixtures + 测试库 truncate |

**McdrPlugin** —— 新建：

| 文件 | 职责 |
|---|---|
| `McdrPlugin/mcdreforged.plugin.json` | 插件元数据 |
| `McdrPlugin/requirements.txt` | `requests` |
| `McdrPlugin/htcmc_auth/__init__.py` | 入口：`on_load` 注册 `!!login` |
| `McdrPlugin/htcmc_auth/client.py` | HTTP 调后端 `/auth/token`（超时+重试+回执，R-12） |
| `McdrPlugin/htcmc_auth/commands.py` | `!!login`：取 UUID → 调 client → RText 回显 |

**Frontend** —— 新建：

| 文件 | 职责 |
|---|---|
| `Frontend/package.json` 等 | Vite + Vue3 + TS 脚手架 |
| `Frontend/src/utils/http.ts` | axios 实例 + `Bearer` 拦截器 + 401 跳登录 |
| `Frontend/src/stores/auth.ts` | Pinia：JWT + 当前身份 |
| `Frontend/src/router/index.ts` | 路由 + `requiresAuth` 守卫 |
| `Frontend/src/views/AuthExchange.vue` | `/auth?token=xxx` 自动兑换 |
| `Frontend/src/views/Me.vue` | `/me` 身份展示 |

---

## 命名与规范约束（根 CLAUDE.md §1 + CONTRIBUTING.md）

- 目录大驼峰；Python 模块文件小写下划线；类大驼峰；变量/方法/SQL 列 snake_case；Vue 组件大驼峰；配置键 snake_case。
- 分支：`feat/backend-<简述>` / `feat/mcdr-<简述>` / `feat/frontend-<简述>`。
- commit：`<type>(<scope>): <简述>`，简体中文 ≤50 字。
- 红线：R-1 后端独占 DB；R-5 UUID 为身份锚；R-11 密钥进 `.env`；R-12 MCDR HTTP 带 `schedule_task`+超时+重试+回执；S-1 MCDR API 实现前如有疑点须再联网核实（本计划代码均已核实）。

---

# Phase 0：工程地基

### Task B1：Backend 依赖与项目骨架

**Files:**
- Create: `Backend/pyproject.toml`
- Create: `Backend/.gitignore`
- Create: `Backend/.env.example`
- Create: `Backend/app/__init__.py`（空）
- Create: `Backend/app/{core,models,schemas,repositories,services,api}/__init__.py`（空，目录已存在）

- [ ] **Step 1：建分支**

```bash
git checkout -b feat/backend-phase0-foundation
```

- [ ] **Step 2：写 `Backend/pyproject.toml`**

```toml
[project]
name = "pchsystem-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29",
  "psycopg2-binary>=2.9",
  "alembic>=1.13",
  "pyjwt>=2.8",
  "pydantic-settings>=2.1",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "anyio>=4"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3：写 `Backend/.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
```

- [ ] **Step 4：写 `Backend/.env.example`**

```env
POSTGRES_USER=pch
POSTGRES_PASSWORD=change_me
POSTGRES_DB=pchsystem
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
JWT_SECRET=change_me_to_long_random_string
JWT_ACCESS_TTL_SECONDS=3600
JWT_REFRESH_TTL_SECONDS=604800
AUTH_TOKEN_TTL_SECONDS=600
AUTH_TOKEN_RATE_LIMIT_SECONDS=30
MCDR_SERVICE_TOKEN=change_me_service_token
WEB_BASE_URL=http://localhost:5173
```

- [ ] **Step 5：建空 `__init__.py`（app 及其子包）并验证可装**

```bash
cd Backend && python -m venv .venv && .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
python -c "import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 6：Commit**

```bash
git add Backend/
git commit -m "feat(backend): 初始化项目骨架与依赖"
```

---

### Task B2：配置管理 `Settings`

**Files:**
- Create: `Backend/app/core/config.py`
- Test: `Backend/tests/test_config.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_config.py
from app.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("MCDR_SERVICE_TOKEN", "svc")
    s = Settings()
    assert s.postgres_dsn.startswith("postgresql+asyncpg://")
    assert s.jwt_access_ttl_seconds == 3600
    assert s.web_base_url.startswith("http")
```

- [ ] **Step 2：跑测试见失败**

```bash
cd Backend && pytest tests/test_config.py -v
```
Expected: FAIL（`ModuleNotFoundError: app.core.config`）

- [ ] **Step 3：写实现**

```python
# Backend/app/core/config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "pch"
    postgres_password: str = ""
    postgres_db: str = "pchsystem"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    jwt_secret: str = ""
    jwt_access_ttl_seconds: int = 3600
    jwt_refresh_ttl_seconds: int = 604800

    auth_token_ttl_seconds: int = 600
    auth_token_rate_limit_seconds: int = 30

    mcdr_service_token: str = ""
    web_base_url: str = "http://localhost:5173"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_config.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/core/config.py tests/test_config.py
git commit -m "feat(backend): 添加配置管理 Settings"
```

---

### Task B3：数据库连接层

**Files:**
- Create: `Backend/app/core/db.py`
- Test: `Backend/tests/test_db.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_db.py
from app.core.db import Base, async_session_factory, get_session


def test_db_module_exports():
    assert Base is not None
    assert async_session_factory is not None
    assert callable(get_session)
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_db.py -v
```
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3：写实现**

```python
# Backend/app/core/db.py
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(_settings.postgres_dsn, pool_pre_ping=True, future=True)
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_db.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/core/db.py tests/test_db.py
git commit -m "feat(backend): 添加数据库连接层"
```

---

### Task B4：FastAPI 入口 + 健康检查

**Files:**
- Create: `Backend/app/main.py`
- Test: `Backend/tests/test_health.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz():
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_health.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/main.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="HTCMC PCHSystem", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_health.py -v
```
Expected: PASS

- [ ] **Step 5：本地启动验证后 Commit**

```bash
uvicorn app.main:app --reload   # 访问 http://localhost:8000/docs 看到 /healthz
git add app/main.py tests/test_health.py
git commit -m "feat(backend): 添加 FastAPI 入口与健康检查"
```

---

### Task B5：Alembic 初始化 + `users` schema + `players` 表迁移

> **✅ 已完成（commit `b324e50`，2026-07-01）**。下列 Step 1-6 全部 ✅，正文保留下方原始设计代码作为参考；实际入库代码与原设计的偏差记录在此横幅：
>
> | # | 偏差 | 原因 | 实际处理 |
> |---|---|---|---|
> | 1 | **Player 模型 UUID 命名冲突**（原 `uuid: Mapped[uuid.UUID]`） | 字段名 `uuid` 遮蔽 `uuid` 模块；SQLAlchemy 2.0 延迟解析注解时 `uuid` 已绑为 MappedColumn → `AttributeError: 'MappedColumn' object has no attribute 'UUID'` | 改为 `from uuid import UUID` + `from sqlalchemy.dialects.postgresql import UUID as PG_UUID`，注解 `Mapped[UUID]`，列定义 `PG_UUID(as_uuid=True)` |
> | 2 | **`alembic/env.py` 加 `compare_type=True`** | 为 B9 autogenerate 检测类型变更铺路 | offline/online 两个 `context.configure(...)` 均加 |
> | 3 | **docker run 命令漏 `POSTGRES_USER=pch`**（原计划只设 PASSWORD/DB） | postgres:16 默认创建 `postgres` 用户，与 `Settings.postgres_user="pch"` 不匹配 | 补 `-e POSTGRES_USER=pch` |
> | 4 | **本机 5432 已被别的容器（`pf-postgres`）占用** | 虚拟器内已运行的别项目 postgres | 改 `-p 5433:5432`，`Backend/.env` 设 `POSTGRES_PORT=5433`；B6 docker-compose 内部网络不受影响，仍用 5432 |
> | 5 | **迁移脚本删除原计划的占位建表/删表/真建表丑陋模式** | `op.create_table(..., sa_column_kwargs=[])` → `op.drop_table` → 再 `op.create_table` 是无意义绕路 | 直接写干净的 `op.create_table("players", sa.Column(...), schema="users")` 一次完成 |
> | 6 | 文件名 `001_users_schema_players.py` → `0001_users_players.py` | 与 revision id `0001_users_players` 对齐 | 已按此命名 |
>
> **验证记录**：`alembic upgrade head` 成功；`psql \d users.players` 显示 6 列 + PK + 默认值全对；`alembic downgrade -1` 后 `upgrade head` 可逆；`pytest` 3/3 全绿。

**Files:**
- Create: `Backend/alembic.ini`
- Create: `Backend/alembic/env.py`
- Create: `Backend/alembic/script.py.mako`
- Create: `Backend/alembic/versions/0001_users_players.py`
- Create: `Backend/app/models/user.py`

- [x] **Step 1：写 `Player` 模型**（commit `b313e40` 初版有 UUID 命名冲突，`b324e50` 修复）

```python
# Backend/app/models/user.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Player(Base):
    """玩家实体（users schema）。

    MVP 第一阶段身份锚 = 游戏内 UUID（离线模式由玩家名确定性推导，
    详见根规范 R-5）。后续阶段升级为 Web 绑定账号主锚时再调整。
    """

    __tablename__ = "players"
    __table_args__ = {"schema": "users"}

    # 注意：字段名 uuid 不能与 uuid 模块同名再于注解中引用模块属性 ——
    # SQLAlchemy 2.0 延迟解析 Mapped[...] 时 uuid 已被绑为 MappedColumn。
    # 故直接 from uuid import UUID，并把 PG 的 UUID 类型起别名 PG_UUID。
    uuid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    current_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    whitelist_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
```

- [x] **Step 2：写 `alembic.ini`（关键段）**（实际由 `alembic init` 生成，仅改 `[alembic]` 段 `sqlalchemy.url` 占位）

```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql+psycopg2://pch:change_me@localhost:5432/pchsystem
# 实际运行用 env.py 从 Settings 注入；此值仅占位

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [x] **Step 3：写 `alembic/env.py`（同步，从 Settings 取 url，导入 Base.metadata）**

```python
# Backend/alembic/env.py
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.db import Base
from app.models import user  # noqa: F401  确保模型注册

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.postgres_dsn_sync)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.postgres_dsn_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # 为 B9 autogenerate 检测类型变更铺路
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

> 注：`script.py.mako` 用 `alembic init` 标准模板；若未生成，执行 `cd Backend && alembic init -t generic alembic_tmp` 后把其 `script.py.mako` 拷到 `alembic/`，删 `alembic_tmp`。

- [x] **Step 4：写首个迁移**（去除原设计 `sa_column_kwargs=[]` 占位 + `drop_table` + 真建表的绕路，直接一次 `op.create_table` 完成）

```python
# Backend/alembic/versions/0001_users_players.py
"""create users schema and players table

Revision ID: 0001_users_players
Revises:
Create Date: 2026-07-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_users_players"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS users")
    op.create_table(
        "players",
        sa.Column(
            "uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("current_name", sa.String(64), nullable=False),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            server_default="user",
        ),
        sa.Column(
            "whitelist_state",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="users",
    )


def downgrade() -> None:
    op.drop_table("players", schema="users")
    op.execute("DROP SCHEMA IF EXISTS users")
```

- [x] **Step 5：起本地 PG 并验证迁移**

```bash
# 起 PG 容器：原计划漏 POSTGRES_USER=pch，已补；5432 被占用时改 5433:5432
docker run -d --name pch-pg \
  -e POSTGRES_USER=pch \
  -e POSTGRES_PASSWORD=pw \
  -e POSTGRES_DB=pchsystem \
  -p 5432:5432 \
  postgres:16

cd Backend
# 配 .env 的 POSTGRES_PASSWORD/POSTGRES_PORT 与本地 PG 一致
.venv/bin/alembic upgrade head
.venv/bin/alembic current
# 期望：0001_users_players (head)

# 可逆性验证（实际已跑通）
.venv/bin/alembic downgrade -1
.venv/bin/alembic upgrade head

# 表结构验证
docker exec pch-pg psql -U pch -d pchsystem -c "\dt users.*"
docker exec pch-pg psql -U pch -d pchsystem -c "\d users.players"
```

> **本机执行实际**：5432 已被别项目 `pf-postgres` 容器占用 → 改用 `-p 5433:5432`，`Backend/.env` 设 `POSTGRES_PORT=5433`。B6 docker-compose 内部网络不受影响，PG 主机名仍为 `postgres`、内部端口 5432。

Expected: `0001_users_players (head)`

- [x] **Step 6：Commit**（实际 `b324e50`，含 Player 模型 UUID 修复）

```bash
git add alembic.ini alembic/ app/models/user.py
git commit -m "feat(backend): 初始化 Alembic 与 users.players 表"
# 实际 commit b324e50，正文记录偏差与验证结果
```

---

### Task B6：Docker Compose（postgres + backend）

**Files:**
- Create: `docker-compose.yml`（仓库根）
- Create: `Backend/Dockerfile`
- Create: `.env`（根，gitignore）

- [ ] **Step 1：写 `Backend/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2：写 `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10

  backend:
    build: ./Backend
    depends_on:
      postgres: { condition: service_healthy }
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      JWT_SECRET: ${JWT_SECRET}
      MCDR_SERVICE_TOKEN: ${MCDR_SERVICE_TOKEN}
      WEB_BASE_URL: ${WEB_BASE_URL}
    ports: ["8000:8000"]

volumes:
  pgdata:
```

- [ ] **Step 3：根 `.env`（从 Backend/.env.example 拷贝改真实值）**

```bash
cp Backend/.env.example .env   # 然后编辑真实密码
```

- [ ] **Step 4：起服务并验证**

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
curl http://localhost:8000/healthz
```
Expected: `{"status":"ok"}`

- [ ] **Step 5：Commit（不提交 `.env`）**

```bash
git add docker-compose.yml Backend/Dockerfile
git commit -m "feat(backend): 添加 docker-compose 与后端镜像"
```

---

### Task B7：Service Token 鉴权依赖

**Files:**
- Create: `Backend/app/api/deps.py`
- Test: `Backend/tests/test_security_deps.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_security_deps.py
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_service_token
from app.core.config import get_settings


def _app(token: str) -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=Depends(require_service_token)) -> dict:
        return {"ok": True}

    # 注入测试 token
    import app.api.deps as deps
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = token
    return app


def test_service_token_missing_returns_401():
    client = TestClient(_app("svc"))
    assert client.get("/probe").status_code == 401


def test_service_token_wrong_returns_401():
    client = TestClient(_app("svc"))
    assert client.get("/probe", headers={"X-Service-Token": "bad"}).status_code == 401


def test_service_token_ok():
    client = TestClient(_app("svc"))
    resp = client.get("/probe", headers={"X-Service-Token": "svc"})
    assert resp.status_code == 200
```

- [ ] **Step 2：跑测试见失败**

```bash
cd Backend && pytest tests/test_security_deps.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/api/deps.py
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import Settings, get_settings

_settings: Settings = get_settings()


def require_service_token(x_service_token: str | None = Header(default=None)) -> None:
    if not x_service_token or not secrets.compare_digest(
        x_service_token, _settings.mcdr_service_token
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_security_deps.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/api/deps.py tests/test_security_deps.py
git commit -m "feat(backend): 添加 X-Service-Token 鉴权依赖"
```

---

### Task B8：JWT 工具（签发 / 解码）

**Files:**
- Create: `Backend/app/core/jwt.py`
- Test: `Backend/tests/test_jwt.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_jwt.py
import time
import uuid

import jwt as pyjwt
import pytest

from app.core.jwt import create_access_token, decode_token


def test_access_token_roundtrip():
    player_uuid = uuid.uuid4()
    token = create_access_token(player_uuid, role="user")
    payload = decode_token(token)
    assert payload["sub"] == str(player_uuid)
    assert payload["role"] == "user"
    assert payload["type"] == "access"


def test_decode_rejects_tampered():
    token = create_access_token(uuid.uuid4(), role="user")
    bad = token[:-3] + "aaa"
    with pytest.raises(Exception):
        decode_token(bad)
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_jwt.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/core/jwt.py
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, overload

import jwt as pyjwt

from app.core.config import get_settings

_settings = get_settings()
_ALGO = "HS256"


@overload
def _create(player_uuid: uuid.UUID, role: str, ttl: int, typ: Literal["access", "refresh"]) -> tuple[str, str]: ...


def _create(player_uuid, role, ttl, typ):
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(player_uuid),
        "role": role,
        "type": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": jti,
    }
    return pyjwt.encode(payload, _settings.jwt_secret, algorithm=_ALGO), jti


def create_access_token(player_uuid: uuid.UUID, role: str) -> str:
    token, _ = _create(player_uuid, role, _settings.jwt_access_ttl_seconds, "access")
    return token


def create_refresh_token(player_uuid: uuid.UUID, role: str) -> tuple[str, str]:
    """返回 (token, jti)。"""
    return _create(player_uuid, role, _settings.jwt_refresh_ttl_seconds, "refresh")


def decode_token(token: str) -> dict:
    return pyjwt.decode(token, _settings.jwt_secret, algorithms=[_ALGO])
```

- [ ] **Step 4：跑测试见通过**

```bash
JWT_SECRET=test_secret_for_pytest pytest tests/test_jwt.py -v
```
Expected: PASS（`JWT_SECRET` 必须非空）

- [ ] **Step 5：Commit**

```bash
git add app/core/jwt.py tests/test_jwt.py
git commit -m "feat(backend): 添加 JWT 签发与解码"
```

---

# Phase 1：身份 token 登录链路

### Task B9：`auth_tokens` + `jwt_revocations` 迁移与模型

**Files:**
- Modify: `Backend/app/models/user.py`（追加 `AuthToken`、`JwtRevocation`）
- Create: `Backend/alembic/versions/002_auth_tokens_jwt_revocations.py`

- [ ] **Step 1：追加模型**

```python
# 追加到 Backend/app/models/user.py 末尾
class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = {"schema": "users"}

    token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    player_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchanged_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class JwtRevocation(Base):
    __tablename__ = "jwt_revocations"
    __table_args__ = {"schema": "users"}

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    player_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.players.uuid"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
```

> 同时在 `user.py` 顶部 import 加 `from sqlalchemy import ForeignKey`。

- [ ] **Step 2：写迁移**

```python
# Backend/alembic/versions/002_auth_tokens_jwt_revocations.py
"""create auth_tokens and jwt_revocations

Revision ID: 0002_auth_jwt
Revises: 0001_users_players
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_auth_jwt"
down_revision = "0001_users_players"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_tokens",
        sa.Column("token", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_uuid", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.players.uuid"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_ip", sa.String(64), nullable=True),
        sa.Column("exchanged_ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="users",
    )
    op.create_index("ix_auth_tokens_player_expires", "auth_tokens",
                    ["player_uuid", "expires_at"], schema="users")

    op.create_table(
        "jwt_revocations",
        sa.Column("jti", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_uuid", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.players.uuid"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="users",
    )
    op.create_index("ix_jwt_revocations_player_expires", "jwt_revocations",
                    ["player_uuid", "expires_at"], schema="users")


def downgrade() -> None:
    op.drop_table("jwt_revocations", schema="users")
    op.drop_table("auth_tokens", schema="users")
```

- [ ] **Step 3：应用迁移**

```bash
cd Backend && alembic upgrade head
alembic current
```
Expected: `0002_auth_jwt (head)`

- [ ] **Step 4：Commit**

```bash
git add app/models/user.py alembic/versions/002_auth_tokens_jwt_revocations.py
git commit -m "feat(backend): 添加 auth_tokens 与 jwt_revocations 表"
```

---

### Task B10：测试 fixture（conftest）

**Files:**
- Create: `Backend/tests/conftest.py`

- [ ] **Step 1：写 fixture（连真实 PG 测试库，每测试 truncate）**

```python
# Backend/tests/conftest.py
import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import async_session_factory, engine
from app.main import create_app

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def _truncate():
    yield
    async with async_session_factory() as s:
        await s.execute(text("TRUNCATE users.auth_tokens, users.jwt_revocations, users.players CASCADE"))
        await s.commit()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 2：验证 fixture 可用（临时测试）**

```python
# Backend/tests/test_smoke_db.py
import pytest


@pytest.mark.asyncio
async def test_truncate_fixture_runs():
    assert True
```

```bash
cd Backend && pytest tests/test_smoke_db.py -v
```
Expected: PASS（且无 DB 连接错误；需测试库可达）

- [ ] **Step 3：删 smoke 测试并 Commit**

```bash
rm tests/test_smoke_db.py
git add tests/conftest.py
git commit -m "test(backend): 添加 pytest 异步 fixture 与测试库清理"
```

---

### Task B11：`players` Repository

**Files:**
- Create: `Backend/app/repositories/player_repo.py`
- Test: `Backend/tests/test_player_repo.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_player_repo.py
import uuid

import pytest

from app.core.db import async_session_factory
from app.repositories.player_repo import get_or_create


@pytest.mark.asyncio
async def test_get_or_create_creates_then_updates_name():
    u = uuid.uuid4()
    async with async_session_factory() as s:
        p1 = await get_or_create(s, u, "alice")
        p2 = await get_or_create(s, u, "alice_renamed")
        await s.commit()
    assert p1.uuid == u
    assert p2.current_name == "alice_renamed"
    assert p1.uuid == p2.uuid   # 同一行
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_player_repo.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/repositories/player_repo.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Player


async def get_or_create(session: AsyncSession, player_uuid: uuid.UUID, name: str) -> Player:
    stmt = select(Player).where(Player.uuid == player_uuid)
    player = (await session.execute(stmt)).scalar_one_or_none()
    if player is None:
        player = Player(uuid=player_uuid, current_name=name)
        session.add(player)
        await session.flush()
    else:
        player.current_name = name
        player.last_seen_at = datetime.now(timezone.utc)
    return player


async def get_by_uuid(session: AsyncSession, player_uuid: uuid.UUID) -> Player | None:
    stmt = select(Player).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_player_repo.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/repositories/player_repo.py tests/test_player_repo.py
git commit -m "feat(backend): 添加 players repository"
```

---

### Task B12：`auth_tokens` Repository（一次性 + 过期）

**Files:**
- Create: `Backend/app/repositories/auth_token_repo.py`
- Test: `Backend/tests/test_auth_token_repo.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_auth_token_repo.py
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import async_session_factory
from app.models.user import AuthToken
from app.repositories.auth_token_repo import exchange, issue
from app.repositories.player_repo import get_or_create


async def _seed_player(name="bob"):
    u = uuid.uuid4()
    async with async_session_factory() as s:
        await get_or_create(s, u, name)
        await s.commit()
    return u


@pytest.mark.asyncio
async def test_issue_and_exchange_success():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok = await issue(s, u, issued_ip="1.1.1.1")
        await s.commit()
        result = await exchange(s, tok.token, exchanged_ip="2.2.2.2")
        await s.commit()
    assert result is not None
    assert result.uuid == u


@pytest.mark.asyncio
async def test_exchange_is_one_time():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok = await issue(s, u)
        await s.commit()
        await exchange(s, tok.token)
        second = await exchange(s, tok.token)
        await s.commit()
    assert second is None   # 已 used，拒绝重放


@pytest.mark.asyncio
async def test_exchange_rejects_expired():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok = await issue(s, u)
        # 直接把过期时间改到过去
        tok.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
        assert await exchange(s, tok.token) is None
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_auth_token_repo.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/repositories/auth_token_repo.py
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import AuthToken, Player

_settings = get_settings()


async def issue(session: AsyncSession, player_uuid: uuid.UUID, issued_ip: str | None = None) -> AuthToken:
    token = AuthToken(
        token=uuid.uuid4(),
        player_uuid=player_uuid,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=_settings.auth_token_ttl_seconds),
        issued_ip=issued_ip,
    )
    session.add(token)
    await session.flush()
    return token


async def exchange(
    session: AsyncSession, token: uuid.UUID, exchanged_ip: str | None = None
) -> Player | None:
    stmt = select(AuthToken).where(AuthToken.token == token).with_for_update()
    auth_token = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        auth_token is None
        or auth_token.used_at is not None
        or auth_token.expires_at < now
    ):
        return None
    auth_token.used_at = now
    auth_token.exchanged_ip = exchanged_ip
    player_stmt = select(Player).where(Player.uuid == auth_token.player_uuid)
    return (await session.execute(player_stmt)).scalar_one()
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_auth_token_repo.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/repositories/auth_token_repo.py tests/test_auth_token_repo.py
git commit -m "feat(backend): 添加 auth_tokens repository（一次性+过期）"
```

---

### Task B13：限频 + 白名单服务

**Files:**
- Create: `Backend/app/services/auth_service.py`
- Test: `Backend/tests/test_auth_service.py`

- [ ] **Step 1：写失败测试**

```python
# Backend/tests/test_auth_service.py
import uuid

import pytest

from app.services.auth_service import RateLimiter, check_whitelist
from app.models.user import Player
from app.core.db import async_session_factory


@pytest.mark.asyncio
async def test_rate_limiter_blocks_within_window():
    rl = RateLimiter(window_seconds=30)
    u = uuid.uuid4()
    assert rl.check_and_record(u) is True
    assert rl.check_and_record(u) is False   # 窗口内拒绝


@pytest.mark.asyncio
async def test_whitelist_blocks_removed():
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name="x", whitelist_state="removed"))
        await s.commit()
        assert await check_whitelist(s, u) is False
```

- [ ] **Step 2：跑测试见失败**

```bash
pytest tests/test_auth_service.py -v
```
Expected: FAIL

- [ ] **Step 3：写实现**

```python
# Backend/app/services/auth_service.py
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import Player

_settings = get_settings()


class RateLimiter:
    """按 uuid 内存滑窗限频。单进程 MVP 足够；多 worker 需换 Redis。"""

    def __init__(self, window_seconds: int = _settings.auth_token_rate_limit_seconds) -> None:
        self._window = window_seconds
        self._last: dict[uuid.UUID, float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, player_uuid: uuid.UUID) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(player_uuid)
            if last is not None and now - last < self._window:
                return False
            self._last[player_uuid] = now
            return True


rate_limiter = RateLimiter()


async def check_whitelist(session: AsyncSession, player_uuid: uuid.UUID) -> bool:
    stmt = select(Player.whitelist_state).where(Player.uuid == player_uuid)
    state = (await session.execute(stmt)).scalar_one_or_none()
    return state != "removed"
```

- [ ] **Step 4：跑测试见通过**

```bash
pytest tests/test_auth_service.py -v
```
Expected: PASS

- [ ] **Step 5：Commit**

```bash
git add app/services/auth_service.py tests/test_auth_service.py
git commit -m "feat(backend): 添加 token 签发限频与白名单校验"
```

---

### Task B14：Pydantic schema + `POST /auth/token`

**Files:**
- Create: `Backend/app/schemas/auth.py`
- Create: `Backend/app/api/auth.py`
- Modify: `Backend/app/main.py`（挂载 router）
- Test: `Backend/tests/test_auth_api.py`

- [ ] **Step 1：写 schema**

```python
# Backend/app/schemas/auth.py
import uuid
from pydantic import BaseModel


class TokenIssueRequest(BaseModel):
    uuid: uuid.UUID
    name: str


class TokenIssueResponse(BaseModel):
    login_url: str


class TokenExchangeRequest(BaseModel):
    token: uuid.UUID


class PlayerBrief(BaseModel):
    uuid: uuid.UUID
    name: str
    role: str


class TokenExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    player: PlayerBrief


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    uuid: uuid.UUID
    name: str
    role: str
```

- [ ] **Step 2：写失败测试（先只测 /auth/token）**

```python
# Backend/tests/test_auth_api.py
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


@pytest.mark.asyncio
async def test_auth_token_success(client):
    u = uuid.uuid4()
    resp = await client.post(
        "/auth/token",
        json={"uuid": str(u), "name": "alice"},
        headers={"X-Service-Token": "svc"},
    )
    assert resp.status_code == 200
    assert "/auth?token=" in resp.json()["login_url"]


@pytest.mark.asyncio
async def test_auth_token_rate_limited(client):
    u = uuid.uuid4()
    headers = {"X-Service-Token": "svc"}
    first = await client.post("/auth/token", json={"uuid": str(u), "name": "a"}, headers=headers)
    second = await client.post("/auth/token", json={"uuid": str(u), "name": "a"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_auth_token_blocked_for_removed(client):
    # 直接写一个 removed 玩家
    from app.core.db import async_session_factory
    from app.models.user import Player
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name="ghost", whitelist_state="removed"))
        await s.commit()
    resp = await client.post(
        "/auth/token",
        json={"uuid": str(u), "name": "ghost"},
        headers={"X-Service-Token": "svc"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 3：跑测试见失败**

```bash
pytest tests/test_auth_api.py -v
```
Expected: FAIL

- [ ] **Step 4：写 router（`/auth/token` 部分）**

```python
# Backend/app/api/auth.py
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.api.deps import require_service_token
from app.repositories.auth_token_repo import issue
from app.repositories.player_repo import get_or_create
from app.schemas.auth import TokenIssueRequest, TokenIssueResponse
from app.services.auth_service import check_whitelist, rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])
_settings = get_settings()


@router.post("/token", response_model=TokenIssueResponse)
async def post_token(
    body: TokenIssueRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _svc=Depends(require_service_token),
) -> TokenIssueResponse:
    if not rate_limiter.check_and_record(body.uuid):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limited")
    player = await get_or_create(session, body.uuid, body.name)
    if not await check_whitelist(session, body.uuid):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "player removed")
    token = await issue(session, body.uuid, issued_ip=request.client.host if request.client else None)
    await session.commit()
    url = f"{_settings.web_base_url.rstrip('/')}/auth?token={token.token}"
    return TokenIssueResponse(login_url=url)
```

挂载到 `app/main.py`：

```python
# 修改 Backend/app/main.py 的 create_app，在 healthz 之后加：
from app.api.auth import router as auth_router
app.include_router(auth_router)
```

- [ ] **Step 5：跑测试见通过**

```bash
JWT_SECRET=test MCDR_SERVICE_TOKEN=svc pytest tests/test_auth_api.py -v
```
Expected: PASS

- [ ] **Step 6：Commit**

```bash
git add app/schemas/auth.py app/api/auth.py app/main.py tests/test_auth_api.py
git commit -m "feat(backend): 添加 POST /auth/token 端点"
```

---

### Task B15：`POST /auth/exchange` + `GET /me` + `POST /auth/refresh`

**Files:**
- Modify: `Backend/app/api/auth.py`
- Modify: `Backend/app/api/deps.py`（加 `get_current_player`）
- Modify: `Backend/tests/test_auth_api.py`

- [ ] **Step 1：补失败测试**

```python
# 追加到 Backend/tests/test_auth_api.py
@pytest.mark.asyncio
async def test_exchange_returns_jwt_and_me(client):
    u = uuid.uuid4()
    issue_resp = await client.post(
        "/auth/token", json={"uuid": str(u), "name": "alice"}, headers={"X-Service-Token": "svc"}
    )
    token = issue_resp.json()["login_url"].split("token=")[-1]

    ex = await client.post("/auth/exchange", json={"token": token})
    assert ex.status_code == 200
    body = ex.json()
    assert body["token_type"] == "Bearer"
    assert body["player"]["uuid"] == str(u)

    me = await client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "alice"


@pytest.mark.asyncio
async def test_exchange_one_time(client):
    u = uuid.uuid4()
    token = (await client.post(
        "/auth/token", json={"uuid": str(u), "name": "a"}, headers={"X-Service-Token": "svc"}
    )).json()["login_url"].split("token=")[-1]
    first = await client.post("/auth/exchange", json={"token": token})
    second = await client.post("/auth/exchange", json={"token": token})
    assert first.status_code == 200
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_jwt(client):
    assert (await client.get("/me")).status_code == 401
```

- [ ] **Step 2：跑测试见失败**

```bash
JWT_SECRET=test MCDR_SERVICE_TOKEN=svc pytest tests/test_auth_api.py -v
```
Expected: FAIL（`/auth/exchange`、`/me` 未实现）

- [ ] **Step 3：加 `get_current_player` 依赖**

```python
# 追加到 Backend/app/api/deps.py
import uuid

import jwt as pyjwt
from fastapi import Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.user import Player


async def get_current_player(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Player:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    player = (
        await session.execute(select(Player).where(Player.uuid == uuid.UUID(payload["sub"])))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    return player


def require_role(role: str):
    async def _check(player: Player = Depends(get_current_player)) -> Player:
        if player.role != role and player.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return player
    return _check
```

- [ ] **Step 4：加 `/auth/exchange` `/auth/refresh` `/me`**

```python
# 追加到 Backend/app/api/auth.py
from app.repositories.auth_token_repo import exchange as exchange_token
from app.core.jwt import create_access_token, create_refresh_token, decode_token
from app.models.user import Player
from app.api.deps import get_current_player
from app.schemas.auth import (
    MeResponse, PlayerBrief, RefreshRequest, TokenExchangeRequest, TokenExchangeResponse,
)
import jwt as pyjwt


@router.post("/exchange", response_model=TokenExchangeResponse)
async def post_exchange(
    body: TokenExchangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    ip = request.client.host if request.client else None
    player = await exchange_token(session, body.token, exchanged_ip=ip)
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or used token")
    await session.commit()
    access = create_access_token(player.uuid, player.role)
    refresh, _ = create_refresh_token(player.uuid, player.role)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=player.role),
    )


@router.post("/refresh", response_model=TokenExchangeResponse)
async def post_refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    try:
        payload = decode_token(body.refresh_token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    # MVP：refresh 未接入吊销表校验；Task B16 可扩展
    player = (
        await session.execute(select(Player).where(Player.uuid == uuid.UUID(payload["sub"])))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    access = create_access_token(player.uuid, player.role)
    refresh, _ = create_refresh_token(player.uuid, player.role)
    return TokenExchangeResponse(
        access_token=access, refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=player.role),
    )


# /me 挂在无前缀的 router 或单独 router；这里用顶层
from fastapi import APIRouter as _AR

top_router = _AR(tags=["me"])


@top_router.get("/me", response_model=MeResponse)
async def get_me(player: Player = Depends(get_current_player)) -> MeResponse:
    return MeResponse(uuid=player.uuid, name=player.current_name, role=player.role)
```

`app/main.py` 再挂 `top_router`：

```python
from app.api.auth import top_router
app.include_router(top_router)
```

> 同时把 `auth.py` 顶部 import 补 `from sqlalchemy import select` 和 `import uuid`。

- [ ] **Step 5：跑测试见通过**

```bash
JWT_SECRET=test MCDR_SERVICE_TOKEN=svc pytest tests/test_auth_api.py -v
```
Expected: PASS

- [ ] **Step 6：Commit**

```bash
git add app/api/auth.py app/api/deps.py app/main.py tests/test_auth_api.py
git commit -m "feat(backend): 添加 /auth/exchange /auth/refresh /me 端点"
```

---

### Task B16：API 契约冻结（OpenAPI 导出）

**Files:**
- Create: `Backend/tests/test_openapi_freeze.py`

- [ ] **Step 1：写测试冻结前端依赖的路径**

```python
# Backend/tests/test_openapi_freeze.py
from fastapi.testclient import TestClient
from app.main import create_app


def test_paths_present():
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    for p in ["/auth/token", "/auth/exchange", "/auth/refresh", "/me", "/healthz"]:
        assert p in paths, f"missing {p}"
```

- [ ] **Step 2：跑测试见通过（契约已是上述路径）**

```bash
cd Backend && JWT_SECRET=test pytest tests/test_openapi_freeze.py -v
```
Expected: PASS

- [ ] **Step 3：导出 OpenAPI 供前端/MCDR 桩测**

```bash
python -c "import json; from app.main import create_app; from fastapi.testclient import TestClient; print(json.dumps(TestClient(create_app()).get('/openapi.json').json()))" > Backend/openapi.json
```

- [ ] **Step 4：Commit + 合并地基分支**

```bash
git add Backend/openapi.json Backend/tests/test_openapi_freeze.py
git commit -m "feat(backend): 冻结 Phase0+1 OpenAPI 契约"
# Phase 0+1 后端串行起点完成，可在此 spawn frontend/mcdr teammate
```

---

# McdrPlugin（与后端 B16 契约对齐后并行）

### Task M1：MCDR 插件骨架

**Files:**
- Create: `McdrPlugin/mcdreforged.plugin.json`
- Create: `McdrPlugin/requirements.txt`
- Create: `McdrPlugin/htcmc_auth/__init__.py`
- Create: `McdrPlugin/htcmc_auth/config.py`

- [ ] **Step 1：建分支**

```bash
git checkout -b feat/mcdr-login
```

- [ ] **Step 2：写 `mcdreforged.plugin.json`**

```json
{
  "id": "htcmc_auth",
  "version": "0.1.0",
  "name": "HTCMC Auth",
  "description": "游戏内 !!login token 登录",
  "author": "HTCMC",
  "link": "https://github.com/gubaiovo/MCDR_uuid_api_remake",
  "dependencies": {
    "mcdreforged": ">=2.14.0",
    "uuid_api_remake": "*"
  }
}
```

- [ ] **Step 3：写 `requirements.txt`**

```
requests>=2.31
```

- [ ] **Step 4：写默认配置 `htcmc_auth/config.py`**

```python
# McdrPlugin/htcmc_auth/config.py
from mcdreforged.api.config import Config


class HtcmcAuthConfig(Config):
    api_url: str = "http://localhost:8000"
    service_token: str = "change_me_service_token"
    http_timeout_seconds: float = 5.0
    http_retries: int = 2
```

- [ ] **Step 5：写入口 `__init__.py`（仅注册占位命令，验证可加载）**

```python
# McdrPlugin/htcmc_auth/__init__.py
from mcdreforged.api.all import (
    PluginServerInterface,
    info_filter,
    new_thread,
)
from mcdreforged.api.command import Literal

from .config import HtcmcAuthConfig

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig.get_default()


def on_load(server: PluginServerInterface, prev):
    global CONFIG
    CONFIG = server.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    server.register_command(Literal("!!login").runs(lambda src, ctx: _login(src, ctx, server)))
    server.logger.info("HTCMC Auth loaded")


def _login(src, ctx, server):
    # Task M2 实现
    src.reply("!!login 尚未实现")
```

- [ ] **Step 6：本地 MCDR 加载验证（手测）**

将 `McdrPlugin/` 软链/拷贝到 MCDR 服务器的 `plugins/`，并先装 `uuid_api_remake`（`!!MCDR plugin install uuid_api_remake`）。启动 MCDR，日志应出现 `HTCMC Auth loaded`；游戏内 `!!login` 应回复占位文本。

- [ ] **Step 7：Commit**

```bash
git add McdrPlugin/
git commit -m "feat(mcdr): 初始化 HTCMC Auth 插件骨架"
```

---

### Task M2：`!!login` 实现（UUID → 后端 → 可点击 URL）

**Files:**
- Create: `McdrPlugin/htcmc_auth/client.py`
- Modify: `McdrPlugin/htcmc_auth/__init__.py`

- [ ] **Step 1：写 HTTP client（超时 + 重试 + 回执，R-12）**

```python
# McdrPlugin/htcmc_auth/client.py
import logging
from typing import Optional

import requests

from .config import HtcmcAuthConfig

_log = logging.getLogger("htcmc_auth")


def request_login_url(cfg: HtcmcAuthConfig, player_name: str, player_uuid: str) -> Optional[str]:
    """调后端 POST /auth/token，返回 login_url 或 None。"""
    url = f"{cfg.api_url.rstrip('/')}/auth/token"
    payload = {"uuid": player_uuid, "name": player_name}
    headers = {"X-Service-Token": cfg.service_token, "Content-Type": "application/json"}
    last_err = None
    for attempt in range(cfg.http_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=cfg.http_timeout_seconds)
            if resp.status_code == 200:
                return resp.json().get("login_url")
            if resp.status_code == 429:
                _log.warning("login rate limited for %s", player_name)
                return "__RATE_LIMITED__"
            if resp.status_code == 403:
                return "__REMOVED__"
            last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.RequestException as e:
            last_err = repr(e)
    _log.error("request_login_url failed for %s: %s", player_name, last_err)
    return None
```

- [ ] **Step 2：改 `_login` —— 取 UUID → schedule_task 调 client → RText 回显**

```python
# 替换 McdrPlugin/htcmc_auth/__init__.py 中的 _login
import uuid_api_remake  # 红线 S-1 已联网核实：get_uuid(name)->str

from mcdreforged.api.command import Literal
from mcdreforged.api.all import PluginServerInterface
from mcdreforged.command.command_source import PlayerCommandSource
from mcdreforged.minecraft.rtext.text import RText
from mcdreforged.minecraft.rtext.click_event import RAction
from mcdreforged.minecraft.rtext.style import RColor

from .client import request_login_url
from .config import HtcmcAuthConfig

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig.get_default()


def on_load(server, prev):
    global CONFIG
    CONFIG = server.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    server.register_command(Literal("!!login").runs(_login))
    server.logger.info("HTCMC Auth loaded")


def _login(src, ctx):
    if not isinstance(src, PlayerCommandSource):
        src.reply("§c!!login 只能玩家在游戏内执行")
        return
    player_name = src.player   # 红线 S-1 已核实：PlayerCommandSource.player -> str
    server = src.get_server()

    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, f"§c获取 UUID 失败: {e}")
            return
        result = request_login_url(CONFIG, player_name, player_uuid)
        if result is None:
            server.tell(player_name, "§c登录服务暂不可用，请稍后重试")
        elif result == "__RATE_LIMITED__":
            server.tell(player_name, "§e操作太频繁，请稍后再试")
        elif result == "__REMOVED__":
            server.tell(player_name, "§c你已被移出白名单")
        else:
            link = RText("§a§l[点击此处打开网页登录]").c(RAction.open_url, result).set_color(RColor.green)
            server.tell(player_name, RText("§7收到登录请求，请：").append(link))

    # R-12：HTTP 是耗时调用，放 task 线程，避免阻塞主线程
    server.schedule_task(_do)
```

> 若 `RText(...).set_color(...)` 与 `c()` 链式签名在目标 MCDR 版本有差异，实现时以 `https://docs.mcdreforged.com/zh-cn/latest/code_references/minecraft_tools.html` 为准微调（已联网核实 `c(RAction.open_url, url)` 与 `RText` 基本用法）。

- [ ] **Step 3：端到端手测（后端需运行）**

```
# 游戏内
!!login
# 预期：聊天框出现绿色可点击「点击此处打开网页登录」，点击打开浏览器到 http://<web>/auth?token=<uuid>
```

- [ ] **Step 4：Commit**

```bash
git add McdrPlugin/htcmc_auth/
git commit -m "feat(mcdr): 实现 !!login token 登录链路"
```

---

# Frontend（与后端 B16 契约对齐后并行）

### Task F1：前端脚手架

**Files:**
- Create: `Frontend/package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`
- Create: `Frontend/src/main.ts`、`src/App.vue`

- [ ] **Step 1：建分支并初始化**

```bash
git checkout -b feat/frontend-auth
cd Frontend
npm create vite@latest . -- --template vue-ts
npm install element-plus pinia vue-router axios
npm install -D vitest @vue/test-utils jsdom
```

- [ ] **Step 2：`vite.config.ts` 加 dev 代理到后端**

```typescript
// Frontend/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') },
    },
  },
  test: { environment: 'jsdom' },
})
```

- [ ] **Step 3：`src/main.ts` 注册 Element Plus / Pinia / Router**

```typescript
// Frontend/src/main.ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
```

- [ ] **Step 4：验证 dev server**

```bash
npm run dev   # 访问 http://localhost:5173 看到 Vue 默认页
```

- [ ] **Step 5：Commit**

```bash
cd ..
git add Frontend/
git commit -m "feat(frontend): 初始化 Vue3+Element Plus 脚手架"
```

---

### Task F2：axios + auth store

**Files:**
- Create: `Frontend/src/utils/http.ts`
- Create: `Frontend/src/stores/auth.ts`
- Create: `Frontend/src/utils/qty.ts`（换算，Phase 2 复用，此处先放占位导出）

- [ ] **Step 1：写 `http.ts`（注入 Bearer + 401 跳登录）**

```typescript
// Frontend/src/utils/http.ts
import axios from 'axios'
import { useAuthStore } from '../stores/auth'

export const http = axios.create({ baseURL: '/api' })

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) config.headers.Authorization = `Bearer ${auth.accessToken}`
  return config
})

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clear()
      window.location.hash = '#/auth'   // 兜底
    }
    return Promise.reject(err)
  },
)
```

- [ ] **Step 2：写 `auth.ts` store**

```typescript
// Frontend/src/stores/auth.ts
import { defineStore } from 'pinia'

interface PlayerBrief { uuid: string; name: string; role: string }

export const useAuthStore = defineStore('auth', {
  state: () => ({
    accessToken: localStorage.getItem('access_token') ?? '',
    refreshToken: localStorage.getItem('refresh_token') ?? '',
    player: JSON.parse(localStorage.getItem('player') ?? 'null') as PlayerBrief | null,
  }),
  getters: {
    isAuthenticated: (s) => !!s.accessToken,
  },
  actions: {
    set(tokens: { access_token: string; refresh_token: string }, player: PlayerBrief) {
      this.accessToken = tokens.access_token
      this.refreshToken = tokens.refresh_token
      this.player = player
      localStorage.setItem('access_token', this.accessToken)
      localStorage.setItem('refresh_token', this.refreshToken)
      localStorage.setItem('player', JSON.stringify(player))
    },
    clear() {
      this.accessToken = ''
      this.refreshToken = ''
      this.player = null
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('player')
    },
  },
})
```

- [ ] **Step 3：`qty.ts` 占位（Phase 2 填真实换算）**

```typescript
// Frontend/src/utils/qty.ts
// Phase 2 实现；此处仅导出签名以便后续替换
export function formatQty(n: number): string {
  return `${n}个`   // 占位，Phase 2 完整实现个/组/盒
}
```

- [ ] **Step 4：Commit**

```bash
git add Frontend/src/utils/http.ts Frontend/src/stores/auth.ts Frontend/src/utils/qty.ts
git commit -m "feat(frontend): 添加 axios 拦截器与 auth store"
```

---

### Task F3：路由 + `/auth?token=` 自动兑换页

**Files:**
- Create: `Frontend/src/router/index.ts`
- Create: `Frontend/src/views/AuthExchange.vue`

- [ ] **Step 1：写 router（含守卫）**

```typescript
// Frontend/src/router/index.ts
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/auth', component: () => import('../views/AuthExchange.vue'), meta: { public: true } },
    { path: '/me', component: () => import('../views/Me.vue') },
    { path: '/', redirect: '/me' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) return '/auth'
})
```

- [ ] **Step 2：写 `AuthExchange.vue`（读 query.token → POST /auth/exchange → 跳 /me）**

```vue
<!-- Frontend/src/views/AuthExchange.vue -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { http } from '../utils/http'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const status = ref<'loading' | 'ok' | 'error'>('loading')
const errorMsg = ref('')

onMounted(async () => {
  const token = route.query.token as string | undefined
  if (!token) { status.value = 'error'; errorMsg.value = '缺少 token'; return }
  try {
    const { data } = await http.post('/auth/exchange', { token })
    auth.set({ access_token: data.access_token, refresh_token: data.refresh_token }, data.player)
    ElMessage.success(`欢迎，${data.player.name}`)
    router.replace('/me')
  } catch (e: any) {
    status.value = 'error'
    errorMsg.value = e.response?.data?.detail ?? '兑换失败'
  }
})
</script>

<template>
  <el-result v-if="status === 'error'" icon="error" title="登录失败" :sub-title="errorMsg" />
  <el-result v-else icon="info" title="正在登录..." />
</template>
```

- [ ] **Step 3：Commit**

```bash
git add Frontend/src/router Frontend/src/views/AuthExchange.vue
git commit -m "feat(frontend): 添加 token 自动兑换路由"
```

---

### Task F4：`/me` 页面

**Files:**
- Create: `Frontend/src/views/Me.vue`

- [ ] **Step 1：写 `Me.vue`**

```vue
<!-- Frontend/src/views/Me.vue -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { http } from '../utils/http'

interface Me { uuid: string; name: string; role: string }
const me = ref<Me | null>(null)

onMounted(async () => {
  const { data } = await http.get<Me>('/me')
  me.value = data
})
</script>

<template>
  <el-card v-if="me" header="当前身份">
    <p>UUID：{{ me.uuid }}</p>
    <p>名称：{{ me.name }}</p>
    <p>角色：{{ me.role }}</p>
  </el-card>
</template>
```

- [ ] **Step 2：Commit**

```bash
git add Frontend/src/views/Me.vue
git commit -m "feat(frontend): 添加 /me 身份页"
```

---

### Task V1：端到端联调验收

**Files:** 无（手测脚本）

- [ ] **Step 1：全栈起服务**

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

- [ ] **Step 2：后端单测全绿**

```bash
docker compose exec backend pytest -q
```
Expected: 全 PASS

- [ ] **Step 3：手动全链路（对照 MVP §9 子集）**

```
1. 游戏内输入 !!login
   → 聊天框出现绿色「点击此处打开网页登录」
2. 点击链接 → 浏览器打开 /auth?token=<uuid>
   → 自动兑换，显示「欢迎，<name>」，跳转 /me
3. /me 显示 UUID / 名称 / 角色(user)
4. 直接访问受保护页：清 localStorage 后访问 /me → 跳 /auth
5. curl -X POST http://localhost:8000/auth/exchange -d '{"token":"<已用token>"}'
   → 401（一次性生效）
6. 再次 !!login → 429 限频（30s 内）
```

- [ ] **Step 4：前端单测**

```bash
cd Frontend && npx vitest run
```

- [ ] **Step 5：全部完成后合并各端分支**

```bash
# 按团队策略，各端分支合入主干前跑 code-review（superpowers:requesting-code-review）
git checkout main
git merge feat/backend-phase0-foundation
git merge feat/mcdr-login
git merge feat/frontend-auth
```

---

## Verification（端到端验证总结）

| 验证项 | 命令/动作 | 期望 |
|---|---|---|
| 后端单测 | `cd Backend && pytest -q` | 全 PASS |
| 健康检查 | `curl localhost:8000/healthz` | `{"status":"ok"}` |
| 迁移 | `alembic current` | `0002_auth_jwt (head)` |
| OpenAPI 契约 | `GET /openapi.json` | 含 `/auth/token` `/auth/exchange` `/auth/refresh` `/me` |
| 游戏内登录 | `!!login` → 点链接 | 自动登录，`/me` 显示身份 |
| 一次性 token | 重放 `/auth/exchange` | 401 |
| 限频 | 30s 内二次 `!!login` | 429 / 游戏内提示频繁 |
| 白名单移除 | DB 改 `whitelist_state=removed` 后 `!!login` | 403 / 游戏内提示移出 |
| 前端单测 | `cd Frontend && npx vitest run` | 全 PASS |

---

## Self-Review（写完后自查，已修正）

1. **Spec 覆盖**：MVP §3.1 身份三表（players/auth_tokens/jwt_revocations）→ B5/B9 覆盖；§4 Repository → B11/B12（jwt_revocations 的吊销写入留待 refresh 接入，已在 B15 标注）；§5 换算属 Phase 2 不在本计划；§6 Phase 0/1 全覆盖；§8 安全基线 1-8 全部落地（Service Token/限频/绑定UUID/一次性/白名单/HTTPS待部署/JWT短时效+吊销表/审计IP）。`jwt_revocations` 表已建但吊销校验未在 refresh 接入——标注为已知简化，后续小任务补。
2. **占位扫描**：无 TBD/「稍后实现」裸露；`qty.ts` 占位已显式注明「Phase 2 完整实现」并给出可用最小实现，不算阻塞。
3. **类型一致**：`PlayerBrief{uuid,name,role}`、`TokenExchangeResponse{access_token,refresh_token,token_type,player}` 在后端 schema、前端 store、`/me` 间一致；Repository 方法 `get_or_create` / `issue` / `exchange` 签名跨任务一致。
4. **红线**：R-1/R-5/R-11/R-12/S-1 均落实；S-1 所有 MCDR API 附联网来源 URL。

---

## 执行交接（ExitPlanMode 批准后）

计划保存到 `Docs/Plans/superpowers/2026-07-01-phase0-1-auth-login.md` 后，两种执行方式：

1. **Subagent-Driven（推荐）**：用 `superpowers:subagent-driven-development`，每个 Task 派一个新 subagent，任务间 review，快速迭代。契合 MVP §7 的 3 teammate 并行（backend-dev 串行跑完 B1-B16 冻结契约后，spawn mcdr-dev + frontend-dev 并行）。
2. **Inline Execution**：用 `superpowers:executing-plans`，本会话批量执行 + 检查点 review。
