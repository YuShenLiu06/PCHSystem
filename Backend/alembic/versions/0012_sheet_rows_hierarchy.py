"""子物品嵌套行（Option 2）

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0012_sheet_rows_hierarchy'
down_revision = '0011_players_last_sheet_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 子物品嵌套行：parent_row_id（FK 自引用 CASCADE）+ qty_per_unit
    op.execute("""
        ALTER TABLE sheets.sheet_rows
            ADD COLUMN parent_row_id BIGINT NULL,
            ADD COLUMN qty_per_unit INTEGER NULL;

        ALTER TABLE sheets.sheet_rows
            ADD CONSTRAINT fk_sheet_rows_parent_row
            FOREIGN KEY (parent_row_id)
            REFERENCES sheets.sheet_rows(id)
            ON DELETE CASCADE;

        ALTER TABLE sheets.sheet_rows
            DROP CONSTRAINT uq_sheet_rows_sheet_item;

        CREATE UNIQUE INDEX uq_sheet_rows_top_name
            ON sheets.sheet_rows (sheet_id, item_name)
            WHERE parent_row_id IS NULL;

        CREATE UNIQUE INDEX uq_sheet_rows_sub_registry
            ON sheets.sheet_rows (parent_row_id, registry_id)
            WHERE parent_row_id IS NOT NULL;

        ALTER TABLE sheets.sheet_rows
            ADD CONSTRAINT ck_sheet_rows_sub_invariants
            CHECK (parent_row_id IS NULL OR (registry_id IS NOT NULL AND qty_per_unit IS NOT NULL AND qty_per_unit >= 1));

        CREATE INDEX ix_sheet_rows_parent
            ON sheets.sheet_rows (parent_row_id)
            WHERE parent_row_id IS NOT NULL;
    """)


def downgrade() -> None:
    # 子行是父行的派生物（item_name 自动加「父名-本名」前缀，need_qty 派生计算）；
    # 降级回「无子物品」时代必须先删子行，否则 DROP COLUMN 后子行变孤儿顶层行，
    # 其拼接名可能与既有顶层行撞 UNIQUE(sheet_id, item_name) 而 abort。
    # 顺序：① DROP 索引/CHECK/FK → ② DELETE 子行 → ③ DROP COLUMN → ④ 还原非部分 UNIQUE。
    op.execute("""
        DROP INDEX sheets.ix_sheet_rows_parent;
        ALTER TABLE sheets.sheet_rows DROP CONSTRAINT ck_sheet_rows_sub_invariants;
        DROP INDEX sheets.uq_sheet_rows_sub_registry;
        DROP INDEX sheets.uq_sheet_rows_top_name;

        DELETE FROM sheets.sheet_rows WHERE parent_row_id IS NOT NULL;

        ALTER TABLE sheets.sheet_rows
            ADD CONSTRAINT uq_sheet_rows_sheet_item
            UNIQUE (sheet_id, item_name);

        ALTER TABLE sheets.sheet_rows DROP CONSTRAINT fk_sheet_rows_parent_row;
        ALTER TABLE sheets.sheet_rows
            DROP COLUMN parent_row_id,
            DROP COLUMN qty_per_unit;
    """)
