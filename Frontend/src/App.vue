<script setup lang="ts">
// RS-3：App.vue 只能是 <router-view /> + 必要的全局 layout 包裹。
// /auth 是 token 兑换中转页，不套外壳（无导航语境，避免闪现导航后立即跳走）。
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppHeader from './components/layout/AppHeader.vue'

const route = useRoute()
const showShell = computed(() => route.path !== '/auth')
</script>

<template>
  <a class="pch-skip-link" href="#main">跳到主内容</a>
  <AppHeader v-if="showShell" />
  <main id="main" class="pch-main" :class="{ 'pch-main--narrow': route.meta.narrow }">
    <RouterView />
  </main>
</template>
