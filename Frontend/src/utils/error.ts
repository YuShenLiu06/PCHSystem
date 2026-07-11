// 从 axios 错误中抽取后端 detail 文案。
// 刻意不分支状态码、不给默认值——调用方按各自场景用 ?? 兜底（如 409 归档只读、归档加载失败等）。
// 401 不在此处理：由 utils/http.ts 响应拦截器统一 auth.clear() + 跳 /auth（RS-5），业务代码不写 status===401。
export function extractApiError(e: unknown): string | undefined {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail
  }
  return undefined
}
