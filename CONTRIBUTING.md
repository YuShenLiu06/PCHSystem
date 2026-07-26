# 贡献与发布规范（CONTRIBUTING）

> HTCMC PCHSystem · 三端 monorepo（McdrPlugin / Backend / Frontend）
> 参考 MCDR 生态标准 + Conventional Commits + SemVer
> 红线 R-1~R-12 见根 [`CLAUDE.md`](./CLAUDE.md) §3，**任何改动不得违反**

## 目录

1. [分支模型](#1-分支模型)
2. [Commit 规范](#2-commit-规范)
3. [PR 规范](#3-pr-规范)
4. [版本与 Release](#4-版本与-release)
5. [MCDR 插件特有要求](#5-mcdr-插件特有要求)
6. [参考来源](#6-参考来源)

---

## 1. 分支模型

| 分支 | 用途 |
|---|---|
| `main` | 稳定主干，只接受 PR 合入、**不直接 push** |
| `feat/<scope>-<简述>` | 新功能 |
| `bugfix/<scope>-<简述>` | 缺陷修复 |
| `release/<component>-vX.Y.Z` | 发布预备（可选） |
| `hotfix/<component>-vX.Y.Z` | 线上紧急修复（可选） |

- `<scope>` ∈ `mcdr / backend / frontend / docs / wiki / chore`
- **一个 PR 一件事**：跨组件/多功能拆成多个 PR；建议先 Issue 再 PR

---

## 2. Commit 规范

```
<type>(<scope>): <简述>

<可选正文：为什么、做了什么>
<可选 footer：BREAKING CHANGE / Closes #n>
```

- **type**：`feat / fix / refactor / perf / docs / test / chore / ci / style / build`
- **scope**：`mcdr / backend / frontend / docs / wiki / chore`
- **简述**：简体中文、祈使语气、≤50 字
- 不兼容变更：type 后加 `!` 或 footer `BREAKING CHANGE:` → MAJOR

```
feat(scoring): 实现材料提交结算事务链路
fix(mcdr): 修复 RCON 超时后误清箱
refactor(backend)!: 重命名 players 主键字段
```

---

## 3. PR 规范

- 标题同 commit 格式；**一个 PR 一件事**；关联 Issue（先 Issue 再 PR）
- 合入前自检：
  - [ ] **CI 通过**（[`ci.yml`](./.github/workflows/ci.yml) 自动跑三端，PR 显 `ci:pass` / `ci:fail` 标签）
  - [ ] 不违反红线 R-1~R-12；MCDR 改动已联网核实（S-1）
  - [ ] 无硬编码密钥（R-11）；新配置项已同步 `.env.example`
  - [ ] 文档 / `CHANGELOG.md` 已更新（`[Unreleased]` 段）

**CI 怎么用**：PR `Checks` tab 看每个 job（backend / frontend / mcdr）红绿；失败 → 点进 job → 红色 step 日志含 `文件:行号`。重跑：PR `Checks`→`Re-run`（刷新标签），或 `Actions`→`CI`→`Run workflow`（无 PR 上下文，仅 debug）。backend 已知 flakiness 会自动 `pytest --lf` 重跑失败项。

**本地预跑**（命令同 CI）：前端 `cd Frontend && npm run build && npm run test:run`；后端起 PG 后 `cd Backend && alembic upgrade head && pytest`（须 `export MCDR_SERVICE_TOKEN=... JWT_SECRET=... POSTGRES_PASSWORD=...`）；mcdr `PYTHONPATH=McdrPlugin pytest McdrPlugin/tests -q`。

---

## 4. 版本与 Release

三端独立 SemVer、各自 tag：

| 组件 | tag | 版本来源 |
|---|---|---|
| McdrPlugin | `pch_system-vX.Y.Z` | [`mcdreforged.plugin.json`](./McdrPlugin/mcdreforged.plugin.json) `version` |
| Backend | `backend-vX.Y.Z` | `Backend/pyproject.toml` |
| Frontend | `frontend-vX.Y.Z` | `Frontend/package.json` `version` |

SemVer：BREAKING→MAJOR、兼容新功能→MINOR、兼容修复→PATCH；预发布后缀 `-rc.N`（[semver.org](https://semver.org/)）。

**MCDR 发版（tag 驱动，自动化）**：
1. 改 `plugin.json` `version` + `CHANGELOG.md` 固化 `## [pch_system-vX.Y.Z] - YYYY-MM-DD` 段
2. `git tag pch_system-vX.Y.Z && git push origin pch_system-vX.Y.Z`
3. [`release.yml`](./.github/workflows/release.yml) 自动：校验 tag（含 `version==tag`）→ 三端检测 → `mcdreforged pack` 构建 `.mcdr` → 创建**草稿 Release**（含 `.mcdr` + `SHA256.txt` + CHANGELOG notes）
4. 所有者完善 notes、检验 `.mcdr`、手动 **Publish**

**Backend / Frontend（手工）**：改版本号文件 + CHANGELOG 段 → `git tag` 并推 → Releases 页手工创建（部署从源码 compose build，无二进制 asset）。

> **版本门禁**：release 意图 PR（命中任一：`release/**` / `hotfix/**` 分支、标题含 `release`、`release` label、改了**任一** version 字段（`plugin.json` / `package.json` / `pyproject.toml`）、commit 被任一 release tag（`pch_system-v*` / `backend-v*` / `frontend-v*`）指向）→ [`ci.yml`](./.github/workflows/ci.yml) 的 `version-metadata` job 自动打 `release` 标签，并要求**至少 bump 了一个 version 字段**（否则 block）；其中 **MCDR（`plugin.json`）发版**额外校验 SemVer 合法 + 严格前进 + CHANGELOG `## [pch_system-vX.Y.Z]` 段，失败则 block 合并。backend / frontend 手动发版仅打标签、不强制校验。直接进 main 的 MCDR 发版由 `release.yml` `version==tag` 兜底。脚本 [`check_version_bump.py`](./.github/scripts/check_version_bump.py)。
>
> **破坏性变更**：会话失效 / 数据迁移 / API 契约变更的版本，CHANGELOG 段开头加粗标注，例 `**破坏性变更：所有玩家需重新 !!PCH login 登录。**`。

---

## 5. MCDR 插件特有要求

严守 [`mcdreforged.plugin.json`](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html) 元数据：

- **版本** SemVer；**插件 ID** 小写 + 数字 + 下划线（1–64 字符），**发布后不再改**、全处一致
- **依赖** `dependencies.mcdreforged` 用约束运算符（`>=` / `^` / `~`）声明最低版本
- **打包** MCDR CLI 生成 `.mcdr`（`archive_name` / `resources` 在 metadata 声明）
- **Catalogue** tag 需符合[解析规则](https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_catalogue.html#release)

---

## 6. 参考来源

- [MCDR 元数据规范](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html)
- [MCDR PluginCatalogue CONTRIBUTING](https://github.com/MCDReforged/PluginCatalogue/blob/master/CONTRIBUTING.md)
- [Conventional Commits](https://www.conventionalcommits.org/) · [SemVer](https://semver.org/)

---

*最后更新：2026-07-25*
