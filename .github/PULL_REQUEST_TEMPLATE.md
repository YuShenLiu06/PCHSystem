## 变更说明

<!-- 本 PR 做了什么、为什么。遵循「一个 PR 只做一件事」（CONTRIBUTING §3） -->

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
- [ ] **CI 通过**：PR 的 Checks 三端 job（backend/frontend/mcdr）全绿，PR 带有 `ci:pass` 标签（`ci.yml` 自动跑；失败时点失败 job 看 step 日志里的 `文件:行号`）
- [ ] 不违反根 [CLAUDE.md](../CLAUDE.md) §3 红线 **R-1 ~ R-12**
- [ ] **涉及 MCDR 的改动已联网核实 API**（根 CLAUDE.md §0 S-1，附文档 URL）
- [ ] 无硬编码密钥（R-11）；新增配置项已同步 `.env.example`
- [ ] 已更新相关文档 / `CHANGELOG.md`（`[Unreleased]` 段）

> 本地预跑（可选，加速反馈）：前端 `npm run build && npm run test:run`；后端起 PG 后 `pytest`；mcdr `PYTHONPATH=McdrPlugin pytest McdrPlugin/tests -q`。

## 测试

<!-- 跑了哪些测试、如何手动验证；新增 / 修改的测试覆盖范围 -->

## 补充

<!-- 截图、迁移编号、BREAKING CHANGE、待办 TODO 等 -->
