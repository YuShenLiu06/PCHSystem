import { describe, expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import ScoreLedgerTable, { REASON_LABEL } from '../ScoreLedgerTable.vue'
import type { ScoreLedgerEntry, ScoreReason } from '../../../api/scoring'
import { ElTableColumnStub, ElTableStub } from './elTableStubs'

// 纯展示表：加载 / 空态 / 分页由父级管，这里只测列渲染（时间本地化不赘测）。
const globalStubs = {
  ElTable: ElTableStub,
  ElTableColumn: ElTableColumnStub,
  ElTag: {
    props: ['type'],
    template: '<span class="stub-tag" :data-type="type"><slot /></span>',
  },
}

function makeEntry(overrides: Partial<ScoreLedgerEntry>): ScoreLedgerEntry {
  return {
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
    ...overrides,
  }
}

function mountTable(entries: ScoreLedgerEntry[]) {
  return mount(ScoreLedgerTable, {
    props: { entries },
    global: { stubs: globalStubs },
  })
}

describe('ScoreLedgerTable', () => {
  test('reason 渲染为中文标签（不露原始枚举）', () => {
    const wrapper = mountTable([
      makeEntry({ reason: 'collect' }),
      makeEntry({ id: 2, reason: 'manual_adj' }),
    ])
    const text = wrapper.text()
    expect(text).toContain('收集')
    expect(text).toContain('手动修正')
    expect(text).not.toContain('collect')
    expect(text).not.toContain('manual_adj')
  })

  test('备注为空显示 —，delta 正数带 + 前缀且 tag 为 success', () => {
    const wrapper = mountTable([makeEntry({ delta: '3.00', note: null })])
    const text = wrapper.text()
    expect(text).toContain('—') // 备注空回退
    expect(text).toContain('+3.00') // 正数前缀
    expect(wrapper.find('.stub-tag').attributes('data-type')).toBe('success')
  })

  test('delta 负数原样显示（不加 + 前缀）且 tag 为 danger，备注正常透出', () => {
    const wrapper = mountTable([makeEntry({ delta: '-3.00', note: '误发回收' })])
    const text = wrapper.text()
    expect(text).toContain('-3.00')
    expect(text).not.toContain('+-3.00')
    expect(text).toContain('误发回收')
    expect(wrapper.find('.stub-tag').attributes('data-type')).toBe('danger')
  })

  test('REASON_LABEL 命名导出全集（供 ScoringAdmin 选项等下游复用）', () => {
    const expected: Record<ScoreReason, string> = {
      collect: '收集',
      build_a: '建造 A',
      leader_bonus: '队长奖励',
      settle: '结算',
      manual_adj: '手动修正',
      season_reset: '赛季重置',
    }
    expect(REASON_LABEL).toEqual(expected)
  })
})
