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

## 2. 端点速查（11 个，前缀 `/v1/construction`）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/report` | reporter（见 §1） | 上报方块净放置 |
| GET | `/active-sheets` | 任意 auth player | 归因查询（追踪器启动/定期查） |
| GET | `/{sheet_id}/progress` | 任意 auth player | 进度展示 |
| GET | `/settings` | admin | 读运行时开关 |
| PATCH | `/settings` | admin | 改运行时开关 |
| GET | `/mod-sources` | admin | 服务端 mod 白名单 |
| POST | `/mod-sources` | admin | 加白名单（幂等） |
| DELETE | `/mod-sources/{name}` | admin | 删白名单 |
| POST | `/source/switch-server` | admin | 切某玩家**服务端**源 |
| POST | `/source/switch-self` | 玩家 JWT | 玩家切自己上报模式 |
| GET | `/source/me` | 玩家 JWT | 查活跃源 + 历史 |

admin 端点挂 `require_role("admin")`（后端 RBAC 真实拒绝 403，R-9/RS-2）。

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
  `客户端模组上报已被服主关闭` / **`方块不在项目材料清单内`（迭代 2 新增，见 §3.2）**。

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

**`GET /{sheet_id}/progress`** → `{sheet_id, account_totals: [...], breakdown: [...], material_completion: [...], timeline: [...]}`。
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

未来归档 md 加「施工贡献」`FunctionSection` + `contributions.png` 含施工占比 —— 本轮不做。

---

## 8. admin 设置（system.settings）

`GET/PATCH /settings`（admin）：5 个运行时开关（DB 值优先，回退 config 默认）：

| 键 | 默认 | 说明 |
|---|---|---|
| `allow_client_mods` | true | 客户端 mod 总开关（关 → JWT 通道全 skip + switch-self local 403） |
| `official_tracker_enabled` | true | 官方 MCDR 追踪器开关（关 → 无默认活跃源） |
| `allow_server_mods` | true | 第三方服务端 mod 开关（白名单独立，此为运行时闸） |
| `report_interval_seconds` | 30 | 追踪器 flush 频率（C-5） |
| `anti_cheat_threshold` | null | 防刷阈值占位（null = 不限；C-6 过滤归追踪器侧） |

PATCH 用 `exclude_unset=True`：仅写客户端实际提供的键；`anti_cheat_threshold=null` 表示关闭阈值（合法写入）。

---

## 9. 错误码

| HTTP | 场景 | 调用方处理 |
|---|---|---|
| 401 | token 缺失/非法；JWT 缺 `mod_id`（「not a mod token」）；`active_uuid` 缺/非已知玩家 | 检查凭证 |
| 403 | admin 端点非 admin；`X-Source-Id` 不在白名单；`allow_client_mods=false` 时 switch-self local | 改权限/开关 |
| 404 | `sheet_id` / player 不存在 | 拉新列表 |
| 409 | 显式 `sheet_id` 非 constructing（archived/collecting） | 提示阶段不符 |
| 422 | `placements` 空 / `qty<0` / switch-self local 缺 source_id / switch-server server_mod 非白名单 | 修请求体 |

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

*创建：2026-07-27。权威 schema = `/openapi.json`（FastAPI 自动生成）；行为契约以
`Backend/app/repositories/construction_repo.py` + `Backend/app/api/construction.py` 为准。
MCDR 默认追踪器实现见 §5（待 S-1 单独 PR）。

迭代 2 增量（2026-07-27）：§3.2 方块清单校验（清单外 skip）+ §4 `material_completion` 材料完成度 / `timeline` 时序快照字段 + §4.1 时序快照表（迁移 0018）+ §6 `dormant_sources` 休眠源查询。*
