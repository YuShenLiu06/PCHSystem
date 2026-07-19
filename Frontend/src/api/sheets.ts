import { http } from '../utils/http'

// 与 Backend/app/schemas/sheet.py 对齐的 TS 类型

// 项目生命周期阶段：collecting（收集中，默认）→ constructing（施工中）→ archived（已归档，只读终态）
// 允许 collecting → archived 直跳（跳过施工）。详见 Docs/architecture/api/sheets.md
export type SheetStatus = 'collecting' | 'constructing' | 'archived'

export interface SheetSummary {
  id: number
  owner_uuid: string
  owner_name: string
  title: string
  status: SheetStatus
  archived_path: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

/** 项目级协管员（迁移 0014）：由 owner 授予，协助管理日常协作（tier B 写权限）。 */
export interface SheetManagerEntry {
  player_uuid: string
  player_name: string
  granted_at: string
}

export interface SheetDetail extends SheetSummary {
  rows: RowDetail[]
  managers: SheetManagerEntry[]
}

// mode: 0=lock（锁定/二元备齐），1=progress（进度/聚合众筹，多人贡献者列表）
// status: open（未认领/未交付）| claimed（认领中/部分交付）| done（已备齐）
// progress 行：claimant_uuid 恒为 null，contributors 列出所有贡献过的玩家（聚合）
// lock 行：contributors 恒为空数组，由 claimant_uuid 单人锁定
// parent_row_id: 非空表示子物品（父行 id），顶层行为 null
// qty_per_unit: 子物品每件需求量（父行 need_qty × qty_per_unit = 子行 need_qty），顶层行为 null
export interface RowDetail {
  id: number
  item_name: string
  // MC 注册名（namespace:path）；隐式可空——投影解析/MCDR 手持新建行会带，
  // 旧行与纯文本行为 null（游戏内一键提交按此精确匹配）
  registry_id: string | null
  need_qty: number
  mode: number
  status: string
  claimant_uuid: string | null
  claimant_name: string | null
  delivered_qty: number
  contributors: { player_uuid: string; player_name: string }[]
  sort_order: number
  parent_row_id: number | null
  qty_per_unit: number | null
  updated_at: string
}

export interface SheetCreateRequest {
  title: string
}

export interface SheetPatchRequest {
  title: string
}

// PUT /sheets/{sid}/rows 单端点按 row_id 分流（issue #20 改名重复修复）：
//   带 row_id → 按主键更新（item_name 可改名，其余字段部分更新；修改以 id 为定位主轴）；
//   不带 row_id → 按 item_name 新建（item_name 与 registry_id 至少传一个）。
//   带 parent_row_id → 新建子物品（子行），须同时传 registry_id + qty_per_unit（>=1）。
export interface RowUpsertRequest {
  row_id?: number
  item_name?: string
  registry_id?: string
  need_qty?: number
  mode?: number
  sort_order?: number
  parent_row_id?: number
  qty_per_unit?: number
}

// mode: 0=lock（默认）| 1=progress，与 RowUpsertRequest 同语义；用于 from-items 批量建表。
// 投影解析路径透传 registry_id（= PreviewItem.item_id）+ 中文 item_name。
// parent_row_id + qty_per_unit: 用于子物品（暂不支持批量建子表，仅为接口一致性）。
export interface SheetItemIn {
  item_name?: string
  registry_id?: string
  need_qty: number
  mode?: number
  sort_order?: number
  parent_row_id?: number
  qty_per_unit?: number
}

export interface SheetFromItemsRequest {
  title: string
  items: SheetItemIn[]
}

/** GET /sheets —— owner 传 "me" 只看自己；status 过滤 collecting|constructing|archived|active（active=收集+施工） */
export interface ListSheetsOptions {
  owner?: 'me'
  status?: SheetStatus | 'active'
}

export async function listSheets(opts?: ListSheetsOptions): Promise<SheetSummary[]> {
  const params: Record<string, string> = {}
  if (opts?.owner) params.owner = opts.owner
  if (opts?.status) params.status = opts.status
  const { data } = await http.get<SheetSummary[]>('/sheets', {
    params: Object.keys(params).length > 0 ? params : undefined,
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

/**
 * POST /sheets/{id}/advance —— owner/admin 阶段流转。
 * to 给定则 query 带 ?to=；省略则后端按状态机推进（collecting→constructing，constructing→archived）。
 * 返回流转后的 SheetDetail（含新 status / 归档后的 archived_path/archived_at）。
 */
export async function advanceSheet(id: number, to?: 'constructing' | 'archived'): Promise<SheetDetail> {
  const { data } = await http.post<SheetDetail>(`/sheets/${id}/advance`, undefined, {
    params: to ? { to } : undefined,
  })
  return data
}

/**
 * GET /sheets/{id}/archive —— 取归档 markdown 文档（text/markdown，纯字符串，非 JSON）。
 * 用 transformResponse 阻止 axios 把响应体当 JSON 解析（响应可能是空或非合法 JSON 的 md 文本）。
 */
export async function getSheetArchive(id: number): Promise<string> {
  const { data } = await http.get<string>(`/sheets/${id}/archive`, {
    responseType: 'text',
    transformResponse: (r) => r,
  })
  return data
}

/**
 * GET /sheets/{id}/archive/assets/{filename} —— 取归档产物（如 contributions.png）为 Blob。
 * 用 Blob 而非裸 URL：asset 端点需 JWT，<img> 直连发不出 Authorization 头，
 * 故用 axios（拦截器注入 Bearer）拉 blob → 调用方 URL.createObjectURL 给 <img>。
 * 无图（404）由调用方 try/catch 静默跳过。
 */
export async function getSheetArchiveAsset(id: number, filename: string): Promise<Blob> {
  const { data } = await http.get<Blob>(`/sheets/${id}/archive/assets/${filename}`, {
    responseType: 'blob',
  })
  return data
}

// ---------- 协管员（manager，迁移 0014）----------

/** GET /sheets/{id}/managers —— 列出协管员（任意登录玩家可读） */
export async function listSheetManagers(id: number): Promise<SheetManagerEntry[]> {
  const { data } = await http.get<SheetManagerEntry[]>(`/sheets/${id}/managers`)
  return data
}

/** POST /sheets/{id}/managers —— owner/超管授予协管员（body {player_uuid}），返回刷新后的列表 */
export async function grantSheetManager(id: number, playerUuid: string): Promise<SheetManagerEntry[]> {
  const { data } = await http.post<SheetManagerEntry[]>(`/sheets/${id}/managers`, {
    player_uuid: playerUuid,
  })
  return data
}

/** DELETE /sheets/{id}/managers/{player_uuid} —— owner/超管撤销（或 manager self-revoke），返回刷新后的列表 */
export async function revokeSheetManager(id: number, playerUuid: string): Promise<SheetManagerEntry[]> {
  const { data } = await http.delete<SheetManagerEntry[]>(`/sheets/${id}/managers/${playerUuid}`)
  return data
}

/** 玩家简要信息（协管员授予联想用） */
export interface PlayerBrief {
  player_uuid: string
  player_name: string
}

/** GET /players?q=<prefix> —— 按玩家名前缀联想（任意登录玩家可调）。前端选中后内部传 uuid 调 grant。 */
export async function searchPlayers(q: string): Promise<PlayerBrief[]> {
  const { data } = await http.get<PlayerBrief[]>('/players', { params: { q } })
  return data
}
