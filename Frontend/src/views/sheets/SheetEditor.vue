<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
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
const newSubRow = ref<Record<number, { registry_id: string; qty_per_unit: number; mode: number; sort_order: number }>>({})

// 子物品展开状态（记录哪些父行的子行表格已展开）
const subRowsExpanded = ref<Set<number>>(new Set())

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
  }
  sheet.value = data
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

// 切换子行展开/折叠
function toggleSubRows(parentRowId: number): void {
  if (subRowsExpanded.value.has(parentRowId)) {
    subRowsExpanded.value.delete(parentRowId)
  } else {
    subRowsExpanded.value.add(parentRowId)
  }
  // 触发响应式更新
  subRowsExpanded.value = new Set(subRowsExpanded.value)
}

// 判断子行是否展开
function isSubRowsExpanded(parentRowId: number): boolean {
  return subRowsExpanded.value.has(parentRowId)
}

// 初始化子物品表单（展开时）
function initSubRowForm(parentRowId: number): void {
  if (!newSubRow.value[parentRowId]) {
    newSubRow.value[parentRowId] = {
      registry_id: '',
      qty_per_unit: 1,
      mode: MODE_LOCK,
      sort_order: 0,
    }
  }
}

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
  if (form.qty_per_unit < 1) {
    ElMessage.warning('每件数量必须 >= 1')
    return
  }

  try {
    // 新建子行：parent_row_id + registry_id + qty_per_unit（必须）
    // need_qty 由后端派生 = qty_per_unit × 父行.need_qty
    const created = await upsertRow(sheetId.value, {
      parent_row_id: parentId,
      registry_id: regId,
      qty_per_unit: form.qty_per_unit,
      mode: form.mode,
      sort_order: form.sort_order,
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

// 保存子物品（编辑 qty_per_unit 等）
async function onSaveSubRow(subRow: RowDetail): Promise<void> {
  const draft = rowDrafts.value[subRow.id]
  if (!draft) return

  const regId = draft.registry_id.trim()
  try {
    // 更新子物品：传 row_id + qty_per_unit（可改每件数量）
    await upsertRow(sheetId.value, {
      row_id: subRow.id,
      qty_per_unit: draft.qty_per_unit ?? 1,
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

// === 树状渲染 computed ===

// 按父行分组：Map<parent_row_id | null, RowDetail[]>
const rowsByParent = computed(() => {
  if (!sheet.value) return new Map<number | null, RowDetail[]>()
  const map = new Map<number | null, RowDetail[]>()
  for (const row of sheet.value.rows) {
    const key = row.parent_row_id
    if (!map.has(key)) {
      map.set(key, [])
    }
    map.get(key)!.push(row)
  }
  return map
})

// 顶层行列表（parent_row_id 为 null）
const topRows = computed(() => {
  return rowsByParent.value.get(null) ?? []
})

// 获取某行的子行列表
function getSubRows(parentRowId: number): RowDetail[] {
  return rowsByParent.value.get(parentRowId) ?? []
}

// 判断是否为子行
function isSubRow(row: RowDetail): boolean {
  return row.parent_row_id !== null
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
        <el-input-number v-model="newRow.need_qty" :min="0" controls-position="right" style="width: 130px;" />
        <el-select v-model="newRow.mode" style="width: 120px;">
          <el-option :value="0" label="锁定" />
          <el-option :value="1" label="进度" />
        </el-select>
        <el-input-number v-model="newRow.sort_order" :min="0" controls-position="right" style="width: 120px;" />
        <el-button type="primary" @click="onAddRow">添加</el-button>
      </div>

      <!-- 树状表格：顶层行 + 嵌套子行 -->
      <el-table :data="topRows" border>
        <el-table-column label="物品名" min-width="180">
          <template #default="{ row }">
            <!-- 顶层行：物品名 -->
            <template v-if="!isSubRow(row)">
              <el-input
                v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
                v-model="rowDrafts[row.id].item_name"
                maxlength="128"
              />
              <span v-else>{{ row.item_name }}</span>
            </template>
            <!-- 子行：缩进 + 物品名 -->
            <template v-else>
              <span style="padding-left: 24px;">└ {{ row.item_name }}</span>
            </template>
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
            <template v-if="isSubRow(row) && row.qty_per_unit">
              <!-- 子行：显示「每件 ×N」+ 总量 -->
              <span style="font-size: 12px; color: #666;">每件 ×{{ row.qty_per_unit }}</span>
              <br />
              <span>{{ row.need_qty }}</span>
            </template>
            <template v-else>
              <!-- 顶层行：可编辑 need_qty -->
              <el-input-number
                v-if="canEdit && !isReadOnly && rowDrafts[row.id] && !isSubRow(row)"
                v-model="rowDrafts[row.id].need_qty"
                :min="0"
                controls-position="right"
                size="small"
              />
              <span v-else>{{ row.need_qty }}</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="换算" width="80">
          <template #default="{ row }">
            {{ formatQty(row.need_qty) }}
          </template>
        </el-table-column>

        <!-- 模式列：顶层行可切换；子行继承父行模式（父 lock 强制子 lock） -->
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
              controls-position="right"
              size="small"
            />
            <span v-else>{{ row.sort_order }}</span>
          </template>
        </el-table-column>

        <!-- 动作列：紧凑按钮 + tooltip -->
        <el-table-column label="操作" width="200" align="center">
          <template #default="{ row }">
            <template v-if="!isReadOnly">
              <!-- 顶层行操作 -->
              <template v-if="!isSubRow(row)">
                <!-- 拥有者：保存/删除/展开子行 -->
                <template v-if="canEdit">
                  <el-tooltip content="保存" placement="top">
                    <el-button type="primary" size="small" icon="Check" circle @click="onSaveRow(row)" />
                  </el-tooltip>
                  <el-tooltip content="删除" placement="top">
                    <el-button type="danger" size="small" icon="Delete" circle @click="onDeleteRow(row)" />
                  </el-tooltip>
                  <el-tooltip content="子物品" placement="top">
                    <el-button
                      :type="isSubRowsExpanded(row.id) ? 'primary' : 'default'"
                      size="small"
                      @click="() => { toggleSubRows(row.id); initSubRowForm(row.id) }"
                    >
                      {{ isSubRowsExpanded(row.id) ? '收起' : `子物品${getSubRows(row.id).length}` }}
                    </el-button>
                  </el-tooltip>
                </template>
                <!-- 玩家协作按钮 -->
                <el-tooltip v-if="row.mode === MODE_LOCK && row.status === 'open' && auth.player" content="认领" placement="top">
                  <el-button type="primary" size="small" icon="User" circle @click="onClaim(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_PROGRESS && row.status !== 'done' && auth.player" content="上交材料" placement="top">
                  <el-button type="primary" size="small" icon="Upload" circle @click="onContribute(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed'" content="标备齐" placement="top">
                  <el-button type="success" size="small" icon="Check" circle @click="onSetDelivery(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed' && !canEdit" content="放弃" placement="top">
                  <el-button size="small" icon="Close" circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_LOCK && (row.status === 'claimed' || row.status === 'done')" content="解除锁定" placement="top">
                  <el-button size="small" icon="Unlock" plain circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_PROGRESS" content="调整进度" placement="top">
                  <el-button type="warning" size="small" icon="Edit" plain circle @click="onAdjustProgress(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && (isClaimant(row) || canEdit) && row.status === 'done'" content="打回" placement="top">
                  <el-button type="warning" size="small" icon="RefreshLeft" plain circle @click="onReject(row)" />
                </el-tooltip>
              </template>

              <!-- 子行操作 -->
              <template v-else>
                <!-- 拥有者：保存/删除子行 -->
                <template v-if="canEdit">
                  <el-tooltip content="保存" placement="top">
                    <el-button type="primary" size="small" icon="Check" circle @click="onSaveSubRow(row)" />
                  </el-tooltip>
                  <el-tooltip content="删除" placement="top">
                    <el-button type="danger" size="small" icon="Delete" circle @click="onDeleteSubRow(row)" />
                  </el-tooltip>
                </template>
                <!-- 子行协作按钮（复用 claim/delivery/contribute） -->
                <el-tooltip v-if="row.mode === MODE_LOCK && row.status === 'open' && auth.player" content="认领" placement="top">
                  <el-button type="primary" size="small" icon="User" circle @click="onClaim(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_PROGRESS && row.status !== 'done' && auth.player" content="上交材料" placement="top">
                  <el-button type="primary" size="small" icon="Upload" circle @click="onContribute(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed'" content="标备齐" placement="top">
                  <el-button type="success" size="small" icon="Check" circle @click="onSetDelivery(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed' && !canEdit" content="放弃" placement="top">
                  <el-button size="small" icon="Close" circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_LOCK && (row.status === 'claimed' || row.status === 'done')" content="解除锁定" placement="top">
                  <el-button size="small" icon="Unlock" plain circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_PROGRESS" content="调整进度" placement="top">
                  <el-button type="warning" size="small" icon="Edit" plain circle @click="onAdjustProgress(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && (isClaimant(row) || canEdit) && row.status === 'done'" content="打回" placement="top">
                  <el-button type="warning" size="small" icon="RefreshLeft" plain circle @click="onReject(row)" />
                </el-tooltip>
              </template>
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 子行嵌套表格（展开时显示） -->
      <template v-for="topRow in topRows" :key="topRow.id">
        <div
          v-if="isSubRowsExpanded(topRow.id) && canEdit && !isReadOnly"
          style="margin-left: 24px; margin-top: 8px; padding: 8px; background: #f5f7fa; border-radius: 4px;"
        >
          <!-- 新增子物品表单 -->
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
            <span style="font-size: 12px; color: #666;">新增子物品：</span>
            <el-input
              v-model="newSubRow[topRow.id].registry_id"
              placeholder="注册名（如 minecraft:stick）"
              style="width: 200px;"
              maxlength="128"
              size="small"
            />
            <el-input-number
              v-model="newSubRow[topRow.id].qty_per_unit"
              :min="1"
              controls-position="right"
              style="width: 110px;"
              size="small"
            />
            <span style="font-size: 12px; color: #888;">每件 × 总量 = {{ topRow.need_qty * (newSubRow[topRow.id].qty_per_unit || 0) }}</span>
            <el-select v-model="newSubRow[topRow.id].mode" style="width: 90px;" size="small">
              <el-option :value="0" label="锁定" />
              <el-option :value="1" label="进度" :disabled="topRow.mode === MODE_LOCK" />
            </el-select>
            <el-input-number
              v-model="newSubRow[topRow.id].sort_order"
              :min="0"
              controls-position="right"
              style="width: 100px;"
              size="small"
            />
            <el-button type="primary" size="small" @click="onAddSubRow(topRow)">添加子物品</el-button>
          </div>
        </div>

        <!-- 子行列表（内联展开） -->
        <el-table
          v-if="isSubRowsExpanded(topRow.id)"
          :data="getSubRows(topRow.id)"
          border
          style="margin-left: 24px; margin-top: 8px;"
          size="small"
        >
          <el-table-column label="物品名" min-width="140">
            <template #default="{ row }">
              <span style="padding-left: 8px;">└ {{ row.item_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="注册名" min-width="140">
            <template #default="{ row }">
              <el-input
                v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
                v-model="rowDrafts[row.id].registry_id"
                placeholder="minecraft:stick"
                maxlength="128"
                size="small"
              />
              <span v-else style="color: #999; font-size: 12px;">{{ row.registry_id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="每件 × 总量" width="100">
            <template #default="{ row }">
              <template v-if="canEdit && !isReadOnly && rowDrafts[row.id]">
                <el-input-number
                  v-model="rowDrafts[row.id].qty_per_unit"
                  :min="1"
                  controls-position="right"
                  size="small"
                  style="width: 80px;"
                />
                <span style="font-size: 12px; color: #666;">× {{ topRow.need_qty }} = {{ row.need_qty }}</span>
              </template>
              <template v-else>
                <span style="font-size: 12px; color: #666;">每件 ×{{ row.qty_per_unit }}</span>
                <br />
                <span>{{ row.need_qty }}</span>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="模式" width="70">
            <template #default="{ row }">
              <el-select
                v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
                v-model="rowDrafts[row.id].mode"
                size="small"
                :disabled="topRow.mode === MODE_LOCK"
              >
                <el-option :value="0" label="锁定" />
                <el-option :value="1" label="进度" :disabled="topRow.mode === MODE_LOCK" />
              </el-select>
              <span v-else>{{ row.mode === 1 ? '进度' : '锁定' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="认领者" width="120">
            <template #default="{ row }">
              <template v-if="row.mode === MODE_PROGRESS">
                <template v-if="row.contributors && row.contributors.length">
                  <el-tag v-for="c in row.contributors" :key="c.player_uuid" size="small" style="margin: 2px;">
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
          <el-table-column label="状态" width="70" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status as 'open' | 'claimed' | 'done')" size="small">
                {{ statusLabel(row.status as 'open' | 'claimed' | 'done') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="sheet.rows.some((r) => r.mode === MODE_PROGRESS)" label="进度" width="100">
            <template #default="{ row }">
              <template v-if="row.mode === MODE_PROGRESS">
                <span style="font-size: 12px;">{{ row.delivered_qty }}/{{ row.need_qty }}</span>
                <el-progress
                  :percentage="row.need_qty > 0 ? Math.min(Math.round((row.delivered_qty / row.need_qty) * 100), 100) : 0"
                  :stroke-width="6"
                  :show-text="false"
                  style="margin-top: 2px;"
                />
              </template>
              <span v-else style="color: #aaa;">—</span>
            </template>
          </el-table-column>
          <el-table-column label="排序" width="80">
            <template #default="{ row }">
              <el-input-number
                v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
                v-model="rowDrafts[row.id].sort_order"
                :min="0"
                controls-position="right"
                size="small"
                style="width: 70px;"
              />
              <span v-else>{{ row.sort_order }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" align="center">
            <template #default="{ row }">
              <template v-if="!isReadOnly">
                <template v-if="canEdit">
                  <el-tooltip content="保存" placement="top">
                    <el-button type="primary" size="small" icon="Check" circle @click="onSaveSubRow(row)" />
                  </el-tooltip>
                  <el-tooltip content="删除" placement="top">
                    <el-button type="danger" size="small" icon="Delete" circle @click="onDeleteSubRow(row)" />
                  </el-tooltip>
                </template>
                <el-tooltip v-if="row.mode === MODE_LOCK && row.status === 'open' && auth.player" content="认领" placement="top">
                  <el-button type="primary" size="small" icon="User" circle @click="onClaim(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_PROGRESS && row.status !== 'done' && auth.player" content="上交" placement="top">
                  <el-button type="primary" size="small" icon="Upload" circle @click="onContribute(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed'" content="备齐" placement="top">
                  <el-button type="success" size="small" icon="Check" circle @click="onSetDelivery(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed' && !canEdit" content="放弃" placement="top">
                  <el-button size="small" icon="Close" circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_LOCK && (row.status === 'claimed' || row.status === 'done')" content="解锁" placement="top">
                  <el-button size="small" icon="Unlock" plain circle @click="onRelease(row)" />
                </el-tooltip>
                <el-tooltip v-if="canEdit && row.mode === MODE_PROGRESS" content="调整" placement="top">
                  <el-button type="warning" size="small" icon="Edit" plain circle @click="onAdjustProgress(row)" />
                </el-tooltip>
                <el-tooltip v-if="row.mode === MODE_LOCK && (isClaimant(row) || canEdit) && row.status === 'done'" content="打回" placement="top">
                  <el-button type="warning" size="small" icon="RefreshLeft" plain circle @click="onReject(row)" />
                </el-tooltip>
              </template>
              <span v-else style="color: #aaa;">—</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
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
