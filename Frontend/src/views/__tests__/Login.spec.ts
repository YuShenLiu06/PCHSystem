import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  alertBox: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: mocks.push, replace: vi.fn() }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { alert: mocks.alertBox },
}))

vi.mock('../../api/identity', () => ({
  passwordLogin: vi.fn(),
}))

import Login from '../identity/Login.vue'

// 简化 stub（同 Me.spec 范式）：el-button 渲染原生 button + click 透传
const globalStubs = {
  ElButton: {
    props: ['loading', 'disabled', 'type'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  ElCard: { template: '<div><slot /><slot name="header" /></div>' },
  ElAlert: { template: '<div />' },
  ElForm: { template: '<div><slot /></div>' },
  ElFormItem: { template: '<div><slot /></div>' },
  ElInput: { template: '<input />' },
  BrandLogo: { template: '<div />' },
}

describe('Login.vue · 注册入口引导', () => {
  beforeEach(() => {
    mocks.push.mockReset()
    mocks.alertBox.mockClear()
  })

  it('登录按钮旁渲染「注册」按钮，点击弹游戏内注册流程引导（不跳转）', async () => {
    // Arrange
    const wrapper = mount(Login, { global: { plugins: [createPinia()], stubs: globalStubs } })
    const btns = wrapper.findAll('button')
    const registerBtn = btns.find((b) => b.text() === '注册')
    expect(registerBtn).toBeDefined()

    // Act
    await registerBtn!.trigger('click')

    // Assert — 引导弹窗说明 !!PCH login 流程，而非直接路由跳转
    expect(mocks.alertBox).toHaveBeenCalledTimes(1)
    const [content] = mocks.alertBox.mock.calls[0]
    expect(content).toContain('!!PCH login')
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
