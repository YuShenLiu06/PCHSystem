"""Alembic 环境脚本。

- 同步模式（psycopg2），从 Settings.postgres_dsn_sync 注入连接串
- 导入 Base.metadata 与所有模型模块以支持 autogenerate
- compare_type=True 便于检测类型变更
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.core.config import get_settings
from app.core.db import Base
from app.models import user  # noqa: F401  注册 Player 模型

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
        compare_type=True,
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
