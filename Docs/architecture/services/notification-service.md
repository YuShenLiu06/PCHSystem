# 服务文档：notification-service（统一通知抽象层）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **数据模型**：[`../data-model.md`](../data-model.md)（`notifications` schema）
> **设计依据**：[`../../superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md`](../../superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md)
> **消费方**：MCDR 插件（轮询投递）；sheets / scoring / title / alert / 未来业务模块（发通知）

## 1. 概述

notification-service 是后端**模块化单体**（红线 R-10）的新增 schema，职责单一：

> **业务事件 → 给特定玩家记一条通知 → 等待投递（落库 + 拉取）。**

它是后端**第一套统一通知基础设施**。在此之前各业务模块没有「给玩家发消息」的能力（sheets 协作的成功回执只能靠 HTTP 响应同步返回；alert-service 的游戏内告警只有 `InGameNotifier.mcdr_broadcast` 伪代码占位，未落地）。本服务把这一能力抽成**可复用的调用契约 + 端点契约**，所有业务模块按同一约定发通知，MCDR 按同一约定轮询投递。

| 管 | 不管 |
|---|---|
| 给**特定玩家**记通知（落库 + 拉取/ack/read） | 全服广播（用 MCDR `server.say`/`broadcast` 直发） |
| Notifier Protocol（落库 + 预留 webhook/discord 扩展点） | 业务事件判定（交各业务模块） |
| `category` 注册表（约定命名，不校验枚举） | 实时推送（首期轮询，非 SSE/websocket） |
| `payload` 结构化字段透传 | 客户端渲染（MCDR/Web 自行解析） |

**定位**：风控/协作等业务事件的「投递候选池」。落库即候选，由 MCDR 后台轮询拉取并 `server.tell` 投递；离线玩家通知堆积在库，上线时补推。

---

## 2. 调用契约（业务模块发通知的唯一入口）

### 2.1 签名

```python
notification_service.notify(
    session,            # 必须传调用方当前事务的 session（与业务写同原子，回滚则通知不落库）
    recipient_uuid,     # UUID — 接收玩家（FK→users.players.uuid）
    category,           # str — 见 §3 枚举注册表
    title,              # str — 标题（≤128 字符）
    body,               # str — 正文（≤512 字符）
    payload,            # dict | None — 结构化字段，供客户端渲染（jsonb）
) -> Notification       # 返回落库的 Notification 记录
```

### 2.2 关键不变量（CRITICAL）

- **必须传调用方事务的 session**：调用方在写端点（如 `claim`）的同一事务内调 `notify`，保证「改库 + 记通知」原子。事务回滚则通知不落库 —— 不会出现「业务没成功却发了通知」。
- **调用方不可自开新 session 记通知**：违反上述原子性。
- **接收者必须是已知 Player**：`recipient_uuid` FK→`users.players.uuid`，`ON DELETE CASCADE`（玩家硬删则通知随之消失，与 RS-5 一致）。
- **通知中心不校验 `category` 枚举值**：仅作为字符串落库 + 客户端展示分类用；新增枚举无需改 notification-service（见 §3 扩展规约）。

### 2.3 sheets 参考实现（伪代码）

```python
# Backend/app/api/sheets.py —— claim 端点挂钩 notify
async def claim(sheet_id, row_id, player, session):
    sheet = await sheet_repo.get_sheet(session, sheet_id)
    row = await sheet_repo.claim_row(session, sheet_id, row_id, player.uuid)
    if row.owner_uuid != player.uuid:                              # 拥有者自己认领不通知
        await notification_service.notify(
            session, row.owner_uuid, "sheet_claimed",
            title=f"{player.current_name} 认领了 [{row.item_name}]",
            body=f"表「{sheet.title}」中 {player.current_name} 已认领 [{row.item_name}]",
            payload={
                "sheet_id": sheet_id, "sheet_title": sheet.title,
                "row_id": row_id, "item_name": row.item_name,
                "actor_uuid": str(player.uuid), "actor_name": player.current_name,
            },
        )
    await session.commit()                                         # 业务写 + 通知同事务提交
    return row
```

> 详见 [`Docs/superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md`](../../superpowers/specs/2026-07-02-sheets-mcdr-bridge-design.md) §5.1 触发规则表。

---

## 3. `category` 枚举注册表

### 3.1 首期（sheets 专用，7 类）

| category | 触发端点 | 接收者 | 文案要点 |
|---|---|---|---|
| `sheet_claimed` | `POST /sheets/{sid}/rows/{rid}/claim` | **拥有者** | 「{actor} 认领了 [{item}]」 |
| `sheet_delivered` | `PATCH .../delivery`（未满） | **拥有者** | 「{actor} 上报交付 {delivered}/{need} [{item}]」 |
| `sheet_done` | `PATCH .../delivery`（≥need→done） | **拥有者** | 「{actor} 已备齐 [{item}]」 |
| `sheet_released` | `POST .../release`（认领人自放） | **拥有者** | 「{actor} 取消了对 [{item}] 的认领」 |
| `sheet_released` | `POST .../release`（owner 解锁） | **认领人** | 「拥有者解除了你对 [{item}] 的锁定」 |
| `sheet_rejected` | `POST .../reject`（owner 打回 / 认领人自取消） | **认领人** | 「[{item}] 已打回，delivered 归零，可重做」 |
| `sheet_qty_changed` | `PUT .../rows` 改 need_qty 且行已认领 | **认领人** | 「[{item}] 所需数量变为 {new}（原 {old}），delivered 已按需封顶」 |
| `sheet_row_deleted` | `DELETE .../rows/{rid}` / `DELETE /sheets/{sid}`（owner） | **该行认领人** | 「[{item}] 已被拥有者删除，认领取消」 |

### 3.2 扩展规约

新业务模块按 **`<domain>_<event>`** 命名新增：

| 模块 | 预留 category（未来） |
|---|---|
| scoring | `score_spike`（积分突变）、`score_settled`（结算完成） |
| title | `title_unlocked`（称号解锁）、`title_announced`（高阶全服公告） |
| alert | `alert_raised`（风控告警） |

**通知中心不校验枚举值**：新增 category 仅需在调用处约定，无需改 notification-service 代码或迁移。客户端（MCDR/Web）按需解析 `payload` 渲染。

---

## 4. 数据模型（迁移 `0006_notifications`）

### 4.1 `notifications.notifications` 表

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint | 主键（identity） |
| `recipient_uuid` | uuid | FK→`users.players.uuid`，`ON DELETE CASCADE` |
| `category` | text | 见 §3（不校验枚举） |
| `title` | text | 标题 |
| `body` | text | 正文 |
| `payload` | jsonb | 结构化字段，供客户端渲染 |
| `created_at` | timestamptz | `now()` |
| `delivered_at` | timestamptz? | null = 未投递（MCDR ack 后置 `now()`） |
| `read_at` | timestamptz? | null = 未读（给未来 Web 通知中心） |

### 4.2 索引

| 索引 | 字段 | 用途 |
|---|---|---|
| `ix_notifications_recipient_delivered` | `(recipient_uuid, delivered_at)` | MCDR `GET /notifications/pending` 按 recipient 拉取未投递 |

### 4.3 分层（遵循现有 repo/service/api 惯例）

| 层 | 文件 | 职责 |
|---|---|---|
| model | `app/models/notification.py` | SQLAlchemy `Notification` 模型 |
| repository | `app/repositories/notification_repo.py` | `create()` / `fetch_pending(uuid, limit)` / `mark_delivered(ids)` / `mark_read(id)` |
| service | `app/services/notification_service.py` | `notify(session, …)`（同事务语义）+ `Notifier` Protocol + `DbNotifier` |
| api | `app/api/notifications.py` | 3 端点（pending/ack/read），挂 `main.py` |

---

## 5. Notifier Protocol 与扩展点

### 5.1 Protocol

```python
from typing import Protocol
from app.models.notification import Notification

class Notifier(Protocol):
    def notify(self, record: Notification) -> None:
        """对已落库的 Notification 执行投递动作。"""
        ...
```

### 5.2 首期实现

| Notifier | 触发 | 投递通道 |
|---|---|---|
| **`DbNotifier`**（首期唯一） | `notify()` 内同事务落库 | 落库即「投递候选」，等 MCDR 轮询拉取 |

### 5.3 预留扩展（首期不实现）

| Notifier | 触发 | 配置占位 |
|---|---|---|
| `WebhookNotifier` | 配置了 external webhook URL 时 | `webhook_url` + `webhook_secret`（env，未启用） |
| `DiscordNotifier` | 配置了 Discord bot webhook 时 | `discord_webhook_url`（env，未启用） |

> 加新 Notifier 只需实现接口 + 在 service 注册，不改调用方。

---

## 6. 端点契约（均 service-token 鉴权）

> 这三个端点是 **MCDR 通知轮询器**的契约边界。鉴权方式与 sheets `/export` 一致：`X-Service-Token` 头。

### 6.1 `GET /notifications/pending`

| 项 | 值 |
|---|---|
| 鉴权 | `X-Service-Token` |
| query | `player_uuid=<uuid>`（必填）、`limit=<n>`（可选，默认 20，上限 50） |
| 响应 | `200 [{id, recipient_uuid, category, title, body, payload, created_at}, ...]`（仅 `delivered_at IS NULL`，按 `created_at` 升序） |
| 错误 | 401（缺/错 service token）、422（player_uuid 缺/格式错） |

### 6.2 `POST /notifications/ack`

| 项 | 值 |
|---|---|
| 鉴权 | `X-Service-Token` |
| body | `{"ids": [1, 2, 3]}` |
| 响应 | `200 {"acked": 3}`（对存在的 id 置 `delivered_at = now()`，幂等：重复 ack 无副作用） |
| 错误 | 401、422（ids 缺/非数组） |

### 6.3 `POST /notifications/{id}/read`

| 项 | 值 |
|---|---|
| 鉴权 | `X-Service-Token` |
| path | `id`（通知主键） |
| 响应 | `200 {"read_at": "<iso8601>"}`（置 `read_at = now()`，幂等） |
| 错误 | 401、404（id 不存在） |

> `read_at` 端点首期为未来 Web 通知中心预留；MCDR 首期只用 `pending` + `ack`。

---

## 7. service-token 安全运维要点（CRITICAL）

> service-token 代玩家写通道（[`sheets.md`](../api/sheets.md) §2）让 MCDR 能以任意玩家身份写 sheets + 读/ack 全员通知。这带来一个根本性结论：**`MCDR_SERVICE_TOKEN` 等同于「全员 root 凭据」**。任何持有该 token 的一方都能冒充任意已存在 `Player` 执行写动作（认领/交付/打回/upsert/删表等），也能拉取/ack 任意玩家的通知。本节是必须遵守的运维红线。

### 7.1 威胁模型

| 攻击面 | 后果 |
|---|---|
| token 泄露到公网/仓库/日志 | 攻击者可冒充**任意已存在 player** 写 sheets + 读取/ack 全员通知（越权读写） |
| backend 监听公网 | 任何能访问该端口的一方都可用 token 代玩家写 |
| token 短/可猜 | 离线爆破可行性上升 |
| 无轮换机制 | 泄露后无法快速止损 |
| 无审计 | 越权写发生后无法追溯 |

### 7.2 必做缓解（部署 + 运维侧）

| # | 缓解 | 说明 |
|---|---|---|
| 1 | **网络隔离** | backend 仅监听内网 / Docker network；MCDR 与 backend 跨独立网络命名空间，**禁止 backend 端口对公网暴露**（compose 中 `ports` 不绑定 `0.0.0.0`，必要时仅 `127.0.0.1`）。token 不该出现在任何可被外部触达的请求路径上 |
| 2 | **经 `.env` / docker secrets 注入** | `MCDR_SERVICE_TOKEN` 走 `.env`（gitignored）或 docker secrets；**不入代码库**、不进镜像 layer、不写日志（红线 R-11） |
| 3 | **token 强度** | ≥32 字节密码学随机（如 `secrets.token_urlsafe(32)`）；backend 启动时校验非空 + 最小长度，不达标拒绝启动 |
| 4 | **定期轮换** | 建立轮换流程（生成新 token → 同步更新 backend `.env` 与 MCDR `config.json` → 滚动重启两端 → 作废旧 token）；建议每季度或任何疑似泄露后立即轮换 |
| 5 | **审计日志（已落地）** | backend 对每次 service-token 代玩家写记 `service_token_proxy` 审计日志：`{timestamp, player_uuid, http_path, client_ip}` —— **记录被代理的玩家与端点，不含 token 本身**。运维侧应集中收集并定期审计异常模式（如同一 token 短时代理大量不同 player、对删表类高危端点的代理写等） |
| 6 | **端点归属校验（已落地/进行中）** | `ack` / `read` 端点校验请求 `player_uuid` 与通知 `recipient_uuid` 归属一致，防越权 ack/read 他人通知（详见 [`sheets.md`](../api/sheets.md) §12） |

### 7.3 红线引用

- **R-11**（根 CLAUDE.md）：`POSTGRES_*`、`WIKI_API_KEY`、`MCDR_SERVICE_TOKEN`、`JWT_SECRET` 经 `.env` / docker secrets 注入，**密钥不进代码库**。
- **R-1**：MCDR 不直连 DB，全走 HTTP —— token 是 MCDR 访问后端的唯一长期凭据，故其保护级别 = 数据库访问级别。

> 任何放宽上述缓解（如临时把 backend 暴露公网调试）都等同于公开全员 root，必须在调试结束后立即收回 + 轮换 token。

---

## 8. MCDR 投递契约（消费方）

> 完整实现见 [`mcdr-plugin.md`](./mcdr-plugin.md)「通知轮询」章节。

| 阶段 | 动作 |
|---|---|
| **在线集合维护** | `on_player_joined` 加入集合 / `on_player_left` 移出；插件加载时 `server.rcon_query('list')` 初始化兜底 |
| **后台轮询** | `@new_thread('htcmc_sheet_notifier')` 循环，每 `notify_poll_interval_seconds`（默认 2.0）对每个在线玩家调 `GET /notifications/pending?player_uuid=<uuid>&limit=notify_max_per_poll`（默认 20） |
| **逐条投递** | `server.tell(player, format_notification(n))` |
| **ack** | 投递成功后 `POST /notifications/ack {player_uuid, ids}` |
| **上线补推** | `on_player_joined` 立即为该玩家拉一次 pending（离线期间堆积的补推） |
| **主动查看** | `!!PCH sheet notify list` 拉取并分页回显 |
| **离线处理** | 通知仅落库后端；MCDR 不持久化，重启后靠上线拉取恢复 |

---

## 9. 与 alert-service 的关系

alert-service 文档（[`alert-service.md`](./alert-service.md) §3.1）曾有 `InGameNotifier.mcdr_broadcast` 伪代码占位 —— 风控告警经 HTTP 推 MCDR 广播。**notification-service 是其落地与泛化**：

- **统一通道**：未来 alert 异常（`score_spike` / `behavior_abuse` / `material_anomaly` 等）也走 `notify()`（`category="alert_raised"`），落库后由 MCDR 轮询投递给相关玩家/管理员。
- **首期不迁移**：alert-service 仍保留 `LogNotifier`（后台日志）+ `InGameNotifier` 占位；待 notification-service 稳定后再统一切换。
- **Notifier Protocol 同源**：alert-service 的 `Notifier` Protocol 与本服务的 `Notifier` Protocol 形状一致（`notify(record)`），未来合并时形状兼容。

---

## 10. 红线遵循（根 CLAUDE.md §3）

| 红线 | 落地 |
|---|---|
| **R-1** | 后端独占 DB；MCDR 经 HTTP 拉取，不直连。 |
| **R-5** | `recipient_uuid` FK→`users.players.uuid`；当前身份锚 = MC UUID，与未来 Web 账号主锚平滑迁移。 |
| **R-10** | notification 是模块化单体的新 schema（`notifications`），单库事务保证「改库+记通知」原子。 |
| **R-11** | 无新密钥；`/notifications/*` 端点复用 `MCDR_SERVICE_TOKEN`；运维红线见 §7。 |
| **RS-5** | 玩家硬删（`ON DELETE CASCADE`）则通知随之消失，无审计残留。 |

---

## 11. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| **service-token 泄露** | 等同全员 root（冒充任意 player 写 + 越权读/ack 通知） | §7 全套缓解（网络隔离 + secrets 注入 + ≥32 字节 + 轮换 + 审计日志 + 归属校验） |
| 轮询延迟 | 默认 2s 投递周期 | 可调 `notify_poll_interval_seconds`；紧急通知可叠加 `WebhookNotifier` |
| 离线堆积 | 离线玩家通知全落库 | 上线补推 + 客户端分页；超长可考虑 TTL 清理（首期不做） |
| 并发 ack | 重复 ack 幂等 | `mark_delivered` 只在 `delivered_at IS NULL` 时置位 |
| payload 体积 | jsonb 无上限约束 | 调用方自律（结构化字段，非全量业务对象）+ payload 8KB 上限（后端加固） |
| 枚举漂移 | `category` 不校验，调用方拼错无报错 | 文档维护注册表（§3）+ 客户端按需渲染 |

> 待确认：未来 Web 通知中心 UI 形态；WebhookNotifier/DiscordNotifier 接入时机；token 轮换周期与流程文档化（§7.2 第 4 项）。

---

*最后更新：2026-07-02（service-token 安全运维要点 §7 + 加固项：审计日志 / ack-read 归属校验 / title-body 限长清洗 / payload 8KB / pending limit 上限 50）*
