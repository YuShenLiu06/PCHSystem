# 服务文档：alert-service（风控告警）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5 / §8
> **数据模型**：[`../data-model.md`](../data-model.md) §7（`alerts` schema）
> **玩法依据**：[`../../guied.md`](../../guied.md) 风控兜底

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 异常检测（积分突变/行为异常） | 业务记账（交 scoring-service） |
| 告警记录与状态流转（`alerts`） | 玩家身份（交 user-service） |
| Notifier 抽象（首期游戏内+后台日志） | 称号/项目逻辑 |
| 白名单复核联动 | 自动封禁决策（人工 ack） |
| 告警去重/聚合/限频 | |

**定位**：风控的「感知侧」。检测可疑模式 → 记录告警 → 经 Notifier 通知。**首期仅游戏内 + 后台日志**两通道，Notifier 留扩展点接 QQ/Discord webhook。**不自动封禁**，由人工 ack 后联动 user-service 改白名单状态。

## 2. 对外接口（REST API）

| 方法 | 路径 | 调用方 | 说明 |
|---|---|---|---|
| POST | `/alerts` | scoring/project | 内部服务上报异常事件 |
| GET | `/alerts` | Web(admin) | 告警队列（按 status/severity 过滤） |
| PATCH | `/alerts/{id}` | Web(admin) | 状态流转 new→ack→resolved |
| POST | `/alerts/{id}/whitelist-review` | Web(admin) | 转白名单复核（联动 user-service） |

## 3. 内部实现要点

### 3.1 Notifier 抽象（核心）

用户明确：「抽象接口 + 首期实现一个，仅游戏内 + 后台」。定义统一接口，首期两个实现：

```python
from typing import Protocol

class Notifier(Protocol):
    def notify(self, severity: str, title: str, body: str, channel: str = "default") -> None: ...

class LogNotifier:                         # 实现 1：后台日志（始终启用）
    def notify(self, severity, title, body, channel="default"):
        log.log(LEVEL_MAP[severity], f"[{severity}] {title}: {body}")

class InGameNotifier:                      # 实现 2：游戏内（推 MCDR 广播）
    def notify(self, severity, title, body, channel="default"):
        if severity in ("high", "medium"):
            mcdr_broadcast(f"§c[{title}] {body}")   # 经 HTTP 推 MCDR

# 预留（首期不实现）
class WebhookNotifier:                     # QQ/Discord webhook，后续接入
    ...
```
- **通道注册表**：`NOTIFIERS = [LogNotifier(), InGameNotifier()]`，告警时遍历分发。
- 后续加 `WebhookNotifier` 只需实现接口 + 注册，不改调用方。

### 3.2 异常检测规则

| 规则 | 触发条件 | severity |
|---|---|---|
| `score_spike` | 单次 `!!submit` 积分超阈值 / 短时累计异常 | medium/high |
| `behavior_abuse` | 高频提交、跨项目异常模式 | medium |
| `material_anomaly` | 交付量远超需求（疑似刷分） | medium |
| `manual` | 管理员手动上报 | low+ |

```python
def evaluate(player_uuid, event):
    for rule in RULES:
        if rule.match(event):
            create_alert(rule.type, player_uuid, rule.severity, evidence=event.snapshot)
```
- 规则阈值配置化（环境变量/配置表），**不硬编码**。

### 3.3 告警去重与限频

```python
def create_alert(type_, player_uuid, severity, evidence):
    # 同类告警 5 分钟内聚合，避免风暴
    existing = db.query("""
        SELECT id FROM alerts.alerts
        WHERE type=:t AND player_uuid=:u AND status='new'
        AND created_at > now() - interval '5 min' LIMIT 1
    """, t=type_, u=player_uuid)
    if existing:
        db.execute("UPDATE alerts.alerts SET evidence = evidence || :ev WHERE id=:i", ...)
        return
    db.execute("INSERT INTO alerts.alerts(...) VALUES(...)", ...)
    dispatch_notifiers(severity, ...)
```

### 3.4 白名单复核联动

```python
# 长期零贡献 / 多次 high 告警 → 转复核
def maybe_flag_review(player_uuid):
    high_count = db.query("SELECT count(*) FROM alerts.alerts WHERE player_uuid=:u AND severity='high' AND status in ('new','ack')", ...)
    if high_count >= THRESHOLD:
        user_service.set_whitelist(player_uuid, "under_review")
```
- **不自动移除白名单**，仅置 `under_review` 等待人工裁决（呼应 guied.md「风控前置入服」的克制原则）。

## 4. 依赖的其他服务

- 被 **scoring-service**（积分异常）、**project-service**（材料异常）调用上报。
- 调用 **user-service** 改 `whitelist_state`。
- 调用 **MCDR 插件** 广播游戏内告警。

## 5. 所属数据表

`alerts` schema（见 [`data-model.md`](../data-model.md) §7）：
- `alerts`（异常事件，`type / player_uuid / project_id / severity / evidence / status`）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| 误报打扰 | 规则过严频繁告警 | 去重聚合 + 阈值可调 + severity 分级 |
| 告警风暴 | 异常事件爆发 | §3.3 限频 + 队列削峰 |
| 游戏内告警干扰 | 高频刷屏 | 仅 high/medium 入游戏内；low 仅日志 |
| 规则维护成本 | 阈值难定 | 配置化 + 上线后观察调参 |
| 自动化边界 | 误自动封禁 | **不自动移除白名单**，仅转复核由人工裁决 |

> 待确认：QQ/Discord webhook 接入时机（Notifier 已留扩展点）；各类规则阈值与「长期零贡献触发复核」的具体周期。
