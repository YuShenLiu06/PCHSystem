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

/** POST /parsing/litematic —— multipart 上传 .litematic，返回解析+翻译后的材料预览（不落库） */
export async function previewLitematic(file: File): Promise<ParsedMaterialPreview> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<ParsedMaterialPreview>('/parsing/litematic', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}
