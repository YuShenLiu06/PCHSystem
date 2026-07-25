# 贡献与发布规范（CONTRIBUTING）

> HTCMC PCHSystem · 适用于三端 monorepo（McdrPlugin / Backend / Frontend）
> 参考 **MCDR 生态标准** + Conventional Commits + SemVer
> 全局红线 R-1~R-12 见根 [`CLAUDE.md`](./CLAUDE.md) §3，**任何改动不得违反**。

---

## 1. 分支模型

| 分支 | 用途 |
|---|---|
| `main` | 稳定主干，始终可发布；只接受 PR 合入，**不直接 push** |
| `feat/<scope>-<简述>` | 新功能，例 `feat/scoring-submit-flow` |
| `bugfix/<scope>-<简述>` | 缺陷修复，例 `bugfix/mcdr-rcon-timeout` |
| `release/<component>-vX.Y.Z` | 发布预备（可选） |
| `hotfix/<component>-vX.Y.Z` | 线上紧急修复（可选） |

- `<scope>` ∈ `mcdr / backend / frontend / docs / wiki / chore`
- **一个 PR 一件事**（MCDR 标准）：跨组件或多功能拆成多个 PR
- 建议先开 Issue 描述，再提关联 PR

---

## 2. Commit 规范（Conventional Commits）

```
<type>(<scope>): <简述>

<可选正文：为什么、做了什么>
<可选 footer：BREAKING CHANGE、Closes #n>
```

- **type**：`feat / fix / refactor / perf / docs / test / chore / ci / style / build`
- **scope**：`mcdr / backend / frontend / docs / wiki / chore`（标三端或领域）
- **简述**：简体中文，祈使语气，≤50 字

**示例**：
```
feat(scoring): 实现材料提交结算事务链路
fix(mcdr): 修复 RCON 超时后误清箱的时序问题
docs: 补充 user-service 架构文档
refactor(backend)!: 重命名 players 主键字段
```

> type 后的 `!` 或 footer 的 `BREAKING CHANGE:` 表示不兼容变更，触发 MAJOR 版本号。

---

## 3. PR 规范

- PR 标题同 commit 格式
- **一个 PR 一件事**（同作者同字段的多处同类修改可合并）
- 关联 Issue（先 Issue 再 PR）
- 合入前自检：
  - [ ] **CI 通过**：PR 推送后 [`ci.yml`](./.github/workflows/ci.yml) 自动跑三端测试（backend / frontend / mcdr），标签 `ci:pass` / `ci:fail` 直观可见结果（详见下方「CI 与本地预跑」）
  - [ ] 不违反根 CLAUDE.md §3 红线 R-1~R-12
  - [ ] MCDR 相关改动已联网核实 API（根 CLAUDE.md §0 S-1）
  - [ ] 无硬编码密钥（R-11）；新增配置项已同步 `.env.example`
  - [ ] 已更新相关文档 / `CHANGELOG.md`（`[Unreleased]` 段）

### CI 与本地预跑

- **CI 自动检测**（[`.github/workflows/ci.yml`](./.github/workflows/ci.yml)）：PR 的 `opened / synchronize / reopened / ready_for_review` 事件触发，三端 job 并行跑 backend（活 PG 集成测试）/ frontend（`vue-tsc` + `vite build` + `vitest`）/ mcdr（纯单测），第四个 job `label-pr` 据结果给 PR 打互斥标签 `ci:pass`（全绿）/ `ci:fail`（任一失败）。后端已知 flakiness（`conftest._truncate_db` 偶发死锁）会自动 `pytest --lf` 重跑一次失败项，真失败不被掩盖。
- **查看失败位置**：PR 底部 `Checks` tab → 点失败的 job → 展开红色 step（如 `pytest 重试`）→ 输出含 `FAILED tests/xxx.py::test_yyy` + `文件:行号` + 断言信息。**不是**评论回复。
- **手动重跑**：① PR `Checks` tab → `Re-run all jobs` / `Re-run failed jobs`（最常用，带 PR 上下文，会刷新标签）；② `Actions` tab → 选 `CI` → `Run workflow`（无 PR 上下文，`label-pr` 跳过，仅用于 debug 或给 main 跑一次）。
- **本地预跑（可选，加速反馈）**：命令与 env 同 CI——前端 `cd Frontend && npm run build && npm run test:run`；后端先起 PG，`export MCDR_SERVICE_TOKEN=dev JWT_SECRET=dev POSTGRES_PASSWORD=...`，`cd Backend && alembic upgrade head && pytest`；mcdr `PYTHONPATH=McdrPlugin pytest McdrPlugin/tests -q`。

---

## 4. 版本与 Release（各组件独立 SemVer）

三端各自独立版本号、各自打 tag：

| 组件 | tag 约定 | 版本来源 |
|---|---|---|
| McdrPlugin | `pch_system-vX.Y.Z` | [`mcdreforged.plugin.json`](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html) 的 `version` |
| Backend | `backend-vX.Y.Z` | `pyproject.toml` / `VERSION` |
| Frontend | `frontend-vX.Y.Z` | `package.json` 的 `version` |

**SemVer 规则**（[semver.org](https://semver.org/)，MCDR 官方"强烈建议"）：

| 变更类型 | 版本段 | 示例 |
|---|---|---|
| 不兼容的 API / 数据 / 行为变更（BREAKING） | MAJOR | `1.0.0` → `2.0.0` |
| 向后兼容的新功能 | MINOR | `1.0.0` → `1.1.0` |
| 向后兼容的缺陷修复 | PATCH | `1.0.0` → `1.0.1` |
| 预发布 | 后缀 | `1.0.0-alpha` / `1.0.0-rc.1`（MCDR 示例：`1.8.9-rc.8`、`1.2.3-beta.4`） |

**Release 流程**：

**MCDR 插件（`pch_system`，tag 驱动半自动，已自动化）**：
1. 更新 [`mcdreforged.plugin.json`](./McdrPlugin/mcdreforged.plugin.json) 的 `version`，并在 `CHANGELOG.md` 固化 `## [pch_system-vX.Y.Z] - YYYY-MM-DD` 段
2. 打 tag 并推：`git tag pch_system-vX.Y.Z && git push origin pch_system-vX.Y.Z`
3. [`.github/workflows/release.yml`](./.github/workflows/release.yml) 自动跑：校验 tag（动态读 plugin id）→ 三端检测（backend 活 PG 集成测试 / frontend 类型检查+构建+单测 / mcdr 单测）→ `mcdreforged pack` 构建 `.mcdr` → 创建该 tag 的**草稿 Release**（含 `.mcdr` + `SHA256.txt` + 自动从 CHANGELOG 抽取的 notes）
4. 所有者在 Releases 页完善 notes、检验 `.mcdr`，手动 **Publish** → 正式发布（catalogue 此时可探测到）

> **PR CI 是发布前置**：日常 PR 已由 [`ci.yml`](./.github/workflows/ci.yml) 跑过三端检测，`release.yml` 仍重跑一遍作发版前最终守门（命令一致），任一失败则整 job 失败、**不建草稿**（打 tag 后问题立即暴露；修后删 tag 重打重跑）。`-rc` 后缀（如 `pch_system-v1.0.0-rc.1`）自动标记为 pre-release。
>
> **版本元数据门禁**（2026-07-25）：release 意图 PR（命中任一信号即触发：`release/**`/`hotfix/**` 分支、PR 标题含 `release`、带 `release` label、改了 `plugin.json` 的 `version`、或 PR commit 被 `pch_system-v*` tag 指向）由 `ci.yml` 的 `version-metadata` job 校验——`plugin.json.version` 须 SemVer 合法（`X.Y.Z` 或 `X.Y.Z-rc.N`）、**PR 内已 bump**、严格前进于最新 tag、且 `CHANGELOG.md` 有 `## [pch_system-vX.Y.Z]` 段；命中自动打 `release` 标签，失败 → block 合并 + `ci:fail`。普通 PR 五源不命中则跳过、不阻。直接进 main 的发版由 `release.yml` "校验 tag" 步兜底 `version==tag`（唯一守门）。校验逻辑见 [`check_version_bump.py`](./.github/scripts/check_version_bump.py)。

**Backend / Frontend（暂未自动化）**：
1. 更新版本号文件（`Backend/pyproject.toml` / `Frontend/package.json`）+ `CHANGELOG.md` 固化段
2. `git tag backend-vX.Y.Z` / `frontend-vX.Y.Z` 并推
3. 手工在 Releases 页创建 Release、附变更说明（部署从源码 compose build，无需二进制 asset）

> **MCDR tag 改名（2026-07-06）**：`mcdr-vX.Y.Z` → `htcmc_auth-vX.Y.Z`，符合 [MCDR PluginCatalogue](https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_catalogue.html#release) 的合法 tag 格式（`<plugin_id>-<version>`，四选一）。历史 `mcdr-v0.3.0` 前向兼容保留、不重打。背景与决策见 [`Docs/Reports/mcdr-publishing-strategy.md`](./Docs/Reports/mcdr-publishing-strategy.md)。
>
> **MCDR plugin id 改名（2026-07-12）**：plugin id 由 `htcmc_auth` 改为 `pch_system`（与项目名 PCHSystem 一致，且不止 auth——含 sheets/submit/notify + 规划中 score/title），tag 前缀随之 `htcmc_auth-vX.Y.Z` → `pch_system-vX.Y.Z`。MCDR 硬性要求 `mcdreforged.plugin.json` 的 `id` = 文件夹名 = 内部包名（联网核实 catalogue/metadata 文档），故 `McdrPlugin/htcmc_auth/htcmc_auth/` 同步改名 `McdrPlugin/pch_system/pch_system/`，类 `HtcmcAuthConfig → PchSystemConfig`。**已部署实例需迁移**：旧 `plugins/htcmc_auth/` 删除（否则与新 `pch_system` 双注册 `!!PCH` 冲突）、`config/htcmc_auth/` 搬到 `config/pch_system/`（`Scripts/lib/common.sh::migrate_legacy_plugin_name()` 自动处理）。历史 `htcmc_auth-v*` / `mcdr-v*` tag 保留不重打。
>
> **破坏性变更 release notes 指引**：凡涉及会话失效 / 数据迁移 / API 契约变更的版本，CHANGELOG 段开头必须加粗标注，例：
> >
> > **破坏性变更：所有玩家需重新 `!!PCH login` 登录。**
> >
> > 参考 `pch_system-v0.8.0` 段（JWT `sub` 由 `player_uuid` 改 `web_account_id`）。前端 / 后端同理。
> >
> **MCDR tag 发布经验（2026-07-20）**：plugin.json version bump 即可触发 release workflow，无需依赖或元数据变更。

---

## 5. MCDR 插件特有要求

严守 [`mcdreforged.plugin.json`](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html) 元数据：

- **版本**：`version` 字段遵循 SemVer
- **插件 ID**：小写 + 数字 + 下划线，1–64 字符，**发布后不再更改**，且全处一致
- **依赖**：`dependencies.mcdreforged` 用约束运算符（`>=`、`^`、`~` 等）声明最低 MCDR 版本
- **打包**：用 MCDR CLI 生成 `.mcdr`（`archive_name`、`resources` 在 metadata 中声明）
- **若发布到 MCDR PluginCatalogue**：tag 需符合 catalogue 解析规则，详见 [plugin_catalogue#release](https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_catalogue.html#release)

---

## 6. 参考来源

- [MCDR 元数据规范](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html) —— 版本字段、插件 ID、依赖约束运算符
- [MCDR PluginCatalogue CONTRIBUTING](https://github.com/MCDReforged/PluginCatalogue/blob/master/CONTRIBUTING.md) —— 分支模型、一个 PR 一件事、tag release
- [Conventional Commits](https://www.conventionalcommits.org/)
- [Semantic Versioning](https://semver.org/)

---

*最后更新：2026-07-21*
