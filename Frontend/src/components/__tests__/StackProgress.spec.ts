import { describe, test, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StackProgress from '../StackProgress.vue'

describe('StackProgress', () => {
  test('填充宽度按已交付/需求比例', () => {
    // Arrange + Act
    const wrapper = mount(StackProgress, { props: { delivered: 32, need: 128 } })

    // Assert
    expect(wrapper.find('.pch-progress__fill').attributes('style')).toContain('width: 25%')
    expect(wrapper.find('.pch-progress__label').text()).toBe('25%')
  })

  test('need ≤32 组时渲染组刻度并注入刻度间距变量', () => {
    const wrapper = mount(StackProgress, { props: { delivered: 0, need: 64 * 16 } })
    const ticks = wrapper.find('.pch-progress__ticks')

    expect(ticks.exists()).toBe(true)
    expect(ticks.attributes('style')).toContain('--pch-tick-pct: 6.25%')
  })

  test('need >32 组时退化为平滑条（刻度过密不渲染）', () => {
    const wrapper = mount(StackProgress, { props: { delivered: 0, need: 64 * 40 } })
    expect(wrapper.find('.pch-progress__ticks').exists()).toBe(false)
  })

  test('超交付截顶 100% 并标 over 修饰（避免溢出条）', () => {
    const wrapper = mount(StackProgress, { props: { delivered: 200, need: 100 } })
    const fill = wrapper.find('.pch-progress__fill')

    expect(fill.attributes('style')).toContain('width: 100%')
    expect(fill.classes()).toContain('pch-progress__fill--over')
  })

  test('need 为 0 时不除零，进度记 0', () => {
    const wrapper = mount(StackProgress, { props: { delivered: 5, need: 0 } })
    expect(wrapper.find('.pch-progress__label').text()).toBe('0%')
  })

  test('暴露 progressbar 语义与当前值（屏幕阅读器可读）', () => {
    const wrapper = mount(StackProgress, { props: { delivered: 50, need: 100 } })

    expect(wrapper.attributes('role')).toBe('progressbar')
    expect(wrapper.attributes('aria-valuenow')).toBe('50')
    expect(wrapper.attributes('aria-label')).toBe('交付进度 50%')
  })
})
