"""sheets.sheets.constructing_at（进入施工时间戳）

Revision ID: 0020_sheet_constructing_at
Revises: 0019_server_mod_sources_enabled
Create Date: 2026-07-28

为 ``sheets.sheets`` 加 ``constructing_at timestamptz nullable``，供：

- **进度图表 xAxis 范围**：前端 ``ConstructionProgress.vue`` 透传给 ``TrendLineChart``
  作为 ``startTime``（左沿贴施工开始；右沿停在 ``archived_at`` 或当前时间）。
- **归档 timeline 点亮「进入施工」行**：``services/archive/service.py`` 读此字段喂给
  ``renderer.render_timeline``（原硬编码 ``constructing_at=None`` → 该行被过滤）。
- **participants join 决策**（迁移 0021）：``collecting→constructing`` 切换时若该字段
  非空，可用于校验是否重复推进。

回填：``status='constructing'`` 的行近似取 ``updated_at``（已 archived 不回填，
timeline 退化到「创建 → 归档」，与原 ``constructing_at=None`` 行为一致）。
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '0020_sheet_constructing_at'
down_revision = '0019_server_mod_sources_enabled'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sheets",
        sa.Column("constructing_at", sa.DateTime(timezone=True), nullable=True),
        schema="sheets",
    )
    # 回填：collecting→constructing 切换历史行（无时间戳记录 → 近似取 updated_at）
    op.execute(
        "UPDATE sheets.sheets SET constructing_at = updated_at "
        "WHERE status = 'constructing' AND constructing_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("sheets", "constructing_at", schema="sheets")
