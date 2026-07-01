# 服务文档：title-service（称号荣誉体系）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5 / §7.3
> **数据模型**：[`../data-model.md`](../data-model.md) §5（`titles` schema）
> **玩法依据**：[`../../guied.md`](../../guied.md) 指数称号梯度

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 称号定义与梯度（`titles`） | 积分计算（交 scoring-service） |
| 解锁判定（`S_升级 = S_基 × r^(tier-1)`） | scoreboard 命令执行（在 mcdr-plugin） |
| 当前展示称号切换（`player_titles`） | wiki 页面（交 wiki-service） |
| 高阶称号全服公告 | 玩家身份（交 user-service） |
| 高阶称号 wiki 编辑权益联动 | |
| 提供 `prefix_text` 供 MCDR 下发 | |

**定位**：荣誉激励的「展示侧」。把积分转化成可见的阶梯称号与聊天前缀。**scoreboard 前缀的实际命令执行在 MCDR**（见 [`mcdr-plugin.md`](./mcdr-plugin.md) §3.5），本服务负责判定、存配置、发通知。

## 2. 对外接口（REST API）

| 方法 | 路径 | 调用方 | 说明 |
|---|---|---|---|
| GET | `/titles` | Web/MCDR | 称号目录（梯度/所需积分/前缀文本） |
| GET | `/players/me/titles` | MCDR/Web | 已解锁称号 + 当前展示 |
| POST | `/players/me/titles/{id}/activate` | MCDR/Web | `!!title set` 切换展示称号 |
| POST | `/internal/titles/check` | scoring-service | 积分变更后触发解锁检查（内部） |

## 3. 内部实现要点

### 3.1 指数梯度（核心公式）

```
S_升级(tier) = S_基 × r^(tier-1)
```
| tier | 所需积分（示例 S_基=100, r=2） |
|---|---|
| 1 | 100 |
| 2 | 200 |
| 3 | 400 |
| 4 | 800 |
| 5 | 1600 |

- `required_score` 在 `titles` 表预算好存储（非每次算），避免浮点漂移。
- `S_基 / r / tier 上限` 由配置注入（**不硬编码**）。

### 3.2 解锁判定（被 scoring-service 触发）

```python
# 积分变更后调用
def check_unlock(player_uuid, balance):
    titles = db.query("""
        SELECT id FROM titles.titles
        WHERE required_score <= :bal
        AND id NOT IN (SELECT title_id FROM titles.player_titles WHERE player_uuid = :u)
    """, bal=balance, u=player_uuid)
    for t in titles:
        unlock(player_uuid, t.id, balance)

def unlock(player_uuid, title_id, balance):
    db.execute("""
        INSERT INTO titles.player_titles(player_uuid, title_id, unlocked_at)
        VALUES (:u, :t, now())
        ON CONFLICT DO NOTHING
    """, u=player_uuid, t=title_id)
    t = get_title(title_id)
    if t.is_high_tier and t.announce_on_unlock:
        announce_global(t)           # 高阶全服公告（经 MCDR）
    notify_mcdr_refresh_prefix(player_uuid)   # 通知 MCDR 刷新前缀
```
- **触发时机**：scoring-service 每次写流水后调 `/internal/titles/check`，或异步事件。

### 3.3 展示称号切换

```python
# POST /players/me/titles/{id}/activate  (!!title set)
def activate(player_uuid, title_id):
    with db.transaction():
        db.execute("UPDATE titles.player_titles SET is_active=false WHERE player_uuid=:u", u=player_uuid)
        db.execute("""
            UPDATE titles.player_titles SET is_active=true
            WHERE player_uuid=:u AND title_id=:t
        """, u=player_uuid, t=title_id)
    prefix = get_title(title_id).prefix_text
    push_mcdr_apply_prefix(player_uuid, prefix)   # 通知 MCDR 重建 scoreboard team prefix
```
- 每玩家至多一条 `is_active=true`（部分唯一索引保证）。
- 切换后通知 MCDR 重建前缀（[`mcdr-plugin.md`](./mcdr-plugin.md) §3.5）。

### 3.4 scoreboard 前缀分工

| 职责 | 归属 |
|---|---|
| 存 `prefix_text`、判定该用哪个前缀 | **title-service** |
| 执行 `scoreboard teams ... prefix` 命令 | **MCDR 插件** |
| 修正前缀对玩家名解析的干扰 | [Title Prefix Handler](https://mcdreforged.com/zh-CN/plugin/title_prefix_handler) |

- title-service 通过 HTTP（或消息）把 `(player_uuid, prefix_text)` 推给 MCDR；MCDR 落地为 scoreboard team prefix。

### 3.5 高阶称号 wiki 权益

`is_high_tier` 称号解锁时，联动 **wiki-service** 授予该玩家额外 wiki 编辑权（如专属板块），作为荣誉权益的延伸。

## 4. 依赖的其他服务

- **scoring-service**：积分变更事件 → 触发解锁检查。
- **user-service**：玩家身份、改名过户时迁移 `player_titles`。
- **wiki-service**：高阶称号 wiki 权益授权。
- **MCDR 插件**：执行 scoreboard 前缀命令 + 全服公告广播。

## 5. 所属数据表

`titles` schema（见 [`data-model.md`](../data-model.md) §5）：
- `titles`（称号定义，含 `base_score / growth_r / required_score / prefix_text / is_high_tier`）
- `player_titles`（玩家已解锁，复合 PK，`is_active` 部分唯一索引）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| scoreboard 前缀显示效果 | Fabric+Carpet 下实际渲染 | 待真机验证；不达标引入 Fabric 前缀 mod |
| 高阶公告刷屏 | 短时多人解锁 | 限频/合并公告 + `announce_on_unlock` 开关 |
| 前缀文本转义 | `"` `{` 等 JSON 字符 | 转义 + 长度限制 |
| 解锁检查性能 | 每次积分变更触发 | 仅当跨越阈值时检查（缓存上次 tier） |
| 改名迁移 | uuid 变致称号丢失 | user-service 过户事务内一并迁移 `player_titles` |

> 待确认：`S_基 / r / 最高 tier` 具体数值；是否需要「称号过期/赛季重置」机制。
