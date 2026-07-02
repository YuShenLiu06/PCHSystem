import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { usePolling, type PollingHandle, type UsePollingOptions } from '../usePolling'

// 把 usePolling 挂到一个真实组件里跑，使 onUnmounted 生效并由 wrapper.unmount() 触发
function mountPolling(
  fn: () => Promise<void>,
  options: UsePollingOptions,
): { wrapper: ReturnType<typeof mount>; handle: PollingHandle } {
  let handle!: PollingHandle
  const wrapper = mount(
    defineComponent({
      name: 'PollingHost',
      setup() {
        handle = usePolling(fn, options)
        return () => null
      },
    }),
  )
  return { wrapper, handle }
}

// jsdom 的 document.hidden 默认 visible，这里可控重置
function setHidden(value: boolean): void {
  Object.defineProperty(document, 'hidden', { configurable: true, writable: true, value })
}

beforeEach(() => {
  vi.useFakeTimers()
  setHidden(false)
})

afterEach(() => {
  vi.useRealTimers()
  setHidden(false)
})

describe('usePolling', () => {
  describe('基本周期', () => {
    it('可见时按 intervalMs 周期调用 fn，首次延迟不立即打', async () => {
      const fn = vi.fn().mockResolvedValue(undefined)
      mountPolling(fn, { intervalMs: 5_000 })

      expect(fn).not.toHaveBeenCalled() // 首载延迟一个 interval
      await vi.advanceTimersByTimeAsync(5_000)
      expect(fn).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(5_000)
      expect(fn).toHaveBeenCalledTimes(2)
    })
  })

  describe('Page Visibility', () => {
    it('document.hidden 期间暂停，回到可见立即触发一次并恢复节拍', async () => {
      const fn = vi.fn().mockResolvedValue(undefined)
      mountPolling(fn, { intervalMs: 5_000 })

      await vi.advanceTimersByTimeAsync(5_000)
      expect(fn).toHaveBeenCalledTimes(1)

      // 切到后台：暂停
      setHidden(true)
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(60_000)
      expect(fn).toHaveBeenCalledTimes(1) // 后台不调用

      // 切回前台：立即触发一次
      setHidden(false)
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
      expect(fn).toHaveBeenCalledTimes(2)
    })
  })

  describe('enabled 开关', () => {
    it('enabled 返回 false 时不发请求', async () => {
      const fn = vi.fn().mockResolvedValue(undefined)
      mountPolling(fn, { intervalMs: 1_000, enabled: () => false })

      await vi.advanceTimersByTimeAsync(10_000)
      expect(fn).not.toHaveBeenCalled()
    })
  })

  describe('失败退避', () => {
    it('连续失败时间隔指数退避，成功后间隔归位，并回调 onError', async () => {
      const fn = vi
        .fn<() => Promise<void>>()
        .mockRejectedValueOnce(new Error('boom'))
        .mockRejectedValueOnce(new Error('boom'))
        .mockResolvedValueOnce(undefined)
        .mockResolvedValue(undefined)
      const onError = vi.fn()
      mountPolling(fn, { intervalMs: 1_000, onError })

      // 第 1 次：1000ms 后失败
      await vi.advanceTimersByTimeAsync(1_000)
      expect(fn).toHaveBeenCalledTimes(1)
      expect(onError).toHaveBeenCalledTimes(1)

      // 退避一档：下次 2000ms（1000 × 2^1）后
      await vi.advanceTimersByTimeAsync(1_999)
      expect(fn).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(fn).toHaveBeenCalledTimes(2)

      // 退避二档：下次 4000ms（1000 × 2^2）后
      await vi.advanceTimersByTimeAsync(3_999)
      expect(fn).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(1)
      expect(fn).toHaveBeenCalledTimes(3) // 这次成功，failCount 归零

      // 成功归位：下次 1000ms 后
      await vi.advanceTimersByTimeAsync(999)
      expect(fn).toHaveBeenCalledTimes(3)
      await vi.advanceTimersByTimeAsync(1)
      expect(fn).toHaveBeenCalledTimes(4)
    })
  })

  describe('卸载清理', () => {
    it('unmount 后定时器与监听均清理，不再调用 fn', async () => {
      const fn = vi.fn().mockResolvedValue(undefined)
      const { wrapper } = mountPolling(fn, { intervalMs: 1_000 })

      await vi.advanceTimersByTimeAsync(1_000)
      expect(fn).toHaveBeenCalledTimes(1)

      wrapper.unmount()
      await vi.advanceTimersByTimeAsync(100_000)
      expect(fn).toHaveBeenCalledTimes(1) // 卸载后停摆
    })
  })

  describe('手动 refresh', () => {
    it('立即触发 fn，且正在跑时重入跳过', async () => {
      let releaseFirst!: () => void
      const fn = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            releaseFirst = resolve
          }),
      )
      const { handle } = mountPolling(fn, { intervalMs: 10_000 })

      // 手动触发，fn 处于 pending
      const first = handle.refresh()
      expect(fn).toHaveBeenCalledTimes(1)

      // 重入：不应二次发起
      await handle.refresh()
      expect(fn).toHaveBeenCalledTimes(1)

      releaseFirst()
      await first
    })
  })
})
