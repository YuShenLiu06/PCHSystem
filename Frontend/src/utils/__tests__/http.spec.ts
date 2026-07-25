import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosError } from 'axios'

// vi.mock 工厂不能引用非 hoisted 变量 → 用 vi.hoisted 提前创建 spy / 可变状态
const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  // router.currentRoute 是 Ref；拦截器读 router.currentRoute.value.meta.public 判公开页
  currentRoute: { value: { path: '/me', meta: {} } },
  clear: vi.fn(),
  set: vi.fn(),
  // 受保护请求判定 = 有 accessToken（请求拦截器据此加 Bearer）；refresh 触发还要有 refreshToken
  accessToken: '',
  refreshToken: '',
  elMessage: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}))

vi.mock('../../router', () => ({
  router: { currentRoute: mocks.currentRoute, push: mocks.push },
}))
vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({
    accessToken: mocks.accessToken,
    refreshToken: mocks.refreshToken,
    set: mocks.set,
    clear: mocks.clear,
  }),
}))
vi.mock('element-plus', () => ({ ElMessage: mocks.elMessage }))

import { http } from '../http'

/** 构造一个只含拦截器所读字段的 AxiosError（response.status / response.data.detail）。 */
function rejectWith(status: number, detail = 'err'): AxiosError {
  return { response: { status, data: { detail } } } as AxiosError
}

/** refresh 成功响应体（与 Backend TokenExchangeResponse 对齐）。 */
function refreshResp() {
  return {
    access_token: 'acc2',
    refresh_token: 'ref2',
    token_type: 'Bearer' as const,
    player: { uuid: 'u-1', name: 'Steve', role: 'user' },
    account: {
      id: 1,
      is_temporary: false,
      username: 'steve',
      display_name: '史蒂夫',
      role: 'user',
    },
  }
}

/** 带 config 的 401 拒绝（让拦截器能读 url 等判定是否可续签）。 */
function reject401(config: unknown, detail = 'expired') {
  return Promise.reject({ config, response: { status: 401, data: { detail } } })
}

describe('http 响应拦截器 · 401 处理（RS-5 + #34 公开页特例）', () => {
  beforeEach(() => {
    mocks.push.mockReset()
    mocks.clear.mockReset()
    mocks.set.mockReset()
    mocks.elMessage.error.mockReset()
    mocks.accessToken = ''
    mocks.refreshToken = ''
    mocks.currentRoute.value = { path: '/me', meta: {} }
  })

  async function fire401(): Promise<void> {
    http.defaults.adapter = (() => Promise.reject(rejectWith(401))) as AxiosAdapter
    await expect(http.get('/x')).rejects.toBeTruthy()
  }

  it('受保护页（/me）401 → clear 登录态 + push /auth（RS-5）', async () => {
    mocks.currentRoute.value = { path: '/me', meta: {} }
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).toHaveBeenCalledWith('/auth')
  })

  it('公开页 /auth 401（token 兑换失败）→ 仍 clear 但不 push（避免推回当前页，#34）', async () => {
    mocks.currentRoute.value = { path: '/auth', meta: { public: true } }
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('公开页 /login 401（密码错误）→ 仍 clear 但不 push（避免 /login→/auth→/login 清空表单 bounce）', async () => {
    mocks.currentRoute.value = { path: '/login', meta: { public: true } }
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('公开页 /register 401 → 仍 clear 但不 push（同 /login）', async () => {
    mocks.currentRoute.value = { path: '/register', meta: { public: true } }
    await fire401()
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).not.toHaveBeenCalled()
  })
})

describe('http 响应拦截器 · 401 自动 refresh 续签重放（#42）', () => {
  beforeEach(() => {
    mocks.push.mockReset()
    mocks.clear.mockReset()
    mocks.set.mockReset()
    mocks.elMessage.error.mockReset()
    mocks.accessToken = ''
    mocks.refreshToken = ''
    mocks.currentRoute.value = { path: '/me', meta: {} }
  })

  it('受保护页 401 + 有 refresh → 续签一次 + 重放原请求，clear 不触发', async () => {
    mocks.accessToken = 'acc'
    mocks.refreshToken = 'ref'
    let xCalls = 0
    let refreshCalls = 0
    http.defaults.adapter = (async (config: {
      url?: string
    }) => {
      if (config.url === '/auth/refresh') {
        refreshCalls++
        return { data: refreshResp() }
      }
      if (config.url === '/x') {
        xCalls++
        if (xCalls === 1) return reject401(config)
        return { data: 'ok' }
      }
      return Promise.reject({ config, response: { status: 500 } })
    }) as AxiosAdapter

    const res = await http.get('/x')
    expect(res.data).toBe('ok')
    expect(xCalls).toBe(2) // 原请求 + 续签后重放
    expect(refreshCalls).toBe(1)
    expect(mocks.set).toHaveBeenCalledWith(
      { access_token: 'acc2', refresh_token: 'ref2' },
      expect.any(Object),
      expect.any(Object),
    )
    expect(mocks.clear).not.toHaveBeenCalled()
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('并发 401 去重——两个请求共享一次 refresh，各自重放', async () => {
    mocks.accessToken = 'acc'
    mocks.refreshToken = 'ref'
    let refreshCalls = 0
    let xCalls = 0
    let yCalls = 0
    http.defaults.adapter = (async (config: { url?: string }) => {
      if (config.url === '/auth/refresh') {
        refreshCalls++
        await new Promise((r) => setTimeout(r, 20)) // 拉宽并发窗口
        return { data: refreshResp() }
      }
      if (config.url === '/x') {
        xCalls++
        if (xCalls === 1) return reject401(config)
        return { data: 'ok-x' }
      }
      if (config.url === '/y') {
        yCalls++
        if (yCalls === 1) return reject401(config)
        return { data: 'ok-y' }
      }
      return Promise.reject({ config, response: { status: 500 } })
    }) as AxiosAdapter

    const [r1, r2] = await Promise.all([http.get('/x'), http.get('/y')])
    expect(r1.data).toBe('ok-x')
    expect(r2.data).toBe('ok-y')
    expect(refreshCalls).toBe(1)
    expect(xCalls).toBe(2)
    expect(yCalls).toBe(2)
    expect(mocks.set).toHaveBeenCalledTimes(1)
    expect(mocks.clear).not.toHaveBeenCalled()
  })

  it('凭证端点（/auth/login）401 即便有 access+refresh 也不续签——避免 wrong password→refresh→重试死循环', async () => {
    mocks.accessToken = 'acc'
    mocks.refreshToken = 'ref'
    mocks.currentRoute.value = { path: '/login', meta: { public: true } }
    let refreshCalls = 0
    http.defaults.adapter = ((config: { url?: string }) => {
      if (config.url === '/auth/refresh') {
        refreshCalls++
        return { data: refreshResp() }
      }
      if (config.url === '/auth/login') return reject401(config, 'bad credentials')
      return Promise.reject({ config, response: { status: 500 } })
    }) as AxiosAdapter

    await expect(
      http.post('/auth/login', { username: 'x', password: 'y' }),
    ).rejects.toBeTruthy()
    expect(refreshCalls).toBe(0)
    expect(mocks.set).not.toHaveBeenCalled()
  })

  it('/auth/refresh 自身 401（refresh token 失效）→ 不递归续签、clear、reject', async () => {
    mocks.accessToken = 'acc'
    mocks.refreshToken = 'ref-stale'
    let refreshCalls = 0
    http.defaults.adapter = ((config: { url?: string }) => {
      if (config.url === '/auth/refresh') {
        refreshCalls++
        return reject401(config, 'invalid refresh')
      }
      // 任一鉴权请求先 401 触发 refresh，refresh 再 401
      return reject401(config)
    }) as AxiosAdapter

    await expect(http.get('/x')).rejects.toBeTruthy()
    expect(refreshCalls).toBe(1) // 只调一次，不递归
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.set).not.toHaveBeenCalled()
  })

  it('无 refresh token → 不续签、走原 401 清场 + 跳 /auth', async () => {
    mocks.accessToken = 'acc'
    mocks.refreshToken = ''
    let refreshCalls = 0
    http.defaults.adapter = ((config: { url?: string }) => {
      if (config.url === '/auth/refresh') {
        refreshCalls++
        return { data: refreshResp() }
      }
      return reject401(config)
    }) as AxiosAdapter

    await expect(http.get('/x')).rejects.toBeTruthy()
    expect(refreshCalls).toBe(0)
    expect(mocks.clear).toHaveBeenCalledTimes(1)
    expect(mocks.push).toHaveBeenCalledWith('/auth')
  })
})
