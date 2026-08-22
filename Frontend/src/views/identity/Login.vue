<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { passwordLogin } from '../../api/identity'
import { resolveDisplayName } from '../../utils/identity'
import { useAuthStore } from '../../stores/auth'
import { extractApiError } from '../../utils/error'
import BrandLogo from '../../components/BrandLogo.vue'

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
    // player 可空：托管管理账号（ADMIN_* 环境同步）无绑定玩家，照常建立会话
    auth.set(
      { access_token: resp.access_token, refresh_token: resp.refresh_token },
      resp.player,
      resp.account,
    )
    ElMessage.success(`欢迎，${resolveDisplayName(resp.account, resp.player)}`)
    // 优先跳 redirect（如 bind 链接带来的 /bind/confirm?code=XXX）；
    // 无玩家 = 托管管理账号 → 落管理页（/me 现已可访问，#74，但管理页更贴合用途）
    const redirect = route.query.redirect as string | undefined
    router.replace(redirect || (resp.player ? '/me' : '/admin/scoring'))
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '登录失败')
  } finally {
    loading.value = false
  }
}

/** 新玩家注册引导：账号身份锚在游戏内玩家，注册页仅经 !!PCH login 回链进入（无直达入口）。 */
async function onRegister(): Promise<void> {
  await ElMessageBox.alert(
    '注册入口在游戏内：先在服务器聊天执行 !!PCH login，点击回执中最新的链接（每次签发会作废旧链接），'
      + '系统自动建立账号并进入注册页，在那里设置用户名和密码。',
    '新玩家注册',
    { confirmButtonText: '知道了', type: 'info' },
  ).catch(() => undefined) // 关闭即取消，无需处理
}
</script>

<template>
  <div class="pch-auth">
    <BrandLogo class="pch-auth__logo" :height="40" />
    <el-card>
      <template #header>登录</template>
      <el-alert type="info" :closable="false" show-icon class="pch-auth__notice">
        <template #title>先在游戏内连接账号</template>
        <div class="pch-auth__notice-body">
          网页登录仅限已设置用户名/密码的账号。首次使用请在游戏内执行
          <code>!!PCH login</code>，经回链建立账号后再回来登录。
        </div>
      </el-alert>
      <el-form label-width="72px">
        <el-form-item label="用户名">
          <el-input
            v-model="username"
            placeholder="3-32 位，字母/数字/下划线/连字符"
            maxlength="32"
            autocomplete="username"
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
            autocomplete="current-password"
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-form-item>
          <div class="pch-auth__actions">
            <el-button type="primary" :loading="loading" @click="onLogin">登录</el-button>
            <el-button @click="onRegister">注册</el-button>
            <el-button text @click="router.push('/auth')">
              首次使用？游戏内 !!PCH login
            </el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.pch-auth {
  display: flex;
  flex-direction: column;
  gap: var(--pch-space-4);
}

.pch-auth__logo {
  align-self: center;
}

.pch-auth__notice {
  margin-bottom: var(--pch-space-4);
}

.pch-auth__notice-body {
  font-size: var(--pch-text-xs);
  line-height: 1.65;
}

.pch-auth__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--pch-space-2);
  align-items: center;
}
</style>
