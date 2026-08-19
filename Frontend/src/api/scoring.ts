import { http } from '../utils/http'

// 与 Backend/app/schemas/scoring.py 对齐的 TS 类型（snake_case 直传；金额一律字符串保 Decimal 精度）

/** 记账原因（delta 方向由 reason 定，对齐后端 LEDGER_REASON_SIGN：前四 +、后二 −） */
export type ScoreReason =
  | 'collect'
  | 'build_a'
  | 'leader_bonus'
  | 'settle'
  | 'manual_adj'
  | 'season_reset'

/** score_ledger 单条流水（append-only，R-2） */
export interface ScoreLedgerEntry {
  id: number
  account_id: number
  delta: string
  reason: ScoreReason
  balance_after: string
  sheet_id: number | null
  operator_uuid: string | null
  idempotency_key: string | null
  note: string | null
  created_at: string
}

/** GET /v1/scoring/ledger 分页响应 */
export interface ScoreLedgerPage {
  items: ScoreLedgerEntry[]
  total: number
  page: number
  limit: number
}

/** ledger 查询参数（player_uuid 省略 = 全局；until 开区间上界） */
export interface LedgerQuery {
  player_uuid?: string
  /** 特权专用：按 Web 账号过滤（与 player_uuid 互斥，余额榜行下钻用） */
  account_id?: number
  since?: string
  until?: string
  page?: number
  limit?: number
}

/** 批量记账单条（amount 恒正字符串，方向由 reason 定；面板不传 operator_uuid——它是 Player UUID） */
export interface ScoreItem {
  player_uuid: string
  amount: string
  reason: ScoreReason
  sheet_id?: number | null
  operator_uuid?: string | null
  idempotency_key?: string | null
  note?: string | null
}

/** 单条结果（skip = accepted false + skip_reason；幂等回放 accepted true 且 entry 为原条目） */
export interface ScoreItemResult {
  player_uuid: string
  accepted: boolean
  entry: ScoreLedgerEntry | null
  idempotent_replay?: boolean
  skip_reason?: string | null
}

/** 批量记账响应（恒 200，逐条看 results） */
export interface ScoreBatchResult {
  results: ScoreItemResult[]
  accepted_count: number
  skipped_count: number
}

/** 特权玩家联想项（GET /v1/scoring/admin/players；仅返回已绑 WebAccount 的玩家） */
export interface PlayerOption {
  player_uuid: string
  player_name: string
  display_name: string | null
}

/** 单账号余额行（排名榜；归属锚 = WebAccount，player_names 按 last_seen_at DESC） */
export interface ScoreBalanceRow {
  account_id: number
  display_name: string
  player_names: string[]
  balance: string
  entries_count: number
  last_entry_at: string | null
}

/** GET /v1/scoring/admin/balances 分页响应 */
export interface ScoreBalancesPage {
  items: ScoreBalanceRow[]
  total: number
  page: number
  limit: number
}

/** 余额榜查询参数（page/limit 即后端分页参数） */
export interface BalancesQuery {
  page?: number
  limit?: number
}

/** 服务端分页每页档位（上限 200 对齐后端 admin/balances · ledger 的 le=200） */
export const LIMIT_OPTIONS = [20, 50, 100, 200] as const

/** GET /v1/scoring/ledger —— 流水分页（admin/owner JWT 或玩家自查，作用域后端定） */
export async function fetchLedger(params: LedgerQuery): Promise<ScoreLedgerPage> {
  const { data } = await http.get<ScoreLedgerPage>('/v1/scoring/ledger', { params })
  return data
}

/** POST /v1/scoring/admin/adjust —— 管理员调控（仅 admin/owner JWT；方向由 reason 定） */
export async function adminAdjust(payload: {
  items: ScoreItem[]
  notify?: boolean
  allow_overdraft?: boolean
}): Promise<ScoreBatchResult> {
  const { data } = await http.post<ScoreBatchResult>('/v1/scoring/admin/adjust', payload)
  return data
}

/** GET /v1/scoring/admin/balances —— 全账号余额排名（仅特权 JWT；排序 balance DESC） */
export async function fetchBalances(params: BalancesQuery): Promise<ScoreBalancesPage> {
  const { data } = await http.get<ScoreBalancesPage>('/v1/scoring/admin/balances', {
    params,
  })
  return data
}

/** GET /v1/scoring/admin/players —— 特权玩家联想（调分/筛选选人） */
export async function searchScoringPlayers(q: string): Promise<PlayerOption[]> {
  const { data } = await http.get<PlayerOption[]>('/v1/scoring/admin/players', {
    params: { q, limit: 10 },
  })
  return data
}
