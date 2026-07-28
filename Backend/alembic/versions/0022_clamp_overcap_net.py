"""clamp 超量 net_qty（按材料封顶历史数据）

Revision ID: 0022_clamp_overcap_net
Revises: 0021_construction_participants
Create Date: 2026-07-28

迭代 4「按材料封顶」上线后，封顶逻辑只管「今后」上报；历史数据中
``sum(net_qty) > sum(need_qty)`` 的 (sheet_id, registry_id) 仍会让前端
``completion_pct`` 显示 >100%（虽然 progress 端点做了 ``min(net/need, 1)``
视觉封顶，但 raw ``net_qty`` 仍超量，下游报表/结算会失真）。

本迁移一次性 clamp 历史超量数据：对每个 ``(sheet_id, registry_id)`` 当前
``sum(net_qty) > sum(need_qty)`` 的材料，按账号**等比例**下调每条
``placement_records.net_qty``（同时同量下调 ``placed_qty`` 保持
``net_qty == placed_qty - broken_qty`` 不变量），使合计 = ``need``。

算法：
- ``need`` 取自 ``sheets.sheet_rows``，按 ``registry_id`` 聚合
  ``sum(need_qty)``（与 ``construction_repo.get_material_completion`` 同口径，
  含子物品——子行也有 ``registry_id``）。
- 超额 ``excess = sum(net_qty) - need``；按各账号行 ``net_qty`` 占总 ``net``
  的比例分摊下调（``floor`` 基础上按 ``net_qty DESC`` 顺序前 ``deficit`` 行
  补 1，保证 ``sum(clamp_delta) == excess`` 精确）。
- 仅对 ``net_qty > 0`` 的行参与分摊（负 net 行不参与，避免反向加成）。
- 同时把 ``placed_qty`` 同量下调（``broken_qty`` 不变）→ 不变量
  ``net_qty == placed_qty - broken_qty`` 自动保持。

downgrade 为 no-op：clamp 不可逆（原始超量数字已丢失），降级到上一版本
不会恢复；如需还原须从备份恢复。
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0022_clamp_overcap_net'
down_revision = '0021_construction_participants'
branch_labels = None
depends_on = None


_CLAMP_SQL = """
WITH
  need_per_rid AS (
    SELECT sheet_id, registry_id, sum(need_qty)::bigint AS need
    FROM sheets.sheet_rows
    WHERE registry_id IS NOT NULL
    GROUP BY sheet_id, registry_id
  ),
  net_per_rid AS (
    SELECT sheet_id, registry_id, sum(net_qty)::bigint AS net
    FROM construction.placement_records
    GROUP BY sheet_id, registry_id
  ),
  overcap AS (
    SELECT n.sheet_id, n.registry_id, n.net, need.need, n.net - need.need AS excess
    FROM net_per_rid n
    JOIN need_per_rid need USING (sheet_id, registry_id)
    WHERE n.net > need.need AND need.need >= 0
  ),
  rows_with_floor AS (
    SELECT
      pr.id AS pr_id,
      pr.sheet_id,
      pr.registry_id,
      pr.net_qty,
      o.excess,
      FLOOR((o.excess::numeric * pr.net_qty::numeric) / o.net::numeric)::bigint AS floor_delta
    FROM construction.placement_records pr
    JOIN overcap o ON o.sheet_id = pr.sheet_id AND o.registry_id = pr.registry_id
    WHERE pr.net_qty > 0
  ),
  floor_sum AS (
    SELECT
      sheet_id, registry_id, excess,
      COALESCE(SUM(floor_delta), 0) AS sum_floor
    FROM rows_with_floor
    GROUP BY sheet_id, registry_id, excess
  ),
  -- 每行加 row_number：按 net_qty DESC 顺序，前 (excess - sum_floor) 行额外 +1
  -- 使 sum(clamp_delta) 精确等于 excess（floor 取整误差每行最多 1，故 deficit <= 行数）
  rows_numbered AS (
    SELECT
      r.pr_id,
      r.floor_delta,
      GREATEST(fs.excess - fs.sum_floor, 0) AS deficit,
      ROW_NUMBER() OVER (
        PARTITION BY r.sheet_id, r.registry_id
        ORDER BY r.net_qty DESC, r.pr_id
      ) AS rn
    FROM rows_with_floor r
    JOIN floor_sum fs USING (sheet_id, registry_id)
  ),
  final_clamp AS (
    SELECT
      pr_id,
      floor_delta + CASE WHEN rn <= deficit THEN 1 ELSE 0 END AS clamp_delta
    FROM rows_numbered
  )
UPDATE construction.placement_records pr
SET
  net_qty = pr.net_qty - fc.clamp_delta,
  placed_qty = pr.placed_qty - fc.clamp_delta,
  updated_at = now()
FROM final_clamp fc
WHERE pr.id = fc.pr_id
  AND fc.clamp_delta > 0
"""


def upgrade() -> None:
    op.execute(_CLAMP_SQL)


def downgrade() -> None:
    # no-op：clamp 不可逆（原始超量数字已丢失）。
    # 如需还原历史 placement 数据，须从备份恢复。
    pass
