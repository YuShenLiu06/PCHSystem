# 施工进度统计 · 端到端流程指南（设计契约）

> 本文件是施工进度统计层的**设计契约**：施工贡献怎么上报、怎么归因、怎么喂给积分层。本层 🚧 规划中（未实现），本文是后续实现的对照基线。
> 顶层索引见 [`../../architecture.md`](../../architecture.md) §7；红线见根 [`../../../CLAUDE.md`](../../../CLAUDE.md) §3（尤其 R-7 MCDR 纯客户端 / R-12 @new_thread）。

**读者**：要实现施工上报 / 写服务端 mod 上报源 / 接入玩家客户端 mod 的二次开发者。

**状态**：🚧 规划中。**关键 MCDR 方块事件 API 待 S-1 联网核实**（见 §9），实现前必查 <https://docs.mcdreforged.com/zh-cn/latest/>。

---

## 1. 一句话定位 + 设计取舍

`constructing` 期内，记录各玩家在项目区域的**方块净变化量**（放置 − 破坏），作为 A 类（建造）施工贡献，在项目归档时喂给 [积分层](./scoring-settlement.md) 的 `BuildAScoreCalculator`。

**核心取舍**（用户拍板）：
- **可插拔上报**：后端开放上报端点，官方默认实现 = MCDR 监听方块事件累计净放置；第三方/服主可写自己的上报源（服务端 mod / 玩家客户端 mod）
- **单项目启发式归因**：同一服务器同时进行两个大型项目概率较低 → 默认把 constructing 期内的方块变化归到当前唯一活跃项目；多项目并发时 fallback 走投影区域 box 裁剪（后续实现）
- **轻量防刷**：净放置（placed − broken）+ 每玩家单源 + 轻度告警（积分纯荣誉，不重度防）

> 与材料收集（sheets 协作，认领/上交）不同：施工统计是**被动观测**（玩家正常造，系统统计），不需要玩家显式操作。

---

## 2. 目标流程

```mermaid
sequenceDiagram
    participant Mod as 上报源（MCDR/mod）
    participant API as construction API
    participant Svc as construction_service
    participant DB as construction 表
    participant Score as score_service（终算时）

    Note over Mod: constructing 期内，玩家在项目区域造方块
    Mod->>API: POST /v1/construction/report（净放置 batch）
    API->>Svc: 鉴权分流 + 单源校验 + 归因
    Svc->>DB: upsert placement_records（按账号 × 方块聚合）
    Note over API,Svc: archived 时，score_service.settle 读 placement_records
    Score->>DB: aggregate placement → BuildAScoreCalculator（见 ./scoring-settlement.md）
```

**两个查询端点**：
- `GET /v1/construction/active-sheets`：MCDR 上报源查「当前该把数据归到哪个 sheet」（启发式归因用）
- `GET /v1/construction/{sheet_id}/progress`：查某项目当前施工进度（Web/MCDR 展示）

---

## 3. 鉴权矩阵（信任边界 = 是否在 MC 服务端）

| 上报源 | 鉴权 | payload | 约束 |
|---|---|---|---|
| **MCDR（官方默认）** | `X-Service-Token` | 多玩家聚合 batch | 信任边界内，代多个玩家上报 |
| **服务端 mod**（第三方）| `X-Service-Token`（约定路径读 token）| 多玩家聚合 batch | 服主 opt-in 白名单（Web 面板审）|
| **玩家客户端 mod** | 玩家 JWT（`Authorization: Bearer`）| **仅自己**（`player_uuid = JWT.active_uuid`）| 服主 opt-in + 玩家 `!!PCH` 开关 |
| **外部第三方** | ❌ **砍掉** | — | YAGNI |

**铁律**（同 [积分层](./scoring-settlement.md) §3）：
- service-token 绝不发第三方（R-11）；服务端 mod 复用 service-token（约定路径 `config/pch_system/sdk.properties` 读取，零配置）
- 玩家客户端 mod 走 JWT，**强制 `player_uuid = JWT.active_uuid`**——不能代他人上报
- 外部 API Key 通道整体砍除

### 3.1 每玩家单源策略

一个玩家同时**只接受一个活跃上报源**（防多源重复计数）：
- **自动切换**：新源首次带 JWT 上报即切，旧源置 `disabled`（可恢复，非黑名单）
- **手动切换**：`!!PCH construction switch <source>`（玩家自主）
- **审计**：切换历史落 `player_source_history`（谁/何时/从哪源切到哪源）

---

## 4. 核心抽象：上报端点契约

单端点 `POST /v1/construction/report`，**按鉴权头分流**（与积分层不同：积分层按角色分端点，本层同端点按头区分）：

```python
# 伪契约（实现时定 Pydantic schema）
class PlacementReport(BaseModel):
    sheet_id: int | None       # 上报源已知则填；None → service 启发式归因
    placements: list[PlacementEntry]

class PlacementEntry(BaseModel):
    player_uuid: UUID          # service-token 通道可代报；JWT 通道强制 = active_uuid
    registry_id: str           # 方块 registry id（namespace:path）
    placed_qty: int            # 该批次放置数
    broken_qty: int            # 该批次破坏数
    # net = placed - broken（防刷：破坏扣回）
```

**service 分流逻辑**：
- 头含 `X-Service-Token` → 校验 token → 多玩家 payload 信任
- 头含 `Authorization: Bearer` → 解 JWT → **强制覆盖** `entry.player_uuid = jwt.active_uuid`（忽略 payload 里的 uuid）
- 两者皆无 / 皆有 → 401（H-2 不静默降级，同 RS-8）

---

## 5. 官方默认实现：MCDR 方块变化量

> **前提（S-1 待核实）**：MCDR 提供方块放置/破坏事件钩子 + ServerInterface。本节是**设计假设**，实现前必查 <https://docs.mcdreforged.com/zh-cn/latest/>。事件名 / 回调签名 / ServerInterface 方法在本文中**未确定**，§9 是核实清单。

设计骨架（**非确定 API 签名，待 S-1 核实后填**）：

```python
# 伪代码 —— 事件名 / 回调签名 / ctx 字段均待 S-1 核实
@on_event(???_block_place)        # 待核实：MCDR 事件名
@new_thread("pch_construction")   # R-12：阻塞/耗时回调放新线程
def on_place(ctx):                # 待核实：ctx 字段（player / pos / block）
    record(player=ctx.player, registry=ctx.block, delta=+1)

@on_event(???_block_break)
@new_thread("pch_construction")
def on_break(ctx):
    record(player=ctx.player, registry=ctx.block, delta=-1)
```

**累计策略**：
- 维护内存 batch（玩家 × registry → 净量），定时（如 30s）或定量 flush 到 `/v1/construction/report`
- constructing 期外的事件不记（启动时 `GET active-sheets` 决定是否启用监听）
- **R-12 必守**：事件回调若跑在主线程，HTTP 上报必含超时 + 重试 + 失败回执，不阻塞 tick（同 sheets 通知轮询范式）

**单项目启发式归因**：`GET active-sheets` 返回当前 constructing 态 sheet（多个时按「最近活跃」或返回 None 让上报源自带 sheet_id）；默认假设同服同时一个大项目。

---

## 6. UI 分配（开关与审批分层）

| 功能 | 入口 | 理由 |
|---|---|---|
| **服务端 mod 白名单审批** | Web 管理面板（不进 `!!PCH`）| 服主权，玩家不该管 |
| **`official_tracker_enabled` 开关** | Web 管理面板 | 服主决定是否启用 MCDR 默认上报 |
| **`allow_server_mods` 开关** | Web 管理面板 | 服主决定是否接受第三方服务端 mod |
| **`allow_client_mods` 总开关** | Web 管理面板（默认 true）+ 玩家 `!!PCH` | 服主开总开关，玩家自主决定是否用客户端 mod |
| **玩家客户端 mod 管理** | `!!PCH construction sources list` / `!!PCH mod-token` / `!!PCH mod-token revoke` | 玩家自主权 |
| **玩家切源** | `!!PCH construction switch <source>` | 见 §3.1 单源策略 |

**开关默认值**：`allow_client_mods = true` / `official_tracker_enabled = true` / `allow_server_mods = true`（宽松默认，纯荣誉 + 白名单服，刷分影响可控）。

---

## 7. 数据模型（R-1 全 DB）

`construction` schema（规划中，迁移待定）：

| 表 | 用途 | 关键约束 |
|---|---|---|
| `player_sources` | 每玩家当前活跃上报源 | `(player_uuid)` 唯一（单源）；列 `source_id` / `source_type`（mcdr/server_mod/client_mod）/ `disabled_at` |
| `player_source_history` | 切换审计 | append-only；`(player_uuid, switched_at)` |
| `server_mod_sources` | 服务端 mod 白名单 | 服主审批后的 mod 标识 + 授权 token |
| `placement_records` | 上报记录（终算聚合源）| `(sheet_id, account_id, registry_id)` 聚合；列 `net_qty`（placed − broken）|

`system.settings`（key-value JSONB，运行时开关）：
- `construction.allow_client_mods` / `official_tracker_enabled` / `allow_server_mods`
- `construction.report_interval_seconds`（flush 频率）/ `anti_cheat_threshold`（轻度告警阈值）

config（`.env`）仅留连接串 / 密钥（R-11）/ 启动默认。

> `placement_records` 同时是 [积分层](./scoring-settlement.md) `BuildAScoreCalculator` 的数据源——跨 schema 只读聚合（R-1 不变：都在 PostgreSQL，后端独占）。

---

## 8. 二次开发指南

### 8.1 写一个上报源（服务端 mod）

1. 读 `config/pch_system/sdk.properties`（`PCH_API_URL` + `PCH_SERVICE_TOKEN`），零配置接入
2. 服主在 Web 面板把你的 mod 加白名单（`server_mod_sources`）
3. 实现方块事件 → 净量 batch → `POST /v1/construction/report`（带 `X-Service-Token`，多玩家 payload）
4. HTTP 必含超时 + 重试 + 失败本地缓冲（R-12 精神：别阻塞游戏 tick）

### 8.2 写一个玩家客户端 mod

1. 玩家游戏内 `!!PCH mod-token` 出码 → Web 兑换 JWT（复用 `!!PCH bind` 双向短码范式）
2. mod 带 `Authorization: Bearer <jwt>` 调 `/v1/construction/report`，payload 只填自己（server 强制 `active_uuid`）
3. 服主 `allow_client_mods=true` + 玩家自己开启；单源约束：开了你的 mod，官方 MCDR 源自动 disabled

### 8.3 换归因算法

默认单项目启发式（§5）。换精确区域扫描：上报源带 `sheet_id`（自带归因）+ 投影区域 box 裁剪（判断方块是否在项目投影内）。归因是 service 层策略，可后续抽 `AttributionStrategy` Protocol（YAGNI：先跑通启发式再抽象）。

---

## 9. S-1 待核实清单（实现前必查）

> 根 [`CLAUDE.md`](../../../CLAUDE.md) S-1：MCDR API 实现前必须联网核实。以下为本文档假设，**实现第一个动作就是逐条核实** <https://docs.mcdreforged.com/zh-cn/latest/>。

| # | 待核实 | 影响 |
|---|---|---|
| S1-1 | 方块**放置**事件名与回调签名（`on_block_place`? / `on_player_block_placement`?）| §5 默认实现可行性 |
| S1-2 | 方块**破坏**事件名与回调签名 | §5 同上 |
| S1-3 | 回调 ctx 字段（玩家 / 坐标 / 方块 registry id / BlockState）| 上报 payload 字段 |
| S1-4 | ServerInterface 拦截/查询方块能力（是否需查区块 NBT）| 归因算法 |
| S1-5 | 事件回调线程模型（是否跑主线程 → R-12 @new_thread 必要性）| §5 R-12 合规 |
| S1-6 | 离线模式 UUID 推导在事件 ctx 里的一致性（与 sheets UUID 推导对齐）| 单源校验 key |

> 若 S-1 核实发现 MCDR 无合适事件钩子，本层默认实现需改方案（如轮询 / 区域快照 diff），届时本文档 §5 重写。

---

## 10. 红线速查

| 红线 | 在施工层的体现 |
|---|---|
| **R-7** MCDR 纯客户端 | 默认实现只观测 + 上报，不做积分计算 / 不持久化业务数据 |
| **R-12** @new_thread | 事件回调若阻塞必放新线程；HTTP 上报含超时+重试+失败回执 |
| **R-5** account 归属 | placement_records 按 account_id 聚合（player_uuid → account 解析）|
| **R-1** 业务库权威 | 所有 placement 落 PostgreSQL；MCDR 内存 batch 是临时缓冲 |
| **R-11** 密钥 | service-token 约定路径读，不发玩家；玩家 mod 走 JWT |
| **每玩家单源** | 同时只接受一个活跃上报源（防多源重复计数）|

---

## 11. 待确认

- 方块过滤规则（空气/水/装饰方块是否计施工贡献？）
- 防刷阈值（单玩家单位时间净放置上限，超过仅告警不拒）
- 多项目并发的归因 fallback（投影 box 裁剪是否首版必须？）
- `report_interval_seconds` 默认值（实时性 vs 后端负载）

---

*创建：2026-07-25（v0.9 文档重构）。本层 🚧 规划中，本文为设计契约基线；§5 默认实现依赖 S-1 核实，§9 是核实清单。数据流向 [积分层](./scoring-settlement.md)，constructing 状态来自 [sheets API](../api/sheets.md) §5.2。*
