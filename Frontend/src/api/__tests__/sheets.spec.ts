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
import {
  listSheets,
  getSheet,
  createSheet,
  patchSheet,
  deleteSheet,
  upsertRow,
  deleteRow,
  exportSheetCSV,
  exportAllCSV,
} from '../../api/sheets'

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

describe('sheets API client', () => {
  describe('listSheets', () => {
    it('无参数时不带 query', async () => {
      mocked.get.mockResolvedValue({ data: [] })
      await listSheets()
      expect(mocked.get).toHaveBeenCalledWith('/sheets', { params: undefined })
    })

    it('owner=me 时带 query', async () => {
      mocked.get.mockResolvedValue({ data: [] })
      await listSheets('me')
      expect(mocked.get).toHaveBeenCalledWith('/sheets', { params: { owner: 'me' } })
    })
  })

  describe('getSheet', () => {
    it('默认返回 SheetDetail JSON', async () => {
      const detail = { id: 1, owner_uuid: 'u', title: 't', created_at: '', updated_at: '', rows: [] }
      mocked.get.mockResolvedValue({ data: detail })
      const result = await getSheet(1)
      expect(mocked.get).toHaveBeenCalledWith('/sheets/1', { params: undefined })
      expect(result).toEqual(detail)
    })

    it('format=csv 返回字符串', async () => {
      mocked.get.mockResolvedValue({ data: 'csv-data' })
      const result = await getSheet(2, 'csv')
      expect(mocked.get).toHaveBeenCalledWith('/sheets/2', { params: { format: 'csv' } })
      expect(result).toBe('csv-data')
    })
  })

  describe('createSheet', () => {
    it('POST /sheets 带 title', async () => {
      const detail = { id: 1, owner_uuid: 'u', title: '新建', created_at: '', updated_at: '', rows: [] }
      mocked.post.mockResolvedValue({ data: detail })
      const result = await createSheet('新建')
      expect(mocked.post).toHaveBeenCalledWith('/sheets', { title: '新建' })
      expect(result).toEqual(detail)
    })
  })

  describe('patchSheet', () => {
    it('PATCH /sheets/{id} 带 title', async () => {
      const detail = { id: 1, owner_uuid: 'u', title: '改', created_at: '', updated_at: '', rows: [] }
      mocked.patch.mockResolvedValue({ data: detail })
      await patchSheet(1, '改')
      expect(mocked.patch).toHaveBeenCalledWith('/sheets/1', { title: '改' })
    })
  })

  describe('deleteSheet', () => {
    it('DELETE /sheets/{id}', async () => {
      mocked.delete.mockResolvedValue({ data: '' })
      await deleteSheet(1)
      expect(mocked.delete).toHaveBeenCalledWith('/sheets/1')
    })
  })

  describe('upsertRow', () => {
    it('PUT /sheets/{id}/rows 带完整 body', async () => {
      const row = { id: 10, item_name: 'iron', need_qty: 64, done_flag: 0, sort_order: 1, updated_at: '' }
      mocked.put.mockResolvedValue({ data: row })
      const result = await upsertRow(1, { item_name: 'iron', need_qty: 64, done_flag: 0, sort_order: 1 })
      expect(mocked.put).toHaveBeenCalledWith('/sheets/1/rows', {
        item_name: 'iron',
        need_qty: 64,
        done_flag: 0,
        sort_order: 1,
      })
      expect(result).toEqual(row)
    })

    it('可省略可选字段（后端有默认值）', async () => {
      const row = { id: 11, item_name: 'gold', need_qty: 0, done_flag: 0, sort_order: 0, updated_at: '' }
      mocked.put.mockResolvedValue({ data: row })
      await upsertRow(2, { item_name: 'gold' })
      expect(mocked.put).toHaveBeenCalledWith('/sheets/2/rows', { item_name: 'gold' })
    })
  })

  describe('deleteRow', () => {
    it('DELETE /sheets/{id}/rows/{rowId}', async () => {
      mocked.delete.mockResolvedValue({ data: '' })
      await deleteRow(1, 10)
      expect(mocked.delete).toHaveBeenCalledWith('/sheets/1/rows/10')
    })
  })

  describe('exportSheetCSV', () => {
    it('等价 getSheet(id, "csv")', async () => {
      mocked.get.mockResolvedValue({ data: 'csv-text' })
      const result = await exportSheetCSV(3)
      expect(mocked.get).toHaveBeenCalledWith('/sheets/3', { params: { format: 'csv' } })
      expect(result).toBe('csv-text')
    })
  })

  describe('exportAllCSV', () => {
    it('GET /sheets/export 全量 CSV', async () => {
      mocked.get.mockResolvedValue({ data: 'all-csv' })
      const result = await exportAllCSV()
      expect(mocked.get).toHaveBeenCalledWith('/sheets/export')
      expect(result).toBe('all-csv')
    })
  })
})
