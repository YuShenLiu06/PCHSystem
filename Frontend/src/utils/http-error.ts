import { isAxiosError } from 'axios'

/**
 * 后端不可达信号：直连无 response（连接拒 / 客户端超时，axios 层不可区分），
 * 或经 web 反代时 backend 不可达（nginx 502/503/504）。
 *
 * 与 http.ts 响应拦截器 noBackend 分支同源，抽成纯函数（不耦合 router/auth/element-plus），
 * 供业务代码（如 AuthExchange catch）复用——网络错误时拦截器已弹「后端超时或未部署」，
 * 业务侧据此跳过误导性「登录失败」提示，避免在业务代码散判 e.response.status（RS-5）。
 */
export function isNoBackendError(e: unknown): boolean {
  if (!isAxiosError(e)) return false
  const status = e.response?.status
  return !e.response || status === 502 || status === 503 || status === 504
}
