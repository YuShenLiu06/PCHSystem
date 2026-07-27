<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { APP_VERSION } from './version'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const auth = useAuthStore()
const isAdmin = auth.account?.role === 'admin' || auth.account?.role === 'owner'
</script>

<template>
  <nav v-if="route.path !== '/auth'" style="padding: 8px 16px; border-bottom: 1px solid #eee; display: flex; gap: 16px; align-items: center;">
    <RouterLink to="/me">身份</RouterLink>
    <RouterLink to="/sheets">项目</RouterLink>
    <RouterLink to="/parsing/batch">解析投影/蓝图</RouterLink>
    <RouterLink v-if="isAdmin" to="/admin/construction">施工管理</RouterLink>
    <span style="margin-left: auto; color: #999; font-size: 12px;">PCH v{{ APP_VERSION }}</span>
  </nav>
  <RouterView />
</template>
