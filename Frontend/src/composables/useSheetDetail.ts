// TODO: 行逻辑增长后拆 useSheetRows（评审 finding 7：单 composable ~550 行可接受，不预先拆）

import { computed, onMounted, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getSheet,
  patchSheet,
  upsertRow,
  deleteRow,
  claimRow,
  setRowDelivery,
  contributeRow,
  setRowProgress,
  releaseRow,
  rejectRow,
  advanceSheet,
  grantSheetManager,
  revokeSheetManager,
  searchPlayers,
  type SheetDetail,
  type RowDetail,
  type PlayerBrief,
} from '../api/sheets'
import { useAuthStore } from '../stores/auth'
import { useSheetStore } from '../stores/sheet'
import { usePolling } from './usePolling'
import {
  MODE_LOCK,
  MODE_PROGRESS,
  type NewSubRowDraft,
  type RowDraft,
  type TreeNode,
  buildTreeRows,
  draftFromRow,
  findParentMode,
  isSubRow,
  newSubRowDraft,
  rowEqual,
} from '../views/sheets/sheetHelpers'
import { extractApiError } from '../utils/error'

// 详情页轮询间隔：认领/交付有 2~3s 延迟可接受，3s 兼顾实时与稳态压力（后台/卸载自动暂停见 usePolling）
const DETAIL_INTERVAL_MS = 3_000

export interface UseSheetDetailOptions {
  /** 当前项目 id（响应式）：silentRefresh / param-watch 每次读当前值 */
  sheetId: Readonly<Ref<number>>
  /** 鉴权 store（shell 注入，避免 composable 直连 Pinia） */
  auth: ReturnType<typeof useAuthStore>
  /** 表格模板 ref（shell 拥有，作依赖传入——composable 需控制展开） */
  sheetTableRef: Ref<any>
}

export interface UseSheetDetailHandle {
  // 状态
  sheet: Ref<SheetDetail | null>
  loading: Ref<boolean>
  errorMsg: Ref<string>
  newRow: Ref<{ item_name: string; registry_id: string; need_qty: number; mode: number; sort_order: number }>
  newSubRow: Ref<Record<number, NewSubRowDraft>>
  rowDrafts: Ref<Record<number, RowDraft>>
  editingRowId: Ref<number | null>
  titleEditing: Ref<boolean>
  titleDraft: Ref<string>
  subRowPopoverVisible: Ref<Record<number, boolean>>
  // 派生
  canEdit: ComputedRef<boolean>
  canManage: ComputedRef<boolean>
  isManager: ComputedRef<boolean>
  isReadOnly: ComputedRef<boolean>
  treeRows: ComputedRef<TreeNode[]>
  // 行/身份判定（依赖 sheet/auth）
  isClaimant: (row: RowDetail) => boolean
  parentMode: (row: RowDetail) => number | undefined
  canClaimRow: (row: RowDetail) => boolean
  canReleaseRow: (row: RowDetail) => boolean
  // sheet 局部错误助手（shell 的 onDeleteSheet 复用，仍在 sheet feature 内——非跨 feature 泄漏）
  sheetErrorMessage: (e: unknown) => string
  // handlers
  onAdvance: (to: 'constructing' | 'archived') => Promise<void>
  onSaveTitle: () => Promise<void>
  onAddRow: () => Promise<void>
  onStartEdit: (row: RowDetail) => void
  onCancelEdit: (row: RowDetail) => void
  onSaveRow: (row: RowDetail) => Promise<void>
  onDeleteRow: (row: RowDetail) => Promise<void>
  onAddSubRow: (parentRow: RowDetail) => Promise<void>
  onSaveSubRow: (subRow: RowDetail) => Promise<void>
  onDeleteSubRow: (subRow: RowDetail) => Promise<void>
  onClaim: (row: RowDetail) => Promise<void>
  onSetDelivery: (row: RowDetail) => Promise<void>
  onContribute: (row: RowDetail) => Promise<void>
  onAdjustProgress: (row: RowDetail) => Promise<void>
  onRelease: (row: RowDetail) => Promise<void>
  onReject: (row: RowDetail) => Promise<void>
  onSubRowPopoverShow: (parentRow: RowDetail) => void
  // 协管员（manager，迁移 0014，account 锚定）
  managerInputName: Ref<string>
  managerPickedUuid: Ref<string>
  searchPlayers: (q: string) => Promise<PlayerBrief[]>
  onGrantManager: () => Promise<void>
  onRevokeManager: (webAccountId: number) => Promise<void>
}

/**
 * Sheet 详情页状态 + 编排 composable。
 *
 * 拥有：sheet 数据 / 加载态 / 行草稿 / 编辑态 / 标题草稿 / 子物品表单 / 协作 handler。
 * 不拥有：路由跳转（onDeleteSheet 删后跳走留 shell）、归档预览 UI（SheetArchiveDialog 子组件）、
 *         列宽拖拽态（纯渲染态留 shell）。
 *
 * 身份保留：写操作 handler 与轮询统一走 applyRefreshedSheet，未变行复用原对象引用
 *（rowEqual 短路）→ el-table row-key=id keyed diff 跳过重渲染。
 */
export function useSheetDetail(opts: UseSheetDetailOptions): UseSheetDetailHandle {
  const { sheetId, auth, sheetTableRef } = opts
  // 详情客户端缓存（SWR）：命中立显 + 后台 revalidate；写操作/轮询统一经 applyRefreshedSheet 刷新
  const sheetStore = useSheetStore()

  const sheet = ref<SheetDetail | null>(null)
  const loading = ref(false)
  const errorMsg = ref('')

  // 新增行表单（mode 默认 lock=0）；registry_id 可空——填了才支持游戏内一键提交匹配
  const newRow = ref({ item_name: '', registry_id: '', need_qty: 0, mode: MODE_LOCK, sort_order: 0 })

  // 新增子物品表单（临时存储，每行独立）
  const newSubRow = ref<Record<number, NewSubRowDraft>>({})

  // 行内编辑缓冲（仅 owner 可编辑）：key=row.id
  const rowDrafts = ref<Record<number, RowDraft>>({})

  // 当前正在编辑的行 id；null = 浏览态（所有行显 span，首渲只建少量组件）。
  // 单值锁：同一时刻最多一行切到 input 态（方案 A 惰性行编辑）。
  const editingRowId = ref<number | null>(null)

  // 编辑标题
  const titleEditing = ref(false)
  const titleDraft = ref('')

  // 添加子物品 popover 受控开关：onAddSubRow 成功后显式关闭
  // （trigger=click + applyRefreshedSheet 身份保留合并后，无全表重渲染副作用来附带关闭 popover）
  const subRowPopoverVisible = ref<Record<number, boolean>>({})

  // 拥有者或全局 admin/owner 角色——tier A 高危写（改名/删表/归档/授予撤销协管员）。
  // R-9：仅控可见性，真实拒绝在后端 403。
  const canManage = computed(() => {
    const p = auth.player
    if (!p || !sheet.value) return false
    // R-5 account 级：表的 owner_uuid 在我的 account UUID 集合里 → 同 account 任一 UUID 建的表都可编辑。
    // 注意方向：viewer_uuids 是「我的」集，判断 owner 在不在此集（不是「我」在不在集——后者恒真）
    return sheet.value.viewer_uuids.includes(sheet.value.owner_uuid) || p.role === 'admin' || p.role === 'owner'
  })

  // 当前查看者是否为本表协管员（account 级判定，DRY 单一入口）：
  // managers[].member_uuids 与 viewer_uuids 任一交集即视为 manager（同账号下任一 UUID 继承）。
  // viewer_uuids 由后端按 viewer 的 account 解析返回，已是权威集（= auth store 绑定 UUIDs + 当前 UUID）。
  const isManager = computed(() => {
    if (!sheet.value) return false
    const viewerUuids = sheet.value.viewer_uuids
    if (viewerUuids.length === 0) return false
    return (sheet.value.managers ?? []).some((m) =>
      m.member_uuids.some((u) => viewerUuids.includes(u)),
    )
  })

  // tier B 常规写（增删改行/子物品/进度/解除/打回/进入施工）——owner、超管，或本表协管员（迁移 0014，account 锚定）。
  // 现 canEdit 语义升级为 tier B（原 tier A 语义移至 canManage）；现有 v-if="canEdit" 自动继承。
  const canEdit = computed(() => canManage.value || isManager.value)

  // 协管员授予输入：玩家名联想（el-autocomplete 远程搜索）+ 选中后存 uuid。
  // 必须从联想下拉选中（保证 uuid 有效）；纯文本未选中 → 警告。
  const managerInputName = ref('')
  const managerPickedUuid = ref('')

  // 已归档 = 只读终态：隐藏所有写操作。R-9：仅可见性，真实拒绝在后端 409
  const isReadOnly = computed(() => sheet.value?.status === 'archived')

  const treeRows = computed<TreeNode[]>(() => {
    if (!sheet.value) return []
    return buildTreeRows(sheet.value.rows)
  })

  // 当前玩家是否为该行的认领人
  function isClaimant(row: RowDetail): boolean {
    // R-5 account 级：同 account 任一 UUID 认领的行都算自己（可 deliver/release/reject）
    const uuids = sheet.value?.viewer_uuids ?? []
    return !!row.claimant_uuid && uuids.includes(row.claimant_uuid)
  }

  // 获取父行模式（子行专用）
  function parentMode(row: RowDetail): number | undefined {
    return findParentMode(row, sheet.value?.rows ?? [])
  }

  // 子行认领条件：仅当父行=progress 时可单独认领
  function canClaimRow(row: RowDetail): boolean {
    if (row.mode !== MODE_LOCK || row.status !== 'open' || !auth.player) return false
    if (isSubRow(row)) return parentMode(row) === MODE_PROGRESS
    return true
  }

  // 子行解除条件：仅当父行=progress 时可单独解除
  function canReleaseRow(row: RowDetail): boolean {
    if (row.mode !== MODE_LOCK) return false
    if (isSubRow(row)) return parentMode(row) === MODE_PROGRESS
    return true
  }

  function is409(e: unknown): boolean {
    return (
      typeof e === 'object' &&
      e !== null &&
      'response' in e &&
      (e as { response?: { status?: number } }).response?.status === 409
    )
  }

  // sheet 局部错误文案：409（已归档/非法转移）给专用提示，其余取后端 detail 或通用兜底。
  // 401 不在此处理（utils/http.ts 拦截器统一，RS-5）。
  function sheetErrorMessage(e: unknown): string {
    const d = extractApiError(e)
    if (is409(e)) return d ?? '项目已归档，只读'
    return d ?? '请求失败'
  }

  // 详情页只取 JSON（不取 CSV），缩小类型为 SheetDetail
  async function fetchSheet(id: number): Promise<SheetDetail> {
    return (await getSheet(id)) as SheetDetail
  }

  async function load(): Promise<void> {
    // SWR：命中缓存则立即渲染（loading 不亮）+ 后台 silentRefresh revalidate。
    // 二次进入同表秒开，规避每进必 fetch 的往返（"后面都快"）。
    const cached = sheetStore.details[sheetId.value]
    if (cached) {
      errorMsg.value = ''
      applyRefreshedSheet(cached)
      titleDraft.value = cached.title
      void silentRefresh()
      return
    }
    loading.value = true
    errorMsg.value = ''
    try {
      const data = await fetchSheet(sheetId.value)
      sheet.value = data
      titleDraft.value = data.title
      // 初始化行草稿 + 顶层行预初始化「添加子物品」表单
      rowDrafts.value = {}
      newSubRow.value = {}
      for (const r of data.rows) {
        rowDrafts.value[r.id] = draftFromRow(r)
        // 顶层行预初始化「添加子物品」表单对象——popover 内容随表格 scoped slot 预渲染，
        // 若 newSubRow[row.id] 缺失，模板访问 .registry_id 会抛 TypeError 中断整表渲染。
        // 模式继承该行当前 mode（owner 改父行 mode 后下次 load/轮询会重建）。
        if (r.parent_row_id === null) {
          newSubRow.value[r.id] = newSubRowDraft(r.mode)
        }
      }
      sheetStore.setDetail(data) // 回填缓存
    } catch (e: unknown) {
      errorMsg.value = sheetErrorMessage(e)
    } finally {
      loading.value = false
    }
  }

  // 身份保留合并：把刷新后的 SheetDetail 并入当前 sheet.value。
  // 1) 复用「未变行」的原对象引用（rowEqual 短路）—— el-table row-key=id keyed diff 命中同引用
  //    → 跳过该行重渲染，避免整表每秒 tear down（176 行 × ~2000 组件卡顿）。
  // 2) 为新增行补初始化草稿（rowDrafts / newSubRow），不覆盖用户正在编辑的已有草稿。
  // 3) 清理已消失行（他端删除 / 本端级联）的残留草稿与子物品表单。
  // 写操作 handler 与轮询统一走本函数，避免 fetchSheet 全量替换绕过身份保留。
  function applyRefreshedSheet(refreshed: SheetDetail): void {
    for (const r of refreshed.rows) {
      if (!rowDrafts.value[r.id]) {
        rowDrafts.value[r.id] = draftFromRow(r)
      }
      if (r.parent_row_id === null && !newSubRow.value[r.id]) {
        newSubRow.value[r.id] = newSubRowDraft(r.mode)
      }
    }
    const refreshedIds = new Set(refreshed.rows.map((r) => r.id))
    for (const id of Object.keys(rowDrafts.value).map(Number)) {
      if (!refreshedIds.has(id)) {
        if (editingRowId.value === id) editingRowId.value = null
        delete rowDrafts.value[id]
        delete newSubRow.value[id]
      }
    }
    const prevById = new Map(sheet.value ? sheet.value.rows.map((r) => [r.id, r]) : [])
    sheet.value = {
      ...refreshed,
      rows: refreshed.rows.map((r) => {
        const prev = prevById.get(r.id)
        return prev && rowEqual(prev, r) ? prev : r
      }),
    }
    sheetStore.setDetail(sheet.value) // SWR：写操作 + 轮询统一刷新缓存，无需额外失效
  }

  // 同步结构字段到草稿（父行保存后级联子行 mode 等后端变更，避免草稿过期）
  function syncStructuralDrafts(rows: RowDetail[], ids: number[]): void {
    const byId = new Map(rows.map((r) => [r.id, r]))
    for (const id of ids) {
      const r = byId.get(id)
      const d = rowDrafts.value[id]
      if (r && d) {
        d.mode = r.mode
        d.need_qty = r.need_qty
        d.qty_per_unit = r.qty_per_unit
        d.parent_row_id = r.parent_row_id
        // 保留 item_name / registry_id / sort_order（用户文本草稿）
      }
    }
  }

  // 静默刷新（轮询专用）：只换 sheet.value 展示数据（状态/认领人/交付进度），
  // 不动 rowDrafts / titleDraft / loading / errorMsg —— 避免覆盖拥有者正在编辑的草稿。
  // 失败直接抛出，交由 usePolling 走 onError + 退避。
  async function silentRefresh(): Promise<void> {
    if (!sheet.value) return // 首载尚未完成则不抢跑
    const data = await fetchSheet(sheetId.value)
    applyRefreshedSheet(data)
  }

  // 协作 handler 公共骨架：api → applyRefreshedSheet(fetchSheet) → 成功 toast / 失败 error toast。
  // prompt + cancel/close 守卫留调用方（onContribute / onAdjustProgress）。
  async function runRowAction(apiCall: () => Promise<unknown>, successMsg: string): Promise<void> {
    try {
      await apiCall()
      applyRefreshedSheet(await fetchSheet(sheetId.value))
      ElMessage.success(successMsg)
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  async function onSaveTitle(): Promise<void> {
    const title = titleDraft.value.trim()
    if (!title) {
      ElMessage.warning('标题不能为空')
      return
    }
    try {
      const updated = await patchSheet(sheetId.value, title)
      applyRefreshedSheet(updated)
      titleEditing.value = false
      ElMessage.success('标题已更新')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  async function onAddRow(): Promise<void> {
    const itemName = newRow.value.item_name.trim()
    if (!itemName) {
      ElMessage.warning('请输入物品名')
      return
    }
    const regId = newRow.value.registry_id.trim()
    try {
      // registry_id 留空则不传（后端落 null，不参与一键匹配）；非空才透传
      const created = await upsertRow(sheetId.value, {
        item_name: itemName,
        need_qty: newRow.value.need_qty,
        mode: newRow.value.mode,
        sort_order: newRow.value.sort_order,
        ...(regId ? { registry_id: regId } : {}),
      })
      if (sheet.value) {
        // 新建行（issue #20：同名已存在 → 后端 409，不再覆盖）；重新拉取一次保证一致
        const refreshed = await fetchSheet(sheetId.value)
        applyRefreshedSheet(refreshed)
        // applyRefreshedSheet 已为新增行补草稿，此处用 created 显式覆盖确保字段精确
        rowDrafts.value[created.id] = draftFromRow(created)
      }
      newRow.value = { item_name: '', registry_id: '', need_qty: 0, mode: MODE_LOCK, sort_order: 0 }
      ElMessage.success('已添加')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // === 行内编辑态切换（方案 A 惰性行编辑）===
  // 进入/取消编辑前按 server 当前值重建草稿——轮询「已有草稿保留不动」会让草稿与
  // row（span 显示值）漂移；这里同步保证编辑态初值 = 浏览态 span 显示值，无 stale 闪烁。
  function resetDraftFromRow(row: RowDetail): void {
    rowDrafts.value[row.id] = draftFromRow(row)
  }

  function onStartEdit(row: RowDetail): void {
    resetDraftFromRow(row)
    editingRowId.value = row.id
  }

  function onCancelEdit(row: RowDetail): void {
    resetDraftFromRow(row)
    editingRowId.value = null
  }

  async function onSaveRow(row: RowDetail): Promise<void> {
    const draft = rowDrafts.value[row.id]
    if (!draft) return
    const itemName = draft.item_name.trim()
    if (!itemName) {
      ElMessage.warning('物品名不能为空')
      return
    }
    const regId = draft.registry_id.trim()
    try {
      // 带 row_id → 后端按主键更新（可改名，不再新建重复行，issue #20）；
      // registry_id 留空则不传（后端 None=不覆盖已有值）
      await upsertRow(sheetId.value, {
        row_id: row.id,
        item_name: itemName,
        need_qty: draft.need_qty,
        mode: draft.mode,
        sort_order: draft.sort_order,
        ...(regId ? { registry_id: regId } : {}),
      })
      const refreshed = await fetchSheet(sheetId.value)
      applyRefreshedSheet(refreshed)
      const childIds = refreshed.rows.filter((r) => r.parent_row_id === row.id).map((r) => r.id)
      syncStructuralDrafts(refreshed.rows, [row.id, ...childIds])
      editingRowId.value = null
      ElMessage.success('已保存')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  async function onDeleteRow(row: RowDetail): Promise<void> {
    try {
      await deleteRow(sheetId.value, row.id)
      if (sheet.value) {
        // 乐观更新：后端 FK ON DELETE CASCADE 已删子行，本地同步滤掉本行 + 其直接子行
        // （单层模型下子行无更深子行），避免轮询窗口内残留子行对象。
        const removedIds = new Set<number>([row.id])
        const remaining = sheet.value.rows.filter((r) => {
          if (r.id === row.id) return false
          if (r.parent_row_id === row.id) {
            removedIds.add(r.id)
            return false
          }
          return true
        })
        sheet.value = { ...sheet.value, rows: remaining }
        // 清理已删行的草稿 / 「添加子物品」表单（含子行条目）
        for (const id of removedIds) {
          if (editingRowId.value === id) editingRowId.value = null
          delete rowDrafts.value[id]
          delete newSubRow.value[id]
        }
        sheetStore.setDetail(sheet.value) // 乐观删除同步缓存
      }
      ElMessage.success('已删除')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // === 子物品操作 ===

  // 新增子物品
  async function onAddSubRow(parentRow: RowDetail): Promise<void> {
    const parentId = parentRow.id
    const form = newSubRow.value[parentId]
    if (!form) return

    const regId = form.registry_id.trim()
    if (!regId) {
      ElMessage.warning('请输入子物品注册名（如 minecraft:stick）')
      return
    }
    if (form.qty_per_unit <= 0) {
      ElMessage.warning('倍数必须 > 0')
      return
    }
    const itemName = form.item_name.trim()

    try {
      // 新建子行：parent_row_id + registry_id + qty_per_unit（必须）
      // need_qty 由后端派生 = ceil(qty_per_unit × 父行.need_qty)；
      // item_name 可选——填了后端拼「父名-item_name」，没填按 registry_id 翻译再拼父名前缀。
      const created = await upsertRow(sheetId.value, {
        parent_row_id: parentId,
        registry_id: regId,
        qty_per_unit: form.qty_per_unit,
        mode: form.mode,
        sort_order: form.sort_order,
        ...(itemName ? { item_name: itemName } : {}),
      })

      if (sheet.value) {
        const refreshed = await fetchSheet(sheetId.value)
        applyRefreshedSheet(refreshed)
        // 初始化新子行草稿（applyRefreshedSheet 已补，此处用 created 显式覆盖确保字段精确）
        rowDrafts.value[created.id] = draftFromRow(created)
      }

      // 重置表单：显式 lock（有意忽略父行 mode，与原始默认一致——避免父=progress 时残留 progress）
      newSubRow.value[parentId] = newSubRowDraft(MODE_LOCK)
      // 关闭 popover（成功后）：trigger=click 不会自动关，显式置 false
      subRowPopoverVisible.value[parentId] = false
      ElMessage.success('已添加子物品')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // 保存子物品（编辑 item_name / qty_per_unit 等）
  async function onSaveSubRow(subRow: RowDetail): Promise<void> {
    const draft = rowDrafts.value[subRow.id]
    if (!draft) return

    if (draft.qty_per_unit === null || draft.qty_per_unit <= 0) {
      ElMessage.warning('倍数必须 > 0')
      return
    }
    const itemName = draft.item_name.trim()
    if (!itemName) {
      ElMessage.warning('物品名不能为空')
      return
    }
    const regId = draft.registry_id.trim()
    try {
      // 更新子物品：传 row_id + item_name（改名）+ qty_per_unit（重算 need）；
      // item_name 为当前完整名（含父名前缀），后端 update 路径尊重传入值、不重拼。
      await upsertRow(sheetId.value, {
        row_id: subRow.id,
        item_name: itemName,
        qty_per_unit: draft.qty_per_unit,
        mode: draft.mode,
        sort_order: draft.sort_order,
        ...(regId ? { registry_id: regId } : {}),
      })
      const refreshed = await fetchSheet(sheetId.value)
      applyRefreshedSheet(refreshed)
      syncStructuralDrafts(refreshed.rows, [subRow.id])
      editingRowId.value = null
      ElMessage.success('已保存子物品')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // 删除子物品（复用 onDeleteRow）
  async function onDeleteSubRow(subRow: RowDetail): Promise<void> {
    await onDeleteRow(subRow)
  }

  // === 协作操作（认领/交付/贡献等，子行复用） ===

  // 任意登录玩家认领（open→claimed）
  function onClaim(row: RowDetail): Promise<void> {
    return runRowAction(() => claimRow(sheetId.value, row.id), '已认领')
  }

  // lock 认领人：一次性标备齐（delivered_qty = need → done）
  // progress 行不再走这里——任意玩家通过 onContribute 上交材料
  function onSetDelivery(row: RowDetail): Promise<void> {
    return runRowAction(() => setRowDelivery(sheetId.value, row.id, row.need_qty), '已标记备齐')
  }

  // progress 行：任意登录玩家上交材料（累加 delivered_qty，自动汇总到 contributors）
  async function onContribute(row: RowDetail): Promise<void> {
    try {
      const { value } = await ElMessageBox.prompt('请输入本次上交数量', '上交材料', {
        confirmButtonText: '上交',
        cancelButtonText: '取消',
        inputPlaceholder: `还需 ${Math.max(row.need_qty - row.delivered_qty, 0)}`,
        inputValidator: (input: string) => {
          const n = Number(input)
          if (!Number.isFinite(n) || n < 1 || !Number.isInteger(n)) return '请输入 >=1 的整数'
          return true
        },
      })
      const qty = Number(value)
      await runRowAction(() => contributeRow(sheetId.value, row.id, qty), '已上交材料')
    } catch (e: unknown) {
      // 用户取消 prompt 抛出 'cancel'/'close' 字符串，不算错误；API 错误已由 runRowAction 提示
      if (e === 'cancel' || e === 'close') return
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // progress 行：拥有者直接调整进度（绝对值，可增可减；不动贡献者名单，保留上交历史）
  async function onAdjustProgress(row: RowDetail): Promise<void> {
    try {
      const { value } = await ElMessageBox.prompt('请输入新的已交付数量（绝对值）', '调整进度', {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: String(row.delivered_qty),
        inputPlaceholder: `需求 ${row.need_qty}`,
        inputValidator: (input: string) => {
          const n = Number(input)
          if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) return '请输入 >=0 的整数'
          return true
        },
      })
      const deliveredQty = Number(value)
      await runRowAction(() => setRowProgress(sheetId.value, row.id, deliveredQty), '进度已调整')
    } catch (e: unknown) {
      if (e === 'cancel' || e === 'close') return
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // 认领人自放 / 拥有者解除锁定（claimed|done→open）
  function onRelease(row: RowDetail): Promise<void> {
    return runRowAction(() => releaseRow(sheetId.value, row.id), '已解除锁定')
  }

  // 认领人/拥有者打回（done→claimed，delivered 归零，认领人保留重做）
  // 合并了原认领人「取消备齐」——两者效果一致（done→claimed, delivered=0）
  function onReject(row: RowDetail): Promise<void> {
    return runRowAction(() => rejectRow(sheetId.value, row.id), '已打回')
  }

  // 阶段流转（owner/admin 触发）。to 省略时后端按状态机推进
  async function onAdvance(to: 'constructing' | 'archived'): Promise<void> {
    const isArchive = to === 'archived'
    try {
      if (isArchive) {
        await ElMessageBox.confirm(
          '将生成归档文档，项目转为只读（不可再编辑）。是否继续？',
          '归档确认',
          { type: 'warning', confirmButtonText: '归档', cancelButtonText: '取消' },
        )
      }
    } catch {
      return // 用户取消
    }
    try {
      const updated = await advanceSheet(sheetId.value, to)
      applyRefreshedSheet(updated) // 整体替换，含新 status / archived_path / archived_at
      ElMessage.success(isArchive ? '已归档' : '已进入施工阶段')
    } catch (e: unknown) {
      // 409 已归档 / 非法转移：给出友好提示
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  // Popover 打开时展开父行
  function onSubRowPopoverShow(parentRow: RowDetail): void {
    sheetTableRef.value?.toggleRowExpansion(parentRow, true)
    // 初始化该父行的新增子物品表单
    if (!newSubRow.value[parentRow.id]) {
      newSubRow.value[parentRow.id] = newSubRowDraft(parentRow.mode)
    }
  }

  // 协管员管理（迁移 0014）—— grant/revoke 返回刷新后的 managers 列表，直接并入 sheet.value
  // 授予：必须从联想下拉选中玩家（保证 uuid 有效），纯文本未选中 → 警告
  async function onGrantManager(): Promise<void> {
    if (!managerPickedUuid.value) {
      ElMessage.warning('请从下拉列表选择玩家')
      return
    }
    try {
      const managers = await grantSheetManager(sheetId.value, managerPickedUuid.value)
      if (sheet.value) sheet.value = { ...sheet.value, managers }
      managerInputName.value = ''
      managerPickedUuid.value = ''
      ElMessage.success('已添加协管员')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  async function onRevokeManager(webAccountId: number): Promise<void> {
    try {
      const managers = await revokeSheetManager(sheetId.value, webAccountId)
      if (sheet.value) sheet.value = { ...sheet.value, managers }
      ElMessage.success('已移除协管员')
    } catch (e: unknown) {
      ElMessage.error(sheetErrorMessage(e))
    }
  }

  onMounted(load)
  // RouterView 无 :key，param-only 变更不重挂载组件；watch 守卫未来跨表导航（当前无此入口，潜伏 gap）
  watch(sheetId, () => {
    void load()
  }, { immediate: false })
  usePolling(silentRefresh, { intervalMs: DETAIL_INTERVAL_MS })

  return {
    sheet,
    loading,
    errorMsg,
    newRow,
    newSubRow,
    rowDrafts,
    editingRowId,
    titleEditing,
    titleDraft,
    subRowPopoverVisible,
    canEdit,
    canManage,
    isManager,
    isReadOnly,
    treeRows,
    isClaimant,
    parentMode,
    canClaimRow,
    canReleaseRow,
    sheetErrorMessage,
    onAdvance,
    onSaveTitle,
    onAddRow,
    onStartEdit,
    onCancelEdit,
    onSaveRow,
    onDeleteRow,
    onAddSubRow,
    onSaveSubRow,
    onDeleteSubRow,
    onClaim,
    onSetDelivery,
    onContribute,
    onAdjustProgress,
    onRelease,
    onReject,
    onSubRowPopoverShow,
    managerInputName,
    managerPickedUuid,
    searchPlayers,
    onGrantManager,
    onRevokeManager,
  }
}
