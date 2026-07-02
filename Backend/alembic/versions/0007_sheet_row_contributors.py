"""sheet_row_contributors table for progress-mode multi-contributor

Revision ID: 0007_sheet_row_contrib
Revises: 0006_notifications
Create Date: 2026-07-02

progress 模式多人协作改进（推翻 spec D-4 的「progress 单认领人」决策）：
- 建 sheets.sheet_row_contributors 子表（UNIQUE(row_id, player_uuid)，行删除 CASCADE）
- 数据迁移：mode=1 行清 claimant_uuid；delivered>0 者的旧 claimant 移入 contributors；
  status 按 delivered 重算（progress 不变量：claimant 恒 null，status 由 delivered 推导）
- downgrade：仅 DROP TABLE（progress 行旧 claimant 不回迁——单认领语义已废弃）
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_sheet_row_contrib"
down_revision = "0006_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sheet_row_contributors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "row_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheet_rows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "player_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "row_id", "player_uuid", name="uq_sheet_row_contributors_row_player"
        ),
        schema="sheets",
    )

    # 数据迁移：mode=1 行的旧单认领语义 → 多贡献者
    # delivered>0 且有 claimant：claimant 作为首位贡献者迁入
    op.execute(
        """
        INSERT INTO sheets.sheet_row_contributors (row_id, player_uuid, joined_at)
        SELECT id, claimant_uuid, updated_at FROM sheets.sheet_rows
        WHERE mode = 1 AND claimant_uuid IS NOT NULL AND delivered_qty > 0
        """
    )
    # mode=1 行：清 claimant_uuid，按 delivered 重算 status（progress 不变量）
    op.execute(
        """
        UPDATE sheets.sheet_rows
        SET claimant_uuid = NULL,
            status = CASE
                WHEN delivered_qty = 0 THEN 'open'
                WHEN delivered_qty >= need_qty THEN 'done'
                ELSE 'claimed'
            END
        WHERE mode = 1
        """
    )


def downgrade() -> None:
    # 仅回退表结构；progress 行的 claimant_uuid/status 不回迁（单认领语义已废弃）
    op.drop_table("sheet_row_contributors", schema="sheets")
