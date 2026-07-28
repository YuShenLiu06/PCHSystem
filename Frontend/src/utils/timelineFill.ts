import type { ProgressTimelinePoint } from '../api/construction'

/**
 * 时序折线前向填充：沿「全部 account 的统一时间轴」补齐每个 account 的点。
 *
 * inactive 时段继承该 account 上一值（水平保持），线全程不断，不再因某段时间无上报而断开；
 * 首点之前不补——晚加入玩家的线从其首个上报点起。返回 account_id → [ts, value][] 映射，
 * 供 ECharts `type: 'line'` + `xAxis.type: 'time'` 直接消费。
 */
export function forwardFillTimeline(
  points: readonly ProgressTimelinePoint[],
): Map<number, Array<[string, number]>> {
  const filled = new Map<number, Array<[string, number]>>()
  if (points.length === 0) return filled

  // 1. 各 account 自己的真实点（按时间升序）
  const raw = new Map<number, Array<[string, number]>>()
  for (const p of points) {
    const arr = raw.get(p.account_id) ?? []
    arr.push([p.recorded_at, p.total_net])
    raw.set(p.account_id, arr)
  }
  for (const arr of raw.values()) {
    arr.sort((a, b) => (a[0] < b[0] ? -1 : 1))
  }

  // 2. 统一时间轴：全部 account 的 recorded_at 去重升序
  const uniTimes = Array.from(
    new Set(points.map((p) => p.recorded_at)),
  ).sort((a, b) => (a < b ? -1 : 1))

  // 3. 每个 account 从自己的首点起，沿统一时间轴前向填充
  for (const [accountId, pts] of raw) {
    if (pts.length === 0) continue
    const firstTs = pts[0][0]
    const lookup = new Map<string, number>()
    for (const [ts, val] of pts) lookup.set(ts, val)
    const out: Array<[string, number]> = []
    let carried: number | null = null
    for (const ts of uniTimes) {
      if (ts < firstTs) continue // 首点之前不补
      if (lookup.has(ts)) carried = lookup.get(ts)!
      if (carried !== null) out.push([ts, carried])
    }
    if (out.length > 0) filled.set(accountId, out)
  }
  return filled
}
