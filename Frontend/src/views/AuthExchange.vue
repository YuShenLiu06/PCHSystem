<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { exchangeToken } from '../api/identity'
import { useAuthStore } from '../stores/auth'
import { extractApiError } from '../utils/error'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const status = ref<'loading' | 'ok' | 'error'>('loading')
const errorMsg = ref('')

onMounted(async () => {
  const token = route.query.token as string | undefined
  if (!token) {
    // 无 token（非 !!PCH login 回链：直接访问/书签/手输）→ 转账号密码登录
    router.replace('/login')
    return
  }
  try {
    const resp = await exchangeToken(token)
    if (!resp.player) {
      status.value = 'error'
      errorMsg.value = '账号数据异常：无绑定玩家，请联系管理员'
      return
    }
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success(`欢迎，${resp.player.name}`)
    // 临时账号引导注册，否则进 /me
    if (resp.account.is_temporary) {
      router.replace('/register')
    } else {
      router.replace('/me')
    }
  } catch (e: unknown) {
    status.value = 'error'
    errorMsg.value = extractApiError(e) ?? '兑换失败'
  }
})
</script>

<template>
  <el-result v-if="status === 'error'" icon="error" title="登录失败" :sub-title="errorMsg" />
  <el-result v-else icon="info" title="正在登录..." />
</template>
