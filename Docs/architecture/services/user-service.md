# 服务文档：user-service（身份核心）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **数据模型**：[`../data-model.md`](../data-model.md) §2（`users` schema）

## 1. 职责边界

| 管 | 不管 |
|---|---|
| MC 玩家身份记录与映射 | UUID 推导本身（在 [`mcdr-plugin.md`](./mcdr-plugin.md) §3.4） |
| Web 账号（**身份主锚**） | 积分/称号计算（交 scoring/title-service） |
| 游戏内绑定 Token 签发与核销（**已落地（迁移 0014/0015）**） | wiki 页面内容（交 wiki-service） |
| wiki.js 账号映射与创建 | 项目/材料（交 project-service） |
| RBAC 权限（user/admin/owner） | 异常判定（交 alert-service） |
| 白名单状态管理 | |
| 离线改名过户运维流程 | |

**定位**：系统的身份核心。所有其他服务通过本服务解析「这个 UUID/JWT 是谁」。**身份锚 = Web 账号**（`web_accounts.id`），MC UUID 为其下的子身份——这是离线改名不丢积分的关键。

## 2. 对外接口（REST API）

### 2.1 双鉴权模型

| 调用方 | 凭证 | 典型用途 |
|---|---|---|
| MCDR 插件 | `X-Service-Token`（共享密钥） | 机器调用：绑定 token、查玩家 |
| Web 前端 | `Authorization: Bearer <JWT>` | 用户调用：后台管理 |

> 待确认：MCDR↔后端的 Service Token 是否按实例独立签发（便于吊销），还是全局共享（运维更简）。

### 2.2 端点清单

| 方法 | 路径 | 鉴权 | 调用方 | 说明 |
|---|---|---|---|---|
| POST | `/auth/login` | 公开 | Web | 用户名密码登录 → 返回 JWT（access + refresh + player + account） |
| POST | `/web-accounts/register` | JWT（临时账号） | Web | 临时账号转永久（设置用户名+密码），换发新 JWT |
| GET | `/web-accounts/me` | JWT | Web | 当前账号 + 绑定 players 列表 |
| PATCH | `/web-accounts/me` | JWT | Web | 更新当前账号 display_name |
| POST | `/bind/token` | service-token | MCDR | 游戏内 `!!PCH bind` → 生成 game_init 短码（6 位 Crockford Base32） |
| POST | `/bind/issue` | JWT（永久账号） | Web | Web 发起绑定 → 生成 web_init 短码 |
| POST | `/bind/confirm` | JWT（永久账号） | Web | 确认 game_init 短码 → 挂接 player_uuid 到当前账号 |
| POST | `/bind/consume` | service-token + X-Player-UUID | MCDR | 游戏内消费 web_init 短码 → 挂接当前 UUID 到目标账号 |
| POST | `/bind/claim` | JWT（临时账号） | Web | 临时账号绑定到已有永久账号（凭用户名+密码） |
| GET | `/players/me` | JWT/service-token+UUID | MCDR/Web | 当前身份玩家信息 |
| GET | `/players/{uuid}` | JWT（admin） | Web | 查玩家详情 |
| PATCH | `/players/{uuid}` | JWT（admin） | Web | 改名过户 / 白名单状态 |

## 3. 内部实现要点

### 3.1 身份锚策略（核心）

```
Web 账号 (web_accounts.id)      ← 身份主锚：积分/称号的真正归属（R-5）
   └── 绑定 1..N 个 MC 玩家 (players.uuid)   ← 子身份：随离线改名而变化
```

- 一个 Web 账号可绑多个 MC 玩家（一人多号），**积分归属按 account 级聚合**（见下文 §3.6）。
- `players.web_account_id`：首次 `!!PCH login` 自动建临时 `web_account` 挂接；玩家可在 Web 端 `register` 转永久或 `bind` 挂接更多 UUID。
- Web 账号分**临时/永久**：`username IS NULL` = 临时账号（自动创建，必须 register 或 claim 转永久）；`username NOT NULL` = 永久账号（密码登录）。

### 3.2 离线改名过户流程

离线模式 `UUID = f(玩家名)`。改名 → 新 UUID → 旧 UUID 的积分/称号需迁移：

```mermaid
sequenceDiagram
    participant P as 玩家(新名)
    participant M as MCDR
    participant U as user-service
    participant W as Web 账号
    P->>M: 用新名入服, !!bind
    M->>U: POST /bind/token(new_uuid, new_name)
    U->>U: 检测 current_name 历史 → 命中旧 uuid
    U-->>W: 引导 Web 端确认过户
    W->>U: PATCH /players/{old_uuid} merge_to={new_uuid}
    U->>U: 跨 schema 事务: 迁移 score_ledger/player_titles/submissions 的 uuid; 旧 uuid 软删
```

- **前提**：玩家必须先绑过 Web 账号，否则无法证明新旧身份同一人。
- **降级**：未绑定就改名 → 视为新人，旧身份积分冻结，运营凭证据手动过户。

### 3.3 绑定 Token 流程（双向）

**game_init 方向**（游戏内发起）：
1. 玩家游戏内 `!!PCH bind` → MCDR 调用 `POST /bind/token`（service-token 单头，body `{uuid, name}`）
2. 后端创建 `bind_tokens` 行（`direction='game_init'`，`player_uuid`，`expires_at=now()+TTL`）→ 返回 6 位短码（Crockford Base32，剔除易混字符 0/O/1/I/L/U）
3. 玩家 Web 端输入短码 → `POST /bind/confirm`（JWT）消费 → 挂接 `player_uuid` 到当前账号

**web_init 方向**（Web 发起）：
1. Web 端 `POST /bind/issue`（JWT）→ 后端创建 `bind_tokens` 行（`direction='web_init'`，`target_account_id`）→ 返回短码
2. 玩家游戏内 `!!PCH bind <code>` → MCDR 调用 `POST /bind/consume`（service-token + `X-Player-UUID` 双头）
3. 后端消费 web_init 短码 → 挂接当前 UUID 到目标账号

**bind_tokens 表结构**：
- `token`：UUID 主键
- `short_code`：6 位短码，UNIQUE
- `direction`：`'game_init'` | `'web_init'`
- `player_uuid`：game_init 方向的玩家 UUID
- `target_account_id`：web_init 方向的目标账号 ID
- `expires_at`：过期时间（配置 `bind_token_ttl_seconds`）
- `used_at`：消费时间（NULL = 未消费）
- 方向一致性 CHECK：`(direction='game_init' AND player_uuid IS NOT NULL AND target_account_id IS NULL) OR (direction='web_init' AND target_account_id IS NOT NULL AND player_uuid IS NULL)`
- 部分索引：`ix_bind_tokens_active WHERE used_at IS NULL`（提升查询性能）

**临时账号挂接永久账号**（`POST /bind/claim`）：
- 临时会话（JWT）输入永久账号用户名+密码 → 校验通过 → 将临时账号名下所有 player 迁移到目标永久账号 → 返回目标账号 JWT

### 3.4 wiki.js 账号映射

玩家绑定 Web 账号后，lazy 在 wiki.js 建用户并映射：

```graphql
mutation {
  users {
    createUser(
      email: "<web_username>@placeholder.local"
      providerKey: "local"
      name: "<mc_name>"
      password: "<random>"
    ) { response { ... on UserResponse { user { id } } } }
  }
}
```
- 映射存 `web_accounts.wiki_user_id`（建议扩展字段）。
- 首次访问建号，不绑定不建。
- 证据：wiki.js GraphQL `users.create(providerKey:"local")`（[wiki.js GraphQL API](https://docs.requarks.io/graphql)）。

> 待确认：wiki 账号「一 Web 账号一号」还是「一玩家一号」。建议按 Web 账号（与身份锚一致）。

### 3.5 RBAC 权限中间件

```python
def require_role(*roles):
    def dep(user = Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403)
        return user
    return dep

@app.patch("/players/{uuid}")
async def update_player(uuid, body, _=Depends(require_role("admin", "owner"))):
    ...
```
三级：`user` / `admin`（运维）/ `owner`（超管）。FastAPI 依赖注入统一鉴权。

### 3.6 JWT sub=web_account_id（**破坏性变更**）

**JWT 签名变更**：
- `sub` 由 `player_uuid`（UUID）改为 `web_account_id`（bigint）
- 新增 `active_uuid` claim：会话来源 UUID（`/auth/login` 取首个绑定 player，`/auth/exchange` 取兑换玩家）
- **破坏性**：现有 JWT 全部失效，所有玩家需重新登录

**RBAC 权威源迁移**：
- `role` 权威源由 `player.role` 迁移到 `account.role`（account 级）
- `require_role` 重构：从 `player.role` 改为 `_resolve_role(player, account)`（未绑玩家回退 `player.role`）

**聚合查询影响**（按 account 级聚合）：
- `sheet_repo.list_sheets` 参与优先排序：`JOIN players → GROUP BY web_account_id`（NULL 用 `COALESCE(web_account_id::text, uuid::text)` 回退）
- `notification_repo.pending`：同上
- `aggregate_contributor_totals` 归档排行：同上，`min(uuid)` 统一 cast text 规避 PostgreSQL 限制

**积分归属落地**：
- 已实现：聚合查询按 `web_account_id` 分组
- 待建表：`score_ledger` 直接加 `owner_account_id` 列（不再待确认）

## 4. 依赖的其他服务

- **被所有服务依赖**（身份解析入口）。
- 调用 **wiki-service**（或直接 GraphQL）建 wiki 用户、取 `wiki_user_id`。
- 改名过户触发跨 schema 事务，触及 **scoring-service**（`score_ledger`）、**title-service**（`player_titles`）的 uuid 列。

## 5. 所属数据表

`users` schema（见 [`data-model.md`](../data-model.md) §2）：
- `players`（玩家，PK=`uuid`，含 `web_account_id bigint NULL FK→web_accounts.id ON DELETE SET NULL`）
- `web_accounts`（Web 账号，身份主锚；列：id/username UNIQUE NULL/password_hash NULL/role/wiki_user_id/display_name/created_at/last_active_at；CHECK：`(username IS NULL) = (password_hash IS NULL)` → NULL = 临时账号；display_name CHECK 非空串）
- `bind_tokens`（绑定令牌；列：token PK/short_code UNIQUE/direction('game_init'|'web_init')/player_uuid NULL/target_account_id NULL/expires_at/used_at/created_at；方向一致性 CHECK；部分索引 `ix_bind_tokens_active WHERE used_at IS NULL`）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| 离线改名丢身份 | UUID 变化致积分丢失 | Web 账号锚 + §3.2 过户流程 |
| Token 重放 | 一次性 token 被复用 | `used_at` + 短有效期 + 唯一性约束 |
| wiki 账号映射失同步 | `wiki_user_id` 与 wiki.js 不一致 | 建号与绑定同事务 + `wiki_sync_log` 记录 |
| JWT 密钥管理 | 泄漏可伪造身份 | 环境变量注入 + 定期轮换 |
| JWT 破坏性变更通知 | v0.8.0 JWT sub 改 `web_account_id` 致现有会话全失效 | 发布前公告 + 强制重登引导 |

> 待确认：Web 注册首版自建（local），还是直接接 OIDC/SSO 与 wiki.js 共享登录态。

---

## 7. 增量日志

**2026-07-19（v0.8.0，身份主锚升级 R-5 落地）**：
- 新表 `web_accounts`（id/username/password_hash/role/wiki_user_id/display_name/timestamps；CHECK：NULL=临时账号）+ `bind_tokens`（双向短码；direction/game_init/web_init 一致性 CHECK；部分索引）
- `players.web_account_id` 外键（首次 `!!PCH login` 自动建临时账号挂接）
- 6 条新端点：`POST /auth/login`（密码登录）+ `POST /web-accounts/register`（临时→永久）+ `GET /web-accounts/me` + `POST /bind/token`（game_init）+ `POST /bind/issue`（web_init）+ `POST /bind/confirm` + `POST /bind/consume` + `POST /bind/claim`（临时挂接永久）
- JWT `sub` 由 `player_uuid` 改 `web_account_id`（**破坏性变更**，现有会话全失效需重登）
- `role` 权威源迁 account 级（未绑玩家回退 `player.role`）
- 聚合查询按 `web_account_id` 分组（`sheet_repo.list_sheets` 参与优先 / `notification_repo.pending` / `aggregate_contributor_totals` 归档排行）
- 积分归属已落地：`score_ledger` 待建时直接加 `owner_account_id`（不再待确认）
- bcrypt 哈希密码（SHA256 预哈希规避 72 字节限制）+ 6 位 Crockford Base32 短码（剔除易混字符 0/O/1/I/L/U）

---

*最后更新：2026-07-21*
