# 在线表格批量提交 · 端点参考

> `POST /sheets/{sheet_id}/submit-batch`：客户端传材料清单，后端按行 mode 分发
> （lock 认领人交付 / progress 任意人增量上交）+ 回执。行语义/状态机/鉴权总章见
> [`sheets.md`](./sheets.md)；信任边界见 [`../architecture.md`](../architecture.md) §9。
> HTTP 签名以运行时 `/openapi.json` 为准（`/docs` 可联调），本文档记录业务语义与雷点。

**状态**：✅ 已实现

## 鉴权（双通道，镜像 [`../flows/construction-progress.md`](../flows/construction-progress.md) §3）

| 集成方 | 头 | 能写谁 |
|---|---|---|
| 服务端组件（MCDR / 服务端 mod / 服主脚本）| `X-Service-Token` + `X-Player-UUID` | 任意玩家（服主守护 token，**绝不外发** R-11）|
| 玩家客户端模组 | `Authorization: Bearer <玩家 JWT>` | **只能写自己**（身份锚 `JWT.sub`；请求里的 X-Player-UUID 被忽略，伪造无效）|
| 外部第三方 | ❌ 不开放 | — |

两通道经 `get_current_player` 等价注入 Player；两通道皆无/皆有 → 401（H-2 不静默降级）。客户端模组取 JWT：游戏内 `!!PCH login` → Web 兑换。

## 请求 / 响应

**请求** `POST /sheets/{sheet_id}/submit-batch`：
```json
{"items": [{"registry_id": "minecraft:iron_ingot", "qty": 10},
           {"registry_id": "minecraft:oak_log", "qty": 64}]}
```
| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array | 材料清单（1..2000 条）；重复 `registry_id` 自动求和聚合 |
| `items[].registry_id` | string | 物品 registry id（`namespace:path`，如 `minecraft:oak_log`），精确匹配表行 |
| `items[].qty` | int | 本次申报数量（`≥0`；0 = 未携带/申报零，progress 视为未提交 → skip） |

**响应**：
```json
{
  "sheet_id": 42, "actor_uuid": "00000000-...",
  "totals": {"delivered": 1, "contributed": 1, "skipped": 1},
  "outcomes": [
    {"row_id": 1, "registry_id": "minecraft:iron_ingot", "item_name": "铁锭",
     "mode": 0, "action": "delivered", "qty": 10, "reason": "",
     "is_claimant": true, "delivered_qty": 10, "need_qty": 10},
    {"row_id": 2, "registry_id": "minecraft:oak_log", "item_name": "橡木原木",
     "mode": 1, "action": "contributed", "qty": 64, "reason": "",
     "is_claimant": false, "delivered_qty": 64, "need_qty": 100},
    {"row_id": 3, "action": "skipped", "reason": "需先认领", "...": "..."}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `sheet_id` | int | 目标表 id |
| `actor_uuid` | UUID | 实际写入者（JWT 通道 = `JWT.active_uuid`；service-token 通道 = `X-Player-UUID`） |
| `totals` | object | 按 action 分类的行计数（三项之和 = `outcomes` 总数） |
| `totals.delivered` | int | lock 交付完成行数 |
| `totals.contributed` | int | progress 上交行数 |
| `totals.skipped` | int | 跳过行数 |
| `outcomes` | array | 逐行结果（顺序与表行一致） |
| `outcomes[].row_id` | int | 命中的表行 id |
| `outcomes[].registry_id` | string | 行 registry id |
| `outcomes[].item_name` | string | 行中文名（由 `registry_id` 翻译补全） |
| `outcomes[].mode` | int | 行模式（0=lock，1=progress） |
| `outcomes[].action` | string | 本次结果：`delivered` / `contributed` / `skipped` |
| `outcomes[].qty` | int | 实际交付/上交量（skipped=0） |
| `outcomes[].reason` | string | skipped 原因（成功时为空串；全集见下文「行为」） |
| `outcomes[].is_claimant` | bool | lock 行认领人是否在 actor 同账号下（R-5；MCDR 折叠非本人 skip 用） |
| `outcomes[].delivered_qty` | int | 写后行已交付量快照（skipped 取当前值） |
| `outcomes[].need_qty` | int | 行需求量快照 |

## 行为（按行 mode 分流）

| 命中条件 | action | 说明 |
|---|---|---|
| lock + 你是认领人（同 account 任一 UUID，R-5）+ 提交量 ≥ need | `delivered` | qty=need（绝对值），行→done |
| lock 未认领 | `skipped` | reason `需先认领`（**不自动认领**，claim 是独立动作） |
| progress + 提交量 > 0 + 未满 | `contributed` | qty=min(提交, need-delivered)（封顶）；need=0 无限模式永不 done |
| done / 已满 | `skipped` | reason `已备齐` |

reason 全集：lock→`需先认领` / `已被他人认领` / `已备齐` / `无需求` / `数量不足（x/y）`；progress→`已备齐` / `背包没有此物` / `不满足上交条件`。NULL `registry_id` 行不参与（无 outcome）。

### 子物品行（issue #80 起）

子行（`parent_row_id` 非空）与顶层行走**同一套**分流规则，无特殊分支：

- **继承认领者后可直接交付**：父行 lock 且已认领时新建的 lock 子行落库即 `claimed` + 继承父行认领者（同账号）——该玩家 `submit` 携带对应 `registry_id` 即 `delivered`，不再产生「父认领后新建子行永远 open → 永远 skip」死行（详见 [`sheets.md`](./sheets.md) §5.3 级联规则）。
- **子行被他人认领** → skip `已被他人认领`；**open 子行**（如父行尚未认领时先建、或显式 progress 转换前）→ skip `需先认领`。
- **MCDR 侧**：「需先认领」skip **不再折叠**为「与您无关」（`scanner.py::skip_is_noise` 对该 reason 放行）——玩家能看到「XX 需先认领」指引并直接 `!!PCH sheet claim <row_id>`；其余 skip 折叠规则不变。
- **协作端点 409 detail 透传**：单独 claim/release 子行冲突时，后端 `detail` 为中文原因（如「无法认领：行状态为 claimed」），MCDR `_resolve` 直达玩家回执（兜底「状态非法」）。

## 示例（Python `requests`）

```python
# 服务端通道（MCDR / 服务端 mod 代玩家写）
r = requests.post(
    f"{API}/sheets/{sid}/submit-batch",
    json={"items": [{"registry_id": "minecraft:iron_ingot", "qty": 10}]},
    headers={"X-Service-Token": TOKEN, "X-Player-UUID": player_uuid},
    timeout=10,
)
# 客户端通道（玩家 JWT，只写自己）：headers 改 {"Authorization": f"Bearer {jwt}"}
for o in r.json()["outcomes"]:        # 遍历回执
    ...  # o["action"] ∈ delivered/contributed/skipped；skipped 看 o["reason"]
```

## HTTP 错误码与处理

| HTTP | 场景 | 调用方处理 |
|---|---|---|
| 401 | token 缺失/非法；`X-Player-UUID` 非已知玩家 | 检查 token / UUID |
| 404 | `sheet_id` 不存在 | 拉新 sheet 列表 |
| 409 | 项目已归档（只读） | 不可写，提示用户 |
| 422 | `items` 空 / `qty<0` / `registry_id` 空 / >2000 条 | 修请求体 |

## 关键约束

- **匹配键 = `registry_id`**（`namespace:path`）；block id ≠ item id，提交前确认是物品形态 id
- **批量 vs 单行**：本端点一次编排多行；`claim`/`delivery`/`contribute` 是精细单行控制（认领/绝对交付/增量上交），两者可混用
- **权威 schema** = `/openapi.json`（FastAPI 自动生成），跨语言客户端用它生成，不建议手写 SDK

---
*创建：2026-07-26。基于 [`sheets.md`](./sheets.md) §5 端点 + construction-progress §3 鉴权矩阵；行为契约以 `Backend/app/repositories/sheet_repo.py::batch_submit` 为准。*
*增量（2026-08-30，issue #80）：新增「子物品行」节——子行走同一套分流规则；继承认领者后可直接交付；MCDR「需先认领」skip 不再折叠；409 detail 透传。*
