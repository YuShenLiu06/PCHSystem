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
  type SheetDetail,
  type RowDetail,
} from '../../api/sheets'
import { formatQty } from '../../utils/qty'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const sheet = ref<SheetDetail | null>(null)
const loading = ref(false)
const errorMsg = ref('')

// 新增行表单
const newRow = ref({
  item_name: '',
  need_qty: 0,
  done_flag: 0,
  sort_order: 0,
})

// 编辑标题
const titleEditing = ref(false)
const titleDraft = ref('')

// 行内编辑缓冲：key=row.id，value=该行的编辑草稿
const rowDrafts = ref<Record<number, { item_name: string; need_qty: number; sort_order: number }>>({})

const sheetId = computed(() => Number(route.params.id))

const canEdit = computed(() => {
  const p = auth.player
  if (!p || !sheet.value) return false
  return sheet.value.owner_uuid === p.uuid || p.role === 'admin' || p.role === 'owner'
})

function errorMessage(e: unknown): string {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail ?? '请求失败'
  }
  return '请求失败'
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
    // 初始化行草稿
    rowDrafts.value = {}
    for (const r of data.rows) {
      rowDrafts.value[r.id] = {
        item_name: r.item_name,
        need_qty: r.need_qty,
        sort_order: r.sort_order,
      }
    }
  } catch (e: unknown) {
    errorMsg.value = errorMessage(e)
  } finally {
    loading.value = false
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
  try {
    const created = await upsertRow(sheetId.value, { ...newRow.value, item_name: itemName })
    if (sheet.value) {
      // upsert：按 item_name 已存在则更新，否则新增；简单起见重新拉取一次保证一致
      const refreshed = await fetchSheet(sheetId.value)
      sheet.value = refreshed
      rowDrafts.value[created.id] = {
        item_name: created.item_name,
        need_qty: created.need_qty,
        sort_order: created.sort_order,
      }
    }
    newRow.value = { item_name: '', need_qty: 0, done_flag: 0, sort_order: 0 }
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
      sort_order: draft.sort_order,
      done_flag: row.done_flag,
    })
    const refreshed = await fetchSheet(sheetId.value)
    sheet.value = refreshed
    ElMessage.success('已保存')
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e))
  }
}

async function onToggleDone(row: RowDetail): Promise<void> {
  const next = row.done_flag === 1 ? 0 : 1
  try {
    await upsertRow(sheetId.value, {
      item_name: row.item_name,
      need_qty: row.need_qty,
      done_flag: next,
      sort_order: row.sort_order,
    })
    const refreshed = await fetchSheet(sheetId.value)
    sheet.value = refreshed
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
        <span style="color: #888; font-size: 12px;">所有者：{{ sheet.owner_uuid }}</span>
        <el-button v-if="canEdit" type="danger" plain @click="onDeleteSheet">删除表格</el-button>
      </div>
      <div v-else>表格详情</div>
    </template>

    <el-result v-if="errorMsg && !sheet" icon="error" title="加载失败" :sub-title="errorMsg" />

    <template v-else-if="sheet">
      <!-- 新增行（仅可编辑者可见） -->
      <div v-if="canEdit" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <el-input v-model="newRow.item_name" placeholder="物品名" style="width: 200px;" maxlength="128" />
        <el-input-number v-model="newRow.need_qty" :min="0" controls-position="right" style="width: 130px;" />
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
        <el-table-column label="需要数量" width="140">
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
        <el-table-column label="换算" width="120">
          <template #default="{ row }">
            {{ formatQty(row.need_qty) }}
          </template>
        </el-table-column>
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
        <el-table-column label="备齐" width="120" align="center">
          <template #default="{ row }">
            <el-tag
              v-if="!canEdit"
              :type="row.done_flag === 1 ? 'success' : 'info'"
              size="small"
            >
              {{ row.done_flag === 1 ? '已备齐' : '未备齐' }}
            </el-tag>
            <el-button
              v-else
              :type="row.done_flag === 1 ? 'success' : 'info'"
              size="small"
              plain
              @click="onToggleDone(row)"
            >
              {{ row.done_flag === 1 ? '已备齐' : '未备齐' }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column v-if="canEdit" label="操作" width="180" align="center">
          <template #default="{ row }">
            <el-button type="primary" size="small" @click="onSaveRow(row)">保存</el-button>
            <el-button type="danger" size="small" @click="onDeleteRow(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>
</template>
