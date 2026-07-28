"""construction.participants（玩家加入施工项目）

Revision ID: 0021_construction_participants
Revises: 0020_sheet_constructing_at
Create Date: 2026-07-28

玩家显式「加入施工」机制（plan BLOCK 1）：每个 Web 账号同时最多活跃加入 1 个
施工项目（``enforce_single_construction=True`` 默认；DB 通过 partial unique index
兜底）。备货（collecting）/施工（constructing）阶段认领/上交时**自动加入**当前
项目；玩家可经 Web/MCDR 显式 join/switch/leave。

- 锚 ``web_account_id``（R-5 account 主锚，同账号任一 UUID 都视为同一加入者）。
- 主表 append-only：仅 UPDATE ``left_at``，从不 DELETE（保留历史行供「我的施工历程」，
  仿 ``player_sources``）。
- ``uq_participants_active`` UNIQUE ``(web_account_id) WHERE left_at IS NULL``
  = one-at-a-time DB 铁律（与 ``uq_player_sources_active`` 范式一致，0017:112-119）。

第三方/mod 源（``POST /v1/construction/report``）**跳过 join 直接上报**——API project
维度零校验不变，多项目同时上报仍允许。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0021_construction_participants'
down_revision = '0020_sheet_constructing_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "web_account_id",
            sa.BigInteger(),
            sa.ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sheet_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("join_source", sa.Text(), nullable=False),
        sa.Column("left_reason", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_participants"),
        sa.CheckConstraint(
            "join_source IN ('auto', 'manual')",
            name="ck_participants_join_source",
        ),
        schema="construction",
    )
    # one-at-a-time DB 兜底（复刻 uq_player_sources_active 范式）
    op.create_index(
        "uq_participants_active",
        "participants",
        ["web_account_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
        schema="construction",
    )
    op.create_index(
        "ix_participants_sheet_active",
        "participants",
        ["sheet_id"],
        postgresql_where=sa.text("left_at IS NULL"),
        schema="construction",
    )
    op.create_index(
        "ix_participants_account_active",
        "participants",
        ["web_account_id", "sheet_id"],
        postgresql_where=sa.text("left_at IS NULL"),
        schema="construction",
    )
    # 含历史行（非 partial）——「我的施工历程」查询用
    op.create_index(
        "ix_participants_account_time",
        "participants",
        ["web_account_id", "joined_at"],
        schema="construction",
    )


def downgrade() -> None:
    # 顺序仿 0017:193-216：先 drop 4 索引再 drop table
    op.drop_index(
        "ix_participants_account_time",
        schema="construction",
        table_name="participants",
    )
    op.drop_index(
        "ix_participants_account_active",
        schema="construction",
        table_name="participants",
    )
    op.drop_index(
        "ix_participants_sheet_active",
        schema="construction",
        table_name="participants",
    )
    op.drop_index(
        "uq_participants_active",
        schema="construction",
        table_name="participants",
    )
    op.drop_table("participants", schema="construction")
