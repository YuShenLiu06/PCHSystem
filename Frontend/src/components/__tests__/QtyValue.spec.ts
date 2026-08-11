import { describe, test, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import QtyValue from '../QtyValue.vue'

describe('QtyValue', () => {
  test('渲染盒/组/个三段，单位字符独立成 span（便于降透明度）', () => {
    // Arrange + Act
    const wrapper = mount(QtyValue, { props: { value: 1728 * 12 + 64 * 3 + 41 } })

    // Assert
    expect(wrapper.text()).toBe('12盒3组41')
    expect(wrapper.findAll('.pch-qty__unit').map((u) => u.text())).toEqual(['盒', '组'])
  })

  test('首段外的段落标 minor 修饰类（主量级先被读到）', () => {
    const wrapper = mount(QtyValue, { props: { value: 1728 + 5 } })
    const segs = wrapper.findAll('.pch-qty__seg')

    expect(segs).toHaveLength(2)
    expect(segs[0].classes()).not.toContain('pch-qty__seg--minor')
    expect(segs[1].classes()).toContain('pch-qty__seg--minor')
  })

  test('title 给出精确个数（等宽分段后仍可核对原值）', () => {
    const wrapper = mount(QtyValue, { props: { value: 1792 } })
    expect(wrapper.attributes('title')).toBe('1792 个')
  })

  test('muted 时挂弱化类', () => {
    const wrapper = mount(QtyValue, { props: { value: 64, muted: true } })
    expect(wrapper.classes()).toContain('pch-qty--muted')
  })

  test('0 渲染为 0，不渲染空内容', () => {
    expect(mount(QtyValue, { props: { value: 0 } }).text()).toBe('0')
  })
})
