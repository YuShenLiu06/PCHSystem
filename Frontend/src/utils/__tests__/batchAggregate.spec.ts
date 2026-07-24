import { describe, expect, it } from 'vitest'
import { aggregateItems } from '../batchAggregate'
import type { BatchFilePreview, ParsedMaterialPreview, PreviewItem } from '../../api/parsing'

// 构造成功解析的 BatchFilePreview 占位（meta 仅放最小字段，聚合逻辑不读 meta）
function okFile(
  filename: string,
  blocks: PreviewItem[],
  containers: PreviewItem[] = [],
): BatchFilePreview {
  const preview: ParsedMaterialPreview = {
    meta: { filename, region_count: 1, total_blocks: 0, total_volume: 0 },
    blocks,
    container_items: containers,
    untranslated: [],
  }
  return { filename, kind: 'litematic', status: 'ok', preview, error: null }
}

function errorFile(filename: string): BatchFilePreview {
  return { filename, kind: 'litematic', status: 'error', preview: null, error: '无法解析该投影文件' }
}

function item(item_id: string, item_name: string, count: number): PreviewItem {
  return { item_id, item_name, count }
}

describe('aggregateItems', () => {
  it('同 item_id 跨文件求和', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('minecraft:stone', '石头', 10)]), multiplier: 1 },
      { file: okFile('b.litematic', [item('minecraft:stone', '石头', 5)]), multiplier: 1 },
    ])
    expect(result).toEqual([item('minecraft:stone', '石头', 15)])
  })

  it('倍数 × 文件 count 生效（multiplier=3 → 三倍）', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('minecraft:stone', '石头', 10)]), multiplier: 3 },
    ])
    expect(result).toEqual([item('minecraft:stone', '石头', 30)])
  })

  it('不同倍数按各自文件应用后求和', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('minecraft:stone', '石头', 10)]), multiplier: 2 },
      { file: okFile('b.litematic', [item('minecraft:stone', '石头', 10)]), multiplier: 3 },
    ])
    // 20 + 30
    expect(result).toEqual([item('minecraft:stone', '石头', 50)])
  })

  it('blocks 与 container_items 合并（同 item_id 在两组 → 求和）', () => {
    const result = aggregateItems([
      {
        file: okFile('a.litematic', [item('minecraft:chest', '箱子', 4)], [item('minecraft:chest', '箱子', 6)]),
        multiplier: 1,
      },
    ])
    expect(result).toEqual([item('minecraft:chest', '箱子', 10)])
  })

  it('未翻译名（item_name === item_id）作为 fallback 保留', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('mod:unknown', 'mod:unknown', 7)]), multiplier: 1 },
    ])
    expect(result).toEqual([item('mod:unknown', 'mod:unknown', 7)])
  })

  it('合并时优先保留已翻译名（先未翻译后翻译 → 升级为翻译名）', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('minecraft:stone', 'minecraft:stone', 1)]), multiplier: 1 },
      { file: okFile('b.litematic', [item('minecraft:stone', '石头', 1)]), multiplier: 1 },
    ])
    expect(result).toEqual([item('minecraft:stone', '石头', 2)])
  })

  it('按 count 降序排序', () => {
    const result = aggregateItems([
      { file: okFile('a.litematic', [item('a:a', '少', 1), item('b:b', '多', 100)]), multiplier: 1 },
    ])
    expect(result.map((r) => r.item_id)).toEqual(['b:b', 'a:a'])
  })

  it('status=error 的文件被跳过（不影响其余聚合）', () => {
    const result = aggregateItems([
      { file: errorFile('bad.litematic'), multiplier: 1 },
      { file: okFile('good.litematic', [item('minecraft:stone', '石头', 8)]), multiplier: 1 },
    ])
    expect(result).toEqual([item('minecraft:stone', '石头', 8)])
  })

  it('空输入返回空数组', () => {
    expect(aggregateItems([])).toEqual([])
  })

  it('不同 item_id 译名撞名时追加 registry id 消歧（防 from-items 重名 409）', () => {
    // 两个不同 registry id 都译作「石头」→ 必须区分，否则 POST /sheets/from-items 撞 uq_sheet_rows_top_name 409
    const result = aggregateItems([
      {
        file: okFile('a.litematic', [
          item('mod:stone_a', '石头', 3),
          item('mod:stone_b', '石头', 5),
        ]),
        multiplier: 1,
      },
    ])
    const names = result.map((r) => r.item_name)
    expect(names).toHaveLength(2)
    expect(new Set(names).size).toBe(2) // 两者互异
    expect(names).toContain('石头 (mod:stone_a)')
    expect(names).toContain('石头 (mod:stone_b)')
  })

  it('不修改入参（immutability）', () => {
    const inputs = [
      { file: okFile('a.litematic', [item('minecraft:stone', '石头', 2)]), multiplier: 2 },
    ]
    aggregateItems(inputs)
    // 原始 count 不被倍数覆写
    expect(inputs[0].file.preview!.blocks[0].count).toBe(2)
  })
})
