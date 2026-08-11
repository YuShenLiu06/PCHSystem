<script setup lang="ts">
// 顶栏：徽标 + 导航 + 用户区；≤1024px 折叠为抽屉（拍板方案）。
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import BrandLogo from '../BrandLogo.vue'
import AppNav from './AppNav.vue'
import UserMenu from './UserMenu.vue'
import { APP_VERSION } from '../../version'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const auth = useAuthStore()
const drawerOpen = ref(false)

// 路由变化关抽屉（点导航项后不残留）
watch(() => route.path, () => (drawerOpen.value = false))
</script>

<template>
  <header class="pch-header">
    <div class="pch-header__inner">
      <!-- 汉堡键只在有导航可展开时出现（未登录页无导航项） -->
      <button
        v-if="auth.isAuthenticated"
        type="button"
        class="pch-burger"
        aria-label="打开导航"
        :aria-expanded="drawerOpen"
        @click="drawerOpen = true"
      >
        <span aria-hidden="true">☰</span>
      </button>

      <RouterLink to="/me" class="pch-header__brand" aria-label="PCHSystem 首页">
        <BrandLogo :height="26" />
      </RouterLink>

      <AppNav v-if="auth.isAuthenticated" class="pch-header__nav" />
      <span v-else class="pch-header__spacer" />

      <div class="pch-header__tail">
        <span class="pch-header__version" :title="`前端版本 ${APP_VERSION}`">
          v{{ APP_VERSION }}
        </span>
        <UserMenu />
      </div>
    </div>

    <el-drawer
      v-if="auth.isAuthenticated"
      v-model="drawerOpen"
      direction="ltr"
      size="264px"
      :with-header="false"
      class="pch-drawer"
    >
      <div class="pch-drawer__head">
        <BrandLogo :height="24" />
      </div>
      <AppNav vertical @navigate="drawerOpen = false" />
    </el-drawer>
  </header>
</template>

<style scoped>
.pch-header {
  position: sticky;
  top: 0;
  z-index: 200;
  background-color: var(--pch-bg-panel);
  border-bottom: 1px solid var(--pch-hairline);
}

.pch-header__inner {
  display: flex;
  align-items: center;
  gap: var(--pch-space-3);
  height: var(--pch-header-h);
  max-width: var(--pch-content-max);
  margin: 0 auto;
  padding: 0 var(--pch-space-4);
}

.pch-header__brand {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}

.pch-header__brand:hover {
  text-decoration: none;
}

.pch-header__nav {
  flex: 1 1 auto;
  margin-left: var(--pch-space-3);
}

/* 未登录时占位，把用户区推到右侧 */
.pch-header__spacer {
  flex: 1 1 auto;
}

.pch-header__tail {
  display: flex;
  align-items: center;
  gap: var(--pch-space-3);
  margin-left: auto;
}

.pch-header__version {
  font-family: var(--pch-font-mono);
  font-size: var(--pch-text-2xs);
  font-variant-numeric: tabular-nums;
  color: var(--pch-text-muted);
}

.pch-burger {
  display: none;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--pch-hairline);
  border-radius: var(--pch-radius-md);
  background-color: transparent;
  color: var(--pch-text);
  cursor: pointer;
}

.pch-drawer__head {
  padding: var(--pch-space-3) var(--pch-space-3) var(--pch-space-4);
  border-bottom: 1px solid var(--pch-hairline);
  margin-bottom: var(--pch-space-3);
}

@media (max-width: 1024px) {
  .pch-header__inner {
    padding: 0 var(--pch-space-3);
    gap: var(--pch-space-2);
  }

  .pch-burger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .pch-header__nav {
    display: none;
  }

  .pch-header__version {
    display: none;
  }
}
</style>
