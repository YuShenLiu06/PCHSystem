<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { previewLitematic, previewNbt, type ParsedMaterialPreview, type PreviewItem } from '../../api/parsing'
import { createSheetFromItems } from '../../api/sheets'
import { formatQty } from '../../utils/qty'
import { detectParseKind } from '../../utils/parseKind'

// mode=0 即 lock（锁定/二元备齐），与 SheetEditor 新增行默认一致
const MODE_LOCK = 0

const router = useRouter()

// el-upload 实例引用：拒绝非法文件时同步清空其内部 fileList，避免占满 :limit=1
const uploadRef = ref<UploadInstance>()

const title = ref('')
const file = ref<File | null>(null)
const loading = ref(false)
const preview = ref<ParsedMaterialPreview | null>(null)
const generating = ref(false)

// 两组独立的勾选态；组为空时自动取消勾选（见 groupMeta）
const includeBlocks = ref(true)
const includeContainers = ref(true)

// 行内可编辑副本（count 可改、可删行）；解析成功后填充
const blocks = ref<PreviewItem[]>([])
const container_items = ref<PreviewItem[]>([])

const groupMeta = computed(() => {
  const blocksEmpty = blocks.value.length === 0
  const containersEmpty = container_items.value.length === 0
  if (blocksEmpty) includeBlocks.value = false
  if (containersEmpty) includeContainers.value = false
  return {
    blocksEmpty,
    containersEmpty,
    blocksCount: blocks.value.length,
    containersCount: container_items.value.length,
    blocksTotal: blocks.value.reduce((s, r) => s + r.count, 0),
    containersTotal: container_items.value.reduce((s, r) => s + r.count, 0),
  }
})

// 生成按钮可用：标题非空 && 至少一个勾选的非空组
const canGenerate = computed(() => {
  const t = title.value.trim()
  if (!t) return false
  const hasBlocks = includeBlocks.value && !groupMeta.value.blocksEmpty
  const hasContainers = includeContainers.value && !groupMeta.value.containersEmpty
  return hasBlocks || hasContainers
})

function errorMessage(e: unknown): string {
  if (typeof e === 'object' && e !== null && 'response' in e) {
    const resp = (e as { response?: { data?: { detail?: string } } }).response
    return resp?.data?.detail ?? '请求失败'
  }
  return '请求失败'
}

function onFileChange(uploadFile: UploadFile): void {
  const raw = uploadFile.raw ?? null
  // 选文件时即按扩展名过滤（.litematic / .nbt），不符直接拒并清空（UX 早失败；后端仍二次校验为最终权威 RS-2）。
  // 拖拽绕过 accept 时被拒文件仍留在 el-upload 内部 fileList，会占满 :limit=1 致后续静默丢弃，故同步 clearFiles。
  if (raw && detectParseKind(raw) === null) {
    ElMessage.warning('仅支持 .litematic / .nbt 文件')
    file.value = null
    uploadRef.value?.clearFiles()
    return
  }
  file.value = raw
}

function onParse(): void {
  if (!file.value) {
    ElMessage.warning('请先选择 .litematic / .nbt 文件')
    return
  }
  // onFileChange 已校验扩展名；按类型选端点（.nbt → /parsing/nbt，其余 → /parsing/litematic）
  const kind = detectParseKind(file.value)
  const request = kind === 'nbt' ? previewNbt(file.value) : previewLitematic(file.value)
  loading.value = true
  request
    .then((data) => {
      preview.value = data
      // 深拷贝到可编辑副本（不污染原始预览）
      blocks.value = data.blocks.map((b) => ({ ...b }))
      container_items.value = data.container_items.map((c) => ({ ...c }))
      includeBlocks.value = data.blocks.length > 0
      includeContainers.value = data.container_items.length > 0
      // region_count 仅 litematic 多区域时 >1；nbt 单结构恒为 1，>1 才补显区域数避免噪音
      const regionPrefix = data.meta.region_count > 1 ? `${data.meta.region_count} 区域 / ` : ''
      ElMessage.success(`解析成功：${regionPrefix}共 ${data.meta.total_blocks} 方块`)
    })
    .catch((e: unknown) => {
      ElMessage.error(errorMessage(e))
    })
    .finally(() => {
      loading.value = false
    })
}

function removeItem(group: 'blocks' | 'containers', index: number): void {
  if (group === 'blocks') {
    blocks.value = blocks.value.filter((_, i) => i !== index)
  } else {
    container_items.value = container_items.value.filter((_, i) => i !== index)
  }
}

// 顺序生成两张表（await each）：方块组 → '·方块'，容器组 → '·容器物品'。
// 任一成功即 toast；首个成功后跳转其详情页；失败保留页面态供重试。
function onGenerate(): Promise<void> {
  if (!canGenerate.value) return Promise.resolve()
  const baseTitle = title.value.trim()
  generating.value = true
  const tasks: { suffix: string; rows: PreviewItem[] }[] = []
  if (includeBlocks.value && !groupMeta.value.blocksEmpty) {
    tasks.push({ suffix: '·方块', rows: blocks.value })
  }
  if (includeContainers.value && !groupMeta.value.containersEmpty) {
    tasks.push({ suffix: '·容器物品', rows: container_items.value })
  }

  let firstId: number | null = null
  return tasks
    .reduce<Promise<void>>(async (prev, task) => {
      await prev
      const created = await createSheetFromItems({
        title: baseTitle + task.suffix,
        items: task.rows.map((r, i) => ({
          item_name: r.item_name,
          need_qty: r.count,
          mode: MODE_LOCK,
          sort_order: i,
          registry_id: r.item_id, // 透传解析产出的 registry id，供游戏内一键提交精确匹配
        })),
      })
      if (firstId === null) firstId = created.id
      ElMessage.success(`已生成「${created.title}」`)
    }, Promise.resolve())
    .then(() => {
      if (firstId !== null) router.push(`/sheets/${firstId}`)
    })
    .catch((e: unknown) => {
      ElMessage.error(errorMessage(e))
    })
    .finally(() => {
      generating.value = false
    })
}
</script>

<template>
  <el-card v-loading="loading" header="解析投影 / 蓝图（.litematic / .nbt）">
    <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px;">
      <el-input
        v-model="title"
        placeholder="如：机械动力仓库"
        style="width: 280px;"
        maxlength="128"
      />
      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".litematic,.nbt"
        :on-change="onFileChange"
        :show-file-list="true"
        style="width: 320px;"
      >
        <div style="padding: 8px;">
          <div style="font-size: 13px;">拖拽 .litematic / .nbt 到此，或点击选择</div>
        </div>
      </el-upload>
      <el-button type="primary" :disabled="!file" @click="onParse">解析</el-button>
    </div>

    <el-alert
      v-if="preview && preview.untranslated.length > 0"
      type="info"
      :closable="false"
      style="margin-bottom: 12px;"
      :title="`${preview.untranslated.length} 个物品未找到中文翻译，已用 registry id 显示`"
    />

    <template v-if="preview">
      <el-card style="margin-bottom: 12px;">
        <template #header>
          <div style="display: flex; align-items: center; gap: 8px;">
            <el-checkbox v-model="includeBlocks" :disabled="groupMeta.blocksEmpty">包含此组</el-checkbox>
            <span style="font-weight: 600;">方块</span>
            <span style="color: #888; font-size: 12px;">
              {{ groupMeta.blocksCount }} 种 / 共 {{ formatQty(groupMeta.blocksTotal) }}
            </span>
          </div>
        </template>
        <el-empty v-if="groupMeta.blocksEmpty" description="无方块" />
        <el-table v-else :data="blocks" border>
          <el-table-column label="registry id" width="220">
            <template #default="{ row }">
              <span style="color: #999; font-size: 12px;">{{ row.item_id }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="item_name" label="中文名" min-width="160" />
          <el-table-column label="数量" width="140">
            <template #default="{ row }">
              <el-input-number v-model="row.count" :min="0" controls-position="right" />
            </template>
          </el-table-column>
          <el-table-column label="换算" width="90">
            <template #default="{ row }">{{ formatQty(row.count) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center">
            <template #default="{ $index }">
              <el-button type="danger" size="small" @click="removeItem('blocks', $index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card style="margin-bottom: 12px;">
        <template #header>
          <div style="display: flex; align-items: center; gap: 8px;">
            <el-checkbox v-model="includeContainers" :disabled="groupMeta.containersEmpty">包含此组</el-checkbox>
            <span style="font-weight: 600;">容器物品</span>
            <span style="color: #888; font-size: 12px;">
              {{ groupMeta.containersCount }} 种 / 共 {{ formatQty(groupMeta.containersTotal) }}
            </span>
          </div>
        </template>
        <el-empty v-if="groupMeta.containersEmpty" description="无容器物品（Create 投影常为空）" />
        <el-table v-else :data="container_items" border>
          <el-table-column label="registry id" width="220">
            <template #default="{ row }">
              <span style="color: #999; font-size: 12px;">{{ row.item_id }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="item_name" label="中文名" min-width="160" />
          <el-table-column label="数量" width="140">
            <template #default="{ row }">
              <el-input-number v-model="row.count" :min="0" controls-position="right" />
            </template>
          </el-table-column>
          <el-table-column label="换算" width="90">
            <template #default="{ row }">{{ formatQty(row.count) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center">
            <template #default="{ $index }">
              <el-button type="danger" size="small" @click="removeItem('containers', $index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-button type="primary" :disabled="!canGenerate" :loading="generating" @click="onGenerate">
        生成表格
      </el-button>
    </template>
  </el-card>
</template>
