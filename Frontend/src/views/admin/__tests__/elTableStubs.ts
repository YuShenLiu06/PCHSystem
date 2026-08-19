import { defineComponent, h, inject, provide, type PropType } from 'vue'

// el-table / el-table-column 测试替身：真实 el-table 在 jsdom 依赖 ResizeObserver，
// 这里按「列定义 × 行数据」铺平成 div 网格——列的 scoped slot 用 { row, $index } 调用，
// 无默认 slot 的列回退渲染 prop 字段；表格还铺一层可点击行 div 并转发 row-click，
// 供下钻等行交互断言。多个表格嵌套（如抽屉内再放一张表）时 provide 就近生效。
const TABLE_ROWS: unique symbol = Symbol('stubTableRows')

export const ElTableStub = defineComponent({
  name: 'ElTable',
  props: {
    data: { type: Array as PropType<unknown[]>, default: () => [] },
  },
  emits: ['row-click'],
  setup(props, { slots, emit }) {
    provide(TABLE_ROWS, () => props.data)
    return () =>
      h('div', { class: 'stub-table' }, [
        h('div', { class: 'stub-cols' }, slots.default?.()),
        ...props.data.map((row, i) =>
          h('div', {
            class: 'stub-row',
            'data-row-index': i,
            onClick: () => emit('row-click', row),
          }),
        ),
      ])
  },
})

export const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  props: {
    label: { type: String, default: '' },
    prop: { type: String, default: '' },
  },
  setup(props, { slots }) {
    const rows = inject<() => unknown[]>(TABLE_ROWS, () => [])
    return () =>
      h(
        'div',
        { class: 'stub-col', 'data-label': props.label },
        rows().map((row, i) =>
          h(
            'div',
            { class: 'stub-cell', key: i },
            slots.default
              ? slots.default({ row, $index: i })
              : String((row as Record<string, unknown>)[props.prop] ?? ''),
          ),
        ),
      )
  },
})
