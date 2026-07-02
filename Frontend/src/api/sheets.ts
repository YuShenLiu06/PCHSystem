import { http } from '../utils/http'

// 与 Backend/app/schemas/sheet.py 对齐的 TS 类型
export interface SheetSummary {
  id: number
  owner_uuid: string
  owner_name: string
  title: string
  created_at: string
  updated_at: string
}

export interface SheetDetail extends SheetSummary {
  rows: RowDetail[]
}

// mode: 0=lock（锁定/二元备齐），1=progress（进度/聚合众筹，多人贡献者列表）
// status: open（未认领/未交付）| claimed（认领中/部分交付）| done（已备齐）
// progress 行：claimant_uuid 恒为 null，contributors 列出所有贡献过的玩家（聚合）
// lock 行：contributors 恒为空数组，由 claimant_uuid 单人锁定
export interface RowDetail {
  id: number
  item_name: string
  need_qty: number
  mode: number
  status: string
  claimant_uuid: string | null
  claimant_name: string | null
  delivered_qty: number
  contributors: { player_uuid: string; player_name: string }[]
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
  mode?: number
  sort_order?: number
}

// mode: 0=lock（默认）| 1=progress，与 RowUpsertRequest 同语义；用于 from-items 批量建表
export interface SheetItemIn {
  item_name: string
  need_qty: number
  mode?: number
  sort_order?: number
}

export interface SheetFromItemsRequest {
  title: string
  items: SheetItemIn[]
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

/** POST /sheets/from-items —— 按材料清单一次性建表+批量行（mode 默认 lock），返回新建表详情 */
export async function createSheetFromItems(body: SheetFromItemsRequest): Promise<SheetDetail> {
  const { data } = await http.post<SheetDetail>('/sheets/from-items', body)
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

/** POST /sheets/{id}/rows/{rowId}/claim —— 任意登录玩家认领（open→claimed），无 body */
export async function claimRow(id: number, rowId: number): Promise<RowDetail> {
  const { data } = await http.post<RowDetail>(`/sheets/${id}/rows/${rowId}/claim`)
  return data
}

/** PATCH /sheets/{id}/rows/{rowId}/delivery —— 认领人上报交付量（>=need 自动 done），body {delivered_qty} */
export async function setRowDelivery(id: number, rowId: number, deliveredQty: number): Promise<RowDetail> {
  const { data } = await http.patch<RowDetail>(`/sheets/${id}/rows/${rowId}/delivery`, {
    delivered_qty: deliveredQty,
  })
  return data
}

/** POST /sheets/{id}/rows/{rowId}/contribute —— progress 行专用：任意登录玩家累加交付（body {qty}，qty>=1） */
export async function contributeRow(id: number, rowId: number, qty: number): Promise<RowDetail> {
  const { data } = await http.post<RowDetail>(`/sheets/${id}/rows/${rowId}/contribute`, { qty })
  return data
}

/** PATCH /sheets/{id}/rows/{rowId}/progress —— 拥有者/admin 直接修正 progress 行进度（body {delivered_qty}，绝对值可增可减，不动贡献者） */
export async function setRowProgress(id: number, rowId: number, deliveredQty: number): Promise<RowDetail> {
  const { data } = await http.patch<RowDetail>(`/sheets/${id}/rows/${rowId}/progress`, {
    delivered_qty: deliveredQty,
  })
  return data
}

/** POST /sheets/{id}/rows/{rowId}/release —— 认领人自放/拥有者解除锁定（claimed|done→open），无 body */
export async function releaseRow(id: number, rowId: number): Promise<RowDetail> {
  const { data } = await http.post<RowDetail>(`/sheets/${id}/rows/${rowId}/release`)
  return data
}

/** POST /sheets/{id}/rows/{rowId}/reject —— 拥有者打回（done→claimed, delivered 归零），无 body */
export async function rejectRow(id: number, rowId: number): Promise<RowDetail> {
  const { data } = await http.post<RowDetail>(`/sheets/${id}/rows/${rowId}/reject`)
  return data
}
