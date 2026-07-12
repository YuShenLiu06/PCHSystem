# sheets API 参考

> 后端在线表格子服务的 HTTP API 权威参考（`Backend/app/api/sheets/` 包，2026-07-09 由原 `sheets.py` 1215 行包化拆分而来）。
> OpenAPI 工件：[`Backend/openapi.json`](../../../Backend/openapi.json)；schema：[`Backend/app/schemas/sheet.py`](../../../Backend/app/schemas/sheet.py)。
> 玩法语义见 [`Docs/guied.md`](../../guied.md) §三「角色与权限」；协作设计见 [`Docs/superpowers/specs/2026-07-02-sheets-collaboration-design.md`](../../superpowers/specs/2026-07-02-sheets-collaboration-design.md)；MC 对等 + 通知见 [`Docs/superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md`](../../superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md)。

---

## 1. 概述

sheets 是 MVP 轻量在线表（与 `projects.material_lists` 投影体系不同）：固定列清单，支持 Web + 游戏内双向查看/编辑，按表（`sheets`）+ 行（`sheet_rows`）组织。每行 = 一个待备货物品条目，支持**认领协作**（认领、上报交付、解除锁定、打回）。

数据在 PostgreSQL 的 `sheets` schema，由 FastAPI 后端**独占**读写（红线 R-1）。前端只走 HTTP，MCDR 经 service token 读。

> **术语演进**：sheet 在玩法语义中已升级为「项目」（前端文案统一改「项目」），但 URL `/sheets`、API 类型名 `Sheet*`、表名 `sheets` 保留不变（YAGNI，避免书签/外链失效）。本文档与代码标识符沿用 `sheet*`。

**项目三阶段生命周期**：每个 sheet（项目）有阶段状态 `collecting`（材料收集，默认）→ `constructing`（施工占位）→ `archived`（只读终态）。owner/admin 经 `POST /sheets/{id}/advance` 流转阶段；进入 `archived` 时后端渲染 markdown 归档 + 贡献占比饼图原子落盘到 `ARCHIVE_ROOT/projects/<id>/`（DB 存相对路径 `archived_path`，为 wiki-service git publisher 同步入口），任意登录玩家可 `GET /sheets/{id}/archive` 取回 markdown、`GET /sheets/{id}/archive/assets/{filename}` 取回归档资产（如饼图 PNG）。详见 §4.1「项目阶段状态机」。

---

## 2. 鉴权

| 鉴权方式 | 头 | 谁用 | 覆盖端点 |
|---|---|---|---|
| **JWT（Bearer）** | `Authorization: Bearer <access_jwt>` | Web 端登录玩家 | 除全量导出外的全部端点 |
| **Service Token + X-Player-UUID（代玩家）** | `X-Service-Token` + `X-Player-UUID` 头 | MCDR / 外部系统 | sheets 写端点（claim / delivery / release / reject / upsert / 删行 / 删表）+ `/me` 与 `/me/last_sheet`（玩家身份读）+ 通知端点（pending/ack/read），与 JWT 等价，复用 RBAC |
| **Service Token（无身份）** | `X-Service-Token: <token>` | MCDR / 外部系统 | 仅 `GET /sheets/export`（全量 CSV 只读） |

- JWT 经 `POST /auth/exchange`（一次性 token → JWT pair）获取，详见 auth API。
- **service-token + `X-Player-UUID` 代玩家写**：后端校验 service token 后用该 UUID 加载 `Player` 注入，下游 `_can_edit` / `claimant_uuid == player.uuid` 等 RBAC 完全复用，**与 JWT 写等价**。MCDR 无需管 JWT。
- 权限以**后端 RBAC 为准**（红线 R-9）：前端按钮显隐只是 UX，真实拒绝是后端 403/409。

---

## 3. 数据模型

### `sheets.sheets`（表主记录）
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键 |
| `owner_uuid` | uuid | 拥有者，FK→`users.players.uuid`（R-5） |
| `title` | text | 表标题 |
| `status` | text | **项目阶段**：`collecting`（默认，材料收集）/ `constructing`（施工占位）/ `archived`（只读终态）；CHECK ∈ 三值，迁移 0009 |
| `archived_path` | text? | 归档 markdown 相对 `ARCHIVE_ROOT` 路径（如 `projects/42/index.md`，每项目独立文件夹，同目录含 `contributions.png`）；仅 `archived` 非空，一致性 CHECK 保证。是 wiki-service git publisher 推送入口 |
| `archived_at` | timestamptz? | 归档时间；仅 `archived` 非空 |
| `created_at` / `updated_at` | timestamptz | 时间戳 |

### `sheets.sheet_rows`（行 = 物品条目）
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键 |
| `sheet_id` | bigint | FK→`sheets.sheets.id`，`ON DELETE CASCADE` |
| `item_name` | text | 显示名/物品名（自由文本数据字段；**部分唯一索引**——改名/新建撞名→409）；新建缺失时后端据 `registry_id` 翻译补默认中文名。**修改行以 `id`（主键）为定位主轴，而非 `item_name`**（issue #20：旧实现按 item_name upsert 改名会重复建行） |
| `registry_id` | text? | MC 物品注册名 `namespace:path`（**隐式可空**，迁移 0010）；**一键提交（`!!PCH sheet submit`）按此精确匹配表行**；无此列值的行被一键提交跳过 |
| `need_qty` | int | 需要数量（原始整数，永不存换算结果） |
| `mode` | smallint | **0=lock**（锁定/二元备齐）/ **1=progress**（进度/跟踪 delivered_qty） |
| `status` | text | **open**（未认领）/ **claimed**（认领中）/ **done**（已备齐） |
| `claimant_uuid` | uuid? | 认领人，FK→`users.players.uuid`；`open` 态为 null |
| `delivered_qty` | int | 已交付数量 |
| `sort_order` | int | 排序 |
| `updated_at` | timestamptz | 时间戳 |
| `parent_row_id` | bigint? | **子物品嵌套**（迁移 0012）：子行的父行 id（FK→sheet_rows.id `ON DELETE CASCADE`）；null = 顶层行。删父行级联删子行 |
| `qty_per_unit` | numeric?(10,2) | **子物品单位用量/倍数**（迁移 0012，0013 改 numeric 支持小数）：子行每个父行物品所需的子物品数量，∈(0,+∞)（如 0.5）；need_qty = ceil(qty_per_unit × 父行 need_qty)（派生整数，向上取整） |

**不变量（lock 模式）**：`open ⇒ claimant IS NULL ∧ delivered=0`；`claimed ⇒ claimant NOT NULL`；`done ⇒ claimant NOT NULL ∧ delivered≥need`。

**不变量（子物品，迁移 0012）**：单层（子只能挂顶层，repo 层校验 `parent.parent_row_id IS NULL`）、模式继承（父 lock→子只能 lock；父 progress→子可 lock/progress）、单位用量级联（父 need 变→子 need 重算 = ceil(qty_per_unit × 新父 need)）、子行必须有 registry_id 且 qty_per_unit>0、子行 item_name 自动加父名前缀「父名-本名」。详见 [`data-model.md`](../data-model.md) §10.2。

**不变量（progress 模式，多人贡献者）**：`claimant_uuid` 恒为 `null`（progress 行不绑定单认领人）；`status` 由 `delivered_qty` 推导 —— `delivered=0 ⇒ open`，`0<delivered<need ⇒ claimed`，`delivered≥need ⇒ done`；贡献者列表由 `sheet_row_contributors` 聚合，单行可含多名贡献者。

### `sheets.sheet_row_contributors`（progress 行的贡献者聚合表）
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键 |
| `row_id` | bigint | FK→`sheets.sheet_rows.id`，`ON DELETE CASCADE`（删行级联清贡献者） |
| `player_uuid` | uuid | 贡献者，FK→`users.players.uuid`（R-5） |
| `joined_at` | timestamptz | 首次贡献时间 |
| | | UNIQUE(`row_id`, `player_uuid`)：每行每人最多一条，contribute 端点幂等加贡献者 |

> 仅 `mode=progress` 行会写入此表；lock 行无贡献者，`contributors` 响应字段恒空数组。

---

## 4. 行状态机（3 态）

```
        claim（任意登录玩家）            set_delivery delivered≥need（认领人）
  open ──────────────────────▶ claimed ──────────────────────────▶ done
   ▲ ▲                            │  ▲                                │
   │ │   release（认领人/拥有者）  │  │  reject delivered=0（认领人/拥有者）│
   │ └───────────────────────────┘  └────────────────────────────────┘ │
   └──────────── release（拥有者，从 done 直接释放）───────────────────┘
```

| 转移 | 端点 | 触发者 | 副作用 |
|---|---|---|---|
| open→claimed | `POST .../claim` | 任意登录玩家 | 置 claimant=self、delivered=0 |
| claimed/done→done/claimed | `PATCH .../delivery` | 认领人 | 设 delivered；`≥need`→done，`<need`→claimed |
| claimed/done→open | `POST .../release` | 认领人自放 或 拥有者 | 清 claimant、delivered=0 |
| done→claimed | `POST .../reject` | 认领人 或 拥有者 | delivered 归零，claimant 保留重做（认领人自取消备齐 / 拥有者打回，效果一致，已合并） |

- **lock 模式**：认领人「标备齐」= 一次性 `delivered=need`→done；「取消备齐」= `delivered=0`→claimed。
- **progress 模式（多人贡献者）**：`claimant_uuid` 恒 null，`status` 完全由 `delivered_qty` 推导（0→open / 0<x<need→claimed / ≥need→done）。任意登录玩家经 `POST .../contribute` 上报 `qty`，后端 `delivered += qty`（不封顶），同时幂等插入 `sheet_row_contributors(row_id, player_uuid)`；`delivered≥need` 时自动转 `done`。owner 可 `PATCH .../progress` 直接覆写 `delivered_qty`（绝对值，可增可减、不动贡献者列表，用于修正/回退进度），或 `POST .../release` 重置：清 `delivered=0` + 清空该行 `sheet_row_contributors` + `status=open`。progress 行对 `claim`/`delivery`/`reject` 一律返 409。
- **拥有者改 need_qty（upsert）时已认领**：`delivered` 按新 need 封顶；`delivered≥新need` 且状态∈{claimed,done}→done；原 done 但 `delivered<新need`→claimed。（progress 行：`delivered` 不回退，仅按新 need 重算 status。）
- 并发由 `select ... with_for_update()` 行锁兜底；非法转移抛 `SheetRowConflict`→409。

### 4.1 项目阶段状态机（3 阶段，迁移 0009）

> 与 §4「行级状态机」是**两个正交状态**：行级 `status`（open/claimed/done）描述单条物品的认领协作进度；项目级 `status`（collecting/constructing/archived）描述整个项目（sheet）的生命周期阶段。

```
   advance(to=constructing)        advance(to=archived，写盘+通知)
collecting ─────────────────────▶ constructing ─────────────────────▶ archived
    └────────── advance(to=archived，跳过施工，写盘+通知) ──────────────▶▲
```

| 转移 | 端点 | 触发者 | 副作用 |
|---|---|---|---|
| collecting→constructing | `POST /sheets/{id}/advance?to=constructing` | owner/admin | 仅置 `status=constructing`（advance→constructing 静默，owner 自触发不发通知） |
| collecting→archived（直跳，跳过施工） | `POST /sheets/{id}/advance?to=archived` | owner/admin | 渲染 md → 写盘 → DB 置 archived 三字段 + `category=sheet_archived` 通知 → commit |
| constructing→archived | `POST /sheets/{id}/advance?to=archived` | owner/admin | 同上（标记施工完成并归档） |
| 缺省 `to` | `POST /sheets/{id}/advance`（不带 query） | owner/admin | 按当前状态推进下一态：`collecting→constructing`、`constructing→archived` |

- **archived = 终态只读**：archived 之后任何写操作（advance、行级 upsert/claim/delivery/contribute/release/reject/progress、删行/删表）一律 repo 层 `SheetArchived` 守卫 → api 翻译 409。删除整个 sheet 仍走 repo 层 `_assert_writable` → 409（archived 后行数据视为冻结）。
- **幂等拒绝**：`to == 当前状态` → `SheetRowConflict` → 409（避免重复通知/覆盖 `archived_at`）。
- **并发归档**：`advance_sheet` 用 `SELECT ... FOR UPDATE` 锁 sheet 行；同 sheet 并发 advance 第二个要么 409（已转走）要么读到最新状态。
- **归档产物路径约定**：`archived_path` 存相对 `ARCHIVE_ROOT` 的 POSIX 路径，指向**每项目独立文件夹**下的 `index.md`（`projects/{sheet_id}/index.md`），同目录含 `contributions.png`（matplotlib 贡献占比饼图，PNG，CJK 字体 Noto Sans CJK SC；≤5 人全显，>5 人 top5+其他）。archived 终态不重归档（`{sheet_id}` 稳定可预测）。
- **归档 markdown section 结构**（去逐行材料清单）：`# 📦 项目归档：{title}` / `## 🏆 贡献者统计`（`aggregate_contributor_totals` 用 union_all 合并 lock 行 `delivered_qty` + progress 行 `contributed_qty` 按人聚合，`HAVING>0` 剔除零和）/ `## 📊 贡献占比`（引用 `![贡献占比](contributions.png)`）/ `## 📅 时间线` / footer `由 PCHSystem 自动生成`。
- **wiki 同步**：归档落盘后 wiki-service git publisher（默认 off，best-effort）把 `projects/<id>/` 整目录 `git commit + push` 到独立 wiki 内容 git 仓（R-8 重写后改 git 双向，废弃原 GraphQL 单向）；失败仅 `notify(category="wiki_publish_failed")`，不回滚 DB。详见 [`services/wiki-service.md`](../services/wiki-service.md)。

---

## 5. 端点

### 5.1 表级

| 方法 | 路径 | 鉴权 | body / query | 成功 | 说明 |
|---|---|---|---|---|---|
| POST | `/sheets` | JWT 或 service-token+UUID | `SheetCreateRequest{title}` | 201 `SheetDetail` | 建表，owner=self。MCDR 可经 service-token+`X-Player-UUID` 代玩家调用 |
| POST | `/sheets/from-items` | JWT 或 service-token+UUID | `SheetFromItemsRequest{title, items[]}`（`items` 元素 = `SheetItemIn`：`item_name`/`registry_id` 均可选但至少一个、`need_qty`、`mode`、`sort_order`） | 201 `SheetDetail` | 一次性建表 + 批量行（`mode` 默认 lock、`items` ≤2000），用于「投影解析→生成表格」。现透传 `registry_id`（= 投影解析 `PreviewItem.item_id`），`item_name` 缺失时后端翻译补中文名。详见 [`parsing.md`](./parsing.md) |
| GET | `/sheets` | JWT 或 service-token+UUID | `?owner=me`（只看自己）+ `?status=collecting\|constructing\|archived\|active`（`active`=collecting+constructing，前端默认） | 200 `[SheetSummary]` | 列所有表（含他人表可读），可按阶段过滤。**参与优先排序**：请求带玩家身份（JWT 或代玩家 UUID）时，该玩家参与过的表（owner / lock 行 claimant / progress 行 contributor 三源 UNION）置顶，组内按 id 升序，未参与表在后（`order_by id.in_(involved).desc(), id.asc()`）。MCDR 同上 |
| GET | `/sheets/{sheet_id}` | JWT 或 service-token+UUID | `?format=csv` `?q=<关键词>` | 200 `SheetDetail` \| `text/csv` | 表详情。`?q=` 按 `item_name`/`registry_id` 大小写不敏感子串过滤行（`registry_id` 可空→NULL 不匹配，天然 null-safe；LIKE 通配符 `%`/`_`/`\` 已转义，搜 `oak_log` 不会误匹配 `oakXlog`）。**行序（JSON 路径）= 玩家相关五档优先级**（按请求者 UUID 排）：0=我认领的 lock / 1=我参与的 progress / 2=我未参与的 progress / 3=非我认领的 lock（open+他人认领）/ 4=done；同档内按还需数量(`need-delivered`) **降序**，末位 tiebreak `sort_order, id`。**仅顶层行参与主排序键**——每个顶层行排定后，其子行按 `(sort_order, id)` **紧跟父行**（子行不参与独立优先级，避免脱离父行甚至排到父行上方）。`format=csv` 用**自然序**（`list_rows` 的分组序：全部父行段在前、全部子行段在后，**子行不紧跟各自父行而是按 `parent_row_id` 分组相邻**，组内再 `sort_order, id`；与导出者身份无关）；`?q=` 对 JSON 与 CSV 均生效。**副作用**：JSON 详情路径（非 csv、非 404）成功返回前 best-effort 记录 `users.players.last_sheet_id = sheet_id`（供 `GET /me/last_sheet` 快速重开；写失败仅记日志，不影响返回）。MCDR 经 `X-Player-UUID` 代玩家→同样玩家相关排序；分页由 MCDR 客户端做（30 行/页，issue #17）。 |
| PATCH | `/sheets/{sheet_id}` | JWT·**owner** 或 service-token+UUID·owner | `SheetPatchRequest{title}` | 200 `SheetDetail` | 改标题。MCDR 同上 |
| DELETE | `/sheets/{sheet_id}` | JWT·**owner** 或 service-token+UUID·owner | — | 204 | 删表（级联 rows + 认领）。MCDR 同上；**archived 态 → 409**（先 advance 不可逆，不能直接删） |

### 5.2 项目阶段生命周期（迁移 0009）

| 方法 | 路径 | 鉴权 | query | 成功 | 说明 |
|---|---|---|---|---|---|
| POST | `/sheets/{sheet_id}/advance` | JWT·**owner/admin** 或 service-token+UUID·owner/admin | `?to=constructing\|archived`（缺省按状态机推进下一态） | 200 `SheetDetail` | 阶段流转。`to=archived` 走归档服务：渲染 md → 写盘 → DB 置 archived + 通知（`category=sheet_archived`）→ 内部 commit；`to=constructing` 仅 repo `advance_sheet` + api 层 commit。允许 `collecting → archived` 直跳。MCDR `!!PCH sheet advance` 同上 |
| GET | `/sheets/{sheet_id}/archive` | JWT 或 service-token+UUID | — | 200 `text/markdown` | 读归档 markdown 文件内容。未归档 / 文件缺失 → 404。归档原文（前端 `<pre>` 预览；MCDR 不拉 md，回执仅给相对路径让玩家去 Web 看） |
| GET | `/sheets/{sheet_id}/archive/assets/{filename}` | JWT 或 service-token+UUID | — | 200 `image/png` | 读归档资产（如 `contributions.png` 贡献占比饼图）。**basename 白名单 + 路径穿越守卫**：`filename` 只允许纯文件名（`/`/`\`/`..` 拒绝），仅解析 `ARCHIVE_ROOT/projects/<sheet_id>/<filename>`；非法名或文件缺失 → 404。鉴权 `get_current_player`，任意登录玩家可读（与 `GET /archive` 一致）。用于前端归档预览内嵌饼图 |

- **事务边界**：`to=constructing` 路径在 api 层 commit；`to=archived` 路径由归档服务 `archive_sheet` 内部 commit（先写盘后置 DB，commit 失败 cleanup 孤儿文件 + rollback）。
- **归档产物结构**：每项目独立文件夹 `projects/<sheet_id>/`，含 `index.md`（归档正文）+ `contributions.png`（matplotlib 贡献占比饼图，CJK 字体 Noto Sans CJK SC，≤5 人全显 / >5 人 top5+其他）。`index.md` 的 📊 section 以 `![贡献占比](contributions.png)` 引用同目录 PNG。
- **状态机与错误码**：详见 §4.1 状态机小节 + §8 错误码。

### 5.3 行级

| 方法 | 路径 | 鉴权 | body | 成功 | 说明 |
|---|---|---|---|---|---|
| PUT | `/sheets/{sheet_id}/rows` | JWT·**owner** 或 service-token+UUID·owner | `RowUpsertRequest` | 200 `RowDetail` | **单端点按 `row_id` 分流**（issue #20）：**带 `row_id`** → 按主键更新（`item_name` 可改名，其余字段部分更新，未传=不改；修改以 `id` 为定位主轴）；**不带 `row_id`** → **严格新建**（同名已存在→409 不再覆盖；新建=open）。MCDR `add` 不带 row_id 走新建；`set`/`setreg` 带 row_id 走更新。 |
| DELETE | `/sheets/{sheet_id}/rows/{row_id}` | JWT·**owner** 或 service-token+UUID·owner | — | 204 | 删行。MCDR 同上 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/claim` | JWT 或 service-token+UUID | — | 200 `RowDetail` | **lock 专用**：任意玩家认领（open→claimed）。progress 行→409 |
| PATCH | `/sheets/{sheet_id}/rows/{row_id}/delivery` | JWT·**认领人** 或 service-token+UUID·认领人 | `RowDeliveryRequest{delivered_qty}` | 200 `RowDetail` | **lock 专用**：上报交付量。progress 行→409（progress 用 `/contribute`） |
| POST | `/sheets/{sheet_id}/rows/{row_id}/contribute` | JWT 或 service-token+UUID | `RowContributeRequest{qty}` | 200 `RowDetail` | **progress 专用**：任意登录玩家上报贡献，`delivered += qty`（不封顶），幂等加贡献者，`≥need` 自动 done。lock 行→409 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/release` | JWT·**认领人\|owner** 或 service-token+UUID | — | 200 `RowDetail` | lock：解除锁定（→open）。progress（仅 owner）：清 delivered + 贡献者列表 + status=open |
| POST | `/sheets/{sheet_id}/rows/{row_id}/reject` | JWT·**认领人\|owner** 或 service-token+UUID | — | 200 `RowDetail` | **lock 专用**：打回（done→claimed；认领人自取消备齐 / 拥有者打回）。progress 行→409 |
| PATCH | `/sheets/{sheet_id}/rows/{row_id}/progress` | JWT·**owner** 或 service-token+UUID·owner | `RowProgressRequest{delivered_qty}` | 200 `RowDetail` | **progress 专用**：拥有者/admin 直接覆写 `delivered_qty`（绝对值，可增可减）+ 按新值重算 status，**不动 contributors**（保留上交历史）。lock 行→409（请用 `/delivery`）。Web only（MCDR 未暴露） |

> **子物品（迁移 0012）复用上述端点**：`PUT /rows` 携带 `parent_row_id` 即新建子行（须同时给 `registry_id` + `qty_per_unit`>0，`need_qty` 由后端 `ceil(qty_per_unit × 父行 need_qty)` 派生、请求值被忽略；`mode` 缺省继承父行）；claim / delivery / contribute / release / reject / progress 对子行**同样生效**——传子行 `row_id` 即可，子行按自身 `mode`/`status` 参与协作（其 `mode` 由父行继承、`need_qty` 派生）。不变量（单层 / 模式继承 / 级联重算 / 删父 CASCADE）见 §3。

### 5.4 导出

| 方法 | 路径 | 鉴权 | 成功 | 说明 |
|---|---|---|---|---|
| GET | `/sheets/export` | **Service Token** | 200 `text/csv` | 全量 CSV（所有表所有行） |

> 注意：`/export` 必须注册在 `/{sheet_id}` 路由之前，否则被动态路径吞掉。

### 5.5 玩家最近查看（last_sheet，迁移 0011）

| 方法 | 路径 | 鉴权 | 成功 | 说明 |
|---|---|---|---|---|
| GET | `/me/last_sheet` | JWT 或 service-token+UUID（双通道 `get_current_player`，与 `/me` 一致） | 200 `LastSheetResponse{sheet_id}` | 返回该玩家最近查看的 sheet id（读 `users.players.last_sheet_id`，无历史返回 `null`）。`last_sheet_id` 由 `GET /sheets/{id}` JSON 详情路径 best-effort 写入（csv 导出 / 404 不记；表被删后自然失效，下次查看任意表覆盖）。MCDR `!!sheet`（无参）/ `!!PCH sheet last` 经此端点实现「快速重开上次表格」 |

> 端点位于 `top_router`（与 `/me`、`/auth/*` 同级），**不在 sheets 子路由内**；schema 见 `Backend/app/schemas/auth.py::LastSheetResponse`。响应模型 `{sheet_id: int | None}`。

---

## 6. 请求 / 响应模型

```python
class SheetCreateRequest(BaseModel):
    title: str               # 1..128

class SheetPatchRequest(BaseModel):
    title: str               # 1..128

class RowUpsertRequest(BaseModel):
    row_id: int | None = None       # ≥1（带=按主键更新路径；不带=按 item_name 新建路径）
    item_name: str | None = None    # 1..64（可选；更新路径可改名）
    registry_id: str | None = None  # MC 注册名 namespace:path（可选，迁移 0010；一键提交按此匹配）
    need_qty: int | None = None     # ≥0（更新路径 None=不改；新建路径缺省 0）
    mode: int | None = None         # 0=lock | 1=progress（更新路径 None=不改；新建缺省 lock）
    sort_order: int | None = None   # ≥0（更新路径 None=不改；新建缺省 0）
    parent_row_id: int | None = None  # 子物品父行 id（可选，迁移 0012）；子行必须同时提供 registry_id 与 qty_per_unit>0，否则 422
    qty_per_unit: float | None = None  # 子物品单位用量/倍数（可选，迁移 0012；0013 改 numeric 支持小数）；>0；子行 need_qty = ceil(qty_per_unit × 父行 need_qty)（派生整数，父 need 变时级联重算）
    # model_validator：仅「新建路径」（row_id 为空）要求 item_name 与 registry_id 至少一个非空，否则 422。
    # 更新路径（带 row_id）字段全可选，可只改 need/mode/sort（甚至只改名）。
    # 新建路径 item_name 缺失时，后端据 registry_id 用 LangJsonTranslator（复用投影解析翻译表，
    # translators/lang/*.zh_cn.json）查中文显示名，未命中回退 registry_id 本身。
    # 子物品路径（parent_row_id 非空）：要求 registry_id 非空且 qty_per_unit>0，否则 422；need_qty 派生忽略（由 ceil(父行 need × qty_per_unit) 计算）；子行 item_name 自动加父名前缀「父名-本名」。

class RowDeliveryRequest(BaseModel):
    delivered_qty: int       # ≥0

class RowContributeRequest(BaseModel):
    qty: int                 # ≥1（progress 行单次贡献量，累加不封顶）

class RowProgressRequest(BaseModel):
    delivered_qty: int       # ≥0（progress 行 owner 直接覆写绝对值，可增可减；不动 contributors）

class RowDetail(BaseModel):
    id: int
    item_name: str
    registry_id: str | None    # MC 注册名（隐式可空，迁移 0010）；一键提交按此匹配
    need_qty: int
    mode: int                # 0|1
    status: str              # open|claimed|done
    claimant_uuid: UUID | None    # lock: 认领人；progress: 恒 null
    claimant_name: str | None     # lock: 认领人名；progress: 恒 null
    delivered_qty: int
    contributors: list[RowContributor] = []  # progress: 贡献者聚合（status=open 时为空，有人上交后非空）；lock: 恒空数组
    sort_order: int
    updated_at: datetime
    parent_row_id: int | None  # 子物品父行 id（迁移 0012）；null = 顶层行
    qty_per_unit: float | None   # 子物品单位用量/倍数（迁移 0012，0013 改 numeric）；子行每个父行物品所需的子物品数量（>0，支持小数）

class RowContributor(BaseModel):
    player_uuid: UUID
    player_name: str         # join users.players.current_name

class SheetSummary(BaseModel):
    id: int
    owner_uuid: UUID
    owner_name: str          # join users.players.current_name
    title: str
    status: str              # collecting | constructing | archived（迁移 0009）
    archived_path: str | None     # 仅 archived 非空（相对 ARCHIVE_ROOT 的 POSIX 路径）
    archived_at: datetime | None  # 仅 archived 非空
    created_at: datetime
    updated_at: datetime

class SheetDetail(SheetSummary):
    rows: list[RowDetail]
```

- `owner_name` / `claimant_name` 由后端 join `users.players.current_name` 返回，前端不再显示 UUID。
- 「多人认领」接口预留：当前 `claimant_uuid`/`claimant_name` 为单值，未来升级为 `claimants[]` 时 API 形状向后兼容（存储层迁 `sheet_claims` 子表时仅 repo 内部重构）。
- **`registry_id`（迁移 0010，隐式可空）**：`RowUpsertRequest` / `SheetItemIn`（`POST /sheets/from-items` 的 items 元素）中 `item_name` 与 `registry_id` 均**可选**，但 **model_validator 要求至少一个非空**，否则 422。`item_name` 缺失时后端用 `LangJsonTranslator`（复用投影解析翻译表 `translators/lang/*.zh_cn.json`）据 `registry_id` 查中文显示名补默认值，未命中回退 `registry_id` 本身。`POST /sheets/from-items` 现透传 `registry_id`（来自投影解析 `PreviewItem.item_id`）。

---

## 7. 权限矩阵

| 动作 | 拥有者 | admin/owner 角色 | 认领人（lock 行） | 其他登录玩家 |
|---|---|---|---|---|
| 读（列表/详情/单表 CSV） | ✅ | ✅ | ✅ | ✅ |
| 改表/行 upsert/删行/删表 | ✅ | ✅ | ❌ | ❌ |
| 认领 claim（**lock** 行） | ✅ | ✅ | — | ✅ |
| 上报交付 / 标备齐（**lock** 行） | 仅当自己是认领人 | 仅当自己是认领人 | ✅ | ❌ |
| **贡献 contribute（progress 行）** | ✅ | ✅ | — | ✅ |
| 解除锁定 release | ✅（lock 自认/owner；progress 仅 owner） | ✅ | ✅（lock 自放） | ❌ |
| 打回 reject（**lock** 行） | ✅ | ✅ | ✅ | ❌ |
| **阶段流转 advance（项目级）** | ✅ | ✅ | — | ❌ |
| 读归档 markdown `GET /archive` | ✅ | ✅ | ✅ | ✅ |
| 读归档资产 `GET /archive/assets/{filename}` | ✅ | ✅ | ✅ | ✅ |
| 全量 CSV 导出 | — service token — | — | — | — |

---

## 8. 错误码

| 状态码 | 场景 |
|---|---|
| 400 | `?to=` 非法值（仅允许 `constructing\|archived`；`active` 仅用于过滤不可作 advance 目标） |
| 401 | 缺/错 Bearer JWT；全量导出缺/错 service token |
| 403 | 权限不足（非 owner 改表/行；非认领人 delivery；非 owner reject/release 他人锁；**非 owner/admin advance**） |
| 404 | 表/行不存在；`GET /archive` 未归档或归档文件缺失；`GET /archive/assets/{filename}` 文件名非法（路径穿越/非白名单）或缺失 |
| 409 | 状态非法转移（对 done 行 claim；对 open 行 reject；upsert 并发同名 insert 命中 UNIQUE；**模式不匹配**：progress 行 claim/delivery/reject，lock 行 contribute）；**项目级**：`SheetArchived`（archived 终态只读——行 upsert/删行/删表返中文文案「项目已归档，只读」；`advance` 路径返「sheet is archived, read-only」；repo 层 `_assert_writable` 统一守卫）、`?to==当前状态` 幂等拒绝、`constructing→collecting` 等非法阶段转移 |
| 422 | 请求体校验失败（如 `mode` 非 0/1、`need_qty<0`、`title` 空） |
| 503 | `to=archived` 但后端 `ARCHIVE_ROOT` 未配置（`ArchiveNotConfigured`） |

错误体统一 FastAPI 默认：`{"detail": "<message>"}`。

---

## 9. CSV 导出列

全量与单表 CSV 共用表头：

```
sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit
```

- `claimant_uuid` 为 null（open 态，或 **progress 行恒 null**）时输出空串。
- `registry_id` 为 null 时输出空串（迁移 0010 新增列，在 `item_name` 之后）。
- `parent_row_id` 为 null 时输出空串（迁移 0012 新增列；null = 顶层行）。
- `qty_per_unit` 为 null 时输出空串（迁移 0012 新增列；仅子行非空且>0，0013 起支持小数）。
- mode/status 为字面值（`0`/`1`、`open`/`claimed`/`done`）。
- 贡献者聚合（`sheet_row_contributors`）不进 CSV 扁平表头；明细走 `GET /sheets/{id}` 的 JSON `contributors` 字段。

---

## 10. 迁移

| 版本 | 说明 |
|---|---|
| `0004_sheets` | 建 `sheets` schema + `sheets`/`sheet_rows` 表（含 `done_flag`） |
| `0005_sheets_collab` | 加 `mode`/`status`/`claimant_uuid`/`delivered_qty`；旧 `done_flag=1`→`status='done'`；删 `done_flag`；加 `ix_sheet_rows_sheet_status`。**可逆**（downgrade 恢复 done_flag） |
| `0006_notifications` | 建 `notifications` schema + `notifications.notifications` 表 + `ix_notifications_recipient_delivered`。**可逆**。详见 [`services/notification-service.md`](../services/notification-service.md) §4 |
| `0007_sheet_row_contributors` | 建 `sheets.sheet_row_contributors`（id/row_id FK CASCADE/player_uuid FK/joined_at；UNIQUE(row_id, player_uuid)）。数据迁移：`mode=1`（progress）行清 `claimant_uuid=null`；旧 claimant 且 `delivered_qty>0` 者移入 contributors；按 `delivered_qty` 重算 status（0→open / 0<x<need→claimed / ≥need→done）。**可逆**。对应 D-4 推翻（progress 单认领人→多人贡献者） |
| `0008_contributed_qty` | （progress owner `PATCH /progress` 配套）保留 progress 行 owner 直覆写绝对进度的数据支撑。**可逆** |
| `0009_sheets_lifecycle` | `sheets.sheets` 加三列：`status`（text NOT NULL DEFAULT `'collecting'`，server_default 自动回填现有行）+ `archived_path`（text null）+ `archived_at`（timestamptz null）。两道 CHECK：`ck_sheets_status_values`（status ∈ collecting/constructing/archived）+ `ck_sheets_status_archive_consistency`（archived ⇒ path/at 非空；非 archived ⇒ path/at 为 null）。索引 `ix_sheets_status`。**可逆**（downgrade drop index/constraints/columns）。对应项目三阶段生命周期 |
| `0010_sheet_rows_registry_id` | 给 `sheets.sheet_rows` 加列 `registry_id TEXT NULL`（隐式可空，无唯一约束；down_revision=`0009_sheets_lifecycle`）。语义：MC 物品注册名 `namespace:path`，**一键提交 `!!PCH sheet submit` 按此精确匹配表行**。兼容旧行（null）；block id ≠ item id 时存原值不归一化。**可逆** |
| `0011_players_last_sheet_id` | **users schema**（sheets 快速重开配套）：给 `users.players` 加列 `last_sheet_id INTEGER NULL`（无 FK、无索引；down_revision=`0010_sheet_rows_registry_id`）。语义：玩家最后查看的 sheet id，由 `GET /sheets/{id}` JSON 详情路径 best-effort 写入（csv/404 不记），供 `GET /me/last_sheet` 读取。故意无 FK——表被删后自然失效（下次查看覆盖），对齐 `registry_id` 先例。**可逆**（downgrade 仅 DROP COLUMN） |
| `0012_sheet_rows_hierarchy` | **子物品嵌套行**（issue #19，迁移 0012，down_revision=`0011_players_last_sheet_id`）：`sheet_rows` 加 `parent_row_id BIGINT NULL`（FK 自引用 ON DELETE CASCADE）+ `qty_per_unit`（初版误写 INTEGER）。删原 `UNIQUE(sheet_id, item_name)`，改两个部分唯一索引（顶层 `uq_sheet_rows_top_name`、子行 `uq_sheet_rows_sub_registry`）+ CHECK `ck_sheet_rows_sub_invariants`（子行必须有 registry_id 且 qty_per_unit≥1）+ 索引 `ix_sheet_rows_parent`。**不变量**：单层（子只能挂顶层）、模式继承（父 lock→子只能 lock）、单位用量级联（子 need = qty_per_unit × 父 need）。**可逆** |
| `0013_qty_per_unit_numeric` | **倍数放宽为小数**（down_revision=`0012_sheet_rows_hierarchy`）：纠正 0012 把 `qty_per_unit` 错写 INTEGER → 改 `NUMERIC(10,2)`（与模型一致，支持 0.5 等小数倍数）；CHECK `ck_sheet_rows_sub_invariants` 由 `qty_per_unit >= 1` 放宽为 `> 0`，匹配 schema `Field(gt=0)` 与玩法「倍数 ∈ (0,+∞)」。子行 need_qty 派生用 `ceil(qty_per_unit × 父 need)` 仍为整数。**downgrade 有损**（NUMERIC→INTEGER 截断小数） |

---

## 11. MCDR `!!PCH sheet` 命令映射表

> 命令树收敛到现有 `!!PCH` 前缀（与 `McdrPlugin/pch_system/pch_system/__init__.py` 一致）。每个命令回调内 `player_uuid = uuid_api_remake.get_uuid(player_name)`（RS-8）→ 作为 `X-Player-UUID` + `X-Service-Token` 头调后端。错误码 403/404/409 → 友好中文文本（`server.tell`）；哨兵（`__RATE_LIMITED__`/`__REMOVED__`/`None`）必须回执玩家（RS-11）。

| 命令 | 角色 | HTTP 端点 | 说明 |
|---|---|---|---|
| `!!PCH sheet list [--mine]` | 任意玩家 | `GET /sheets[?owner=me]` | 列所有表（或仅自己拥有） |
| `!!PCH sheet view <sheet_id>` | 任意玩家 | `GET /sheets/{sheet_id}` | 表详情（行 + 认领/状态/进度） |
| `!!PCH sheet create <title...>` | 任意玩家 | `POST /sheets` | 建表，owner=self（标题含空格用 QuotableText） |
| `!!PCH sheet rename <sheet_id> <title...>` | owner | `PATCH /sheets/{sheet_id}` | 改标题 |
| `!!PCH sheet delete <sheet_id>` | owner | `DELETE /sheets/{sheet_id}` | 删表（级联） |
| `!!PCH sheet add <sheet_id> <item> <need> [lock\|progress] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | **严格新建**（不带 row_id）；同名已存在→409 报错，不再覆盖。mode：lock=0/progress=1，默认 lock |
| `!!PCH sheet set <sheet_id> <row_id> <need> [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | **按 row_id 更新** need/sort（带 row_id）；id 主轴，不改 item_name/mode。行不存在→404 |
| `!!PCH sheet delrow <sheet_id> <row_id>` | owner | `DELETE /sheets/{sheet_id}/rows/{row_id}` | 删行 |
| `!!PCH sheet claim <sheet_id> <row_id>` | 任意玩家 | `POST /sheets/{sheet_id}/rows/{row_id}/claim` | 认领（open→claimed） |
| `!!PCH sheet deliver <sheet_id> <row_id> <qty>` | 认领人 | `PATCH /sheets/{sheet_id}/rows/{row_id}/delivery` | **lock 行**上报交付量（**绝对值**，与后端契约一致） |
| `!!PCH sheet done <sheet_id> <row_id>` | 认领人 | `PATCH /sheets/{sheet_id}/rows/{row_id}/delivery`（=need） | lock 模式快捷「标备齐」（deliver need） |
| `!!PCH sheet contribute <sheet_id> <row_id> <qty>` | 任意玩家 | `POST /sheets/{sheet_id}/rows/{row_id}/contribute` | **progress 行**上报贡献（任意玩家，qty≥1，累加不封顶，自动 done） |
| `!!PCH sheet release <sheet_id> <row_id>` | 认领人自放 / owner 解锁 | `POST /sheets/{sheet_id}/rows/{row_id}/release` | 解除锁定（→open） |
| `!!PCH sheet reject <sheet_id> <row_id>` | 认领人(done 态自取消) / owner 打回 | `POST /sheets/{sheet_id}/rows/{row_id}/reject` | 打回（done→claimed，delivered 归零） |
| `!!PCH sheet submit <sheet_id>` | 任意玩家 | 复合（scan→按 `registry_id` 精确匹配→claim+delivery 或 contribute） | **一键提交**：扫完整背包（含潜影盒嵌套）→ 按 `registry_id` 匹配表行 → lock(open+have≥need) claim+deliver(need)→done / progress contribute(封顶到 need)；**纯申报不清背包**；跳过无 `registry_id` 的行。依赖 `minecraft_data_api` 插件 |
| `!!submit` / `!!submit <sheet_id>` | 任意玩家 | `GET /me/last_sheet`（无参时）→ 同 `!!PCH sheet submit <sheet_id>` | **一键提交新根**：`!!submit` 重开上次查看的表格并直接提交（复用 `!!sheet` 的 `last_sheet` 存储，后端零改动）；`!!submit <编号>` 指定表格。与 `!!PCH sheet submit <id>` 共用实现；回执仅逐行展示与本人相关的跳过行，其余折叠为一行计数（降噪 bugfix） |
| `!!PCH sheet addhand <sheet_id> <need> [lock\|progress] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | 手持物品自动填 `registry_id`（中文名后端翻译补）新建行 |
| `!!PCH sheet setreg <sheet_id> <row_id> <registry_id>` | owner | `PUT /sheets/{sheet_id}/rows`（保留原 item_name，补 `registry_id`） | 给已有行补 `registry_id`（让该行可被一键提交匹配） |
| `!!PCH sheet addsub <sheet_id> <parent_row_id> <registry_id> <qty_per_unit> [mode] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | **子物品新建**（迁移 0012）：给指定父行添加子物品（`parent_row_id`）。必须提供 registry_id 与 qty_per_unit>0（0013 起支持小数）；need_qty 派生（= ceil(qty_per_unit × 父 need)）；子行 item_name 自动加父名前缀「父名-本名」。mode 可选（缺省继承父行模式）。**单层限制**：父行必须是顶层行（parent_row_id IS NULL），否则 409 |
| `!!PCH sheet delsub <sheet_id> <row_id>` | owner | `DELETE /sheets/{sheet_id}/rows/{row_id}` | **子物品删除**（迁移 0012）：删子行（复用现有 `delrow` 端点）。删父行自动级联删所有子行（ON DELETE CASCADE） |
| `!!PCH sheet setsub <sheet_id> <row_id> <qty_per_unit> [mode] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | **子物品修改**（迁移 0012）：按 row_id 更新子行的 qty_per_unit/mode/sort。子行 need_qty 自动重算（= qty_per_unit × 父 need）。父行 need 改变时级联重算所有子行 |
| `!!PCH sheet advance <sheet_id> [constructing\|archived]` | owner / admin | `POST /sheets/{sheet_id}/advance?to=` | **项目阶段流转**：缺省 `to` 走后端状态机默认推进；`to=archived` 触发归档（写盘+通知，回执含归档相对路径）；archived 态再 advance → 409「只读」回执；`ARCHIVE_ROOT` 未配置 → 503 回执 |
| `!!PCH sheet notify list` | 自己 | `GET /notifications/pending?player_uuid=<self>` | 查看自己近期通知（见 §12） |

> 权限文案在 help 里说明；真实 RBAC 以后端 403/409 为准（R-9）。`qty` 用绝对值（与后端/前端契约一致，避免额外 GET + 并发，KISS）；progress 模式玩家先 `view` 看当前 delivered 再决定。
>
> **设计待办（§13）**：Web 已统一「表格 → 项目」，MCDR 仍 `!!PCH sheet …` + 文案「表格 / 表」，且与 `!!PCH project` 占位节点冲突。提议主节点改 `!!PCH project`、删占位、文案改「项目」、迁移期双注册兼容；**本期仅设计不实现**。

---

## 12. 通知端点（service-token 鉴权）

> 通知抽象层完整契约见 [`services/notification-service.md`](../services/notification-service.md)。三个端点均 `X-Service-Token` 鉴权（与 sheets `/export` 一致），供 MCDR 通知轮询器消费。

| 方法 | 路径 | 鉴权 | 请求 | 响应 | 说明 |
|---|---|---|---|---|---|
| GET | `/notifications/pending` | service-token | `?player_uuid=<uuid>&limit=<n>` | `200 [{id, recipient_uuid, category, title, body, payload, created_at}, ...]` | 拉 `delivered_at IS NULL` 的通知（按 `created_at` 升序，limit 默认 20/上限 **50**） |
| POST | `/notifications/ack` | service-token | `{player_uuid: <uuid>, ids: [int]}` | `200 {acked: int}` | 置**该 player_uuid 名下**通知 `delivered_at = now()`（**C-1 防越权**：跨玩家 id 计 0 不命中；幂等） |
| POST | `/notifications/{id}/read` | service-token | `?player_uuid=<uuid>` | `200 NotificationOut` | 置**归属该 player_uuid** 通知 `read_at = now()`（**C-1**：跨玩家返 404；L-2 同步幂等置 `delivered_at`） |

**错误码**：401（缺/错 service token）、404（read 的 id 不存在 **或不归属该 player_uuid**）、422（player_uuid 缺/格式错、ids 非数组）。

**触发规则与 category 枚举**：见 [`services/notification-service.md`](../services/notification-service.md) §3（首期 7 类 sheets 行级专用：`sheet_claimed`/`sheet_delivered`/`sheet_done`/`sheet_released`/`sheet_rejected`/`sheet_qty_changed`/`sheet_row_deleted`；项目级归档追加 `sheet_archived`——owner/admin `POST /sheets/{id}/advance?to=archived` 成功后落库，payload 含 `sheet_id`/`sheet_title`/`archived_path`/`archived_at`）。

**MCDR 投递流程**：在线集合（`on_player_joined`/`on_player_left` + `rcon_query('list')` 初始化）→ `@new_thread` 后台轮询 → `server.tell` 投递 → `POST /ack`；玩家上线时 `on_player_joined` 立即拉一次 pending 补推。详见 [`services/mcdr-plugin.md`](../services/mcdr-plugin.md)「通知轮询」。

---

## 13. 设计待办：MCDR 指令组对齐「项目」语义

> **仅设计，本期不实现**（主工程师在改代码，本节为 Phase D 设计记录，落地见 [`services/mcdr-plugin.md`](../services/mcdr-plugin.md)「项目语义对齐设计」）。

### 13.1 现状（不对齐）

- **Web 端**：文案已统一「表格 → 项目」（前端 SheetList / SheetEditor / tab 文案全改「项目」）。
- **MCDR 端**：主命令节点仍是 `!!PCH sheet …`，回执/帮助文案仍大量用「表格 / 表」；与 `sheets.md` §1「术语演进」的「sheet = 项目」语义脱节。
- **节点冲突**：`!!PCH project …` 已被占用为 `_not_impl` 占位节点（占坑未实现），与「sheet 升级为项目」后的语义命令名正面冲突。

### 13.2 提议（迁移期设计）

| 项 | 现状 | 提议 |
|---|---|---|
| 主命令节点 | `!!PCH sheet …` | 主节点改 `!!PCH project …`（别名 `proj`），下接现有 sheet 子命令树（list/view/create/.../advance/notify） |
| 占位节点 | `!!PCH project` = `_not_impl` 占位 | **删占位**（被新主节点复用，语义自然收敛） |
| 文案 | 「表格 / 表」 | 统一改「项目」（help / 回执 / 阶段横幅） |
| 兼容期 | — | **双注册**：迁移期 `sheet` + `project` 两个 Literal 同时挂同一套子命令树（玩家两种写法都生效），稳定后再评估下线 `sheet` |
| 归档节点 | `!!PCH sheet advance <id> [constructing\|archived]` 已存在 | 对齐节点名 → `!!PCH project advance <id> [constructing\|archived]`（HTTP 端点 `/sheets/{id}/advance` URL 不变，YAGNI，避免书签/外链失效） |

### 13.3 风险与约束

- **双注册期命令重复**：玩家输入 `sheet` 和 `project` 触发同一回调，help 文案需说明「两者等价、`project` 为新名」；help 列表会有两条入口（容忍）。
- **MCDR worktree 改动不被 reload 看到**：在 worktree 改 `.py` 不会被运行中的 MCDR reload 察觉，须先把改动同步到主仓路径再 `!!MCDR reload` 测试（项目已知坑，见主仓 CLAUDE.md 记忆）。
- **S-1 联网验证**：任何命令节点树重构（`Literal` / 别名 / 子树复用）实现前必须查 [MCDR 官方文档](https://docs.mcdreforged.com/zh-cn/latest/) 核实 API 签名（根红线 S-1）。
- **API 契约不变**：URL 仍是 `/sheets/*`、类型名仍是 `Sheet*`（§1 术语演进），MCDR 改的只是**游戏内命令名与文案**，后端零改动。

---

## 14. 增量日志（registry_id 配套）

*增量（2026-07-03 bugfix）*：MCDR 暴露 `!!PCH sheet progress`（撤回原 "Web only"）；owner `PATCH /progress`（值变化）、`POST /release`（progress 行）、`PUT /rows` 改 mode（progress→lock）三类写操作现通知该行所有贡献者——新增 category `sheet_progress_changed` / `sheet_progress_reset`。

*增量（2026-07-03，registry_id）*：sheet 行加隐式可空 `registry_id`（迁移 0010）；`RowUpsertRequest`/`SheetItemIn` 的 `item_name` 改可选 + 新增 `registry_id`（至少一个，否则 422），缺失时后端据 `registry_id` 翻译补中文名；`POST /sheets/from-items` 透传 `registry_id`；CSV 导出列追加 `registry_id`（item_name 之后）；MCDR 新增 `!!PCH sheet submit`（一键提交，按 registry_id 精确匹配）/ `addhand`（手持新建行）/ `setreg`（给已有行补 registry_id）。

*增量（2026-07-05）*：sheet view tellraw 像素级美化——新增 `text_layout.py` 像素宽度估算模块 + `format_section_separator` 分节标题（`════ 物品列表 ════`）+ 行尾按钮右对齐 / 底栏按钮居中；`format_section_separator` 配色回归 §6 色板「重要/标题」`§6§l`（gold+bold）；MCDR 测试 113 绿。

*增量（2026-07-06，快速重开 + list 增强）*：迁移 `0011_players_last_sheet_id`（`users.players` 加 `last_sheet_id INTEGER NULL`，无 FK/索引）；`GET /sheets/{id}` JSON 详情路径 best-effort 写入 `last_sheet_id`（csv/404 不记，失败仅记日志）；新增 `GET /me/last_sheet`（双通道鉴权，响应 `{sheet_id: int|null}`，供 MCDR `!!sheet` / `!!PCH sheet last` 快速重开）；`list_sheets` 加可选 `player_uuid`——参与优先排序（owner / lock 行 claimant / progress 行 contributor 三源 UNION 置顶，组内按 id 升序），`GET /sheets` 透传 `player.uuid`，`player_uuid=None` 时按 id 升序向后兼容。

*增量（2026-07-09，子物品嵌套行 + sheets.py 包化重构）*：**Phase 1 重构**：`Backend/app/api/sheets.py` 包化拆分为 `sheets/` 包（`__init__/_shared/sheets_crud/rows/collab/lifecycle`）；新增公共翻译 `app/services/translation.py`（`get_translator`/`resolve_item_name`）修正 sheets→parsing 反向依赖；通知 helper（`_row_payload`/`notify_owner_row_event`/`notify_uuids`/`_row_response`）。**Phase 2 子物品（issue #19，迁移 0012）**：`sheet_rows` 加 `parent_row_id`/`qty_per_unit`；部分唯一索引（顶层 `uq_sheet_rows_top_name`、子行 `uq_sheet_rows_sub_registry`）+ CHECK `ck_sheet_rows_sub_invariants`；**不变量**：单层（子只能挂顶层）、模式继承（父 lock→子只能 lock）、单位用量级联（子 need = qty_per_unit × 父 need）。`RowUpsertRequest`/`RowDetail`/CSV 加 `parent_row_id`/`qty_per_unit`；MCDR 新增 `addsub`/`delsub`/`setsub` 命令；子行复用既有 claim/deliver/contribute 命令（传子 row_id）。详见 [`data-model.md`](../data-model.md) §10.2。

---

*最后更新：2026-07-10（文档审计对齐实现：§5 顶栏引用改 `sheets/` 包；§5.1 GET 详情行序补「子行紧跟父行」(JSON) 与「父行段/子行段分两段」(CSV 自然序) + `?q=` LIKE 通配符转义说明；§5.3 补子物品复用协作端点说明；§8 archived 409 补实际文案「项目已归档，只读」/ advance「sheet is archived, read-only」；§6 RowDetail.contributors 修正 status=open 时为空）*

*2026-07-09（子物品嵌套行 + sheets.py 包化重构：迁移 0012 + `parent_row_id`/`qty_per_unit` + 单层/模式继承/级联重算；sheets/ 包结构 + translation.py 公共翻译 + 通知 helper；MCDR addsub/delsub/setsub + 缩进渲染 + 单字按钮；前端树状渲染 + 子物品内联编辑）*
