import { http } from '../utils/http'

// 与 Backend/app/schemas/construction.py 对齐的 TS 类型

/** 单条方块净放置上报 */
export interface PlacementEntry {
  player_uuid: string
  registry_id: string
  placed_qty: number
  broken_qty: number
}

/** POST /report 请求体 */
export interface PlacementReport {
  sheet_id: number | null
  placements: PlacementEntry[]
}

/** 上报单条结果 */
export interface PlacementOutcome {
  player_uuid: string
  registry_id: string
  action: 'accepted' | 'skipped'
  reason: string
  net_delta: number
}

/** 上报响应 */
export interface PlacementReportResult {
  sheet_id: number | null
  attribution_source: 'explicit' | 'heuristic' | 'none'
  totals: { accepted: number; skipped: number }
  outcomes: PlacementOutcome[]
}

/** 归因查询 */
export interface ActiveSheet {
  id: number
  title: string
}
export interface ActiveSheetsResult {
  sheets: ActiveSheet[]
  heuristic_eligible: boolean
}

/** 进度（account 聚合） */
export interface ProgressAccountTotal {
  account_id: number
  display_name: string
  placed_qty: number
  broken_qty: number
  net_qty: number
}

/** 进度（明细） */
export interface ProgressBreakdownItem {
  account_id: number
  display_name: string
  registry_id: string
  placed_qty: number
  broken_qty: number
  net_qty: number
}

/** 材料完成度（迭代 2 需求 4）：按 registry 聚合 need vs net */
export interface ProgressMaterialItem {
  registry_id: string
  item_name: string
  need_qty: number
  net_qty: number
  /** need=0 → null；否则 [0,100] 视觉封顶（保留 1 位小数） */
  completion_pct: number | null
}

/** 时序快照点（迭代 2 需求 4）：某 account 截至该时刻的累计净放置 */
export interface ProgressTimelinePoint {
  account_id: number
  total_net: number
  recorded_at: string
}

export interface ConstructionProgress {
  sheet_id: number
  account_totals: ProgressAccountTotal[]
  breakdown: ProgressBreakdownItem[]
  material_completion: ProgressMaterialItem[]
  timeline: ProgressTimelinePoint[]
  /** 进入施工阶段时间（sheets.constructing_at）；早期数据可能为 null */
  construction_started_at: string | null
  /** 归档时间（sheets.archived_at）；constructing 期为 null */
  archived_at: string | null
}

/** 个人上报历史单条（GET /v1/construction/me/reports，placement_snapshots 投影） */
export interface MyReportHistoryItem {
  recorded_at: string
  sheet_id: number
  sheet_title: string
  total_net: number
  delta: number | null
}

/**
 * 个人上报事件流水单条（GET /v1/construction/me/report-events，迭代 5）。
 *
 * accepted + 所有 skip 原因都落库；让玩家看到「为什么我的上报被拒」。
 * - sheet_id / sheet_title：归因失败等场景可能为 null
 * - action：'accepted' | 'skipped'；reason：accepted="" skipped=中文
 * - net_delta：accepted=本次计入；skipped=被拒/尝试量（部分接受场景的 over 部分）
 */
export interface ReportEventItem {
  recorded_at: string
  sheet_id: number | null
  sheet_title: string | null
  registry_id: string | null
  action: 'accepted' | 'skipped'
  reason: string
  net_delta: number | null
}

/** admin 设置 */
export interface ConstructionSettings {
  allow_client_mods: boolean
  official_tracker_enabled: boolean
  allow_server_mods: boolean
  report_interval_seconds: number
  anti_cheat_threshold: number | null
}
export type ConstructionSettingsUpdate = Partial<
  Omit<ConstructionSettings, 'anti_cheat_threshold'>
> & {
  anti_cheat_threshold?: number | null
}

/** 服务端 mod 白名单（迭代 3：逐源 enabled 启停） */
export interface ServerModSourceEntry {
  name: string
  enabled: boolean
  approved_by_uuid: string | null
  approved_at: string
  notes: string | null
}

/** 上报源状态 + 历史 */
export interface SourceState {
  source_type: string | null
  source_id: string | null
  is_default: boolean
}
export interface SourceHistoryEntry {
  from_type: string | null
  from_id: string | null
  to_type: string
  to_id: string | null
  switched_at: string
  reason: string | null
}

/** 休眠源（迭代 2 需求 1）：曾活跃、当前 disabled 的 client_mod 源，可一键切回 */
export interface DormantSource {
  source_id: string
  last_active_at: string
}

export interface SourceMeResult {
  active: SourceState
  history: SourceHistoryEntry[]
  dormant_sources: DormantSource[]
}

// --- 加入施工（POST /me/join | switch | leave，GET /me/construction）---

/**
 * 当前活跃加入状态（与 `Backend/app/schemas/construction.py::ParticipantState` 对齐）。
 *
 * 后端约定：未加入时返回**空 ParticipantState 对象**（所有字段为 null），
 * 不是 `active: null`。调用方按 `sheet_id` 是否 truthy 判断「已加入」。
 */
export interface ParticipantState {
  sheet_id: number | null
  sheet_title: string | null
  joined_at: string | null
  join_source: 'auto' | 'manual' | null
}

/** `GET /me/construction` 响应：当前玩家活跃加入的施工项目（未加入 = 空 ParticipantState）。 */
export interface MyConstructionResult {
  active: ParticipantState
}

// --- 请求函数 ---

/** GET /v1/construction/{sheet_id}/progress —— 项目施工进度展示 */
export async function getConstructionProgress(sheetId: number): Promise<ConstructionProgress> {
  const { data } = await http.get<ConstructionProgress>(`/v1/construction/${sheetId}/progress`)
  return data
}

/** GET /v1/construction/me/reports —— 当前玩家个人上报历史（每次 report 成功一条，最近 limit 条） */
export async function getMyReportHistory(limit = 50): Promise<MyReportHistoryItem[]> {
  const { data } = await http.get<MyReportHistoryItem[]>('/v1/construction/me/reports', {
    params: { limit },
  })
  return data
}

/** GET /v1/construction/me/report-events —— 当前玩家完整上报事件流水（accepted + skip 原因，迭代 5） */
export async function getMyReportEvents(limit = 50): Promise<ReportEventItem[]> {
  const { data } = await http.get<ReportEventItem[]>('/v1/construction/me/report-events', {
    params: { limit },
  })
  return data
}

/** GET /v1/construction/settings —— admin 读运行时开关 */
export async function getConstructionSettings(): Promise<ConstructionSettings> {
  const { data } = await http.get<ConstructionSettings>('/v1/construction/settings')
  return data
}

/** PATCH /v1/construction/settings —— admin 改开关（部分更新） */
export async function updateConstructionSettings(
  patch: ConstructionSettingsUpdate,
): Promise<ConstructionSettings> {
  const { data } = await http.patch<ConstructionSettings>('/v1/construction/settings', patch)
  return data
}

/** GET /v1/construction/mod-sources —— admin 白名单列表 */
export async function listServerModSources(): Promise<ServerModSourceEntry[]> {
  const { data } = await http.get<ServerModSourceEntry[]>('/v1/construction/mod-sources')
  return data
}

/** POST /v1/construction/mod-sources —— admin 加白名单（幂等） */
export async function createServerModSource(
  name: string,
  notes?: string,
): Promise<ServerModSourceEntry> {
  const { data } = await http.post<ServerModSourceEntry>('/v1/construction/mod-sources', {
    name,
    notes: notes ?? null,
  })
  return data
}

/** DELETE /v1/construction/mod-sources/{name} —— admin 删白名单 */
export async function deleteServerModSource(name: string): Promise<void> {
  await http.delete(`/v1/construction/mod-sources/${encodeURIComponent(name)}`)
}

/** PATCH /v1/construction/mod-sources/{name} —— admin 逐源启停（迭代 3 卡片开关） */
export async function setServerModSourceEnabled(
  name: string,
  enabled: boolean,
): Promise<ServerModSourceEntry> {
  const { data } = await http.patch<ServerModSourceEntry>(
    `/v1/construction/mod-sources/${encodeURIComponent(name)}`,
    { enabled },
  )
  return data
}

/** POST /v1/construction/source/switch-server —— admin 切某玩家服务端源 */
export async function switchServerSource(body: {
  player_uuid: string
  source_type: 'mcdr' | 'server_mod'
  source_id?: string | null
  reason?: string | null
}): Promise<SourceState> {
  const { data } = await http.post<SourceState>('/v1/construction/source/switch-server', body)
  return data
}

/** POST /v1/construction/source/switch-self —— 玩家切自己上报模式 */
export async function switchSelfSource(body: {
  mode: 'server' | 'local'
  source_id?: string | null
  reason?: string | null
}): Promise<SourceState> {
  const { data } = await http.post<SourceState>('/v1/construction/source/switch-self', body)
  return data
}

/** GET /v1/construction/source/me —— 玩家查活跃源 + 历史 + 休眠源 */
export async function getMyConstructionSource(): Promise<SourceMeResult> {
  const { data } = await http.get<SourceMeResult>('/v1/construction/source/me')
  return data
}

// --- 加入施工 ---

/** GET /v1/construction/me/construction —— 查当前玩家活跃加入的施工项目（Me.vue「当前施工项目」卡片） */
export async function getMyConstruction(): Promise<MyConstructionResult> {
  const { data } = await http.get<MyConstructionResult>('/v1/construction/me/construction')
  return data
}

/**
 * POST /v1/construction/me/join —— 手动加入指定 sheet 的施工。
 *
 * - 后端对「已活跃加入本 sheet」幂等返回当前状态。
 * - 冲突（enforce_single_construction=True 且已活跃加入他 sheet）→ 后端 409，**函数内 rethrow**，
 *   由调用方处理（SheetEditor 用 switch 替代）。
 * - 未绑 Web 账号 → 后端 403（由 axios 拦截器 / 调用方各自处理）。
 */
export async function joinConstruction(sheetId: number): Promise<MyConstructionResult> {
  const { data } = await http.post<MyConstructionResult>('/v1/construction/me/join', {
    sheet_id: sheetId,
  })
  return data
}

/** POST /v1/construction/me/switch —— 切换到指定 sheet（leave 旧活跃 + join 新 sheet 同事务）。 */
export async function switchConstruction(sheetId: number): Promise<MyConstructionResult> {
  const { data } = await http.post<MyConstructionResult>('/v1/construction/me/switch', {
    sheet_id: sheetId,
  })
  return data
}

/** POST /v1/construction/me/leave —— 退出当前活跃加入（未活跃加入 → 后端幂等返回空态）。 */
export async function leaveConstruction(): Promise<MyConstructionResult> {
  const { data } = await http.post<MyConstructionResult>('/v1/construction/me/leave')
  return data
}
