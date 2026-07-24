<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { register } from '../../api/identity'
import { useAuthStore } from '../../stores/auth'
import { extractApiError } from '../../utils/error'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const loading = ref(false)

// 校验规则：与后端一致（用户名 3-32 位 [A-Za-z0-9_-]+，密码 8-128）
const USERNAME_REGEX = /^[A-Za-z0-9_-]{3,32}$/
const MIN_PASSWORD = 8
const MAX_PASSWORD = 128

async function onRegister(): Promise<void> {
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
    // register 返回换发后的永久账号 JWT，同步到 store 避免 isTemporaryAccount 等 getter 滞后
    const resp = await register(trimmedUsername, password.value)
    if (!resp.player) {
      ElMessage.error('账号数据异常：无绑定玩家，请联系管理员')
      return
    }
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success('注册成功')
    router.replace('/me')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '注册失败')
  } finally {
    loading.value = false
  }
}

function goToClaim(): void {
  router.push('/bind/claim')
}
</script>

<template>
  <el-card header="注册永久账号" style="max-width: 480px; margin: 40px auto;">
    <el-form label-width="80px">
      <el-form-item label="用户名">
        <el-input
          v-model="username"
          placeholder="3-32 位，字母/数字/下划线/连字符"
          maxlength="32"
          show-word-limit
          @keyup.enter="onRegister"
        />
      </el-form-item>
      <el-form-item label="密码">
        <el-input
          v-model="password"
          type="password"
          placeholder="8-128 位"
          maxlength="128"
          show-password
          @keyup.enter="onRegister"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="onRegister">注册</el-button>
        <el-button @click="goToClaim">已有账号？绑定</el-button>
        <el-button text @click="router.push('/login')">返回登录</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>
