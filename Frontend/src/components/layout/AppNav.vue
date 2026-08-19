<script setup lang="ts">
// 导航项单一来源：顶栏与移动端抽屉共用，避免两处漂移。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../../stores/auth'

const props = withDefaults(defineProps<{ vertical?: boolean }>(), { vertical: false })
const emit = defineEmits<{ navigate: [] }>()

const route = useRoute()
const auth = useAuthStore()

interface NavItem {
  readonly to: string
  readonly label: string
  /** 仅 admin/owner 可见（R-9：仅可见性，真实权限后端 RBAC 为准）。 */
  readonly adminOnly?: boolean
}

const ALL_ITEMS: readonly NavItem[] = [
  { to: '/me', label: '身份' },
  { to: '/sheets', label: '项目' },
  { to: '/parsing/batch', label: '解析投影' },
  { to: '/admin/construction', label: '施工管理', adminOnly: true },
  { to: '/admin/scoring', label: '积分管理', adminOnly: true },
]

const isAdmin = computed(
  () => auth.account?.role === 'admin' || auth.account?.role === 'owner',
)
const items = computed(() => ALL_ITEMS.filter((it) => !it.adminOnly || isAdmin.value))

/** 当前项：精确匹配或作为路径前缀（/sheets/12 仍高亮「项目」）。 */
function isCurrent(to: string): boolean {
  return route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <nav
    class="pch-nav"
    :class="{ 'pch-nav--vertical': props.vertical }"
    aria-label="主导航"
  >
    <RouterLink
      v-for="item in items"
      :key="item.to"
      class="pch-nav__link"
      :class="{ 'is-current': isCurrent(item.to) }"
      :to="item.to"
      :aria-current="isCurrent(item.to) ? 'page' : undefined"
      @click="emit('navigate')"
    >
      {{ item.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.pch-nav {
  display: flex;
  align-items: center;
  gap: var(--pch-space-1);
}

.pch-nav__link {
  position: relative;
  padding: var(--pch-space-2) var(--pch-space-3);
  border-radius: var(--pch-radius-md);
  color: var(--pch-text-muted);
  font-size: var(--pch-text-base);
  font-weight: 500;
  text-decoration: none;
  transition:
    color var(--pch-dur-fast) var(--pch-ease),
    background-color var(--pch-dur-fast) var(--pch-ease);
}

.pch-nav__link:hover {
  color: var(--pch-text-strong);
  background-color: var(--pch-bg-raised);
  text-decoration: none;
}

.pch-nav__link.is-current {
  color: var(--pch-accent);
}

/* 当前项下方 2px 方头指示条（对齐徽标的像素语汇，不用圆角/下划线） */
.pch-nav__link.is-current::after {
  content: '';
  position: absolute;
  left: var(--pch-space-3);
  right: var(--pch-space-3);
  bottom: 2px;
  height: 2px;
  background-color: var(--pch-accent);
}

/* 抽屉内：纵向排列，指示条移到左侧 */
.pch-nav--vertical {
  flex-direction: column;
  align-items: stretch;
  gap: var(--pch-space-1);
}

.pch-nav--vertical .pch-nav__link {
  padding: var(--pch-space-3);
}

.pch-nav--vertical .pch-nav__link.is-current::after {
  left: 0;
  right: auto;
  top: var(--pch-space-2);
  bottom: var(--pch-space-2);
  width: 2px;
  height: auto;
}
</style>
