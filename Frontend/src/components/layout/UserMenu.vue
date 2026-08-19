<script setup lang="ts">
// 用户区：当前身份 + 主题切换 + 登出。
// 登出仅清本地（JWT 无状态，后端无 /auth/logout），与 RS-4 既有妥协一致。
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useTheme, type ThemeMode } from '../../composables/useTheme'
import { resolveDisplayName } from '../../utils/identity'

const router = useRouter()
const auth = useAuthStore()
const { mode, cycleThemeMode } = useTheme()

// account 可为 null（未绑 Web 账号的纯游戏身份）→ 退回 player.name，
// 不放宽 resolveDisplayName 的签名（其余调用方传的都是非空 resp.account）
const name = computed(() => {
  if (auth.account) return resolveDisplayName(auth.account, auth.player)
  return auth.player?.name || '未登录'
})
const isTemporary = computed(() => auth.isTemporaryAccount)

const THEME_LABEL: Record<ThemeMode, string> = {
  auto: '主题：跟随系统',
  light: '主题：亮色',
  dark: '主题：暗色',
}
const THEME_GLYPH: Record<ThemeMode, string> = { auto: '◐', light: '☀', dark: '☾' }

function onLogout(): void {
  auth.clear()
  router.push('/auth')
}
</script>

<template>
  <div class="pch-user">
    <button
      type="button"
      class="pch-icon-btn"
      :aria-label="THEME_LABEL[mode]"
      :title="THEME_LABEL[mode]"
      @click="cycleThemeMode()"
    >
      <span aria-hidden="true">{{ THEME_GLYPH[mode] }}</span>
    </button>

    <el-dropdown v-if="auth.isAuthenticated" trigger="click">
      <button type="button" class="pch-user__trigger">
        <span class="pch-user__name">{{ name }}</span>
        <el-tag v-if="isTemporary" size="small" type="warning" effect="plain">临时</el-tag>
        <span class="pch-user__caret" aria-hidden="true">▾</span>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <!-- 托管管理账号无绑定玩家（auth.player 为 null）：/me 需玩家身份，隐藏入口 -->
          <el-dropdown-item v-if="auth.player" @click="router.push('/me')">我的身份</el-dropdown-item>
          <el-dropdown-item v-if="isTemporary" @click="router.push('/register')">
            注册永久账号
          </el-dropdown-item>
          <el-dropdown-item divided @click="onLogout">退出登录</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<style scoped>
.pch-user {
  display: flex;
  align-items: center;
  gap: var(--pch-space-2);
}

.pch-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--pch-hairline);
  border-radius: var(--pch-radius-md);
  background-color: transparent;
  color: var(--pch-text-muted);
  font-size: var(--pch-text-base);
  cursor: pointer;
  transition:
    color var(--pch-dur-fast) var(--pch-ease),
    border-color var(--pch-dur-fast) var(--pch-ease);
}

.pch-icon-btn:hover {
  color: var(--pch-accent);
  border-color: var(--pch-accent);
}

.pch-user__trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--pch-space-2);
  max-width: 200px;
  padding: var(--pch-space-1) var(--pch-space-2);
  border: 1px solid transparent;
  border-radius: var(--pch-radius-md);
  background-color: transparent;
  color: var(--pch-text);
  font-family: inherit;
  font-size: var(--pch-text-base);
  cursor: pointer;
  transition: border-color var(--pch-dur-fast) var(--pch-ease);
}

.pch-user__trigger:hover {
  border-color: var(--pch-hairline);
}

.pch-user__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pch-user__caret {
  color: var(--pch-text-muted);
  font-size: var(--pch-text-xs);
}

@media (max-width: 640px) {
  .pch-user__name {
    max-width: 96px;
  }
}
</style>
