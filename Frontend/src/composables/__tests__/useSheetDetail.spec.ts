// useSheetDetail 协管员（manager，迁移 0014）分支单测。
// 覆盖 cr MEDIUM：canManage/canEdit tier 分流、onGrantManager「未选中下拉→警告」分支、
// grant/revoke 成功后 managers 列表刷新与输入重置。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref, type Ref } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// element-plus mock：捕获 ElMessage 调用 + 避免 jsdom 渲染组件。
// vi.hoisted 让 spy 与 vi.mock 工厂同处 hoist 区，工厂内可引用。
const elMessage = vi.hoisted(() => ({
  warning: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: elMessage,
  ElMessageBox: { prompt: vi.fn(), confirm: vi.fn() },
}))

// api/sheets mock：全部函数为 spy；getSheet/grant/revoke 按用例覆写返回值。
vi.mock('../../api/sheets', () => ({
  getSheet: vi.fn(),
  patchSheet: vi.fn(),
  upsertRow: vi.fn(),
  deleteRow: vi.fn(),
  claimRow: vi.fn(),
  setRowDelivery: vi.fn(),
  contributeRow: vi.fn(),
  setRowProgress: vi.fn(),
  releaseRow: vi.fn(),
  rejectRow: vi.fn(),
  advanceSheet: vi.fn(),
  grantSheetManager: vi.fn(),
  revokeSheetManager: vi.fn(),
  searchPlayers: vi.fn(),
}))

// 必须在 vi.mock 之后导入
import { useAuthStore } from '../../stores/auth'
import { useSheetDetail, type UseSheetDetailHandle } from '../useSheetDetail'
import * as sheetsApi from '../../api/sheets'
import type { SheetDetail, SheetManagerEntry } from '../../api/sheets'

const api = sheetsApi as unknown as Record<string, ReturnType<typeof vi.fn>>

function makeSheet(over: Partial<SheetDetail> = {}): SheetDetail {
  return {
    id: 1,
    owner_uuid: 'owner-uuid',
    owner_name: 'owner',
    title: 'T',
    status: 'collecting',
    archived_path: null,
    archived_at: null,
    created_at: '',
    updated_at: '',
    rows: [],
    viewer_uuids: [],
    managers: [],
    ...over,
  } as SheetDetail
}

interface MountOpts {
  /** 当前玩家 uuid（auth.player.uuid） */
  uuid?: string
  /** 当前玩家全局角色（user/admin/owner） */
  role?: string
  /** 项目拥有者 uuid */
  ownerUuid?: string
  /** 当前查看者 account 下的 UUID 集（viewer_uuids，含当前 uuid） */
  viewerUuids?: string[]
  /** 项目既有协管员列表 */
  managers?: SheetManagerEntry[]
}

let cleanup: (() => void) | null = null

function mountDetail(opts: MountOpts = {}): UseSheetDetailHandle {
  const auth = useAuthStore()
  // Pinia 允许直接置 state（测试用），构造当前玩家身份
  auth.player = { uuid: opts.uuid ?? 'me', name: opts.uuid ?? 'me', role: opts.role ?? 'user' }
  api.getSheet.mockResolvedValue(
    makeSheet({
      owner_uuid: opts.ownerUuid ?? 'owner-uuid',
      viewer_uuids: opts.viewerUuids ?? [],
      managers: opts.managers ?? [],
    }),
  )
  let handle!: UseSheetDetailHandle
  const sheetId: Ref<number> = ref(1)
  const sheetTableRef = ref(null)
  const wrapper = mount(
    defineComponent({
      name: 'SheetDetailHost',
      setup() {
        handle = useSheetDetail({ sheetId, auth, sheetTableRef })
        return () => null
      },
    }),
  )
  cleanup = () => wrapper.unmount()
  return handle
}

beforeEach(() => {
  setActivePinia(createPinia())
  elMessage.warning.mockClear()
  elMessage.success.mockClear()
  elMessage.error.mockClear()
  api.getSheet.mockReset()
  api.grantSheetManager.mockReset()
  api.revokeSheetManager.mockReset()
})

afterEach(() => {
  cleanup?.()
  cleanup = null
})

describe('useSheetDetail · 协管员权限与授权', () => {
  describe('canManage / canEdit tier 分流', () => {
    it('项目 owner：canManage 与 canEdit 均 true（tier A+B 全开）', async () => {
      const handle = mountDetail({
        uuid: 'owner-uuid',
        role: 'user',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['owner-uuid'],
      })
      await flushPromises()
      expect(handle.canManage.value).toBe(true)
      expect(handle.canEdit.value).toBe(true)
    })

    it('全局 admin：canManage 与 canEdit 均 true（超管覆盖一切）', async () => {
      const handle = mountDetail({ uuid: 'someone-else', role: 'admin', ownerUuid: 'owner-uuid' })
      await flushPromises()
      expect(handle.canManage.value).toBe(true)
      expect(handle.canEdit.value).toBe(true)
    })

    it('协管员：canManage=false、canEdit=true（tier B 继承，tier A 不可见）', async () => {
      const handle = mountDetail({
        uuid: 'mgr-uuid',
        role: 'user',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['mgr-uuid'],
        managers: [
          {
            web_account_id: 200,
            display_name: 'mgr',
            member_uuids: ['mgr-uuid'],
            granted_at: '',
          },
        ],
      })
      await flushPromises()
      expect(handle.canManage.value).toBe(false)
      expect(handle.canEdit.value).toBe(true)
      expect(handle.isManager.value).toBe(true)
    })

    it('普通玩家：canManage=false、canEdit=false（无任何管理权）', async () => {
      const handle = mountDetail({ uuid: 'plain', role: 'user', ownerUuid: 'owner-uuid' })
      await flushPromises()
      expect(handle.canManage.value).toBe(false)
      expect(handle.canEdit.value).toBe(false)
      expect(handle.isManager.value).toBe(false)
    })

    it('archived 终态：协管员仍可计算 tier，但 isReadOnly=true（前端可见性，真实拒绝靠后端 409）', async () => {
      const handle = mountDetail({
        uuid: 'mgr-uuid',
        role: 'user',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['mgr-uuid'],
        managers: [
          {
            web_account_id: 200,
            display_name: 'mgr',
            member_uuids: ['mgr-uuid'],
            granted_at: '',
          },
        ],
      })
      await flushPromises()
      // 直接构造归档态：canEdit 仍 true（仅可见性），isReadOnly 拦截实际操作
      handle.sheet.value = { ...(handle.sheet.value as SheetDetail), status: 'archived' }
      expect(handle.canEdit.value).toBe(true)
      expect(handle.isReadOnly.value).toBe(true)
    })
  })

  describe('onGrantManager', () => {
    it('未从下拉选中（pickedUuid 为空）→ 警告且不调 API', async () => {
      const handle = mountDetail({
        uuid: 'owner-uuid',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['owner-uuid'],
      })
      await flushPromises()
      handle.managerPickedUuid.value = ''
      handle.managerInputName.value = 'bob' // 输入了文字但没从下拉选中
      await handle.onGrantManager()
      expect(elMessage.warning).toHaveBeenCalledWith('请从下拉列表选择玩家')
      expect(api.grantSheetManager).not.toHaveBeenCalled()
    })

    it('选中后授予 → 调 API + 刷新 managers + 清空输入', async () => {
      const handle = mountDetail({
        uuid: 'owner-uuid',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['owner-uuid'],
      })
      await flushPromises()
      const granted: SheetManagerEntry[] = [
        {
          web_account_id: 300,
          display_name: 'bob',
          member_uuids: ['bob-uuid'],
          granted_at: 't',
        },
      ]
      api.grantSheetManager.mockResolvedValue(granted)
      handle.managerPickedUuid.value = 'bob-uuid'
      handle.managerInputName.value = 'bob'
      await handle.onGrantManager()
      expect(api.grantSheetManager).toHaveBeenCalledWith(1, 'bob-uuid')
      expect(handle.sheet.value?.managers).toEqual(granted)
      expect(handle.managerPickedUuid.value).toBe('')
      expect(handle.managerInputName.value).toBe('')
      expect(elMessage.success).toHaveBeenCalledWith('已添加协管员')
    })
  })

  describe('onRevokeManager', () => {
    it('撤销 → 调 API（body web_account_id）+ 刷新 managers（列表置空）', async () => {
      const handle = mountDetail({
        uuid: 'owner-uuid',
        ownerUuid: 'owner-uuid',
        viewerUuids: ['owner-uuid'],
        managers: [
          {
            web_account_id: 300,
            display_name: 'bob',
            member_uuids: ['bob-uuid'],
            granted_at: 't',
          },
        ],
      })
      await flushPromises()
      api.revokeSheetManager.mockResolvedValue([])
      await handle.onRevokeManager(300)
      expect(api.revokeSheetManager).toHaveBeenCalledWith(1, 300)
      expect(handle.sheet.value?.managers).toEqual([])
      expect(elMessage.success).toHaveBeenCalledWith('已移除协管员')
    })
  })
})
