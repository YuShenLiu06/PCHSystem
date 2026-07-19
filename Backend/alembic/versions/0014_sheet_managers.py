"""sheet_managers table for per-sheet manager role

Revision ID: 0014_sheet_managers
Revises: 0013_qty_per_unit_float
Create Date: 2026-07-19

项目级协管员（manager）角色：owner 可授予某玩家为其项目的 manager，
manager 拥有除「删项目/改名/授予撤销协管员/归档」以外的全部写权限（tier B），
协助 owner 日常协作。关系绑定 sheet_id（per-sheet），不复用全局 players.role。

- 表 sheets.sheet_managers：PRIMARY KEY (sheet_id, player_uuid) 天然幂等防重复授予
- player_uuid / granted_by_uuid 均 FK→users.players.uuid（跨 schema）
- granted_by_uuid 是审计字段（ON DELETE SET NULL），不参与权限判定
- 反向查询索引 ix_sheet_managers_player（list_sheets 参与排序 UNION 第 4 源用）
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0014_sheet_managers'
down_revision = '0013_qty_per_unit_float'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sheet_managers",
        sa.Column(
            "sheet_id",
            sa.BigInteger(),
            sa.ForeignKey("sheets.sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "player_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "granted_by_uuid",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.players.uuid", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("sheet_id", "player_uuid", name="pk_sheet_managers"),
        schema="sheets",
    )
    # 反向查询：按 player_uuid 查其协管的全部项目（list_sheets UNION 第 4 源）
    op.create_index(
        "ix_sheet_managers_player",
        "sheet_managers",
        ["player_uuid"],
        schema="sheets",
    )


def downgrade() -> None:
    op.drop_index("ix_sheet_managers_player", schema="sheets", table_name="sheet_managers")
    op.drop_table("sheet_managers", schema="sheets")
