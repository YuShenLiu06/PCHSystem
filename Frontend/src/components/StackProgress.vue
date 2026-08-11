<script setup lang="ts">
// 交付进度条：刻度锚在「组」边界（玩家按组搬箱子），≤32 组时可见刻度，
// 超出则退化为平滑条 —— 不硬撑刻度密度。
import { computed } from 'vue'
import { stackTickPercent } from '../utils/qtySegments'

const props = defineProps<{
  /** 已交付净量（个）。 */
  delivered: number
  /** 需求总量（个）。 */
  need: number
}>()

const pct = computed(() => {
  if (props.need <= 0) return 0
  return Math.min(100, Math.max(0, (props.delivered / props.need) * 100))
})

const isOver = computed(() => props.need > 0 && props.delivered > props.need)
const tickPct = computed(() => stackTickPercent(props.need))
const label = computed(() => `${Math.round(pct.value)}%`)
</script>

<template>
  <div
    class="pch-progress"
    role="progressbar"
    :aria-valuenow="Math.round(pct)"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-label="`交付进度 ${label}`"
  >
    <div class="pch-progress__track">
      <div
        class="pch-progress__fill"
        :class="{ 'pch-progress__fill--over': isOver }"
        :style="{ width: `${pct}%` }"
      />
      <div
        v-if="tickPct !== null"
        class="pch-progress__ticks"
        :style="{ '--pch-tick-pct': `${tickPct}%` }"
      />
    </div>
    <span class="pch-progress__label">{{ label }}</span>
  </div>
</template>
