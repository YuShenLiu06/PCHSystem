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
  - [ ] 构建通过：前端 `cd Frontend && npm run build`（含 `vue-tsc` 类型检查）；后端 `cd Backend && pytest`（无独立 lint，依赖类型 + 测试）
  - [ ] 不违反根 CLAUDE.md §3 红线 R-1~R-12
  - [ ] MCDR 相关改动已联网核实 API（根 CLAUDE.md §0 S-1）

---

## 4. 版本与 Release（各组件独立 SemVer）

三端各自独立版本号、各自打 tag：

| 组件 | tag 约定 | 版本来源 |
|---|---|---|
| McdrPlugin | `mcdr-vX.Y.Z` | [`mcdreforged.plugin.json`](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/metadata.html) 的 `version` |
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
1. 更新对应组件的版本号文件
2. 打 tag：`git tag mcdr-v1.2.0` —— **必须正确打 tag**（MCDR catalogue 靠 tag 取版本）
3. 创建 GitHub Release，附变更说明（关联 PR / Issue）
4. **MCDR 插件额外**：上传 `.mcdr` 打包产物作为 release asset

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

*最后更新：2026-07-02*
