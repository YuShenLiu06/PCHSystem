# sheets API 参考

> 后端在线表格子服务的 HTTP API 权威参考（`Backend/app/api/sheets.py`）。
> OpenAPI 工件：[`Backend/openapi.json`](../../../Backend/openapi.json)；schema：[`Backend/app/schemas/sheet.py`](../../../Backend/app/schemas/sheet.py)。
> 玩法语义见 [`Docs/guied.md`](../../guied.md) §三「角色与权限」；协作设计见 [`Docs/superpowers/specs/2026-07-02-sheets-collaboration-design.md`](../../superpowers/specs/2026-07-02-sheets-collaboration-design.md)；MC 对等 + 通知见 [`Docs/superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md`](../../superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md)。

---

## 1. 概述

sheets 是 MVP 轻量在线表（与 `projects.material_lists` 投影体系不同）：固定列清单，支持 Web + 游戏内双向查看/编辑，按表（`sheets`）+ 行（`sheet_rows`）组织。每行 = 一个待备货物品条目，支持**认领协作**（认领、上报交付、解除锁定、打回）。

数据在 PostgreSQL 的 `sheets` schema，由 FastAPI 后端**独占**读写（红线 R-1）。前端只走 HTTP，MCDR 经 service token 读。

---

## 2. 鉴权

| 鉴权方式 | 头 | 谁用 | 覆盖端点 |
|---|---|---|---|
| **JWT（Bearer）** | `Authorization: Bearer <access_jwt>` | Web 端登录玩家 | 除全量导出外的全部端点 |
| **Service Token + X-Player-UUID（代玩家）** | `X-Service-Token` + `X-Player-UUID` 头 | MCDR / 外部系统 | sheets 写端点（claim / delivery / release / reject / upsert / 删行 / 删表）+ 通知端点（pending/ack/read），与 JWT 等价，复用 RBAC |
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
| `created_at` / `updated_at` | timestamptz | 时间戳 |

### `sheets.sheet_rows`（行 = 物品条目）
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键 |
| `sheet_id` | bigint | FK→`sheets.sheets.id`，`ON DELETE CASCADE` |
| `item_name` | text | 物品名（自由文本；UNIQUE(sheet_id, item_name) 兼作 upsert 锁点） |
| `need_qty` | int | 需要数量（原始整数，永不存换算结果） |
| `mode` | smallint | **0=lock**（锁定/二元备齐）/ **1=progress**（进度/跟踪 delivered_qty） |
| `status` | text | **open**（未认领）/ **claimed**（认领中）/ **done**（已备齐） |
| `claimant_uuid` | uuid? | 认领人，FK→`users.players.uuid`；`open` 态为 null |
| `delivered_qty` | int | 已交付数量 |
| `sort_order` | int | 排序 |
| `updated_at` | timestamptz | 时间戳 |

**不变量（lock 模式）**：`open ⇒ claimant IS NULL ∧ delivered=0`；`claimed ⇒ claimant NOT NULL`；`done ⇒ claimant NOT NULL ∧ delivered≥need`。

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
- **progress 模式（多人贡献者）**：`claimant_uuid` 恒 null，`status` 完全由 `delivered_qty` 推导（0→open / 0<x<need→claimed / ≥need→done）。任意登录玩家经 `POST .../contribute` 上报 `qty`，后端 `delivered += qty`（不封顶），同时幂等插入 `sheet_row_contributors(row_id, player_uuid)`；`delivered≥need` 时自动转 `done`。owner 可 `POST .../release` 重置：清 `delivered=0` + 清空该行 `sheet_row_contributors` + `status=open`。progress 行对 `claim`/`delivery`/`reject` 一律返 409。
- **拥有者改 need_qty（upsert）时已认领**：`delivered` 按新 need 封顶；`delivered≥新need` 且状态∈{claimed,done}→done；原 done 但 `delivered<新need`→claimed。（progress 行：`delivered` 不回退，仅按新 need 重算 status。）
- 并发由 `select ... with_for_update()` 行锁兜底；非法转移抛 `SheetRowConflict`→409。

---

## 5. 端点

### 5.1 表级

| 方法 | 路径 | 鉴权 | body / query | 成功 | 说明 |
|---|---|---|---|---|---|
| POST | `/sheets` | JWT 或 service-token+UUID | `SheetCreateRequest{title}` | 201 `SheetDetail` | 建表，owner=self。MCDR 可经 service-token+`X-Player-UUID` 代玩家调用 |
| POST | `/sheets/from-items` | JWT 或 service-token+UUID | `SheetFromItemsRequest{title, items[]}` | 201 `SheetDetail` | 一次性建表 + 批量行（`mode` 默认 lock、`items` ≤2000），用于「投影解析→生成表格」。详见 [`parsing.md`](./parsing.md) |
| GET | `/sheets` | JWT 或 service-token+UUID | `?owner=me`（只看自己） | 200 `[SheetSummary]` | 列所有表（含他人表可读）。MCDR 同上 |
| GET | `/sheets/{sheet_id}` | JWT 或 service-token+UUID | `?format=csv` | 200 `SheetDetail` \| `text/csv` | 表详情；`format=csv` 返回单表 CSV。MCDR 同上 |
| PATCH | `/sheets/{sheet_id}` | JWT·**owner** 或 service-token+UUID·owner | `SheetPatchRequest{title}` | 200 `SheetDetail` | 改标题。MCDR 同上 |
| DELETE | `/sheets/{sheet_id}` | JWT·**owner** 或 service-token+UUID·owner | — | 204 | 删表（级联 rows + 认领）。MCDR 同上 |

### 5.2 行级

| 方法 | 路径 | 鉴权 | body | 成功 | 说明 |
|---|---|---|---|---|---|
| PUT | `/sheets/{sheet_id}/rows` | JWT·**owner** 或 service-token+UUID·owner | `RowUpsertRequest` | 200 `RowDetail` | upsert（按 item_name）；新建=open，更新保留 status/claimant/delivered。MCDR 同上 |
| DELETE | `/sheets/{sheet_id}/rows/{row_id}` | JWT·**owner** 或 service-token+UUID·owner | — | 204 | 删行。MCDR 同上 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/claim` | JWT 或 service-token+UUID | — | 200 `RowDetail` | **lock 专用**：任意玩家认领（open→claimed）。progress 行→409 |
| PATCH | `/sheets/{sheet_id}/rows/{row_id}/delivery` | JWT·**认领人** 或 service-token+UUID·认领人 | `RowDeliveryRequest{delivered_qty}` | 200 `RowDetail` | **lock 专用**：上报交付量。progress 行→409（progress 用 `/contribute`） |
| POST | `/sheets/{sheet_id}/rows/{row_id}/contribute` | JWT 或 service-token+UUID | `RowContributeRequest{qty}` | 200 `RowDetail` | **progress 专用**：任意登录玩家上报贡献，`delivered += qty`（不封顶），幂等加贡献者，`≥need` 自动 done。lock 行→409 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/release` | JWT·**认领人\|owner** 或 service-token+UUID | — | 200 `RowDetail` | lock：解除锁定（→open）。progress（仅 owner）：清 delivered + 贡献者列表 + status=open |
| POST | `/sheets/{sheet_id}/rows/{row_id}/reject` | JWT·**认领人\|owner** 或 service-token+UUID | — | 200 `RowDetail` | **lock 专用**：打回（done→claimed；认领人自取消备齐 / 拥有者打回）。progress 行→409 |

### 5.3 导出

| 方法 | 路径 | 鉴权 | 成功 | 说明 |
|---|---|---|---|---|
| GET | `/sheets/export` | **Service Token** | 200 `text/csv` | 全量 CSV（所有表所有行） |

> 注意：`/export` 必须注册在 `/{sheet_id}` 路由之前，否则被动态路径吞掉。

---

## 6. 请求 / 响应模型

```python
class SheetCreateRequest(BaseModel):
    title: str               # 1..128

class SheetPatchRequest(BaseModel):
    title: str               # 1..128

class RowUpsertRequest(BaseModel):
    item_name: str           # 1..64
    need_qty: int = 0        # ≥0
    mode: int = 0            # 0=lock | 1=progress
    sort_order: int = 0      # ≥0

class RowDeliveryRequest(BaseModel):
    delivered_qty: int       # ≥0

class RowContributeRequest(BaseModel):
    qty: int                 # ≥1（progress 行单次贡献量，累加不封顶）

class RowDetail(BaseModel):
    id: int
    item_name: str
    need_qty: int
    mode: int                # 0|1
    status: str              # open|claimed|done
    claimant_uuid: UUID | None    # lock: 认领人；progress: 恒 null
    claimant_name: str | None     # lock: 认领人名；progress: 恒 null
    delivered_qty: int
    contributors: list[RowContributor]  # progress: 贡献者聚合（非空）；lock: 空数组
    sort_order: int
    updated_at: datetime

class RowContributor(BaseModel):
    player_uuid: UUID
    player_name: str         # join users.players.current_name

class SheetSummary(BaseModel):
    id: int
    owner_uuid: UUID
    owner_name: str          # join users.players.current_name
    title: str
    created_at: datetime
    updated_at: datetime

class SheetDetail(SheetSummary):
    rows: list[RowDetail]
```

- `owner_name` / `claimant_name` 由后端 join `users.players.current_name` 返回，前端不再显示 UUID。
- 「多人认领」接口预留：当前 `claimant_uuid`/`claimant_name` 为单值，未来升级为 `claimants[]` 时 API 形状向后兼容（存储层迁 `sheet_claims` 子表时仅 repo 内部重构）。

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
| 全量 CSV 导出 | — service token — | — | — | — |

---

## 8. 错误码

| 状态码 | 场景 |
|---|---|
| 401 | 缺/错 Bearer JWT；全量导出缺/错 service token |
| 403 | 权限不足（非 owner 改表/行；非认领人 delivery；非 owner reject/release 他人锁） |
| 404 | 表/行不存在 |
| 409 | 状态非法转移（对 done 行 claim；对 open 行 reject；upsert 并发同名 insert 命中 UNIQUE；**模式不匹配**：progress 行 claim/delivery/reject，lock 行 contribute） |
| 422 | 请求体校验失败（如 `mode` 非 0/1、`need_qty<0`、`title` 空） |

错误体统一 FastAPI 默认：`{"detail": "<message>"}`。

---

## 9. CSV 导出列

全量与单表 CSV 共用表头：

```
sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order
```

- `claimant_uuid` 为 null（open 态，或 **progress 行恒 null**）时输出空串。
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

---

## 11. MCDR `!!PCH sheet` 命令映射表

> 命令树收敛到现有 `!!PCH` 前缀（与 `McdrPlugin/htcmc_auth/htcmc_auth/__init__.py` 一致）。每个命令回调内 `player_uuid = uuid_api_remake.get_uuid(player_name)`（RS-8）→ 作为 `X-Player-UUID` + `X-Service-Token` 头调后端。错误码 403/404/409 → 友好中文文本（`server.tell`）；哨兵（`__RATE_LIMITED__`/`__REMOVED__`/`None`）必须回执玩家（RS-11）。

| 命令 | 角色 | HTTP 端点 | 说明 |
|---|---|---|---|
| `!!PCH sheet list [--mine]` | 任意玩家 | `GET /sheets[?owner=me]` | 列所有表（或仅自己拥有） |
| `!!PCH sheet view <sheet_id>` | 任意玩家 | `GET /sheets/{sheet_id}` | 表详情（行 + 认领/状态/进度） |
| `!!PCH sheet create <title...>` | 任意玩家 | `POST /sheets` | 建表，owner=self（标题含空格用 QuotableText） |
| `!!PCH sheet rename <sheet_id> <title...>` | owner | `PATCH /sheets/{sheet_id}` | 改标题 |
| `!!PCH sheet delete <sheet_id>` | owner | `DELETE /sheets/{sheet_id}` | 删表（级联） |
| `!!PCH sheet add <sheet_id> <item> <need> [lock\|progress] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | upsert 新建行（mode：lock=0/progress=1，默认 lock） |
| `!!PCH sheet set <sheet_id> <item> <need> [lock\|progress] [sort]` | owner | `PUT /sheets/{sheet_id}/rows` | upsert 更新行（同端点，item_name 已存在则改） |
| `!!PCH sheet delrow <sheet_id> <row_id>` | owner | `DELETE /sheets/{sheet_id}/rows/{row_id}` | 删行 |
| `!!PCH sheet claim <sheet_id> <row_id>` | 任意玩家 | `POST /sheets/{sheet_id}/rows/{row_id}/claim` | 认领（open→claimed） |
| `!!PCH sheet deliver <sheet_id> <row_id> <qty>` | 认领人 | `PATCH /sheets/{sheet_id}/rows/{row_id}/delivery` | **lock 行**上报交付量（**绝对值**，与后端契约一致） |
| `!!PCH sheet done <sheet_id> <row_id>` | 认领人 | `PATCH /sheets/{sheet_id}/rows/{row_id}/delivery`（=need） | lock 模式快捷「标备齐」（deliver need） |
| `!!PCH sheet contribute <sheet_id> <row_id> <qty>` | 任意玩家 | `POST /sheets/{sheet_id}/rows/{row_id}/contribute` | **progress 行**上报贡献（任意玩家，qty≥1，累加不封顶，自动 done） |
| `!!PCH sheet release <sheet_id> <row_id>` | 认领人自放 / owner 解锁 | `POST /sheets/{sheet_id}/rows/{row_id}/release` | 解除锁定（→open） |
| `!!PCH sheet reject <sheet_id> <row_id>` | 认领人(done 态自取消) / owner 打回 | `POST /sheets/{sheet_id}/rows/{row_id}/reject` | 打回（done→claimed，delivered 归零） |
| `!!PCH sheet notify list` | 自己 | `GET /notifications/pending?player_uuid=<self>` | 查看自己近期通知（见 §12） |

> 权限文案在 help 里说明；真实 RBAC 以后端 403/409 为准（R-9）。`qty` 用绝对值（与后端/前端契约一致，避免额外 GET + 并发，KISS）；progress 模式玩家先 `view` 看当前 delivered 再决定。

---

## 12. 通知端点（service-token 鉴权）

> 通知抽象层完整契约见 [`services/notification-service.md`](../services/notification-service.md)。三个端点均 `X-Service-Token` 鉴权（与 sheets `/export` 一致），供 MCDR 通知轮询器消费。

| 方法 | 路径 | 鉴权 | 请求 | 响应 | 说明 |
|---|---|---|---|---|---|
| GET | `/notifications/pending` | service-token | `?player_uuid=<uuid>&limit=<n>` | `200 [{id, recipient_uuid, category, title, body, payload, created_at}, ...]` | 拉 `delivered_at IS NULL` 的通知（按 `created_at` 升序，limit 默认 20/上限 **50**） |
| POST | `/notifications/ack` | service-token | `{player_uuid: <uuid>, ids: [int]}` | `200 {acked: int}` | 置**该 player_uuid 名下**通知 `delivered_at = now()`（**C-1 防越权**：跨玩家 id 计 0 不命中；幂等） |
| POST | `/notifications/{id}/read` | service-token | `?player_uuid=<uuid>` | `200 NotificationOut` | 置**归属该 player_uuid** 通知 `read_at = now()`（**C-1**：跨玩家返 404；L-2 同步幂等置 `delivered_at`） |

**错误码**：401（缺/错 service token）、404（read 的 id 不存在 **或不归属该 player_uuid**）、422（player_uuid 缺/格式错、ids 非数组）。

**触发规则与 category 枚举**：见 [`services/notification-service.md`](../services/notification-service.md) §3（首期 7 类 sheets 专用：`sheet_claimed`/`sheet_delivered`/`sheet_done`/`sheet_released`/`sheet_rejected`/`sheet_qty_changed`/`sheet_row_deleted`）。

**MCDR 投递流程**：在线集合（`on_player_joined`/`on_player_left` + `rcon_query('list')` 初始化）→ `@new_thread` 后台轮询 → `server.tell` 投递 → `POST /ack`；玩家上线时 `on_player_joined` 立即拉一次 pending 补推。详见 [`services/mcdr-plugin.md`](../services/mcdr-plugin.md)「通知轮询」。

---

*最后更新：2026-07-02（progress 模式改为多人贡献者列表：新增 `sheet_row_contributors` 表 + `POST .../contribute` 端点 + RowDetail.contributors；progress 行 claim/delivery/reject → 409；迁移 0007）*
