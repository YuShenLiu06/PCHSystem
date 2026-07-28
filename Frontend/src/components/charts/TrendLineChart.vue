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
import { forwardFillTimeline } from '../../utils/timelineFill'
import { formatQty } from '../../utils/qty'

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

// 按 account_id 分组 + 前向填充（逻辑见 utils/timelineFill，纯函数可单测）：
// inactive 时段水平保持上一值，线全程不断；晚加入玩家从其首点起。
const grouped = computed(() => forwardFillTimeline(props.points))

const option = computed<Option>(() => {
  const series: LineSeriesOption[] = []
  grouped.value.forEach((data, accountId) => {
    series.push({
      name: props.accountNames[accountId] ?? `账号 ${accountId}`,
      type: 'line',
      data: data as LineSeriesOption['data'],
      smooth: true,
      connectNulls: true,
      // 单点时显圆点，避免折线在缺数据时一片空白（timeline 刚起步常见）
      showSymbol: true,
      symbolSize: 6,
    })
  })
  return {
    tooltip: {
      trigger: 'axis',
      // 每条线的累计净放置用 formatQty 显单位（个/组/盒），复用公共方法
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as Array<{
          axisValueLabel?: string
          seriesName?: string
          value?: unknown
          marker?: string
        }>
        if (rows.length === 0) return ''
        const header = rows[0].axisValueLabel ?? ''
        const body = rows
          .map((p) => {
            const v = Array.isArray(p.value) ? p.value[1] : p.value
            return `${p.marker ?? ''}${p.seriesName ?? ''}: ${formatQty(Number(v ?? 0))}`
          })
          .join('<br/>')
        return header ? `${header}<br/>${body}` : body
      },
    },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 30, containLabel: true },
    xAxis: { type: 'time' },
    yAxis: {
      type: 'value',
      name: '累计净放置',
      // 左侧轴刻度同样用 formatQty 显单位
      axisLabel: { formatter: (v: number) => formatQty(v) },
    },
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
