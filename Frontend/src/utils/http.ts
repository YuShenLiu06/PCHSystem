import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

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
    // 401：清登录态跳 /auth（RS-5 不变）
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clear()
      window.location.hash = '#/auth'   // 兜底
      return Promise.reject(err)
    }
    // 需求 1：后端挂信号 —— 直连无 response，或经 web 反代时 backend 不可达（nginx 返 502/503/504）
    // 需求 3：ECONNABORTED / timeout = 请求超时（客户端 30s）→ 带 !!PCH status 提示
    const status = err.response?.status
    const noBackend = !err.response || status === 502 || status === 503 || status === 504
    const isTimeout = err.code === 'ECONNABORTED' || /timeout/i.test(err.message || '')
    if (noBackend || isTimeout) {
      const now = Date.now()
      if (now - lastNetErrAt > NET_ERR_THROTTLE_MS) {
        lastNetErrAt = now
        ElMessage.error(noBackend ? `后端服务未启动或不可达${STATUS_HINT}` : `请求超时${STATUS_HINT}`)
      }
    }
    return Promise.reject(err)
  },
)
