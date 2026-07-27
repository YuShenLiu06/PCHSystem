<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import type { ComposeOption } from 'echarts/core'
import type { LineSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  TooltipComponentOption,
  LegendComponentOption,
} from 'echarts/components'
import type { ProgressTimelinePoint } from '../../api/construction'

// 按需注册（模块级，只跑一次）：renderer + line + grid/tooltip/legend
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

type Option = ComposeOption<
  | LineSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
>

const props = defineProps<{
  /** 时序快照点（后端按上报批次落 placement_snapshots） */
  points: ProgressTimelinePoint[]
  /** account_id → 展示名映射，由父组件从 account_totals + breakdown 构建 */
  accountNames: Record<number, string>
}>()

// 按 account_id 分组，组内按时间升序，避免折线左右乱跳
const grouped = computed(() => {
  const groups = new Map<number, LineSeriesOption['data']>()
  for (const p of props.points) {
    const arr = groups.get(p.account_id) ?? []
    arr.push([p.recorded_at, p.total_net])
    groups.set(p.account_id, arr)
  }
  for (const arr of groups.values()) {
    ;(arr as Array<[string, number]>).sort((a, b) => (a[0] < b[0] ? -1 : 1))
  }
  return groups
})

const option = computed<Option>(() => {
  const series: LineSeriesOption[] = []
  grouped.value.forEach((data, accountId) => {
    series.push({
      name: props.accountNames[accountId] ?? `账号 ${accountId}`,
      type: 'line',
      data,
      smooth: true,
      connectNulls: true,
      // 单点时显圆点，避免折线在缺数据时一片空白（timeline 刚起步常见）
      showSymbol: true,
      symbolSize: 6,
    })
  })
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 30, containLabel: true },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', name: '累计净放置' },
    series,
  }
})
</script>

<template>
  <el-empty
    v-if="points.length === 0"
    description="暂无时序数据（需上报后生成）"
  />
  <v-chart
    v-else
    :option="option"
    autoresize
    style="height: 300px;"
  />
</template>
