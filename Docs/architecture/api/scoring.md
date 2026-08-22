<!-- omit in toc -->
# scoring API · 端点参考

> `POST /v1/scoring/credit` / `POST /v1/scoring/debit`：服务端组件专用的批量记账端点；
> `POST /v1/scoring/admin/adjust`：管理员（服主）积分调控（**仅 admin·owner JWT**，admin ≠ service-token）；
> `GET /v1/scoring/admin/players`：特权玩家联想（面板选人，仅特权 JWT）；`GET /v1/scoring/admin/balances`：全账号余额排名（面板「玩家积分」tab，仅特权 JWT）；`GET /v1/scoring/ledger`：多角色流水分页查询。全部经 `score_service.write_ledger` 落
> append-only `scoring.score_ledger`（R-2）。settle 编排（Calculator 链、归档自动结算）未实现，
> 设计契约见 [`../flows/scoring-settlement.md`](../flows/scoring-settlement.md)。
> HTTP 签名以运行时 `/openapi.json` 为准（`/docs` 可联调），本文档记录业务语义与雷点。

**状态**：✅ 已实现（迁移 0024，积分层首批）

---

- [1. 概述与鉴权矩阵](#1-%E6%A6%82%E8%BF%B0%E4%B8%8E%E9%89%B4%E6%9D%83%E7%9F%A9%E9%98%B5)
- [2. 端点速查（6 个，前缀 `/v1/scoring`）](#2-%E7%AB%AF%E7%82%B9%E9%80%9F%E6%9F%A56-%E4%B8%AA%E5%89%8D%E7%BC%80-v1scoring)
- [3. credit（`POST /v1/scoring/credit`）](#3-creditpost-v1scoringcredit)
- [4. debit（`POST /v1/scoring/debit`）](#4-debitpost-v1scoringdebit)
- [5. admin adjust（`POST /v1/scoring/admin/adjust`）](#5-admin-adjustpost-v1scoringadminadjust)
- [6. ledger（`GET /v1/scoring/ledger`）](#6-ledgerget-v1scoringledger)
- [7. HTTP 错误码与处理](#7-http-%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E5%A4%84%E7%90%86)
- [8. 示例（Python `requests`）](#8-%E7%A4%BA%E4%BE%8Bpython-requests)

## 1. 概述与鉴权矩阵

积分变动的 REST 写通道收敛为批量端点（逐条独立、skip 不连坐，见 §4）；内部统一经 `score_service.write_ledger` 落库（RS-9 范式：不 commit，通知同事务原子）。表结构 / 触发器 / 索引见 [`../flows/scoring-settlement.md`](../flows/scoring-settlement.md) §6。

| 调用方 | 头 | 能调什么 |
|---|---|---|
| 服务端组件（MCDR / 服务端 mod / 服主脚本）| `X-Service-Token` | `credit` / `debit` 批量代任意玩家记账 + `ledger` **特权**（全局 + 任意玩家）。**admin 端点不放行**（admin ≠ service-token，见 §5）|
| admin / owner（含 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 环境同步的托管面板账号）| `Authorization: Bearer <JWT>` | `admin/adjust`（见 §5）+ `admin/players` + `admin/balances` + `ledger` **特权**（全局 + 任意玩家）|
| 普通玩家 | `Authorization: Bearer <JWT>` | `ledger` **仅自己**（作用域见 §6）|
| 玩家 JWT 调 credit / debit | ❌ 不开放 | 两端点结构上无 Authorization 处理 → 401；admin 端点（adjust / players / balances）对普通玩家 403 |
| service-token 调 admin 端点 | ❌ 不放行 | admin 端点（adjust / players / balances）仅特权 JWT，service-token（含正确值）→ 401 `missing authorization` |
| 外部第三方 | ❌ 不开放 | —（YAGNI，R-11）|

**H-2**：`Authorization` 头存在（即便非 Bearer/非法）只走 JWT 通道，**绝不静默降级** service-token——credit / debit 无 JWT 通道，带该头直接 401。

## 2. 端点速查（6 个，前缀 `/v1/scoring`）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/credit` | 仅 `X-Service-Token` | 批量积分新增（delta = +amount）|
| POST | `/debit` | 仅 `X-Service-Token` | 批量积分扣除（delta = −amount；`allow_overdraft` 开关）|
| POST | `/admin/adjust` | **仅** admin/owner JWT（service-token 401）| 管理员调控任意玩家积分（**方向由 reason 定**，双向；见 §5）|
| GET | `/admin/players` | **仅** admin/owner JWT（service-token 401）| 特权玩家联想（面板调分/筛选选人，见 §5.1）|
| GET | `/admin/balances` | **仅** admin/owner JWT（service-token 401）| 全账号余额排名（面板「玩家积分」tab，见 §5.2）|
| GET | `/ledger` | 多角色（见 §1 / §6）| 流水查询（日期范围 + 分页）|

## 3. credit（`POST /v1/scoring/credit`）

**请求**：
```json
{"items": [{"player_uuid": "550e8400-...", "amount": "12.50", "reason": "collect",
            "sheet_id": null, "operator_uuid": null,
            "idempotency_key": "op-2026-08-15-001", "note": "月度奖励"}],
 "notify": true}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array | 批量条目（1..100，`MAX_BATCH_ITEMS=100`；超限 422 整批拒）|
| `items[].player_uuid` | UUID | 目标玩家；不存在 / 未绑 Web 账号 → 该条 skip（见 §4）|
| `items[].amount` | string | **Decimal 字符串恒正**（>0、≤18 位总宽、≤2 位小数，违例 422 整批拒）；方向由端点定（credit = +，debit = −）|
| `items[].reason` | string | credit 枚举：`collect` / `build_a` / `leader_bonus` / `settle`；越界 422 整批拒 |
| `items[].sheet_id` | int \| null | 可选；提供时校验 sheets 存在，不存在该条 skip。**弱引用无 FK**（append-only 审计不连坐）|
| `items[].operator_uuid` | UUID \| null | 可选；出账时记录管理员 UUID |
| `items[].idempotency_key` | string \| null | 可选（1..128 字符）；作用域 `(account_id, key)`。同 key 同 payload（delta/reason/sheet_id）→ 回放原条目；不一致 → 该条 skip（见 §4）|
| `items[].note` | string \| null | 可选（≤200 字符）；运维备注 |
| `notify` | bool | 默认 true；发站内通知（category = `scoring_credit` / `scoring_debit`，**同事务原子** RS-9）；skip 条目与**幂等重放**不发（MCDR 重试语义下副作用不重复）|

**响应**（200）：
```json
{"results": [{"player_uuid": "550e8400-...", "accepted": true, "idempotent_replay": false,
              "entry": {"id": 1, "account_id": 7, "delta": "12.50", "reason": "collect",
                        "balance_after": "12.50", "sheet_id": null, "operator_uuid": null,
                        "idempotency_key": "op-2026-08-15-001", "note": "月度奖励",
                        "created_at": "2026-08-15T10:00:00Z"},
              "skip_reason": null}],
 "accepted_count": 1, "skipped_count": 0}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `results` | array | 逐条结果（顺序与 `items` 一致）|
| `results[].player_uuid` | UUID | 回显目标玩家 |
| `results[].accepted` | bool | 该条是否落库（幂等回放同样计 accepted）|
| `results[].idempotent_replay` | bool | true = 命中幂等键回放原条目（不重复记账）|
| `results[].entry` | object \| null | 落库条目快照（幂等回放 = 原条目；skip = null）；字段即 `score_ledger` 列 |
| `results[].skip_reason` | string \| null | skip 原因（全集见 §4；成功为 null）|
| `accepted_count` / `skipped_count` | int | 计数（两者之和 = items 条数）|

金额（`amount` / `delta` / `balance_after`）**一律字符串传输**（Decimal 精度）。

## 4. debit（`POST /v1/scoring/debit`）

请求 / 响应结构与 §3 credit 完全一致（方向翻转为 delta = −amount），仅两点差异：

| 差异点 | debit 行为 |
|---|---|
| `items[].reason` 枚举 | `manual_adj`（手动修正，配 `operator_uuid`）/ `season_reset`（赛季重置）|
| `allow_overdraft` | **仅 debit**，默认 false：false 且余额不足 → 该条 skip `insufficient balance`；true → 允许扣成负数（大额缴回场景）|

### skip_reason 全集（credit / debit 通用）

| skip_reason | 场景 | 调用方处理 |
|---|---|---|
| `player not found` | `player_uuid` 非已知玩家 | 校验 uuid |
| `player not bound to a web account` | 玩家未绑 Web 账号（R-5 锚缺失）| 引导绑定 |
| `sheet not found` | 提供了 `sheet_id` 但 sheets 无此行 | 修正或置 null |
| `insufficient balance` | debit 且 `allow_overdraft=false` 余额不足 | 补额 / 开透支 |
| `idempotency key conflict` | 同 `(account_id, key)` 已存在但 payload 不一致 | 检查 key 复用 |

### 批量语义（逐条独立，参照 [`./construction.md`](./construction.md) §3 惯例）

| 错误层级 | 例子 | 结果 |
|---|---|---|
| schema 级 → 整批 422 | `items` 空 / >100；`amount` 非法（≤0 / >18 位 / >2 位小数）；`reason` 越界；字段类型错 | 整批拒，0 条落库 |
| 业务级 → 该条 skip，HTTP 恒 200 | 上表 5 类 skip_reason | 仅该条不落库，其余正常 |
| 成功 | — | 落库 +（notify 时）同事务通知 |

## 5. admin adjust（`POST /v1/scoring/admin/adjust`）

管理员（服主）积分调控端点：**仅特权 JWT**（`require_privileged_access`）——`Authorization: Bearer <JWT>` 且 role ∈ {admin, owner}（积分管理面板通道；普通玩家 JWT → 403）。**admin ≠ service-token**：本端点不认 `X-Service-Token`（含正确值）→ 401 `missing authorization`——系统组件代玩家记账一律走 credit/debit，管理操作与管理面板绑定。面板账号经环境变量同步：`.env` 配 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（两者均非空才启用），后端启动 lifespan 幂等同步为 role=owner 的无绑定玩家 WebAccount（`sync_admin_account`，env 为该账号密码权威源，修改需重启 backend；未配置静默跳过），复用 `POST /auth/login` 登录。

**与 credit/debit 的差异**（其余请求/响应结构、批量语义、skip_reason 全集、通知行为完全一致，复用 §3/§4 契约）：

| 差异点 | admin/adjust 行为 |
|---|---|
| `items[].reason` 枚举 | **全集 6 种**：`collect` / `build_a` / `leader_bonus` / `settle`（加）/ `manual_adj` / `season_reset`（减）|
| delta 方向 | **由 reason 符号定**（`LEDGER_REASON_SIGN`）：入账 reason → `+amount`，出账 reason → `−amount`——单端点双向，加减分无需选端点 |
| `allow_overdraft` | 语义同 debit：默认 false 余额不足 skip `insufficient balance`；true 允许扣成负数 |
| 审计 | 操作者经审计日志 `operator=jwt-account:<id>` 标签记录（唯一通道即 JWT；面板托管账号无绑定玩家、不传 `operator_uuid`——它是 Player UUID）；`operator_uuid` / `note` 由调用方按条提供（如需指认某管理员玩家）|

```json
{"items": [{"player_uuid": "550e8400-...", "amount": "3", "reason": "manual_adj",
            "note": "误发回收"}],
 "notify": true, "allow_overdraft": false}
```

### 5.1 特权玩家联想（`GET /v1/scoring/admin/players`）

面板调分/筛选「玩家名 → UUID」联想端点，鉴权同 admin/adjust（**仅特权 JWT**，普通玩家 403、service-token 401）。与 `GET /players` 同源（`player_repo.search_for_manager`）但走特权鉴权——托管 admin 账号无绑定玩家，调不了需玩家身份（`active_uuid`）的 `get_current_player` 通道。仅返回已绑 WebAccount 的玩家。

```
GET /v1/scoring/admin/players?q=ali&limit=10   →  [{"player_uuid": "…", "player_name": "alice", "display_name": "…"}]
```

### 5.2 余额排名（`GET /v1/scoring/admin/balances`）

所有玩家（WebAccount）当前积分余额排名（面板「玩家积分」tab），鉴权同 admin/adjust（**仅特权 JWT**，普通玩家 403、service-token 401）。

- **行集** = 有 ≥1 绑定玩家的 WebAccount（积分归属锚 = WebAccount，R-5；同账号多玩家一行，未绑定玩家不入榜）。
- **`balance` = SUM(delta)**（append-only 可审计重建，R-2，与该账号最新 `balance_after` 恒一致；无流水 → `0.00`）。
- 排序 `balance DESC` + `account_id ASC`（平分稳定序）；`player_names` 按 `last_seen_at` DESC（同名去重），`display_name` 空 → 回退首个玩家名（#41 回退链）。

```
GET /v1/scoring/admin/balances?page=1&limit=50
→ {"items": [{"account_id": 7, "display_name": "alice", "player_names": ["alice", "alice_old"],
              "balance": "130.00", "entries_count": 2, "last_entry_at": "2026-08-19T10:00:00Z"}],
    "total": 1, "page": 1, "limit": 50}
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | ≥1 |
| `limit` | int | 50 | 1..200（同 ledger，超限 422）|

## 6. ledger（`GET /v1/scoring/ledger`）

**请求**：

```
GET /v1/scoring/ledger?player_uuid=…&since=2026-08-01T00:00:00Z&until=…&page=1&limit=50
GET /v1/scoring/ledger?account_id=42&page=1&limit=50        # 余额下钻：特权直按账号收敛
```

**作用域解析**（`player_uuid` / `account_id` 均可选、**互斥**（同传 422），按调用方凭证分流）：

| 调用方 | 过滤参数全省略 | player_uuid = 自己 | player_uuid = 他人 | account_id |
|---|---|---|---|---|
| service-token（特权）| 全局流水 | 该玩家 | 该玩家；未知 uuid → 404 `player not found` | 该账号；未知 → 404 `account not found` |
| JWT admin / owner（特权）| 全局流水 | 该玩家 | 同上 | 同上 |
| 普通玩家 JWT | **默认查自己** | 自己 | 403 `forbidden`（全局 / 他人一律不可得）| 403（显式拒绝；自账号放行为**将来预留语义，暂未实现**）|

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `player_uuid` | UUID | — | 可选；作用域见上表；与 `account_id` 互斥 |
| `account_id` | int | — | 可选（≥1）；**特权专用**直按账号收敛（余额行下钻入口，多 UUID 账号整账号可见）|
| `since` | ISO 8601（tz-aware）| — | `created_at >= since` |
| `until` | ISO 8601（tz-aware）| — | `created_at < until`（开区间上界）|
| `page` | int | 1 | ≥1 |
| `limit` | int | 50 | 1..200（防单次爆量，超限 422）|

排序 `id DESC`（最新在前）。**响应**：`{"items": [ScoreLedgerEntry…], "total": 123, "page": 1, "limit": 50}`；`items[]` 字段同 §3 响应 `entry`。

## 7. HTTP 错误码与处理

| HTTP | 场景 | 调用方处理 |
|---|---|---|
| 401 | 缺/错 service token；JWT 无效（ledger / adjust / players / balances；credit / debit 带 Authorization 头）；admin 端点（adjust / players / balances）无 Authorization（含仅带 service-token）| 检查凭证 |
| 403 | ledger 普通玩家查他人；ledger 普通玩家传 `account_id`；普通玩家 JWT 调 admin 端点（adjust / players / balances，`forbidden`）| 改查自己或提权 |
| 404 | ledger 特权方传未知 `player_uuid`（`player not found`）或未知 `account_id`（`account not found`）| 校验 uuid / 账号 id |
| 422 | schema 级：reason 越界 / amount 非法 / items 超 100 / limit>200；ledger `player_uuid` 与 `account_id` 同传（互斥）| 修请求体 |
| 200 | 批量端点恒 200，逐条看 `results[].accepted` / `skip_reason` | 按条处理 |

## 8. 示例（Python `requests`）

```python
import requests

BASE = "http://localhost:8000"
H = {"X-Service-Token": "svc", "Content-Type": "application/json"}

# 批量加分（幂等键防 MCDR 重试重复记账）
r = requests.post(f"{BASE}/v1/scoring/credit", headers=H, timeout=10, json={
    "items": [{"player_uuid": "550e8400-...", "amount": "12.50", "reason": "collect",
               "idempotency_key": "daily-2026-08-15-001"}],
    "notify": True})

# 扣分（大额缴回，允许透支）
r = requests.post(f"{BASE}/v1/scoring/debit", headers=H, timeout=10, json={
    "items": [{"player_uuid": "550e8400-...", "amount": "25.00", "reason": "manual_adj",
               "operator_uuid": "<管理员 uuid>", "note": "回收误发"}],
    "allow_overdraft": True})

# 管理员调控（仅特权 JWT 通道：/auth/login 用 ADMIN_USERNAME/ADMIN_PASSWORD
# 环境同步的托管账号登录；admin ≠ service-token，X-Service-Token 调本端点 → 401）
B = {"Authorization": f"Bearer {login_access_token}"}
r = requests.post(f"{BASE}/v1/scoring/admin/adjust", headers=B, timeout=10, json={
    "items": [{"player_uuid": "550e8400-...", "amount": "3", "reason": "manual_adj",
               "note": "误发回收"}]})  # 操作者经审计日志 operator=jwt-account:<id> 记录

# 特权玩家联想（面板选人：玩家名前缀 → uuid）
r = requests.get(f"{BASE}/v1/scoring/admin/players", headers=B, timeout=10,
                 params={"q": "ali"}).json()

# 余额排名（面板「玩家积分」tab；balance = SUM(delta)，排序 balance DESC）
r = requests.get(f"{BASE}/v1/scoring/admin/balances", headers=B, timeout=10,
                 params={"page": 1, "limit": 50}).json()

# 流水分页迭代（service-token 特权；普通玩家 JWT 省略 player_uuid 即查自己）
page = 1
while True:
    r = requests.get(f"{BASE}/v1/scoring/ledger", headers=H, timeout=10,
                     params={"page": page, "limit": 200,
                             "since": "2026-08-01T00:00:00Z"}).json()
    for e in r["items"]:
        print(e)
    if page * r["limit"] >= r["total"]:
        break
    page += 1
```

---

*创建：2026-08-15；2026-08-19 增量：admin/adjust 收敛为**仅特权 JWT**（admin ≠ service-token，service-token 调用 401）+ `admin/players` 联想端点（同鉴权）+ 环境同步托管账号（积分管理面板）；同日增量 2：`admin/balances` 余额排名端点（同鉴权，balance = SUM(delta) 重建 + balance DESC 排名，面板「玩家积分」tab 消费）；同日增量 3：ledger 加 `account_id` 查询参数（特权专用直按账号收敛，余额行下钻入口；普通玩家 403、与 `player_uuid` 互斥 422）+ 调分通知文案携带 note（`reason: note`，标点 ASCII 避开通知清洗白名单）。行为契约以 `Backend/app/services/score_service.py::write_ledger` 与 `Backend/app/api/scoring.py` 为准；权威 schema = `/openapi.json`。*
