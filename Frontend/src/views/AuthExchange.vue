<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { exchangeToken } from '../api/identity'
import { resolveDisplayName } from '../utils/identity'
import { isNoBackendError } from '../utils/http-error'
import { useAuthStore } from '../stores/auth'

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
    ElMessage.success(`欢迎，${resolveDisplayName(resp.account, resp.player)}`)
    // 临时账号引导注册，否则进 /me
    if (resp.account.is_temporary) {
      router.replace('/register')
    } else {
      router.replace('/me')
    }
  } catch (e: unknown) {
    // 401 已被 http.ts 拦截器 auth.clear()。此处只负责导航 + 提示。
    // 网络错误（后端宕机 / 反代 5xx）：http.ts 已弹「后端超时或未部署」，此处借
    // isNoBackendError（与拦截器同源）识别，跳过误导性「登录失败」，仍引导 /login（后端恢复可重试）。
    // RS-5 仍守：不散判 e.response.status，仅复用拦截器同源 helper 做网络层 vs 后端响应二分。
    if (!isNoBackendError(e)) {
      ElMessage.warning('登录失败，请重新登录或重新获取链接')
    }
    router.replace('/login')
  }
})
</script>

<template>
  <el-result v-if="status === 'error'" icon="error" title="登录失败" :sub-title="errorMsg" />
  <el-result v-else icon="info" title="正在登录..." />
</template>
