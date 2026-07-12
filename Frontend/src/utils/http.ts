import axios, { AxiosError } from 'axios'
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
    // 401：清登录态跳 /auth（RS-5）。history 模式下 window.location.hash 不跳转，
    // 用 router.push 立即导航（路由守卫 beforeEach 兜底，未登录必落 /auth）。
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clear()
      router.push('/auth')
      return Promise.reject(err)
    }
    // 后端不可达信号：直连无 response（连接拒 / 客户端超时，axios 层不可区分），
    // 或经 web 反代时 backend 不可达（nginx 返 502/503/504）。统一一个分支 + 文案
    // （需求 1+3 合并：玩家动作一致——!!PCH status 排查）。
    const status = err.response?.status
    const noBackend = !err.response || status === 502 || status === 503 || status === 504
    if (noBackend) {
      const now = Date.now()
      if (now - lastNetErrAt > NET_ERR_THROTTLE_MS) {
        lastNetErrAt = now
        ElMessage.error(`后端超时或未部署${STATUS_HINT}`)
      }
    }
    return Promise.reject(err)
  },
)
