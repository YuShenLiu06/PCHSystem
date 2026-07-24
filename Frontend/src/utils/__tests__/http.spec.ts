import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosError } from 'axios'

// vi.mock 工厂不能引用非 hoisted 变量 → 用 vi.hoisted 提前创建 spy / 可变状态
const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  // router.currentRoute 是 Ref；拦截器读 router.currentRoute.value.path
  currentRoute: { value: { path: '/me' } },
  clear: vi.fn(),
  elMessage: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}))

vi.mock('../../router', () => ({
  router: { currentRoute: mocks.currentRoute, push: mocks.push },
}))
vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ clear: mocks.clear }),
}))
vi.mock('element-plus', () => ({ ElMessage: mocks.elMessage }))

import { http } from '../http'

/** 构造一个只含拦截器所读字段的 AxiosError（response.status / response.data.detail）。 */
function rejectWith(status: number, detail = 'err'): AxiosError {
  return { response: { status, data: { detail } } } as AxiosError
}

describe('http 响应拦截器 · 401 处理（RS-5 + #34 /auth 特例）', () => {
  beforeEach(() => {
    mocks.push.mockReset()
    mocks.clear.mockReset()
    mocks.elMessage.error.mockReset()
  })

  async function fire401(): Promise<void> {
    http.defaults.adapter = (() => Promise.reject(rejectWith(401))) as AxiosAdapter
    await expect(http.get('/x')).rejects.toBeTruthy()
  }

  it('受保护页（/me）401 → clear 登录态 + push /auth（RS-5）', async () => {
    mocks.currentRoute.value.path = '/me'
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).toHaveBeenCalledWith('/auth')
  })

  it('/auth 页 401（token 兑换失败）→ 仍 clear 但不 push（避免推回当前页，#34）', async () => {
    mocks.currentRoute.value.path = '/auth'
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
