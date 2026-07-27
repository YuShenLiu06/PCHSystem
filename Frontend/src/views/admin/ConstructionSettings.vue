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

async function toggleSetting(key: keyof ConstructionSettings, value: boolean | number | null): Promise<void> {
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

// --- 白名单 ---
const modSources = ref<ServerModSourceEntry[]>([])
const newName = ref('')
const newNotes = ref('')
const adding = ref(false)

async function loadModSources(): Promise<void> {
  try {
    modSources.value = await listServerModSources()
  } catch (e) {
    ElMessage.error(extractApiError(e) ?? '加载白名单失败')
  }
}

async function addSource(): Promise<void> {
  if (!newName.value.trim()) return
  adding.value = true
  try {
    await createServerModSource(newName.value.trim(), newNotes.value.trim() || undefined)
    ElMessage.success('已加入白名单')
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

// --- 服务端源切换 ---
const switchPlayerUuid = ref('')
const switchSourceType = ref<'mcdr' | 'server_mod'>('mcdr')
const switchSourceId = ref('')
const switching = ref(false)

const whitelistedNames = computed(() => modSources.value.map((m) => m.name))

async function doSwitchServer(): Promise<void> {
  if (!switchPlayerUuid.value.trim()) {
    ElMessage.warning('请填玩家 UUID')
    return
  }
  if (switchSourceType.value === 'server_mod' && !switchSourceId.value) {
    ElMessage.warning('server_mod 须选白名单内源')
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

onMounted(() => {
  loadSettings()
  loadModSources()
})
</script>

<template>
  <div style="max-width: 900px; margin: 0 auto; padding: 16px;">
    <h2>施工管理</h2>

    <!-- 运行时开关 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>运行时开关</template>
      <div v-loading="settingsLoading" style="display: flex; flex-direction: column; gap: 12px;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="width: 240px;">官方 MCDR 追踪器</span>
          <el-switch
            :model-value="settings?.official_tracker_enabled ?? true"
            :loading="savingKey === 'official_tracker_enabled'"
            @change="(v: boolean) => toggleSetting('official_tracker_enabled', v)"
          />
        </div>
        <div style="display: flex; align-items: center; gap: 12px;">
          <span style="width: 240px;">允许第三方服务端 mod</span>
          <el-switch
            :model-value="settings?.allow_server_mods ?? true"
            :loading="savingKey === 'allow_server_mods'"
            @change="(v: boolean) => toggleSetting('allow_server_mods', v)"
          />
        </div>
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

    <!-- 服务端 mod 白名单 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>服务端 mod 白名单</template>
      <div style="display: flex; gap: 8px; margin-bottom: 12px;">
        <el-input v-model="newName" placeholder="mod 名（唯一标识）" style="width: 220px;" />
        <el-input v-model="newNotes" placeholder="备注（可选）" style="width: 260px;" />
        <el-button type="primary" :loading="adding" @click="addSource">加入白名单</el-button>
      </div>
      <el-table :data="modSources" size="small" empty-text="暂无白名单">
        <el-table-column prop="name" label="mod 名" />
        <el-table-column prop="notes" label="备注" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click="removeSource(row.name)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 服务端源切换 -->
    <el-card>
      <template #header>切换某玩家的服务端上报源</template>
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
            placeholder="选白名单内源"
            style="width: 220px;"
          >
            <el-option v-for="n in whitelistedNames" :key="n" :label="n" :value="n" />
          </el-select>
        </div>
        <el-button type="primary" :loading="switching" style="width: fit-content;" @click="doSwitchServer">切换</el-button>
      </div>
    </el-card>
  </div>
</template>
