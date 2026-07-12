# sheets 协作改进 · 设计文档（点1 名称显示 + 点2 认领/进度协作）

> **日期**：2026-07-02
> **状态**：设计稿（待用户复核 → 转 writing-plans 出实现计划）
> **来源**：用户反馈两点优化 + [`Docs/guied.md`](../../guied.md) §三「角色与权限」
> **前置**：Phase 2+3 sheets 已落地（commit `b4d37f4` 及之前），分支 `feat/sheets`

---

## 1. Context（背景与动机）

Phase 2+3 的 sheets 子服务（在线表格 CRUD + CSV + Web 可编辑 UI）已完成验收。用户在 Web 端试用后提两点改进：

1. **点1（UX）**：表格所有者目前显示为 UUID（`SheetEditor.vue:226` 的「所有者：{uuid}」灰色小字、`SheetList.vue:71` 的「所有者 UUID」列），对人不友好 → 改显游戏名。
2. **点2（功能）**：当前 sheets 只有拥有者能改、`done_flag` 二元、无协作。用户要求落地 `guied.md` §三的协作语义：**非拥有者可认领（锁定）某物品去备货、标记已备齐；拥有者可解除锁定、打回提交**。

`guied.md` §三原文依据：
- 项目参与者：「交付材料、放置方块，提交后自动锁定」
- 项目负责人：「修改材料清单、打回提交、解除锁定、维护进度」

sheets 是 MVP 轻量表（与 `projects.material_lists` 投影体系不同），本设计把上述语义以**最简形式**落到 sheets。

---

## 2. 范围

| 在范围内 | 不在范围内 |
|---|---|
| 点1：API 返回 `owner_name`（join players），前端显名隐 UUID | — |
| 点2：`sheet_rows` 加 mode（lock/progress，拥有者每行手选） + 认领人 + delivered_qty + status | 多人分摊认领（现在单认领人，留 API 接口） |
| 认领 / 上报交付 / 解除锁定 / 打回 的 API + 前端按钮 + 状态 tag | 拥有者「通过 approve」环节（3 态模型：备齐即 done，靠打回回退） |
| 自动阈值判模式（已否决，改拥有者手控） | ~~进度模式的「众筹」（谁都能交付，已否决，改单认领人跟踪 delivered_qty）~~ ⚠️ **已推翻（2026-07-02）**：progress 改为多人贡献者列表，见 D-4 标注 |
| 迁移 `0005`（加列 + done_flag→status 数据迁移 + 删 done_flag），可逆 | MCDR `!!sheet` 认领命令（Phase 4，待后端稳定） |
| 迁移 `0007_sheet_row_contributors`（建贡献者表 + 数据迁移 mode=1 行清 claimant、按 delivered 重算 status） |  |

---

## 3. 关键决策（已与用户对齐 ✅）

| # | 决策 | 说明 |
|---|---|---|
| D-1 | 认领模型 = **单认领人**（每行至多 1 个 active 认领人） | 用户定；多人留给未来，靠 API 返 `claimants[]` 列表语义作「接口」 |
| D-2 | 模式判定的「阈值」**否决**，改 **拥有者每行手选 mode** | 用户「下放权力，由表格拥有者手动设置」 |
| D-3 | mode 两档：`lock`（锁定/二元备齐）/ `progress`（进度/跟踪 delivered_qty） | 用户定 |
| D-4 | ~~进度模式 = **单认领人跟踪 delivered_qty**（非众筹）~~ ⚠️ **已推翻（2026-07-02）**：progress 改为**多人贡献者列表（聚合众筹）**。`claimant_uuid` 恒 null，`status` 由 `delivered_qty` 推导，任意玩家经 `POST .../contribute` 上报。新增 `sheet_row_contributors` 表 + 迁移 `0007`。详见 [sheets.md](../../architecture/api/sheets.md) §3/§4/§5.2 | 与 lock 一致的认领基座；未来多人 = 多人各自跟踪 |
| D-5 | 存储 = **方案 A：认领信息上墙到 `sheet_rows`**（加 claimant_uuid + delivered_qty 列） | 用户选 A（YAGNI；多人时再迁独立表） |
| D-6 | 状态机 = **3 态**（open / claimed / done，备齐=done） | 用户选；打回 = done→claimed；无 approve/ready |
| D-7 | 打回 target = 回 `claimed`（认领人重做，delivered 归零，认领人不变） | 默认采纳 |
| D-8 | 点1 UUID 完全隐藏（API 仍返 owner_uuid，前端不显） | 默认采纳 |
| D-9 | 允许拥有者认领自己行（无害，拥有者亦是参与者） | 默认采纳 |
| D-10 | 新行默认 `mode=lock`、`status=open` | 默认采纳 |

---

## 4. 点1 · 显示游戏名而非 UUID

### 后端
- `SheetSummary` / `SheetDetail` 增字段 `owner_name: str`。
- `RowDetail` 增字段 `claimant_name: str | None`（点2 认领人名，同理 join）。
- repo：
  - `list_sheets` / `get_sheet` 改为 join `users.players`（按 owner_uuid）取 `current_name`，返回 `(Sheet, owner_name)`。
  - `list_rows` 改为 left join `users.players`（按 claimant_uuid）取认领人名，返回 `(SheetRow, claimant_name | None)`。
  - `create_sheet`：owner = 当前登录玩家，api 层直接用 `player.current_name`，无需额外查询。

### 前端
- `sheets.ts` 类型增 `owner_name`、`claimant_name`。
- `SheetList.vue`：「所有者 UUID」列 →「所有者」列，显 `owner_name`。
- `SheetEditor.vue:226`：「所有者：{{ sheet.owner_uuid }}」→「所有者：{{ sheet.owner_name }}」。

---

## 5. 点2 · 认领/进度协作

### 5.1 数据模型（迁移 `0005_sheets_collaboration`）

`sheet_rows` 改造：
```sql
ALTER TABLE sheets.sheet_rows ADD COLUMN mode         smallint NOT NULL DEFAULT 0;  -- 0=lock, 1=progress
ALTER TABLE sheets.sheet_rows ADD COLUMN status       text NOT NULL DEFAULT 'open'; -- open/claimed/done
ALTER TABLE sheets.sheet_rows ADD COLUMN claimant_uuid uuid REFERENCES users.players(uuid);  -- null when open
ALTER TABLE sheets.sheet_rows ADD COLUMN delivered_qty integer NOT NULL DEFAULT 0;
-- 数据迁移：旧 done_flag=1 → status='done'；done_flag=0 → status='open'
ALTER TABLE sheets.sheet_rows DROP COLUMN done_flag;
CREATE INDEX ix_sheet_rows_sheet_status ON sheets.sheet_rows (sheet_id, status);
```
- `claimant_uuid` 可空（open 态为 null）；不加 NOT NULL，方便状态切换。
- 不加 (sheet_id, claimant) 唯一约束 —— 单认领人由 `status='claimed'` 时 claimant 非空 + 应用层保证（同行只一条 active 认领）；未来多人无需改约束。
- 可逆 downgrade：加回 done_flag（status='done'→1 否则 0），删 4 个新列与索引。

ORM `SheetRow` 同步：去 `done_flag`，加 `mode`/`status`/`claimant_uuid`/`delivered_qty`。

### 5.2 状态机（3 态，D-6）

```
        认领(任意登录玩家)            标备齐/交付满(认领人)
  open ─────────────────▶ claimed ─────────────────▶ done
   ▲ ▲                     │  ▲                         │
   │ │     解除锁定         │  │  打回(delivered=0)      │
   │ └─(认领人自放/拥有者)──┘  └──(拥有者)──────────────┘
   └─────解除锁定(拥有者，从 done 直接释放)───────────────┘
```
- **解除锁定 release**：从 `claimed`（认领人自放 / 拥有者）或从 `done`（拥有者）→ `open`，清空 claimant + delivered。
- **打回 reject**：从 `done`（拥有者）→ `claimed`，delivered 归零、认领人保留重做。

**不变量**：
- `status='open'` ⇒ `claimant_uuid IS NULL` 且 `delivered_qty=0`
- `status='claimed'` ⇒ `claimant_uuid IS NOT NULL`（`delivered_qty < need_qty` 或未标备齐）
- `status='done'` ⇒ `claimant_uuid IS NOT NULL` 且 `delivered_qty >= need_qty`

**lock 模式**：认领人「标备齐」= 一次性 `delivered_qty = need_qty` → `done`；「取消备齐」= `delivered=0` → `claimed`。
**progress 模式**：认领人多次「上报交付」累加 `delivered_qty`；`>= need_qty` 自动转 `done`。

### 5.3 权限

| 动作 | 谁可执行 |
|---|---|
| 认领 claim（open→claimed） | 任意登录玩家（含拥有者，D-9） |
| 上报交付 / 标备齐（claimed→done） | **当前认领人** only |
| 解除锁定 release（claimed/done→open） | 当前认领人（自放）或拥有者 |
| 打回 reject（done→claimed） | **拥有者** only（admin/owner 角色同） |
| 改 mode / need_qty / item_name / sort_order（upsert） | **拥有者** only |
| 删行 / 删表 | **拥有者** only（认领随之消失，不单独通知） |

**拥有者改 need_qty 时已认领**：`delivered_qty` 按新 need 封顶；若 `delivered >= 新 need` 则 status 自动转 `done`（progress）或保持（lock 由认领人重标）。边界场景，不过度设计。

### 5.4 API（JWT，全部挂现有 `/sheets` router）

新增 4 端点（path 参数 `{sheet_id}` + `{row_id}`）：

| 方法 | 路径 | 鉴权 | body | 说明 |
|---|---|---|---|---|
| POST | `/sheets/{sid}/rows/{rid}/claim` | JWT | — | open→claimed，claimant=self |
| PATCH | `/sheets/{sid}/rows/{rid}/delivery` | JWT（认领人） | `{delivered_qty: int≥0}` | 设交付量；`>=need` 自动 done，否则 claimed |
| POST | `/sheets/{sid}/rows/{rid}/release` | JWT（认领人/拥有者） | — | claimed/done→open（清认领人+delivered） |
| POST | `/sheets/{sid}/rows/{rid}/reject` | JWT（拥有者） | — | done→claimed（delivered 归零，认领人不变） |

改动现有端点：
- `PUT /sheets/{sid}/rows`（upsert）→ **拥有者 only**；body 用 `RowUpsertRequest` 增 `mode` 字段、**删 `done_flag`**。两种情形：
  - **新建行**（item_name 不存在）：产出 `status='open'`、`claimant=null`、`delivered=0`。
  - **更新已存在行**：仅改 `item_name`/`need_qty`/`mode`/`sort_order`，**保留** `status`/`claimant_uuid`/`delivered_qty`（拥有者改需求不打断已认领人的进度，delivered 按新 need 封顶）。
- `GET /sheets/{sid}`（detail）与 `GET /sheets`（list）：response 增 `owner_name`；RowDetail 增 `claimant_uuid`/`claimant_name`/`status`/`mode`/`delivered_qty`，去 `done_flag`。
- CSV 导出（`?format=csv` 单表 + `/export` 全量）：列头追加 `mode,status,claimant_uuid,delivered_qty`，去 `done_flag`。

**错误码**：状态非法转移 → 409（如对 done 行 claim、对非认领人 delivery、对 open 行 reject）；权限不足 → 403；行/表不存在 → 404。

### 5.5 前端 UX（`SheetEditor.vue`，R-9 仅可见性）

行表格新增列/控件：
- **认领者** 列：显 `claimant_name`（open 态显「—」）。
- **状态** 列：el-tag —— `open` 灰 / `claimed` 蓝 / `done` 绿。
- **交付进度** 列：progress 模式显 `delivered/need` + el-progress；lock 模式隐。
- **动作** 列（按角色与状态条件渲染）：
  - 任意玩家 × `open` →「认领」按钮（claim）
  - 认领人自己 × `claimed` → lock:「标备齐」「放弃」；progress:「上报交付」(输入数量)+「放弃」
  - 认领人自己 × `done` →「取消备齐」(lock: PATCH delivery 0；progress: 减数量)
  - 拥有者 × `claimed/done` →「解除锁定」（release）
  - 拥有者 × `done` →「打回」（reject）
  - 拥有者 × 任意 → 行内编辑 mode（下拉 lock/progress）/ need_qty / item_name / sort_order（现有 upsert）
- 非 owner 且非 claimant 的旁观者：所有动作按钮隐藏，只读。

### 5.6 「留多人接口」的体现（D-1/D-5）

- API 的 RowDetail 把认领信息以**列表语义**返回：现在返 `claimant_uuid`/`claimant_name`（单）；未来多人时升级为 `claimants: [{uuid, name, claimed_qty, delivered_qty}]`，前端把「认领者」列改为渲染该列表，其余逻辑不变。
- 存储层未来迁出 `sheets.sheet_claims` 子表时，API 形状不变（仅 repo 内部重构）。
- 现在不上 claims 子表 = YAGNI；接口契约已为多人铺路。

---

## 6. 红线对齐（根 CLAUDE.md §3）

- **R-1** 后端独占 DB，前端只走 HTTP；✅ 全部新动作经 API。
- **R-5** UUID 身份锚：`claimant_uuid`/`owner_uuid` 均 FK→`users.players.uuid`；✅
- **R-9** 前端权限仅可见性：动作按钮显隐是 UX，真实拒绝在后端（403/409）；✅
- **R-10/RS-3** 模块化单体：新端点挂现有 `/sheets` router，不拆服务；✅
- **R-11/RS-4** 无新密钥；✅
- **RS-5** sheets 用户主动数据硬删除：删行/删表级联清认领，无审计残留；✅（与 Phase 2 一致）
- **Backend 分层**：状态转移逻辑放 repo（或 service），commit 在 api 层，并发 `with_for_update`（认领/打回等改 status 的写）；✅

---

## 7. 验证（实现后）

| 项 | 期望 |
|---|---|
| 迁移 `0005` upgrade/downgrade 可逆 | 无报错；旧 done_flag 正确迁移到 status |
| 后端 pytest（含新增认领/打回等用例） | 全绿；覆盖状态机全分支 + 权限 403/409 + 非法转移 |
| OpenAPI 重导出 | 含 4 新端点；RowDetail/SheetSummary 字段更新 |
| 前端 vitest + build | 通过；sheets 客户端覆盖新端点 |
| 端到端（Web）：A 建表加行 → B（非拥有者）认领 → B 标备齐(done) → A 打回(claimed) → B 再备齐 → A 解除锁定(open) → 进度模式行上报交付 | 全链路状态 tag 与进度条正确 |
| 点1：列表/详情显游戏名，不显 UUID | ✓ |
| CSV 导出含新列 | ✓ |

---

## 8. 开放项 / 后续

- **多人认领**：本设计 D-1/D-5/D-6 已为多人留接口（API 列表语义 + 未来 claims 子表），实现暂不做。
- **MCDR `!!sheet` 认领**：Phase 4，待此 API 稳定后 spawn `mcdr-dev`（service-token 写通道 + body.uuid 代玩家操作契约）。
- **进度模式「众筹」**：~~已否决（D-4）；若未来需要任意玩家上报，再扩展。~~ ✅ **已实现（2026-07-02）**：D-4 推翻，progress 改为多人贡献者列表 —— 新增 `sheets.sheet_row_contributors` 表（迁移 `0007`）+ `POST /sheets/{sid}/rows/{rid}/contribute` 端点；任意玩家可上报贡献，`delivered_qty` 聚合不封顶，按 delivered 重算 status。详见 [sheets.md](../../architecture/api/sheets.md) §3/§5.2/§10。
- **打回/解除的通知**：MVP 不做推送；前端靠刷新或手动查询。未来接 alert-service。
