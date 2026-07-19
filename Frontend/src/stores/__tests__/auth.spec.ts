import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '../auth'
import type { AccountBrief, PlayerBrief } from '../../api/identity'

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value
    },
    removeItem: (key: string) => {
      delete store[key]
    },
    clear: () => {
      store = {}
    },
  }
})()

vi.stubGlobal('localStorage', localStorageMock)

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorageMock.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const player: PlayerBrief = { uuid: 'uuid-123', name: 'Steve', role: 'player' }
  const account: AccountBrief = { id: 1, is_temporary: false, username: 'steve', role: 'user' }
  const tokens = { access_token: 'acc', refresh_token: 'ref' }

  describe('初始状态', () => {
    it('localStorage 为空时初始为默认空状态', () => {
      const store = useAuthStore()
      expect(store.accessToken).toBe('')
      expect(store.refreshToken).toBe('')
      expect(store.player).toBeNull()
      expect(store.account).toBeNull()
      expect(store.isAuthenticated).toBe(false)
      expect(store.isTemporaryAccount).toBe(false)
    })

    it('localStorage 有值时从 localStorage 恢复状态', () => {
      localStorageMock.setItem('access_token', tokens.access_token)
      localStorageMock.setItem('refresh_token', tokens.refresh_token)
      localStorageMock.setItem('player', JSON.stringify(player))
      localStorageMock.setItem('account', JSON.stringify(account))

      setActivePinia(createPinia())
      const store = useAuthStore()

      expect(store.accessToken).toBe(tokens.access_token)
      expect(store.refreshToken).toBe(tokens.refresh_token)
      expect(store.player).toEqual(player)
      expect(store.account).toEqual(account)
      expect(store.isAuthenticated).toBe(true)
    })
  })

  describe('set', () => {
    it('设置 tokens + player + account 并同步到 localStorage', () => {
      const store = useAuthStore()
      store.set(tokens, player, account)

      expect(store.accessToken).toBe(tokens.access_token)
      expect(store.refreshToken).toBe(tokens.refresh_token)
      expect(store.player).toEqual(player)
      expect(store.account).toEqual(account)
      expect(localStorageMock.getItem('access_token')).toBe(tokens.access_token)
      expect(localStorageMock.getItem('refresh_token')).toBe(tokens.refresh_token)
      expect(localStorageMock.getItem('player')).toBe(JSON.stringify(player))
      expect(localStorageMock.getItem('account')).toBe(JSON.stringify(account))
    })

    it('isTemporaryAccount getter 正确反映 account.is_temporary', () => {
      const store = useAuthStore()

      store.set(tokens, player, { ...account, is_temporary: true })
      expect(store.isTemporaryAccount).toBe(true)

      store.set(tokens, player, { ...account, is_temporary: false })
      expect(store.isTemporaryAccount).toBe(false)

      store.set(tokens, player, { ...account, is_temporary: false })
      expect(store.isTemporaryAccount).toBe(false)
    })
  })

  describe('clear', () => {
    it('清空状态并同步到 localStorage', () => {
      const store = useAuthStore()
      store.set(tokens, player, account)

      store.clear()

      expect(store.accessToken).toBe('')
      expect(store.refreshToken).toBe('')
      expect(store.player).toBeNull()
      expect(store.account).toBeNull()
      expect(localStorageMock.getItem('access_token')).toBeNull()
      expect(localStorageMock.getItem('refresh_token')).toBeNull()
      expect(localStorageMock.getItem('player')).toBeNull()
      expect(localStorageMock.getItem('account')).toBeNull()
    })
  })

  describe('isAuthenticated getter', () => {
    it('有 accessToken 时为 true', () => {
      const store = useAuthStore()
      expect(store.isAuthenticated).toBe(false)

      store.set(tokens, player, account)
      expect(store.isAuthenticated).toBe(true)
    })

    it('无 accessToken 时为 false', () => {
      const store = useAuthStore()
      store.set(tokens, player, account)

      store.clear()
      expect(store.isAuthenticated).toBe(false)
    })
  })
})
