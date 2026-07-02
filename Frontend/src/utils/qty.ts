// 与 Backend/app/core/qty.py 完全对齐的数量换算
// STACK = 64（一组）；SHULKER = 27 * 64 = 1728（一盒）
// 规则：n >= 1728 → "X盒"；n >= 64 → "X组"；否则 "X个"
// 换算系数取 round(n / X, 2) 后去尾零（等价 Python 的 :g 格式化）
const STACK = 64
const SHULKER = 27 * 64 // 1728

/**
 * 将数字格式化为「去尾零」字符串，等价 Python `f"{v:g}"`。
 * 例：2.0 → "2"；1.16 → "1.16"；1.56 → "1.56"；63 → "63"。
 */
function trimZero(v: number): string {
  return String(parseFloat(v.toFixed(2)))
}

export function formatQty(n: number): string {
  if (n >= SHULKER) return `${trimZero(n / SHULKER)}盒`
  if (n >= STACK) return `${trimZero(n / STACK)}组`
  return `${n}个`
}
