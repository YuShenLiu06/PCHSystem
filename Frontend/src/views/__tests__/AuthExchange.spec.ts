import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import type { AxiosError } from 'axios'

// vi.mock 工厂在 import 前 hoisted 执行 → 用 vi.hoisted 提前创建 spy
const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { token: 'expired-or-invalid-token' } }),
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
}))

vi.mock('../../api/identity', () => ({
  exchangeToken: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: { warning: mocks.warning, success: vi.fn(), error: vi.fn() },
}))

import AuthExchange from '../AuthExchange.vue'
import { exchangeToken } from '../../api/identity'

const mockedExchangeToken = vi.mocked(exchangeToken)

/** 构造带 isAxiosError 标志的 AxiosError：省略 status = 无 response（网络层错误）。 */
function axiosError(status?: number): AxiosError {
  return (status === undefined
    ? { isAxiosError: true }
    : { isAxiosError: true, response: { status, data: { detail: 'err' } } }) as AxiosError
}

describe('AuthExchange.vue · token 兑换失败重定向（#34 + 网络错误提示修正）', () => {
  beforeEach(() => {
    mocks.replace.mockReset()
    mocks.warning.mockReset()
    mockedExchangeToken.mockReset()
  })

  async function mountAndFlush(): Promise<void> {
    const wrapper = mount(AuthExchange, {
      global: { plugins: [createPinia()], stubs: { 'el-result': true } },
    })
    await flushPromises()
    wrapper.unmount()
  }

  it('兑换抛 401（token 过期/无效）→ 弹中性 warning + replace 到 /login', async () => {
    mockedExchangeToken.mockRejectedValueOnce(axiosError(401))
    await mountAndFlush()
    expect(mocks.warning).toHaveBeenCalledWith('登录失败，请重新登录或重新获取链接')
    expect(mocks.replace).toHaveBeenCalledWith('/login')
  })

  it('网络错误（无 response / 后端宕机）→ 不重复弹 warning（http.ts 已弹）+ 仍 replace 到 /login', async () => {
    mockedExchangeToken.mockRejectedValueOnce(axiosError())
    await mountAndFlush()
    expect(mocks.warning).not.toHaveBeenCalled()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
  })

  it('反代 502 → 视同网络错误，不弹 warning + replace /login', async () => {
    mockedExchangeToken.mockRejectedValueOnce(axiosError(502))
    await mountAndFlush()
    expect(mocks.warning).not.toHaveBeenCalled()
    expect(mocks.replace).toHaveBeenCalledWith('/login')
  })
})
