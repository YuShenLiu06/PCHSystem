<script setup lang="ts">
import { ref, computed, watch } from 'vue'
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
import {
  sortMaterialCompletion,
  type MaterialWithMine,
} from '../../utils/materialSort'
import { formatQty } from '../../utils/qty'

// 按需注册（模块级，只跑一次）
use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

type Option = ComposeOption<
  BarSeriesOption | GridComponentOption | TooltipComponentOption
>

const props = defineProps<{
  /** 材料完成度（按 registry 聚合 need vs net） */
  items: ProgressMaterialItem[]
  /** registry_id → 当前查看者的净贡献（父组件从 breakdown 按账号聚合得出）；缺失记 0 */
  myNetByRegistry?: Record<string, number>
}>()

// 上百种材料需翻页 + 排序（排序逻辑见 utils/materialSort，纯函数可单测）
const PAGE_SIZE = 15
const currentPage = ref(1)

const sorted = computed<MaterialWithMine[]>(() =>
  sortMaterialCompletion(props.items, props.myNetByRegistry ?? {}),
)

// 总页数（至少 1）。条数变化（轮询刷新 / sheetId 切换）→ 仅当当前页越界时夹回末页，
// 不在轮询重排时把用户拽回第 1 页（live chart 原地刷新更自然）。
const totalPages = computed(() =>
  Math.max(1, Math.ceil(sorted.value.length / PAGE_SIZE)),
)
watch(totalPages, (tp) => {
  if (currentPage.value > tp) currentPage.value = tp
})

const paged = computed<MaterialWithMine[]>(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE
  return sorted.value.slice(start, start + PAGE_SIZE)
})

interface BarItem {
  value: number
  fullName: string
  registryId: string
  need: number
  net: number
  rawPct: number | null
  mine: number
}

const chartData = computed<BarItem[]>(() =>
  paged.value.map((it) => ({
    // 完成度 null（need=0）→ 纵轴显 0（spec 要求）
    value: it.completion_pct ?? 0,
    fullName: it.item_name,
    registryId: it.registry_id,
    need: it.need_qty,
    net: it.net_qty,
    rawPct: it.completion_pct,
    mine: it.my_net_qty,
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
      return `<div>${fullName}</div><div>净放置 ${formatQty(d?.net ?? 0)} · 需求 ${formatQty(d?.need ?? 0)} = ${pctText}</div><div>我的贡献 ${formatQty(d?.mine ?? 0)}</div>`
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

function onPageChange(p: number): void {
  currentPage.value = p
}
</script>

<template>
  <div v-if="items.length > 0">
    <v-chart :option="option" autoresize style="height: 300px;" />
    <div style="display: flex; justify-content: center; margin-top: 12px;">
      <el-pagination
        :current-page="currentPage"
        :page-size="PAGE_SIZE"
        :total="sorted.length"
        layout="prev, pager, next"
        :hide-on-single-page="sorted.length <= PAGE_SIZE"
        small
        @current-change="onPageChange"
      />
    </div>
  </div>
  <el-empty v-else description="暂无材料数据（项目无 lock/progress 行或未上报）" />
</template>
