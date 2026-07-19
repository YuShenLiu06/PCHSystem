<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchMe, confirmBind, updateMyDisplayName, type MeResponse } from '../api/identity'
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

onMounted(load)
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
