"""construction.placement_snapshots（时序快照，迭代 2）

Revision ID: 0018_placement_snapshots
Revises: 0017_construction
Create Date: 2026-07-27

进度图表的时序数据源（迭代 2 需求 4）：每次 ``POST /v1/construction/report``
成功落 placement 后，为**涉及的 account** 写一条快照（``total_net`` = 该 account
此刻在该 sheet 的 ``sum(net_qty)``）。前端折线图「时序累计趋势」按 account 拆线
消费（``construction_repo.get_placement_timeline``，limit 200 防爆）。

- 写入 **best-effort**：失败仅记日志、不阻断 report 主流程（展示用，非权威源；
  权威仍是 ``placement_records`` 聚合）。
- 仅对**本轮有 accepted entry 的 account** 写——skip 的不写，避免无意义行。

R-1：落 PostgreSQL（后端独占）；R-5：``account_id`` 锚 Web 账号（非 player_uuid）。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0018_placement_snapshots'
down_revision = '0017_construction'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "placement_snapshots",
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
        sa.Column("total_net", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_placement_snapshots"),
        schema="construction",
    )
    # 进度端点时序查询：按 sheet 取全部 account 的时序点（ORDER BY recorded_at）
    op.create_index(
        "ix_placement_snapshots_sheet_time",
        "placement_snapshots",
        ["sheet_id", "recorded_at"],
        schema="construction",
    )
    # 单 account 时序查询（预留：按 account 拆线的服务端预聚合）
    op.create_index(
        "ix_placement_snapshots_account_time",
        "placement_snapshots",
        ["sheet_id", "account_id", "recorded_at"],
        schema="construction",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_placement_snapshots_account_time",
        schema="construction",
        table_name="placement_snapshots",
    )
    op.drop_index(
        "ix_placement_snapshots_sheet_time",
        schema="construction",
        table_name="placement_snapshots",
    )
    op.drop_table("placement_snapshots", schema="construction")
