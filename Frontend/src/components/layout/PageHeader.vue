<script setup lang="ts">
// 页头：眉标 + 标题 + 右侧操作区。
// 眉标只表达真实上下文（所属项目、阶段），不做装饰性编号。
defineProps<{ title: string; eyebrow?: string }>()
</script>

<template>
  <div class="pch-page-head">
    <div class="pch-page-head__text">
      <p v-if="eyebrow" class="pch-eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <slot name="meta" />
    </div>
    <div v-if="$slots.actions" class="pch-page-head__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.pch-page-head__text {
  display: flex;
  flex-direction: column;
  gap: var(--pch-space-1);
  min-width: 0;
}
</style>
