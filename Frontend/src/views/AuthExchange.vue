<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { http } from '../utils/http'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const status = ref<'loading' | 'ok' | 'error'>('loading')
const errorMsg = ref('')

onMounted(async () => {
  const token = route.query.token as string | undefined
  if (!token) { status.value = 'error'; errorMsg.value = '缺少 token'; return }
  try {
    const { data } = await http.post('/auth/exchange', { token })
    auth.set({ access_token: data.access_token, refresh_token: data.refresh_token }, data.player)
    ElMessage.success(`欢迎，${data.player.name}`)
    router.replace('/me')
  } catch (e: any) {
    status.value = 'error'
    errorMsg.value = e.response?.data?.detail ?? '兑换失败'
  }
})
</script>

<template>
  <el-result v-if="status === 'error'" icon="error" title="登录失败" :sub-title="errorMsg" />
  <el-result v-else icon="info" title="正在登录..." />
</template>
