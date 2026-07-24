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
import { previewBatch } from '../../api/parsing'

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
  describe('previewBatch', () => {
    it('POST /parsing/batch 带 FormData（重复 files 字段）+ 180s timeout', async () => {
      const batch = {
        files: [
          {
            filename: 'a.litematic',
            kind: 'litematic',
            status: 'ok',
            preview: {
              meta: { filename: 'a.litematic', region_count: 1, total_blocks: 1, total_volume: 1 },
              blocks: [{ item_id: 'minecraft:stone', item_name: '石头', count: 1 }],
              container_items: [],
              untranslated: [],
            },
            error: null,
          },
        ],
      }
      mocked.post.mockResolvedValue({ data: batch })

      const f1 = new File([new Uint8Array([1])], 'a.litematic', { type: 'application/octet-stream' })
      const f2 = new File([new Uint8Array([2])], 'b.nbt', { type: 'application/octet-stream' })
      const result = await previewBatch([f1, f2])

      expect(mocked.post).toHaveBeenCalledTimes(1)
      const [url, body, config] = mocked.post.mock.calls[0]
      expect(url).toBe('/parsing/batch')
      expect(body).toBeInstanceOf(FormData)
      expect((body as FormData).getAll('files')).toEqual([f1, f2])
      expect(config).toEqual({
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 180000,
      })
      expect(result).toEqual(batch)
    })
  })
})
