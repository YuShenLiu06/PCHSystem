# 服务文档：wiki-service（wiki.js 经 git 仓双向同步）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5 / §7.4
> **数据模型**：[`../data-model.md`](../data-model.md) §6（`wiki` schema）、§10.4（归档产物结构）
> **根红线 R-8（重写）**：wiki.js **经独立 wiki 内容 git 仓双向同步**（非 GraphQL 单向）；wiki 是人类可读可编辑的投影，**绝不回写** `sheets` / `score_ledger` 等业务表（R-1 不变）；wiki git 仓 = wiki 内容权威源。

---

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 归档产物（`index.md` + `contributions.png`）提交推送到 wiki 内容 git 仓（**publisher**，默认 off，best-effort） | 业务数据持久化（在后端 PG，R-1 独占） |
| wiki.js 与 git 远端的双向同步（wiki.js 原生能力，独立部署） | 积分计算（交 scoring-service） |
| 拥有者编辑权限模型（host git 分支保护 + PR + wiki.js Page Rules，**本期仅设计不实现**） | 称号判定（交 title-service） |
| 推送失败通知（`notify(category="wiki_publish_failed")`，不回滚 DB） | wiki.js 自身的部署运维（独立部署、不入本仓 compose） |

**定位**：归档 markdown 的「发布渠道」之一。后端 `archive` 服务渲染并原子落盘后，本服务的 publisher **best-effort** 把整目录推到独立 wiki 内容 git 仓；wiki.js 与该远端双向同步渲染。**业务库（PG）才是权威源**，wiki 是投影。

**与原 GraphQL 方案的关系（R-8 重写）**：早期架构设想的「后端 → wiki.js GraphQL 单向同步」**已废弃**。新方案以 git 仓为 wiki 内容权威源，支持双向回流 + PR 审查 + 人类编辑。原 GraphQL 客户端代码（`pages.*` / `groups.*` / `users.*` CRUD）**不在 publisher 路径上**；wiki.js 侧 Page Rules 仅在需要更细粒度的编辑权限时按需补充（见 §4）。

---

## 2. 对外接口

> publisher 是 `archive` 服务的下游消费者，**不暴露独立 REST 端点**（与原设计的 `/wiki/*` 路由表不同）。归档落盘后由 `archive_sheet()` 内部触发推送。

| 接口 | 调用方 | 说明 |
|---|---|---|
| `wiki_service.publisher.publish(sheet_id)`（内部函数） | archive-service | 把 `ARCHIVE_ROOT/projects/<id>/` 整目录 `git add + commit + push` 到配置的 wiki 内容 git 仓。默认 off（`WIKI_GIT_REMOTE_URL` 为空时 no-op）；失败仅 `notify(category="wiki_publish_failed")`，不抛、不回滚 DB |

> 未来的「同步日志/失败重试」面板（`wiki_sync_log`，见 [`data-model.md`](../data-model.md) §6）暂未落地；当前失败只走通知，运营靠 `wiki_publish_failed` 通知人工排查/重推（git 仓提交幂等，重推不会产生重复 commit）。

---

## 3. 内部实现要点

### 3.1 git publisher（subprocess git，token 内嵌 push URL，不落盘）

```python
import subprocess

def publish(sheet_id: int) -> None:
    if not settings.wiki_git_remote_url:        # 空 remote = 不推送（默认 off）
        return
    project_dir = settings.archive_root / "projects" / str(sheet_id)
    # 1) clone-or-pull 远端到临时工作区（或维护本地镜像）
    # 2) 把 project_dir 覆盖到工作区 projects/<id>/
    # 3) git add projects/<id> && git commit -m "archive(sheet=<id>)"
    # 4) push：token 内嵌进 push URL，绝不写入 .git/config
    push_url = settings.wiki_git_remote_url.replace(
        "https://", f"https://x-access-token:{settings.wiki_git_token}@"
    )
    subprocess.run(["git", "push", push_url, settings.wiki_git_branch], check=True, timeout=60)
```

- **配置**（`app/core/config.py`）：`WIKI_GIT_REMOTE_URL` / `WIKI_GIT_BRANCH` / `WIKI_GIT_TOKEN` / `WIKI_GIT_AUTHOR_NAME` / `WIKI_GIT_AUTHOR_EMAIL`。
- **token 不落盘**：以内嵌形式传给单次 `git push` 命令，**不**写入 `.git/config`（避免凭据持久化泄漏）。
- **best-effort**：`publish` 抛异常时 archive 主链路已 commit 完成，仅 catch 后 `notify(category="wiki_publish_failed", payload={sheet_id, error})`，**不回滚 DB**（业务库是权威源，wiki 滞后可重推）。
- **幂等**：git 仓对同一 sheet_id 的目录覆盖提交，重推不会产生重复内容（diff 为空则无新 commit）。

### 3.2 归档产物结构（publisher 推送的内容）

每项目独立文件夹，publisher 推送整目录：

```
projects/
└── <sheet_id>/
    ├── index.md            # 归档正文（markdown，wiki.js 直接渲染）
    └── contributions.png   # matplotlib 贡献占比饼图（PNG，CJK 字体 Noto Sans CJK SC）
```

`index.md` section 结构（去逐行材料清单）：

- `# 📦 项目归档：{title}`
- `## 🏆 贡献者统计`：`aggregate_contributor_totals` 用 union_all 把 lock 行 `delivered_qty`（按 claimant）+ progress 行 `contributed_qty`（按贡献者）合并按人聚合，`HAVING SUM > 0` 剔除零和，输出精确排行
- `## 📊 贡献占比`：`![贡献占比](contributions.png)`（同目录引用）
- `## 📅 时间线`：收集/施工/归档时间戳
- footer：`由 PCHSystem 自动生成`

> `DB.archived_path` 存相对 `ARCHIVE_ROOT` 的 POSIX 路径（`projects/<id>/index.md`），是 publisher 的推送入口与 wiki-service 同步入口。详见 [`data-model.md`](../data-model.md) §10.4。

### 3.3 资产读取（后端 asset 端点，非本服务）

`GET /sheets/{sheet_id}/archive/assets/{filename}` 返 `image/png`（basename 白名单 + 路径穿越守卫 → 非法/缺失 404；鉴权 `get_current_player`，任意登录玩家可读）。该端点读 `ARCHIVE_ROOT/projects/<id>/<filename>`，**与 publisher 解耦**——即使 publisher off，玩家经 Web 也能读到归档图。详见 [`api/sheets.md`](../api/sheets.md) §5.2。

---

## 4. wiki.js 双向同步与编辑权限模型

### 4.1 双向同步（wiki.js 原生 git 集成）

wiki.js 内建 git 集成：与配置的 wiki 内容 git 远端定时拉取/推送，本地编辑产生 commit 推回远端，远端变更拉回渲染。**后端 publisher 只负责把归档推到远端**，wiki.js 自动拉取——三方（后端 / wiki.js / 人类编辑）都收敛到 git 远端这一个权威源。

### 4.2 host 选型权衡（未决）

wiki 内容 git 仓的 host 由部署方决定，**文档记为可配置远端，不替用户拍**：

| Host | 分支保护 + PR | wiki.js 同步稳定性 | 备注 |
|---|---|---|---|
| **GitHub** | ✅ 完整 PR 流程 | ✅ 稳定 | 但 **GitHub Wiki 无 PR**（必须用独立 repo，不能用 repo 自带 wiki） |
| **Gitea**（自托管） | ✅ 分支保护 + PR | ⚠️ 与 wiki.js 偶发不稳 | 需在 wiki.js 用 **「Purge Local Repository」** 强制重拉解决同步滞后 |
| **GitLab** | ✅ MR + 分支保护 | ✅ 稳定 | 体验接近 GitHub |

> 已知坑：**Gitea ↔ wiki.js 同步偶发不稳**（远端已更新但 wiki.js 渲染过期）。缓解：wiki.js 管理面板 `Storage → Git → Purge Local Repository` 强制重新克隆。GitHub/GitLab 部署无此问题。

### 4.3 拥有者编辑权限模型（**本期仅设计，不实现**）

设计目标：项目拥有者在 wiki.js 上可编辑自己项目的归档页（`/projects/<id>`），他人只读；改动经 git 回流、支持 host 层 PR 审查。

落地方案（二选一或叠加，**未决、依赖 wiki.js 部署**）：

- **host 层（推荐主路径）**：wiki 内容 git 仓对 `projects/<owner>` 路径设 CODEOWNERS / 分支保护规则，拥有者的编辑走 PR 合并；后端 publisher 用独立机器账号推送（绕过分支保护或推到 feature 分支）。
- **wiki.js 侧（补充）**：`groups.update(pageRules)`，`match:"START"` + `path:"/projects/<id>"` + `roles:["write:pages"]` 精确授权拥有者所在组（原 GraphQL Page Rules，按需启用）。

> **关键风险（Page Rules 优先级绕）**：wiki.js 权限是「全局规则 + 组规则」叠加，Deny 优先。若启用 wiki.js 侧 Page Rules，**必须先开全局 read，再用组规则精确授权 write**，否则组规则可能被全局默认覆盖。
>
> **待确认**：拥有者 wiki 编辑权限自动授予依赖 wiki.js 部署 + Web↔wiki.js 账号身份匹配（OIDC/SSO 或后端 `users.create` 建号映射）。**本期不实现**，仅文档记录。

---

## 5. 依赖的其他服务

- 被 **archive-service**（`archive_sheet()` 内部）调用触发推送；归档落盘是推送的前置。
- 依赖 **notification-service** 报告推送失败（`category="wiki_publish_failed"`）。
- 外部 **wiki 内容 git 仓**（host 选型未决，§4.2）+ **wiki.js 实例**（独立部署）。

---

## 6. 所属数据表

`wiki` schema（见 [`data-model.md`](../data-model.md) §6，**规划中、未落地**）：
- `wiki_sync_log`（同步日志/幂等表，`entity_type / entity_id / wiki_page_id / action / status / payload / error`）—— 当前 publisher 失败只走通知，本表预留给未来的同步面板/失败重试。

> publisher 本身**不写业务表**：推送是否成功只影响 wiki 投影的新鲜度，不影响 `sheets.archived_*` 字段（archived 三字段在 `archive_sheet()` 内部 commit 时已落库，与 publisher 解耦）。

---

## 7. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| publisher 推送失败 | wiki 投影滞后于业务库 | best-effort：失败仅 `notify(wiki_publish_failed)`，不回滚 DB；git 提交幂等可重推；业务库完整（R-1） |
| token 落盘泄漏 | 写入 `.git/config` 致凭据持久化 | token 内嵌单次 push URL，**不**写入 git 配置；专用 token + 最小权限（仅 wiki 内容仓 write） |
| Gitea↔wiki.js 同步不稳 | wiki 页面渲染过期 | host 选型避开 Gitea，或部署后用「Purge Local Repository」强制重拉 |
| Page Rules 优先级绕 | 拥有者授权不准 | 主路径靠 host git 分支保护 + PR；wiki.js 侧 Page Rules 按需启用 + 全局先开 read + Deny/Allow 测试 |
| GitHub Wiki 无 PR | 编辑无审查 | 用独立 repo（非 repo 自带 wiki）作为 wiki.js 远端 |
| 双向同步冲突 | 人类编辑与 publisher 同时改 | git 三方合并；publisher 覆盖整 `projects/<id>/` 目录，人类编辑若在同目录需走 PR 协调 |

> **业务库不回写（R-1 强调）**：wiki 是人类可读可编辑的**投影**。拥有者在 wiki.js 的编辑、git PR 的改动**只回流到 wiki 内容 git 仓**，**绝不写回** `sheets` / `sheet_rows` / `score_ledger` 等业务表。业务表以 PostgreSQL 为唯一权威源。
>
> 待确认：① wiki 内容 git 仓 host 选型（GitHub/Gitea/GitLab）；② 拥有者 wiki 编辑权限自动授予的实现路径（OIDC/SSO 或后端建号映射，本期仅设计不实现）；③ 是否启用 wiki.js 侧 Page Rules 补充 host 分支保护。
