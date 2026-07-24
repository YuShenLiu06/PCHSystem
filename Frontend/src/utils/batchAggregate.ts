// 批量解析聚合：把多个文件的解析预览（blocks + container_items）按 registry id 合并求和。
// 倍数（multiplier）是纯 UI 概念——后端不收，前端聚合时按文件应用，便于随时调倍数无需重新上传。
import type { BatchFilePreview, PreviewItem } from '../api/parsing'

export interface AggregationInput {
  file: BatchFilePreview
  multiplier: number
}

/**
 * 聚合多文件预览：blocks + container_items 展平后按 item_id（registry id）分组求和，
 * 每组 count = Σ(文件 count × 该文件 multiplier)。display item_name 优先取已翻译名
 * （item_name !== item_id）；未翻译则回退 item_id。
 *
 * 同一 item_id 跨 blocks / container_items 也合并（批量流程产出单一材料清单）。
 *
 * 防护 from-items 重名 409：uq_sheet_rows_top_name 顶层按 item_name 唯一——两个不同
 * item_id 译名相同时，给所有撞名项追加 ` ({item_id})` 后缀消歧。
 *
 * 纯函数、不修改入参（immutability）。
 */
export function aggregateItems(inputs: AggregationInput[]): PreviewItem[] {
  const byId = new Map<string, { count: number; name: string }>()

  for (const { file, multiplier } of inputs) {
    if (file.status !== 'ok' || !file.preview) continue
    const all = [...file.preview.blocks, ...file.preview.container_items]
    for (const it of all) {
      const existing = byId.get(it.item_id)
      if (existing) {
        existing.count += it.count * multiplier
        // 已翻译名优先：若旧名是回退（=== item_id）而新名是翻译，则升级
        if (existing.name === it.item_id && it.item_name !== it.item_id) {
          existing.name = it.item_name
        }
      } else {
        byId.set(it.item_id, { count: it.count * multiplier, name: it.item_name })
      }
    }
  }

  // 按 count 降序；同数按 item_id 升序保证稳定
  const result: PreviewItem[] = [...byId.entries()].map(([item_id, v]) => ({
    item_id,
    item_name: v.name,
    count: v.count,
  }))
  result.sort((a, b) => b.count - a.count || (a.item_id < b.item_id ? -1 : 1))

  // 撞名消歧：对出现 >1 次的 item_name，给所有持有者追加 ` ({item_id})`
  const nameCounts = new Map<string, number>()
  for (const r of result) nameCounts.set(r.item_name, (nameCounts.get(r.item_name) ?? 0) + 1)
  for (const r of result) {
    if ((nameCounts.get(r.item_name) ?? 0) > 1) {
      r.item_name = `${r.item_name} (${r.item_id})`
    }
  }

  return result
}
