# 服务文档：wiki-service（wiki.js 集成）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5 / §7.4
> **数据模型**：[`../data-model.md`](../data-model.md) §6（`wiki` schema）
> **外部依赖**：[wiki.js GraphQL API](https://docs.requarks.io/graphql)

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 项目归档页创建/更新（`pages.*`） | 业务数据持久化（在后端 PG） |
| 名人堂/榜单同步到 wiki | 积分计算（交 scoring-service） |
| wiki 用户组管理（`groups.*`） | Web/MC 身份（交 user-service） |
| Page Rules 编辑权限授权 | 称号判定（交 title-service） |
| wiki 用户创建与映射（`users.create`） | wiki 页面人工编辑内容 |
| 同步日志与幂等重试（`wiki_sync_log`） | |

**定位**：wiki.js 的唯一写入方。后端 → wiki.js **单向同步**，wiki 不回写业务库。所有 wiki 操作经 GraphQL，本地留 `wiki_sync_log` 保证幂等可重试。

## 2. 对外接口（REST，主要供内部服务调用）

| 方法 | 路径 | 调用方 | 说明 |
|---|---|---|---|
| POST | `/wiki/pages` | project/scoring | 创建/更新归档页 |
| POST | `/wiki/groups/assign` | user/title | 授予用户组（编辑权益） |
| POST | `/wiki/groups/revoke` | user/title | 回收用户组 |
| POST | `/wiki/page-rules` | project | 为项目路径授权编辑组 |
| GET | `/wiki/sync-log` | Web(admin) | 同步日志/失败重试入口 |
| POST | `/wiki/sync-log/{id}/retry` | Web(admin) | 手动重试失败同步 |

## 3. 内部实现要点

### 3.1 GraphQL 客户端

```python
import requests
WIKI_GQL = "http://wikijs:3000/graphql"

def gql(query, variables, api_key):
    return requests.post(WIKI_GQL, json={"query": query, "variables": variables},
                         headers={"Authorization": f"Bearer {api_key}"}, timeout=15).json()
```
- 认证：Bearer API Key，需 `manage:system` 级权限（建用户/改组规则）。
- 证据：[wiki.js GraphQL](https://docs.requarks.io/graphql)（`pages.*` / `groups.*` / `users.*`）。

### 3.2 归档页同步（`pages.create/update`）

```graphql
mutation($path:String,$title:String,$content:String){
  pages{
    create(path:$path, title:$title, content:$content, editor:"markdown"){
      response { ... on PageResponse { page { id } } }
    }
  }
}
```
- 项目完结 → project-service 触发 → 本服务用模板渲染归档页（成员/材料/积分分布）。
- 更新用 `pages.update(id, ...)`，幂等靠 `wiki_sync_log(entity_type='project_archive', entity_id)`。

### 3.3 Page Rules 授权（关键复杂点）

为项目 `/projects/<id>` 路径授予负责人编辑组：

```graphql
mutation($gid:Int!,$rules:[PageRuleInput!]!){
  groups{
    update(id:$gid, pageRules:$rules){
      response { ... on GroupResponse { group { id } } }
    }
  }
}
# variables.rules = [{ match:"START", path:"/projects/xxx", roles:["write:pages"], deny:false }]
```
- **`match`**：`START`（路径前缀匹配）/`EXACT`/`TAG`/`TAGS_some` 等。
- **`roles`**：`write:pages`（编辑）/`read:pages` 等。
- **优先级绕（关键风险）**：wiki.js 权限是「全局规则 + 组规则」叠加，Deny 优先。**必须先开全局 read，再用组规则精确授权 write**，否则组规则可能被全局默认覆盖。
- 证据：wiki.js Page Rules（[权限模型](https://docs.requarks.io/groups)）。

### 3.4 用户组与权益联动

```python
def grant_editor(wiki_user_id, project_id):
    gid = ensure_project_group(project_id)        # groups.create 若不存在
    gql("mutation($u:Int!,$g:Int!){groups{assignUser(userId:$u,groupId:$g){...}}}",
        {"u": wiki_user_id, "g": gid}, API_KEY)
```
- 项目立项建专属组 + 授负责人；高阶称号解锁授额外组（与 title-service 联动）。
- 项目归档回收负责人编辑权（`groups.unassignUser` 或删除组规则）。

### 3.5 wiki 用户创建映射

```graphql
mutation($email:String,$pk:String,$name:String,$pw:String){
  users{create(email:$email, providerKey:$pk, name:$name, password:$pw){
    response{...on UserResponse{user{id}}}}}
```
- 与 [user-service](./user-service.md) §3.4 配合：user-service 负责映射关系，本服务负责建号 + 返回 `wiki_user_id`。
- **待确认**：是否接 OIDC/SSO 与 Web 端共享登录态（省去独立建号）。

### 3.6 幂等与重试（`wiki_sync_log`）

```python
def sync(entity_type, entity_id, action, gql_fn, payload):
    log_id = db.execute("INSERT INTO wiki.wiki_sync_log(...) VALUES(..., 'pending', ...) RETURNING id", ...)
    try:
        gql_fn()
        db.execute("UPDATE wiki.wiki_sync_log SET status='done', synced_at=now() WHERE id=:i", i=log_id)
    except Exception as e:
        db.execute("UPDATE wiki.wiki_sync_log SET status='failed', error=:e WHERE id=:i", e=str(e), i=log_id)
```
- 幂等键：`(entity_type, entity_id, action)`，重试前查已 done 则跳过。
- 失败入列，后台 worker / 管理员手动重试。

## 4. 依赖的其他服务

- 被 **project-service**（归档）、**scoring-service**（榜单）、**title-service**（高阶权益）调用。
- 依赖 **user-service** 提供 `wiki_user_id` 映射。
- 外部 **wiki.js 容器**（GraphQL endpoint）。

## 5. 所属数据表

`wiki` schema（见 [`data-model.md`](../data-model.md) §6）：
- `wiki_sync_log`（同步日志/幂等表，`entity_type / entity_id / wiki_page_id / action / status / payload / error`）

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| Page Rules 优先级绕 | 组规则被全局默认覆盖 | 全局先开 read + Deny/Allow 测试用例验证 |
| API Key 权限过大 | 泄漏可改全站 | 专用 Key + 定期轮换 + 仅内网可达 |
| 单向同步不一致 | wiki 改了业务库不知 | wiki 为展示副本，权威以 PG 为准；定期校对 |
| GraphQL schema 版本 | wiki.js 升级致字段变更 | 锁版本 + 集成测试 |
| 大量页面同步性能 | 批量归档慢 | 异步队列 + 限流 |

> 待确认：wiki 登录是否首版就用 API Key 建本地账号，还是接 OIDC/SSO 与 Web 共享会话。
