"""construction + system schemas（施工进度上报层 + 运行时设置）

Revision ID: 0017_construction
Revises: 0016_sheet_managers
Create Date: 2026-07-27

施工进度上报层落地（[`Docs/architecture/flows/construction-progress.md`] 设计契约）：
- construction.placement_records：按 ``(sheet_id, account_id, registry_id)`` 聚合的
  方块净放置（``net_qty = placed_qty - broken_qty``，允许负），归档时由
  ``construction_repo.aggregate_placement_totals`` 按 account 聚合喂给
  ``BuildAScoreCalculator``（scoring-settlement.md §4 SettlementContext）。
- construction.player_sources：每玩家当前/历史活跃上报源（单源策略）；
  部分唯一索引 ``uq_player_sources_active`` 保证每玩家同时仅一条
  ``disabled_at IS NULL`` 的活跃源。代理主键 id（旧源 disabled 后留存，故
  player_uuid 不可独作 PK）。
- construction.player_source_history：切换审计（append-only，仅 INSERT）。
- construction.server_mod_sources：服务端 mod 白名单（服主审批，PK=name 幂等）。
- system.settings：key-value JSONB 运行时开关（``construction.*`` 键，DB 无值时
  应用层回退 ``app.core.config.Settings`` 默认；不在迁移里 seed 数据）。

R-1：全表落 PostgreSQL，后端独占。R-5：placement_records 锚 ``account_id``
（非 player_uuid，离线改名/换 UUID 积分不丢）。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0017_construction'
down_revision = '0016_sheet_managers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- schemas（仿 0001/0004/0006：开头建 schema）---
    op.execute("CREATE SCHEMA IF NOT EXISTS construction")
    op.execute("CREATE SCHEMA IF NOT EXISTS system")

    # --- construction.placement_records ---
    op.create_table(
        "placement_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "sheet_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("registry_id", sa.Text(), nullable=False),
        sa.Column("placed_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("broken_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("net_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_placement_records"),
        sa.UniqueConstraint(
            "sheet_id", "account_id", "registry_id",
            name="uq_placement_records_sheet_account_registry",
        ),
        sa.CheckConstraint(
            "placed_qty >= 0 AND broken_qty >= 0",
            name="ck_placement_records_nonneg",
        ),
        schema="construction",
    )
    # 归因查询 + 进度端点：按 sheet 取该表所有行（account 聚合 / breakdown 明细）
    op.create_index(
        "ix_placement_records_sheet_account",
        "placement_records",
        ["sheet_id", "account_id"],
        schema="construction",
    )

    # --- construction.player_sources（单活跃源）---
    op.create_table(
        "player_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "player_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_player_sources"),
        sa.CheckConstraint(
            "source_type IN ('mcdr', 'server_mod', 'client_mod')",
            name="ck_player_sources_source_type",
        ),
        schema="construction",
    )
    # 单活跃源铁律：每玩家同时仅一条 disabled_at IS NULL（部分唯一索引）
    op.create_index(
        "uq_player_sources_active",
        "player_sources",
        ["player_uuid"],
        unique=True,
        postgresql_where=sa.text("disabled_at IS NULL"),
        schema="construction",
    )

    # --- construction.player_source_history（append-only 审计）---
    op.create_table(
        "player_source_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "player_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_type", sa.Text(), nullable=True),
        sa.Column("from_id", sa.Text(), nullable=True),
        sa.Column("to_type", sa.Text(), nullable=False),
        sa.Column("to_id", sa.Text(), nullable=True),
        sa.Column(
            "switched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_player_source_history"),
        sa.CheckConstraint(
            "to_type IN ('mcdr', 'server_mod', 'client_mod')",
            name="ck_player_source_history_to_type",
        ),
        schema="construction",
    )
    op.create_index(
        "ix_player_source_history_uuid_time",
        "player_source_history",
        ["player_uuid", "switched_at"],
        schema="construction",
    )

    # --- construction.server_mod_sources（服务端 mod 白名单）---
    op.create_table(
        "server_mod_sources",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "approved_by_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_server_mod_sources"),
        schema="construction",
    )

    # --- system.settings（key-value JSONB 运行时开关）---
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name="pk_system_settings"),
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("settings", schema="system")
    op.drop_table("server_mod_sources", schema="construction")
    op.drop_index(
        "ix_player_source_history_uuid_time",
        schema="construction",
        table_name="player_source_history",
    )
    op.drop_table("player_source_history", schema="construction")
    op.drop_index(
        "uq_player_sources_active",
        schema="construction",
        table_name="player_sources",
    )
    op.drop_table("player_sources", schema="construction")
    op.drop_index(
        "ix_placement_records_sheet_account",
        schema="construction",
        table_name="placement_records",
    )
    op.drop_table("placement_records", schema="construction")
    # schema 仅在为空时才会被删除（CASCADE 不用于反向，避免误伤）
    op.execute("DROP SCHEMA IF EXISTS system")
    op.execute("DROP SCHEMA IF EXISTS construction")
