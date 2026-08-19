import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock utils/http 模块（scoring api 用到 get/post）
vi.mock('../../utils/http', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { http } from '../../utils/http'
import { fetchLedger, adminAdjust, searchScoringPlayers } from '../../api/scoring'

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

describe('scoring api', () => {
  it('fetchLedger hits GET /v1/scoring/ledger with snake_case query', async () => {
    const page = {
      items: [
        {
          id: 1,
          account_id: 7,
          delta: '-3.00',
          reason: 'manual_adj',
          balance_after: '9.00',
          sheet_id: null,
          operator_uuid: null,
          idempotency_key: null,
          note: '误发回收',
          created_at: '2026-08-19T10:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      limit: 50,
    }
    mocked.get.mockResolvedValueOnce({ data: page })
    const r = await fetchLedger({
      player_uuid: '550e8400-e29b-41d4-a716-446655440000',
      since: '2026-08-01T00:00:00Z',
      until: '2026-09-01T00:00:00Z',
      page: 1,
      limit: 50,
    })
    expect(mocked.get).toHaveBeenCalledWith('/v1/scoring/ledger', {
      params: {
        player_uuid: '550e8400-e29b-41d4-a716-446655440000',
        since: '2026-08-01T00:00:00Z',
        until: '2026-09-01T00:00:00Z',
        page: 1,
        limit: 50,
      },
    })
    expect(r.total).toBe(1)
    expect(r.items[0].delta).toBe('-3.00')
  })

  it('adminAdjust posts single-item batch to /v1/scoring/admin/adjust', async () => {
    const result = {
      results: [
        {
          player_uuid: '550e8400-e29b-41d4-a716-446655440000',
          accepted: true,
          idempotent_replay: false,
          entry: null,
          skip_reason: null,
        },
      ],
      accepted_count: 1,
      skipped_count: 0,
    }
    mocked.post.mockResolvedValueOnce({ data: result })
    const r = await adminAdjust({
      items: [
        {
          player_uuid: '550e8400-e29b-41d4-a716-446655440000',
          amount: '3',
          reason: 'manual_adj',
          note: '误发回收',
        },
      ],
      notify: true,
      allow_overdraft: false,
    })
    expect(mocked.post).toHaveBeenCalledWith('/v1/scoring/admin/adjust', {
      items: [
        {
          player_uuid: '550e8400-e29b-41d4-a716-446655440000',
          amount: '3',
          reason: 'manual_adj',
          note: '误发回收',
        },
      ],
      notify: true,
      allow_overdraft: false,
    })
    expect(r.accepted_count).toBe(1)
  })

  it('searchScoringPlayers hits GET /v1/scoring/admin/players', async () => {
    const players = [
      {
        player_uuid: '550e8400-e29b-41d4-a716-446655440000',
        player_name: 'alice',
        display_name: '爱丽丝',
      },
    ]
    mocked.get.mockResolvedValueOnce({ data: players })
    const r = await searchScoringPlayers('ali')
    expect(mocked.get).toHaveBeenCalledWith('/v1/scoring/admin/players', {
      params: { q: 'ali', limit: 10 },
    })
    expect(r[0].player_name).toBe('alice')
  })
})
