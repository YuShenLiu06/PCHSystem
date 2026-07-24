<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { previewBatch, type BatchFilePreview, type PreviewItem } from '../../api/parsing'
import { createSheetFromItems } from '../../api/sheets'
import { formatQty } from '../../utils/qty'
import { detectParseKind } from '../../utils/parseKind'
import { aggregateItems } from '../../utils/batchAggregate'

// mode=0 即 lock（锁定/二元备齐），与 SheetEditor 新增行默认一致
const MODE_LOCK = 0
// POST /sheets/from-items 的 items max_length=2000：聚合后种类超此禁用生成
const FROM_ITEMS_MAX = 2000
// 单个投影解析的表格高度上限（px）：el-table max-height 使表体内部滚动、表头吸顶，
// 避免一个大投影把整页撑长（嵌套滚动条：每表内部滚动 + 外层页面滚动）。
const TABLE_MAX_HEIGHT = 240

const router = useRouter()

const title = ref('')
const selectedFiles = ref<File[]>([])
const loading = ref(false)
const generating = ref(false)

// 解析结果（index 与 multipliers / perFile* / removedFiles 对齐）
const fileResults = ref<BatchFilePreview[]>([])
const multipliers = ref<number[]>([])
// 每文件可编辑副本（count 可改——解析纠正在源头；聚合表只读，由它驱动，避免编辑被倍数覆盖）
const perFileBlocks = ref<PreviewItem[][]>([])
const perFileContainers = ref<PreviewItem[][]>([])
const removedFiles = ref<Set<number>>(new Set())
const removedItems = ref<Set<string>>(new Set())

function errorMessage(e: unknown): string {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail ?? '请求失败'
  }
  return '请求失败'
}

function onFileChange(_uf: UploadFile, allFiles: UploadFile[]): void {
  // el-upload change 第二参 = 当前完整列表；据此重建 selectedFiles，过滤非法扩展名
  const valid: File[] = []
  let hasInvalid = false
  for (const f of allFiles) {
    if (!f.raw) continue
    if (detectParseKind(f.raw) === null) {
      hasInvalid = true
      continue
    }
    valid.push(f.raw)
  }
  if (hasInvalid) ElMessage.warning('已忽略非 .litematic / .nbt 文件')
  selectedFiles.value = valid
}

function onParse(): void {
  if (selectedFiles.value.length === 0) {
    ElMessage.warning('请先选择至少一个 .litematic / .nbt 文件')
    return
  }
  loading.value = true
  previewBatch(selectedFiles.value)
    .then((data) => {
      fileResults.value = data.files
      multipliers.value = data.files.map(() => 1)
      perFileBlocks.value = data.files.map((f) =>
        f.preview ? f.preview.blocks.map((b) => ({ ...b })) : [],
      )
      perFileContainers.value = data.files.map((f) =>
        f.preview ? f.preview.container_items.map((c) => ({ ...c })) : [],
      )
      removedFiles.value = new Set()
      removedItems.value = new Set()
      const okCount = data.files.filter((f) => f.status === 'ok').length
      const errCount = data.files.length - okCount
      const parts: string[] = [`成功 ${okCount}`]
      if (errCount > 0) parts.push(`失败 ${errCount}`)
      ElMessage.success(`解析完成（${parts.join(' / ')}）`)
    })
    .catch((e: unknown) => ElMessage.error(errorMessage(e)))
    .finally(() => {
      loading.value = false
    })
}

// 可见文件列表：从 fileResults 派生，剔除已「移除」的 idx——removedFiles 仅过滤聚合，
// 这里同步过滤卡片显示，避免「移除该文件」后幽灵卡片仍可见/可编辑（数据视图一致）。
const visibleFiles = computed(() =>
  fileResults.value
    .map((file, idx) => ({ file, idx }))
    .filter((x) => !removedFiles.value.has(x.idx)),
)

// 聚合（只读 computed）：未移除文件 × 各自倍数（含源头 qty 纠正），扣除 removedItems
const aggregated = computed<PreviewItem[]>(() => {
  const inputs = fileResults.value
    .map((file, idx) => {
      if (removedFiles.value.has(idx) || file.status !== 'ok' || !file.preview) return null
      const edited: BatchFilePreview = {
        ...file,
        preview: {
          ...file.preview,
          blocks: perFileBlocks.value[idx] ?? file.preview.blocks,
          container_items: perFileContainers.value[idx] ?? file.preview.container_items,
        },
      }
      return { file: edited, multiplier: multipliers.value[idx] ?? 1 }
    })
    .filter((x): x is { file: BatchFilePreview; multiplier: number } => x !== null)
  const merged = aggregateItems(inputs)
  return merged.filter((r) => !removedItems.value.has(r.item_id))
})

const aggregatedTotal = computed(() => aggregated.value.reduce((s, r) => s + r.count, 0))
const overLimit = computed(() => aggregated.value.length > FROM_ITEMS_MAX)
const canGenerate = computed(() => {
  if (!title.value.trim()) return false
  if (aggregated.value.length === 0) return false
  if (overLimit.value) return false
  return true
})

function removeFile(idx: number): void {
  removedFiles.value = new Set(removedFiles.value).add(idx)
}

function removeAggregatedItem(id: string): void {
  removedItems.value = new Set([...removedItems.value, id])
}

// 单次生成单张项目表（无 suffix、无两段 reduce）：方块与容器物品合并为单一材料清单
function onGenerate(): void {
  if (!canGenerate.value) return
  generating.value = true
  const items = aggregated.value.map((r, i) => ({
    item_name: r.item_name,
    registry_id: r.item_id, // 透传 registry id，供游戏内一键提交精确匹配
    need_qty: r.count,
    mode: MODE_LOCK,
    sort_order: i,
  }))
  createSheetFromItems({ title: title.value.trim(), items })
    .then((created) => {
      ElMessage.success(`已生成「${created.title}」`)
      router.push(`/sheets/${created.id}`)
    })
    .catch((e: unknown) => ElMessage.error(errorMessage(e)))
    .finally(() => {
      generating.value = false
    })
}
</script>

<template>
  <el-card v-loading="loading" header="解析投影 / 蓝图（支持多文件与倍数）">
    <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px;">
      <el-input
        v-model="title"
        placeholder="如：主基地（合并多投影）"
        style="width: 280px;"
        maxlength="128"
      />
      <el-upload
        drag
        multiple
        :auto-upload="false"
        :limit="10"
        :on-exceed="() => ElMessage.warning('最多 10 个文件')"
        accept=".litematic,.nbt"
        :on-change="onFileChange"
        :show-file-list="true"
        style="width: 320px;"
      >
        <div style="padding: 8px;">
          <div style="font-size: 13px;">拖拽 .litematic / .nbt 到此，或点击选择（可单个或多个）</div>
        </div>
      </el-upload>
      <el-button type="primary" :disabled="selectedFiles.length === 0" @click="onParse">解析全部</el-button>
    </div>

    <el-alert
      v-if="fileResults.length > 0"
      type="info"
      :closable="false"
      style="margin-bottom: 12px;"
      title="方块与容器物品合并为单一材料清单；相同物品跨文件按各自倍数求和，生成单张项目表。"
    />

    <!-- 每文件：倍数（建造份数）+ 可编辑源头数量（解析纠正） -->
    <el-card
      v-for="{ file, idx } in visibleFiles"
      :key="idx"
      style="margin-bottom: 12px;"
    >
      <template #header>
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          <el-tag size="small" :type="file.status === 'ok' ? 'success' : 'danger'">{{ file.kind }}</el-tag>
          <span style="font-weight: 600;">{{ file.filename }}</span>
          <span v-if="file.status === 'error'" style="color: #f56c6c; font-size: 12px;">{{ file.error }}</span>
          <span v-if="file.status === 'ok' && file.preview" style="color: #888; font-size: 12px;">
            {{ formatQty(file.preview.meta.total_blocks) }}<span style="color: #bbb;">（{{ file.preview.meta.total_blocks }}）</span>
          </span>
          <template v-if="file.status === 'ok'">
            <span style="font-size: 12px; color: #666; margin-left: 8px;">建造份数（倍数）</span>
            <el-input-number
              v-model="multipliers[idx]"
              :min="1"
              :step="1"
              size="small"
              controls-position="right"
              style="width: 120px;"
            />
          </template>
          <el-button
            type="danger"
            size="small"
            link
            style="margin-left: auto;"
            @click="removeFile(idx)"
          >移除该文件</el-button>
        </div>
      </template>

      <template v-if="file.status === 'ok' && file.preview">
        <el-table :data="perFileBlocks[idx]" border size="small" :max-height="TABLE_MAX_HEIGHT" style="margin-bottom: 8px;">
          <el-table-column label="方块" min-width="200">
            <template #default="{ row }">
              <span style="color: #999; font-size: 12px;">{{ row.item_id }}</span>
              <span style="margin-left: 6px;">{{ row.item_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.count" :min="0" controls-position="right" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="换算" width="90">
            <template #default="{ row }">{{ formatQty(row.count) }}</template>
          </el-table-column>
        </el-table>
        <el-table
          v-if="perFileContainers[idx] && perFileContainers[idx].length > 0"
          :data="perFileContainers[idx]"
          border
          size="small"
          :max-height="TABLE_MAX_HEIGHT"
        >
          <el-table-column label="容器物品" min-width="200">
            <template #default="{ row }">
              <span style="color: #999; font-size: 12px;">{{ row.item_id }}</span>
              <span style="margin-left: 6px;">{{ row.item_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.count" :min="0" controls-position="right" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="换算" width="90">
            <template #default="{ row }">{{ formatQty(row.count) }}</template>
          </el-table-column>
        </el-table>
      </template>
      <el-empty v-else description="解析失败，无材料" :image-size="40" />
    </el-card>

    <!-- 聚合材料清单（只读 + 删除） -->
    <el-card v-if="fileResults.length > 0" style="margin-bottom: 12px;">
      <template #header>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-weight: 600;">聚合材料清单</span>
          <span style="color: #888; font-size: 12px;">
            {{ aggregated.length }} 种 / 共 {{ formatQty(aggregatedTotal) }}
          </span>
        </div>
      </template>
      <el-alert
        v-if="overLimit"
        type="error"
        :closable="false"
        style="margin-bottom: 8px;"
        :title="`物品种类 ${aggregated.length} 超过单表上限 ${FROM_ITEMS_MAX}，无法生成（请减少文件或合并）`"
      />
      <el-empty v-if="aggregated.length === 0" description="无可用物品（全部移除或解析失败）" />
      <el-table v-else :data="aggregated" border :max-height="TABLE_MAX_HEIGHT">
        <el-table-column label="registry id" width="220">
          <template #default="{ row }">
            <span style="color: #999; font-size: 12px;">{{ row.item_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="item_name" label="中文名" min-width="160" />
        <el-table-column label="数量" width="120">
          <template #default="{ row }">{{ row.count }}</template>
        </el-table-column>
        <el-table-column label="换算" width="90">
          <template #default="{ row }">{{ formatQty(row.count) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button type="danger" size="small" @click="removeAggregatedItem(row.item_id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-button
      v-if="fileResults.length > 0"
      type="primary"
      :disabled="!canGenerate"
      :loading="generating"
      @click="onGenerate"
    >生成项目表</el-button>
  </el-card>
</template>
