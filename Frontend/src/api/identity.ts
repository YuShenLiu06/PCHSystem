import { http } from '../utils/http'

// 与 Backend/app/schemas 对齐的 TS 类型

/** 账号简要信息 */
export interface AccountBrief {
  id: number
  is_temporary: boolean
  username: string | null
  role: string
}

/** 玩家简要信息 */
export interface PlayerBrief {
  uuid: string
  name: string
  role: string
}

/** 认证响应（exchange / login / register / claim-bind 共用 shape） */
export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: 'Bearer'
  player: PlayerBrief
  account: AccountBrief
}

/** /me 响应 */
export interface MeResponse {
  account: AccountBrief
  players: PlayerBrief[]
  active_uuid: string
}

/** /web-accounts/me 响应 */
export interface MyAccountResponse {
  account: AccountBrief
  players: PlayerBrief[]
}

/** 绑定短码响应 */
export interface BindCodeResponse {
  short_code: string
  expires_in: number
}

/** 绑定确认响应 */
export interface BindResultResponse {
  player: PlayerBrief
  account: AccountBrief
}

/** POST /auth/exchange —— 一次性 token 换 JWT pair */
export async function exchangeToken(token: string): Promise<AuthResponse> {
  const { data } = await http.post<AuthResponse>('/auth/exchange', { token })
  return data
}

/** POST /auth/login —— 用户名密码登录 */
export async function passwordLogin(username: string, password: string): Promise<AuthResponse> {
  const { data } = await http.post<AuthResponse>('/auth/login', { username, password })
  return data
}

/** GET /me —— 取当前身份信息（账号 + 绑定的玩家列表 + 当前激活 uuid） */
export async function fetchMe(): Promise<MeResponse> {
  const { data } = await http.get<MeResponse>('/me')
  return data
}

/** POST /web-accounts/register —— 注册永久账号（临时会话转正） */
export async function register(username: string, password: string): Promise<AuthResponse> {
  const { data } = await http.post<AuthResponse>('/web-accounts/register', { username, password })
  return data
}

/** GET /web-accounts/me —— 取账号详情（含绑定玩家列表） */
export async function getMyAccount(): Promise<MyAccountResponse> {
  const { data } = await http.get<MyAccountResponse>('/web-accounts/me')
  return data
}

/** POST /bind/issue —— 申请绑定短码 */
export async function issueBindCode(): Promise<BindCodeResponse> {
  const { data } = await http.post<BindCodeResponse>('/bind/issue')
  return data
}

/** POST /bind/confirm —— 确认绑定（输入游戏内 !!PCH bind 给的短码） */
export async function confirmBind(shortCode: string): Promise<BindResultResponse> {
  const { data } = await http.post<BindResultResponse>('/bind/confirm', { short_code: shortCode })
  return data
}

/** POST /bind/claim —— 临时会话下绑定到已有永久账号 */
export async function claimBind(username: string, password: string): Promise<AuthResponse> {
  const { data } = await http.post<AuthResponse>('/bind/claim', { username, password })
  return data
}
