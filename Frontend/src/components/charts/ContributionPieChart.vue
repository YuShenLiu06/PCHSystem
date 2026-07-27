<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import type { ComposeOption } from 'echarts/core'
import type { PieSeriesOption } from 'echarts/charts'
import type { TooltipComponentOption, LegendComponentOption } from 'echarts/components'
import type { ProgressAccountTotal } from '../../api/construction'

// 按需注册（模块级，只跑一次）
use([CanvasRenderer, PieChart, TooltipComponent, LegendComponent])

type Option = ComposeOption<
  PieSeriesOption | TooltipComponentOption | LegendComponentOption
>

const props = defineProps<{
  /** 账号累计聚合（placed / broken / net） */
  totals: ProgressAccountTotal[]
  /** 项目总需求量（sum of material need_qty）；用于算「未完成」扇区 */
  totalNeed: number
}>()

// 贡献来源组成：正贡献账号（net_qty > 0）。net<0 排除（明细表已可见，饼图不显负扇区）
const positive = computed(() => props.totals.filter((t) => t.net_qty > 0))
const sumPositiveNet = computed(() =>
  positive.value.reduce((s, t) => s + t.net_qty, 0),
)
// 未完成 = 总需求 - 已正贡献（封顶 ≥0；超完成或无需求 → 0）
const remaining = computed(() =>
  Math.max(props.totalNeed - sumPositiveNet.value, 0),
)

const isEmpty = computed(
  () => positive.value.length === 0 && remaining.value === 0,
)

// 「未完成」扇区用灰色区分（账号贡献用 echarts 默认配色）
const REMAINING_COLOR = '#e5e5e5'

interface PieData {
  name: string
  value: number
  itemStyle?: { color: string }
}

const option = computed<Option>(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (params: unknown) => {
      const p = (params ?? {}) as {
        name?: string
        value?: number
        percent?: number
      }
      const pct = (p.percent ?? 0).toFixed(1)
      return `${p.name ?? ''}: ${p.value ?? 0}（${pct}%）`
    },
  },
  legend: { type: 'scroll', orient: 'vertical', left: 'left', top: 'middle' },
  series: [
    {
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['60%', '50%'],
      avoidLabelOverlap: true,
      data: [
        ...positive.value.map<PieData>((t) => ({
          name: t.display_name,
          value: t.net_qty,
        })),
        ...(remaining.value > 0
          ? [
              {
                name: '未完成',
                value: remaining.value,
                itemStyle: { color: REMAINING_COLOR },
              },
            ]
          : []),
      ],
      label: { show: true, formatter: '{b}: {d}%' },
    },
  ],
}))
</script>

<template>
  <el-empty v-if="isEmpty" description="暂无贡献数据" />
  <v-chart v-else :option="option" autoresize style="height: 300px;" />
</template>
