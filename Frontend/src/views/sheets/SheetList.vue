<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listSheets, createSheet, type SheetSummary } from '../../api/sheets'

const router = useRouter()
const sheets = ref<SheetSummary[]>([])
const loading = ref(false)
const createVisible = ref(false)
const newTitle = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    sheets.value = await listSheets()
  } catch (e: unknown) {
    ElMessage.error(errorMessage(e) ?? '加载表格列表失败')
  } finally {
    loading.value = false
  }
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
</script>

<template>
  <el-card header="表格列表">
    <template #default>
      <div style="margin-bottom: 12px;">
        <el-button type="primary" @click="openCreate">新建</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
      <el-table
        v-loading="loading"
        :data="sheets"
        style="cursor: pointer;"
        @row-click="(row: SheetSummary) => router.push(`/sheets/${row.id}`)"
      >
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="owner_uuid" label="所有者 UUID" width="320" />
        <el-table-column label="更新时间" width="200">
          <template #default="{ row }">
            {{ new Date(row.updated_at).toLocaleString() }}
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <el-dialog v-model="createVisible" title="新建表格" width="420px">
    <el-input v-model="newTitle" placeholder="请输入表格标题" maxlength="128" show-word-limit @keyup.enter="onCreateConfirm" />
    <template #footer>
      <el-button @click="createVisible = false">取消</el-button>
      <el-button type="primary" @click="onCreateConfirm">创建</el-button>
    </template>
  </el-dialog>
</template>
