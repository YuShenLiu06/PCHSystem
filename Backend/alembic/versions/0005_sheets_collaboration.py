"""sheets collaboration: mode/status/claimant/delivered columns

Revision ID: 0005_sheets_collab
Revises: 0004_sheets
Create Date: 2026-07-02

协作改进（spec 2026-07-02-sheets-collaboration-design §5.1）：
- sheet_rows 加 mode（lock/progress）、status（open/claimed/done）、claimant_uuid、delivered_qty
- 旧 done_flag=1 → status='done'，done_flag=0 → status='open'（默认值）
- 删 done_flag，加 (sheet_id, status) 索引
- downgrade 可逆：回填 done_flag，删 4 列与索引（claimant 的 FK 由 PG 自动级联）
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_sheets_collab"
down_revision = "0004_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sheet_rows",
        sa.Column(
            "mode",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="sheets",
    )
    op.add_column(
        "sheet_rows",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        schema="sheets",
    )
    op.add_column(
        "sheet_rows",
        sa.Column(
            "claimant_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid"),
            nullable=True,
        ),
        schema="sheets",
    )
    op.add_column(
        "sheet_rows",
        sa.Column(
            "delivered_qty",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="sheets",
    )
    # 数据迁移：旧 done_flag=1 → status='done'（done_flag=0 保持默认 'open'）
    op.execute(
        "UPDATE sheets.sheet_rows SET status = 'done' WHERE done_flag = 1"
    )
    op.drop_column("sheet_rows", "done_flag", schema="sheets")
    op.create_index(
        "ix_sheet_rows_sheet_status",
        "sheet_rows",
        ["sheet_id", "status"],
        schema="sheets",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sheet_rows_sheet_status", table_name="sheet_rows", schema="sheets"
    )
    # 加回 done_flag：status='done' → 1，否则 0
    op.add_column(
        "sheet_rows",
        sa.Column(
            "done_flag",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="sheets",
    )
    op.execute(
        "UPDATE sheets.sheet_rows SET done_flag = 1 WHERE status = 'done'"
    )
    op.drop_column("sheet_rows", "claimant_uuid", schema="sheets")
    op.drop_column("sheet_rows", "delivered_qty", schema="sheets")
    op.drop_column("sheet_rows", "status", schema="sheets")
    op.drop_column("sheet_rows", "mode", schema="sheets")
