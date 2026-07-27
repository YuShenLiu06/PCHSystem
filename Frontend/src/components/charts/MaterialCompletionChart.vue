<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import type { ComposeOption } from 'echarts/core'
import type { BarSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import type { ProgressMaterialItem } from '../../api/construction'

// 按需注册（模块级，只跑一次）
use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

type Option = ComposeOption<
  BarSeriesOption | GridComponentOption | TooltipComponentOption
>

const props = defineProps<{
  /** 材料完成度（按 registry 聚合 need vs net） */
  items: ProgressMaterialItem[]
}>()

interface BarItem {
  value: number
  fullName: string
  registryId: string
  need: number
  net: number
  rawPct: number | null
}

const chartData = computed<BarItem[]>(() =>
  props.items.map((it) => ({
    // 完成度 null（need=0）→ 纵轴显 0（spec 要求）
    value: it.completion_pct ?? 0,
    fullName: it.item_name,
    registryId: it.registry_id,
    need: it.need_qty,
    net: it.net_qty,
    rawPct: it.completion_pct,
  })),
)

// 横轴标签过长截断（保留全名给 tooltip）
const axisLabels = computed(() =>
  chartData.value.map((d) =>
    d.fullName.length > 12 ? `${d.fullName.slice(0, 12)}…` : d.fullName,
  ),
)

const option = computed<Option>(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (params: unknown) => {
      // echarts tooltip formatter 回调类型过宽，运行时从 data 字段安全读取
      const p = (params ?? {}) as {
        data?: BarItem
        name?: string
      }
      const d = p.data
      const fullName = d?.fullName ?? p.name ?? ''
      const pctText = !d || d.rawPct == null ? '—' : `${d.rawPct.toFixed(1)}%`
      return `<div>${fullName}</div><div>净放置 ${d?.net ?? 0} / 需求 ${d?.need ?? 0} = ${pctText}</div>`
    },
  },
  grid: { left: 40, right: 20, top: 20, bottom: 60, containLabel: true },
  xAxis: {
    type: 'category',
    data: axisLabels.value,
    axisLabel: { rotate: 30 },
  },
  yAxis: { type: 'value', name: '完成度 %', max: 100 },
  series: [
    {
      type: 'bar',
      data: chartData.value,
      itemStyle: { color: '#409EFF' },
      // 完成度 100% 视觉提示（绿色）/ 不足保持蓝色
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#67C23A', type: 'dashed' },
        data: [{ yAxis: 100, label: { formatter: '满', position: 'end' } }],
      },
    },
  ],
}))
</script>

<template>
  <v-chart
    v-if="items.length > 0"
    :option="option"
    autoresize
    style="height: 300px;"
  />
  <el-empty
    v-else
    description="暂无材料数据（项目无 lock/progress 行或未上报）"
  />
</template>
