import { describe, expect, it } from 'vitest'
import { sortMaterialCompletion, isMaterialFinished } from '../materialSort'
import type { ProgressMaterialItem } from '../../api/construction'

function item(
  registry_id: string,
  need_qty: number,
  net_qty: number,
  completion_pct: number | null = null,
): ProgressMaterialItem {
  return {
    registry_id,
    item_name: registry_id,
    need_qty,
    net_qty,
    completion_pct,
  }
}

describe('isMaterialFinished', () => {
  it('need>0 且 net>=need 视作已完成', () => {
    expect(isMaterialFinished({ need_qty: 100, net_qty: 100 })).toBe(true)
    expect(isMaterialFinished({ need_qty: 100, net_qty: 150 })).toBe(true)
  })
  it('need>0 且 net<need 未完成', () => {
    expect(isMaterialFinished({ need_qty: 100, net_qty: 99 })).toBe(false)
  })
  it('need==0 视作已完成（无需求）', () => {
    expect(isMaterialFinished({ need_qty: 0, net_qty: 0 })).toBe(true)
  })
})

describe('sortMaterialCompletion', () => {
  it('排序：我贡献且未完成 > 未完成 > 已完成；组内按 need 降序', () => {
    const items = [
      item('a', 5000, 3200), // 我贡献 800，未完成 → tier1
      item('b', 1200, 900), // 未完成，非我贡献 → tier2
      item('c', 600, 600), // 已完成（我贡献 60，仍垫底）→ tier3
      item('d', 800, 100), // 未完成，非我贡献 → tier2
      item('e', 0, 0), // need=0 → tier3
      item('f', 2000, 500), // 我贡献 100，未完成 → tier1
    ]
    const myNet = { a: 800, c: 60, f: 100 }
    const sorted = sortMaterialCompletion(items, myNet).map((m) => m.registry_id)
    // tier1: a(5000) > f(2000) → a, f
    // tier2: b(1200) > d(800) → b, d
    // tier3: c(600) > e(0) → c, e
    expect(sorted).toEqual(['a', 'f', 'b', 'd', 'c', 'e'])
  })

  it('已完成项即使我贡献过也放最后', () => {
    const items = [
      item('done-mine', 100, 100), // 已完成，我贡献 90
      item('todo-notmine', 50, 10), // 未完成，非我贡献
    ]
    const sorted = sortMaterialCompletion(items, { 'done-mine': 90 }).map(
      (m) => m.registry_id,
    )
    expect(sorted).toEqual(['todo-notmine', 'done-mine'])
  })

  it('未提供 myNetByRegistry 时 my_net_qty 全为 0（无 tier1）', () => {
    const items = [
      item('a', 100, 50), // 未完成
      item('b', 200, 10), // 未完成
      item('c', 100, 100), // 已完成
    ]
    const sorted = sortMaterialCompletion(items, {})
    // 无 tier1；tier2 按 need 降序：b(200) > a(100)；tier3: c
    expect(sorted.map((m) => m.registry_id)).toEqual(['b', 'a', 'c'])
    expect(sorted.every((m) => m.my_net_qty === 0)).toBe(true)
  })

  it('不改原入参数组（不可变）', () => {
    const items = [item('a', 100, 50), item('b', 200, 10)]
    const snapshot = items.map((m) => m.registry_id)
    sortMaterialCompletion(items, {})
    expect(items.map((m) => m.registry_id)).toEqual(snapshot)
  })
})
