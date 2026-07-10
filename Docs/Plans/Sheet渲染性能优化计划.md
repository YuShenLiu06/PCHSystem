# Sheet 渲染性能优化计划（C + A）

> 状态：**已选定方案**（C 轮询降频 + A 惰性行编辑）
> 目标样本：sheet 4111（178 行 / 52KB JSON / 172 顶层 lock + 3 progress + 3 子行）
> 目标：前端首渲 2~4s → <1s，并降低稳态轮询压力

---

## 1. 背景与验证数据（实测）

| 指标 | 实测 | 命令 |
|---|---|---|
| `GET /sheets/4111` 端到端 | **~27ms**（TTFB 26ms，178 行/52KB） | `curl -H "X-Service-Token: …" -H "X-Player-UUID: …" http://localhost:8004/sheets/4111` |
| 后端查询 | 无 N+1：`get_sheet` + `list_rows`(join) + `list_contributors`(批量 IN) | `Backend/app/api/sheets/sheets_crud.py:121-143` |
| contributors 排序 | 确定性（row_id, contributed_qty desc, joined_at, id） | `Backend/app/repositories/sheet_repo.py:803-808` |

**结论**：后端 ~27ms，2~4s 的 ~99% 耗时**全部在前端首次渲染**。前端未能跑 MCP trace（环境无 Chrome），但后端基线已排除，瓶颈定位充分。

## 2. 瓶颈：前端组件密度

`Frontend/src/views/sheets/SheetEditor.vue` owner 视角，每个顶层 lock 行实例化约 13~15 个 Element Plus 组件（el-input×2 + el-input-number×2 + el-select + el-tag + el-popover 内含 6 组件 + el-button×3~5）。178 行 × ~15 ≈ **2700 个组件实例**，el-table 非虚拟滚动一次性全实例化。代码注释自证（`SheetEditor.vue:254`「176 行 × ~2000 组件卡顿」）。

稳态无问题：`applyRefreshedSheet` + `rowEqual`（JSON.stringify）+ contributors 确定性排序 → 未变行复用引用，轮询不触发整表重渲染。

测试现状：vitest + @vue/test-utils + jsdom 齐全，但**仅 5 个测试文件（工具/API/composable 层），零 `.vue` 组件测试**，SheetEditor 无现成组件测试。

---

## 3. 方案 C：轮询降频（XS，零风险，先合）

`SheetEditor.vue:33`

```ts
const DETAIL_INTERVAL_MS = 1_000   // before
const DETAIL_INTERVAL_MS = 3_000   // after
```

不影响首渲；降稳态后端压力（每秒 → 每 3 秒一次 GET + UPDATE）。认领/交付 2~3s 延迟可接受。

**复杂度**：1 行改动，~5 分钟，无回归风险。

---

## 4. 方案 A：惰性行编辑（M，主优化）

owner 默认全显 `<span>` 文本，点「编辑」只把**当前行**切 input 态。组件数 ~15/行 → ~7/行，预期首渲 2~4s → <1s。单行编辑（`editingRowId`），简单可靠。

### A.1 新增状态（script 区，`rowDrafts` 定义附近）

```ts
// 当前正在编辑的行 id；null = 浏览态（默认所有行显 span，首渲只建少量组件）
const editingRowId = ref<number | null>(null)
```

### A.2 进入/取消编辑

```ts
function onStartEdit(row: RowDetail): void {
  editingRowId.value = row.id   // rowDrafts 已在 load/applyRefreshedSheet 初始化
}

function onCancelEdit(row: RowDetail): void {
  const d = rowDrafts.value[row.id]
  if (d) {
    d.item_name = row.item_name
    d.registry_id = row.registry_id ?? ''
    d.need_qty = row.need_qty
    d.mode = row.mode
    d.sort_order = row.sort_order
    d.parent_row_id = row.parent_row_id
    d.qty_per_unit = row.qty_per_unit
  }
  editingRowId.value = null
}
```

### A.3 保存/删除后退出编辑

`onSaveRow` / `onSaveSubRow` 末尾（`applyRefreshedSheet(...)` 之后）加：

```ts
editingRowId.value = null
```

`onDeleteRow` 乐观更新段（清理 `removedIds` 草稿循环）加：

```ts
if (editingRowId.value === row.id) editingRowId.value = null
```

### A.4 模板：各 input 列的 `v-if` 收窄到「当前编辑行」

物品名 / 注册名 / 需要数量 / 倍数 / 模式 / 排序列，统一把：

```vue
v-if="canEdit && !isReadOnly && rowDrafts[row.id]"
```

改为：

```vue
v-if="canEdit && !isReadOnly && editingRowId === row.id"
```

涉及行（当前文件参考）：`794`、`805`、`819`、`834`、`858`、`921`。

### A.5 模板：操作列按钮按编辑态分流

替换 `:938-939` 的「保存/删除」常驻按钮为：

```vue
<template v-if="editingRowId === row.id">
  <el-button size="small" type="primary" @click="isSubRow(row) ? onSaveSubRow(row) : onSaveRow(row)">保存</el-button>
  <el-button size="small" @click="onCancelEdit(row)">取消</el-button>
</template>
<template v-else>
  <el-button v-if="canEdit" size="small" @click="onStartEdit(row)">编辑</el-button>
  <el-button v-if="canEdit" size="small" type="danger" @click="isSubRow(row) ? onDeleteSubRow(row) : onDeleteRow(row)">删除</el-button>
  <!-- 父行「添加子物品」popover：原样保留每行 popover（共享化见「暂缓」项） -->
</template>
```

### A.6 轮询刷新时保护编辑态

`applyRefreshedSheet` 清理消失行循环（`:283-288`）补：

```ts
if (editingRowId.value === id) editingRowId.value = null
```

> 现有「已有草稿保留不动」逻辑（`:261`）已确保编辑中的行不被覆盖，无需额外处理。

### A.7 风险

- **UX 变更**：从「常驻可改」→「点编辑才改」，需用户适应（可接受，收益大）。
- **子行编辑**：`onSaveSubRow` / `onStartEdit(subRow)` 走同一 `editingRowId`，单层模型下无冲突。
- **草稿初始化时机不变**：`load`（`:223-232`）+ `applyRefreshedSheet`（`:261-271`）已兜底新增行草稿。

---

## 5. 实施顺序

1. **C 先合**（一行改动，零风险，立即减轻后端每秒压力）。
2. **A 主优化**（首渲收益最大，需回归编辑/保存/取消/子行/轮询不覆盖草稿）。

## 6. 验证步骤

- **后端基线**：已测 27ms，无需复测。
- **前端首渲**（需浏览器）：DevTools Performance 录「打开 /sheets/4111」，对比 A 前后 Scripting 段耗时；目标 <1s。
- **功能回归**：
  - owner 点「编辑」→ 改值 →「保存」生效 /「取消」回滚。
  - 子行编辑/保存/删除同上。
  - 编辑中轮询不覆盖草稿；他端删除正在编辑的行 → 自动退出编辑态。
  - 非 owner 仍只读（无「编辑」按钮）。
- **前端单测**：`cd Frontend && npm run test` + `vue-tsc` 保持绿（现有 19 例不涉及 SheetEditor，应不受影响）。

> **测试策略**：SheetEditor 零组件测试基础，严格组件 TDD 需从零搭 mock 链路（~1.5h），成本接近实施本身。务实做法：A 的 UI/编辑态走手动回归；纯函数（如草稿回滚）若抽到 `utils/` 可加单测。

## 7. 复杂度与工时

| 方案 | 尺码 | 工时 | 收益 |
|---|---|---|---|
| C 轮询降频 | XS | ~5 min | 稳态减压（首渲不变） |
| A 惰性行编辑 | M | ~1.5-2 h（含手动回归） | 首渲 2~4s → <1s |
| **C + A 合计** | **M** | **~2 h** | **首渲 <1s + 稳态减压** |

## 8. 不做 / 暂缓的事

- **B（el-popover 共享化）暂缓**：虚拟触发需联网核实 Element Plus API、定位/销毁易踩坑（尺码 L、~2-3h）。A 落地后若首渲仍不达标再评估。
- **不分页**：割裂单表全览（认领/进度分布）体验，100 行未达分页体量（门槛 500+）。
- **不上 el-table-v2 虚拟滚动**：100 行收益有限，v2 的 tree 模式 / 自定义 cell / popover 支持不成熟，迁移成本 > 收益。
- **不改后端**：实测 27ms，无需动。
- **不为优化顺手拆 SheetEditor 组件**（YAGNI）：该文件已 1053 行（规范上限 800），但拆分是独立决策、风险更高；可在 A 落地后单独立项（如抽 `RowEditor` 子组件，反而能天然实现惰性编辑隔离）。
