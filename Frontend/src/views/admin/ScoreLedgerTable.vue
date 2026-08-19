<script lang="ts">
import type { ScoreReason } from '../../api/scoring'

// reason → 中文（方向对齐后端 LEDGER_REASON_SIGN：前四入账 +、后二出账 −）。
// 放普通 <script> 以支持具名导出（<script setup> 不允许模块级导出），
// ScoringAdmin 的 REASON_OPTIONS 等下游均从这里取。
export const REASON_LABEL: Record<ScoreReason, string> = {
  collect: '收集',
  build_a: '建造 A',
  leader_bonus: '队长奖励',
  settle: '结算',
  manual_adj: '手动修正',
  season_reset: '赛季重置',
}
</script>

<script setup lang="ts">
import type { ScoreLedgerEntry } from '../../api/scoring'

// 积分流水纯展示表（DRY）：ScoringAdmin 流水 tab 与 ScoringBalances 下钻抽屉共用。
// 加载 / 空态 / 错误态 / 分页由父级管理，本组件只负责把 entries 画出来。
defineProps<{ entries: ScoreLedgerEntry[] }>()
</script>

<template>
  <el-table :data="entries">
    <el-table-column label="时间" width="180" class-name="pch-mono-col">
      <template #default="{ row }">
        {{ new Date(row.created_at).toLocaleString() }}
      </template>
    </el-table-column>
    <el-table-column prop="account_id" label="账号" width="90" class-name="pch-mono-col" />
    <el-table-column label="变动" width="120" align="right" class-name="pch-mono-col">
      <template #default="{ row }">
        <el-tag
          :type="row.delta.startsWith('-') ? 'danger' : 'success'"
          size="small"
          effect="plain"
        >
          {{ row.delta.startsWith('-') ? row.delta : `+${row.delta}` }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column
      prop="balance_after"
      label="余额"
      width="110"
      align="right"
      class-name="pch-mono-col"
    />
    <el-table-column label="原因" width="110">
      <template #default="{ row }">
        {{ REASON_LABEL[row.reason as ScoreReason] ?? row.reason }}
      </template>
    </el-table-column>
    <el-table-column label="项目" width="90" align="center">
      <template #default="{ row }">{{ row.sheet_id ?? '—' }}</template>
    </el-table-column>
    <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip>
      <template #default="{ row }">{{ row.note ?? '—' }}</template>
    </el-table-column>
  </el-table>
</template>
