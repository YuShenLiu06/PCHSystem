# 施工进度上报 · 端点参考

> `POST /v1/construction/report`：施工方（MCDR / 服务端 mod / 玩家客户端 mod）上报
> constructing 期内各玩家在项目区域的**方块净放置**（placed − broken），按
> (sheet × account × registry) 聚合，归档时喂给积分层 `BuildAScoreCalculator`
> （[scoring-settlement.md](../flows/scoring-settlement.md) §4）。
> 设计契约见 [`../flows/construction-progress.md`](../flows/construction-progress.md)。

**状态**：✅ 已实现（后端 + 前端 + 测试脚本）。**MCDR 默认方块追踪器（§5）待 S-1 单独 PR**——
本文「默认追踪器实现契约」段是其实现的对照基线。

---

## 1. 鉴权矩阵（信任边界 = 是否在 MC 服务端）

| 上报源 | 头 | 能写谁 | source 标识 |
|---|---|---|---|
| **MCDR（官方默认）** | `X-Service-Token` | 多玩家 batch | `{mcdr, official}`（**无** `X-Source-Id` → 固定官方锚） |
| **服务端 mod**（第三方）| `X-Service-Token` + `X-Source-Id=<name>` | 多玩家 batch | `{server_mod, <name>}`（name 须在白名单，否则 403） |
| **玩家客户端 mod** | `Authorization: Bearer <mod-token JWT>` | **仅自己**（`player_uuid = JWT.active_uuid`，payload 值忽略） | `{client_mod, <JWT.mod_id>}` |
| 外部第三方 | ❌ 不开放 | — | — |

**铁律**（同 [`submit-extension.md`](./submit-extension.md) §鉴权）：
- service-token 绝不发第三方（R-11）；服务端 mod 复用 service-token + `X-Source-Id` 区分。
- 玩家客户端 mod 走 **mod-token JWT**（必带 `mod_id` claim，缺 → 401「not a mod token」），
  强制 `player_uuid = active_uuid`——不能代他人上报。
- **H-2**：`Authorization` 头存在（即便非 Bearer/非法）只走 JWT 通道报 401，**绝不静默降级**到 service-token。
- mod-token 签发流（带 `mod_id` 的 JWT）属 **MCDR PR**（`!!PCH mod-token` 出码 → Web 兑换，复用 bind 双向短码范式）。

> 与 `submit-batch` 的区别：`submit-batch` 两通道都解析成**单** actor；本端点 service-token
> 通道接受**多玩家** batch（actor 由 payload 每条 entry 决定），故不复用 `get_current_player`，
> 专用 `get_construction_reporter`。

---

## 2. 端点速查（19 个，前缀 `/v1/construction`）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/report` | reporter（见 §1） | 上报方块净放置 |
| GET | `/active-sheets` | 任意 auth player | 归因查询（追踪器启动/定期查） |
| GET | `/{sheet_id}/progress` | 任意 auth player | 进度展示 |
| GET | `/settings` | admin | 读运行时开关 |
| PATCH | `/settings` | admin | 改运行时开关 |
| GET | `/mod-sources` | admin | 服务端 mod 白名单 |
| POST | `/mod-sources` | admin | 加白名单（幂等） |
| PATCH | `/mod-sources/{name}` | admin | 逐源启停 `{enabled}`（迭代 3） |
| DELETE | `/mod-sources/{name}` | admin | 删白名单 |
| POST | `/source/switch-server` | admin | 切某玩家**服务端**源 |
| POST | `/source/switch-self` | 玩家 JWT | 玩家切自己上报模式 |
| GET | `/source/me` | 玩家 JWT | 查活跃源 + 历史 + 休眠源 |
| GET | `/me/reports` | 任意 auth player（须绑账号） | 个人上报历史（迭代 2，`placement_snapshots` 投影） |
| **GET** | **`/me/report-events`** | 任意 auth player（须绑账号） | **个人完整事件流水（迭代 5，`accepted` + 所有 `skipped` reason）** |
| **GET** | **`/me/construction`** | 任意 auth player | **查自己当前活跃加入的施工项目（迭代 4）** |
| **POST** | **`/me/join`** | 任意 auth player（须绑账号） | **手动加入 sheet（迭代 4）** |
| **POST** | **`/me/switch`** | 任意 auth player（须绑账号） | **切换到指定 sheet（迭代 4，leave + join 同事务）** |
| **POST** | **`/me/leave`** | 任意 auth player | **退出当前活跃加入（迭代 4，幂等空态）** |
| **POST** | **`/active-by-uuids`** | service-token 单头 | **批量 UUID → 活跃 sheet_id（迭代 4，tracker 按玩家路由用，非敏感）** |

admin 端点挂 `require_role("admin")`（后端 RBAC 真实拒绝 403，R-9/RS-2）。

> **路由顺序**：`/me/...` 字面路由 + `/active-by-uuids` 必须在 `/{sheet_id}/progress` 之前注册（FastAPI 按声明顺序匹配），避免被 `{sheet_id}` 路径参数吞没——见 [`Backend/app/api/construction.py`](../../../Backend/app/api/construction.py) 末尾注释。

---

## 3. 上报（`POST /v1/construction/report`）

**请求**：
```json
{
  "sheet_id": 42,
  "placements": [
    {"player_uuid": "00000000-...", "registry_id": "minecraft:stone", "placed_qty": 32, "broken_qty": 4},
    {"player_uuid": "11111111-...", "registry_id": "minecraft:glass", "placed_qty": 8, "broken_qty": 0}
  ]
}
```

- `sheet_id`：非空 → 显式归因（须 constructing，否则 404/409）；`null` → 启发式归因（恰 1 个 constructing 自动归因，0 或 >1 全 skip）。
- `placements`：1..2000 条；`placed_qty`/`broken_qty` 均 `≥ 0`；重复 `(player_uuid, registry_id)` 自动求和聚合。
- JWT 通道：`player_uuid` 被 server **强制覆盖**为 `active_uuid`（payload 值忽略）。
- service-token 通道：`player_uuid` 逐个验证存在 + 已绑 Web 账号（未绑 → skip `玩家未绑 Web 账号`）。

**响应**（200）：
```json
{
  "sheet_id": 42,
  "attribution_source": "explicit",
  "totals": {"accepted": 2, "skipped": 0},
  "outcomes": [
    {"player_uuid": "00000000-...", "registry_id": "minecraft:stone",
     "action": "accepted", "reason": "", "net_delta": 28},
    {"player_uuid": "11111111-...", "registry_id": "minecraft:glass",
     "action": "skipped", "reason": "玩家当前由其他源上报", "net_delta": 0}
  ]
}
```

- `attribution_source`：`explicit` / `heuristic` / `none`（0 或 >1 个 constructing）。
- `action`：`accepted`（落库）/ `skipped`（附 reason，不写库）。
- skip reason 全集：`当前无施工中项目` / `多个施工项目并发，须显式指定 sheet_id` /
  `玩家不存在` / `玩家未绑 Web 账号` / `玩家当前无活跃上报源` / `玩家当前由其他源上报` /
  `客户端模组上报已被服主关闭` / `方块不在项目材料清单内`（迭代 2 新增，见 §3.2）/
  **`已达材料上限`（迭代 4 新增，见 §3.3）**。

### 3.2 方块清单校验（迭代 2）

**迭代 2 在后端加一道防线**：每条 placement 落库前，校验 `registry_id` 必须在该 sheet 的收集清单内——
即 `sheet_rows.registry_id` 集合（**含子物品**：顶层行的 `registry_id` + 子行的 `registry_id`）。
不在清单内的方块 → `action="skipped"`（reason `方块不在项目材料清单内`），**不落库**。

设计动机：

- **与 C-6 叠加（双保险）**：C-6 是「追踪器侧自过滤空气/水」；本层是「后端按清单再挡」。追踪器漏过/恶意客户端 mod 故意上报非清单方块（如刷石机产出的圆石不属本项目材料）→ 后端拦下，不污染 `placement_records`，不影响积分结算。
- **不强制清单**：清单是「项目方声明的需求集合」，**不可省**——若该 sheet 的 `sheet_rows.registry_id` 集合为空（空项目/异常），所有 placement 全 skip。建议项目方在 collecting 期填齐清单后再 advance 到 constructing。
- **YAGNI**：暂不区分「清单内但 need 已满」（仍允许落库，超量归档时按 net_qty 算贡献）；暂不做按位置 box 裁剪（fallback 留给追踪器侧）。

**实现要点**：
- 单条 placement 校验：`registry_id ∈ sheet.registry_id_set`（cache 一次，本批 placement 共享，避免 N+1）。
- skip 不计入 `totals.accepted`，但 `totals.skipped` 自增；前端按 reason 折叠展示。
- **同 C-7 的关系**：单源校验先于清单校验（非活跃源 → 全 skip `由其他源上报`，不进清单校验）；归因校验先于清单校验（无 sheet_id → 全 skip `无施工中项目`）。

### 3.3 按材料封顶（迭代 4，迁移 0022 回填）

**跨账号合计净量不得超过该材料需求总量**——`sum(placement_records.net_qty) WHERE sheet_id=$sheet AND registry_id=$rid` 上限 = `sum(sheet_rows.need_qty)` 同口径聚合（含子物品 `registry_id`，与 §4 `material_completion` 同口径）。

逐条处理（[`construction_repo.submit_report`](../../../Backend/app/repositories/construction_repo.py) §「按材料封顶」分支）：

- **delta > 0（净放置）**：`accepted = min(delta, available)`，`available = max(need − 当前跨账号合计 net, 0)`。
  - `available = 0`（已达上限）→ **整条 skip**（reason `已达材料上限`，`net_delta=delta`）。
  - `accepted < delta`（部分接受）→ 落 accepted 条目（`net_delta=accepted`），**且**额外 emit 一条 skipped 条目（`net_delta=over`），让玩家在 `/me/report-events` 看到「这部分因满额被拒」。
- **delta ≤ 0（拆毁/中性）**：照常接受并**释放容量**（`material_totals[rid]` 同步下调），不限上限。拆毁后再次放置可重新吃回释放的容量。
- **同批次内共享容量**：`material_totals` 是逐条累加的内存字典，本批次先后顺序影响单条可用量；下一批次重新从 DB 读最新合计。

设计动机：
- **避免超量数据污染积分**：归档时 `BuildAScoreCalculator` 读 `placement_records` 聚合，若某材料合计 > 需求会过度奖励。封顶由后端兜底，无需追踪器侧实现。
- **YAGNI**：暂不区分「清单内但 need=0」（need=0 时 available 恒 0，净放置全 skip；拆毁照常落库）。
- **历史数据回填**：迁移 0022 一次性 clamp 已存超量——对每个 `(sheet_id, registry_id)` 当前 `sum(net_qty) > sum(need_qty)` 的材料，按账号**等比例**下调每条 `placement_records.net_qty`（同时同量下调 `placed_qty` 保持 `net_qty == placed_qty − broken_qty` 不变量），使合计 = need。clamp 不可逆（原始超量数字已丢失），`downgrade` no-op，如需还原须从备份恢复。

**与 §3.2 清单校验的关系**：清单校验先于封顶（`registry_id ∉ sheet_rows.registry_id 集合` → skip `方块不在项目材料清单内`）；封顶只针对清单内材料。

---

### 3.1 严格单源（C-7，用户拍板）

**每玩家同时仅一个活跃上报源**（防多源重复计数）：
- 玩家无 `player_sources` 记录时，默认活跃 = `{mcdr, official}`（仅当 `official_tracker_enabled=true`；
  否则无默认 → 任何上报 skip `玩家当前无活跃上报源`）。
- **`/report` 不隐式切源**：上报方 ≠ 玩家当前活跃源 → 该玩家 entries 全 skip
  （reason `玩家当前由其他源上报`）。切源只走显式端点（§6）。
- 落库按 `(sheet_id, account_id, registry_id)` upsert 聚合：`net_qty += placed-broken`
  （允许负）。`account_id = player.web_account_id`（R-5，离线改名/换 UUID 不丢）。

---

## 4. 归因查询 + 进度（GET）

**`GET /active-sheets`** → `{sheets: [{id, title}], heuristic_eligible: bool}`。
`heuristic_eligible=true`（恰 1 个 constructing）→ 追踪器可不带 `sheet_id` 上报。

**`GET /{sheet_id}/progress`** → `{sheet_id, account_totals: [...], breakdown: [...], material_completion: [...], timeline: [...], construction_started_at, archived_at}`。
- `account_totals`：按 Web 账号聚合（`account_id`/`display_name`/`placed_qty`/`broken_qty`/`net_qty`）。
- `breakdown`：按 account × registry 明细。
- `material_completion`（**迭代 2 新增**）：材料完成度——按 `registry_id` 聚合 placement 并对照该 sheet 的 `sheet_rows` 清单（含子物品 `registry_id`），结构：
  ```json
  [
    {"registry_id": "minecraft:stone", "item_name": "石头",
     "need_qty": 64, "net_qty": 48, "completion_pct": 75.0},
    {"registry_id": "minecraft:oak_log", "item_name": "橡木原木",
     "need_qty": 0, "net_qty": 16, "completion_pct": null}
  ]
  ```
  - `completion_pct` 视觉封顶 **100.0**（`round(min(100.0, net_qty / need_qty × 100), 1)`）—— 超量交付也只展示 100%，**积分层**仍读真实净量（详见 §7）。
  - `need_qty = 0`（清单未设需求、纯 progress 行、或非清单方块但 net_qty>0）→ `completion_pct = null`（前端展示「—」）。
  - 子物品：按子行 `registry_id` 单列一项，`need_qty = ceil(qty_per_unit × 父行 need_qty)`（与 [sheets.md](./sheets.md) §14 子物品不变量对齐）。
- `timeline`（**迭代 2 新增**）：时序快照——按 `construction.placement_snapshots` 表升序 limit 200，结构 `[{account_id, total_net, recorded_at}]`，每次 report 落库后为「本轮 accepted 的 account」各写一条（详见 §6.1）。
  - 展示用、非权威：失败仅日志、不阻断 report；缺数据时前端按 `account_totals` 兜底（折线退化为单点）。
- `construction_started_at` / `archived_at`（**迭代 4 新增**，迁移 0020 加 `sheets.sheets.constructing_at`）：分别取施工开始时间 + 归档时间。供前端折线图 xAxis 范围（左沿贴施工开始；右沿停在归档或当前时间）+ 归档 timeline 点亮「进入施工」段。直跳归档（`collecting → archived` 跳过 constructing）时 `construction_started_at = null`。迁移 0020 回填：已 `status='constructing'` 的行近似取 `updated_at`，已 `archived` 的行不回填（timeline 退化到「创建 → 归档」，与原行为一致）。

### 4.1 时序快照（迭代 2，迁移 0018）

> 独立小节：`construction.placement_snapshots` 表（迁移 0018，`down_revision=0017`）—— 服务端**展示用**时序数据，非权威（业务库 `placement_records` 仍是单一权威源）。

**表结构**：
| 列 | 类型 | 说明 |
|---|---|---|
| `snapshot_id` | BIGINT PK | 自增 |
| `sheet_id` | BIGINT FK→sheets.sheets.id ON DELETE CASCADE | 所属项目 |
| `account_id` | BIGINT FK→users.web_accounts.id ON DELETE CASCADE | 归属账号（R-5） |
| `total_net` | INTEGER NOT NULL | 该账号当前在 `placement_records` 累计净量（`sum(net_qty)`）|
| `recorded_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | 写入时刻 |

索引：`(sheet_id, recorded_at)` 复合索引（进度端点 timeline 查询路径）。

**写入时机**（report 端点内，落 placement 完成后）：
1. 计算**本轮 accepted** 涉及的 `(sheet_id, account_id)` 集合（skip 不写）。
2. 对每个 account 执行 `INSERT INTO placement_snapshots (sheet_id, account_id, total_net) SELECT $sheet, $account, COALESCE(SUM(net_qty), 0) FROM placement_records WHERE sheet_id=$sheet AND account_id=$account`。
3. 批量拼一条 SQL（多 account 用 `UNION ALL` 或 `VALUES (...)` 子句一次性提交）。

**best-effort 语义**：
- snapshot 写失败**仅记日志**，不回滚已落的 placement、不阻断 report 200 返回。
- 不写 snapshot ≠ 数据丢失：`placement_records` 始终是权威源，前端 timeline 缺段时降级展示。
- 不补写历史：迁移 0018 不回填（旧行无时序点，timeline 仅含迁移后的新写入）。

**读取端点**：`GET /{sheet_id}/progress` 的 `timeline` 字段——`SELECT account_id, total_net, recorded_at FROM placement_snapshots WHERE sheet_id=$sheet ORDER BY recorded_at ASC LIMIT 200`。

**与归档/结算的关系**：归档时 `placement_snapshots` 随 sheet CASCADE 删除（R-1 单一权威源原则——归档 md 不消费 timeline，仅读 `placement_records` 聚合）。timeline 是**运维/展示**用途，**不参与积分计算**。

---

### 4.2 加入施工端点（迭代 4，迁移 0021）

5 个端点（前缀 `/v1/construction`）：

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/me/construction` | 任意 auth player | 查自己当前活跃加入的施工项目（Me.vue「当前施工项目」卡片） |
| POST | `/me/join` | 任意 auth player（须绑账号） | 手动加入 sheet |
| POST | `/me/switch` | 任意 auth player（须绑账号） | 切换到指定 sheet（leave 旧 + join 新同事务） |
| POST | `/me/leave` | 任意 auth player | 退出当前活跃加入（幂等空态） |
| POST | `/active-by-uuids` | service-token 单头 | 批量 UUID → 活跃 sheet_id（tracker 按玩家路由用，非敏感） |

**响应**（`/me/construction` / `/me/join` / `/me/switch` / `/me/leave`）：`MyConstructionResult`：
```json
{
  "active": {
    "sheet_id": 42,
    "sheet_title": "主城北门",
    "joined_at": "2026-07-28T10:30:00Z",
    "join_source": "manual"
  }
}
```
未加入 / 未绑账号（`/me/construction`、`/me/leave` 幂等）→ `active = {sheet_id: null, sheet_title: null, joined_at: null, join_source: null}`。

**`/me/join` 行为**（[`join_construction`](../../../Backend/app/repositories/construction_repo.py) `source='manual'`）：
- 未绑账号 → 403；sheet 不存在 → 404；archived → 409。
- 已活跃加入**本 sheet** → 幂等返回当前状态（不报错；保留首行 `join_source`）。
- `enforce_single_construction=True`（默认）且已活跃加入**他 sheet** → **409**（`ParticipantConflict`，body 含 `current_sheet_id`）。
- `enforce_single_construction=False` → 自动切换（旧行 `left_at=now` / `left_reason='switched'`，新行插入）。

**`/me/switch` 行为**：等价 `/me/join` enforce=False 的自动切换路径——先 `leave_construction(reason='switched')` 再 `join_construction(source='manual')`，**同事务原子**。并发极端场景（leave→join 之间他人插入活跃行）join 抛 `ParticipantConflict` → 409；未到 commit 即抛 → 整事务回滚（leave 一并撤销）。

**`/me/leave` 行为**：UPDATE 当前活跃行 `left_at=now` / `left_reason='manual_leave'`；未活跃加入 → 幂等空态。

**`/active-by-uuids` 行为**（service-token 单头）：body `{player_uuids: [uuid, ...]}`（1..500），响应 `{mappings: {uuid: sheet_id | null}}`。经 `Player.uuid → web_account_id → construction.participants 活跃行 → sheet_id` 解析；未绑账号 / 未加入 → `null`。**非敏感数据**（仅 sheet_id，无 account 信息），tracker 按玩家路由上报用。

---

### 4.3 个人查询端点（迭代 2 + 迭代 5）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/me/reports` | 任意 auth player（须绑账号） | 个人上报历史（迭代 2，`placement_snapshots` 投影） |
| GET | `/me/report-events` | 任意 auth player（须绑账号） | 个人完整事件流水（迭代 5，`report_events` 投影） |

两端点均查 `limit`（默认 50，`1..200`），按时间倒序，跨所有项目。归因锚 account（R-5）：未绑 Web 账号 → 403。展示用，非权威源（权威仍是 `placement_records` 聚合）。

**`/me/reports`**：取 `placement_snapshots`（每次 report 成功落一条），结构：
```json
[
  {"recorded_at": "2026-07-28T10:31:00Z", "sheet_id": 42, "sheet_title": "主城北门",
   "total_net": 128, "delta": 28}
]
```
`delta` = 本次相对同项目上一条快照的净增量（最旧/首条为 `null`）。

**`/me/report-events`**（迭代 5 新增）：取 `report_events`（每次 report 的 `PlacementOutcome` 逐条落库），结构：
```json
[
  {"recorded_at": "2026-07-28T10:31:00Z", "sheet_id": 42, "sheet_title": "主城北门",
   "registry_id": "minecraft:stone", "action": "accepted", "reason": "", "net_delta": 28},
  {"recorded_at": "2026-07-28T10:31:00Z", "sheet_id": 42, "sheet_title": "主城北门",
   "registry_id": "minecraft:oak_log", "action": "skipped", "reason": "已达材料上限", "net_delta": 12}
]
```
- `sheet_id` / `sheet_title` nullable：归因失败 / 客户端 mod 全局关闭场景照落（`sheet_title` 时间线展示回退显示「未归因」）。
- `registry_id` nullable：防御性（outcome 都带）。
- `action ∈ {accepted, skipped}`；`reason`：`accepted=""`；`skipped` 为中文 reason（如 `已达材料上限` / `方块不在项目材料清单内` / `玩家当前由其他源上报` / `客户端模组上报已被服主关闭` 等，全集见 §3）。
- `net_delta`：accepted=本次计入；skipped=被拒/尝试量（部分接受场景的 over 部分）。

**两者区别**：
- `reports` = **已接受**上报的累计快照（按 account 一条），折线图源；
- `report_events` = **每条** outcome 都落（accepted + 所有 skip reason），让玩家看到「为什么我的上报被拒」。

> 仅 bound 玩家（`web_account_id` 非空）落 `report_events`（`account_id` 是查询锚）；未绑玩家不落事件——`/me/report-events` 对未绑账号返 403。

---

## 5. 默认追踪器实现契约（供 MCDR PR 照实现）

> 本段固化本 PR 已落地的事实，MCDR 默认方块追踪器（construction-progress.md §5）实现 PR **必须遵守**。

| # | 契约 | 值 / 约定 |
|---|---|---|
| **C-1** | 默认追踪器源标识 | service-token 通道 + **不带** `X-Source-Id` → 后端识别为 `{source_type:"mcdr", source_id:"official"}`（官方源固定锚） |
| **C-2** | 显示名（前端映射，不落库） | `mcdr`→`官方方块追踪器`；`client_mod`→`客户端模组`；`server_mod`→白名单 `name`。`player_sources` 只存 type+id |
| **C-3** | 上报 API | `POST /v1/construction/report`，body 见 §3，头 `X-Service-Token` |
| **C-4** | 归因查询 | 启动/定期调 `GET /active-sheets`：`heuristic_eligible=true` → 自动归因；否则自带 `sheet_id` |
| **C-5** | flush 频率 | 读 `GET /settings` 的 `report_interval_seconds`（默认 30s）定 flush 间隔 |
| **C-6** | 方块过滤 | **追踪器上报前自行过滤**（空气/水不计），后端只记收到的 registry_id 净量。过滤清单由追踪器定 |
| **C-7** | 单源互斥（严格） | report 不隐式切（§3.1）；切源走 §6 端点 |
| **C-8** | R-12 | HTTP 上报含超时 + 重试 + 失败本地缓冲；事件回调 `@new_thread("pch_construction")`，不阻塞 tick |
| **C-9** | 切源端点 | `POST /source/switch-server`（admin）/ `POST /source/switch-self`（玩家）/ `GET /source/me`（见 §6） |
| **C-10** | 本地源标识 | JWT `mod_id` claim 区分多 client mod；mod-token 签发流属 MCDR PR（`!!PCH mod-token`） |
| **C-11** | 加入施工联动（迭代 4） | 默认 tracker 启动时调 `POST /active-by-uuids` 按玩家批量解析活跃 sheet_id（无 record → skip 该玩家），无需逐玩家查 `/me/construction`；tracker 不直接落 `participants`，加入由 Web/MCDR 显式 join 或 `sheet_repo._maybe_auto_join` 自动触发 |
| **C-12** | 按材料封顶后端防线（迭代 4） | tracker 不需要本地维护「是否超 need」——后端 `submit_report` 逐条按 `(sheet_id, registry_id)` 跨账号合计净量封顶（§3.3）；满额 placement 返 `skipped(reason=已达材料上限)`，tracker 侧可记日志但不必重试 |
| **C-13** | 单账号单活跃项目（迭代 4） | tracker 经 `enforce_single_construction=True` 默认获「同账号同时仅 1 个活跃项目」保证（DB `uq_participants_active` partial unique index 兜底）；tracker 不需要自己维护单项目约束 |

**S-1 待核实**（construction-progress.md §9）：方块放置/破坏事件名、回调签名、ctx 字段、
ServerInterface 能力、线程模型、离线 UUID 一致性——实现前必查
<https://docs.mcdreforged.com/zh-cn/latest/>。

---

## 6. 切源端点（D9）

**`POST /source/switch-server`**（admin）：body `{player_uuid, source_type:"mcdr"|"server_mod", source_id?, reason?}`。
- `mcdr` → `source_id` 强制 `official`。
- `server_mod` → `source_id` 必填且须在白名单（否则 422）。

**`POST /source/switch-self`**（玩家 JWT）：body `{mode:"server"|"local", source_id?, reason?}`。
- `mode="server"` → 退回官方代报（`{mcdr, official}`）。
- `mode="local"` → `source_id` 必填（= mod_id）；`allow_client_mods=false` → 403。
  归属校验留 mod-token PR（本端点接受玩家声明的 mod_id）。

**`GET /source/me`**（玩家 JWT）：`{active: {source_type, source_id, is_default}, history: [...], dormant_sources: [...]}`。
`is_default=true` 表示无显式记录、走默认 mcdr/official。

`dormant_sources`（**迭代 2 新增**）：休眠源列表——`[{source_id, last_active_at}]`。
- **「休眠源」定义**：曾活跃但当前 `disabled_at` 非空的 `client_mod` 源（玩家此前用过的客户端 mod 上报模式，后被切回 mcdr/server_mod 或被 admin 切走）。
- **去重**：按 `source_id` 去重，每个 `source_id` 取最近一次 `activated_at`（即 `disabled_at` 触发前的激活时间）。
- **严格单源不变**：休眠源列表只是**历史展示**，不影响 §3.1 严格单源校验——`/report` 的活跃源判定仍只看 `player_sources` 当前唯一活跃记录。玩家想切回某休眠源 → 走显式 `POST /source/switch-self` mode=local（唤醒、写新 history，旧 dormant 记录自然失效）。
- **用途**：前端在「上报源」控件列出休眠源，玩家可一键「切回某 mod_id」，无需重新输入 source_id（高频场景：临时切回官方追踪器调试后再切回客户端 mod）。

切换语义：旧活跃源 `disabled_at=now()` + 插新活跃 + 写 `player_source_history`（append-only 审计,
from/to/reason）。已是目标源 → no-op。

---

## 7. 归档 / 结算接入契约（D8，本轮仅固化接口 + hook）

> scoring-settlement.md §2/§4 约定：归档（advance→archived）后 `score_service.settle(sheet_id)`
> 聚合 `placement_totals` → `BuildAScoreCalculator`。**本轮不接 archive/settle**，但提前固化其消费的读接口，
> 使后续 PR 在归档处直接调用，零重构。

**稳定纯读契约**（已实现）：
```python
construction_repo.aggregate_placement_totals(session, sheet_id) -> list[PlacementTotal]
# PlacementTotal = {account_id: int, display_name: str, net_qty: int}
```
即 `BuildAScoreCalculator` 的 `placement_totals` 源（scoring-settlement.md §4 SettlementContext）。

**调用时机**：post-archive，best-effort（不阻塞归档；归档已 commit，结算失败仅记日志 + 通知，
事后 `resettle`——同 scoring-settlement.md §2）。

**接入点 hook**（本轮仅注释，不实现逻辑）：
- `Backend/app/services/archive/archive_sheet()` 归档成功后
- `Backend/app/api/sheets/lifecycle.py` advance→archived 处

均留 `# TODO(scoring): construction_repo.aggregate_placement_totals(sheet_id) → score_service.settle`。

**归档批量退出活跃参与者**（迭代 4，plan BLOCK 1.10）：`archive_sheet()` 在 advance→archived 同事务内调 [`construction_repo.close_all_participants(session, sheet_id, reason='archived')`](../../../Backend/app/repositories/construction_repo.py)，UPDATE 该 sheet 所有活跃参与者行 `left_at=now()` / `left_reason='archived'`（保留历史行，不 DELETE）。返回实际退出行数（日志用）。不嵌入 `advance_sheet`（单一职责；归档必经 archive service，RS-10）。不额外发「已退出施工」通知（archive 通知已涵盖）。

未来归档 md 加「施工贡献」`FunctionSection` + `contributions.png` 含施工占比 —— 本轮不做。

---

## 8. admin 设置（system.settings）

`GET/PATCH /settings`（admin）：6 个运行时开关（DB 值优先，回退 config 默认）：

| 键 | 默认 | 说明 |
|---|---|---|
| `allow_client_mods` | true | 客户端 mod 总开关（关 → JWT 通道全 skip + switch-self local 403） |
| `official_tracker_enabled` | true | 官方 MCDR 追踪器开关（关 → 无默认活跃源） |
| `allow_server_mods` | true | 第三方服务端 mod 开关（白名单独立，此为运行时闸） |
| `report_interval_seconds` | 30 | 追踪器 flush 频率（C-5） |
| `anti_cheat_threshold` | null | 防刷阈值占位（null = 不限；C-6 过滤归追踪器侧） |
| **`enforce_single_construction`** | **true** | **同账号是否同时仅 1 个活跃施工项目（迭代 4 第 6 项开关）**：True 时 `/me/join` 冲突抛 409（要求玩家显式 `/me/switch`）；False 时自动切换。**仅约束默认 MCDR 追踪器 + Web/MCDR 的 join 流程**；`POST /report` API 零 project 校验不变——第三方/玩家客户端 mod 源仍可同时多项目上报 |

PATCH 用 `exclude_unset=True`：仅写客户端实际提供的键；`anti_cheat_threshold=null` 表示关闭阈值（合法写入）。

---

## 9. 错误码

| HTTP | 场景 | 调用方处理 |
|---|---|---|
| 401 | token 缺失/非法；JWT 缺 `mod_id`（「not a mod token」）；`active_uuid` 缺/非已知玩家 | 检查凭证 |
| 403 | admin 端点非 admin；`X-Source-Id` 不在白名单；`allow_client_mods=false` 时 switch-self local；`/me/join`/`switch`/`/me/report-events` 未绑账号 | 改权限/开关 |
| 404 | `sheet_id` / player 不存在 | 拉新列表 |
| 409 | 显式 `sheet_id` 非 constructing（archived/collecting）；**`/me/join`/`/me/switch` `enforce_single_construction=True` 冲突（`ParticipantConflict`，含 `current_sheet_id`）/ `/me/switch` 并发竞争** | 提示阶段不符 / 先 `/me/leave` 或 `/me/switch` / 重试 |
| 422 | `placements` 空 / `qty<0` / switch-self local 缺 source_id / switch-server server_mod 非白名单 / **`active-by-uuids` 空 / >500** | 修请求体 |

---

## 10. 示例

见 [`Scripts/test-construction-report.py`](../../../Scripts/test-construction-report.py)（零依赖 Python，
**兼上报源参考实现**）：bootstrap 玩家+项目 → service-token 多玩家上报 → JWT[mod_id] 上报 → 进度查询。

```python
# service-token 多玩家上报（MCDR / 服务端 mod 代报）
requests.post(f"{API}/v1/construction/report",
    json={"sheet_id": sid, "placements": [
        {"player_uuid": p1, "registry_id": "minecraft:stone", "placed_qty": 32, "broken_qty": 4}]},
    headers={"X-Service-Token": TOKEN}, timeout=10)
```

---

## 11. 数据模型（迭代 4-5 新增）

### 11.1 `sheets.sheets.constructing_at`（迁移 0020）

| 列 | 类型 | 说明 |
|---|---|---|
| `constructing_at` | TIMESTAMPTZ nullable | 进入施工时间戳（`collecting → constructing` 切换时写入；直跳归档时 null） |

回填策略：迁移时 `status='constructing'` 的行近似取 `updated_at`，已 `archived` 的行不回填（timeline 退化到「创建 → 归档」，与原行为一致）。

用途：进度图表 xAxis 左沿（前端 `ConstructionProgress.vue` 透传给 `TrendLineChart` 的 `startTime`）+ 归档 timeline 点亮「进入施工」段（原硬编码 `constructing_at=None` → 该行被过滤）。

### 11.2 `construction.participants`（迁移 0021）

玩家显式「加入施工」机制：每个 Web 账号同时最多活跃加入 1 个施工项目（`enforce_single_construction=True` 默认；DB 通过 partial unique index 兜底）。

| 列 | 类型 | 约束 |
|---|---|---|
| `id` | BIGINT PK | 自增 |
| `web_account_id` | BIGINT FK→`users.web_accounts.id` ON DELETE CASCADE | NOT NULL（R-5 account 主锚） |
| `sheet_id` | BIGINT FK→`sheets.sheets.id` ON DELETE CASCADE | NOT NULL |
| `joined_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | 加入时刻 |
| `left_at` | TIMESTAMPTZ nullable | 退出时刻（null = 仍活跃） |
| `join_source` | TEXT NOT NULL CHECK `IN ('auto','manual')` | `auto` = `sheet_repo._maybe_auto_join` 自动加入；`manual` = Web/MCDR 显式 join |
| `left_reason` | TEXT nullable | `manual_leave` / `switched` / `archived` / `auto_displaced` |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | 退出时显式刷新（model 无 onupdate） |

**约束 & 索引**：
- `uq_participants_active` UNIQUE `(web_account_id) WHERE left_at IS NULL` —— **one-at-a-time DB 铁律**（与 `uq_player_sources_active` 范式一致），同账号同时最多 1 个活跃项目
- `ix_participants_sheet_active(sheet_id) WHERE left_at IS NULL` —— 按 sheet 查活跃参与者（归档批量退出用）
- `ix_participants_account_active(web_account_id, sheet_id) WHERE left_at IS NULL` —— `/me/construction` 查询路径
- `ix_participants_account_time(web_account_id, joined_at)` —— 含历史行，「我的施工历程」查询用

**主表 append-only**：仅 UPDATE `left_at` / `left_reason` / `updated_at`，**从不 DELETE**（保留历史行供「我的施工历程」，仿 `player_sources`）。

**加入路径**：
- `join_source='auto'`：备货(`collecting`)/施工(`constructing`)阶段认领(lock `claim`)与上交(progress `contribute`/`batch_submit`)时由 [`sheet_repo._maybe_auto_join`](../../../Backend/app/repositories/sheet_repo.py) 触发——已在他项目 silent skip（`auto` 永不抛 `ParticipantConflict`）；失败 swallow 不阻断上交主流程
- `join_source='manual'`：玩家经 `/me/join` 或 `/me/switch` 显式加入（enforce=True 冲突 → `ParticipantConflict` → api 409）

**退出路径**：
- `left_reason='manual_leave'`：`/me/leave` 玩家主动退出
- `left_reason='switched'`：`/me/switch` 切换项目（或 enforce=False 时 auto-join 自动切换），旧行同步退出
- `left_reason='archived'`：归档经 `close_all_participants` 批量退出（与 `advance_sheet` 同事务）
- `left_reason='auto_displaced'`：预留（auto 切换的旧活跃行；当前实现 enforce=False 走 `'switched'`）

> **API project 维度零校验不变**：`POST /report` 跳过 join 直接落 `placement_records`，第三方/客户端 mod 源可同时多项目上报；`participants` 仅约束默认 MCDR 追踪器 + Web/MCDR 显式 join 流程。

### 11.3 `construction.report_events`（迁移 0023）

玩家可见的完整上报事件流水：`submit_report` 逐条产出的 `PlacementOutcome` 都落一行（仅 bound 玩家：未绑 Web 账号 / 玩家不存在 → 不落，`account_id` 是 `/me/report-events` 查询锚）。

| 列 | 类型 | 约束 |
|---|---|---|
| `id` | BIGINT PK | 自增 |
| `sheet_id` | BIGINT FK→`sheets.sheets.id` ON DELETE CASCADE | **nullable**（归因失败 / 客户端 mod 全局关闭等无 sheet 场景照落） |
| `account_id` | BIGINT FK→`users.web_accounts.id` ON DELETE CASCADE | **NOT NULL**（查询锚；未绑玩家不落事件） |
| `player_uuid` | UUID nullable | 玩家 UUID（防御性 nullable，落时必有） |
| `registry_id` | TEXT nullable | 方块 registry id（防御性 nullable，outcome 都带） |
| `action` | TEXT NOT NULL CHECK `IN ('accepted','skipped')` | 与 `PlacementOutcome.action` 同口径 |
| `reason` | TEXT NOT NULL | `accepted=""`；`skipped` 为中文 reason（全集见 §3） |
| `net_delta` | INTEGER nullable | accepted=本次计入；skipped=被拒/尝试量（部分接受场景的 over 部分） |
| `recorded_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | 落库时刻 |

**索引**：
- `ix_report_events_account_time(account_id, recorded_at DESC)` —— `/me/report-events` 查询路径
- `ix_report_events_sheet_time(sheet_id, recorded_at)` —— 按 sheet 回查（预留：项目维度审计 / 归档产物消费）

**与 `placement_snapshots` 的区别**：
- `snapshots`：每次 report 成功后为本轮 accepted 的 account 各写**一条**累计净放置（折线图数据源）；不含 skip 信息。
- `report_events`：逐条 outcome 落一行（accepted + 所有 skip），玩家个人时间线「我的上报历史」消费——让玩家看到「为什么我的上报被拒」。

**Append-only**（无 UPDATE/DELETE 端点），与 `placement_snapshots` 同为展示用辅助表（权威源仍是 `placement_records`）。R-5：锚 `account_id`。写入 best-effort + SAVEPOINT 隔离（失败仅日志、不阻断主事务——否则整次上报回滚、tracker 不推进 baseline → 增量堆积）。

---

*创建：2026-07-27。权威 schema = `/openapi.json`（FastAPI 自动生成）；行为契约以
`Backend/app/repositories/construction_repo.py` + `Backend/app/api/construction.py` 为准。
MCDR 默认追踪器实现见 §5（待 S-1 单独 PR）。

迭代 2 增量（2026-07-27）：§3.2 方块清单校验（清单外 skip）+ §4 `material_completion` 材料完成度 / `timeline` 时序快照字段 + §4.1 时序快照表（迁移 0018）+ §6 `dormant_sources` 休眠源查询。*

迭代 3 增量（2026-07-28）：`server_mod_sources` 加 `enabled` 字段（迁移 0019）+ §2 `PATCH /mod-sources/{name}` 逐源启停端点；`get_construction_reporter`（service-token+X-Source-Id）与 `switch-server` 校验 `enabled=true`（停用 → 403/422）；原 `allow_server_mods` 全局开关从未强制，schema 字段保留默认 true 不删；§4.1 `timeline` 改取**最近** 200 点（原取最早）。前端：折线前向填充（`utils/timelineFill.ts`，不再断线）+ 材料完成度分页排序（`utils/materialSort.ts`，「我贡献优先」按账号聚合 R-5）+ 数量用 `formatQty`（个/组/盒）+ `/register` 需登录守卫（避免 401 missing authorization）。管理员面板重构为「服务器上报源（插件）」卡片网格（官方追踪器降为默认插件 + 第三方 mod 逐卡启停）。*

迭代 4-5 增量（2026-07-28）：§2 端点表扩 19 个（新增 `/me/construction`·`/me/join`·`/me/switch`·`/me/leave`·`/me/report-events`·`/active-by-uuids`，路由顺序前置避让 `{sheet_id}`）+ §3.3 按材料封顶（跨账号合计 net 不得超过 sum(need)，部分接受 emit 双 outcome；迁移 0022 回填 clamp）+ §4 进度端点加 `construction_started_at`/`archived_at`（迁移 0020 `sheets.sheets.constructing_at`）+ §4.2 加入施工端点（manual join/switch/leave + 自动 join + active-by-uuids）+ §4.3 个人查询端点（`/me/reports` + `/me/report-events`）+ §5 追加 C-11/C-12/C-13 三项默认 tracker 实现契约 + §7 归档经 `close_all_participants` 批量退出活跃参与者 + §8 admin 设置加第 6 项 `enforce_single_construction`（默认 True，仅约束默认 tracker + Web/MCDR join 流程，`POST /report` 零 project 校验不变）+ §9 错误码补 409 `ParticipantConflict` / 422 active-by-uuids + §11 数据模型（迁移 0020/0021/0023 表 + 索引 + 不变量）。*
