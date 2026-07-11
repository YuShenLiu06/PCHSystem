import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSheetStore } from '../sheet'
import type { SheetDetail } from '../../api/sheets'

// 构造最小 SheetDetail（store 只关心 id + 引用，rows 留空即可）
function makeDetail(id: number, title: string): SheetDetail {
  return {
    id,
    owner_uuid: 'u1',
    owner_name: 'owner',
    title,
    status: 'collecting',
    archived_path: null,
    archived_at: null,
    created_at: '',
    updated_at: '',
    rows: [],
  } as unknown as SheetDetail
}

describe('useSheetStore', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('setDetail 写入后可读', () => {
    const s = useSheetStore()
    s.setDetail(makeDetail(1, 'A'))
    expect(s.details[1]?.title).toBe('A')
  })

  it('setDetail 不可变——产出新 Record，不污染旧引用', () => {
    const s = useSheetStore()
    s.setDetail(makeDetail(1, 'A'))
    const before = s.details
    s.setDetail(makeDetail(2, 'B'))
    expect(s.details).not.toBe(before)
    expect(before[2]).toBeUndefined()
  })

  it('setDetail 同 id 覆盖', () => {
    const s = useSheetStore()
    s.setDetail(makeDetail(1, 'A'))
    s.setDetail(makeDetail(1, 'A2'))
    expect(s.details[1]?.title).toBe('A2')
  })

  it('removeDetail 删除指定 id（不可变，保留其它）', () => {
    const s = useSheetStore()
    s.setDetail(makeDetail(1, 'A'))
    s.setDetail(makeDetail(2, 'B'))
    s.removeDetail(1)
    expect(s.details[1]).toBeUndefined()
    expect(s.details[2]?.title).toBe('B')
  })

  it('removeDetail 不存在的 id 无副作用（同引用返回）', () => {
    const s = useSheetStore()
    s.setDetail(makeDetail(1, 'A'))
    const before = s.details
    s.removeDetail(999)
    expect(s.details).toBe(before)
  })
})
