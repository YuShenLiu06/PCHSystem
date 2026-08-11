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
import { useChartTheme } from '../../composables/useChartTheme'

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
  /** xAxis 左沿（施工开始时间）；null/undefined 时由 ECharts 自动决定 */
  startTime?: string | null
  /** xAxis 右沿（归档时间或当前时间）；null/undefined 时由 ECharts 自动决定 */
  endTime?: string | null
}>()

// 按 account_id 分组 + 前向填充（逻辑见 utils/timelineFill，纯函数可单测）：
// inactive 时段水平保持上一值，线全程不断；startTime 早于首点时补 [startTime, 0] 锚点，
// 让每条线从 y=0 升起；晚加入玩家从其首点起。
const grouped = computed(() =>
  forwardFillTimeline(props.points, props.startTime, props.endTime),
)

// 主题随 data-theme 切换（option 是 computed → 依赖 appearance，切主题原地重算）
const theme = useChartTheme()

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
    color: [...theme.value.palette],
    textStyle: theme.value.textStyle,
    tooltip: {
      ...theme.value.tooltip,
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
    legend: { ...theme.value.legend, type: 'scroll', top: 0 },
    grid: { left: 40, right: 20, top: 40, bottom: 30, containLabel: true },
    // xAxis 范围 = 施工开始 → 当前/归档：min/max 由父组件透传（null/undefined 时自动）
    xAxis: {
      ...theme.value.axis,
      type: 'time',
      min: props.startTime ?? undefined,
      max: props.endTime ?? undefined,
      // 时序轴不画横向分隔虚线（与 yAxis 的重叠成网格噪声）
      splitLine: { show: false },
    },
    yAxis: {
      ...theme.value.axis,
      type: 'value',
      name: '累计净放置',
      // 左侧轴刻度同样用 formatQty 显单位（覆盖 theme.axis.axisLabel 的纯样式）
      axisLabel: {
        ...(theme.value.axis.axisLabel as object),
        formatter: (v: number) => formatQty(v),
      },
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
