# 全局数据模型（data-model）

> **统一总览**：[`../architecture.md`](../architecture.md) §6
> 本文档定义后端唯一数据库 **PostgreSQL** 的全部表结构、约束与索引。
> 模块化单体内按 **schema 隔离**各领域模块（部署仍是单库）。

## 0. 设计约定

- **玩家主键 = MC UUID**（离线模式 OfflinePlayer 确定性推导，见 [`services/user-service.md`](./services/user-service.md)）。`UUID` 类型为 PostgreSQL 原生 `uuid`。
- **身份主锚 = Web 账号**：`players.web_account_id` 可空（未绑定时），绑定后指向 `web_accounts.id`。离线改名通过更新映射做过户，不丢积分。
- **物品 id = registry id**：统一 `namespace:path`（如 `create:warehouse`、`minecraft:chest`），存储前剥离 BlockState properties。
- **时间戳**：`TIMESTAMPTZ`，统一存 UTC。
- **积分流水 append-only**：任何积分变动写一条 `score_ledger`，含 `balance_after`，可审计与重建榜单。
- **schema 划分**：`users` / `projects` / `scoring` / `titles` / `wiki` / `alerts`（原架构 6 个）+ `sheets`（MVP 第一阶段新增，见 §10 附录）。

## 1. 全局 ER 图

```mermaid
erDiagram
    web_accounts ||--o{ players : "绑定"
    players ||--o{ bind_tokens : "签发"
    players ||--o{ submissions : "提交"
    players ||--o{ placement_records : "放置"
    players ||--o{ score_ledger : "积分流水"
    players ||--o{ player_titles : "持有称号"
    projects ||--o{ material_lists : "材料清单"
    projects ||--o{ submissions : "接收"
    projects ||--o{ placement_records : "接收"
    projects }o--|| players : "负责人"
    titles ||--o{ player_titles : "解锁为"
    projects ||--o{ wiki_sync_log : "同步"
    players ||--o{ alerts : "触发"
```

---

## 2. `users` schema —— 身份核心

### 2.1 `players`（玩家）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `uuid` | uuid | PK | MC OfflinePlayer UUID |
| `current_name` | text | not null | 当前游戏名（离线改名会变） |
| `web_account_id` | bigint | FK→web_accounts.id, null | 绑定的 Web 账号（身份主锚） |
| `total_score` | numeric(18,2) | not null default 0 | 历史累计积分（冗余，由 ledger 重建） |
| `current_title_id` | bigint | FK→titles.id, null | 当前展示称号 |
| `whitelist_state` | text | not null default 'active' | active/under_review/removed |
| `credit_score` | int | not null default 100 | 负责人信用分（项目互评） |
| `first_seen_at` | timestamptz | not null | 首次入服 |
| `last_seen_at` | timestamptz | null | 最近在线 |

索引：`uniq(current_name)` 部分索引（仅 active）；`idx(web_account_id)`。

### 2.2 `web_accounts`（Web 账号 —— 身份主锚）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `username` | text | unique not null | 登录名/邮箱 |
| `password_hash` | text | not null | bcrypt/argon2 |
| `role` | text | not null default 'user' | user/admin/owner |
| `created_at` | timestamptz | not null | |

### 2.3 `bind_tokens`（游戏内绑定令牌）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `token` | uuid | PK | 一次性随机 token |
| `player_uuid` | uuid | FK→players.uuid, not null | 申请绑定的玩家 |
| `expires_at` | timestamptz | not null | 短有效期（如 10 分钟） |
| `used_at` | timestamptz | null | 已使用时间 |
| `created_at` | timestamptz | not null | |

索引：`idx(player_uuid)`；定期清理过期 token。

> 流程：玩家游戏内 `!!bind` → 生成 `bind_tokens` → 后端返回短码 → 玩家 Web 端输入 → 写 `players.web_account_id` + `used_at`。

---

## 3. `projects` schema —— 项目与材料

### 3.1 `projects`（项目）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `name` | text | not null | 项目名 |
| `type` | text | not null | `COLLECT` / `BUILD_A` |
| `total_score_pool` | numeric(18,2) | not null | 固定总积分池 S_总 |
| `score_cap` | numeric(18,2) | null | 单项目兜底上限 |
| `status` | text | not null default 'draft' | draft/active/settling/archived |
| `leader_uuid` | uuid | FK→players.uuid, not null | 项目负责人 |
| `phase_meta` | jsonb | null | 阶段拆分/里程碑 |
| `litematic_path` | text | null | 已上传投影文件路径 |
| `created_at` / `archived_at` | timestamptz | | |

索引：`idx(status)`、`idx(leader_uuid)`。

### 3.2 `material_lists`（材料清单 —— 来自 .litematic 解析）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `project_id` | bigint | FK→projects.id, not null | |
| `item_id` | text | not null | registry id，如 `create:warehouse` |
| `required_qty` | int | not null | 需求量 |
| `delivered_qty` | int | not null default 0 | 已交付（冗余，由 submissions 汇总） |

约束：`uniq(project_id, item_id)`。

---

## 4. `scoring` schema —— 提交、放置、流水

### 4.1 `submissions`（材料提交记录）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `project_id` | bigint | FK, not null | |
| `player_uuid` | uuid | FK, not null | |
| `item_id` | text | not null | registry id |
| `qty` | int | not null | 本次提交数量 |
| `batch_token` | uuid | not null | 一次 `!!submit` 一个批次（多物品共用） |
| `status` | text | not null default 'confirmed' | confirmed/reverted |
| `submitted_at` | timestamptz | not null | |

约束：`uniq(project_id, player_uuid, item_id, batch_token)` 防重复；`idx(project_id, item_id)`、`idx(player_uuid)`。

### 4.2 `placement_records`（A 类放置贡献）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `project_id` | bigint | FK, not null | |
| `player_uuid` | uuid | FK, not null | |
| `work_units` | numeric(12,2) | not null | 放置工作量（方块数/工时） |
| `source` | text | not null | 记录来源（如区域扫描/手动） |
| `recorded_at` | timestamptz | not null | |

索引：`idx(project_id, player_uuid)`。

### 4.3 `score_ledger`（积分流水 —— append-only）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `player_uuid` | uuid | FK, not null | |
| `project_id` | bigint | FK, null | 关联项目（运维转移可为空） |
| `delta` | numeric(18,2) | not null | 增减（可负，如修正/回收） |
| `reason` | text | not null | submit/place/leader_bonus/manual_adj/season_reset |
| `balance_after` | numeric(18,2) | not null | 变更后余额 |
| `operator` | text | null | 手动修正时的操作者 |
| `created_at` | timestamptz | not null | |

索引：`idx(player_uuid, created_at desc)`、`idx(project_id)`、`idx(reason)`。**禁止 UPDATE/DELETE**（由权限/触发器保证）。

---

## 5. `titles` schema —— 称号体系

### 5.1 `titles`（称号定义）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `code` | text | unique not null | 程序标识 |
| `name` | text | not null | 显示名 |
| `tier` | int | not null | 阶位（1 起） |
| `base_score` | numeric(14,2) | not null | S_基 |
| `growth_r` | numeric(6,3) | not null | 增长系数 r |
| `required_score` | numeric(14,2) | not null | 解锁所需积分 `S_基×r^(tier-1)` |
| `prefix_text` | text | null | 聊天前缀文本（scoreboard 用） |
| `is_high_tier` | bool | not null default false | 是否触发全服公告 / wiki 权益 |
| `announce_on_unlock` | bool | not null default true | |

### 5.2 `player_titles`（玩家已解锁称号）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `player_uuid` | uuid | FK, PK | |
| `title_id` | bigint | FK, PK | |
| `unlocked_at` | timestamptz | not null | |
| `is_active` | bool | not null default false | 当前是否展示 |

约束：复合 PK `(player_uuid, title_id)`；每玩家至多一条 `is_active=true`（部分唯一索引）。

---

## 6. `wiki` schema —— wiki.js 同步

### 6.1 `wiki_sync_log`（同步日志 / 幂等表）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `entity_type` | text | not null | project_archive/leaderboard/page_rule/user_group |
| `entity_id` | text | not null | 关联业务实体 |
| `wiki_page_id` | int | null | wiki.js 页面 id |
| `action` | text | not null | create/update/delete/grant/revoke |
| `status` | text | not null | pending/done/failed |
| `payload` | jsonb | null | 请求快照 |
| `error` | text | null | 失败原因 |
| `synced_at` | timestamptz | not null | |

索引：`idx(entity_type, entity_id)`、`idx(status)`。失败可重试，幂等靠 `entity_type+entity_id+action`。

---

## 7. `alerts` schema —— 风控告警

### 7.1 `alerts`（异常事件）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `type` | text | not null | score_spike/behavior_abuse/manual |
| `player_uuid` | uuid | FK, null | |
| `project_id` | bigint | FK, null | |
| `severity` | text | not null | low/medium/high |
| `message` | text | not null | |
| `evidence` | jsonb | null | 触发依据（数值快照） |
| `status` | text | not null default 'new' | new/ack/resolved |
| `created_at` | timestamptz | not null | |

索引：`idx(status, created_at)`、`idx(player_uuid)`。

---

## 8. 关键查询模式（索引依据）

- **占比结算**（收集类 `S_i = S_总 × n_i/N_总`）：窗口函数 `SUM(qty) OVER (PARTITION BY project_id, item_id)`。
- **A 类贡献度** `G = α(t/T)+β(p/P)`：分别聚合 `placement_records`（t）与 `submissions`（p）占比，加权。
- **榜单**：`players.total_score` 排序 + 赛季窗口（按 `score_ledger.created_at` 范围重算）。
- **进度监控**：`material_lists.delivered_qty / required_qty`。

## 9. 迁移与种子

- 使用 **Alembic** 管理迁移；每个 schema/模块一组迁移脚本。
- 种子数据：`titles` 的初始梯度、`web_accounts` 的初始管理员。
- 所有迁移**可重入**，回滚脚本必填。

> 待确认：负责人系数 `k` 分档、A 类 `α/β`、称号 `S_基/r` 的具体数值，由配置表或环境变量注入（不硬编码）。

---

## 10. 附录：`sheets` schema —— MVP 第一阶段在线表格

> MVP 第一阶段新增（不在原架构 6 schema 内），权威定义见 [`../../Plans/MVP-第一阶段计划.md`](../../Plans/MVP-第一阶段计划.md) §3.2，落地迁移 `0004_sheets`。与 `projects.material_lists`（投影解析、强制 registry id）是**两套体系**：sheets 是玩家自建、自由文本、Web + 游戏内双向可编辑的轻量协作表。

### 10.1 `sheets`（表格主表）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `owner_uuid` | uuid | FK→players.uuid, not null | 表主（身份锚 R-5） |
| `title` | text | not null | 表标题 |
| `created_at` / `updated_at` | timestamptz | not null default now() | |

### 10.2 `sheet_rows`（表格行）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `sheet_id` | bigint | FK→sheets.id `ON DELETE CASCADE`, not null | 删表级联删行 |
| `item_name` | text | not null | 自由文本（红线 R-6 不覆盖 sheets） |
| `need_qty` | integer | not null default 0 | 原始整数，永不存换算结果 |
| `mode` | smallint | not null default 0 | 0=lock（二元备齐，单人锁定）/ 1=progress（进度，多人贡献者列表） |
| `status` | text | not null default 'open' | open / claimed / done（见下不变量） |
| `claimant_uuid` | uuid | FK→players.uuid, null | lock 模式当前认领人（open 态 null）；**progress 模式恒 null** |
| `delivered_qty` | integer | not null default 0 | lock 认领人维护；progress 任何人 `contribute` 累加 |
| `sort_order` | integer | not null default 0 | |
| `updated_at` | timestamptz | not null default now() | |

约束：`UNIQUE(sheet_id, item_name)`（兼作 upsert 锁点）。索引：`idx(sheet_id)`、`idx(sheet_id, status)`（迁移 0005）。

**双模式不变量**（迁移 0005 协作改进 + 0007 progress 多人贡献者，推翻原 spec D-4）：
- **lock（mode=0）**：单认领人状态机 `open → claimed → done`（claim / delivery / release / reject）；`open ⇒ claimant IS NULL ∧ delivered=0`，`claimed ⇒ claimant NOT NULL`，`done ⇒ claimant NOT NULL ∧ delivered≥need`。
- **progress（mode=1）**：`claimant_uuid` **恒 null**（不锁定单人）；`status` 由 `delivered_qty` 推导（`=0 → open` / `0<x<need → claimed` / `>=need → done`）；贡献者走 `sheet_row_contributors` 子表，任何人 `POST /contribute`（增量累加、幂等加入）。`claim/delivery/reject` 对 progress 行 → 409；owner `release` 清 delivered + 贡献者重置。

### 10.3 `sheet_row_contributors`（progress 行贡献者，迁移 0007）
| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | bigserial | PK | |
| `row_id` | bigint | FK→sheet_rows.id `ON DELETE CASCADE`, not null | 删行级联清贡献者 |
| `player_uuid` | uuid | FK→players.uuid, not null | 贡献者身份锚（R-5） |
| `joined_at` | timestamptz | not null default now() | 首次上交时间（贡献者排序依据） |

约束：`UNIQUE(row_id, player_uuid)`（同一玩家对同一行只一条记录，配合 `INSERT ... ON CONFLICT DO NOTHING` 幂等加入）。仅 progress（mode=1）行写入；lock 行不用本表。

> **权限（RBAC，后端为准）**：JWT 已登录可读所有表；表的 `owner_uuid` 或 admin/owner 角色可写；CSV 全量导出 `GET /sheets/export` 走 service token（外部读取硬约束，MVP §4）。
> **数量换算 `format_qty`**（个/组/盒，STACK=64/SHULKER=1728）是显示层纯函数，不入库、不进 API 响应（前端 `utils/qty.ts` 与后端 `core/qty.py` 对齐）。
