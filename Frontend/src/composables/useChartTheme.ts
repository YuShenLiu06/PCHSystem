import { computed, type ComputedRef } from 'vue'
import { useTheme } from './useTheme'

/**
 * ECharts 主题。**不用 vue-echarts 的 `theme` prop** —— 那需要注册全局主题且切换时要
 * dispose 重建实例；改主题值直接写进 `option`，`computed` 依赖 `appearance` 后
 * 切主题自动重算、实例原地更新。
 *
 * 色板取 Minecraft 材质色（草/钻石/铜/金/红石/石头），与深板岩底色同居一个世界，
 * 不用 ECharts 默认蓝紫（与草方块绿打架）。
 */

/** 暗色：直接用饱和材质色。 */
const PALETTE_DARK: readonly string[] = [
  '#4ADE80', // 草方块绿（主）
  '#38BDF8', // 钻石青
  '#C08A5A', // 铜
  '#E3B341', // 金
  '#DC5B4A', // 红石
  '#94A3B8', // 石头
]

/** 亮色：同色相压暗，保证白底对比度。 */
const PALETTE_LIGHT: readonly string[] = [
  '#15803D',
  '#0369A1',
  '#92400E',
  '#A16207',
  '#B3402F',
  '#475569',
]

/**
 * 颗粒化输出：各图表按 option 真实结构 spread 到对应位置。
 * 刻意不给「一个 base 对象」——`categoryAxis`/`valueAxis` 只在**注册主题**里生效，
 * 直接塞进 option 是无效键（静默不生效，最难查）。
 */
export interface ChartTheme {
  readonly palette: readonly string[]
  /** → option.textStyle */
  readonly textStyle: Record<string, unknown>
  /** → option.legend */
  readonly legend: Record<string, unknown>
  /** → option.tooltip（与各图自有 formatter 合并） */
  readonly tooltip: Record<string, unknown>
  /** → option.xAxis / option.yAxis（与各图自有 type/min/max 合并） */
  readonly axis: Record<string, unknown>
  /** 饼图扇区描边色（与面板同色，形成切口感） */
  readonly panel: string
}

export function useChartTheme(): ComputedRef<ChartTheme> {
  const { appearance } = useTheme()

  return computed<ChartTheme>(() => {
    const isDark = appearance.value === 'dark'
    const text = isDark ? '#CBD5E1' : '#334155'
    const muted = isDark ? '#94A3B8' : '#64748B'
    const line = isDark ? '#1F2D44' : '#E2E8F0'
    const panel = isDark ? '#0E1626' : '#FFFFFF'

    return {
      palette: isDark ? PALETTE_DARK : PALETTE_LIGHT,
      textStyle: { color: text, fontSize: 12 },
      legend: { textStyle: { color: muted } },
      tooltip: {
        backgroundColor: isDark ? '#131C2E' : '#FFFFFF',
        borderColor: line,
        textStyle: { color: text },
      },
      axis: {
        axisLine: { lineStyle: { color: line } },
        axisLabel: { color: muted, fontSize: 11 },
        axisTick: { lineStyle: { color: line } },
        nameTextStyle: { color: muted, fontSize: 11 },
        splitLine: { lineStyle: { color: line, type: 'dashed' } },
      },
      panel,
    }
  })
}
