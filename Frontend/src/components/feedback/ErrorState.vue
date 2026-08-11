<script setup lang="ts">
// 失败态：说清发生了什么 + 给重试。不道歉、不含糊。
const props = withDefaults(
  defineProps<{
    /** 后端 detail 或兜底文案。 */
    message: string
    retryText?: string
  }>(),
  { retryText: '重新加载' },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="pch-error" role="alert">
    <p class="pch-error__msg">{{ props.message }}</p>
    <el-button plain size="small" @click="emit('retry')">{{ props.retryText }}</el-button>
  </div>
</template>

<style scoped>
.pch-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--pch-space-3);
  padding: var(--pch-space-6) var(--pch-space-4);
  text-align: center;
  border: 1px solid var(--pch-redstone);
  border-radius: var(--pch-radius-lg);
  background-color: var(--pch-redstone-soft);
}

.pch-error__msg {
  max-width: 48ch;
  color: var(--pch-text-strong);
  font-size: var(--pch-text-sm);
}
</style>
