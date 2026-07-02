<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <nav v-if="route.path !== '/auth'" style="padding: 8px 16px; border-bottom: 1px solid #eee; display: flex; gap: 16px;">
    <RouterLink to="/me">身份</RouterLink>
    <RouterLink to="/sheets">表格</RouterLink>
  </nav>
  <RouterView />
</template>
