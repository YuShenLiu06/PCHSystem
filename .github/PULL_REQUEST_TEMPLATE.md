## 变更说明

<!-- 本 PR 做了什么、为什么。一个 PR 一件事（CONTRIBUTING §3）。-->

## 类型（Conventional Commits）

- [ ] `feat` 新功能
- [ ] `fix` 缺陷修复
- [ ] `refactor` 重构
- [ ] `docs` 文档
- [ ] `chore` / `ci` / `test` / `perf`

## 涉及组件

- [ ] 后端　
- [ ] 游戏端（MCDR 插件）　
- [ ] 前端　
- [ ] 文档 / 部署

## 关联 Issue

<!-- Closes #xxx -->

## 自检清单

<!-- 合入前全部勾选 -->
- [ ] **CI 通过**：PR 显 `ci:pass` 标签（`ci.yml` 跑 backend / frontend / mcdr / version-metadata 四 job 全绿；失败 → 点失败 job 看 step 日志里的 `文件:行号`）
- [ ] 不违反根 [CLAUDE.md](../CLAUDE.md) §3 红线 **R-1 ~ R-12**
- [ ] **涉及 MCDR 的改动已联网核实 API**（根 CLAUDE.md §0 S-1，附文档 URL）
- [ ] 无硬编码密钥（R-11）；新增配置项已同步 `.env.example`
- [ ] 文档 / `CHANGELOG.md` 已更新

## 发版（仅 release PR 填）

<!--
命中任一信号 CI 即视为 release PR：release/hotfix 分支、标题含 release、release label、
改了 plugin.json version、commit 被 pch_system-v* tag 指向。version-metadata job 会校验，
命中自动打 release 标签；普通功能/修复 PR 不命中则跳过、无需填本栏。
-->
- [ ] 已 bump `McdrPlugin/mcdreforged.plugin.json` 的 `version`
- [ ] `CHANGELOG.md` 有 `## [pch_system-vX.Y.Z] - YYYY-MM-DD` 段
- [ ] 版本严格前进于最新 `pch_system-v*` tag（非同版本/倒退）

## 测试

<!-- 跑了哪些测试、如何手动验证；新增 / 修改测试的覆盖范围 -->

## 补充

<!-- 截图、迁移编号、BREAKING CHANGE、待办 TODO 等 -->
