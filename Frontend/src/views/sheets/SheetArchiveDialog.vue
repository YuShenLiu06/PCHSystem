<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSheetArchive, getSheetArchiveAsset } from '../../api/sheets'
import { extractApiError } from '../../utils/error'

const props = defineProps<{ sheetId: number }>()
const visible = defineModel<boolean>('visible')

const ARCHIVE_CHART_FILENAME = 'contributions.png'

const archiveLoading = ref(false)
const archiveContent = ref('')
// 贡献占比图 object URL（asset 端点需 JWT，<img> 直连发不出头，故 axios 拉 blob 再 createObjectURL）
const archiveImgUrl = ref('')

function revokeArchiveImgUrl(): void {
  if (archiveImgUrl.value) {
    URL.revokeObjectURL(archiveImgUrl.value)
    archiveImgUrl.value = ''
  }
}

watch(visible, async (v) => {
  if (!v) {
    revokeArchiveImgUrl()
    return
  }

  archiveLoading.value = true
  archiveContent.value = ''
  revokeArchiveImgUrl()

  try {
    archiveContent.value = await getSheetArchive(props.sheetId)
    // 贡献占比图（无图项目 404 → 静默不显，不影响 md 预览）
    try {
      const blob = await getSheetArchiveAsset(props.sheetId, ARCHIVE_CHART_FILENAME)
      archiveImgUrl.value = URL.createObjectURL(blob)
    } catch {
      // 无贡献占比图（项目无贡献者）→ 不显图，吞掉
    }
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '加载归档文档失败')
    visible.value = false
  } finally {
    archiveLoading.value = false
  }
})

onUnmounted(revokeArchiveImgUrl)
</script>

<template>
  <el-dialog v-model="visible" title="归档文档" width="80%" top="5vh">
    <div v-loading="archiveLoading">
      <pre style="white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; max-height: 50vh; overflow: auto; margin: 0;">{{ archiveContent }}</pre>
      <img v-if="archiveImgUrl" :src="archiveImgUrl" alt="贡献占比" style="max-width: 100%; margin-top: 12px;" />
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
