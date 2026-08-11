<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { claimBind } from '../../api/identity'
import { useAuthStore } from '../../stores/auth'
import { extractApiError } from '../../utils/error'
import BrandLogo from '../../components/BrandLogo.vue'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const loading = ref(false)

// 校验规则：与后端一致
const USERNAME_REGEX = /^[A-Za-z0-9_-]{3,32}$/
const MIN_PASSWORD = 8
const MAX_PASSWORD = 128

async function onClaim(): Promise<void> {
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
    // claim 返回目标永久账号的 JWT（替换当前临时会话），同步到 store
    const resp = await claimBind(trimmedUsername, password.value)
    if (!resp.player) {
      ElMessage.error('账号数据异常：无绑定玩家，请联系管理员')
      return
    }
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success(`绑定成功：${resp.player.name}`)
    router.replace('/me')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '绑定失败')
  } finally {
    loading.value = false
  }
}

function goToRegister(): void {
  router.push('/register')
}
</script>

<template>
  <div class="pch-claim">
    <BrandLogo class="pch-claim__logo" :height="40" />
    <el-card>
      <template #header>绑定已有账号</template>
      <p class="pch-claim__lead">把当前临时会话挂接到已有的永久账号。</p>
      <el-form label-width="72px">
        <el-form-item label="用户名">
          <el-input
            v-model="username"
            placeholder="3-32 位，字母/数字/下划线/连字符"
            maxlength="32"
            autocomplete="username"
            @keyup.enter="onClaim"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            placeholder="8-128 位"
            maxlength="128"
            show-password
            autocomplete="current-password"
            @keyup.enter="onClaim"
          />
        </el-form-item>
        <el-form-item>
          <div class="pch-claim__actions">
            <el-button type="primary" :loading="loading" @click="onClaim">绑定</el-button>
            <el-button text @click="goToRegister">改为注册新账号</el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.pch-claim {
  display: flex;
  flex-direction: column;
  gap: var(--pch-space-4);
}

.pch-claim__logo {
  align-self: center;
}

.pch-claim__lead {
  margin-bottom: var(--pch-space-4);
  color: var(--pch-text-muted);
  font-size: var(--pch-text-sm);
}

.pch-claim__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--pch-space-2);
  align-items: center;
}
</style>
