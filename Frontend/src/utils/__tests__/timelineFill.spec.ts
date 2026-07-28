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
})
