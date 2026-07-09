<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElTable } from 'element-plus'
import {
  getSheet,
  patchSheet,
  deleteSheet,
  upsertRow,
  deleteRow,
  claimRow,
  setRowDelivery,
  contributeRow,
  setRowProgress,
  releaseRow,
  rejectRow,
  advanceSheet,
  getSheetArchive,
  getSheetArchiveAsset,
  type SheetDetail,
  type RowDetail,
  type SheetStatus,
} from '../../api/sheets'
import { formatQty } from '../../utils/qty'
import { useAuthStore } from '../../stores/auth'
import { usePolling } from '../../composables/usePolling'

// mode 取值：0=lock（锁定/二元备齐），1=progress（进度/跟踪 delivered_qty）
const MODE_LOCK = 0
const MODE_PROGRESS = 1

// 详情页轮询间隔：有认领/交付状态，需相对实时（后台/卸载自动暂停见 usePolling）
const DETAIL_INTERVAL_MS = 1_000

// status 取值：open / claimed / done（与后端契约对齐）
type RowStatus = 'open' | 'claimed' | 'done'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const sheet = ref<SheetDetail | null>(null)
const loading = ref(false)
const errorMsg = ref('')

// 新增行表单（mode 默认 lock=0）；registry_id 可空——填了才支持游戏内一键提交匹配
const newRow = ref({
  item_name: '',
  registry_id: '',
  need_qty: 0,
  mode: MODE_LOCK,
  sort_order: 0,
})

// 新增子物品表单（临时存储，每行独立）
const newSubRow = ref<Record<number, { item_name: string; registry_id: string; qty_per_unit: number; mode: number; sort_order: number }>>({})

// 表格 ref（用于控制展开/折叠）
const sheetTableRef = ref<any>()

// 编辑标题
const titleEditing = ref(false)
const titleDraft = ref('')

// 行内编辑缓冲（仅 owner 可编辑）：key=row.id
// 含 mode —— 拥有者可下拉切换 lock/progress
// 含 parent_row_id / qty_per_unit —— 子物品字段
const rowDrafts = ref<
  Record<
    number,
    {
      item_name: string
      registry_id: string
      need_qty: number
      mode: number
      sort_order: number
      parent_row_id: number | null
      qty_per_unit: number | null
    }
  >
>({})

const sheetId = computed(() => Number(route.params.id))

// 拥有者（或 admin/owner 角色）——可改清单（item/need/mode/sort）、删行、解除锁定、打回
const canEdit = computed(() => {
  const p = auth.player
  if (!p || !sheet.value) return false
  return sheet.value.owner_uuid === p.uuid || p.role === 'admin' || p.role === 'owner'
})

// 已归档 = 只读终态：隐藏所有写操作（行 CRUD / 流转 / 改标题 / 删除）。R-9：仅可见性，真实拒绝在后端 409
const isReadOnly = computed(() => sheet.value?.status === 'archived')

// 项目阶段 el-tag 配色 + 文案
function phaseTagType(status: SheetStatus | undefined): 'info' | 'warning' | 'success' {
  if (status === 'constructing') return 'warning'
  if (status === 'archived') return 'success'
  return 'info' // collecting / 未加载
}

function phaseLabel(status: SheetStatus | undefined): string {
  if (status === 'constructing') return '施工中'
  if (status === 'archived') return '已归档'
  return '收集中'
}

// 归档文档预览
const archiveVisible = ref(false)
const archiveLoading = ref(false)
const archiveContent = ref('')
// 贡献占比图 object URL（asset 端点需 JWT，<img> 直连发不出头，故 axios 拉 blob 再 createObjectURL）
const archiveImgUrl = ref('')
const ARCHIVE_CHART_FILENAME = 'contributions.png'

function revokeArchiveImgUrl(): void {
  if (archiveImgUrl.value) {
    URL.revokeObjectURL(archiveImgUrl.value)
    archiveImgUrl.value = ''
  }
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
    sheet.value = updated // 整体替换，含新 status / archived_path / archived_at
    ElMessage.success(isArchive ? '已归档' : '已进入施工阶段')
  } catch (e: unknown) {
    // 409 已归档 / 非法转移：给出友好提示
    const msg = errorMessage(e)
    ElMessage.error(msg)
  }
}

// 查看归档文档（仅 archived 态可用）
async function onShowArchive(): Promise<void> {
  archiveLoading.value = true
  archiveContent.value = ''
  revokeArchiveImgUrl()
  archiveVisible.value = true
  try {
    archiveContent.value = await getSheetArchive(sheetId.value)
    // 贡献占比图（无图项目 404 → 静默不显，不影响 md 预览）
    try {
      const blob = await getSheetArchiveAsset(sheetId.value, ARCHIVE_CHART_FILENAME)
      archiveImgUrl.value = URL.createObjectURL(blob)
    } catch {
      // 无贡献占比图（项目无贡献者）→ 不显图，吞掉
    }
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e) ?? '加载归档文档失败')
    archiveVisible.value = false
  } finally {
    archiveLoading.value = false
  }
}

// 归档 dialog 关闭：释放 object URL 避免内存泄漏
function onArchiveDialogClose(): void {
  revokeArchiveImgUrl()
}

// 当前玩家是否为该行的认领人
function isClaimant(row: RowDetail): boolean {
  const p = auth.player
  return !!p && !!row.claimant_uuid && p.uuid === row.claimant_uuid
}

function errorMessage(e: unknown): string {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { status?: number; data?: { detail?: string } } }).response
    // 409 = 已归档/非法转移：行操作/流转被后端拒绝
    if (resp?.status === 409) {
      return resp?.data?.detail ?? '项目已归档，只读'
    }
    return resp?.data?.detail ?? '请求失败'
  }
  return '请求失败'
}

// 状态 tag 配色（R-9：仅可见性，真实拒绝在后端 403/409）
function statusTagType(status: RowStatus): 'info' | 'primary' | 'success' {
  if (status === 'claimed') return 'primary'
  if (status === 'done') return 'success'
  return 'info'
}

function statusLabel(status: RowStatus): string {
  if (status === 'claimed') return '认领中'
  if (status === 'done') return '已备齐'
  return '未认领'
}

// 详情页只取 JSON（不取 CSV），缩小类型为 SheetDetail
async function fetchSheet(id: number): Promise<SheetDetail> {
  const data = await getSheet(id)
  return data as SheetDetail
}

async function load(): Promise<void> {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await fetchSheet(sheetId.value)
    sheet.value = data
    titleDraft.value = data.title
    // 初始化行草稿（含 mode + parent_row_id + qty_per_unit）
    rowDrafts.value = {}
    newSubRow.value = {}
    for (const r of data.rows) {
      rowDrafts.value[r.id] = {
        item_name: r.item_name,
        registry_id: r.registry_id ?? '',
        need_qty: r.need_qty,
        mode: r.mode,
        sort_order: r.sort_order,
        parent_row_id: r.parent_row_id,
        qty_per_unit: r.qty_per_unit,
      }
      // 顶层行预初始化「添加子物品」表单对象——popover 内容随表格 scoped slot 预渲染，
      // 若 newSubRow[row.id] 缺失，模板访问 .registry_id 会抛 TypeError 中断整表渲染。
      // 模式继承该行当前 mode（owner 改父行 mode 后下次 load/轮询会重建）。
      if (r.parent_row_id === null) {
        newSubRow.value[r.id] = {
          item_name: '',
          registry_id: '',
          qty_per_unit: 1,
          mode: r.mode === MODE_LOCK ? MODE_LOCK : MODE_PROGRESS,
          sort_order: 0,
        }
      }
    }
  } catch (e: unknown) {
    errorMsg.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

// 静默刷新（轮询专用）：只换 sheet.value 展示数据（状态/认领人/交付进度），
// 不动 rowDrafts / titleDraft / loading / errorMsg —— 避免覆盖拥有者正在编辑的草稿。
// 失败直接抛出，交由 usePolling 走 onError + 退避。
async function silentRefresh(): Promise<void> {
  if (!sheet.value) return // 首载尚未完成则不抢跑
  const data = await fetchSheet(sheetId.value)
  // 轮询可能拉到新增行（如 MCDR 游戏内 !!PCH sheet add 创建），需补初始化其草稿，
  // 否则模板 rowDrafts[row.id] 为 undefined → 整行回退纯文本、不可编辑。
  // 已有草稿保留不动（拥有者正在编辑的内容不被覆盖）。
  for (const r of data.rows) {
    if (!rowDrafts.value[r.id]) {
      rowDrafts.value[r.id] = {
        item_name: r.item_name,
        registry_id: r.registry_id ?? '',
        need_qty: r.need_qty,
        mode: r.mode,
        sort_order: r.sort_order,
        parent_row_id: r.parent_row_id,
        qty_per_unit: r.qty_per_unit,
      }
    }
    // 顶层行预初始化「添加子物品」表单（同 load，防 popover 预渲染抛错）；已有则保留用户输入
    if (r.parent_row_id === null && !newSubRow.value[r.id]) {
      newSubRow.value[r.id] = {
        item_name: '',
        registry_id: '',
        qty_per_unit: 1,
        mode: r.mode === MODE_LOCK ? MODE_LOCK : MODE_PROGRESS,
        sort_order: 0,
      }
    }
  }
  // 身份保留式合并：轮询拿到的 data 是全新对象，直接赋值会让 el-table 判定数据全变 →
  // 整表 tear down 重建（176 行 × ~2000 组件）造成每秒卡顿。改为复用「未变化行」的原对象引用，
  // el-table keyed diff 命中同引用 → 跳过该行重渲染。仅真正变化的行（状态/认领/交付进度等）才换新。
  const prevById = new Map(sheet.value.rows.map((r) => [r.id, r]))
  sheet.value = {
    ...data,
    rows: data.rows.map((r) => {
      const prev = prevById.get(r.id)
      return prev && rowEqual(prev, r) ? prev : r
    }),
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
    sheet.value = updated
    titleEditing.value = false
    ElMessage.success('标题已更新')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
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
      sheet.value = refreshed
      rowDrafts.value[created.id] = {
        item_name: created.item_name,
        registry_id: created.registry_id ?? '',
        need_qty: created.need_qty,
        mode: created.mode,
        sort_order: created.sort_order,
        parent_row_id: created.parent_row_id,
        qty_per_unit: created.qty_per_unit,
      }
    }
    newRow.value = { item_name: '', registry_id: '', need_qty: 0, mode: MODE_LOCK, sort_order: 0 }
    ElMessage.success('已添加')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
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
    sheet.value = refreshed
    ElMessage.success('已保存')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

async function onDeleteRow(row: RowDetail): Promise<void> {
  try {
    await deleteRow(sheetId.value, row.id)
    if (sheet.value) {
      sheet.value = {
        ...sheet.value,
        rows: sheet.value.rows.filter((r) => r.id !== row.id),
      }
      delete rowDrafts.value[row.id]
    }
    ElMessage.success('已删除')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
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
      sheet.value = refreshed
      // 初始化新子行草稿
      rowDrafts.value[created.id] = {
        item_name: created.item_name,
        registry_id: created.registry_id ?? '',
        need_qty: created.need_qty,
        mode: created.mode,
        sort_order: created.sort_order,
        parent_row_id: created.parent_row_id,
        qty_per_unit: created.qty_per_unit,
      }
    }

    // 重置表单
    newSubRow.value[parentId] = {
      item_name: '',
      registry_id: '',
      qty_per_unit: 1,
      mode: MODE_LOCK,
      sort_order: 0,
    }
    ElMessage.success('已添加子物品')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
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
    sheet.value = refreshed
    ElMessage.success('已保存子物品')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

// 删除子物品（复用 onDeleteRow）
async function onDeleteSubRow(subRow: RowDetail): Promise<void> {
  await onDeleteRow(subRow)
}

// === 协作操作（认领/交付/贡献等，子行复用） ===

// 任意登录玩家认领（open→claimed）
async function onClaim(row: RowDetail): Promise<void> {
  try {
    await claimRow(sheetId.value, row.id)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已认领')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

// lock 认领人：一次性标备齐（delivered_qty = need → done）
// progress 行不再走这里——任意玩家通过 onContribute 上交材料
async function onSetDelivery(row: RowDetail): Promise<void> {
  try {
    await setRowDelivery(sheetId.value, row.id, row.need_qty)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已标记备齐')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
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
    await contributeRow(sheetId.value, row.id, qty)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已上交材料')
  } catch (e: unknown) {
    // 用户取消 prompt 抛出 'cancel'/'close' 字符串，不算错误
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(errorMessage(e))
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
    await setRowProgress(sheetId.value, row.id, deliveredQty)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('进度已调整')
  } catch (e: unknown) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(errorMessage(e))
  }
}

// 认领人自放 / 拥有者解除锁定（claimed|done→open）
async function onRelease(row: RowDetail): Promise<void> {
  try {
    await releaseRow(sheetId.value, row.id)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已解除锁定')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

// 认领人/拥有者打回（done→claimed，delivered 归零，认领人保留重做）
// 合并了原认领人「取消备齐」——两者效果一致（done→claimed, delivered=0）
async function onReject(row: RowDetail): Promise<void> {
  try {
    await rejectRow(sheetId.value, row.id)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已打回')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

async function onDeleteSheet(): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除项目「${sheet.value?.title ?? ''}」？此操作不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteSheet(sheetId.value)
    ElMessage.success('项目已删除')
    router.push('/sheets')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

function back(): void {
  router.push('/sheets')
}

// === 树状渲染：身份保留构建 ===

// 判断是否为子行
function isSubRow(row: RowDetail): boolean {
  return row.parent_row_id !== null
}

// 轮询身份保留用：比较两行是否完全一致（全字段，含 contributors 嵌套数组）。
// 未变行复用原对象引用 → el-table keyed diff 跳过重渲染。
// JSON.stringify 安全：两端均出自同一 Pydantic 序列化路径，键序一致、均为 JSON 原生类型（无函数/Date 对象）。
function rowEqual(a: RowDetail, b: RowDetail): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

// 朴素 treeRows = top.map(r => ({...r, children})) 每秒轮询都产全新包装对象 → el-table 判定
// :data 全变 → 反复 re-normalize 树 → 展开态丢失 / 图标过渡动画被打断 / 缩进抖动。
// 解法：缓存包装节点，仅当某顶层行引用或其子行集合（按元素引用）变化时才重建该节点；
//       全部未变时复用上一轮外层数组引用。silentRefresh 已对未变行复用 sheet.value.rows
//       元素引用，故多数轮询周期 :data 引用完全不变 → el-table 跳过 re-normalize → 稳定。
//       注：行字段真变化（他人协作）时 silentRefresh 换新对象 → 命中重建，标量自然刷新，无陈旧。
type TreeNode = RowDetail & { children: RowDetail[] }
const nodeCache = new Map<number, { node: TreeNode; src: RowDetail; children: RowDetail[] }>()
let cachedTree: TreeNode[] = []

const treeRows = computed<TreeNode[]>(() => {
  if (!sheet.value) return []
  // 按父分组（每轮重建，仅供本次决策，不长期持有）
  const byParent = new Map<number | null, RowDetail[]>()
  for (const r of sheet.value.rows) {
    const list = byParent.get(r.parent_row_id)
    if (list) list.push(r)
    else byParent.set(r.parent_row_id, [r])
  }
  const tops = byParent.get(null) ?? []

  let anyChanged = tops.length !== cachedTree.length
  const next: TreeNode[] = []
  for (let i = 0; i < tops.length; i++) {
    const row = tops[i]
    const newChildren = byParent.get(row.id) ?? []
    const cached = nodeCache.get(row.id)
    // 子行集合按元素引用比较（newChildren 每轮是新数组，不能比数组引用）
    const reuse =
      !!cached &&
      cached.src === row &&
      cached.children.length === newChildren.length &&
      cached.children.every((c, idx) => c === newChildren[idx])
    if (reuse) {
      next.push(cached!.node)
    } else {
      const node: TreeNode = { ...row, children: newChildren }
      nodeCache.set(row.id, { node, src: row, children: newChildren })
      next.push(node)
      anyChanged = true
    }
  }
  // 全部未变 → 复用上一轮外层数组引用
  if (!anyChanged) return cachedTree
  cachedTree = next
  return next
})

// Popover 打开时展开父行
function onSubRowPopoverShow(parentRow: RowDetail): void {
  sheetTableRef.value?.toggleRowExpansion(parentRow, true)
  // 初始化该父行的新增子物品表单
  if (!newSubRow.value[parentRow.id]) {
    newSubRow.value[parentRow.id] = {
      item_name: '',
      registry_id: '',
      qty_per_unit: 1,
      mode: parentRow.mode === MODE_LOCK ? MODE_LOCK : MODE_PROGRESS,
      sort_order: 0,
    }
  }
}

onMounted(load)
usePolling(silentRefresh, { intervalMs: DETAIL_INTERVAL_MS })
</script>

<template>
  <el-card v-loading="loading">
    <template #header>
      <div v-if="sheet" style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
        <el-button link @click="back">← 返回项目列表</el-button>
        <!-- 阶段横幅 -->
        <el-tag :type="phaseTagType(sheet.status)" size="default" effect="plain">
          阶段：{{ phaseLabel(sheet.status) }}
        </el-tag>
        <span v-if="!titleEditing" style="font-weight: 600; font-size: 16px;">{{ sheet.title }}</span>
        <el-input
          v-else
          v-model="titleDraft"
          style="width: 280px;"
          maxlength="128"
          @keyup.enter="onSaveTitle"
        />
        <el-button v-if="canEdit && !isReadOnly && !titleEditing" link type="primary" @click="titleEditing = true">改标题</el-button>
        <template v-if="canEdit && !isReadOnly && titleEditing">
          <el-button type="primary" size="small" @click="onSaveTitle">保存</el-button>
          <el-button size="small" @click="() => { titleEditing = false; titleDraft = sheet!.title }">取消</el-button>
        </template>
        <span style="flex: 1;" />
        <!-- owner 阶段流转按钮（非 archived 态） -->
        <template v-if="canEdit && !isReadOnly">
          <el-button v-if="sheet.status === 'collecting'" size="small" type="warning" plain @click="onAdvance('constructing')">进入施工</el-button>
          <el-button v-if="sheet.status === 'collecting'" size="small" type="success" plain @click="onAdvance('archived')">直接归档</el-button>
          <el-button v-if="sheet.status === 'constructing'" size="small" type="success" plain @click="onAdvance('archived')">标记施工完成并归档</el-button>
        </template>
        <!-- 已归档：查看归档文档 -->
        <el-button v-if="isReadOnly" size="small" @click="onShowArchive">查看归档文档</el-button>
        <span style="color: #888; font-size: 12px;">所有者：{{ sheet.owner_name }}</span>
        <el-button v-if="canEdit && !isReadOnly" type="danger" plain @click="onDeleteSheet">删除项目</el-button>
      </div>
      <div v-else>项目详情</div>
    </template>

    <el-result v-if="errorMsg && !sheet" icon="error" title="加载失败" :sub-title="errorMsg" />

    <template v-else-if="sheet">
      <!-- 新增行（仅拥有者可见 + 非 archived 只读态） -->
      <div v-if="canEdit && !isReadOnly" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <el-input v-model="newRow.item_name" placeholder="物品名" style="width: 200px;" maxlength="128" />
        <el-input v-model="newRow.registry_id" placeholder="注册名（可空，如 minecraft:stone）" style="width: 240px;" maxlength="128" />
        <el-input-number v-model="newRow.need_qty" :min="0" placeholder="数量" controls-position="right" style="width: 130px;" />
        <el-select v-model="newRow.mode" style="width: 120px;">
          <el-option :value="0" label="锁定" />
          <el-option :value="1" label="进度" />
        </el-select>
        <el-input-number v-model="newRow.sort_order" :min="0" placeholder="排序" controls-position="right" style="width: 120px;" />
        <el-button type="primary" @click="onAddRow">添加</el-button>
      </div>

      <!-- 树状表格：顶层行 + 嵌套子行（el-table tree mode）。indent 加大让父子层级更直观 -->
      <el-table
        ref="sheetTableRef"
        :data="treeRows"
        border
        row-key="id"
        :indent="24"
        :tree-props="{ children: 'children' }"
      >
        <el-table-column label="物品名" min-width="180">
          <template #default="{ row }">
            <el-input
              v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].item_name"
              maxlength="128"
            />
            <span v-else>{{ row.item_name }}</span>
          </template>
        </el-table-column>

        <el-table-column label="注册名" min-width="180">
          <template #default="{ row }">
            <el-input
              v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].registry_id"
              placeholder="minecraft:stone"
              maxlength="128"
              size="small"
            />
            <span v-else-if="row.registry_id" style="color: #999; font-size: 12px;">{{ row.registry_id }}</span>
            <span v-else style="color: #ccc;">—</span>
          </template>
        </el-table-column>

        <el-table-column label="需要数量" width="100">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && !isReadOnly && rowDrafts[row.id] && !isSubRow(row)"
              v-model="rowDrafts[row.id].need_qty"
              :min="0"
              placeholder="数量"
              controls-position="right"
              size="small"
            />
            <span v-else>{{ row.need_qty }}</span>
          </template>
        </el-table-column>

        <el-table-column label="倍数" width="100">
          <template #default="{ row }">
            <template v-if="isSubRow(row)">
              <el-input-number
                v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
                v-model="rowDrafts[row.id].qty_per_unit"
                :min="0"
                :step="0.1"
                :precision="2"
                controls-position="right"
                size="small"
              />
              <span v-else>{{ row.qty_per_unit }}</span>
            </template>
            <span v-else style="color: #ccc;">—</span>
          </template>
        </el-table-column>

        <el-table-column label="换算" width="80">
          <template #default="{ row }">
            {{ formatQty(row.need_qty) }}
          </template>
        </el-table-column>

        <!-- 模式列：顶层行可切换；子行继承父行模式 -->
        <el-table-column label="模式" width="80">
          <template #default="{ row }">
            <el-select
              v-if="canEdit && !isReadOnly && rowDrafts[row.id] && !isSubRow(row)"
              v-model="rowDrafts[row.id].mode"
              size="small"
            >
              <el-option :value="0" label="锁定" />
              <el-option :value="1" label="进度" />
            </el-select>
            <span v-else>{{ row.mode === 1 ? '进度' : '锁定' }}</span>
          </template>
        </el-table-column>

        <!-- 认领者/贡献者列：lock 显单人 claimant_name；progress 显 contributors 多人 tag -->
        <el-table-column label="认领者" width="140">
          <template #default="{ row }">
            <template v-if="row.mode === MODE_PROGRESS">
              <template v-if="row.contributors && row.contributors.length">
                <el-tag
                  v-for="c in row.contributors"
                  :key="c.player_uuid"
                  size="small"
                  style="margin: 2px;"
                >
                  {{ c.player_name }}
                </el-tag>
              </template>
              <span v-else style="color: #aaa;">—</span>
            </template>
            <template v-else>
              <span v-if="row.claimant_name">{{ row.claimant_name }}</span>
              <span v-else style="color: #aaa;">—</span>
            </template>
          </template>
        </el-table-column>

        <!-- 状态列：el-tag，open 灰/claimed 蓝/done 绿 -->
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status as 'open' | 'claimed' | 'done')" size="small">
              {{ statusLabel(row.status as 'open' | 'claimed' | 'done') }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 交付进度列：仅 progress 模式显 -->
        <el-table-column v-if="sheet.rows.some((r) => r.mode === MODE_PROGRESS)" label="交付进度" width="120">
          <template #default="{ row }">
            <template v-if="row.mode === MODE_PROGRESS">
              <span style="font-size: 12px;">{{ row.delivered_qty }}/{{ row.need_qty }}</span>
              <el-progress
                :percentage="row.need_qty > 0 ? Math.min(Math.round((row.delivered_qty / row.need_qty) * 100), 100) : 0"
                :stroke-width="8"
                :show-text="false"
                style="margin-top: 2px;"
              />
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>

        <!-- 排序列：拥有者可编辑 -->
        <el-table-column label="排序" width="90">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].sort_order"
              :min="0"
              placeholder="排序"
              controls-position="right"
              size="small"
            />
            <span v-else>{{ row.sort_order }}</span>
          </template>
        </el-table-column>

        <!-- 统一操作列：文字按钮 -->
        <el-table-column label="操作" width="320" align="center">
          <template #default="{ row }">
            <template v-if="!isReadOnly">
              <!-- 拥有者操作 -->
              <template v-if="canEdit">
                <el-button size="small" type="primary" @click="isSubRow(row) ? onSaveSubRow(row) : onSaveRow(row)">保存</el-button>
                <el-button size="small" type="danger" @click="isSubRow(row) ? onDeleteSubRow(row) : onDeleteRow(row)">删除</el-button>
                <!-- 父行：添加子物品按钮（Popover） -->
                <el-popover
                  v-if="!isSubRow(row)"
                  placement="right"
                  width="400"
                  trigger="click"
                  @show="() => onSubRowPopoverShow(row)"
                >
                  <template #reference>
                    <el-button size="small">添加子物品</el-button>
                  </template>
                  <!-- v-if 守卫：popover 内容随表格预渲染，newSubRow[row.id] 缺失时跳过整块，
                       避免下方 v-model="newSubRow[row.id].X" 访问 undefined 抛错（数据层已预初始化，此处为防御兜底） -->
                  <div v-if="newSubRow[row.id]" style="display: flex; flex-direction: column; gap: 8px;">
                    <div style="font-weight: 600;">新增子物品</div>
                    <el-input
                      v-model="newSubRow[row.id].item_name"
                      placeholder="物品名（可空，留空按注册名翻译；存储为「父名-本名」）"
                      maxlength="128"
                      size="small"
                    />
                    <el-input
                      v-model="newSubRow[row.id].registry_id"
                      placeholder="注册名（如 minecraft:stick）"
                      maxlength="128"
                      size="small"
                    />
                    <div style="display: flex; gap: 8px; align-items: center;">
                      <span style="font-size: 12px; color: #666;">倍数：</span>
                      <el-input-number
                        v-model="newSubRow[row.id].qty_per_unit"
                        :min="0"
                        :step="0.1"
                        :precision="2"
                        controls-position="right"
                        size="small"
                        style="width: 110px;"
                      />
                      <span style="font-size: 12px; color: #888;">× {{ row.need_qty }} = {{ Math.ceil(row.need_qty * (newSubRow[row.id]?.qty_per_unit || 0)) }}</span>
                    </div>
                    <el-select v-model="newSubRow[row.id].mode" size="small">
                      <el-option :value="0" label="锁定" />
                      <el-option :value="1" label="进度" :disabled="row.mode === MODE_LOCK" />
                    </el-select>
                    <div style="display: flex; gap: 8px; align-items: center;">
                      <span style="font-size: 12px; color: #666;">排序：</span>
                      <el-input-number
                        v-model="newSubRow[row.id].sort_order"
                        :min="0"
                        controls-position="right"
                        size="small"
                        style="width: 100px;"
                      />
                    </div>
                    <el-button type="primary" size="small" @click="onAddSubRow(row)">确认添加</el-button>
                  </div>
                </el-popover>
              </template>

              <!-- 玩家协作按钮 -->
              <el-button v-if="row.mode === MODE_LOCK && row.status === 'open' && auth.player" size="small" type="primary" @click="onClaim(row)">认领</el-button>
              <el-button v-if="row.mode === MODE_PROGRESS && row.status !== 'done' && auth.player" size="small" type="primary" @click="onContribute(row)">上交材料</el-button>
              <el-button v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed'" size="small" type="success" @click="onSetDelivery(row)">备齐</el-button>
              <el-button v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed' && !canEdit" size="small" @click="onRelease(row)">放弃</el-button>
              <el-button v-if="canEdit && row.mode === MODE_LOCK && (row.status === 'claimed' || row.status === 'done')" size="small" @click="onRelease(row)">解除锁定</el-button>
              <el-button v-if="canEdit && row.mode === MODE_PROGRESS" size="small" type="warning" @click="onAdjustProgress(row)">调整进度</el-button>
              <el-button v-if="row.mode === MODE_LOCK && (isClaimant(row) || canEdit) && row.status === 'done'" size="small" type="warning" @click="onReject(row)">打回</el-button>
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <!-- 归档文档预览（text/markdown，保留白空格 + 等宽字体）+ 贡献占比图 -->
  <el-dialog v-model="archiveVisible" title="归档文档" width="80%" top="5vh" @close="onArchiveDialogClose">
    <div v-loading="archiveLoading">
      <pre style="white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; max-height: 50vh; overflow: auto; margin: 0;">{{ archiveContent }}</pre>
      <img v-if="archiveImgUrl" :src="archiveImgUrl" alt="贡献占比" style="max-width: 100%; margin-top: 12px;" />
    </div>
    <template #footer>
      <el-button @click="archiveVisible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
