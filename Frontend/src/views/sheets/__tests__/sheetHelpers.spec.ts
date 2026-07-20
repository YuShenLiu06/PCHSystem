import { describe, expect, it } from 'vitest'
import type { RowDetail } from '../../../api/sheets'
import {
  draftFromRow,
  findParentMode,
  isSubRow,
  MODE_LOCK,
  MODE_PROGRESS,
  newSubRowDraft,
  phaseLabel,
  phaseTagType,
  rowEqual,
  statusLabel,
  statusTagType,
  buildTreeRows,
} from '../sheetHelpers'

describe('sheetHelpers', () => {
  // ============================================================================
  // rowEqual
  // ============================================================================
  describe('rowEqual', () => {
    // 这是 C+A 轮询身份保留优化的不变量（未变行复用引用，跳过重渲染）
    const baseRow: RowDetail = {
      id: 1,
      item_name: '石头',
      registry_id: 'minecraft:stone',
      need_qty: 64,
      mode: 0,
      status: 'open',
      claimant_uuid: null,
      claimant_name: null,
      delivered_qty: 0,
      contributors: [{ account_id: null, display_name: 'Steve', member_uuids: ['uuid1'], contributed_qty: 1 }],
      sort_order: 0,
      parent_row_id: null,
      qty_per_unit: null,
      updated_at: '2026-07-11T00:00:00Z',
    }

    it('完全相同的行（含 contributors）→ true', () => {
      expect(rowEqual(baseRow, { ...baseRow })).toBe(true)
    })

    it('标量字段不同 → false', () => {
      const other = { ...baseRow, need_qty: 128 }
      expect(rowEqual(baseRow, other)).toBe(false)
    })

    it('contributors 嵌套数组条目不同 → false', () => {
      const other = {
        ...baseRow,
        contributors: [{ account_id: null, display_name: 'Alex', member_uuids: ['uuid2'], contributed_qty: 1 }],
      }
      expect(rowEqual(baseRow, other)).toBe(false)
    })

    it('contributors 为空数组 vs 有条目 → false', () => {
      const other = { ...baseRow, contributors: [] }
      expect(rowEqual(baseRow, other)).toBe(false)
    })
  })

  // ============================================================================
  // draftFromRow
  // ============================================================================
  describe('draftFromRow', () => {
    it('映射所有 7 个字段', () => {
      const row: RowDetail = {
        id: 1,
        item_name: '石头',
        registry_id: 'minecraft:stone',
        need_qty: 64,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 5,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const draft = draftFromRow(row)
      expect(draft).toEqual({
        item_name: '石头',
        registry_id: 'minecraft:stone',
        need_qty: 64,
        mode: 0,
        sort_order: 5,
        parent_row_id: null,
        qty_per_unit: null,
      })
    })

    it('registry_id: null 规范化为空字符串', () => {
      const row: RowDetail = {
        id: 1,
        item_name: '旧物品',
        registry_id: null,
        need_qty: 10,
        mode: 1,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      expect(draftFromRow(row).registry_id).toBe('')
    })

    it('透传 parent_row_id 和 qty_per_unit（含 null）', () => {
      const row: RowDetail = {
        id: 2,
        item_name: '子物品',
        registry_id: 'minecraft:stick',
        need_qty: 192,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 1,
        qty_per_unit: 3,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const draft = draftFromRow(row)
      expect(draft.parent_row_id).toBe(1)
      expect(draft.qty_per_unit).toBe(3)
    })
  })

  // ============================================================================
  // buildTreeRows
  // ============================================================================
  describe('buildTreeRows', () => {
    it('构建父子层级（父行 + 2 子行）', () => {
      const parent: RowDetail = {
        id: 1,
        item_name: '木板',
        registry_id: null,
        need_qty: 192,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const child1: RowDetail = {
        id: 2,
        item_name: '木板-棍子',
        registry_id: 'minecraft:stick',
        need_qty: 192,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 1,
        qty_per_unit: 1,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const child2: RowDetail = {
        id: 3,
        item_name: '木板-木板',
        registry_id: 'minecraft:planks',
        need_qty: 192,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 1,
        qty_per_unit: 1,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const trees = buildTreeRows([parent, child1, child2])
      expect(trees).toHaveLength(1)
      expect(trees[0].id).toBe(1)
      expect(trees[0].children).toHaveLength(2)
      expect(trees[0].children[0].id).toBe(2)
      expect(trees[0].children[1].id).toBe(3)
    })

    it('无子行的父行 → children: []', () => {
      const parent: RowDetail = {
        id: 1,
        item_name: '孤行',
        registry_id: null,
        need_qty: 10,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const trees = buildTreeRows([parent])
      expect(trees).toHaveLength(1)
      expect(trees[0].children).toEqual([])
    })

    it('顶层行（parent_row_id: null）出现在根', () => {
      const topRow: RowDetail = {
        id: 1,
        item_name: '顶层',
        registry_id: null,
        need_qty: 10,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      const trees = buildTreeRows([topRow])
      expect(trees).toHaveLength(1)
      expect(trees[0].id).toBe(1)
    })

    it('空输入 → []', () => {
      expect(buildTreeRows([])).toEqual([])
    })
  })

  // ============================================================================
  // findParentMode
  // ============================================================================
  describe('findParentMode', () => {
    const rows: RowDetail[] = [
      {
        id: 1,
        item_name: '父行',
        registry_id: null,
        need_qty: 64,
        mode: MODE_PROGRESS,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      },
      {
        id: 2,
        item_name: '子行',
        registry_id: null,
        need_qty: 192,
        mode: MODE_LOCK,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 1,
        qty_per_unit: 3,
        updated_at: '2026-07-11T00:00:00Z',
      },
    ]

    it('找到父行 → 返回父行的 mode', () => {
      const child = rows[1]
      expect(findParentMode(child, rows)).toBe(MODE_PROGRESS)
    })

    it('顶层行（parent_row_id: null）→ undefined', () => {
      const parent = rows[0]
      expect(findParentMode(parent, rows)).toBeUndefined()
    })

    it('父 id 不在 rows 中 → undefined', () => {
      const orphan: RowDetail = {
        id: 99,
        item_name: '孤儿',
        registry_id: null,
        need_qty: 10,
        mode: MODE_LOCK,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 999,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      expect(findParentMode(orphan, rows)).toBeUndefined()
    })
  })

  // ============================================================================
  // 状态/阶段相关
  // ============================================================================
  describe('statusTagType', () => {
    it('claimed → primary', () => {
      expect(statusTagType('claimed')).toBe('primary')
    })
    it('done → success', () => {
      expect(statusTagType('done')).toBe('success')
    })
    it('open → info', () => {
      expect(statusTagType('open')).toBe('info')
    })
  })

  describe('statusLabel', () => {
    it('claimed → 认领中', () => {
      expect(statusLabel('claimed')).toBe('认领中')
    })
    it('done → 已备齐', () => {
      expect(statusLabel('done')).toBe('已备齐')
    })
    it('open → 未认领', () => {
      expect(statusLabel('open')).toBe('未认领')
    })
  })

  describe('phaseTagType', () => {
    it('constructing → warning', () => {
      expect(phaseTagType('constructing')).toBe('warning')
    })
    it('archived → success', () => {
      expect(phaseTagType('archived')).toBe('success')
    })
    it('collecting → info', () => {
      expect(phaseTagType('collecting')).toBe('info')
    })
    it('undefined → info（未加载）', () => {
      expect(phaseTagType(undefined)).toBe('info')
    })
  })

  describe('phaseLabel', () => {
    it('constructing → 施工中', () => {
      expect(phaseLabel('constructing')).toBe('施工中')
    })
    it('archived → 已归档', () => {
      expect(phaseLabel('archived')).toBe('已归档')
    })
    it('collecting → 收集中', () => {
      expect(phaseLabel('collecting')).toBe('收集中')
    })
    it('undefined → 收集中（未加载默认）', () => {
      expect(phaseLabel(undefined)).toBe('收集中')
    })
  })

  // ============================================================================
  // 工具函数
  // ============================================================================
  describe('isSubRow', () => {
    it('有 parent_row_id → true', () => {
      const row: RowDetail = {
        id: 2,
        item_name: '子',
        registry_id: null,
        need_qty: 10,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: 1,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      expect(isSubRow(row)).toBe(true)
    })
    it('parent_row_id: null → false', () => {
      const row: RowDetail = {
        id: 1,
        item_name: '父',
        registry_id: null,
        need_qty: 10,
        mode: 0,
        status: 'open',
        claimant_uuid: null,
        claimant_name: null,
        delivered_qty: 0,
        contributors: [],
        sort_order: 0,
        parent_row_id: null,
        qty_per_unit: null,
        updated_at: '2026-07-11T00:00:00Z',
      }
      expect(isSubRow(row)).toBe(false)
    })
  })

  describe('newSubRowDraft', () => {
    it('父行 MODE_LOCK → 子行 mode=0', () => {
      const draft = newSubRowDraft(MODE_LOCK)
      expect(draft.mode).toBe(MODE_LOCK)
    })
    it('父行 MODE_PROGRESS → 子行 mode=1', () => {
      const draft = newSubRowDraft(MODE_PROGRESS)
      expect(draft.mode).toBe(MODE_PROGRESS)
    })
    it('默认值校验', () => {
      const draft = newSubRowDraft(MODE_LOCK)
      expect(draft).toEqual({
        item_name: '',
        registry_id: '',
        qty_per_unit: 1,
        mode: MODE_LOCK,
        sort_order: 0,
      })
    })
  })
})
