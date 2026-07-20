<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { confirmBind } from '../../api/identity'
import { extractApiError } from '../../utils/error'

const router = useRouter()
const shortCode = ref('')
const loading = ref(false)

async function onConfirm(): Promise<void> {
  const code = shortCode.value.trim()
  if (!code) {
    ElMessage.warning('请输入短码')
    return
  }
  loading.value = true
  try {
    const resp = await confirmBind(code)
    ElMessage.success(`绑定成功：${resp.player.name}`)
    router.replace('/me')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '绑定失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card header="确认绑定" style="max-width: 480px; margin: 40px auto;">
    <p style="margin-bottom: 16px; color: #666;">
      请在游戏内执行 <code>!!PCH bind</code> 获取短码，然后输入下方：
    </p>
    <el-form label-width="80px">
      <el-form-item label="短码">
        <el-input
          v-model="shortCode"
          placeholder="输入游戏内显示的 6 位短码"
          maxlength="6"
          @keyup.enter="onConfirm"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="onConfirm">确认绑定</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>
