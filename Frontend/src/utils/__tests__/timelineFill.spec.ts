import { describe, expect, it } from 'vitest'
import { forwardFillTimeline } from '../timelineFill'
import type { ProgressTimelinePoint } from '../../api/construction'

function pt(account_id: number, recorded_at: string, total_net: number): ProgressTimelinePoint {
  return { account_id, total_net, recorded_at }
}

describe('forwardFillTimeline', () => {
  it('空数组返回空 Map', () => {
    expect(forwardFillTimeline([]).size).toBe(0)
  })

  it('inactive 时段水平保持上一值（线不断）—— A 在 t1/t3、B 在 t2', () => {
    // A 在 t1=10、t3=30；B 在 t2=5。统一时间轴 [t1, t2, t3]。
    const filled = forwardFillTimeline([
      pt(1, 't1', 10),
      pt(2, 't2', 5),
      pt(1, 't3', 30),
    ])
    // A 应有 3 个点：t1=10 / t2=10（保持）/ t3=30
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 10],
      ['t3', 30],
    ])
    // B 从自己首点 t2 起，并前向填充到统一时间轴末尾 t3（保持 5，线不断）
    expect(filled.get(2)).toEqual([
      ['t2', 5],
      ['t3', 5],
    ])
  })

  it('晚加入玩家首点之前不补点', () => {
    // A 早（t1）、B 晚（t3）。统一时间轴 [t1, t3]。B 在 t1 之前，不应有 t1 点。
    const filled = forwardFillTimeline([
      pt(1, 't1', 10),
      pt(2, 't3', 7),
    ])
    expect(filled.get(2)).toEqual([['t3', 7]])
  })

  it('每个 account 的线延伸到统一时间轴末尾（保持末值）', () => {
    // A 在 t1=10；B 在 t2=5、t3=8。末尾 t3，A 无 t3 真实点 → 保持 t1 值到 t3。
    const filled = forwardFillTimeline([
      pt(1, 't1', 10),
      pt(2, 't2', 5),
      pt(2, 't3', 8),
    ])
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 10],
      ['t3', 10],
    ])
  })

  it('同一 account 多点按时间升序排列（输入乱序）', () => {
    const filled = forwardFillTimeline([
      pt(1, 't3', 30),
      pt(1, 't1', 10),
      pt(1, 't2', 20),
    ])
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 20],
      ['t3', 30],
    ])
  })

  // —— 施工开始 0 锚点（修复2）——
  it('startTime 严格早于首点时，在最前补 [startTime, 0] 锚点', () => {
    // A 真实首点 t2=10；startTime=t0 早于 t2 → 期望 [t0,0] + 真实填充
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(1, 't3', 20)],
      't0',
    )
    expect(filled.get(1)).toEqual([
      ['t0', 0],
      ['t2', 10],
      ['t3', 20],
    ])
  })

  it('startTime 为 null 不补 0 锚点（降级到旧行为）', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(1, 't3', 20)],
      null,
    )
    expect(filled.get(1)).toEqual([
      ['t2', 10],
      ['t3', 20],
    ])
  })

  it('startTime 为 undefined 不补 0 锚点', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(1, 't3', 20)],
      undefined,
    )
    expect(filled.get(1)).toEqual([
      ['t2', 10],
      ['t3', 20],
    ])
  })

  it('startTime 等于首点时不补（避免重复点）', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(1, 't3', 20)],
      't2',
    )
    expect(filled.get(1)).toEqual([
      ['t2', 10],
      ['t3', 20],
    ])
  })

  it('startTime 晚于首点时不补', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(1, 't3', 20)],
      't2.5',
    )
    expect(filled.get(1)).toEqual([
      ['t2', 10],
      ['t3', 20],
    ])
  })

  it('多 account 各自独立判断补 0 锚点', () => {
    // A 早（t2=10）；B 晚（t4=5）。startTime=t0 同时早于两者 → 都应补 [t0, 0]。
    const filled = forwardFillTimeline(
      [pt(1, 't2', 10), pt(2, 't4', 5)],
      't0',
    )
    expect(filled.get(1)).toEqual([
      ['t0', 0],
      ['t2', 10],
      ['t4', 10],
    ])
    expect(filled.get(2)).toEqual([
      ['t0', 0],
      ['t4', 5],
    ])
  })

  it('startTime 介于两 account 首点之间时，仅早于 startTime 的 account 不补', () => {
    // A 首点 t1=10（早于 startTime t2）；B 首点 t3=5（晚于 startTime t2）。
    // 仅 B 补 [t2, 0]；A 不补。
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10), pt(2, 't3', 5)],
      't2',
    )
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t3', 10],
    ])
    expect(filled.get(2)).toEqual([
      ['t2', 0],
      ['t3', 5],
    ])
  })

  it('startTime 早于所有点但仅有一个 account 一个点（最小稀疏场景）', () => {
    const filled = forwardFillTimeline([pt(1, 't2', 7)], 't0')
    expect(filled.get(1)).toEqual([
      ['t0', 0],
      ['t2', 7],
    ])
  })

  // —— 右沿末值锚点（endTime）——
  it('endTime 严格晚于末点时，在末尾补 [endTime, lastValue]', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10), pt(1, 't2', 20)],
      undefined,
      't3',
    )
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 20],
      ['t3', 20],
    ])
  })

  it('endTime 等于末点时不补（避免重复点）', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10), pt(1, 't2', 20)],
      undefined,
      't2',
    )
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 20],
    ])
  })

  it('endTime 早于末点时不补', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10), pt(1, 't3', 20)],
      undefined,
      't2',
    )
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t3', 20],
    ])
  })

  it('endTime 为 null 时不补', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10)],
      undefined,
      null,
    )
    expect(filled.get(1)).toEqual([['t1', 10]])
  })

  it('endTime 为 undefined 时不补', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10)],
      undefined,
      undefined,
    )
    expect(filled.get(1)).toEqual([['t1', 10]])
  })

  it('多 account 各自独立补末值锚点（末值不同）', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't1', 10), pt(2, 't2', 5)],
      undefined,
      't3',
    )
    expect(filled.get(1)).toEqual([
      ['t1', 10],
      ['t2', 10],
      ['t3', 10],
    ])
    expect(filled.get(2)).toEqual([
      ['t2', 5],
      ['t3', 5],
    ])
  })

  it('与 0 锚点同时生效（startTime + endTime 两端锚点）', () => {
    const filled = forwardFillTimeline(
      [pt(1, 't2', 15), pt(1, 't3', 30)],
      't0',
      't4',
    )
    expect(filled.get(1)).toEqual([
      ['t0', 0],
      ['t2', 15],
      ['t3', 30],
      ['t4', 30],
    ])
  })

  it('真实 ISO 时间戳：endTime 为当前时间，末点为旧日期时补右沿', () => {
    const filled = forwardFillTimeline(
      [pt(1, '2026-07-01T00:00:00Z', 100)],
      undefined,
      '2026-08-06T12:00:00Z',
    )
    expect(filled.get(1)).toEqual([
      ['2026-07-01T00:00:00Z', 100],
      ['2026-08-06T12:00:00Z', 100],
    ])
  })
})
