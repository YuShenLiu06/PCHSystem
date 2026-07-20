# HTCMC PCHSystem · 前端

> PCHSystem 的 Web 管理后台。Vue 3 + Element Plus + Vite + Pinia。
>
> ⚠️ 非独立可运行——强依赖后端 FastAPI 服务。完整架构见 [`../Docs/architecture/frontend.md`](../Docs/architecture/frontend.md)，项目总览见 [`../README.md`](../README.md)。

## 功能（已实现）

- 身份管理：密码登录 / 临时账号转正 / Web 账号绑多 MC 身份（双向短码）/ 自助账号页
- 项目协作：在线表格（认领·交付·贡献·进度）+ 协管员角色 + 三层 RBAC（owner/manager/user）
- 投影蓝图建表：上传 `.litematic` / Create `.nbt` 蓝图解析批量建表
- 项目三阶段：收集 → 施工 → 归档（Markdown 持久化）

## 开发

```bash
npm install
npm run dev       # Vite dev server（默认 5173）
npm run build     # 类型检查 + 生产构建
npm run preview   # 预览构建产物
npm run test      # vitest 单元测试
```

环境变量见 [`../.env.example`](../.env.example)（`VITE_API_BASE_URL` 等）。

## 部署

容器化方案（推荐）：根目录 `docker compose --profile web up -d` 构建 nginx 托管镜像，详见 [`../Docs/architecture/frontend.md`](../Docs/architecture/frontend.md) §5。

## 相关文档

- 前端架构：[`../Docs/architecture/frontend.md`](../Docs/architecture/frontend.md)
- sheets API（含 MCDR 命令映射 + 权限矩阵）：[`../Docs/architecture/api/sheets.md`](../Docs/architecture/api/sheets.md)
- 项目总览 / 快速开始：[`../README.md`](../README.md)
- 贡献规范：[`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- 变更日志：[`../CHANGELOG.md`](../CHANGELOG.md)
