import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// mock api（不真发请求；组件还解构 LIMIT_OPTIONS，一并给出）
vi.mock('../../../api/scoring', () => ({
  fetchBalances: vi.fn(),
  fetchLedger: vi.fn(),
  LIMIT_OPTIONS: [20, 50, 100, 200],
}))

// mock element-plus：notifyErr 走 ElMessage.error
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))

import { ElMessage } from 'element-plus'
import { fetchBalances, fetchLedger } from '../../../api/scoring'
import ScoringBalances from '../ScoringBalances.vue'
import { ElTableColumnStub, ElTableStub } from './elTableStubs'

const mockedBalances = fetchBalances as unknown as ReturnType<typeof vi.fn>
const mockedLedger = fetchLedger as unknown as ReturnType<typeof vi.fn>

const BALANCES_PAGE = {
  items: [
    {
      account_id: 7,
      display_name: '爱丽丝',
      player_names: ['alice'],
      balance: '130.00',
      entries_count: 2,
      last_entry_at: '2026-08-19T10:00:00Z',
    },
    {
      account_id: 9,
      display_name: 'Bob',
      player_names: ['bob'],
      balance: '-5.00',
      entries_count: 1,
      last_entry_at: null,
    },
  ],
  total: 2,
  page: 1,
  limit: 50,
}

const LEDGER_PAGE = {
  items: [
    {
      id: 1,
      account_id: 7,
      delta: '3.00',
      reason: 'collect',
      balance_after: '13.00',
      sheet_id: null,
      operator_uuid: null,
      idempotency_key: null,
      note: null,
      created_at: '2026-08-19T10:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  limit: 50,
}

const globalStubs = {
  ElTable: ElTableStub,
  ElTableColumn: ElTableColumnStub,
  ElTag: {
    props: ['type'],
    template: '<span class="stub-tag" :data-type="type"><slot /></span>',
  },
  ElDrawer: {
    props: ['modelValue', 'size'],
    template:
      '<div class="stub-drawer" :data-open="modelValue"><slot name="header" /><slot /></div>',
  },
  ElSkeleton: { template: '<div class="stub-skeleton" />' },
  ElButton: { template: '<button><slot /></button>' },
  ElSelect: { template: '<div class="stub-select"><slot /></div>' },
  ElOption: { template: '<div class="stub-option" />' },
  ElPagination: { template: '<div class="stub-pagination" />' },
}

function mountBalances() {
  return mount(ScoringBalances, { global: { stubs: globalStubs } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ScoringBalances · 行点击下钻抽屉', () => {
  it('mount 调 fetchBalances 并渲染余额行', async () => {
    mockedBalances.mockResolvedValueOnce(BALANCES_PAGE)
    const wrapper = mountBalances()
    await flushPromises()
    expect(mockedBalances).toHaveBeenCalledWith({ page: 1, limit: 50 })
    expect(wrapper.findAll('.stub-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('爱丽丝')
  })

  it('点击行打开抽屉并按 account_id 查该账号流水（page 重置 1）', async () => {
    mockedBalances.mockResolvedValueOnce(BALANCES_PAGE)
    mockedLedger.mockResolvedValueOnce(LEDGER_PAGE)
    const wrapper = mountBalances()
    await flushPromises()
    await wrapper.findAll('.stub-row')[0].trigger('click')
    await flushPromises()
    expect(mockedLedger).toHaveBeenCalledWith({ account_id: 7, page: 1, limit: 50 })
    const drawer = wrapper.find('.stub-drawer')
    expect(drawer.attributes('data-open')).toBe('true')
    // 标题 = 显示名 + 余额；正文 = ScoreLedgerTable 渲染的流水（reason 中文标签）
    expect(drawer.text()).toContain('爱丽丝')
    expect(drawer.text()).toContain('130.00')
    expect(drawer.text()).toContain('收集')
  })

  it('换行重开即重载（新 account_id 重新查询，page 归 1）', async () => {
    mockedBalances.mockResolvedValueOnce(BALANCES_PAGE)
    mockedLedger.mockResolvedValue(LEDGER_PAGE)
    const wrapper = mountBalances()
    await flushPromises()
    await wrapper.findAll('.stub-row')[0].trigger('click')
    await flushPromises()
    await wrapper.findAll('.stub-row')[1].trigger('click')
    await flushPromises()
    expect(mockedLedger).toHaveBeenCalledTimes(2)
    expect(mockedLedger).toHaveBeenLastCalledWith({ account_id: 9, page: 1, limit: 50 })
  })

  it('负余额行标题 tag 为 danger（同主表样式）', async () => {
    mockedBalances.mockResolvedValueOnce(BALANCES_PAGE)
    mockedLedger.mockResolvedValueOnce(LEDGER_PAGE)
    const wrapper = mountBalances()
    await flushPromises()
    await wrapper.findAll('.stub-row')[1].trigger('click')
    await flushPromises()
    const drawer = wrapper.find('.stub-drawer')
    expect(drawer.find('.stub-tag').attributes('data-type')).toBe('danger')
  })

  it('下钻加载失败 → ElMessage.error + ErrorState，重试重查', async () => {
    mockedBalances.mockResolvedValueOnce(BALANCES_PAGE)
    mockedLedger.mockRejectedValueOnce(new Error('boom'))
    mockedLedger.mockResolvedValueOnce(LEDGER_PAGE)
    const wrapper = mountBalances()
    await flushPromises()
    await wrapper.findAll('.stub-row')[0].trigger('click')
    await flushPromises()
    const drawer = wrapper.find('.stub-drawer')
    expect(drawer.text()).toContain('加载玩家流水失败')
    expect(ElMessage.error).toHaveBeenCalledWith('加载玩家流水失败')
    // ErrorState 的重试按钮 → 重新 loadDrawer
    await drawer.find('button').trigger('click')
    await flushPromises()
    expect(mockedLedger).toHaveBeenCalledTimes(2)
    expect(drawer.text()).toContain('收集')
  })
})
