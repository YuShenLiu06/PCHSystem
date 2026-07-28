import type { ProgressMaterialItem } from '../api/construction'

/** 材料完成度条目（附带「我的贡献」净量，用于排序与 tooltip）。 */
export interface MaterialWithMine extends ProgressMaterialItem {
  my_net_qty: number
}

/**
 * 判断材料是否「已完成」：``need>0 && net>=need``；``need==0``（completion_pct=null）
 * 视作已完成（无需求，垫底）。
 */
export function isMaterialFinished(it: { need_qty: number; net_qty: number }): boolean {
  return it.need_qty > 0 ? it.net_qty >= it.need_qty : true
}

/**
 * 材料完成度排序（参考 sheet「mine first」语义，客户端实现因数据集小）：
 *
 * 1. **我贡献且未完成**（``my_net_qty>0`` 且未完成）最优先
 * 2. **未完成**（无论是否我贡献）
 * 3. **已完成**垫底（即使我贡献过）
 *
 * 各组内按**需求总数 ``need_qty`` 降序**（不是剩余量）。返回新数组（不改原入参）。
 *
 * ``myNetByRegistry`` = ``registry_id → 我的净贡献``（前端从 ``progress.breakdown`` 按
 * 当前账号 id 聚合得出，缺失记 0）。
 */
export function sortMaterialCompletion(
  items: readonly ProgressMaterialItem[],
  myNetByRegistry: Record<string, number>,
): MaterialWithMine[] {
  const enriched: MaterialWithMine[] = items.map((it) => ({
    ...it,
    my_net_qty: myNetByRegistry[it.registry_id] ?? 0,
  }))
  return enriched.sort((a, b) => {
    const ta = materialTier(a)
    const tb = materialTier(b)
    if (ta !== tb) return ta - tb // 低 tier 值在前（1 > 2 > 3 优先级）
    return b.need_qty - a.need_qty // 组内 need 降序
  })
}

/** tier 值越小越靠前：1=我贡献且未完成 / 2=未完成非我贡献 / 3=已完成。 */
function materialTier(it: MaterialWithMine): number {
  if (isMaterialFinished(it)) return 3
  if (it.my_net_qty > 0) return 1
  return 2
}
