<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { passwordLogin } from '../../api/identity'
import { useAuthStore } from '../../stores/auth'
import { extractApiError } from '../../utils/error'

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
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success(`欢迎，${resp.player.name}`)
    router.replace('/me')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card header="登录" style="max-width: 480px; margin: 40px auto;">
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
      </el-form-item>
    </el-form>
  </el-card>
</template>
