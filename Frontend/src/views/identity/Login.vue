<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { passwordLogin } from '../../api/identity'
import { resolveDisplayName } from '../../utils/identity'
import { useAuthStore } from '../../stores/auth'
import { extractApiError } from '../../utils/error'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const loading = ref(false)

// 校验规则：与后端一致
const USERNAME_REGEX = /^[A-Za-z0-9_-]{3,32}$/
const MIN_PASSWORD = 8
const MAX_PASSWORD = 128

async function onLogin(): Promise<void> {
  const trimmedUsername = username.value.trim()
  if (!USERNAME_REGEX.test(trimmedUsername)) {
    ElMessage.warning('用户名需 3-32 位，仅支持字母、数字、下划线、连字符')
    return
  }
  if (password.value.length < MIN_PASSWORD || password.value.length > MAX_PASSWORD) {
    ElMessage.warning(`密码长度需 ${MIN_PASSWORD}-${MAX_PASSWORD} 位`)
    return
  }
  loading.value = true
  try {
    const resp = await passwordLogin(trimmedUsername, password.value)
    if (!resp.player) {
      ElMessage.error('账号数据异常：无绑定玩家，请联系管理员')
      return
    }
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success(`欢迎，${resolveDisplayName(resp.account, resp.player)}`)
    // 优先跳 redirect（如 bind 链接带来的 /bind/confirm?code=XXX），否则进 /me
    const redirect = route.query.redirect as string | undefined
    router.replace(redirect || '/me')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card header="登录" style="max-width: 480px; margin: 40px auto;">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 16px;"
    >
      <template #title>必须在游戏内连接后才能登录</template>
      <div style="font-size: 12px; line-height: 1.6;">
        网页登录仅限已设置用户名/密码的账号。首次使用请先在游戏内执行
        <strong>!!PCH login</strong>，经回链建立账号；网页不支持单独注册。
      </div>
    </el-alert>
    <el-form label-width="80px">
      <el-form-item label="用户名">
        <el-input
          v-model="username"
          placeholder="3-32 位，字母/数字/下划线/连字符"
          maxlength="32"
          @keyup.enter="onLogin"
        />
      </el-form-item>
      <el-form-item label="密码">
        <el-input
          v-model="password"
          type="password"
          placeholder="8-128 位"
          maxlength="128"
          show-password
          @keyup.enter="onLogin"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="onLogin">登录</el-button>
        <el-button text @click="router.push('/auth')">首次使用？游戏内 !!PCH login</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>
