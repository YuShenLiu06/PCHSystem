import type { ProgressTimelinePoint } from '../api/construction'

/**
 * 时间戳比较：真实 ISO 字符串走 ``Date.parse`` 数值比较（不依赖两端字符串格式严格
 * 一致——例如 ``+08:00`` 与 ``Z``、不同小数精度也能正确排序）；任一侧非合法日期
 * （如测试占位 ``t0``/``t2``）则回退字典序，保持占位场景行为不变。
 */
function compareTs(a: string, b: string): number {
  const ta = Date.parse(a)
  const tb = Date.parse(b)
  if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb
  return a < b ? -1 : a > b ? 1 : 0
}

/**
 * 时序折线前向填充：沿「全部 account 的统一时间轴」补齐每个 account 的点。
 *
 * inactive 时段继承该 account 上一值（水平保持），线全程不断，不再因某段时间无上报而断开；
 * 除施工开始 0 锚点外，首点之前不补——晚加入玩家的线从其首个上报点起。
 * 当 startTime 存在且严格早于某 account 首点时，在该 account 的线最前补一个 [startTime, 0]
 * 锚点，使折线从 y=0 升起（视觉上锚点→首点是斜线，符合「累计从 0 起」语义）。
 * 返回 account_id → [ts, value][] 映射，供 ECharts `type: 'line'` + `xAxis.type: 'time'` 直接消费。
 */
export function forwardFillTimeline(
  points: readonly ProgressTimelinePoint[],
  startTime?: string | null,
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
    arr.sort((a, b) => compareTs(a[0], b[0]))
  }

  // 2. 统一时间轴：全部 account 的 recorded_at 去重升序
  const uniTimes = Array.from(
    new Set(points.map((p) => p.recorded_at)),
  ).sort((a, b) => compareTs(a, b))

  // 3. 每个 account 从自己的首点起，沿统一时间轴前向填充
  for (const [accountId, pts] of raw) {
    if (pts.length === 0) continue
    const firstTs = pts[0][0]
    const lookup = new Map<string, number>()
    for (const [ts, val] of pts) lookup.set(ts, val)
    const out: Array<[string, number]> = []
    let carried: number | null = null
    for (const ts of uniTimes) {
      if (compareTs(ts, firstTs) < 0) continue // 首点之前不补（施工开始 0 锚点在循环外单独处理）
      if (lookup.has(ts)) carried = lookup.get(ts)!
      if (carried !== null) out.push([ts, carried])
    }
    // 施工开始 0 锚点：startTime 严格早于该组首点时，在最前补 (startTime, 0)，
    // 让该 account 的线从 y=0 升起；startTime 缺省 / 晚于或等于首点则不补。
    // compareTs 对真实 ISO 时间戳走数值比较（不依赖字符串格式严格一致），非日期
    // 占位串回退字典序（测试用）；out 是本地新数组，unshift 不影响入参。
    if (startTime !== null && startTime !== undefined && compareTs(startTime, firstTs) < 0) {
      out.unshift([startTime, 0])
    }
    if (out.length > 0) filled.set(accountId, out)
  }
  return filled
}
