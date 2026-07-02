"""contributed_qty column for sheet_row_contributors (per-player accumulated)

Revision ID: 0008_contributed_qty
Revises: 0007_sheet_row_contrib
Create Date: 2026-07-02

progress 模式贡献者记录每人累计上交量（contributed_qty），用于：
1. MCDR/Web 按贡献量排序显示「提交最多的」贡献者（spec：至多两位 + 省略号）；
2. 与行级 delivered_qty 解耦——后者是 owner 可修正的当前进度，
   contributed_qty 是每位玩家历史累计上交（append-only 增量，owner 调整进度不改它）。

downgrade：仅 DROP COLUMN（contributed_qty 为派生展示数据，丢失可接受）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_contributed_qty"
down_revision = "0007_sheet_row_contrib"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sheet_row_contributors",
        sa.Column(
            "contributed_qty",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="sheets",
    )


def downgrade() -> None:
    op.drop_column("sheet_row_contributors", "contributed_qty", schema="sheets")
