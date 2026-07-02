import { onUnmounted } from 'vue'

// 连续失败退避上限：intervalMs × 2^failCount，封顶 60s，避免狂打挂掉的后端
const BACKOFF_MAX_MS = 60_000

export interface UsePollingOptions {
  /** 正常轮询间隔（ms） */
  intervalMs: number
  /** 可选外部开关；返回 false 时不发起请求，但仍按节拍续排以便恢复后自动续上 */
  enabled?: () => boolean
  /** 单次失败回调（不静默吞错）。默认 console.debug，不弹 toast 防刷屏 */
  onError?: (e: unknown) => void
}

export interface PollingHandle {
  /** 手动触发一次；与定时器共用 in-flight guard，正在跑则跳过 */
  refresh: () => Promise<void>
}

/**
 * 轮询 composable，周期性执行 fn。自动处理：
 * - 页面后台（document.hidden）暂停，回到前台立即触发一次并恢复节拍（「后台不更新」）；
 * - 连续失败指数退避（intervalMs × 2^failCount，上限 BACKOFF_MAX_MS），任一成功归零；
 * - 组件卸载清理定时器与 visibilitychange 监听；
 * - 请求重叠保护：上一次 fn 未完成不发起下一次，手动 refresh 重入也跳过。
 *
 * 用递归 setTimeout（非 setInterval）：fn 完成（resolve/reject）后才排下一次，
 * 天然防请求堆积，慢响应不会叠加。
 */
export function usePolling(
  fn: () => Promise<void>,
  options: UsePollingOptions,
): PollingHandle {
  const intervalMs = options.intervalMs
  const isEnabled = options.enabled ?? (() => true)
  const onError =
    options.onError ??
    ((e: unknown) => {
      // 轮询失败高频，不弹 toast；仅低级别诊断日志，调用方可覆盖
      console.debug('[usePolling] refresh failed', e)
    })

  let timerId: ReturnType<typeof setTimeout> | null = null
  let running = false // in-flight guard
  let stopped = false // unmounted / 终止
  let failCount = 0

  function currentDelay(): number {
    if (failCount <= 0) return intervalMs
    return Math.min(intervalMs * 2 ** failCount, BACKOFF_MAX_MS)
  }

  async function run(): Promise<void> {
    if (running || stopped) return
    if (!isEnabled()) {
      // 开关关闭：不发请求，但续排下一次以便开关恢复后自动续上
      scheduleNext()
      return
    }
    running = true
    try {
      await fn()
      failCount = 0
    } catch (e: unknown) {
      failCount += 1
      onError(e)
    } finally {
      running = false
      if (!stopped) scheduleNext()
    }
  }

  function scheduleNext(): void {
    if (stopped) return
    if (timerId !== null) clearTimeout(timerId)
    timerId = setTimeout(() => {
      void run()
    }, currentDelay())
  }

  function onVisibilityChange(): void {
    if (stopped) return
    if (document.hidden) {
      // 后台暂停：清掉待执行的定时器
      if (timerId !== null) {
        clearTimeout(timerId)
        timerId = null
      }
    } else {
      // 回到前台：立即刷新一次（正在跑则 run 内部重入跳过），随后按节拍续
      void refresh()
    }
  }

  async function refresh(): Promise<void> {
    if (running || stopped) return
    await run()
  }

  // 首次延迟一个 interval 再跑（组件 onMounted 已首载，避免立即重复请求）
  scheduleNext()
  document.addEventListener('visibilitychange', onVisibilityChange)

  onUnmounted(() => {
    stopped = true
    if (timerId !== null) clearTimeout(timerId)
    timerId = null
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { refresh }
}
