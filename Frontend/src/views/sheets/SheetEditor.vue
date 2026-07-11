<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteSheet } from '../../api/sheets'
import { formatQty } from '../../utils/qty'
import { useAuthStore } from '../../stores/auth'
import { useSheetDetail } from '../../composables/useSheetDetail'
import {
  MODE_LOCK,
  MODE_PROGRESS,
  isSubRow,
  phaseLabel,
  phaseTagType,
  statusLabel,
  statusTagType,
} from './sheetHelpers'
import SheetArchiveDialog from './SheetArchiveDialog.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const sheetId = computed(() => Number(route.params.id))
// 表格 ref：模板拥有，作依赖传入 composable（控制展开）
const sheetTableRef = ref<any>()

const {
  sheet,
  loading,
  errorMsg,
  newRow,
  newSubRow,
  rowDrafts,
  editingRowId,
  titleEditing,
  titleDraft,
  subRowPopoverVisible,
  canEdit,
  isReadOnly,
  treeRows,
  isClaimant,
  parentMode,
  canClaimRow,
  canReleaseRow,
  sheetErrorMessage,
  onAdvance,
  onSaveTitle,
  onAddRow,
  onStartEdit,
  onCancelEdit,
  onSaveRow,
  onDeleteRow,
  onAddSubRow,
  onSaveSubRow,
  onDeleteSubRow,
  onClaim,
  onSetDelivery,
  onContribute,
  onAdjustProgress,
  onRelease,
  onReject,
  onSubRowPopoverShow,
} = useSheetDetail({ sheetId, auth, sheetTableRef })

// 归档文档预览（子组件拥有加载/blob 生命周期）
const archiveVisible = ref(false)

// 用户拖拽过的列宽（key=列 label）——外置为 Vue 权威态。
// 根因：el-table 在 :data 变化（treeRows 每轮新引用）时重算 min-width 列，覆盖内部拖拽态，
// 导致用户拖拽的列宽刷新/轮询后丢失。把列宽外置 + localStorage 持久化后，刷新/轮询均回填。
const COLUMN_WIDTHS_KEY = 'pch_sheet_col_widths'

function loadSavedColumnWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(COLUMN_WIDTHS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, number>) : {}
  } catch {
    return {}
  }
}

const columnWidths = ref<Record<string, number>>(loadSavedColumnWidths())

function onHeaderDragEnd(newWidth: number, _oldWidth: number, column: { label?: string }): void {
  if (!column?.label) return
  columnWidths.value = { ...columnWidths.value, [column.label]: Math.round(newWidth) }
  try {
    localStorage.setItem(COLUMN_WIDTHS_KEY, JSON.stringify(columnWidths.value))
  } catch {
    // localStorage 不可用（隐私模式等）→ 仅内存态，忽略
  }
}

async function onDeleteSheet(): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除项目「${sheet.value?.title ?? ''}」？此操作不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteSheet(sheetId.value)
    ElMessage.success('项目已删除')
    router.push('/sheets')
  } catch (e: unknown) {
    ElMessage.error(sheetErrorMessage(e))
  }
}

function back(): void {
  router.push('/sheets')
}
</script>

<template>
  <el-card v-loading="loading">
    <template #header>
      <div v-if="sheet" style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
        <el-button link @click="back">← 返回项目列表</el-button>
        <!-- 阶段横幅 -->
        <el-tag :type="phaseTagType(sheet.status)" size="default" effect="plain">
          阶段：{{ phaseLabel(sheet.status) }}
        </el-tag>
        <span v-if="!titleEditing" style="font-weight: 600; font-size: 16px;">{{ sheet.title }}</span>
        <el-input
          v-else
          v-model="titleDraft"
          style="width: 280px;"
          maxlength="128"
          @keyup.enter="onSaveTitle"
        />
        <el-button v-if="canEdit && !isReadOnly && !titleEditing" link type="primary" @click="titleEditing = true">改标题</el-button>
        <template v-if="canEdit && !isReadOnly && titleEditing">
          <el-button type="primary" size="small" @click="onSaveTitle">保存</el-button>
          <el-button size="small" @click="() => { titleEditing = false; titleDraft = sheet!.title }">取消</el-button>
        </template>
        <span style="flex: 1;" />
        <!-- owner 阶段流转按钮（非 archived 态） -->
        <template v-if="canEdit && !isReadOnly">
          <el-button v-if="sheet.status === 'collecting'" size="small" type="warning" plain @click="onAdvance('constructing')">进入施工</el-button>
          <el-button v-if="sheet.status === 'collecting'" size="small" type="success" plain @click="onAdvance('archived')">直接归档</el-button>
          <el-button v-if="sheet.status === 'constructing'" size="small" type="success" plain @click="onAdvance('archived')">标记施工完成并归档</el-button>
        </template>
        <!-- 已归档：查看归档文档 -->
        <el-button v-if="isReadOnly" size="small" @click="archiveVisible = true">查看归档文档</el-button>
        <span style="color: #888; font-size: 12px;">所有者：{{ sheet.owner_name }}</span>
        <el-button v-if="canEdit && !isReadOnly" type="danger" plain @click="onDeleteSheet">删除项目</el-button>
      </div>
      <div v-else>项目详情</div>
    </template>

    <el-result v-if="errorMsg && !sheet" icon="error" title="加载失败" :sub-title="errorMsg" />

    <template v-else-if="sheet">
      <!-- 新增行（仅拥有者可见 + 非 archived 只读态） -->
      <div v-if="canEdit && !isReadOnly" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <el-input v-model="newRow.item_name" placeholder="物品名" style="width: 200px;" maxlength="128" />
        <el-input v-model="newRow.registry_id" placeholder="注册名（可空，如 minecraft:stone）" style="width: 240px;" maxlength="128" />
        <el-input-number v-model="newRow.need_qty" :min="0" placeholder="数量" controls-position="right" style="width: 130px;" />
        <el-select v-model="newRow.mode" style="width: 120px;">
          <el-option :value="0" label="锁定" />
          <el-option :value="1" label="进度" />
        </el-select>
        <el-input-number v-model="newRow.sort_order" :min="0" placeholder="排序" controls-position="right" style="width: 120px;" />
        <el-button type="primary" @click="onAddRow">添加</el-button>
      </div>

      <!-- 树状表格：顶层行 + 嵌套子行（el-table tree mode）。indent 加大让父子层级更直观 -->
      <el-table
        ref="sheetTableRef"
        :data="treeRows"
        border
        row-key="id"
        :indent="24"
        :tree-props="{ children: 'children' }"
        @header-dragend="onHeaderDragEnd"
      >
        <el-table-column label="物品名" min-width="180" class-name="tree-name-col" :width="columnWidths['物品名']">
          <template #default="{ row }">
            <el-input
              v-if="canEdit && !isReadOnly && editingRowId === row.id"
              v-model="rowDrafts[row.id].item_name"
              maxlength="128"
            />
            <span v-else>{{ row.item_name }}</span>
          </template>
        </el-table-column>

        <el-table-column label="注册名" min-width="180" :width="columnWidths['注册名']">
          <template #default="{ row }">
            <el-input
              v-if="canEdit && !isReadOnly && editingRowId === row.id"
              v-model="rowDrafts[row.id].registry_id"
              placeholder="minecraft:stone"
              maxlength="128"
              size="small"
            />
            <span v-else-if="row.registry_id" style="color: #999; font-size: 12px;">{{ row.registry_id }}</span>
            <span v-else style="color: #ccc;">—</span>
          </template>
        </el-table-column>

        <el-table-column label="需要数量" :width="columnWidths['需要数量'] ?? 100">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && !isReadOnly && editingRowId === row.id && !isSubRow(row)"
              v-model="rowDrafts[row.id].need_qty"
              :min="0"
              placeholder="数量"
              controls-position="right"
              size="small"
            />
            <span v-else>{{ row.need_qty }}</span>
          </template>
        </el-table-column>

        <el-table-column label="倍数" :width="columnWidths['倍数'] ?? 100">
          <template #default="{ row }">
            <template v-if="isSubRow(row)">
              <el-input-number
                v-if="canEdit && !isReadOnly && editingRowId === row.id"
                v-model="rowDrafts[row.id].qty_per_unit"
                :min="0.01"
                :step="0.5"
                :precision="2"
                controls-position="right"
                size="small"
              />
              <span v-else>{{ row.qty_per_unit }}</span>
            </template>
            <span v-else style="color: #ccc;">—</span>
          </template>
        </el-table-column>

        <el-table-column label="换算" :width="columnWidths['换算'] ?? 80">
          <template #default="{ row }">
            {{ formatQty(row.need_qty) }}
          </template>
        </el-table-column>

        <!-- 模式列：顶层行可切换；子行仅父=progress时可切换 -->
        <el-table-column label="模式" :width="columnWidths['模式'] ?? 80">
          <template #default="{ row }">
            <el-select
              v-if="canEdit && !isReadOnly && editingRowId === row.id && (!isSubRow(row) || parentMode(row) === MODE_PROGRESS)"
              v-model="rowDrafts[row.id].mode"
              size="small"
            >
              <el-option :value="0" label="锁定" />
              <el-option :value="1" label="进度" />
            </el-select>
            <span v-else>{{ row.mode === 1 ? '进度' : '锁定' }}</span>
          </template>
        </el-table-column>

        <!-- 认领者/贡献者列：lock 显单人 claimant_name；progress 显 contributors 多人 tag -->
        <el-table-column label="认领者" :width="columnWidths['认领者'] ?? 140">
          <template #default="{ row }">
            <template v-if="row.mode === MODE_PROGRESS">
              <template v-if="row.contributors && row.contributors.length">
                <el-tag
                  v-for="c in row.contributors"
                  :key="c.player_uuid"
                  size="small"
                  style="margin: 2px;"
                >
                  {{ c.player_name }}
                </el-tag>
              </template>
              <span v-else style="color: #aaa;">—</span>
            </template>
            <template v-else>
              <span v-if="row.claimant_name">{{ row.claimant_name }}</span>
              <span v-else style="color: #aaa;">—</span>
            </template>
          </template>
        </el-table-column>

        <!-- 状态列：el-tag，open 灰/claimed 蓝/done 绿 -->
        <el-table-column label="状态" :width="columnWidths['状态'] ?? 80" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status as 'open' | 'claimed' | 'done')" size="small">
              {{ statusLabel(row.status as 'open' | 'claimed' | 'done') }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 交付进度列：仅 progress 模式显 -->
        <el-table-column v-if="sheet.rows.some((r) => r.mode === MODE_PROGRESS)" label="交付进度" :width="columnWidths['交付进度'] ?? 120">
          <template #default="{ row }">
            <template v-if="row.mode === MODE_PROGRESS">
              <span style="font-size: 12px;">{{ row.delivered_qty }}/{{ row.need_qty }}</span>
              <el-progress
                :percentage="row.need_qty > 0 ? Math.min(Math.round((row.delivered_qty / row.need_qty) * 100), 100) : 0"
                :stroke-width="8"
                :show-text="false"
                style="margin-top: 2px;"
              />
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>

        <!-- 排序列：拥有者可编辑 -->
        <el-table-column label="排序" :width="columnWidths['排序'] ?? 90">
          <template #default="{ row }">
            <el-input-number
              v-if="canEdit && !isReadOnly && editingRowId === row.id"
              v-model="rowDrafts[row.id].sort_order"
              :min="0"
              placeholder="排序"
              controls-position="right"
              size="small"
            />
            <span v-else>{{ row.sort_order }}</span>
          </template>
        </el-table-column>

        <!-- 统一操作列：文字按钮 -->
        <el-table-column label="操作" :width="columnWidths['操作'] ?? 320" align="center">
          <template #default="{ row }">
            <template v-if="!isReadOnly">
              <!-- 拥有者操作 -->
              <template v-if="canEdit">
                <template v-if="editingRowId === row.id">
                  <el-button size="small" type="primary" @click="isSubRow(row) ? onSaveSubRow(row) : onSaveRow(row)">保存</el-button>
                  <el-button size="small" @click="onCancelEdit(row)">取消</el-button>
                </template>
                <template v-else>
                  <el-button size="small" @click="onStartEdit(row)">编辑</el-button>
                  <el-button size="small" type="danger" @click="isSubRow(row) ? onDeleteSubRow(row) : onDeleteRow(row)">删除</el-button>
                </template>
                <!-- 父行：添加子物品按钮（Popover） -->
                <el-popover
                  v-if="!isSubRow(row)"
                  v-model:visible="subRowPopoverVisible[row.id]"
                  placement="right"
                  width="400"
                  trigger="click"
                  @show="() => onSubRowPopoverShow(row)"
                >
                  <template #reference>
                    <el-button size="small">添加子物品</el-button>
                  </template>
                  <!-- v-if 守卫：popover 内容随表格预渲染，newSubRow[row.id] 缺失时跳过整块，
                       避免下方 v-model="newSubRow[row.id].X" 访问 undefined 抛错（数据层已预初始化，此处为防御兜底） -->
                  <div v-if="newSubRow[row.id]" style="display: flex; flex-direction: column; gap: 8px;">
                    <div style="font-weight: 600;">新增子物品</div>
                    <el-input
                      v-model="newSubRow[row.id].item_name"
                      placeholder="物品名（可空，留空按注册名翻译；存储为「父名-本名」）"
                      maxlength="128"
                      size="small"
                    />
                    <el-input
                      v-model="newSubRow[row.id].registry_id"
                      placeholder="注册名（如 minecraft:stick）"
                      maxlength="128"
                      size="small"
                    />
                    <div style="display: flex; gap: 8px; align-items: center;">
                      <span style="font-size: 12px; color: #666;">倍数：</span>
                      <el-input-number
                        v-model="newSubRow[row.id].qty_per_unit"
                        :min="0.01"
                        :step="0.5"
                        :precision="2"
                        controls-position="right"
                        size="small"
                        style="width: 110px;"
                      />
                      <span style="font-size: 12px; color: #888;">× {{ row.need_qty }} = {{ Math.ceil(row.need_qty * (newSubRow[row.id]?.qty_per_unit || 0)) }}</span>
                    </div>
                    <el-select v-model="newSubRow[row.id].mode" size="small" :teleported="false">
                      <el-option :value="0" label="锁定" />
                      <el-option :value="1" label="进度" :disabled="row.mode === MODE_LOCK" />
                    </el-select>
                    <div style="display: flex; gap: 8px; align-items: center;">
                      <span style="font-size: 12px; color: #666;">排序：</span>
                      <el-input-number
                        v-model="newSubRow[row.id].sort_order"
                        :min="0"
                        controls-position="right"
                        size="small"
                        style="width: 100px;"
                      />
                    </div>
                    <el-button type="primary" size="small" @click="onAddSubRow(row)">确认添加</el-button>
                  </div>
                </el-popover>
              </template>

              <!-- 玩家协作按钮 -->
              <el-button v-if="canClaimRow(row)" size="small" type="primary" @click="onClaim(row)">认领</el-button>
              <el-button v-if="row.mode === MODE_PROGRESS && row.status !== 'done' && auth.player" size="small" type="primary" @click="onContribute(row)">上交材料</el-button>
              <el-button v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed'" size="small" type="success" @click="onSetDelivery(row)">备齐</el-button>
              <el-button v-if="row.mode === MODE_LOCK && isClaimant(row) && row.status === 'claimed' && !canEdit && canReleaseRow(row)" size="small" @click="onRelease(row)">放弃</el-button>
              <el-button v-if="canEdit && row.mode === MODE_LOCK && (row.status === 'claimed' || row.status === 'done') && canReleaseRow(row)" size="small" @click="onRelease(row)">解除锁定</el-button>
              <el-button v-if="canEdit && row.mode === MODE_PROGRESS" size="small" type="warning" @click="onAdjustProgress(row)">调整进度</el-button>
              <el-button v-if="row.mode === MODE_LOCK && (isClaimant(row) || canEdit) && row.status === 'done'" size="small" type="warning" @click="onReject(row)">打回</el-button>
            </template>
            <span v-else style="color: #aaa;">—</span>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <!-- 归档文档预览（text/markdown + 贡献占比图）—— 子组件拥有加载/blob 生命周期 -->
  <SheetArchiveDialog v-model:visible="archiveVisible" :sheet-id="sheetId" />
</template>

<style scoped>
/*
 * 树状列（物品名）缩进修复：
 * el-table 把 el-table__indent（缩进）/ 展开图标 / el-table__placeholder（占位）注入该列的 .cell，
 * 紧贴 slot 内容之前。owner 编辑态的 el-input 默认 width:100%，与前面的缩进挤不下同一行 →
 * 输入框换行到 .cell 最左，视觉上"吃掉"了缩进（父子输入框都贴左边，看不出层级）。
 * 非 owner 态是 <span> 文本，内联顺排有缩进 —— 故仅 owner 视角无缩进。
 * 修法：该列 .cell 改 flex 横排，输入框 flex:1 + min-width:0 吃剩余宽度，
 * 缩进/图标/占位各守其位不再被挤掉。仅作用 tree-name-col 列，其它列不受影响。
 */
:deep(.tree-name-col .cell) {
  display: flex;
  align-items: center;
}

:deep(.tree-name-col .cell .el-input) {
  flex: 1;
  min-width: 0;
}
</style>
