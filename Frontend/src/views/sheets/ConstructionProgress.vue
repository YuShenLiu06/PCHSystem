<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { usePolling } from '../../composables/usePolling'
import { extractApiError } from '../../utils/error'
import {
  getConstructionProgress,
  type ConstructionProgress,
} from '../../api/construction'
import TrendLineChart from '../../components/charts/TrendLineChart.vue'
import MaterialCompletionChart from '../../components/charts/MaterialCompletionChart.vue'
import ContributionPieChart from '../../components/charts/ContributionPieChart.vue'

const props = defineProps<{ sheetId: number }>()

const progress = ref<ConstructionProgress | null>(null)
const loading = ref(false)
// 最近一次加载失败的后端文案；轮询失败高频，不弹 toast（同 usePolling 哲学），仅空态旁小字提示
const errorMsg = ref<string | undefined>(undefined)

async function load(): Promise<void> {
  loading.value = true
  try {
    progress.value = await getConstructionProgress(props.sheetId)
    errorMsg.value = undefined
  } catch (e: unknown) {
    // 失败静默兜底：空态 UI 旁附带最近一次错误文案，便于排查
    errorMsg.value = extractApiError(e)
  } finally {
    loading.value = false
  }
}

// 复用 usePolling：后台暂停 + 失败退避 + 卸载清理 + in-flight 重入保护（与 SheetList 一致）
const { refresh } = usePolling(load, { intervalMs: 5000 })

onMounted(load)

// 路由复用（sheetId 变）→ 立即拉新；refresh 自带 in-flight guard
watch(() => props.sheetId, refresh)

// 是否有施工记录：account_totals 非空才算「有」
// 边界防御（CLAUDE.md 约定）：后端历史曾漏字段（iter-1 无 timeline/material_completion），
// 字段缺失时退化为「无数据」空态，绝不读 undefined.length 让整页 SheetEditor 渲染崩溃。
const hasData = computed(
  () => !!progress.value && (progress.value.account_totals?.length ?? 0) > 0,
)

// {account_id → display_name} 映射，供 TrendLineChart 显示线条名称
// 双数据源：account_totals（已聚合）+ breakdown（仍待聚合的明细项），覆盖 timeline 出现的所有账号
const accountNames = computed<Record<number, string>>(() => {
  const map: Record<number, string> = {}
  const p = progress.value
  if (!p) return map
  for (const t of p.account_totals) {
    map[t.account_id] = t.display_name
  }
  for (const b of p.breakdown ?? []) {
    if (!(b.account_id in map)) {
      map[b.account_id] = b.display_name
    }
  }
  return map
})

// 当前查看者的「我的贡献」（registry_id → 净量）：从 breakdown 按当前 Web 账号 id 聚合
// （placement_records 锚 account_id，R-5）。材料完成度排序「我贡献优先」消费此映射；
// 未登录 / 未绑账号 → 空 Map（my_net_qty 全 0，无 tier1）。
const auth = useAuthStore()
const myNetByRegistry = computed<Record<string, number>>(() => {
  const aid = auth.account?.id
  if (aid === undefined) return {}
  const map: Record<string, number> = {}
  for (const b of progress.value?.breakdown ?? []) {
    if (b.account_id === aid) {
      map[b.registry_id] = (map[b.registry_id] ?? 0) + b.net_qty
    }
  }
  return map
})

// slot 暴露的跨版本稳定子集（CLAUDE.md 约定）：timeline / completion / totals
const slotTimeline = computed(() => progress.value?.timeline ?? [])
const slotCompletion = computed(() => progress.value?.material_completion ?? [])
const slotTotals = computed(() => progress.value?.account_totals ?? [])

// xAxis 左沿 = 施工开始时间（construction_started_at）；早期数据无此字段 → null（ECharts 自动）
const chartStartTime = computed<string | null | undefined>(
  () => progress.value?.construction_started_at ?? null,
)
// xAxis 右沿：archived 时停在归档时间；constructing 时取当前时间，使右沿随当前时间推进。
// computed 依赖 progress.value（usePolling 每 5s 刷新重赋值），刷新时重新计算 endTime。
const chartEndTime = computed<string | null | undefined>(() => {
  const p = progress.value
  if (!p) return null
  return p.archived_at ?? new Date().toISOString()
})

// 项目总需求量（sum of material need_qty）—— 供 ContributionPieChart 算「未完成」扇区
const totalNeed = computed(() =>
  (progress.value?.material_completion ?? []).reduce((s, m) => s + m.need_qty, 0),
)
</script>

<template>
  <el-card v-loading="loading" style="margin-top: 16px;">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <span>施工进度（方块净放置）</span>
        <el-button size="small" link @click="refresh">刷新</el-button>
      </div>
    </template>

    <div v-if="!hasData" class="pch-muted">
      暂无施工记录（constructing 期内由追踪器上报累计）。
      <div v-if="errorMsg" class="pch-note pch-text-danger" style="margin-top: var(--pch-space-1);">
        最近一次加载失败：{{ errorMsg }}
      </div>
    </div>

    <template v-else>
      <!--
        具名 slot「charts」：默认渲染三图（时序折线 / 材料完成度柱图 / 账号贡献占比饼图）。
        父组件可通过 `<template #charts="{ timeline, completion, totals }">...</template>`
        完全覆盖图表区自定义。slot prop 三件套跨版本稳定（CLAUDE.md 约定）。
      -->
      <slot
        name="charts"
        :progress="progress"
        :timeline="slotTimeline"
        :completion="slotCompletion"
        :totals="slotTotals"
        :account-names="accountNames"
      >
        <h4 style="margin: 0 0 8px;">时序（按账号累计净放置）</h4>
        <TrendLineChart
          v-if="progress && (progress.timeline?.length ?? 0) > 0"
          :points="progress.timeline"
          :account-names="accountNames"
          :start-time="chartStartTime"
          :end-time="chartEndTime"
        />
        <el-empty v-else description="暂无时序数据（需上报后生成）" />

        <h4 style="margin: 16px 0 8px;">材料完成度</h4>
        <MaterialCompletionChart
          v-if="progress && (progress.material_completion?.length ?? 0) > 0"
          :items="progress.material_completion"
          :my-net-by-registry="myNetByRegistry"
        />
        <el-empty v-else description="暂无材料数据（项目无 lock/progress 行或未上报）" />

        <h4 style="margin: 16px 0 8px;">账号贡献占比</h4>
        <ContributionPieChart
          v-if="progress"
          :totals="progress.account_totals"
          :total-need="totalNeed"
        />
      </slot>
    </template>
  </el-card>
</template>
