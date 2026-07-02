# sheets ↔ MCDR 对接 + 统一通知抽象层 · 设计文档

> **日期**：2026-07-02
> **状态**：设计稿（权威实现依据，附 plan [`wobbly-exploring-hearth`](../../../.claude/plans/wobbly-exploring-hearth.md)）
> **来源**：v0.2.0 sheets 协作改进已落地（[`2026-07-02-sheets-collaboration-design`](./2026-07-02-sheets-collaboration-design.md)）；本次补齐 MC 游戏内对等 + 后端首套通知基础设施。
> **前置**：sheets 协作（认领/交付/解除/打回）已稳定，Web 端可用；MCDR 端仅 `!!PCH login` 落地，sheets 命令为空。

---

## 1. 背景（Context）

v0.2.0 后，sheets 的认领/进度协作与名称显示在 **Web 端 + 后端** 已完成（commit `80ec623` / `b66916f`）。但：

1. **游戏内端不可用**：玩家进了游戏（MCDR）就只能 `!!PCH login`，sheets 全部能力（建表/认领/上报交付/标备齐/解除锁定/打回/upsert/删行/删表/改标题）在游戏内用不上，且身份与 Web 端脱节。
2. **零通知基础设施**：认领者被打回、所需数量变化、状态变化，拥有者收到用户提交/取消提交 —— 这些消息提醒目前**后端完全没有钩子**，玩家只能靠手动刷新。

本次目标是**端到端可用**：后端开 MCDR 写通道 + 通知落库/拉取端点，MCDR 实现命令树 + 后台轮询投递，架构文档同步。身份对等：**同一玩家 = 同一后端 `Player`**（当前身份锚 = MC UUID，与未来 Web 账号主锚平滑迁移）。

---

## 2. 目标与不在范围

| 在范围内 | 不在范围内 |
|---|---|
| 后端 service-token 代玩家写通道（与 JWT 等价，复用 RBAC） | 多人认领（v0.2.0 已为单认领人留接口） |
| 后端统一通知抽象层（新 schema `notifications` + service + 端点） | alert-service 落地（仅留接口对齐，首期不迁移） |
| MCDR `!!PCH sheet` 全套命令树 + 通知轮询投递 + 离线补推 | 实时推送（SSE/websocket/webhook MCDR 端口） |
| 后端写端点同事务挂钩 `notify`（改库 + 记通知原子） | 跨账号通知（邮箱/第三方 IM），首期仅游戏内 |

---

## 3. 已定决策（与用户拍板）

| # | 决策 | 选择 |
|---|---|---|
| D1 | 交付范围 | **完整实现**：后端写通道 + 通知端点 + 迁移/测试 + MCDR 命令树 + 轮询投递 + 文档全套。通知抽象层无现成插件可复用 → 自研。 |
| D2 | MCDR 鉴权 | **service-token + `X-Player-UUID` 代理**：后端校验 service token 后加载该 UUID 的 Player 注入，复用现有 RBAC。MCDR 无需管 JWT。 |
| D3 | 通知投递 | **MCDR 轮询** `GET /notifications/pending`（service-token 鉴权），按 `recipient_uuid` 用 `server.tell` 投递；离线玩家通知落库，上线时补推。 |
| D4 | 交付语义 | `!!PCH sheet deliver` 用**绝对值**（与后端/前端契约一致，避免额外 GET + 并发，KISS）；progress 模式玩家先 `view` 看当前 delivered 再决定。 |
| D5 | 命令前缀 | 收敛到现有 `!!PCH` 前缀（与 `__init__.py` 一致）。 |

---

## 4. 鉴权双通道（service-token + X-Player-UUID 代玩家写）

### 4.1 后端 `get_current_player` 改双通道（端点签名与业务层零改动）

```
凭证解析优先级：
  1. Authorization: Bearer <jwt>            → Web，沿用 core/jwt.py 解码（type=access）
  2. X-Service-Token + X-Player-UUID        → MCDR / 外部系统
       └─ secrets.compare_digest(token)      （复用 api/deps.py 现有 require_service_token 比较逻辑）
       └─ 用 UUID 查 Player（复用 repositories/player_repo.py:get_by_uuid）
       └─ 注入同型 Player，下游 RBAC 零感知
```

- **RBAC 不变**：`sheets.py` 的 `_can_edit(sheet, player)` 与各处 `claimant_uuid == player.uuid` 都基于 `Player`，与凭证来源无关 → **业务层零改动，只动 deps.py**。
- **`/sheets/export` 仍独占 service-token-only**（不带身份，全量 CSV 只读），保留 `require_service_token` 给 export。
- **关键文件**：`Backend/app/api/deps.py`（新增双通道 `get_current_player`，保留 `require_service_token` 给 export）；`Backend/app/core/config.py` 无需新配置，复用现有 `mcdr_service_token`。

### 4.2 红线遵循

- **R-1**：MCDR 不直连 DB，全走 HTTP。✅
- **R-5**：当前身份锚 = MC UUID，service-token+UUID 代理天然一致；未来升级 Web 账号主锚时，API 形状不变（repo FK / JWT claims 平滑迁移）。
- **R-9**：MC 端命令仅文案提示角色，真实 RBAC 以后端 403/409 为准。
- **R-11**：`MCDR_SERVICE_TOKEN` 复用现有 `.env` 注入，不入库；`config.json.example` 占位。

---

## 5. 通知抽象层契约（可复用 — 其他业务模块的发通知入口）

> 完整契约见 [`Docs/architecture/services/notification-service.md`](../../architecture/services/notification-service.md)。本节是 sheets 实现侧的要点摘录。

### 5.1 调用契约（调用方在**写端点的同一事务 session** 内调用）

```
notification_service.notify(
    session,            # 必须传调用方当前事务的 session（与业务写同原子，回滚则通知不落库）
    recipient_uuid,     # UUID — 接收玩家
    category,           # str — 见 §5.2 枚举
    title,              # str
    body,               # str
    payload,            # dict — 结构化字段，供客户端渲染
) -> Notification
```

**不变量**：与业务写在同一事务 → 事务回滚则通知不落库（一致性）。调用方**不可**自开新 session 记通知。

sheets 参考调用（伪代码）：

```python
async def claim(sheet_id, row_id, player, session):
    row = await sheet_repo.claim_row(session, sheet_id, row_id, player.uuid)
    if row.owner_uuid != player.uuid:                              # 拥有者自己认领不通知
        await notification_service.notify(
            session, row.owner_uuid, "sheet_claimed",
            title=f"{player.current_name} 认领了 [{row.item_name}]",
            body=f"表「{sheet.title}」中 {player.current_name} 已认领 [{row.item_name}]",
            payload={"sheet_id": sheet_id, "sheet_title": sheet.title,
                     "row_id": row_id, "item_name": row.item_name,
                     "actor_uuid": str(player.uuid), "actor_name": player.current_name},
        )
    await session.commit()
    return row
```

### 5.2 `category` 枚举注册表（首期 7 类，sheets 专用）

| category | 触发端点 | 接收者 | 文案要点 |
|---|---|---|---|
| `sheet_claimed` | `POST .../claim` | **拥有者** | 「{actor} 认领了 [{item}]」 |
| `sheet_delivered` | `PATCH .../delivery`（未满） | **拥有者** | 「{actor} 上报交付 {delivered}/{need} [{item}]」 |
| `sheet_done` | `PATCH .../delivery`（≥need→done） | **拥有者** | 「{actor} 已备齐 [{item}]」 |
| `sheet_released` | `POST .../release`（认领人自放） | **拥有者** | 「{actor} 取消了对 [{item}] 的认领」 |
| `sheet_released` | `POST .../release`（owner 解锁） | **认领人** | 「拥有者解除了你对 [{item}] 的锁定」 |
| `sheet_rejected` | `POST .../reject`（owner 打回 / 认领人自取消） | **认领人** | 「[{item}] 已打回，delivered 归零，可重做」 |
| `sheet_qty_changed` | `PUT .../rows` 改 need_qty 且行已认领 | **认领人** | 「[{item}] 所需数量变为 {new}（原 {old}），delivered 已按需封顶」 |
| `sheet_row_deleted` | `DELETE .../rows/{row_id}` / `DELETE /sheets/{id}`（owner） | **该行认领人** | 「[{item}] 已被拥有者删除，认领取消」 |

**扩展规约**：新业务模块按 `<domain>_<event>` 命名新增（如 `score_spike`/`title_unlocked`/`alert_raised`），通知中心**不校验枚举值**（仅作为字符串落库 + 客户端展示分类用），新增枚举无需改 notification-service。

### 5.3 数据模型（迁移 `0006_notifications`）

`notifications.notifications` 表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键 |
| `recipient_uuid` | uuid | FK→`users.players.uuid`，`ON DELETE CASCADE` |
| `category` | text | 见 §5.2 |
| `title` | text | 标题 |
| `body` | text | 正文 |
| `payload` | jsonb | 结构化字段（sheet_id/sheet_title/row_id/item_name/actor_uuid/actor_name/old/new 等） |
| `created_at` | timestamptz | `now()` |
| `delivered_at` | timestamptz? | null = 未投递 |
| `read_at` | timestamptz? | null = 未读（给未来 Web 通知中心） |

**索引**：`ix_notifications_recipient_delivered` on `(recipient_uuid, delivered_at)`。

### 5.4 Notifier Protocol 与扩展点

```python
class Notifier(Protocol):
    def notify(self, record: Notification) -> None: ...

class DbNotifier:           # 首期唯一实现：落库即「投递候选」，等 MCDR 轮询拉取
    ...

# 预留（首期不实现）：
class WebhookNotifier:      # 触发：配置 external webhook URL；配置占位 webhook_url + secret
    ...
class DiscordNotifier:      # 触发：配置 bot webhook；配置占位 discord_webhook_url
    ...
```

> 与 alert-service 的关系：alert-service 文档曾有 `InGameNotifier.mcdr_broadcast` 伪代码占位（`Docs/architecture/services/alert-service.md` §3.1）。notification-service 是其**落地与泛化** —— 未来 alert 异常也走 `notify()`（`category="alert_raised"` 等），`InGameNotifier` 占位将让位于统一的「落库 → MCDR 轮询拉取 → `server.tell` 投递」链路。

### 5.5 端点契约（均 service-token 鉴权）

| 方法 | 路径 | 鉴权 | 请求 | 响应 |
|---|---|---|---|---|
| GET | `/notifications/pending?player_uuid=<uuid>&limit=<n>` | service-token | query | `[{id, category, title, body, payload, created_at}, ...]`（`delivered_at IS NULL`） |
| POST | `/notifications/ack` | service-token | `{ids: [int]}` | `{acked: int}`（置 `delivered_at = now()`） |
| POST | `/notifications/{id}/read` | service-token | — | `{read_at}`（置 `read_at = now()`，给未来 Web） |

详见 [`notification-service.md`](../../architecture/services/notification-service.md) §端点契约。

---

## 6. MCDR `!!PCH sheet` 命令映射（命令树 + HTTP 端点 + 角色）

> 完整命令↔端点↔角色映射表见 [`Docs/architecture/api/sheets.md`](../../architecture/api/sheets.md) §11。本节为命令树总览。

```
!!PCH sheet list [--mine]                                   # 任意玩家 → GET /sheets[?owner=me]
!!PCH sheet view <sheet_id>                                 # 任意玩家 → GET /sheets/{sheet_id}
!!PCH sheet create <title...>                               # 任意玩家 → POST /sheets
!!PCH sheet rename <sheet_id> <title...>                    # owner      → PATCH /sheets/{sheet_id}
!!PCH sheet delete <sheet_id>                               # owner      → DELETE /sheets/{sheet_id}
!!PCH sheet add <sheet_id> <item> <need> [lock|progress] [sort]   # owner → PUT /sheets/{sheet_id}/rows（upsert）
!!PCH sheet set <sheet_id> <item> <need> [lock|progress] [sort]   # owner → PUT /sheets/{sheet_id}/rows（同 upsert 端点）
!!PCH sheet delrow <sheet_id> <row_id>                      # owner      → DELETE /sheets/{sheet_id}/rows/{row_id}
!!PCH sheet claim <sheet_id> <row_id>                       # 任意玩家   → POST .../claim
!!PCH sheet deliver <sheet_id> <row_id> <qty>               # 认领人     → PATCH .../delivery（绝对值）
!!PCH sheet done <sheet_id> <row_id>                        # 认领人     → PATCH .../delivery = need（lock 快捷）
!!PCH sheet release <sheet_id> <row_id>                     # 认领人自放 / owner 解锁 → POST .../release
!!PCH sheet reject <sheet_id> <row_id>                      # 认领人(done态自取消) / owner 打回 → POST .../reject
!!PCH sheet notify list                                     # 自己 → GET /notifications/pending
```

**身份**：每个 sheet 命令回调内 `player_uuid = uuid_api_remake.get_uuid(player_name)`（RS-8）→ 作为 `X-Player-UUID` + `X-Service-Token` 头调后端。

**回执**：错误码 403/404/409 → 友好中文文本（`server.tell`）；哨兵（`__RATE_LIMITED__`/`__REMOVED__`/`None`）必须回执玩家（RS-11）。

---

## 7. 轮询投递与离线补推

### 7.1 MCDR 通知轮询器（`notifier.py`，遵循 RS-6 全程 `@new_thread`）

- `on_load` 启动 `@new_thread('htcmc_sheet_notifier')` 循环；`on_unload` 设停止位退出。
- **在线玩家集合**维护：
  - `on_player_joined(server, player, info)` → 加入集合
  - `on_player_left(server, player)` → 移出集合
  - 插件加载时若服务端已启动，用 `server.rcon_query('list')` 解析初始化（兜底）
- **轮询循环**（每 `notify_poll_interval_seconds` 秒，默认 15.0）：
  1. 对每个在线玩家调 `GET /notifications/pending?player_uuid=<uuid>&limit=notify_max_per_poll`
  2. 逐条 `server.tell(player, format_notification(n))`
  3. 成功后 `POST /notifications/ack {ids}`
- **`on_player_joined` 补推**：玩家上线立即为该玩家拉一次 pending 并投递（离线期间堆积的补推）。
- **`!!PCH sheet notify list`**：主动拉取并分页回显（玩家想看历史/未读）。
- **离线处理**：通知仅落库后端；MCDR 不持久化，重启后靠上线拉取恢复。

### 7.2 配置（`config.py` 加字段）

```python
notify_poll_interval_seconds: float = 15.0
notify_max_per_poll: int = 20
```

---

## 8. 红线遵循（plan「红线遵循」节落地清单）

| 红线 | 落地 |
|---|---|
| **R-1** | MCDR 不直连 DB，全走 HTTP。 |
| **R-5** | 当前身份锚 = MC UUID，service-token+UUID 代理天然一致；未来升级 Web 账号主锚 API 形状不变。 |
| **R-9** | MC 端命令仅文案提示角色，真实 RBAC 以后端 403/409 为准。 |
| **R-10** | notification 模块是模块化单体的新 schema（`notifications`），单库事务保证「改库+记通知」原子。 |
| **R-11** | `MCDR_SERVICE_TOKEN` 复用现有 `.env` 注入，不入库；提交 `config.json.example`。 |
| **R-12 / RS-6** | 所有 HTTP/轮询放 `@new_thread`，含超时(≤10s)+重试+失败回执；**禁用 `schedule_task` 卸载阻塞**（task executor = 主线程）。 |
| **RS-8** | UUID 推导只用 `uuid_api_remake.get_uuid(name)`。 |
| **RS-11** | 哨兵（`__RATE_LIMITED__`/`__REMOVED__`/`None`）必须回执玩家。 |
| **S-1** | MCDR API 已联网核实，依据见 §9。 |

---

## 9. MCDR API 依据（S-1 联网核实）

| API | 用途 | 依据 |
|---|---|---|
| 命令注册（`Literal`/`Text`/`Integer`/`QuotableText`） | `!!PCH sheet …` 命令树 | [`__init__.py:18-52`](../../../McdrPlugin/htcmc_auth/htcmc_auth/__init__.py) 已落地 + https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/command.html |
| `@new_thread('name')` | 卸载阻塞 HTTP / 轮询到 daemon 线程 | `commands.py:63` 已落地 + https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html |
| `server.tell(player, text)` / `server.say` / `server.broadcast` / `server.reply` | 玩家/全服回执 | https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html |
| `server.rcon_query('list') → str \| None` | 在线玩家集合初始化兜底 | 同上 |
| `on_player_joined(server, player, info)` / `on_player_left(server, player)` / `server.register_event_listener(event_id, callback)` | 在线集合维护 + 上线补推 | https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/event.html |
| `server.get_plugin_instance('uuid_api_remake')` | 取得 UUID 推导器（RS-8） | https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html |
| ⚠️ `schedule_task` 跑 TaskExecutor（主）线程 | **禁用于阻塞**（RS-6） | 同 ServerInterface 页 `schedule_task` 条目 |
| ⚠️ `get_online_players` 不在通用 ServerInterface API | 改用 on_player_joined/left 自维护集合 + rcon list 初始化 | 同上 |

---

## 10. 验证（实现后）

**后端**（`cd Backend`）：
- `pytest tests/test_notifications.py tests/test_sheets_service_write.py -v` 全绿（落库/拉取/ack/read + 同事务原子 + service-token+UUID 走 claim/delivery/release/reject/upsert RBAC + 状态机 403/409 + 正确 category/接收者/payload）。
- `uvicorn app.main:app` 起服，`curl` 带 `X-Service-Token`+`X-Player-UUID` 验 claim→owner 收 `sheet_claimed`、`GET /notifications/pending`、`POST /notifications/ack`。
- OpenAPI 重新导出校对（含 3 新通知端点）。

**MCDR 端到端**（需 MC + MCDR + 后端三件套起）：
- A `!!PCH sheet create 测试表` → A `!!PCH sheet add 1 铁锭 64 lock` → B `!!PCH sheet claim 1 <row>` → A 游戏内立即收到「B 认领了 [铁锭]」
- A `!!PCH sheet reject 1 <row>` → B 收到「[铁锭] 已打回」
- B 离线时 A 触发若干操作 → B 上线时补推
- 拔后端网线 → MCDR 回执「服务不可用」并保留命令可重试

**文档**：
- [`Docs/architecture/api/sheets.md`](../../architecture/api/sheets.md) §11 命令映射表与 MCDR `__init__.py` 命令树**逐条对照一致**。
- [`mcdr-plugin.md`](../../architecture/services/mcdr-plugin.md) §3.6 已无 `schedule_task` 卸载阻塞的过时描述。

---

## 11. 开放项 / 后续

- **alert-service 迁移**：notification-service 落地后，alert-service 的 `InGameNotifier.mcdr_broadcast` 占位将让位于统一 `notify()`（`category="alert_raised"` 等），首期不迁移。
- **WebhookNotifier / DiscordNotifier**：Notifier Protocol 已留扩展点，触发与配置占位，未实现；接入时机待定。
- **Web 通知中心**：`POST /notifications/{id}/read` 已为 Web 留 `read_at`，未来做前端通知中心时复用。
- **多人认领**：v0.2.0 已为单认领人留 API 列表语义接口，本任务不涉及。

---

*本设计为本次任务的权威记录；实现以 plan [`wobbly-exploring-hearth`](../../../.claude/plans/wobbly-exploring-hearth.md) 为准。*
