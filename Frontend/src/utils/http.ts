import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import { isNoBackendError } from './http-error'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { router } from '../router'
import { refreshToken as refreshTokens } from '../api/identity'

// 全局请求超时（需求 3）：后端绝大多数端点 <1s；投影解析/归档稍久，30s 兜底足够，
// 触发 ECONNABORTED → 拦截器给「!!PCH status 排查」提示。慢请求可调用级 timeout 覆盖。
export const http = axios.create({ baseURL: '/api', timeout: 30000 })

// 网络/超时错误节流：usePolling（SheetList 10s / SheetEditor 1s）失败会重复进拦截器，
// 同类提示 5s 内只弹一次，避免刷屏。
let lastNetErrAt = 0
const NET_ERR_THROTTLE_MS = 5000
const STATUS_HINT = '（可在游戏内 !!PCH status 排查服务状况）'

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) config.headers.Authorization = `Bearer ${auth.accessToken}`
  return config
})

// #42：access token 1h 过期、refresh token 7d 续签。这些端点的 401 = 凭证错误（不是 access
// 过期），续签后重试只会再 401（wrong password→refresh→重试→再 401…）死循环，故不参与续签。
// /auth/refresh 自身列入 → 续签失败时不递归再调 refresh。
const NO_REFRESH_URLS = new Set(['/auth/login', '/auth/exchange', '/auth/refresh', '/bind/claim'])

// 并发去重：多个请求同时 401 时共享一次 refresh；in-flight 期间后续 401 await 同一个 promise。
let refreshPromise: Promise<void> | null = null

/** 用 refresh token 续签并整包更新 store（tokens + player + account，顺手刷新 display_name）。 */
function refreshSession(): Promise<void> {
  if (refreshPromise) return refreshPromise
  const auth = useAuthStore()
  refreshPromise = refreshTokens(auth.refreshToken)
    .then((resp) => {
      // refresh 端点对有效 token 必返 player；null 视为失败兜底（auth.set 要求非空）
      if (!resp.player) throw new Error('refresh returned no player')
      auth.set(
        { access_token: resp.access_token, refresh_token: resp.refresh_token },
        resp.player,
        resp.account,
      )
    })
    .catch((e) => {
      // refresh 失败（refresh token 过期 / 被吊销 / 后端 5xx）→ 清场 + 跳登录。
      // 放此层而非各调用方 catch：并发 401 共享同一 promise，此 catch 只执行一次，
      // 避免多个 awaiter 各自 clear + push 造成 double navigation（CR #1）。
      auth.clear()
      if (!router.currentRoute.value.meta?.public) router.push('/auth')
      throw e
    })
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

http.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const auth = useAuthStore()
    const url = err.config?.url ?? ''
    // 续签触发条件：401 + 有 refresh + 原请求是鉴权请求（有 access → 拦截器加过 Bearer）
    // + 不是凭证端点（避免死循环 / refresh 递归）。
    const canRefresh =
      err.response?.status === 401 &&
      !!auth.refreshToken &&
      !!auth.accessToken &&
      !NO_REFRESH_URLS.has(url)

    if (canRefresh) {
      // CR #2：重放后的请求若再次 401（新 token 仍被拒——服务端撤销竞态 / 时钟偏移），
      // 不再递归 refresh，否则 refreshPromise 已清空会再起一次 refresh，形成无界循环。
      if ((err.config as AxiosRequestConfig & { _replayed?: boolean })._replayed) {
        return Promise.reject(err)
      }
      try {
        // refresh 失败已在 refreshSession 内部 clear + push（只一次，CR #1）
        await refreshSession()
        // 重放原请求；请求拦截器自动注入新 access token；打 _replayed 标记防重放后再次 401 递归续签
        return http({ ...err.config, _replayed: true } as AxiosRequestConfig)
      } catch {
        return Promise.reject(err)
      }
    }

    // 不可续签的 401：保留原 RS-5 处理——清场 + 受保护页跳 /auth。
    // history 模式下 window.location.hash 不跳转，用 router.push 立即导航（路由守卫 beforeEach 兜底）。
    if (err.response?.status === 401) {
      // /auth/refresh 自身 401 不在此清场——它是「续签尝试失败」，由 refreshSession 的 catch
      // 统一 clear + push，避免这里再清一次造成 double clear / double push（CR #1）。
      if (url !== '/auth/refresh') {
        auth.clear()
        // 公开页（/auth /login /register，均 meta.public）的 401 = 凭证错误而非会话失效 →
        // 不重复 push（否则 /login 输错密码会被推去 /auth、再 replace 回 /login，表单清空 + 闪烁），
        // 由对应页面自身 catch 决定去向；受保护页照旧回 /auth（路由守卫兜底）。
        if (!router.currentRoute.value.meta?.public) {
          router.push('/auth')
        }
      }
      return Promise.reject(err)
    }
    // 后端不可达信号：复用 isNoBackendError（直连无 response / 反代 502/503/504）。
    // 统一文案（需求 1+3 合并：玩家动作一致——!!PCH status 排查）。
    if (isNoBackendError(err)) {
      const now = Date.now()
      if (now - lastNetErrAt > NET_ERR_THROTTLE_MS) {
        lastNetErrAt = now
        ElMessage.error(`后端超时或未部署${STATUS_HINT}`)
      }
    }
    return Promise.reject(err)
  },
)
