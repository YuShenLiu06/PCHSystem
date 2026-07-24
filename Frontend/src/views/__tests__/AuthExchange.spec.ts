import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

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
  // token 过期/无效/已用 → 后端返 401，exchangeToken 抛错
  exchangeToken: vi.fn(() => Promise.reject(new Error('invalid or used token'))),
}))

vi.mock('element-plus', () => ({
  ElMessage: { warning: mocks.warning, success: vi.fn(), error: vi.fn() },
}))

import AuthExchange from '../AuthExchange.vue'

describe('AuthExchange.vue · token 兑换失败重定向（#34）', () => {
  beforeEach(() => {
    mocks.replace.mockReset()
    mocks.warning.mockReset()
  })

  it('兑换抛错（token 过期/无效）→ 弹中性 warning + replace 到 /login', async () => {
    const wrapper = mount(AuthExchange, {
      global: {
        plugins: [createPinia()],
        stubs: { 'el-result': true },
      },
    })
    await flushPromises()

    expect(mocks.warning).toHaveBeenCalledWith('登录失败，请重新登录或重新获取链接')
    expect(mocks.replace).toHaveBeenCalledWith('/login')
    wrapper.unmount()
  })
})
