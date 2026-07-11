/**
 * 纯函数/类型/常量模块，从 SheetEditor.vue 抽出
 *
 * 本模块仅包含无状态的纯函数和类型定义，不依赖任何组件状态。
 * 可独立单测，便于在多处复用（SheetEditor / 其他组件 / 测试）。
 */

import type { RowDetail, SheetStatus } from '../../api/sheets'

// ============================================================================
// 常量
// ============================================================================

/** mode 取值：0=lock（锁定/二元备齐），1=progress（进度/跟踪 delivered_qty） */
export const MODE_LOCK = 0
export const MODE_PROGRESS = 1

// ============================================================================
// 类型
// ============================================================================

/** 行状态：open（未认领/未交付）| claimed（认领中/部分交付）| done（已备齐） */
export type RowStatus = 'open' | 'claimed' | 'done'

/**
 * 行编辑草稿（与 RowDetail 对齐）
 *
 * 用于行内编辑表单的缓冲。registry_id 改为非空（空字符串表示未填）。
 */
export interface RowDraft {
  item_name: string
  registry_id: string
  need_qty: number
  mode: number
  sort_order: number
  parent_row_id: number | null
  qty_per_unit: number | null
}

/**
 * 新增子物品表单（与 RowDraft 对齐，但不含 need_qty——由后端按 qty_per_unit 派生）
 */
export interface NewSubRowDraft {
  item_name: string
  registry_id: string
  qty_per_unit: number
  mode: number
  sort_order: number
}

/**
 * 树状节点（RowDetail + children 数组）
 *
 * 用于 el-table tree 渲染。顶层行的 children 包含其直接子行。
 */
export type TreeNode = RowDetail & { children: RowDetail[] }

// ============================================================================
// 纯函数
// ============================================================================

/**
 * 从 RowDetail 构建行草稿
 *
 * registry_id 为 null 时规范化为空字符串（表单输入框用空串表示"未填"）。
 */
export function draftFromRow(row: RowDetail): RowDraft {
  return {
    item_name: row.item_name,
    registry_id: row.registry_id ?? '',
    need_qty: row.need_qty,
    mode: row.mode,
    sort_order: row.sort_order,
    parent_row_id: row.parent_row_id,
    qty_per_unit: row.qty_per_unit,
  }
}

/**
 * 创建新增子物品表单默认值
 *
 * mode 继承父行：父行=lock 时子行只能 lock；父行=progress 时子行默认 progress（可手动切 lock）。
 */
export function newSubRowDraft(parentMode: number): NewSubRowDraft {
  return {
    item_name: '',
    registry_id: '',
    qty_per_unit: 1,
    mode: parentMode === MODE_LOCK ? MODE_LOCK : MODE_PROGRESS,
    sort_order: 0,
  }
}

/**
 * 行相等性判定（轮询身份保留用）
 *
 * 未变行复用原对象引用 → el-table row-key=id keyed diff 跳过重渲染。
 * JSON.stringify 安全：两端均出自同一 Pydantic 序列化路径，键序一致、均为 JSON 原生类型（无函数/Date 对象）。
 */
export function rowEqual(a: RowDetail, b: RowDetail): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/**
 * 构建树状行结构
 *
 * 按 parent_row_id 分组，返回顶层行（parent_row_id=null）嵌套其 children。
 * 对未变行复用 sheet.value.rows 元素引用（rowEqual 短路），el-table row-key=id 的 keyed
 * diff 命中同一行对象 → 跳过行级重渲染；包装节点每轮新建是浅对象（spread + children 数组），
 * 成本极低。展开态由 el-table store 基于 row-key 持久化，不受 :data 引用变化影响。
 */
export function buildTreeRows(rows: RowDetail[]): TreeNode[] {
  // 按父分组（每轮局部变量，纯函数无副作用）
  const byParent = new Map<number | null, RowDetail[]>()
  for (const r of rows) {
    const list = byParent.get(r.parent_row_id)
    if (list) list.push(r)
    else byParent.set(r.parent_row_id, [r])
  }
  const tops = byParent.get(null) ?? []
  return tops.map((row) => ({ ...row, children: byParent.get(row.id) ?? [] }))
}

/**
 * 查找父行的 mode
 *
 * 子行专用：返回父行的 mode，若父行不存在或 row 本身是顶层行则返回 undefined。
 */
export function findParentMode(row: RowDetail, rows: RowDetail[]): number | undefined {
  if (row.parent_row_id == null) return undefined
  return rows.find((r) => r.id === row.parent_row_id)?.mode
}

/**
 * 判断是否为子行
 */
export function isSubRow(row: RowDetail): boolean {
  return row.parent_row_id !== null
}

/**
 * 行状态 tag 配色
 */
export function statusTagType(status: RowStatus): 'info' | 'primary' | 'success' {
  if (status === 'claimed') return 'primary'
  if (status === 'done') return 'success'
  return 'info'
}

/**
 * 行状态文案
 */
export function statusLabel(status: RowStatus): string {
  if (status === 'claimed') return '认领中'
  if (status === 'done') return '已备齐'
  return '未认领'
}

/**
 * 项目阶段 tag 配色
 */
export function phaseTagType(status: SheetStatus | undefined): 'info' | 'warning' | 'success' {
  if (status === 'constructing') return 'warning'
  if (status === 'archived') return 'success'
  return 'info' // collecting / 未加载
}

/**
 * 项目阶段文案
 */
export function phaseLabel(status: SheetStatus | undefined): string {
  if (status === 'constructing') return '施工中'
  if (status === 'archived') return '已归档'
  return '收集中'
}
