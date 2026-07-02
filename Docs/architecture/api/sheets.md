# sheets API 参考

> 后端在线表格子服务的 HTTP API 权威参考（`Backend/app/api/sheets.py`）。
> OpenAPI 工件：[`Backend/openapi.json`](../../../Backend/openapi.json)；schema：[`Backend/app/schemas/sheet.py`](../../../Backend/app/schemas/sheet.py)。
> 玩法语义见 [`Docs/guied.md`](../../guied.md) §三「角色与权限」；协作设计见 [`Docs/superpowers/specs/2026-07-02-sheets-collaboration-design.md`](../../superpowers/specs/2026-07-02-sheets-collaboration-design.md)。

---

## 1. 概述

sheets 是 MVP 轻量在线表（与 `projects.material_lists` 投影体系不同）：固定列清单，支持 Web + 游戏内双向查看/编辑，按表（`sheets`）+ 行（`sheet_rows`）组织。每行 = 一个待备货物品条目，支持**认领协作**（认领、上报交付、解除锁定、打回）。

数据在 PostgreSQL 的 `sheets` schema，由 FastAPI 后端**独占**读写（红线 R-1）。前端只走 HTTP，MCDR 经 service token 读。

---

## 2. 鉴权

| 鉴权方式 | 头 | 谁用 | 覆盖端点 |
|---|---|---|---|
| **JWT（Bearer）** | `Authorization: Bearer <access_jwt>` | Web 端登录玩家 | 除全量导出外的全部端点 |
| **Service Token** | `X-Service-Token: <token>` | MCDR / 外部系统 | 仅 `GET /sheets/export`（全量 CSV 只读） |

- JWT 经 `POST /auth/exchange`（一次性 token → JWT pair）获取，详见 auth API。
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

**不变量**：`open ⇒ claimant IS NULL ∧ delivered=0`；`claimed ⇒ claimant NOT NULL`；`done ⇒ claimant NOT NULL ∧ delivered≥need`。

---

## 4. 行状态机（3 态）

```
        claim（任意登录玩家）            set_delivery delivered≥need（认领人）
  open ──────────────────────▶ claimed ──────────────────────────▶ done
   ▲ ▲                            │  ▲                                │
   │ │   release（认领人/拥有者）  │  │  reject delivered=0（拥有者）    │
   │ └───────────────────────────┘  └────────────────────────────────┘ │
   └──────────── release（拥有者，从 done 直接释放）───────────────────┘
```

| 转移 | 端点 | 触发者 | 副作用 |
|---|---|---|---|
| open→claimed | `POST .../claim` | 任意登录玩家 | 置 claimant=self、delivered=0 |
| claimed/done→done/claimed | `PATCH .../delivery` | 认领人 | 设 delivered；`≥need`→done，`<need`→claimed |
| claimed/done→open | `POST .../release` | 认领人自放 或 拥有者 | 清 claimant、delivered=0 |
| done→claimed | `POST .../reject` | 拥有者 | delivered 归零，claimant 保留重做 |

- **lock 模式**：认领人「标备齐」= 一次性 `delivered=need`→done；「取消备齐」= `delivered=0`→claimed。
- **progress 模式**：认领人多次「上报交付」累加 delivered；`≥need` 自动转 done。
- **拥有者改 need_qty（upsert）时已认领**：`delivered` 按新 need 封顶；`delivered≥新need` 且状态∈{claimed,done}→done；原 done 但 `delivered<新need`→claimed。
- 并发由 `select ... with_for_update()` 行锁兜底；非法转移抛 `SheetRowConflict`→409。

---

## 5. 端点

### 5.1 表级

| 方法 | 路径 | 鉴权 | body / query | 成功 | 说明 |
|---|---|---|---|---|---|
| POST | `/sheets` | JWT | `SheetCreateRequest{title}` | 201 `SheetDetail` | 建表，owner=self |
| GET | `/sheets` | JWT | `?owner=me`（只看自己） | 200 `[SheetSummary]` | 列所有表（含他人表可读） |
| GET | `/sheets/{sheet_id}` | JWT | `?format=csv` | 200 `SheetDetail` \| `text/csv` | 表详情；`format=csv` 返回单表 CSV |
| PATCH | `/sheets/{sheet_id}` | JWT·**owner** | `SheetPatchRequest{title}` | 200 `SheetDetail` | 改标题 |
| DELETE | `/sheets/{sheet_id}` | JWT·**owner** | — | 204 | 删表（级联 rows + 认领） |

### 5.2 行级

| 方法 | 路径 | 鉴权 | body | 成功 | 说明 |
|---|---|---|---|---|---|
| PUT | `/sheets/{sheet_id}/rows` | JWT·**owner** | `RowUpsertRequest` | 200 `RowDetail` | upsert（按 item_name）；新建=open，更新保留 status/claimant/delivered |
| DELETE | `/sheets/{sheet_id}/rows/{row_id}` | JWT·**owner** | — | 204 | 删行 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/claim` | JWT | — | 200 `RowDetail` | 任意玩家认领（open→claimed） |
| PATCH | `/sheets/{sheet_id}/rows/{row_id}/delivery` | JWT·**认领人** | `RowDeliveryRequest{delivered_qty}` | 200 `RowDetail` | 上报交付量 |
| POST | `/sheets/{sheet_id}/rows/{row_id}/release` | JWT·**认领人\|owner** | — | 200 `RowDetail` | 解除锁定（→open） |
| POST | `/sheets/{sheet_id}/rows/{row_id}/reject` | JWT·**owner** | — | 200 `RowDetail` | 打回（done→claimed） |

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
    item_name: str           # 1..128
    need_qty: int = 0        # ≥0
    mode: int = 0            # 0=lock | 1=progress
    sort_order: int = 0      # ≥0

class RowDeliveryRequest(BaseModel):
    delivered_qty: int       # ≥0

class RowDetail(BaseModel):
    id: int
    item_name: str
    need_qty: int
    mode: int                # 0|1
    status: str              # open|claimed|done
    claimant_uuid: UUID | None
    claimant_name: str | None
    delivered_qty: int
    sort_order: int
    updated_at: datetime

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

| 动作 | 拥有者 | admin/owner 角色 | 认领人 | 其他登录玩家 |
|---|---|---|---|---|
| 读（列表/详情/单表 CSV） | ✅ | ✅ | ✅ | ✅ |
| 改表/行 upsert/删行/删表 | ✅ | ✅ | ❌ | ❌ |
| 认领 claim | ✅ | ✅ | — | ✅ |
| 上报交付 / 标备齐 | 仅当自己是认领人 | 仅当自己是认领人 | ✅ | ❌ |
| 解除锁定 release | ✅ | ✅ | ✅（自放） | ❌ |
| 打回 reject | ✅ | ✅ | ❌ | ❌ |
| 全量 CSV 导出 | — service token — | — | — | — |

---

## 8. 错误码

| 状态码 | 场景 |
|---|---|
| 401 | 缺/错 Bearer JWT；全量导出缺/错 service token |
| 403 | 权限不足（非 owner 改表/行；非认领人 delivery；非 owner reject/release 他人锁） |
| 404 | 表/行不存在 |
| 409 | 状态非法转移（对 done 行 claim；对 open 行 reject；upsert 并发同名 insert 命中 UNIQUE） |
| 422 | 请求体校验失败（如 `mode` 非 0/1、`need_qty<0`、`title` 空） |

错误体统一 FastAPI 默认：`{"detail": "<message>"}`。

---

## 9. CSV 导出列

全量与单表 CSV 共用表头：

```
sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order
```

- `claimant_uuid` 为 null（open 态）时输出空串。
- mode/status 为字面值（`0`/`1`、`open`/`claimed`/`done`）。

---

## 10. 迁移

| 版本 | 说明 |
|---|---|
| `0004_sheets` | 建 `sheets` schema + `sheets`/`sheet_rows` 表（含 `done_flag`） |
| `0005_sheets_collab` | 加 `mode`/`status`/`claimant_uuid`/`delivered_qty`；旧 `done_flag=1`→`status='done'`；删 `done_flag`；加 `ix_sheet_rows_sheet_status`。**可逆**（downgrade 恢复 done_flag） |

---

*最后更新：2026-07-02（协作改进：认领/进度协作 + owner_name/claimant_name 显示）*
