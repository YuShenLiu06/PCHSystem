import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// vi.hoisted 提前创建 spy（vi.mock 工厂在 import 前 hoisted 执行）
const mocks = vi.hoisted(() => ({
  // identity
  fetchMe: vi.fn(),
  getMyConstructionSource: vi.fn(),
  getMyReportEvents: vi.fn(),
  // construction join
  getMyConstruction: vi.fn(),
  leaveConstruction: vi.fn(),
  // element-plus
  ElMessageSuccess: vi.fn(),
  ElMessageError: vi.fn(),
  ElMessageBoxConfirm: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../../api/identity', () => ({
  fetchMe: mocks.fetchMe,
  confirmBind: vi.fn(),
  updateMyDisplayName: vi.fn(),
}))

vi.mock('../../api/construction', () => ({
  getMyConstructionSource: mocks.getMyConstructionSource,
  getMyReportEvents: mocks.getMyReportEvents,
  switchSelfSource: vi.fn(),
  getMyConstruction: mocks.getMyConstruction,
  leaveConstruction: mocks.leaveConstruction,
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    success: mocks.ElMessageSuccess,
    error: mocks.ElMessageError,
    warning: vi.fn(),
  },
  ElMessageBox: {
    confirm: mocks.ElMessageBoxConfirm,
  },
}))

import Me from '../Me.vue'

// 简化 stub：el-card 透传 header slot；el-tag/el-button 渲染 slot/默认文案
const globalStubs = {
  ElCard: {
    props: ['header'],
    template:
      '<div class="stub-card"><div class="stub-header">{{ header }}</div><slot /><slot name="header" /></div>',
  },
  ElButton: {
    props: ['loading', 'disabled', 'type'],
    emits: ['click'],
    template:
      '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  ElTag: { template: '<span><slot /></span>' },
  ElAlert: { template: '<div />' },
  ElEmpty: { template: '<div />' },
  ElTable: { template: '<div />' },
  ElDivider: { template: '<div />' },
  ElCollapse: { template: '<div><slot /></div>' },
  ElTimeline: { template: '<div><slot /></div>' },
  ElInput: { template: '<input />' },
  ElRadioGroup: { template: '<div><slot /></div>' },
  ElRadio: { template: '<div />' },
  ElCollapseItem: { template: '<div><slot /></div>' },
  // 透传 timestamp / color 便于断言；wrapper.text() 会包含 slot 内容
  ElTimelineItem: {
    props: ['timestamp', 'color', 'placement'],
    template: '<div class="stub-timeline-item" :data-color="color" :data-timestamp="timestamp"><slot /></div>',
  },
  ElTooltip: {
    props: ['content', 'placement'],
    template: '<span class="stub-tooltip" :title="content"><slot /></span>',
  },
  ElDialog: { template: '<div />' },
  ElForm: { template: '<div><slot /></div>' },
  ElFormItem: { template: '<div><slot /></div>' },
}

const EMPTY_CONSTRUCTION = {
  active: { sheet_id: null, sheet_title: null, joined_at: null, join_source: null },
}

const JOINED_CONSTRUCTION = {
  active: {
    sheet_id: 42,
    sheet_title: '中央塔楼',
    joined_at: '2026-07-28T10:00:00Z',
    join_source: 'manual' as const,
  },
}

function mockMeResponse() {
  // 玩家账号场景（players 非空才有玩家专属卡片；空 = player-less 托管账号，#74）
  mocks.fetchMe.mockResolvedValue({
    account: { id: 1, username: 'u', display_name: 'U', role: 'player', is_temporary: false },
    players: [{ uuid: '00000000-0000-0000-0000-000000000001', name: 'u', role: 'player' }],
    active_uuid: '00000000-0000-0000-0000-000000000001',
  })
  mocks.getMyConstructionSource.mockResolvedValue({
    active: { source_type: 'mcdr', source_id: 'official', is_default: true },
    history: [],
    dormant_sources: [],
  })
  mocks.getMyReportEvents.mockResolvedValue([])
}

function mountMe() {
  return mount(Me, { global: { stubs: globalStubs } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockMeResponse()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Me.vue · player-less 托管账号（#74）', () => {
  it('玩家专属卡片不渲染 + 玩家端点不调用', async () => {
    // Arrange — 无绑定玩家的托管账号（admin 面板）
    mocks.fetchMe.mockResolvedValue({
      account: { id: 2, username: 'panel', display_name: null, role: 'owner', is_temporary: false },
      players: [],
      active_uuid: null,
    })
    // Act
    const wrapper = mountMe()
    await flushPromises()
    // Assert — 账号卡片在、玩家专属卡片全隐藏
    expect(wrapper.text()).toContain('账号信息')
    expect(wrapper.text()).not.toContain('当前施工项目')
    expect(wrapper.text()).not.toContain('施工上报源')
    expect(wrapper.text()).not.toContain('我的上报历史')
    // 玩家专属端点未被调用（调了会 401）
    expect(mocks.getMyConstructionSource).not.toHaveBeenCalled()
    expect(mocks.getMyReportEvents).not.toHaveBeenCalled()
    expect(mocks.getMyConstruction).not.toHaveBeenCalled()
  })
})

describe('Me.vue · 当前施工项目卡片', () => {
  it('未加入时显示引导文案 + 提示游戏内命令', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    const wrapper = mountMe()
    await flushPromises()
    const cardText = wrapper.text()
    expect(cardText).toContain('当前施工项目')
    expect(cardText).toContain('当前未加入任何施工项目')
    expect(cardText).toContain('!!PCH construction join')
    // 未显「退出施工」按钮
    expect(wrapper.text()).not.toContain('退出施工')
  })

  it('已加入时显示项目标题 + 加入时间 + 退出按钮', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(JOINED_CONSTRUCTION)
    const wrapper = mountMe()
    await flushPromises()
    const cardText = wrapper.text()
    expect(cardText).toContain('中央塔楼')
    expect(cardText).toContain('加入时间')
    expect(cardText).toContain('手动加入')
    expect(cardText).toContain('退出施工')
  })

  it('退出施工：二次确认 → leaveConstruction → 成功 toast + 状态刷新', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(JOINED_CONSTRUCTION)
    mocks.ElMessageBoxConfirm.mockResolvedValueOnce('confirm')
    // leaveConstruction 返回空态
    mocks.leaveConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    const wrapper = mountMe()
    await flushPromises()
    // 找到「退出施工」按钮并点击
    const leaveBtn = wrapper.findAll('button').find((b) => b.text().includes('退出施工'))
    expect(leaveBtn).toBeDefined()
    await leaveBtn!.trigger('click')
    await flushPromises()
    expect(mocks.ElMessageBoxConfirm).toHaveBeenCalled()
    expect(mocks.leaveConstruction).toHaveBeenCalled()
    expect(mocks.ElMessageSuccess).toHaveBeenCalledWith('已退出施工项目')
    // 状态已刷新为空态
    expect(wrapper.text()).toContain('当前未加入任何施工项目')
  })

  it('退出施工：用户取消 → 不调 leaveConstruction', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(JOINED_CONSTRUCTION)
    mocks.ElMessageBoxConfirm.mockRejectedValueOnce('cancel')
    const wrapper = mountMe()
    await flushPromises()
    const leaveBtn = wrapper.findAll('button').find((b) => b.text().includes('退出施工'))
    await leaveBtn!.trigger('click')
    await flushPromises()
    expect(mocks.leaveConstruction).not.toHaveBeenCalled()
    // 仍显已加入态
    expect(wrapper.text()).toContain('中央塔楼')
  })

  it('退出施工：leaveConstruction 失败 → ElMessage.error + 状态不变', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(JOINED_CONSTRUCTION)
    mocks.ElMessageBoxConfirm.mockResolvedValueOnce('confirm')
    mocks.leaveConstruction.mockRejectedValueOnce({ response: { status: 500, data: { detail: '服务器错误' } } })
    const wrapper = mountMe()
    await flushPromises()
    const leaveBtn = wrapper.findAll('button').find((b) => b.text().includes('退出施工'))
    await leaveBtn!.trigger('click')
    await flushPromises()
    expect(mocks.ElMessageError).toHaveBeenCalledWith('服务器错误')
    // 仍显已加入态
    expect(wrapper.text()).toContain('中央塔楼')
  })

  it('auto 来源 tag 显示「自动（认领/上交触发）」', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce({
      active: {
        sheet_id: 5,
        sheet_title: '广场',
        joined_at: '2026-07-28T08:00:00Z',
        join_source: 'auto',
      },
    })
    const wrapper = mountMe()
    await flushPromises()
    expect(wrapper.text()).toContain('自动（认领/上交触发）')
  })
})

describe('Me.vue · 我的上报历史（事件流水，迭代 5）', () => {
  it('空态文案改为「暂无上报事件（含成功与被拒记录）」', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    mocks.getMyReportEvents.mockResolvedValueOnce([])
    const wrapper = mountMe()
    await flushPromises()
    expect(wrapper.text()).toContain('暂无上报事件（含成功与被拒记录）')
  })

  it('accepted 事件绿色 +N（绿色 timeline-item color）', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    mocks.getMyReportEvents.mockResolvedValueOnce([
      {
        recorded_at: '2026-07-28T10:00:00Z',
        sheet_id: 42,
        sheet_title: '中央塔楼',
        registry_id: 'minecraft:stone',
        action: 'accepted',
        reason: '',
        net_delta: 7,
      },
    ])
    const wrapper = mountMe()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('中央塔楼')
    expect(text).toContain('+7')
    expect(text).toContain('minecraft:stone')
    // accepted 颜色 = accent 绿
    const items = wrapper.findAll('.stub-timeline-item')
    expect(items.length).toBe(1)
    expect(items[0].attributes('data-color')).toBe('var(--pch-accent)')
  })

  it('skipped 事件红色 + reason + 尝试量', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    mocks.getMyReportEvents.mockResolvedValueOnce([
      {
        recorded_at: '2026-07-28T11:00:00Z',
        sheet_id: 42,
        sheet_title: '中央塔楼',
        registry_id: 'minecraft:stone',
        action: 'skipped',
        reason: '已达材料上限',
        net_delta: 3,
      },
    ])
    const wrapper = mountMe()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('被拒：已达材料上限')
    expect(text).toContain('尝试 3')
    // skipped 颜色 = redstone 红
    const items = wrapper.findAll('.stub-timeline-item')
    expect(items.length).toBe(1)
    expect(items[0].attributes('data-color')).toBe('var(--pch-redstone)')
  })

  it('未归因事件（sheet_id=null）显示「未归因」回退文案', async () => {
    mocks.getMyConstruction.mockResolvedValueOnce(EMPTY_CONSTRUCTION)
    mocks.getMyReportEvents.mockResolvedValueOnce([
      {
        recorded_at: '2026-07-28T12:00:00Z',
        sheet_id: null,
        sheet_title: null,
        registry_id: 'minecraft:stone',
        action: 'skipped',
        reason: '当前无施工中项目',
        net_delta: 0,
      },
    ])
    const wrapper = mountMe()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('未归因')
    expect(text).toContain('被拒：当前无施工中项目')
    // net_delta=0 → 不显「尝试 N」
    expect(text).not.toContain('尝试')
  })
})
