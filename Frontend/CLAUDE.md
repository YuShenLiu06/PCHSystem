# 前端 Web 后台 · 子服务 CLAUDE.md

> 本文件由 `service-claude-md` skill 生成 / 维护，**禁止手写**。
> 全局统一规范见根 [`CLAUDE.md`](../CLAUDE.md)；本服务完整架构见架构文档。

---

## 1. 服务定位

Vue 3 + Element Plus 后台，三端架构中的网页端：管理员/负责人运营管理（玩家、项目、积分、称号、告警、系统设置）；普通玩家主要走游戏内 `!!` 命令，仅在绑定/兑换/查看身份等少数流程进 Web。

> 完整架构：[`Docs/architecture/frontend.md`](../Docs/architecture/frontend.md)

---

## 2. 职责边界

| 管 | 不管 |
|---|---|
| 调后端 REST API 展示数据、发操作指令 | 业务逻辑（一律在后端） |
| JWT 持有与注入（axios 拦截器） | 权限判定（后端 RBAC 为准，前端只控可见性） |
| 路由守卫（未登录跳 `/auth`、`/login`） | 玩家游戏内交互（MCDR 管） |
| `.litematic` 等文件上传 UI | 文件解析（project-service 管） |
| 一次性 token → JWT 兑换页（`/auth`） | token 签发（后端管） |

---

## 3. 雷点·红线（服务特有）

> 全局红线见根 CLAUDE.md §3（R-1~R-12）。此处只列**本服务特有**或对本服务**特别需要强调**的约束。

| # | 红线 | 说明 |
|---|---|---|
| **RS-1** | **测试阶段保持精简甚至简陋** | 测试阶段不要新增过多美观元素，仅保留最基本能完成测试的功能性内容。Element Plus 用最少必要组件（`el-button / el-card / el-result / el-message`），不追求视觉打磨；样式/布局/响应式优化一律延后到测试通过后。理由：避免功能未稳时浪费精力打磨 UI，便于快速发现链路问题。 |
| **RS-2** | 遵守 R-9 前端权限仅可见性 | 任何按钮/页面的禁用、隐藏、置灰都只是 UX，**不构成权限**。后端 RBAC 拒绝才算真拒绝；前端不写"应该不能"的乐观逻辑。 |
| **RS-3** | App.vue 必须含 `<router-view />` | F1 脚手架阶段曾把 App.vue 当占位模板（`<h1>...</h1><el-button>...</el-button>`），F3 加路由时漏改，导致路由匹配但页面不渲染（V1 验收时发现）。任何带路由的项目，App.vue 只能是 `<router-view />` + 必要的全局 layout 包裹。 |
| **RS-4** | JWT 存 localStorage 是 XSS 妥协 | localStorage 可被 XSS 偷，配合 CSP + 输入转义缓解；不存更敏感数据。后续若改 HttpOnly cookie 需重做鉴权链路。 |
| **RS-5** | 401 由 axios 拦截器统一处理 | 业务代码不写 `if (e.response?.status === 401)`，由 `utils/http.ts` 响应拦截器统一 `auth.clear()` + 跳 `/auth`。 |

---

## 4. 关键要素

### 主要消费的后端接口（已实现）
| 端点 | 用途 |
|---|---|
| `POST /auth/exchange` | 一次性 token → JWT pair（`/auth` 页 `onMounted` 调用） |
| `GET /me` | 取当前身份（`/me` 页展示 UUID/名称/角色） |
| `POST /auth/refresh` | access 过期前用 refresh 续签（待接入） |
| `/sheets/*` 全套 | 项目（sheet）CRUD + 行 upsert/删 + 协作（claim/delivery/contribute/release/reject/progress）+ **阶段生命周期**（`POST /sheets/{id}/advance?to=` 流转 / `GET /sheets/{id}/archive` 归档 md 预览 / `GET /sheets/{id}/archive/assets/{filename}` 归档产物（贡献占比 PNG）/ `GET /sheets?status=` 进行中·已归档 tab 过滤），`SheetList`（tab 进行中/已归档 + 状态列）/ `SheetEditor`（阶段横幅 + owner 流转按钮 + archived 只读 + 归档 `<pre>` md 预览 + 下方贡献占比 `<img>`）消费；详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) |
| `POST /parsing/litematic` | 投影解析上传（`LitematicImport.vue` → 预览 → 生成 Sheet）；详见 [`api/parsing.md`](../Docs/architecture/api/parsing.md) |

> **术语演进**：玩法语义 sheet 已升级为「项目」，UI 文案统一改「项目」（`App.vue` 导航 / `SheetList` / `SheetEditor`），但 URL `/sheets`、API 类型名 `Sheet*` 保留不变（YAGNI，避免书签/外链失效）。

> 完整 API 列表见各服务架构文档；OpenAPI 工件 `Backend/openapi.json`。

### 依赖的其他服务
- **user-service**：`/auth/exchange`、`/auth/refresh`、`/me`、`/players`、`/web-accounts`
- **project-service**：`/projects/*`（`.litematic` 上传）
- **scoring-service**：`/submissions`、`/scores`
- **title-service**：`/titles/*`
- **wiki-service**：`/wiki/sync-log`
- **alert-service**：`/alerts`

### 关键运行时状态
- `useAuthStore`（Pinia）：`accessToken / refreshToken / player`，持久化到 `localStorage`
- `utils/http.ts`：axios 实例 + 请求/响应拦截器（Bearer 注入、401 兜底）
- `usePolling`（composable）：递归 `setTimeout` 轮询 + Page Visibility 后台暂停 + 连续失败指数退避 + 卸载清理 + in-flight 重入保护（`SheetList` 10s / `SheetEditor` 1s + `silentRefresh` 保护正在编辑的草稿）

---

## 5. 文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| 本服务架构 | `../Docs/architecture/frontend.md` | 页面模块地图、交互流程、鉴权、构建部署 |
| 数据模型 | `../Docs/architecture/data-model.md` | 前端消费的实体定义（只读引用） |
| 工程总览 | `../Docs/architecture.md` | 三端架构与跨服务流程 |
| sheets API | `../Docs/architecture/api/sheets.md` | 表/行端点 + 行状态机 + 权限矩阵（前端协作 UI 消费） |
| parsing API | `../Docs/architecture/api/parsing.md` | 投影解析上传端点 + 响应模型（`LitematicImport.vue`） |
| 根规范 | `../CLAUDE.md` | 统一命名 / 红线 / 索引 |

---

## 6. 开发热重载工作流

> 前端**不在 docker 里跑**（根 [`docker-compose.yml`](../docker-compose.yml) 只含 postgres + backend）。开发时宿主机直接 `npm run dev`，依赖 Vite HMR。

| 改动类型 | 操作 | 生效方式 |
|---|---|---|
| `.vue` / `.ts` / `.js` 源码 | 保存即可 | Vite HMR 自动热替换（保留组件状态） |
| `vite.config.ts` / `package.json` | Ctrl+C 后 `npm run dev` 重启 | 手动重启 dev server |
| 加 npm 依赖 | `npm install <pkg>` | 重启 dev server |
| 生产构建验证 | `npm run build` → `npm run preview` | 静态产物预览 |

### 首次启动
```bash
cd Frontend
npm install                 # 装依赖
npm run dev                 # 启 Vite dev server（默认 http://localhost:5173）
```

### 验证前端 + 后端联调
1. backend 在跑（`curl http://localhost:8000/me` 返回 401）
2. `WEB_BASE_URL` 指向 dev server（`.env` 里 `WEB_BASE_URL=http://localhost:5173`，决定 `!!PCH login` 回链前缀）
3. 浏览器打开 `http://localhost:5173`，走 `/auth` token 兑换流程

### 常见排错
- **HMR 不生效**：确认 dev server 在跑（终端有 Vite logo）；浏览器没开「禁用缓存」时偶尔需要手动刷新
- **`/auth` 页兑换 401**：检查 token 是否过期（TTL 10 分钟）或已被新一次签发 revoke（见 [`Backend/CLAUDE.md`](../Backend/CLAUDE.md) RS-5）
- **401 后没跳 `/auth`**：axios 拦截器没装好，检查 `utils/http.ts` 响应拦截器（RS-5）

---

## 7. 与根规范的关系

- 遵守根 [`CLAUDE.md`](../CLAUDE.md) 的命名分层（§1：目录大驼峰 `Frontend/`、Vue 组件 PascalCase）与全局红线（§3 R-1~R-12）。
- 本文件的 RS-x 红线是**服务特有**补充，不覆写全局红线。
- 命名 / 全局红线 / 技术栈若有冲突，以根 CLAUDE.md 为准并修正本文件。

---

*最后更新：2026-07-03（sheet 升级为「项目」三阶段 UI：SheetList tab 进行中/已归档 + 状态列 + 文案表格→项目；SheetEditor 阶段横幅 + owner 流转按钮 + archived 只读 + 归档 md `<pre>` 预览 + 贡献占比 `<img>`；归档 asset 端点接入）*
