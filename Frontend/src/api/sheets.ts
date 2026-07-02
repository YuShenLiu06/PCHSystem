import { http } from '../utils/http'

// 与 Backend/app/schemas/sheet.py 对齐的 TS 类型
export interface SheetSummary {
  id: number
  owner_uuid: string
  title: string
  created_at: string
  updated_at: string
}

export interface SheetDetail extends SheetSummary {
  rows: RowDetail[]
}

export interface RowDetail {
  id: number
  item_name: string
  need_qty: number
  done_flag: number
  sort_order: number
  updated_at: string
}

export interface SheetCreateRequest {
  title: string
}

export interface SheetPatchRequest {
  title: string
}

export interface RowUpsertRequest {
  item_name: string
  need_qty?: number
  done_flag?: number
  sort_order?: number
}

/** GET /sheets —— owner 传 "me" 只看自己，省略看全部 */
export async function listSheets(owner?: 'me' | string): Promise<SheetSummary[]> {
  const { data } = await http.get<SheetSummary[]>('/sheets', {
    params: owner ? { owner } : undefined,
  })
  return data
}

/** GET /sheets/{id} —— format 传 "csv" 返回 text/csv 字符串，省略返回 SheetDetail JSON */
export async function getSheet(id: number, format?: 'csv'): Promise<SheetDetail | string> {
  const { data } = await http.get<SheetDetail | string>(`/sheets/${id}`, {
    params: format ? { format } : undefined,
  })
  return data
}

/** POST /sheets —— 建表，owner=current，返回 SheetDetail（含空 rows） */
export async function createSheet(title: string): Promise<SheetDetail> {
  const { data } = await http.post<SheetDetail>('/sheets', { title })
  return data
}

/** PATCH /sheets/{id} —— 改标题，返回更新后的 SheetDetail */
export async function patchSheet(id: number, title: string): Promise<SheetDetail> {
  const { data } = await http.patch<SheetDetail>(`/sheets/${id}`, { title })
  return data
}

/** DELETE /sheets/{id} —— 删表（级联 rows），返回 204 空 */
export async function deleteSheet(id: number): Promise<void> {
  await http.delete(`/sheets/${id}`)
}

/** PUT /sheets/{id}/rows —— upsert 行（按 item_name），返回 RowDetail */
export async function upsertRow(
  id: number,
  body: RowUpsertRequest,
): Promise<RowDetail> {
  const { data } = await http.put<RowDetail>(`/sheets/${id}/rows`, body)
  return data
}

/** DELETE /sheets/{id}/rows/{rowId} —— 删行，返回 204 空 */
export async function deleteRow(id: number, rowId: number): Promise<void> {
  await http.delete(`/sheets/${id}/rows/${rowId}`)
}

/** GET /sheets/{id}?format=csv —— 单表 CSV 文本（JWT 鉴权） */
export async function exportSheetCSV(id: number): Promise<string> {
  return getSheet(id, 'csv') as Promise<string>
}

/** GET /sheets/export —— 全量 CSV 文本（service token 鉴权） */
export async function exportAllCSV(): Promise<string> {
  const { data } = await http.get<string>('/sheets/export')
  return data
}
