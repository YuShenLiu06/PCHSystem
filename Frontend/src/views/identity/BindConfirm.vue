<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { confirmBind } from '../../api/identity'
import { extractApiError } from '../../utils/error'
import BrandLogo from '../../components/BrandLogo.vue'

const route = useRoute()
const router = useRouter()
const shortCode = ref((route.query.code as string) || '')
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
  <div class="pch-bind">
    <BrandLogo class="pch-bind__logo" :height="40" />
    <el-card>
      <template #header>确认绑定</template>
      <p class="pch-bind__lead">
        在游戏内执行 <code>!!PCH bind</code> 取短码，输入下方完成绑定。
      </p>
      <el-form label-width="56px">
        <el-form-item label="短码">
          <el-input
            v-model="shortCode"
            class="pch-input-code"
            placeholder="6 位"
            maxlength="6"
            autocomplete="one-time-code"
            @keyup.enter="onConfirm"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="onConfirm">确认绑定</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.pch-bind {
  display: flex;
  flex-direction: column;
  gap: var(--pch-space-4);
}

.pch-bind__logo {
  align-self: center;
}

.pch-bind__lead {
  margin-bottom: var(--pch-space-4);
  color: var(--pch-text-muted);
  font-size: var(--pch-text-sm);
}
</style>
