<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
</script>

<template>
  <div class="pch-notfound">
    <p class="pch-eyebrow">404</p>
    <h1>这个地址没有对应页面</h1>
    <p class="pch-notfound__path">
      <code>{{ route.fullPath }}</code>
    </p>
    <el-button type="primary" plain @click="router.replace('/me')">回到我的身份</el-button>
  </div>
</template>

<style scoped>
.pch-notfound {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--pch-space-3);
  padding: var(--pch-space-7) var(--pch-space-4);
  text-align: center;
}

.pch-notfound__path {
  max-width: 100%;
  overflow-wrap: anywhere;
}
</style>
