import { describe, test, expect } from 'vitest'
import { toQtySegments, stackTickPercent } from '../qtySegments'

describe('toQtySegments', () => {
  test('拆出盒/组/个三段（1 盒 = 27 组 = 1728 个）', () => {
    // Arrange
    const n = 1728 * 12 + 64 * 3 + 41

    // Act
    const segs = toQtySegments(n)

    // Assert
    expect(segs).toEqual([
      { value: 12, unit: '盒' },
      { value: 3, unit: '组' },
      { value: 41, unit: '' },
    ])
  })

  test('省略为零的高位与中位', () => {
    expect(toQtySegments(41)).toEqual([{ value: 41, unit: '' }])
    expect(toQtySegments(128)).toEqual([{ value: 2, unit: '组' }])
    expect(toQtySegments(1728)).toEqual([{ value: 1, unit: '盒' }])
  })

  test('中位为零时不塞占位段（1 盒 + 5 个）', () => {
    expect(toQtySegments(1728 + 5)).toEqual([
      { value: 1, unit: '盒' },
      { value: 5, unit: '' },
    ])
  })

  test('0 返回单段 0，不返回空数组', () => {
    expect(toQtySegments(0)).toEqual([{ value: 0, unit: '' }])
  })

  test('负数按 0 处理（净量可能为负，UI 不显示负库存记数）', () => {
    expect(toQtySegments(-5)).toEqual([{ value: 0, unit: '' }])
  })

  test('非整数向下取整（后端净量恒为整数，此处仅防御）', () => {
    expect(toQtySegments(64.9)).toEqual([{ value: 1, unit: '组' }])
  })
})

describe('stackTickPercent', () => {
  test('总量 ≤32 组时返回每组占比（离散刻度映射搬箱动作）', () => {
    // 16 组 → 每组 6.25%
    expect(stackTickPercent(64 * 16)).toBeCloseTo(6.25, 4)
  })

  test('总量 >32 组时返回 null（刻度过密，退化为平滑条）', () => {
    expect(stackTickPercent(64 * 33)).toBeNull()
  })

  test('不足一组返回 null（无组边界可画）', () => {
    expect(stackTickPercent(40)).toBeNull()
  })

  test('非正数返回 null', () => {
    expect(stackTickPercent(0)).toBeNull()
    expect(stackTickPercent(-64)).toBeNull()
  })

  test('恰好 32 组仍给刻度（边界含入）', () => {
    expect(stackTickPercent(64 * 32)).toBeCloseTo(3.125, 4)
  })
})
