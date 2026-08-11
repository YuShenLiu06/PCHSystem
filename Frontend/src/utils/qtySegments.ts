// 库存记数分段：把总个数拆成「盒 / 组 / 个」三段，供 QtyValue 等宽渲染。
// 与 utils/qty.ts::formatQty 的区别：formatQty 给单一量级近似值（"12.02盒"，用于概览），
// 本文件给精确分段（12盒 3组 41，用于台账逐行核对）。两者并存，不互相替代。
const STACK = 64
const SHULKER = 27 * STACK // 1728

/** 刻度密度上限：超过 32 组则刻度线过密（<3% 间距），退化为平滑条。 */
const MAX_TICK_STACKS = 32

export interface QtySegment {
  readonly value: number
  /** `盒` / `组` / `''`（个，末段不加单位字，视觉更接近物品栏计数）。 */
  readonly unit: '盒' | '组' | ''
}

/**
 * 拆分数量为盒/组/个三段，**省略为零的高位与中位**。
 * 负数与非整数按防御处理（clamp 到 0、向下取整）。总为 0 时返回单段 `0`。
 */
export function toQtySegments(total: number): QtySegment[] {
  const n = Math.max(0, Math.floor(total))
  const shulkers = Math.floor(n / SHULKER)
  const stacks = Math.floor((n % SHULKER) / STACK)
  const items = n % STACK

  const segments: QtySegment[] = []
  if (shulkers > 0) segments.push({ value: shulkers, unit: '盒' })
  if (stacks > 0) segments.push({ value: stacks, unit: '组' })
  if (items > 0 || segments.length === 0) segments.push({ value: items, unit: '' })
  return segments
}

/**
 * 进度条组刻度间距（百分比）。返回 `null` = 不画刻度（不足一组，或组数过多）。
 * 玩家按「组」搬箱子，故刻度锚在组边界而非等分。
 */
export function stackTickPercent(totalNeed: number): number | null {
  if (totalNeed <= 0) return null
  const stacks = Math.floor(totalNeed / STACK)
  if (stacks < 1 || stacks > MAX_TICK_STACKS) return null
  return (STACK / totalNeed) * 100
}
