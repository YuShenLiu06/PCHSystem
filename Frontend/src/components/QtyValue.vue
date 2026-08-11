<script setup lang="ts">
// Signature 组件：库存记数排版。
// 领域单位离散（1 盒 = 27 组 = 1728 个），按段渲染 + 等宽 tabular 对齐，
// 让材料行读起来像物品栏计数，而非小数百分比。
import { computed } from 'vue'
import { toQtySegments } from '../utils/qtySegments'

const props = withDefaults(
  defineProps<{
    /** 总个数。 */
    value: number
    /** 弱化显示（如「已交付」次要列）。 */
    muted?: boolean
  }>(),
  { muted: false },
)

const segments = computed(() => toQtySegments(props.value))
// 首段是主量级，后续段弱化，让读者先抓到最大单位
const title = computed(() => `${props.value} 个`)
</script>

<template>
  <span class="pch-qty" :class="{ 'pch-qty--muted': props.muted }" :title="title">
    <span
      v-for="(seg, i) in segments"
      :key="seg.unit || 'item'"
      class="pch-qty__seg"
      :class="{ 'pch-qty__seg--minor': i > 0 }"
      >{{ seg.value }}<span v-if="seg.unit" class="pch-qty__unit">{{ seg.unit }}</span></span
    >
  </span>
</template>
