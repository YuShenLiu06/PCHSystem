"""sheet_managers table for per-sheet manager role (account-anchored, R-5)

Revision ID: 0016_sheet_managers
Revises: 0015_web_account_display_name
Create Date: 2026-07-19

项目级协管员（manager）角色：owner 可授予某玩家为其项目的 manager，
manager 拥有除「删项目/改名/授予撤销协管员/归档」以外的全部写权限（tier B），
协助 owner 日常协作。关系绑定 sheet_id（per-sheet），不复用全局 players.role。

R-5 身份主锚 = Web 账号：manager 权限锚 ``web_account_id``（非 player_uuid），
同账号下任一 UUID 都继承 manager；授予目标必须已绑 Web 账号（列 NOT NULL，
应用层未绑 → 422）。

- 表 sheets.sheet_managers：PRIMARY KEY (sheet_id, web_account_id) 天然幂等防重复授予
- web_account_id FK→users.web_accounts.id（跨 schema，ON DELETE CASCADE）
- granted_by_uuid 是审计字段（FK→users.players.uuid，ON DELETE SET NULL），不参与权限判定
- 反向查询索引 ix_sheet_managers_account（list_sheets 参与排序 UNION 第 4 源用）
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0016_sheet_managers'
down_revision = '0015_web_account_display_name'
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
            "web_account_id",
            sa.BigInteger(),
            sa.ForeignKey("users.web_accounts.id", ondelete="CASCADE"),
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
        sa.PrimaryKeyConstraint("sheet_id", "web_account_id", name="pk_sheet_managers"),
        schema="sheets",
    )
    # 反向查询：按 web_account_id 查其协管的全部项目（list_sheets UNION 第 4 源）
    op.create_index(
        "ix_sheet_managers_account",
        "sheet_managers",
        ["web_account_id"],
        schema="sheets",
    )


def downgrade() -> None:
    op.drop_index("ix_sheet_managers_account", schema="sheets", table_name="sheet_managers")
    op.drop_table("sheet_managers", schema="sheets")
