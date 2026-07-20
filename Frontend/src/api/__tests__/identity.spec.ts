import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock utils/http 模块
vi.mock('../../utils/http', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { http } from '../../utils/http'
import {
  exchangeToken,
  passwordLogin,
  fetchMe,
  register,
  getMyAccount,
  issueBindCode,
  confirmBind,
  claimBind,
} from '../../api/identity'

const mocked = http as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// Fixtures
const playerBrief = { uuid: 'uuid-123', name: 'Steve', role: 'player' }
const accountBrief = { id: 1, is_temporary: false, username: 'steve', role: 'user' }
const authResponse = {
  access_token: 'acc',
  refresh_token: 'ref',
  token_type: 'Bearer' as const,
  player: playerBrief,
  account: accountBrief,
}

describe('identity API client', () => {
  describe('exchangeToken', () => {
    it('POST /auth/exchange 带 token', async () => {
      mocked.post.mockResolvedValue({ data: authResponse })
      const result = await exchangeToken('one-time-token')
      expect(mocked.post).toHaveBeenCalledWith('/auth/exchange', { token: 'one-time-token' })
      expect(result).toEqual(authResponse)
    })
  })

  describe('passwordLogin', () => {
    it('POST /auth/login 带 username + password', async () => {
      mocked.post.mockResolvedValue({ data: authResponse })
      const result = await passwordLogin('steve', 'password123')
      expect(mocked.post).toHaveBeenCalledWith('/auth/login', { username: 'steve', password: 'password123' })
      expect(result).toEqual(authResponse)
    })
  })

  describe('fetchMe', () => {
    it('GET /me 返回账号 + 玩家列表 + active_uuid', async () => {
      const meResponse = {
        account: accountBrief,
        players: [playerBrief],
        active_uuid: 'uuid-123',
      }
      mocked.get.mockResolvedValue({ data: meResponse })
      const result = await fetchMe()
      expect(mocked.get).toHaveBeenCalledWith('/me')
      expect(result).toEqual(meResponse)
    })
  })

  describe('register', () => {
    it('POST /web-accounts/register 带 username + password', async () => {
      mocked.post.mockResolvedValue({ data: authResponse })
      const result = await register('newuser', 'pass12345')
      expect(mocked.post).toHaveBeenCalledWith('/web-accounts/register', {
        username: 'newuser',
        password: 'pass12345',
      })
      expect(result).toEqual(authResponse)
    })
  })

  describe('getMyAccount', () => {
    it('GET /web-accounts/me 返回账号 + 玩家列表', async () => {
      const myAccountResponse = {
        account: accountBrief,
        players: [playerBrief],
      }
      mocked.get.mockResolvedValue({ data: myAccountResponse })
      const result = await getMyAccount()
      expect(mocked.get).toHaveBeenCalledWith('/web-accounts/me')
      expect(result).toEqual(myAccountResponse)
    })
  })

  describe('issueBindCode', () => {
    it('POST /bind/issue 返回 short_code + expires_in', async () => {
      const bindCodeResponse = { short_code: 'ABC123', expires_in: 300 }
      mocked.post.mockResolvedValue({ data: bindCodeResponse })
      const result = await issueBindCode()
      expect(mocked.post).toHaveBeenCalledWith('/bind/issue')
      expect(result).toEqual(bindCodeResponse)
    })
  })

  describe('confirmBind', () => {
    it('POST /bind/confirm 带 short_code', async () => {
      const bindResult = { player: playerBrief, account: accountBrief }
      mocked.post.mockResolvedValue({ data: bindResult })
      const result = await confirmBind('ABC123')
      expect(mocked.post).toHaveBeenCalledWith('/bind/confirm', { short_code: 'ABC123' })
      expect(result).toEqual(bindResult)
    })
  })

  describe('claimBind', () => {
    it('POST /bind/claim 带 username + password', async () => {
      mocked.post.mockResolvedValue({ data: authResponse })
      const result = await claimBind('steve', 'password123')
      expect(mocked.post).toHaveBeenCalledWith('/bind/claim', { username: 'steve', password: 'password123' })
      expect(result).toEqual(authResponse)
    })
  })
})
