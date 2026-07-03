"""sheets 三阶段生命周期 + 归档元数据。

升级 sheets.sheets：
- status text NOT NULL DEFAULT 'collecting' + CHECK ∈ (collecting/constructing/archived)
- archived_path text NULL（仅 archived 非空，存相对 archive_root 路径）
- archived_at timestamptz NULL（仅 archived 非空）
- 一致性 CHECK：archived ⇒ path/at 非空；非 archived ⇒ path/at 为 null
- ix_sheets_status 索引

downgrade 逆序可逆（drop index/constraints/columns）。

详见 Docs/Plans/1-sheet-crystalline-clock.md「数据模型与迁移」。
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_sheets_lifecycle"
down_revision = "0008_contributed_qty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 三列
    op.add_column(
        "sheets",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'collecting'"),
        ),
        schema="sheets",
    )
    op.add_column(
        "sheets",
        sa.Column("archived_path", sa.Text(), nullable=True),
        schema="sheets",
    )
    op.add_column(
        "sheets",
        sa.Column(
            "archived_at", sa.DateTime(timezone=True), nullable=True
        ),
        schema="sheets",
    )

    # 2) 两道 CHECK
    op.create_check_constraint(
        "ck_sheets_status_values",
        "sheets",
        "status IN ('collecting','constructing','archived')",
        schema="sheets",
    )
    op.create_check_constraint(
        "ck_sheets_status_archive_consistency",
        "sheets",
        "(status='archived' AND archived_path IS NOT NULL AND archived_at IS NOT NULL) "
        "OR (status IN ('collecting','constructing') AND archived_path IS NULL AND archived_at IS NULL)",
        schema="sheets",
    )

    # 3) 状态索引
    op.create_index(
        "ix_sheets_status", "sheets", ["status"], schema="sheets"
    )


def downgrade() -> None:
    op.drop_index("ix_sheets_status", table_name="sheets", schema="sheets")
    op.drop_constraint(
        "ck_sheets_status_archive_consistency",
        "sheets",
        schema="sheets",
        type_="check",
    )
    op.drop_constraint(
        "ck_sheets_status_values",
        "sheets",
        schema="sheets",
        type_="check",
    )
    op.drop_column("sheets", "archived_at", schema="sheets")
    op.drop_column("sheets", "archived_path", schema="sheets")
    op.drop_column("sheets", "status", schema="sheets")
