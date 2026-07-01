# 服务文档：scoring-service（积分结算引擎）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5 / §7.2
> **数据模型**：[`../data-model.md`](../data-model.md) §4（`scoring` schema）
> **玩法公式依据**：[`../../guied.md`](../../guied.md) 黄皮子积分体系

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 材料提交入库（`submissions`） | 箱子扫描（在 mcdr-plugin） |
| A 类放置贡献记录（`placement_records`） | 项目配置（交 project-service） |
| 黄皮子积分引擎（占比/加权/负责人增发） | 称号梯度判定（交 title-service） |
| 积分流水（`score_ledger` append-only） | 玩家身份（交 user-service） |
| 榜单计算（总/赛季/分类） | wiki 同步（交 wiki-service） |
| 终算与手动修正 | 异常告警判定（交 alert-service） |

**定位**：系统的心脏。所有「贡献→积分」的换算与记账在此完成，是数据一致性要求最高的服务。

## 2. 对外接口（REST API）

| 方法 | 路径 | 调用方 | 说明 |
|---|---|---|---|
| POST | `/submissions` | MCDR | `!!submit` 材料提交（含 batch_token 防重复） |
| POST | `/placement-records` | Web(admin) | A 类建造贡献录入（区域扫描/手动） |
| POST | `/projects/{id}/settle` | project-service | 终算（占比/加权/负责人 k 增发） |
| GET | `/scores/me` | MCDR/Web | 个人积分（总/赛季/分类） |
| GET | `/scores/rank` | MCDR/Web | 榜单（总/赛季/分类，分页） |
| POST | `/admin/scores/adjust` | Web(admin/owner) | 手动修正（写 reason=manual_adj） |

## 3. 内部实现要点

### 3.1 提交结算事务（核心链路）

```python
# POST /submissions —— 单次 !!submit 一个 batch_token，可含多物品
def submit(project_id, player_uuid, items: list[Item], batch_token):
    with db.transaction():
        for it in items:
            # 1. 防重复：唯一约束 (project_id, player_uuid, item_id, batch_token)
            db.execute("INSERT INTO scoring.submissions(...) VALUES (...)", ...)
            # 2. 累加材料交付量
            db.execute("""
                UPDATE projects.material_lists
                SET delivered_qty = delivered_qty + :qty
                WHERE project_id = :pid AND item_id = :iid
            """, ...)
        # 3. 按占比即时记账（收集类）
        delta = allocate_collect_score(project_id, player_uuid, items)
        # 4. 写流水 + 更新冗余总分
        write_ledger(player_uuid, project_id, delta, reason="submit")
    return delta
```
- **顺序保证**：MCDR 端「上报成功 → 才清箱」（见 [`mcdr-plugin.md`](./mcdr-plugin.md) §3.2），本服务事务失败则回滚，玩家可重试。

### 3.2 黄皮子积分公式

| 类型 | 公式 | 说明 |
|---|---|---|
| 收集类（COLLECT） | `S_i = S_总 × (n_i / N_总)` | `n_i`=玩家 i 在项目总提交量，`N_总`=项目总提交量；按占比分摊固定积分池 |
| 建造类（BUILD_A） | `G_i = α·(t_i/T) + β·(p_i/P)` | `t`=工作量占比，`p`=材料贡献占比，`α+β=1`；加权贡献度 |
| 负责人增发 | `S_负责人 = S_全体 × k` | 负责人额外获得全体积分的 k 倍（`k∈[0.05,0.5]` 分档） |
| 称号升级阈值 | `S_升级 = S_基 × r^(tier-1)` | 在 [title-service](./title-service.md) 判定，本服务提供余额 |

- 参数 `S_总 / score_cap / α / β / k` 由 project-service 配置或环境变量注入，**不硬编码**。
- 收集类占比用窗口函数实现（见 [`data-model.md`](../data-model.md) §8）。

### 3.3 占比结算 SQL（窗口函数）

```sql
-- 项目终算：每个玩家在项目内的提交占比 × S_总
WITH proj_total AS (
    SELECT project_id, SUM(qty) AS N FROM scoring.submissions
    WHERE project_id = :pid AND status='confirmed' GROUP BY project_id
)
SELECT s.player_uuid,
       (:S_pool * SUM(s.qty) / pt.N) AS score_i
FROM scoring.submissions s
JOIN proj_total pt ON pt.project_id = s.project_id
WHERE s.project_id = :pid AND s.status='confirmed'
GROUP BY s.player_uuid, pt.N;
```
- **幂等终算**：`settle` 前检查是否已终算（`reason='settle'` 的流水），重复调用先冲销再重算，或拒绝重算。

### 3.4 积分流水 append-only

```python
def write_ledger(player_uuid, project_id, delta, reason, operator=None):
    bal = current_balance(player_uuid) + delta
    db.execute("""
        INSERT INTO scoring.score_ledger
        (player_uuid, project_id, delta, reason, balance_after, operator)
        VALUES (:u, :p, :d, :r, :b, :op)
    """, u=player_uuid, p=project_id, d=delta, r=reason, b=bal, op=operator)
    db.execute("UPDATE users.players SET total_score = :b WHERE uuid = :u", ...)
```
- **禁止 UPDATE/DELETE**：由数据库角色权限 + 触发器保证，`balance_after` 使流水可审计、榜单可重建。
- `reason` 枚举：`submit / place / leader_bonus / settle / manual_adj / season_reset`。

### 3.5 防重复提交

- `submissions` 上 `uniq(project_id, player_uuid, item_id, batch_token)`：同一次 `!!submit`（一个 batch_token）的同一物品重复上报直接被约束拒绝。
- batch_token 由 MCDR 每次命令生成（uuid4），天然防网络重放。

### 3.6 手动修正

```python
@app.post("/admin/scores/adjust")
async def adjust(body, _=Depends(require_role("admin", "owner"))):
    write_ledger(body.player_uuid, None, body.delta,
                 reason="manual_adj", operator=current_user())
```
- 修正可正可负（如回收误发），全程留痕（`operator` + `balance_after`）。

## 4. 依赖的其他服务

- **user-service**：解析玩家身份、积分归属锚（见 [`user-service.md`](./user-service.md) §3.1 归属粒度待确认）。
- **project-service**：取 `total_score_pool / score_cap`、`material_lists.required_qty`、状态机触发终算。
- **title-service**：积分变更后通知检查称号升级（`S_升级 = S_基 × r^(tier-1)`）。
- **wiki-service**：榜单变化同步到名人堂。
- **alert-service**：积分异常波动（score_spike）触发告警。

## 5. 所属数据表

`scoring` schema（见 [`data-model.md`](../data-model.md) §4）：
- `submissions`（材料提交，含 `batch_token` 防重复）
- `placement_records`（A 类放置贡献）
- `score_ledger`（积分流水，**append-only**）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| 并发提交竞争 | 同项目多人同时提交致占比错算 | 行锁 + 事务 + 终算时一次性重算（不依赖即时占比） |
| 终算幂等 | 重复终算多发积分 | settle 前查 `reason='settle'` 流水，幂等键 |
| 积分归属粒度 | 按 uuid 还是 account（与 user-service 联动） | **待确认**：建议 `score_ledger` 加 `owner_account_id` 冗余 |
| 负积分/透支 | 手动修正或回收致负 | `balance_after` 允许负但告警；或加非负约束按需 |
| 榜单重算成本 | 全量重算慢 | `total_score` 冗余 + 赛季窗口增量重算 |
| 占比公式 N 的口径 | 按总提交还是总需求 | **待确认**：建议按实际总提交（防止超量交付刷分→配 `score_cap`） |

> 待确认：负责人 k 系数分档、A 类 `α/β`、赛季重置周期的具体数值与触发规则。
