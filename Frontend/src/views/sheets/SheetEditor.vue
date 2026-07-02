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
  releaseRow,
  rejectRow,
  type SheetDetail,
  type RowDetail,
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

// 新增行表单（mode 默认 lock=0）
const newRow = ref({
  item_name: '',
  need_qty: 0,
  mode: MODE_LOCK,
  sort_order: 0,
})

// 编辑标题
const titleEditing = ref(false)
const titleDraft = ref('')

// 行内编辑缓冲（仅 owner 可编辑）：key=row.id
// 含 mode —— 拥有者可下拉切换 lock/progress
const rowDrafts = ref<
  Record<number, { item_name: string; need_qty: number; mode: number; sort_order: number }>
>({})

const sheetId = computed(() => Number(route.params.id))

// 拥有者（或 admin/owner 角色）——可改清单（item/need/mode/sort）、删行、解除锁定、打回
const canEdit = computed(() => {
  const p = auth.player
  if (!p || !sheet.value) return false
  return sheet.value.owner_uuid === p.uuid || p.role === 'admin' || p.role === 'owner'
})

// 当前玩家是否为该行的认领人
function isClaimant(row: RowDetail): boolean {
  const p = auth.player
  return !!p && !!row.claimant_uuid && p.uuid === row.claimant_uuid
}

function errorMessage(e: unknown): string {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
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
    // 初始化行草稿（含 mode）
    rowDrafts.value = {}
    for (const r of data.rows) {
      rowDrafts.value[r.id] = {
        item_name: r.item_name,
        need_qty: r.need_qty,
        mode: r.mode,
        sort_order: r.sort_order,
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
  sheet.value = await fetchSheet(sheetId.value)
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
  try {
    const created = await upsertRow(sheetId.value, { ...newRow.value, item_name: itemName })
    if (sheet.value) {
      // upsert：按 item_name 已存在则更新，否则新增；简单起见重新拉取一次保证一致
      const refreshed = await fetchSheet(sheetId.value)
      sheet.value = refreshed
      rowDrafts.value[created.id] = {
        item_name: created.item_name,
        need_qty: created.need_qty,
        mode: created.mode,
        sort_order: created.sort_order,
      }
    }
    newRow.value = { item_name: '', need_qty: 0, mode: MODE_LOCK, sort_order: 0 }
    ElMessage.success('已添加/更新')
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
  try {
    await upsertRow(sheetId.value, {
      item_name: itemName,
      need_qty: draft.need_qty,
      mode: draft.mode,
      sort_order: draft.sort_order,
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

// 认领人上报交付/标备齐：
// - lock 模式：直接 PATCH delivered_qty = need（一次性标备齐 → done）
// - progress 模式：ElMessageBox.prompt 输入本次交付量，累加后上报
async function onSetDelivery(row: RowDetail): Promise<void> {
  if (row.mode === MODE_LOCK) {
    try {
      await setRowDelivery(sheetId.value, row.id, row.need_qty)
      sheet.value = await fetchSheet(sheetId.value)
      ElMessage.success('已标记备齐')
    } catch (e: unknown) {
      ElMessage.error(errorMessage(e))
    }
    return
  }
  // progress 模式
  try {
    const { value } = await ElMessageBox.prompt('请输入本次上报交付数量', '上报交付', {
      confirmButtonText: '上报',
      cancelButtonText: '取消',
      inputPlaceholder: `还需 ${Math.max(row.need_qty - row.delivered_qty, 0)}`,
      inputValidator: (input: string) => {
        const n = Number(input)
        if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) return '请输入非负整数'
        return true
      },
    })
    const delta = Number(value)
    const next = Math.min(row.delivered_qty + delta, row.need_qty)
    await setRowDelivery(sheetId.value, row.id, next)
    sheet.value = await fetchSheet(sheetId.value)
    ElMessage.success('已上报交付')
  } catch (e: unknown) {
    // 用户取消 prompt 抛出 'cancel' 字符串，不算错误
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
    await ElMessageBox.confirm(`确认删除表格「${sheet.value?.title ?? ''}」？此操作不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteSheet(sheetId.value)
    ElMessage.success('表格已删除')
    router.push('/sheets')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

function back(): void {
  router.push('/sheets')
}

onMounted(load)
usePolling(silentRefresh, { intervalMs: DETAIL_INTERVAL_MS })
</script>

<template>
  <el-card v-loading="loading">
    <template #header>
      <div v-if="sheet" style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
        <el-button link @click="back">← 返回列表</el-button>
        <span v-if="!titleEditing" style="font-weight: 600; font-size: 16px;">{{ sheet.title }}</span>
        <el-input
          v-else
          v-model="titleDraft"
          style="width: 280px;"
          maxlength="128"
          @keyup.enter="onSaveTitle"
        />
        <el-button v-if="canEdit && !titleEditing" link type="primary" @click="titleEditing = true">改标题</el-button>
        <template v-if="canEdit && titleEditing">
          <el-button type="primary" size="small" @click="onSaveTitle">保存</el-button>
          <el-button size="small" @click="() => { titleEditing = false; titleDraft = sheet!.title }">取消</el-button>
        </template>
        <span style="flex: 1;" />
        <span style="color: #888; font-size: 12px;">所有者：{{ sheet.owner_name }}</span>
        <el-button v-if="canEdit" type="danger" plain @click="onDeleteSheet">删除表格</el-button>
      </div>
      <div v-else>表格详情</div>
    </template>

    <el-result v-if="errorMsg && !sheet" icon="error" title="加载失败" :sub-title="errorMsg" />

    <template v-else-if="sheet">
      <!-- 新增行（仅拥有者可见） -->
      <div v-if="canEdit" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <el-input v-model="newRow.item_name" placeholder="物品名" style="width: 200px;" maxlength="128" />
        <el-input-number v-model="newRow.need_qty" :min="0" controls-position="right" style="width: 130px;" />
        <el-select v-model="newRow.mode" style="width: 120px;">
          <el-option :value="0" label="锁定" />
          <el-option :value="1" label="进度" />
        </el-select>
        <el-input-number v-model="newRow.sort_order" :min="0" controls-position="right" style="width: 120px;" />
        <el-button type="primary" @click="onAddRow">添加/upsert</el-button>
      </div>

      <el-table :data="sheet.rows" border>
        <el-table-column label="物品名" min-width="180">
          <template #default="{ row }">
            <el-input
              v-if="canEdit && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].item_name"
              maxlength="128"
            />
            <span v-else>{{ row.item_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="需要数量" width="120">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].need_qty"
              :min="0"
              controls-position="right"
            />
            <span v-else>{{ row.need_qty }}</span>
          </template>
        </el-table-column>
        <el-table-column label="换算" width="100">
          <template #default="{ row }">
            {{ formatQty(row.need_qty) }}
          </template>
        </el-table-column>
        <!-- 模式列：拥有者可下拉切换 lock/progress -->
        <el-table-column label="模式" width="110">
          <template #default="{ row }">
            <el-select
              v-if="canEdit && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].mode"
              size="small"
            >
              <el-option :value="0" label="锁定" />
              <el-option :value="1" label="进度" />
            </el-select>
            <span v-else>{{ row.mode === 1 ? '进度' : '锁定' }}</span>
          </template>
        </el-table-column>
        <!-- 认领者列：open 显「—」 -->
        <el-table-column label="认领者" width="120">
          <template #default="{ row }">
            <span v-if="row.claimant_name">{{ row.claimant_name }}</span>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>
        <!-- 状态列：el-tag，open 灰/claimed 蓝/done 绿 -->
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status as 'open' | 'claimed' | 'done')" size="small">
              {{ statusLabel(row.status as 'open' | 'claimed' | 'done') }}
            </el-tag>
          </template>
        </el-table-column>
        <!-- 交付进度列：仅 progress 模式显 -->
        <el-table-column v-if="sheet.rows.some((r) => r.mode === MODE_PROGRESS)" label="交付进度" width="160">
          <template #default="{ row }">
            <template v-if="row.mode === MODE_PROGRESS">
              <span style="font-size: 12px;">{{ row.delivered_qty }}/{{ row.need_qty }}</span>
              <el-progress
                :percentage="row.need_qty > 0 ? Math.min(Math.round((row.delivered_qty / row.need_qty) * 100), 100) : 0"
                :stroke-width="10"
                :show-text="false"
                style="margin-top: 2px;"
              />
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>
        <!-- 排序列：拥有者可编辑 -->
        <el-table-column label="排序" width="120">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && rowDrafts[row.id]"
              v-model="rowDrafts[row.id].sort_order"
              :min="0"
              controls-position="right"
            />
            <span v-else>{{ row.sort_order }}</span>
          </template>
        </el-table-column>
        <!-- 动作列：按 角色×状态 条件渲染 -->
        <el-table-column label="操作" width="240" align="center">
          <template #default="{ row }">
            <!-- 拥有者：行内编辑保存/删除 -->
            <template v-if="canEdit">
              <el-button type="primary" size="small" @click="onSaveRow(row)">保存</el-button>
              <el-button type="danger" size="small" @click="onDeleteRow(row)">删除</el-button>
            </template>
            <!-- 任意玩家 × open → 认领 -->
            <el-button v-if="row.status === 'open' && auth.player" size="small" type="primary" @click="onClaim(row)">认领</el-button>
            <!-- 认领人 × claimed → 标备齐/上报交付 + 放弃 -->
            <template v-if="isClaimant(row) && row.status === 'claimed'">
              <el-button size="small" type="success" @click="onSetDelivery(row)">
                {{ row.mode === MODE_PROGRESS ? '上报交付' : '标备齐' }}
              </el-button>
              <el-button v-if="!canEdit" size="small" @click="onRelease(row)">放弃</el-button>
            </template>
            <!-- 拥有者 × claimed|done → 解除锁定 -->
            <el-button v-if="canEdit && (row.status === 'claimed' || row.status === 'done')" size="small" plain @click="onRelease(row)">解除锁定</el-button>
            <!-- 认领人|拥有者 × done → 打回（合并原「取消备齐」，done→claimed, delivered 归零） -->
            <el-button v-if="(isClaimant(row) || canEdit) && row.status === 'done'" size="small" type="warning" plain @click="onReject(row)">打回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>
</template>
