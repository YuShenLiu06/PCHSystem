"""construction.server_mod_sources.enabled（逐源启停，迭代 3）

Revision ID: 0019_server_mod_sources_enabled
Revises: 0018_placement_snapshots
Create Date: 2026-07-28

管理员面板「允许第三方服务端 mod」单全局开关改为**逐源卡片 + 独立启停**：每个
server_mod 源一张卡片 + ``el-switch``，可同时开启多个（组合）。存量行 ``enabled=true``
（保持原「在白名单即生效」语义）。

真正生效（迭代 3）：``get_construction_reporter``（service-token + ``X-Source-Id`` 通道）
与 ``switch-server``（admin 分配 server_mod）校验 ``enabled=true``，否则 403/422。
``allow_server_mods`` 全局开关 schema 字段保留（默认 true、不再单显），避免破坏 settings 契约。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0019_server_mod_sources_enabled'
down_revision = '0018_placement_snapshots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "server_mod_sources",
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        schema="construction",
    )


def downgrade() -> None:
    op.drop_column("server_mod_sources", "enabled", schema="construction")
