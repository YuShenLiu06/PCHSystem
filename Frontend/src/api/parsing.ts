import { http } from '../utils/http'

// 与 Backend/app/schemas/parsing.py 对齐的 TS 类型
// item_id 为 registry id（namespace:path，如 "create:item_vault"）；item_name 为中文展示名，
// 未翻译时后端用 item_id 兜底（untranslated 列出未翻译的 registry id）
export interface PreviewItem {
  item_id: string
  item_name: string
  count: number
}

export interface ParsedMaterialPreview {
  meta: {
    filename: string
    region_count: number
    total_blocks: number
    total_volume: number
  }
  blocks: PreviewItem[]
  container_items: PreviewItem[]
  untranslated: string[]
}

// /parsing/batch 为唯一解析端点（混型 .litematic / .nbt，单文件等价于批量 1 个）。
// 后端只解析、不收 multiplier（倍数是纯 UI 概念，前端聚合时应用，便于随时调倍数无需重新上传）。
// 与 Backend/app/schemas/parsing.py 的 BatchFilePreview / BatchParsedPreview 对齐。
export interface BatchFilePreview {
  filename: string
  kind: 'litematic' | 'nbt' // 按扩展名判定，失败项仍标 kind
  status: 'ok' | 'error'
  preview: ParsedMaterialPreview | null
  error: string | null // status=error 时为玩家可读中文文案
}

export interface BatchParsedPreview {
  files: BatchFilePreview[]
}

/**
 * POST /parsing/batch —— multipart 上传多个 .litematic / .nbt（FormData 重复 `files` 字段），
 * 返回每文件独立预览（成功/失败隔离）。多文件顺序解析可能 >120s，故 timeout 提到 180s。
 */
export async function previewBatch(files: File[]): Promise<BatchParsedPreview> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  const { data } = await http.post<BatchParsedPreview>('/parsing/batch', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180000,
  })
  return data
}
