"""registry_id column for sheet_rows (MC registry id, namespace:path)

Revision ID: 0009_sheet_rows_registry_id
Revises: 0008_contributed_qty
Create Date: 2026-07-03

为 sheet_rows 增加隐式 registry_id 字段（MC 物品/方块注册名，如 ``minecraft:stone``）：
- 游戏内「一键提交」/「手持新建行」按 registry_id 精确匹配行；
- 投影解析 ``POST /sheets/from-items`` 现透传此字段（解析侧已产出 ``PreviewItem.item_id``）。

``nullable=True`` 兼容旧行与纯文本行；**不加 UNIQUE / 索引**
（``UNIQUE(sheet_id, item_name)`` 仍是唯一 upsert 锁点；一键提交走 ``view sheet``
拉全表后内存匹配，无需 DB 索引——KISS）。

落库时若 ``item_name`` 缺失但 ``registry_id`` 提供，API 层用
``LangJsonTranslator``（复用 ``translators/lang/*.zh_cn.json``）翻译补默认中文名，
未命中回退 registry_id 本身。后续新增 mod 翻译表只需往 ``lang/`` 目录加 JSON 即可，
翻译器单例零改动自动合并。

downgrade：仅 DROP COLUMN（registry_id 为匹配用辅助字段，丢失可接受）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_sheet_rows_registry_id"
down_revision = "0008_contributed_qty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sheet_rows",
        sa.Column("registry_id", sa.Text(), nullable=True),
        schema="sheets",
    )


def downgrade() -> None:
    op.drop_column("sheet_rows", "registry_id", schema="sheets")
