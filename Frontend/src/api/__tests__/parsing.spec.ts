import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock utils/http 模块，避免真实网络请求
vi.mock('../../utils/http', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

// 导入必须在 mock 之后
import { http } from '../../utils/http'
import { previewLitematic, previewNbt } from '../../api/parsing'

const mocked = http as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  put: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('parsing API client', () => {
  describe('previewLitematic', () => {
    it('POST /parsing/litematic 带 FormData（含 file）', async () => {
      const preview = {
        meta: { filename: 'x.litematic', region_count: 1, total_blocks: 2, total_volume: 3 },
        blocks: [{ item_id: 'minecraft:stone', item_name: '石头', count: 2 }],
        container_items: [],
        untranslated: [],
      }
      mocked.post.mockResolvedValue({ data: preview })

      const file = new File([new Uint8Array([1, 2, 3])], 'x.litematic', {
        type: 'application/octet-stream',
      })
      const result = await previewLitematic(file)

      expect(mocked.post).toHaveBeenCalledTimes(1)
      const [url, body, config] = mocked.post.mock.calls[0]
      expect(url).toBe('/parsing/litematic')
      expect(body).toBeInstanceOf(FormData)
      expect((body as FormData).get('file')).toBe(file)
      expect(config).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } })
      expect(result).toEqual(preview)
    })
  })

  describe('previewNbt', () => {
    it('POST /parsing/nbt 带 FormData（含 file）', async () => {
      const preview = {
        meta: { filename: 'x.nbt', region_count: 1, total_blocks: 2, total_volume: 3 },
        blocks: [{ item_id: 'create:item_vault', item_name: '物品保管库', count: 2 }],
        container_items: [],
        untranslated: [],
      }
      mocked.post.mockResolvedValue({ data: preview })

      const file = new File([new Uint8Array([1, 2, 3])], 'x.nbt', {
        type: 'application/octet-stream',
      })
      const result = await previewNbt(file)

      expect(mocked.post).toHaveBeenCalledTimes(1)
      const [url, body, config] = mocked.post.mock.calls[0]
      expect(url).toBe('/parsing/nbt')
      expect(body).toBeInstanceOf(FormData)
      expect((body as FormData).get('file')).toBe(file)
      expect(config).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } })
      expect(result).toEqual(preview)
    })
  })
})
