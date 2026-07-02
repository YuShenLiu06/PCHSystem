import { describe, expect, it } from 'vitest'
import { formatQty } from '../qty'

describe('formatQty', () => {
  // 与 Backend/app/core/qty.py 对齐的边界用例（B1 同款）
  it('3456 -> 2盒', () => {
    expect(formatQty(3456)).toBe('2盒')
  })

  it('2000 -> 1.16盒', () => {
    expect(formatQty(2000)).toBe('1.16盒')
  })

  it('1728（恰好一盒）-> 1盒', () => {
    expect(formatQty(1728)).toBe('1盒')
  })

  it('192 -> 3组', () => {
    expect(formatQty(192)).toBe('3组')
  })

  it('64（恰好一组）-> 1组', () => {
    expect(formatQty(64)).toBe('1组')
  })

  it('100 -> 1.56组', () => {
    expect(formatQty(100)).toBe('1.56组')
  })

  it('63 -> 63个', () => {
    expect(formatQty(63)).toBe('63个')
  })

  it('0 -> 0个', () => {
    expect(formatQty(0)).toBe('0个')
  })

  it('超过一盒但仍可整除时去尾零（3456 = 2.0 盒）', () => {
    expect(formatQty(3456)).toBe('2盒')
  })

  it('整数倍不带小数点', () => {
    expect(formatQty(1728 * 5)).toBe('5盒')
    expect(formatQty(64 * 4)).toBe('4组')
  })
})
