"""last_sheet_id column for players (quick reopen last viewed sheet)

Revision ID: 0011_players_last_sheet_id
Revises: 0010_sheet_rows_registry_id
Create Date: 2026-07-06

为 users.players 增加 last_sheet_id 字段，支持「快速打开上次查看的表格」：
- 后端 GET /sheets/{id} 返回详情时自动记录（尽力写，失败不影响返回）
- GET /me/last_sheet 查询该字段，返回 sheet_id 或 null
- MCDR 通过此端点实现 !!sheet / !!PCH sheet last 快捷命令

``nullable=True`` 兼容新玩家无历史记录；**不加 FK**（对齐 registry_id 先例，
表被删时 view_sheet 返回 404 → 下次查看任意表自然覆盖）；**不加索引**
（只按 PK uuid 单行查，last_sheet_id 无查询用途）。

downgrade：仅 DROP COLUMN（丢失可接受）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_players_last_sheet_id"
down_revision = "0010_sheet_rows_registry_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("last_sheet_id", sa.Integer(), nullable=True),
        schema="users",
    )


def downgrade() -> None:
    op.drop_column("players", "last_sheet_id", schema="users")
