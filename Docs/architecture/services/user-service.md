# 服务文档：user-service（身份核心）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **数据模型**：[`../data-model.md`](../data-model.md) §2（`users` schema）

## 1. 职责边界

| 管 | 不管 |
|---|---|
| MC 玩家身份记录与映射 | UUID 推导本身（在 [`mcdr-plugin.md`](./mcdr-plugin.md) §3.4） |
| Web 账号（**身份主锚**） | 积分/称号计算（交 scoring/title-service） |
| 游戏内绑定 Token 签发与核销 | wiki 页面内容（交 wiki-service） |
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

| 方法 | 路径 | 调用方 | 说明 |
|---|---|---|---|
| POST | `/bind/token` | MCDR | `!!bind` 申请绑定 token，返回短码 |
| POST | `/bind/confirm` | Web | token + Web 账号完成绑定 |
| GET | `/players/me` | MCDR/Web | 当前身份玩家信息 |
| GET | `/players/{uuid}` | Web(admin) | 查玩家详情 |
| PATCH | `/players/{uuid}` | Web(admin) | 改名过户 / 白名单状态 |
| GET | `/web-accounts/me` | Web | 当前账号 + 绑定玩家列表 |
| POST | `/auth/login` | Web(open) | 登录 → 签 JWT |
| POST | `/auth/refresh` | Web | 刷新 token |

## 3. 内部实现要点

### 3.1 身份锚策略（核心）

```
Web 账号 (web_accounts.id)      ← 身份主锚：积分/称号的真正归属
   └── 绑定 1..N 个 MC 玩家 (players.uuid)   ← 子身份：随离线改名而变化
```

- 一个 Web 账号可绑多个 MC 玩家（一人多号），但**积分归属需明确**——见下文「积分归属粒度」待确认项。
- 未绑定时 `players.web_account_id` 为空，玩家仍可游戏内提交（按 uuid 记账），但**无法过户**。

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

### 3.3 绑定 Token 流程

```python
# POST /bind/token
def issue_bind_token(player_uuid: UUID, name: str) -> str:
    token = uuid4()
    db.execute("""
        INSERT INTO users.bind_tokens(token, player_uuid, expires_at)
        VALUES (:token, :uuid, now() + interval '10 min')
    """, token=token, uuid=player_uuid)
    return short_code(token)          # 6 位短码回显给玩家
```
- **一次性**：`/bind/confirm` 成功写 `used_at`，重复使用拒绝。
- **短有效期**：10 分钟，过期清理任务回收。

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

## 4. 依赖的其他服务

- **被所有服务依赖**（身份解析入口）。
- 调用 **wiki-service**（或直接 GraphQL）建 wiki 用户、取 `wiki_user_id`。
- 改名过户触发跨 schema 事务，触及 **scoring-service**（`score_ledger`）、**title-service**（`player_titles`）的 uuid 列。

## 5. 所属数据表

`users` schema（见 [`data-model.md`](../data-model.md) §2）：
- `players`（玩家，PK=`uuid`）
- `web_accounts`（Web 账号，身份主锚；建议扩 `wiki_user_id int null`）
- `bind_tokens`（绑定令牌）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| 离线改名丢身份 | UUID 变化致积分丢失 | Web 账号锚 + §3.2 过户流程 |
| **积分归属粒度** | `score_ledger` 按 uuid 还是按 account 记 | **待确认**：建议加 `owner_account_id` 冗余，过户免迁移 |
| Token 重放 | 一次性 token 被复用 | `used_at` + 短有效期 + 唯一性 |
| wiki 账号映射失同步 | `wiki_user_id` 与 wiki.js 不一致 | 建号与绑定同事务 + `wiki_sync_log` 记录 |
| JWT 密钥管理 | 泄漏可伪造身份 | 环境变量注入 + 定期轮换 |

> 待确认：Web 注册首版自建（local），还是直接接 OIDC/SSO 与 wiki.js 共享登录态。
