"""qty_per_unit 改为浮点（支持 0.5 等非整数单位用量）

Revision ID: 0013_qty_per_unit_float
Revises: 0012_sheet_rows_hierarchy
Create Date: 2026-07-09
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0013_qty_per_unit_float'
down_revision = '0012_sheet_rows_hierarchy'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # qty_per_unit: INTEGER → NUMERIC(10,2)，支持小数（如 0.5）
    # 约束从 ≥ 1 改为 > 0（允许 0.5 等小数，但仍排除 0 和负数）
    op.execute("""
        ALTER TABLE sheets.sheet_rows
            ALTER COLUMN qty_per_unit TYPE NUMERIC(10,2)
            USING qty_per_unit::numeric(10,2);

        ALTER TABLE sheets.sheet_rows
            DROP CONSTRAINT ck_sheet_rows_sub_invariants;

        ALTER TABLE sheets.sheet_rows
            ADD CONSTRAINT ck_sheet_rows_sub_invariants
            CHECK (parent_row_id IS NULL OR (registry_id IS NOT NULL AND qty_per_unit IS NOT NULL AND qty_per_unit > 0));
    """)


def downgrade() -> None:
    # 有损降级（符合 data-model 承诺）：子行 0.x 单位用量先钳到 1，
    # 避免 ROUND(0.4)=0 违反 ≥1 CHECK，或 ROUND 成 0 造成单位用量失真。
    # 顺序：① DROP 旧 CHECK（>0）→ ② 钳子行 qty 到 ≥1 → ③ ALTER TYPE INTEGER →
    # ④ ADD CHECK ≥1。任何顺序错位都会让 0.x 子行 abort 整个降级。
    op.execute("""
        ALTER TABLE sheets.sheet_rows
            DROP CONSTRAINT ck_sheet_rows_sub_invariants;

        UPDATE sheets.sheet_rows
            SET qty_per_unit = GREATEST(ROUND(qty_per_unit), 1)
            WHERE parent_row_id IS NOT NULL AND ROUND(qty_per_unit) < 1;

        ALTER TABLE sheets.sheet_rows
            ALTER COLUMN qty_per_unit TYPE INTEGER
            USING ROUND(qty_per_unit)::INTEGER;

        ALTER TABLE sheets.sheet_rows
            ADD CONSTRAINT ck_sheet_rows_sub_invariants
            CHECK (parent_row_id IS NULL OR (registry_id IS NOT NULL AND qty_per_unit IS NOT NULL AND qty_per_unit >= 1));
    """)
