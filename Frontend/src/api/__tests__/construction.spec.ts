import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock utils/http 模块（construction api 用到 get/post/patch/delete）
vi.mock('../../utils/http', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { http } from '../../utils/http'
import {
  getConstructionProgress,
  getConstructionSettings,
  updateConstructionSettings,
  createServerModSource,
  deleteServerModSource,
  switchSelfSource,
  getMyConstructionSource,
} from '../../api/construction'

const mocked = http as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('construction api', () => {
  it('getConstructionProgress hits GET /v1/construction/{id}/progress', async () => {
    const progress = {
      sheet_id: 42,
      account_totals: [],
      breakdown: [],
      material_completion: [
        { registry_id: 'minecraft:stone', item_name: '石头', need_qty: 10, net_qty: 5, completion_pct: 50.0 },
      ],
      timeline: [
        { account_id: 1, total_net: 5, recorded_at: '2026-07-27T00:00:00Z' },
      ],
    }
    mocked.get.mockResolvedValueOnce({ data: progress })
    const r = await getConstructionProgress(42)
    expect(mocked.get).toHaveBeenCalledWith('/v1/construction/42/progress')
    expect(r).toEqual(progress)
    expect(r.material_completion[0].completion_pct).toBe(50.0)
    expect(r.timeline[0].total_net).toBe(5)
  })

  it('getConstructionSettings returns 5 fields', async () => {
    const settings = {
      allow_client_mods: true,
      official_tracker_enabled: true,
      allow_server_mods: true,
      report_interval_seconds: 30,
      anti_cheat_threshold: null,
    }
    mocked.get.mockResolvedValueOnce({ data: settings })
    const r = await getConstructionSettings()
    expect(mocked.get).toHaveBeenCalledWith('/v1/construction/settings')
    expect(r.report_interval_seconds).toBe(30)
  })

  it('updateConstructionSettings sends partial PATCH', async () => {
    mocked.patch.mockResolvedValueOnce({ data: { allow_client_mods: false } })
    await updateConstructionSettings({ allow_client_mods: false })
    expect(mocked.patch).toHaveBeenCalledWith('/v1/construction/settings', { allow_client_mods: false })
  })

  it('createServerModSource posts name + notes', async () => {
    const entry = { name: 'srv-a', approved_by_uuid: null, approved_at: 't', notes: 'n' }
    mocked.post.mockResolvedValueOnce({ data: entry })
    const r = await createServerModSource('srv-a', 'n')
    expect(mocked.post).toHaveBeenCalledWith('/v1/construction/mod-sources', {
      name: 'srv-a',
      notes: 'n',
    })
    expect(r.name).toBe('srv-a')
  })

  it('createServerModSource defaults notes to null when omitted', async () => {
    mocked.post.mockResolvedValueOnce({ data: { name: 'x', approved_by_uuid: null, approved_at: 't', notes: null } })
    await createServerModSource('x')
    expect(mocked.post).toHaveBeenCalledWith('/v1/construction/mod-sources', {
      name: 'x',
      notes: null,
    })
  })

  it('deleteServerModSource DELETEs encoded name', async () => {
    mocked.delete.mockResolvedValueOnce({})
    await deleteServerModSource('srv a/b')
    expect(mocked.delete).toHaveBeenCalledWith('/v1/construction/mod-sources/srv%20a%2Fb')
  })

  it('switchSelfSource posts mode + source_id', async () => {
    mocked.post.mockResolvedValueOnce({
      data: { source_type: 'client_mod', source_id: 'm1', is_default: false },
    })
    const r = await switchSelfSource({ mode: 'local', source_id: 'm1' })
    expect(mocked.post).toHaveBeenCalledWith('/v1/construction/source/switch-self', {
      mode: 'local',
      source_id: 'm1',
    })
    expect(r.source_type).toBe('client_mod')
  })

  it('getMyConstructionSource returns active + history + dormant_sources', async () => {
    const me = {
      active: { source_type: 'mcdr', source_id: 'official', is_default: true },
      history: [],
      dormant_sources: [
        { source_id: 'old-mod', last_active_at: '2026-07-01T00:00:00Z' },
      ],
    }
    mocked.get.mockResolvedValueOnce({ data: me })
    const r = await getMyConstructionSource()
    expect(r.active.is_default).toBe(true)
    expect(r.history).toEqual([])
    expect(r.dormant_sources[0].source_id).toBe('old-mod')
  })
})
