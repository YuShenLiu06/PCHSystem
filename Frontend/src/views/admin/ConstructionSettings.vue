<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { extractApiError } from '../../utils/error'
import {
  getConstructionSettings,
  updateConstructionSettings,
  listServerModSources,
  createServerModSource,
  deleteServerModSource,
  setServerModSourceEnabled,
  switchServerSource,
  type ConstructionSettings,
  type ServerModSourceEntry,
} from '../../api/construction'

// --- 设置 ---
const settings = ref<ConstructionSettings | null>(null)
const settingsLoading = ref(false)
const savingKey = ref<string | null>(null)

async function loadSettings(): Promise<void> {
  settingsLoading.value = true
  try {
    settings.value = await getConstructionSettings()
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '加载设置失败')
  } finally {
    settingsLoading.value = false
  }
}

async function toggleSetting(
  key: keyof ConstructionSettings,
  value: boolean | number | null,
): Promise<void> {
  if (!settings.value) return
  savingKey.value = String(key)
  try {
    const updated = await updateConstructionSettings({ [key]: value })
    settings.value = updated
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '保存失败')
    await loadSettings()
  } finally {
    savingKey.value = null
  }
}

// report_interval_seconds 单独处理（数字输入）
const intervalInput = computed({
  get: () => settings.value?.report_interval_seconds ?? 30,
  set: (v: number) => {
    if (settings.value) settings.value = { ...settings.value, report_interval_seconds: v }
  },
})
async function saveInterval(): Promise<void> {
  await toggleSetting('report_interval_seconds', intervalInput.value)
}

// --- 服务器上报源（插件管理）---
// 官方 MCDR 追踪器 = 默认插件（settings.official_tracker_enabled）；
// 第三方服务端 mod = server_mod_sources 白名单（逐源 enabled）。前端统一为「插件卡片」。
const officialEnabled = computed(() => settings.value?.official_tracker_enabled ?? true)

const modSources = ref<ServerModSourceEntry[]>([])
const newName = ref('')
const newNotes = ref('')
const adding = ref(false)
// 逐源启停 per-name loading + 失败回滚，与 toggleSetting 同范式
const togglingName = ref<string | null>(null)

const enabledPluginCount = computed(
  () => (officialEnabled.value ? 1 : 0) + modSources.value.filter((m) => m.enabled).length,
)
const totalPluginCount = computed(() => 1 + modSources.value.length)

async function loadModSources(): Promise<void> {
  try {
    modSources.value = await listServerModSources()
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '加载插件失败')
  }
}

async function addSource(): Promise<void> {
  if (!newName.value.trim()) return
  adding.value = true
  try {
    await createServerModSource(newName.value.trim(), newNotes.value.trim() || undefined)
    ElMessage.success('已登记插件')
    newName.value = ''
    newNotes.value = ''
    await loadModSources()
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '添加失败')
  } finally {
    adding.value = false
  }
}

async function removeSource(name: string): Promise<void> {
  try {
    await deleteServerModSource(name)
    ElMessage.success('已移除')
    await loadModSources()
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '删除失败')
  }
}

async function toggleSourceEnabled(name: string, enabled: boolean): Promise<void> {
  togglingName.value = name
  try {
    const updated = await setServerModSourceEnabled(name, enabled)
    modSources.value = modSources.value.map((m) => (m.name === name ? updated : m))
    ElMessage.success(enabled ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '切换失败')
    await loadModSources() // 失败回滚
  } finally {
    togglingName.value = null
  }
}

// --- 切换某玩家的服务器上报源（暂未上线：管理员强制某玩家走指定上报源，调试用）---
// 正式管理员后台完善前，UI 默认折叠隐藏（见模板 el-collapse），功能保留可展开使用。
const switchPlayerUuid = ref('')
const switchSourceType = ref<'mcdr' | 'server_mod'>('mcdr')
const switchSourceId = ref('')
const switching = ref(false)

const enabledPluginNames = computed(() =>
  modSources.value.filter((m) => m.enabled).map((m) => m.name),
)

async function doSwitchServer(): Promise<void> {
  if (!switchPlayerUuid.value.trim()) {
    ElMessage.warning('请填玩家 UUID')
    return
  }
  if (switchSourceType.value === 'server_mod' && !switchSourceId.value) {
    ElMessage.warning('须选一个启用中的插件')
    return
  }
  switching.value = true
  try {
    const state = await switchServerSource({
      player_uuid: switchPlayerUuid.value.trim(),
      source_type: switchSourceType.value,
      source_id: switchSourceType.value === 'server_mod' ? switchSourceId.value : null,
    })
    ElMessage.success(`已切换：${state.source_type}/${state.source_id ?? '-'}`)
    switchPlayerUuid.value = ''
    switchSourceId.value = ''
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '切换失败')
  } finally {
    switching.value = false
  }
}

function formatApprovedAt(iso: string): string {
  // 测试阶段精简日期（RS-1）
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

onMounted(() => {
  loadSettings()
  loadModSources()
})
</script>

<template>
  <div style="max-width: 900px; margin: 0 auto; padding: 16px;">
    <h2>施工管理</h2>

    <!-- 客户端上报 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>客户端上报</template>
      <div v-loading="settingsLoading" style="display: flex; flex-direction: column; gap: 12px;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="width: 240px;">允许玩家客户端 mod</span>
          <el-switch
            :model-value="settings?.allow_client_mods ?? true"
            :loading="savingKey === 'allow_client_mods'"
            @change="(v: boolean) => toggleSetting('allow_client_mods', v)"
          />
        </div>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="width: 240px;">上报 flush 间隔（秒）</span>
          <el-input-number v-model="intervalInput" :min="1" :step="5" size="small" />
          <el-button size="small" :loading="savingKey === 'report_interval_seconds'" @click="saveInterval">保存</el-button>
        </div>
      </div>
    </el-card>

    <!-- 服务器上报源（插件管理） -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>服务器上报源（插件）</span>
          <span class="pch-note">
            {{ enabledPluginCount }} / {{ totalPluginCount }} 启用
          </span>
        </div>
      </template>
      <div style="color: #909399; font-size: 12px; margin-bottom: 12px;">
        服务器上报源以「插件」方式为玩家代报施工进度。官方 MCDR 追踪器为默认插件；下方可登记第三方服务端 mod 插件并逐个启停。
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px;">
        <!-- 官方 MCDR 追踪器：默认插件，不可移除 -->
        <el-card
          shadow="hover"
          class="mod-source-card"
          :class="{ 'is-disabled': !officialEnabled }"
          :body-style="{ padding: '14px' }"
        >
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
            <div style="min-width: 0; flex: 1;">
              <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                <span style="font-weight: 600; font-size: 14px;">官方 MCDR 追踪器</span>
                <el-tag size="small" type="warning" effect="plain">默认</el-tag>
              </div>
              <div style="color: #909399; font-size: 12px; margin-top: 4px;">服务端内置 · 由 MCDR 插件代报</div>
            </div>
            <el-tag size="small" :type="officialEnabled ? 'success' : 'info'" effect="light">
              {{ officialEnabled ? '启用中' : '已停用' }}
            </el-tag>
          </div>
          <div style="display: flex; align-items: center; gap: 8px; margin-top: 12px;">
            <el-switch
              :model-value="officialEnabled"
              :loading="savingKey === 'official_tracker_enabled'"
              @change="(v: boolean) => toggleSetting('official_tracker_enabled', v)"
            />
            <span style="font-size: 12px; color: #909399;">
              {{ officialEnabled ? '允许上报' : '已禁用上报' }}
            </span>
          </div>
        </el-card>

        <!-- 第三方服务端 mod 插件 -->
        <el-card
          v-for="src in modSources"
          :key="src.name"
          shadow="hover"
          class="mod-source-card"
          :class="{ 'is-disabled': !src.enabled }"
          :body-style="{ padding: '14px' }"
        >
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
            <div style="min-width: 0; flex: 1;">
              <div style="font-weight: 600; font-size: 14px; word-break: break-all;">{{ src.name }}</div>
              <div
                v-if="src.notes"
                style="color: #909399; font-size: 12px; margin-top: 4px; word-break: break-all;"
              >
                {{ src.notes }}
              </div>
            </div>
            <el-tag size="small" :type="src.enabled ? 'success' : 'info'" effect="light">
              {{ src.enabled ? '启用中' : '已停用' }}
            </el-tag>
          </div>
          <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 12px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <el-switch
                :model-value="src.enabled"
                :loading="togglingName === src.name"
                @change="(v: boolean) => toggleSourceEnabled(src.name, v)"
              />
              <span style="font-size: 12px; color: #909399;">
                {{ src.enabled ? '允许上报' : '已禁用上报' }}
              </span>
            </div>
            <el-button size="small" type="danger" link @click="removeSource(src.name)">移除</el-button>
          </div>
          <div style="color: #c0c4cc; font-size: 11px; margin-top: 8px;">
            登记 {{ formatApprovedAt(src.approved_at) }}
          </div>
        </el-card>
      </div>

      <el-divider />
      <div style="display: flex; gap: 8px;">
        <el-input v-model="newName" placeholder="插件名（唯一标识）" style="width: 220px;" />
        <el-input v-model="newNotes" placeholder="备注（可选）" style="width: 260px;" />
        <el-button type="primary" :loading="adding" @click="addSource">登记插件</el-button>
      </div>
    </el-card>

    <!-- 高级：切换某玩家的服务器上报源（暂未上线，默认折叠隐藏，功能保留） -->
    <el-collapse style="margin-top: 16px;">
      <el-collapse-item name="switch-server">
        <template #title>
          <span style="font-weight: 600;">切换某玩家的服务器上报源</span>
          <el-tag size="small" type="info" effect="plain" style="margin-left: 8px;">暂未上线</el-tag>
        </template>
        <div style="color: #909399; font-size: 12px; margin-bottom: 12px;">
          强制某玩家走指定服务器上报源（调试用）。正式管理员后台完善前默认折叠；server_mod 须选启用中的插件。
        </div>
        <div style="display: flex; flex-direction: column; gap: 12px;">
          <el-input v-model="switchPlayerUuid" placeholder="玩家 UUID" style="width: 420px;" />
          <div style="display: flex; gap: 12px; align-items: center;">
            <el-radio-group v-model="switchSourceType">
              <el-radio value="mcdr">官方追踪器（mcdr）</el-radio>
              <el-radio value="server_mod">服务端 mod</el-radio>
            </el-radio-group>
            <el-select
              v-if="switchSourceType === 'server_mod'"
              v-model="switchSourceId"
              placeholder="选启用中的插件"
              style="width: 220px;"
            >
              <el-option v-for="n in enabledPluginNames" :key="n" :label="n" :value="n" />
            </el-select>
          </div>
          <el-button type="primary" :loading="switching" style="width: fit-content;" @click="doSwitchServer">切换</el-button>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.mod-source-card {
  transition:
    opacity 0.2s,
    transform 0.2s;
}
.mod-source-card.is-disabled {
  opacity: 0.6;
  background: #fafafa;
}
.mod-source-card.is-disabled :deep(.el-card__body) {
  background: #fafafa;
}
</style>
