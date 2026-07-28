import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

// mock api（不真发请求）
vi.mock('../../../api/construction', () => ({
  getConstructionProgress: vi.fn(),
}))

// mock usePolling：避免 setInterval 干扰测试，refresh 暴露为 load 本身
vi.mock('../../../composables/usePolling', () => ({
  usePolling: vi.fn((fn: () => Promise<void>) => ({ refresh: fn, stop: () => {} })),
}))

// mock auth store：ConstructionProgress 取 viewer account id 算「我的贡献」；测试无账号即可
vi.mock('../../../stores/auth', () => ({
  useAuthStore: () => ({ account: null }),
}))

import { getConstructionProgress } from '../../../api/construction'
import ConstructionProgress from '../ConstructionProgress.vue'

const mocked = getConstructionProgress as unknown as ReturnType<typeof vi.fn>

// stub 图表组件 + element-plus（避免 echarts 在 jsdom 的 canvas 依赖）
const globalStubs = {
  TrendLineChart: { template: '<div data-test="trend" />' },
  MaterialCompletionChart: { template: '<div data-test="material" />' },
  ContributionPieChart: { template: '<div data-test="pie" />' },
  ElCard: { template: '<div><slot name="header" /><slot /></div>' },
  ElButton: { template: '<button><slot /></button>' },
  ElEmpty: { props: ['description'], template: '<div class="stub-empty">{{ description }}</div>' },
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

const PROGRESS_WITH_DATA = {
  sheet_id: 1,
  account_totals: [
    { account_id: 1, display_name: 'Alice', placed_qty: 10, broken_qty: 2, net_qty: 8 },
  ],
  breakdown: [],
  material_completion: [
    { registry_id: 'minecraft:stone', item_name: '石头', need_qty: 10, net_qty: 5, completion_pct: 50.0 },
  ],
  timeline: [
    { account_id: 1, total_net: 5, recorded_at: '2026-07-27T00:00:00Z' },
  ],
}

const PROGRESS_EMPTY = {
  sheet_id: 1,
  account_totals: [],
  breakdown: [],
  material_completion: [],
  timeline: [],
}

describe('ConstructionProgress', () => {
  it('mount 时调 getConstructionProgress(sheetId)', async () => {
    mocked.mockResolvedValueOnce(PROGRESS_WITH_DATA)
    mount(ConstructionProgress, { props: { sheetId: 42 }, global: { stubs: globalStubs } })
    await flushPromises()
    expect(mocked).toHaveBeenCalledWith(42)
  })

  it('有数据时不显空态文案', async () => {
    mocked.mockResolvedValueOnce(PROGRESS_WITH_DATA)
    const wrapper = mount(ConstructionProgress, { props: { sheetId: 1 }, global: { stubs: globalStubs } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('暂无施工记录')
    // 默认内容渲染了三图（stub）
    expect(wrapper.find('[data-test="trend"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="material"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="pie"]').exists()).toBe(true)
  })

  it('无数据时显空态文案', async () => {
    mocked.mockResolvedValueOnce(PROGRESS_EMPTY)
    const wrapper = mount(ConstructionProgress, { props: { sheetId: 1 }, global: { stubs: globalStubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('暂无施工记录')
  })

  it('具名 slot charts 可覆盖默认三图', async () => {
    mocked.mockResolvedValueOnce(PROGRESS_WITH_DATA)
    const Custom = defineComponent({
      name: 'CustomChart',
      setup() {
        return () => h('div', { class: 'custom-chart' }, '自定义图表')
      },
    })
    const wrapper = mount(ConstructionProgress, {
      props: { sheetId: 1 },
      slots: { charts: Custom },
      global: { stubs: globalStubs },
    })
    await flushPromises()
    expect(wrapper.find('.custom-chart').exists()).toBe(true)
    // 默认三图被覆盖（不存在）
    expect(wrapper.find('[data-test="trend"]').exists()).toBe(false)
  })
})
