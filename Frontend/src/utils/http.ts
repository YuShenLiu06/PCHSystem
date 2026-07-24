import axios, { AxiosError } from 'axios'
import { isNoBackendError } from './http-error'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { router } from '../router'

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

http.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    // 401：清登录态（RS-5）。history 模式下 window.location.hash 不跳转，
    // 用 router.push 立即导航（路由守卫 beforeEach 兜底，未登录必落 /auth）。
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clear()
      // 公开页（/auth /login /register，均 meta.public）的 401 = 凭证错误而非会话失效 →
      // 不重复 push（否则 /login 输错密码会被推去 /auth、再 replace 回 /login，表单清空 + 闪烁），
      // 由对应页面自身 catch 决定去向；受保护页照旧回 /auth（路由守卫兜底）。
      if (!router.currentRoute.value.meta?.public) {
        router.push('/auth')
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
