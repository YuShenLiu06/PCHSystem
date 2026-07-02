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
  claimRow,
  setRowDelivery,
  contributeRow,
  setRowProgress,
  releaseRow,
  rejectRow,
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

// 新契约 fixture：SheetSummary/SheetDetail 含 owner_name；RowDetail 去掉 done_flag、
// 含 mode/status/claimant_uuid/claimant_name/delivered_qty。
const sheetSummary = {
  id: 1,
  owner_uuid: 'u',
  owner_name: 'Steve',
  title: 't',
  created_at: '',
  updated_at: '',
}

const rowDetail = {
  id: 10,
  item_name: 'iron',
  need_qty: 64,
  mode: 0,
  status: 'open',
  claimant_uuid: null,
  claimant_name: null,
  delivered_qty: 0,
  contributors: [],
  sort_order: 1,
  updated_at: '',
}

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
      const detail = { ...sheetSummary, rows: [] }
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
      const detail = { ...sheetSummary, title: '新建', rows: [] }
      mocked.post.mockResolvedValue({ data: detail })
      const result = await createSheet('新建')
      expect(mocked.post).toHaveBeenCalledWith('/sheets', { title: '新建' })
      expect(result).toEqual(detail)
    })
  })

  describe('patchSheet', () => {
    it('PATCH /sheets/{id} 带 title', async () => {
      const detail = { ...sheetSummary, title: '改', rows: [] }
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
    it('PUT /sheets/{id}/rows 带完整 body（mode 替代 done_flag）', async () => {
      mocked.put.mockResolvedValue({ data: rowDetail })
      const result = await upsertRow(1, {
        item_name: 'iron',
        need_qty: 64,
        mode: 0,
        sort_order: 1,
      })
      expect(mocked.put).toHaveBeenCalledWith('/sheets/1/rows', {
        item_name: 'iron',
        need_qty: 64,
        mode: 0,
        sort_order: 1,
      })
      expect(result).toEqual(rowDetail)
    })

    it('可省略可选字段（后端有默认值）', async () => {
      const minimal = { ...rowDetail, id: 11, item_name: 'gold', need_qty: 0, sort_order: 0 }
      mocked.put.mockResolvedValue({ data: minimal })
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

  describe('claimRow', () => {
    it('POST /sheets/{id}/rows/{rowId}/claim 无 body', async () => {
      const claimed = { ...rowDetail, status: 'claimed', claimant_uuid: 'me', claimant_name: 'Me' }
      mocked.post.mockResolvedValue({ data: claimed })
      const result = await claimRow(1, 10)
      expect(mocked.post).toHaveBeenCalledWith('/sheets/1/rows/10/claim')
      expect(result).toEqual(claimed)
    })
  })

  describe('setRowDelivery', () => {
    it('PATCH /sheets/{id}/rows/{rowId}/delivery 带 {delivered_qty}', async () => {
      const done = { ...rowDetail, status: 'done', delivered_qty: 64 }
      mocked.patch.mockResolvedValue({ data: done })
      const result = await setRowDelivery(1, 10, 64)
      expect(mocked.patch).toHaveBeenCalledWith('/sheets/1/rows/10/delivery', { delivered_qty: 64 })
      expect(result).toEqual(done)
    })
  })

  describe('contributeRow', () => {
    it('POST /sheets/{id}/rows/{rowId}/contribute 带 {qty}', async () => {
      const contributed = {
        ...rowDetail,
        mode: 1,
        status: 'claimed',
        delivered_qty: 32,
        contributors: [{ player_uuid: 'me', player_name: 'Me' }],
      }
      mocked.post.mockResolvedValue({ data: contributed })
      const result = await contributeRow(1, 10, 32)
      expect(mocked.post).toHaveBeenCalledWith('/sheets/1/rows/10/contribute', { qty: 32 })
      expect(result).toEqual(contributed)
    })
  })

  describe('setRowProgress', () => {
    it('PATCH /sheets/{id}/rows/{rowId}/progress 带 {delivered_qty}（绝对值）', async () => {
      const adjusted = {
        ...rowDetail,
        mode: 1,
        status: 'done',
        delivered_qty: 64,
        contributors: [{ player_uuid: 'me', player_name: 'Me' }],
      }
      mocked.patch.mockResolvedValue({ data: adjusted })
      const result = await setRowProgress(1, 10, 64)
      expect(mocked.patch).toHaveBeenCalledWith('/sheets/1/rows/10/progress', { delivered_qty: 64 })
      expect(result).toEqual(adjusted)
    })
  })

  describe('releaseRow', () => {
    it('POST /sheets/{id}/rows/{rowId}/release 无 body', async () => {
      const opened = { ...rowDetail, status: 'open' }
      mocked.post.mockResolvedValue({ data: opened })
      const result = await releaseRow(1, 10)
      expect(mocked.post).toHaveBeenCalledWith('/sheets/1/rows/10/release')
      expect(result).toEqual(opened)
    })
  })

  describe('rejectRow', () => {
    it('POST /sheets/{id}/rows/{rowId}/reject 无 body', async () => {
      const reClaimed = { ...rowDetail, status: 'claimed', delivered_qty: 0 }
      mocked.post.mockResolvedValue({ data: reClaimed })
      const result = await rejectRow(1, 10)
      expect(mocked.post).toHaveBeenCalledWith('/sheets/1/rows/10/reject')
      expect(result).toEqual(reClaimed)
    })
  })
})
