<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchBalances, LIMIT_OPTIONS, type ScoreBalanceRow } from '../../api/scoring'
import { notifyErr } from '../../composables/useNotify'
import { extractApiError } from '../../utils/error'
import EmptyState from '../../components/feedback/EmptyState.vue'
import ErrorState from '../../components/feedback/ErrorState.vue'

// 「玩家积分」tab：全账号余额排名（后端 GET /v1/scoring/admin/balances，
// 排序 balance DESC + account_id 稳定序；行 = 有绑定玩家的 WebAccount，R-5）。
// 自含加载与分页，经 defineExpose(load) 供父页「刷新 / 调分后联动」调用。

const page = ref(1)
const limit = ref(50)
const rows = ref<ScoreBalanceRow[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const isFirstLoad = ref(true)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await fetchBalances({ page: page.value, limit: limit.value })
    rows.value = data.items
    total.value = data.total
    loadError.value = ''
  } catch (e: unknown) {
    loadError.value = extractApiError(e) ?? '加载玩家积分失败'
    notifyErr(e, '加载玩家积分失败')
  } finally {
    loading.value = false
    isFirstLoad.value = false
  }
}

function onLimitChange(): void {
  page.value = 1
  void load()
}

/** 全局排名 = 跨页连续序（后端只回当页行，名次由 (page-1)*limit + 行序推得） */
function rankOf(index: number): number {
  return (page.value - 1) * limit.value + index + 1
}

defineExpose({ load })

onMounted(load)
</script>

<template>
  <el-skeleton v-if="isFirstLoad" :rows="6" animated />

  <ErrorState
    v-else-if="loadError && rows.length === 0"
    :message="loadError"
    @retry="load"
  />

  <EmptyState
    v-else-if="rows.length === 0"
    with-mark
    title="暂无玩家积分"
    hint="还没有绑定 Web 账号的玩家，先让玩家完成绑定。"
  />

  <template v-else>
    <el-table v-loading="loading" :data="rows">
      <el-table-column label="排名" width="80" align="center" class-name="pch-mono-col">
        <template #default="{ $index }">{{ rankOf($index) }}</template>
      </el-table-column>
      <el-table-column prop="account_id" label="账号" width="90" class-name="pch-mono-col" />
      <el-table-column prop="display_name" label="显示名" min-width="120" show-overflow-tooltip />
      <el-table-column label="玩家名" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ row.player_names.join('、') }}</template>
      </el-table-column>
      <el-table-column label="余额" width="120" align="right" class-name="pch-mono-col">
        <template #default="{ row }">
          <el-tag v-if="row.balance.startsWith('-')" type="danger" size="small" effect="plain">
            {{ row.balance }}
          </el-tag>
          <template v-else>{{ row.balance }}</template>
        </template>
      </el-table-column>
      <el-table-column prop="entries_count" label="流水笔数" width="100" align="right" />
      <el-table-column label="最近变动" width="180" class-name="pch-mono-col">
        <template #default="{ row }">
          {{ row.last_entry_at ? new Date(row.last_entry_at).toLocaleString() : '—' }}
        </template>
      </el-table-column>
    </el-table>

    <div class="pch-balances__footer">
      <el-select :model-value="limit" class="pch-balances__limit-select" @change="onLimitChange">
        <el-option v-for="n in LIMIT_OPTIONS" :key="n" :value="n" :label="`${n} 条/页`" />
      </el-select>
      <el-pagination
        v-if="total > 0"
        v-model:current-page="page"
        layout="total, prev, pager, next, jumper"
        :total="total"
        :page-size="limit"
        @current-change="load"
      />
    </div>
  </template>
</template>

<style scoped>
.pch-balances__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--pch-space-3);
  margin-top: var(--pch-space-4);
}

.pch-balances__limit-select {
  width: 110px;
}
</style>
