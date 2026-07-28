<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchMe, confirmBind, updateMyDisplayName, type MeResponse } from '../api/identity'
import {
  getMyConstructionSource,
  switchSelfSource,
  type DormantSource,
  type SourceMeResult,
} from '../api/construction'
import { extractApiError } from '../utils/error'

const router = useRouter()
const me = ref<MeResponse | null>(null)
const loading = ref(false)

// 绑定新身份对话框（game_init 方向：游戏内 !!PCH bind 出码 → Web 输码确认）
const showBindDialog = ref(false)
const bindCode = ref('')
const binding = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    me.value = await fetchMe()
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '加载失败')
  } finally {
    loading.value = false
  }
}

function openBindDialog(): void {
  bindCode.value = ''
  showBindDialog.value = true
}

async function onConfirmBind(): Promise<void> {
  const code = bindCode.value.trim()
  if (!code) {
    ElMessage.warning('请输入绑定码')
    return
  }
  binding.value = true
  try {
    const resp = await confirmBind(code)
    ElMessage.success(`绑定成功：${resp.player.name}`)
    showBindDialog.value = false
    await load()
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '绑定失败')
  } finally {
    binding.value = false
  }
}

function goToRegister(): void {
  router.push('/register')
}

function goToClaim(): void {
  router.push('/bind/claim')
}

// 昵称编辑（display_name = sheets 三端显示名主源；空则回退游戏名）
const editingName = ref(false)
const displayNameInput = ref('')
const savingName = ref(false)

function startEditName(): void {
  displayNameInput.value = me.value?.account.display_name ?? ''
  editingName.value = true
}

function cancelEditName(): void {
  editingName.value = false
}

async function saveDisplayName(): Promise<void> {
  const name = displayNameInput.value.trim()
  if (!name) {
    ElMessage.warning('昵称不能为空')
    return
  }
  savingName.value = true
  try {
    const resp = await updateMyDisplayName(name)
    if (me.value) {
      me.value = { ...me.value, account: resp.account }
    }
    editingName.value = false
    ElMessage.success('昵称已更新')
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '保存失败')
  } finally {
    savingName.value = false
  }
}

// 施工上报源（玩家自助切：server=官方代报 / local=客户端 mod）
const constructionSource = ref<SourceMeResult | null>(null)
const sourceMode = ref<'server' | 'local'>('server')
const sourceIdInput = ref('')
const switchingSource = ref(false)

// local 模式选项：存选中的 source_id，或哨兵 '__new__' 表示手填新 mod_id
const LOCAL_CHOICE_NEW = '__new__'
const localChoice = ref<string>(LOCAL_CHOICE_NEW)

// 休眠源列表（曾活跃、当前未活跃的 client_mod 源，可一键切回）
const dormantSources = computed<DormantSource[]>(
  () => constructionSource.value?.dormant_sources ?? [],
)

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN')
}

function describeSource(s: { source_type: string | null; source_id: string | null }): string {
  if (!s.source_type) return '无（当前无活跃上报源）'
  if (s.source_type === 'mcdr') return '官方方块追踪器'
  if (s.source_type === 'client_mod') return `客户端模组（${s.source_id ?? '-'}）`
  if (s.source_type === 'server_mod') return `服务端 mod（${s.source_id ?? '-'}）`
  return `${s.source_type}/${s.source_id ?? '-'}`
}

async function loadConstructionSource(): Promise<void> {
  try {
    constructionSource.value = await getMyConstructionSource()
    const a = constructionSource.value.active
    sourceMode.value = a.source_type === 'client_mod' ? 'local' : 'server'
    // 进入 local 模式时：active 是 client_mod → 选中其 source_id（即便不在 dormant 列表）；
    // 否则默认 '__new__' 让玩家手填
    localChoice.value = a.source_type === 'client_mod' ? (a.source_id ?? LOCAL_CHOICE_NEW) : LOCAL_CHOICE_NEW
    if (a.source_type === 'client_mod') sourceIdInput.value = a.source_id ?? ''
  } catch {
    // 辅助展示，失败静默
  }
}

async function onSwitchSource(): Promise<void> {
  // local 模式：localChoice === '__new__' 用 sourceIdInput（空则 warning）；
  // 否则用 localChoice（休眠源 source_id）
  let targetSourceId: string | null = null
  if (sourceMode.value === 'local') {
    if (localChoice.value === LOCAL_CHOICE_NEW) {
      const trimmed = sourceIdInput.value.trim()
      if (!trimmed) {
        ElMessage.warning('请输入 mod_id')
        return
      }
      targetSourceId = trimmed
    } else {
      targetSourceId = localChoice.value
    }
  }
  switchingSource.value = true
  try {
    await switchSelfSource({
      mode: sourceMode.value,
      source_id: targetSourceId,
    })
    ElMessage.success('已切换上报源')
    await loadConstructionSource()
  } catch (e: unknown) {
    ElMessage.error(extractApiError(e) ?? '切换失败')
  } finally {
    switchingSource.value = false
  }
}

onMounted(() => {
  load()
  loadConstructionSource()
})
</script>

<template>
  <div v-loading="loading">
    <!-- 临时账号引导横幅 -->
    <el-alert
      v-if="me?.account.is_temporary"
      title="当前是临时账号"
      type="warning"
      :closable="false"
      style="margin-bottom: 16px;"
    >
      <template #default>
        <p>请注册永久账号或绑定已有账号，避免数据丢失。</p>
        <el-space>
          <el-button type="primary" size="small" @click="goToRegister">注册永久账号</el-button>
          <el-button size="small" @click="goToClaim">绑定已有账号</el-button>
        </el-space>
      </template>
    </el-alert>

    <!-- 账号信息 + 绑定入口 -->
    <el-card style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>账号信息</span>
          <el-button type="primary" size="small" @click="openBindDialog">绑定新身份</el-button>
        </div>
      </template>
      <div v-if="me">
        <p><strong>账号 ID：</strong>{{ me.account.id }}</p>
        <p><strong>用户名：</strong>{{ me.account.username ?? '(未设置)' }}</p>
        <p>
          <strong>昵称：</strong>
          <span v-if="!editingName">{{ me.account.display_name ?? '(未设置，显示游戏名)' }}</span>
          <el-input
            v-else
            v-model="displayNameInput"
            size="small"
            style="width: 200px; margin-left: 8px;"
            maxlength="64"
            placeholder="用于项目贡献/拥有者显示"
            @keyup.enter="saveDisplayName"
          />
          <el-button v-if="!editingName" link type="primary" size="small" style="margin-left: 8px;" @click="startEditName">修改</el-button>
          <template v-else>
            <el-button type="primary" size="small" :loading="savingName" @click="saveDisplayName">保存</el-button>
            <el-button size="small" @click="cancelEditName">取消</el-button>
          </template>
        </p>
        <p><strong>角色：</strong>{{ me.account.role }}</p>
        <p><strong>类型：</strong>
          <el-tag :type="me.account.is_temporary ? 'warning' : 'success'" size="small">
            {{ me.account.is_temporary ? '临时账号' : '永久账号' }}
          </el-tag>
        </p>
      </div>
    </el-card>

    <!-- 绑定的游戏身份 -->
    <el-card header="绑定的游戏身份">
      <el-table v-if="me" :data="me.players" style="width: 100%;">
        <el-table-column prop="uuid" label="UUID" width="280" />
        <el-table-column prop="name" label="玩家名" width="160" />
        <el-table-column prop="role" label="角色" width="120" />
      </el-table>
      <el-empty v-if="me && me.players.length === 0" description="暂无绑定的游戏身份">
        <el-button type="primary" @click="openBindDialog">绑定新身份</el-button>
      </el-empty>
    </el-card>

    <!-- 施工上报源（玩家自助切：影响「由谁替你统计施工方块净放置」） -->
    <el-card header="施工上报源" style="margin-top: 16px;">
      <div v-if="constructionSource">
        <p>
          <strong>当前：</strong>{{ describeSource(constructionSource.active) }}
          <el-tag
            v-if="constructionSource.active.source_type === 'client_mod'"
            size="small"
            type="success"
            style="margin-left: 8px;"
          >当前活跃</el-tag>
          <el-tag v-if="constructionSource.active.is_default" size="small" type="info" style="margin-left: 8px;">默认</el-tag>
        </p>
        <el-divider style="margin: 12px 0;" />
        <p style="margin-bottom: 8px;"><strong>切换</strong>（切到客户端 mod 后，官方追踪器将不再替你统计）：</p>
        <div style="display: flex; flex-direction: column; gap: 8px;">
          <el-radio-group v-model="sourceMode">
            <el-radio value="server">使用服务器上报（官方追踪器代报）</el-radio>
            <el-radio value="local">使用本地上报（客户端 mod）</el-radio>
          </el-radio-group>
          <template v-if="sourceMode === 'local'">
            <!-- 有休眠源：列表 + 哨兵「新 mod_id」 -->
            <el-radio-group
              v-if="dormantSources.length > 0"
              v-model="localChoice"
              style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;"
            >
              <el-radio
                v-for="src in dormantSources"
                :key="src.source_id"
                :value="src.source_id"
              >{{ src.source_id }}（最近活跃：{{ formatTime(src.last_active_at) }}）</el-radio>
              <el-radio :value="LOCAL_CHOICE_NEW">使用新 mod_id...</el-radio>
            </el-radio-group>
            <!-- 选中「使用新 mod_id」或休眠源为空 → 展开手填 -->
            <el-input
              v-if="localChoice === LOCAL_CHOICE_NEW"
              v-model="sourceIdInput"
              placeholder="mod_id（客户端 mod 标识，由模组提供）"
              style="max-width: 360px;"
            />
          </template>
          <el-button type="primary" :loading="switchingSource" style="width: fit-content;" @click="onSwitchSource">切换</el-button>
        </div>
        <el-collapse v-if="constructionSource.history.length > 0" style="margin-top: 12px;">
          <el-collapse-item title="切换历史" name="history">
            <el-timeline>
              <el-timeline-item
                v-for="(h, idx) in constructionSource.history"
                :key="idx"
                :timestamp="h.switched_at"
                placement="top"
              >
                {{ (h.from_type ?? '无') }} → <strong>{{ h.to_type }}{{ h.to_id ? '/' + h.to_id : '' }}</strong>
                <span v-if="h.reason" style="color: #999;">（{{ h.reason }}）</span>
              </el-timeline-item>
            </el-timeline>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-card>

    <!-- 绑定新身份对话框（game_init：游戏 !!PCH bind 出码 → Web 输码） -->
    <el-dialog v-model="showBindDialog" title="绑定新游戏身份" width="460px">
      <p style="margin-bottom: 12px; color: #666;">
        请在游戏内执行 <code>!!PCH bind</code> 获取绑定码，然后输入下方完成绑定：
      </p>
      <el-form label-width="72px">
        <el-form-item label="绑定码">
          <el-input
            v-model="bindCode"
            placeholder="输入游戏内显示的 6 位短码"
            maxlength="6"
            @keyup.enter="onConfirmBind"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showBindDialog = false">取消</el-button>
        <el-button type="primary" :loading="binding" @click="onConfirmBind">确认绑定</el-button>
      </template>
    </el-dialog>
  </div>
</template>
