<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  listSheets,
  createSheet,
  type SheetSummary,
  type SheetStatus,
} from '../../api/sheets'
import { usePolling } from '../../composables/usePolling'

// 列表页轮询间隔：列表只有新建项目会变化，可慢于详情页（后台/卸载自动暂停见 usePolling）
const LIST_INTERVAL_MS = 10_000

// Tab 过滤值：active=收集+施工（进行中）；archived=已归档。后端接受 status query
type ListTab = 'active' | 'archived'

// 项目阶段 → el-tag 配色 + 文案
function statusTagType(status: SheetStatus): 'info' | 'warning' | 'success' {
  if (status === 'constructing') return 'warning'
  if (status === 'archived') return 'success'
  return 'info' // collecting
}

function statusLabel(status: SheetStatus): string {
  if (status === 'constructing') return '施工中'
  if (status === 'archived') return '已归档'
  return '收集中'
}

const router = useRouter()
const sheets = ref<SheetSummary[]>([])
const loading = ref(false)
const activeTab = ref<ListTab>('active')
const createVisible = ref(false)
const newTitle = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    sheets.value = await listSheets({ status: activeTab.value })
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e) ?? '加载项目列表失败')
  } finally {
    loading.value = false
  }
}

function onTabChange(): void {
  void load()
}

async function onCreateConfirm(): Promise<void> {
  const title = newTitle.value.trim()
  if (!title) {
    ElMessage.warning('请输入标题')
    return
  }
  try {
    const created = await createSheet(title)
    createVisible.value = false
    newTitle.value = ''
    ElMessage.success('已创建')
    router.push(`/sheets/${created.id}`)
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e) ?? '创建失败')
  }
}

function openCreate(): void {
  newTitle.value = ''
  createVisible.value = true
}

function errorMessage(e: unknown): string | undefined {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail
  }
  return undefined
}

onMounted(load)
usePolling(load, { intervalMs: LIST_INTERVAL_MS })
</script>

<template>
  <el-card header="项目列表">
    <template #default>
      <div style="margin-bottom: 12px;">
        <el-button type="primary" @click="openCreate">新建项目</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
      <!-- Tab：进行中（active）/ 已归档（archived） -->
      <el-tabs v-model="activeTab" @tab-change="onTabChange" style="margin-bottom: 8px;">
        <el-tab-pane label="进行中" name="active" />
        <el-tab-pane label="已归档" name="archived" />
      </el-tabs>
      <el-table
        v-loading="loading"
        :data="sheets"
        style="cursor: pointer;"
        @row-click="(row: SheetSummary) => router.push(`/sheets/${row.id}`)"
      >
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="owner_name" label="所有者" width="160" />
        <el-table-column label="状态" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="200">
          <template #default="{ row }">
            {{ new Date(row.updated_at).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <el-dialog v-model="createVisible" title="新建项目" width="420px">
    <el-input v-model="newTitle" placeholder="请输入项目标题" maxlength="128" show-word-limit @keyup.enter="onCreateConfirm" />
    <template #footer>
      <el-button @click="createVisible = false">取消</el-button>
      <el-button type="primary" @click="onCreateConfirm">创建</el-button>
    </template>
  </el-dialog>
</template>
