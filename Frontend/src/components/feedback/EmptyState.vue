<script setup lang="ts">
// 空态是行动邀请，不是情绪表达：说清「这里放什么」+ 给下一步入口。
import BrandLogo from '../BrandLogo.vue'

const props = withDefaults(
  defineProps<{
    /** 这里本该有什么（如「还没有项目」）。 */
    title: string
    /** 怎么让它出现。可省。 */
    hint?: string
    /** 主操作按钮文案；省略则不渲染按钮。 */
    actionText?: string
    /** 显示淡化徽标（列表页主空态用；面板内小空态不用）。 */
    withMark?: boolean
  }>(),
  { withMark: false },
)

const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="pch-empty">
    <BrandLogo v-if="props.withMark" class="pch-empty__mark" :height="32" />
    <p class="pch-empty__title">{{ props.title }}</p>
    <p v-if="props.hint" class="pch-empty__hint">{{ props.hint }}</p>
    <el-button v-if="props.actionText" type="primary" plain @click="emit('action')">
      {{ props.actionText }}
    </el-button>
  </div>
</template>

<style scoped>
.pch-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--pch-space-3);
  padding: var(--pch-space-7) var(--pch-space-4);
  text-align: center;
}

.pch-empty__mark {
  opacity: 0.28;
  margin-bottom: var(--pch-space-1);
}

.pch-empty__title {
  color: var(--pch-text-strong);
  font-size: var(--pch-text-md);
}

.pch-empty__hint {
  max-width: 42ch;
  color: var(--pch-text-muted);
  font-size: var(--pch-text-sm);
}
</style>
