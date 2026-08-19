<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  fetchLedger,
  adminAdjust,
  searchScoringPlayers,
  type ScoreLedgerEntry,
  type ScoreReason,
  type PlayerOption,
} from '../../api/scoring'
import { notifyOk, notifyWarn, notifyErr } from '../../composables/useNotify'
import { confirmAction } from '../../composables/useConfirm'
import { extractApiError } from '../../utils/error'
import PageHeader from '../../components/layout/PageHeader.vue'
import EmptyState from '../../components/feedback/EmptyState.vue'
import ErrorState from '../../components/feedback/ErrorState.vue'

// reason → 中文（方向对齐后端 LEDGER_REASON_SIGN：前四入账 +、后二出账 −）
const REASON_LABEL: Record<ScoreReason, string> = {
  collect: '收集',
  build_a: '建造 A',
  leader_bonus: '队长奖励',
  settle: '结算',
  manual_adj: '手动修正',
  season_reset: '赛季重置',
}
const DEBIT_REASONS: ReadonlySet<string> = new Set(['manual_adj', 'season_reset'])
const REASON_OPTIONS: ReadonlyArray<{ value: ScoreReason; label: string; sign: '+' | '−' }> = (
  Object.entries(REASON_LABEL) as Array<[ScoreReason, string]>
).map(([value, label]) => ({ value, label, sign: DEBIT_REASONS.has(value) ? '−' : '+' }))

// skip_reason → 中文（§4 全集；面板单条提交只需逐条提示）
const SKIP_REASON_LABEL: Record<string, string> = {
  'player not found': '玩家不存在',
  'player not bound to a web account': '玩家未绑定 Web 账号',
  'sheet not found': '关联项目不存在',
  'insufficient balance': '余额不足（未开透支）',
  'idempotency key conflict': '幂等键冲突',
}

const LIMIT_OPTIONS = [20, 50, 100, 200] as const

function playerLabel(p: PlayerOption): string {
  return p.display_name && p.display_name !== p.player_name
    ? `${p.display_name}（${p.player_name}）`
    : p.player_name
}

// --- 筛选 + 流水查询（服务端分页） ---

const filterPlayerUuid = ref<string | undefined>()
const playerOptions = ref<PlayerOption[]>([])
const playerSearchLoading = ref(false)
const since = ref<Date>()
const until = ref<Date>()
const limit = ref<number>(50)

const page = ref(1)
const entries = ref<ScoreLedgerEntry[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const isFirstLoad = ref(true)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await fetchLedger({
      player_uuid: filterPlayerUuid.value || undefined,
      since: since.value ? since.value.toISOString() : undefined,
      until: until.value ? until.value.toISOString() : undefined, // 后端开区间上界（<）
      page: page.value,
      limit: limit.value,
    })
    entries.value = data.items
    total.value = data.total
    loadError.value = ''
  } catch (e: unknown) {
    loadError.value = extractApiError(e) ?? '加载积分流水失败'
    notifyErr(e, '加载积分流水失败')
  } finally {
    loading.value = false
    isFirstLoad.value = false
  }
}

function onQuery(): void {
  page.value = 1
  void load()
}

function onReset(): void {
  filterPlayerUuid.value = undefined
  playerOptions.value = []
  since.value = undefined
  until.value = undefined
  limit.value = 50
  onQuery()
}

async function onSearchPlayers(q: string): Promise<void> {
  if (!q.trim()) {
    playerOptions.value = []
    return
  }
  playerSearchLoading.value = true
  try {
    playerOptions.value = await searchScoringPlayers(q.trim())
  } catch (e: unknown) {
    playerOptions.value = [] // 联想失败清空候选，不阻塞筛选
    notifyErr(e, '玩家联想失败')
  } finally {
    playerSearchLoading.value = false
  }
}

// --- 调分弹窗（单条；方向由 reason 定，对齐后端 admin/adjust 契约） ---

const adjustVisible = ref(false)
const submitting = ref(false)
const adjustForm = reactive({
  player_uuid: '',
  reason: 'manual_adj' as ScoreReason,
  amount: 1,
  sheet_id: undefined as number | undefined,
  note: '',
  notify: true,
  allow_overdraft: false,
})
// 弹窗内独立联想候选（与筛选候选互不串选）
const adjustOptions = ref<PlayerOption[]>([])
const adjustSearchLoading = ref(false)

const isDebitReason = computed(() => DEBIT_REASONS.has(adjustForm.reason))
const amountPreview = computed(() => {
  const n = Number(adjustForm.amount)
  if (!Number.isFinite(n) || n <= 0) return ''
  return isDebitReason.value ? `将 −${n.toFixed(2)} 积分` : `将 +${n.toFixed(2)} 积分`
})

function openAdjust(): void {
  Object.assign(adjustForm, {
    player_uuid: '',
    reason: 'manual_adj',
    amount: 1,
    sheet_id: undefined,
    note: '',
    notify: true,
    allow_overdraft: false,
  })
  adjustOptions.value = []
  adjustVisible.value = true
}

async function onAdjustSearchPlayers(q: string): Promise<void> {
  if (!q.trim()) {
    adjustOptions.value = []
    return
  }
  adjustSearchLoading.value = true
  try {
    adjustOptions.value = await searchScoringPlayers(q.trim())
  } catch (e: unknown) {
    adjustOptions.value = []
    notifyErr(e, '玩家联想失败')
  } finally {
    adjustSearchLoading.value = false
  }
}

async function onAdjustSubmit(): Promise<void> {
  if (!adjustForm.player_uuid) {
    notifyWarn('请选择玩家')
    return
  }
  if (!(adjustForm.amount > 0)) {
    notifyWarn('积分数量须为正数')
    return
  }
  const amount = adjustForm.amount.toFixed(2) // 恒正字符串（≤2 位小数），方向由 reason 定
  const target = adjustOptions.value.find((o) => o.player_uuid === adjustForm.player_uuid)
  const name = target ? playerLabel(target) : adjustForm.player_uuid
  const confirmed = await confirmAction({
    title: '调整积分',
    message: `${name} ${amountPreview.value}${adjustForm.note.trim() ? `（${adjustForm.note.trim()}）` : ''}`,
    confirmText: '确认调整',
    danger: isDebitReason.value,
  })
  if (!confirmed) return

  submitting.value = true
  try {
    const result = await adminAdjust({
      items: [
        {
          player_uuid: adjustForm.player_uuid,
          amount,
          reason: adjustForm.reason,
          sheet_id: adjustForm.sheet_id ?? null,
          note: adjustForm.note.trim() || null,
          // 每次提交新键：防网络层重试重复记账，弹窗内重复确认各自独立成账
          idempotency_key: crypto.randomUUID(),
        },
      ],
      notify: adjustForm.notify,
      allow_overdraft: isDebitReason.value && adjustForm.allow_overdraft,
    })
    const r = result.results[0]
    if (r.accepted) {
      adjustVisible.value = false
      notifyOk(
        r.idempotent_replay
          ? '该调整此前已生效（幂等回放），未重复记账'
          : `已调整，调整后余额 ${r.entry?.balance_after ?? '—'}`,
      )
      page.value = 1
      void load()
    } else {
      // 恒 200 逐条结算：skip 非异常，warn 提示原因
      notifyWarn(`未生效：${SKIP_REASON_LABEL[r.skip_reason ?? ''] ?? r.skip_reason ?? '未知原因'}`)
    }
  } catch (e: unknown) {
    notifyErr(e, '调整失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <PageHeader title="积分管理">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button type="primary" @click="openAdjust">调整积分</el-button>
    </template>
  </PageHeader>

  <el-card class="pch-scoring__filters">
    <el-form inline @submit.prevent="onQuery">
      <el-form-item label="玩家">
        <el-select
          v-model="filterPlayerUuid"
          filterable
          remote
          clearable
          placeholder="玩家名 / 昵称联想"
          :remote-method="onSearchPlayers"
          :loading="playerSearchLoading"
          class="pch-scoring__player-select"
        >
          <el-option
            v-for="p in playerOptions"
            :key="p.player_uuid"
            :value="p.player_uuid"
            :label="playerLabel(p)"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="起始">
        <el-date-picker v-model="since" type="datetime" placeholder="含此时刻起" />
      </el-form-item>
      <el-form-item label="截止">
        <el-date-picker v-model="until" type="datetime" placeholder="到此时刻前（不含）" />
      </el-form-item>
      <el-form-item label="每页">
        <el-select v-model="limit" class="pch-scoring__limit-select">
          <el-option v-for="n in LIMIT_OPTIONS" :key="n" :value="n" :label="`${n} 条`" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onQuery">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </el-form-item>
    </el-form>
  </el-card>

  <el-card>
    <el-skeleton v-if="isFirstLoad" :rows="6" animated />

    <ErrorState
      v-else-if="loadError && entries.length === 0"
      :message="loadError"
      @retry="load"
    />

    <EmptyState
      v-else-if="entries.length === 0"
      with-mark
      title="暂无积分流水"
      hint="调整玩家 / 时间筛选，或先做一次积分调整。"
    />

    <el-table v-else v-loading="loading" :data="entries">
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

    <el-pagination
      v-if="total > 0"
      v-model:current-page="page"
      class="pch-scoring__pager"
      layout="total, prev, pager, next, jumper"
      :total="total"
      :page-size="limit"
      @current-change="load"
    />
  </el-card>

  <el-dialog v-model="adjustVisible" title="调整积分" width="480px">
    <el-form label-width="88px">
      <el-form-item label="玩家" required>
        <el-select
          v-model="adjustForm.player_uuid"
          filterable
          remote
          clearable
          placeholder="输入玩家名联想"
          :remote-method="onAdjustSearchPlayers"
          :loading="adjustSearchLoading"
          class="pch-scoring__full"
        >
          <el-option
            v-for="p in adjustOptions"
            :key="p.player_uuid"
            :value="p.player_uuid"
            :label="playerLabel(p)"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="原因" required>
        <el-select v-model="adjustForm.reason" class="pch-scoring__full">
          <el-option
            v-for="opt in REASON_OPTIONS"
            :key="opt.value"
            :value="opt.value"
            :label="`${opt.label}（${opt.sign}）`"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="数量" required>
        <el-input-number
          v-model="adjustForm.amount"
          :min="0.01"
          :precision="2"
          :step="1"
          class="pch-scoring__full"
        />
      </el-form-item>
      <el-form-item v-if="amountPreview" label="预览">
        <span class="pch-scoring__preview">{{ amountPreview }}</span>
      </el-form-item>
      <el-form-item label="关联项目">
        <el-input-number
          v-model="adjustForm.sheet_id"
          :min="1"
          :step="1"
          placeholder="可选，项目（sheet）id"
          class="pch-scoring__full"
        />
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="adjustForm.note"
          maxlength="200"
          show-word-limit
          placeholder="审计备注，如：误发回收"
        />
      </el-form-item>
      <el-form-item label="站内通知">
        <el-switch v-model="adjustForm.notify" />
      </el-form-item>
      <el-form-item v-if="isDebitReason" label="允许透支">
        <div class="pch-scoring__overdraft">
          <el-switch v-model="adjustForm.allow_overdraft" />
          <span>开启后余额不足仍可扣成负数</span>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="adjustVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onAdjustSubmit">
        确认调整
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pch-scoring__filters {
  margin-bottom: var(--pch-space-4);
}

.pch-scoring__player-select {
  width: 240px;
}

.pch-scoring__limit-select {
  width: 110px;
}

.pch-scoring__full {
  width: 100%;
}

.pch-scoring__preview {
  color: var(--pch-text-muted);
  font-family: var(--pch-font-mono);
  font-size: var(--pch-text-sm);
}

.pch-scoring__overdraft {
  display: flex;
  align-items: center;
  gap: var(--pch-space-2);
  color: var(--pch-text-muted);
  font-size: var(--pch-text-xs);
}

.pch-scoring__pager {
  margin-top: var(--pch-space-4);
  justify-content: flex-end;
}
</style>
