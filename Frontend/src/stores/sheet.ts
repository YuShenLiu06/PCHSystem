import { defineStore } from 'pinia'
import type { SheetDetail } from '../api/sheets'

/**
 * Sheet 详情客户端缓存（stale-while-revalidate）。
 *
 * - 进入详情页命中缓存 → useSheetDetail.load 立即 applyRefreshedSheet(cached) 渲染 + 后台
 *   silentRefresh revalidate，避免每次进入都等 fetch（"后面都快"）。
 * - 写操作 handler 与轮询统一经 useSheetDetail.applyRefreshedSheet → 本 store setDetail 自动刷新，
 *   无需额外失效逻辑；删除整表走 removeDetail。
 * - 不可变更新（全局 immutability 红线）：每次 setDetail/removeDetail 产出新 Record 引用。
 *
 * 仅缓存 detail（含 rows）；列表 summary 不入此 store（YAGNI——列表→详情的乐观骨架非必需）。
 */
export const useSheetStore = defineStore('sheet', {
  state: () => ({
    details: {} as Record<number, SheetDetail>,
  }),
  actions: {
    setDetail(detail: SheetDetail): void {
      this.details = { ...this.details, [detail.id]: detail }
    },
    removeDetail(id: number): void {
      if (!(id in this.details)) return
      const next = { ...this.details }
      delete next[id]
      this.details = next
    },
  },
})
