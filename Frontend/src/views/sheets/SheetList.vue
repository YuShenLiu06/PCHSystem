<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  listSheets,
  createSheet,
  type SheetSummary,
  type SheetStatus,
} from '../../api/sheets'
import { usePolling } from '../../composables/usePolling'
import { notifyOk, notifyWarn, notifyErr } from '../../composables/useNotify'
import { extractApiError } from '../../utils/error'
import PageHeader from '../../components/layout/PageHeader.vue'
import EmptyState from '../../components/feedback/EmptyState.vue'
import ErrorState from '../../components/feedback/ErrorState.vue'

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
const loadError = ref('')
// 区分首屏（显骨架）与轮询刷新（静默 v-loading），避免每 10s 闪一次骨架
const isFirstLoad = ref(true)
const activeTab = ref<ListTab>('active')
const createVisible = ref(false)
const newTitle = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    sheets.value = await listSheets({ status: activeTab.value })
    loadError.value = ''
  } catch (e: unknown) {
    loadError.value = extractApiError(e) ?? '加载项目列表失败'
    notifyErr(e, '加载项目列表失败')
  } finally {
    loading.value = false
    isFirstLoad.value = false
  }
}

function onTabChange(): void {
  void load()
}

async function onCreateConfirm(): Promise<void> {
  const title = newTitle.value.trim()
  if (!title) {
    notifyWarn('请输入项目标题')
    return
  }
  try {
    const created = await createSheet(title)
    createVisible.value = false
    newTitle.value = ''
    notifyOk('已创建')
    router.push(`/sheets/${created.id}`)
  } catch (e: unknown) {
    notifyErr(e, '创建失败')
  }
}

function openCreate(): void {
  newTitle.value = ''
  createVisible.value = true
}

onMounted(load)
usePolling(load, { intervalMs: LIST_INTERVAL_MS })
</script>

<template>
  <PageHeader title="项目">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button type="primary" @click="openCreate">新建项目</el-button>
    </template>
  </PageHeader>

  <el-card>
    <!-- Tab：进行中（active）/ 已归档（archived） -->
    <el-tabs v-model="activeTab" class="pch-list__tabs" @tab-change="onTabChange">
      <el-tab-pane label="进行中" name="active" />
      <el-tab-pane label="已归档" name="archived" />
    </el-tabs>

    <!-- 首屏骨架；轮询刷新走 v-loading 静默，不闪骨架 -->
    <el-skeleton v-if="isFirstLoad" :rows="4" animated />

    <ErrorState
      v-else-if="loadError && sheets.length === 0"
      :message="loadError"
      @retry="load"
    />

    <EmptyState
      v-else-if="sheets.length === 0"
      with-mark
      :title="activeTab === 'active' ? '还没有进行中的项目' : '还没有归档的项目'"
      :hint="
        activeTab === 'active'
          ? '新建项目后，可上传投影或蓝图生成材料清单。'
          : '项目完工归档后会出现在这里。'
      "
      :action-text="activeTab === 'active' ? '新建项目' : undefined"
      @action="openCreate"
    />

    <el-table
      v-else
      v-loading="loading"
      :data="sheets"
      class="pch-list__table"
      @row-click="(row: SheetSummary) => router.push(`/sheets/${row.id}`)"
    >
      <el-table-column prop="title" label="标题" min-width="220" />
      <el-table-column prop="owner_name" label="所有者" width="160" />
      <el-table-column label="阶段" width="110" align="center">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="180" class-name="pch-mono-col">
        <template #default="{ row }">
          {{ new Date(row.updated_at).toLocaleString() }}
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="createVisible" title="新建项目" width="420px">
    <el-input
      v-model="newTitle"
      placeholder="项目标题"
      maxlength="128"
      show-word-limit
      @keyup.enter="onCreateConfirm"
    />
    <template #footer>
      <el-button @click="createVisible = false">取消</el-button>
      <el-button type="primary" @click="onCreateConfirm">创建</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pch-list__tabs {
  margin-bottom: var(--pch-space-2);
}

.pch-list__table {
  cursor: pointer;
}
</style>
