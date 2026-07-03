# HTCMC PCHSystem · 根规范（CLAUDE.md）

> 本文件是**分布式 CLAUDE.md 的根**，仅规范全项目统一的要求。
> 各子服务目录下各有自己的 `CLAUDE.md`（雷点 / 关键要素 / 文档索引），由 `service-claude-md` skill 生成与维护。
> 完整工程架构详见 [`Docs/architecture.md`](./Docs/architecture.md)。

---

## 0. 特殊约束（最高优先级，覆盖一切）

| # | 约束 | 说明 |
|---|---|---|
| **S-1** | **MCDR 相关内容必须联网验证后再实现** | 任何 MCDReforged API、命令节点树、RCON 用法、事件监听、插件元数据，**实现前必须**通过官方文档（<https://docs.mcdreforged.com/zh-cn/latest/>）或可靠来源联网核实 API 签名与版本兼容性，**禁止凭记忆臆造**；验证结论需附 URL。 |
| **S-2** | **输出语言使用中文** | 所有解释、注释、用户沟通使用简体中文；代码标识符沿用各语言命名惯例。 |

---

## 1. 命名规范（分层适用）

| 命名对象 | 规范 | 示例 |
|---|---|---|
| 目录 / 文件夹（领域、容器、前端模块） | **大驼峰 PascalCase** | `Backend/`、`Frontend/`、`McdrPlugin/` |
| 类 / 类型 | **大驼峰 PascalCase** | `SubmissionHandler`、`Project` |
| 变量、方法 / 函数 | **蛇形 snake_case** | `player_name`、`calc_score()` |
| Python 包名、模块文件（被 `import` 的） | **PEP8 小写**（例外） | 包 `user_service/`、模块 `score_ledger.py` |
| Vue 组件文件 | **大驼峰 PascalCase** | `ProjectList.vue`、`BindConfirm.vue` |
| 配置键、YAML / JSON 字段 | **蛇形 snake_case** | `api_url`、`service_token` |
| 数据库表 / 列 | **蛇形 snake_case** | `score_ledger`、`player_uuid`（见 [data-model.md](./Docs/architecture/data-model.md)） |
| Skill 目录、`.claude/` 配置 | **kebab-case**（系统例外） | `service-claude-md/` —— 沿用 Claude Code 工具链约定 |

> **原则**：目录与类用大驼峰；变量与方法用蛇形；Python 可 `import` 的包/模块、SQL 表列、配置键、Claude 工具链目录沿用各自生态惯例。
> 全局 `naming-conventions` skill 的「目录 snake_case」约定**在本项目被本表覆盖**。

---

## 2. 技术栈与架构（引用，不重复）

完整决策记录（ADR）见 [`Docs/architecture.md`](./Docs/architecture.md) §3。要点：

| 维度 | 选型 |
|---|---|
| 整体架构 | API 网关 · 三端完全分离（MCDR / FastAPI+Vue / wiki.js） |
| 后端 | Python · FastAPI · **模块化单体**（单库单服务，schema 隔离） |
| 前端 | Vue 3 + Element Plus + Vite + Pinia |
| 数据库 | PostgreSQL（Alembic 迁移，唯一业务库） |
| MC 层 | MCDReforged 插件（Fabric + Create + Carpet，**离线模式**） |
| Wiki | wiki.js（经**独立 wiki 内容 git 仓双向同步**，非 GraphQL 单向；后端 publisher 默认 off，配置 `WIKI_GIT_*`） |
| 部署 | Docker Compose（postgres + backend；wiki.js 独立部署、不入本仓 compose） |
| 关键库 | [`litemapy`](https://github.com/SmylerMC/litemapy)（`.litematic` 投影解析，自带 `nbtlib`，**不自研**）、[`amulet-nbt`](https://github.com/Amulet-Team/amulet-nbt)（SNBT 解析，**不自研**） |

---

## 3. 壁垒要点（红线，任何改动都不得违反）

| # | 红线 | 出处 |
|---|---|---|
| **R-1** | **数据唯一拥有者**：所有业务数据集中在 PostgreSQL，由 **FastAPI 后端独占**读写。**MCDR 插件绝不直连数据库**，仅经 HTTP API 与后端交互。 | arch §2.1 |
| **R-2** | **积分流水 append-only**：`score_ledger` **禁止 UPDATE/DELETE**（由权限/触发器保证）；任何积分变动记一条，含 `balance_after`，可审计重建。 | data-model §4.3 |
| **R-3** | **清箱时序**：扫描 → 上报后端 → 后端事务成功 → **才**清箱。**失败绝不清箱**，玩家可重试。 | arch §7.2 |
| **R-4** | **清空箱子用 `data merge block x y z {Items:[]}`**，**不是 `/clear`**（`/clear` 只清玩家背包）。 | mcdr-plugin §3.2 |
| **R-5** | **身份主锚 = Web 绑定账号**；MC UUID 为子身份（离线模式 UUID 由玩家名确定性推导，改名即换身份）。 | arch §2.1 |
| **R-6** | **物品 id 统一 registry id**（`namespace:path`），存储前剥离 BlockState properties；block→item 归一化集中在 project-service。 | data-model §0 |
| **R-7** | **MCDR 是纯游戏内客户端**：只做命令交互、箱子/背包扫描、UUID 推导、称号下发、HTTP 上报；**不做积分计算、不持久化业务数据、不做 wiki 同步**。 | mcdr-plugin §1 |
| **R-8** | **wiki.js 经 git 仓库双向同步（非 GraphQL 单向）**：后端把归档（`index.md` + `contributions.png`）提交推送到**独立 wiki 内容 git 仓**（默认 off，配置 `WIKI_GIT_*`；wiki.js 独立部署、不入本仓 compose）；wiki.js 与该远端双向同步渲染，拥有者获授权后可在 wiki.js 编辑自己的页面，改动经 git 回流、支持 PR 审查（host 层分支保护）。**PostgreSQL 业务库仍由后端独占（R-1 不变）—— wiki 是人类可读可编辑的投影，wiki 编辑绝不回写 sheets/score_ledger 等业务表。wiki git 仓 = wiki 内容权威源。** | arch §2.1 |
| **R-9** | **前端权限仅可见性**：真实权限以**后端 RBAC 为准**，前端只控展示。 | frontend §4 |
| **R-10** | **模块化单体**：部署单一 FastAPI 服务，内部按 schema 隔离（`users / projects / scoring / titles / wiki / alerts`），**不拆独立子服务**；跨表事务用单库事务。 | arch §2.2 |
| **R-11** | **密钥不进代码库**：`POSTGRES_*`、`WIKI_API_KEY`、`MCDR_SERVICE_TOKEN`、`JWT_SECRET` 经 `.env` / docker secrets 注入。 | arch §4 |
| **R-12** | **MCDR 阻塞调用放 `@new_thread`**（`mcdreforged.api.decorator.new_thread`）；`schedule_task` 的同步回调跑在 task executor = 主线程，**不可**用于卸载阻塞工作。HTTP 调用必含**超时 + 重试 + 失败回执**。 | mcdr-plugin §3.6 |

> 服务特有、局部的雷点写在各子服务 `CLAUDE.md`，不在此重复。

---

## 4. 项目结构概览

```
PCHSystem/
├── CLAUDE.md                          # 根规范（本文件）
├── README.md                          # 项目说明 / 快速开始 / 文档导航
├── CONTRIBUTING.md                    # 分支 / Commit / SemVer / MCDR 发布
├── CHANGELOG.md                       # 三端变更日志（Keep a Changelog）
├── docker-compose.yml                 # postgres + backend（dev，源码挂载热重载）
├── .env.example                       # compose 环境变量模板
├── Docs/                              # 架构与设计文档（权威）
│   ├── architecture.md                #   工程架构统一总览
│   ├── guied.md                       #   玩法设计
│   ├── Cheatsheets/dev-cheatsheet.md  #   开发指令速查
│   └── architecture/                  #   data-model / frontend / services/* / api/*
├── Backend/        (M1+M2 已实现)     # FastAPI 模块化单体   → 内含 CLAUDE.md
├── Frontend/       (F1–F4 已实现)     # Vue3 后台            → 内含 CLAUDE.md
├── McdrPlugin/     (已实现)           # MCDReforged 插件     → 内含 CLAUDE.md
├── TestServer/                        # 集成测试用 Docker 测试服（mc-test）
└── .claude/skills/service-claude-md/  # 子服务 CLAUDE.md 生成/维护 skill
```

---

## 5. 文档索引

### 架构与设计
| 文档 | 路径 | 说明 |
|---|---|---|
| 工程架构总览 | [`Docs/architecture.md`](./Docs/architecture.md) | 三端架构、技术栈、ADR、风险矩阵、跨服务流程 |
| 玩法设计 | [`Docs/guied.md`](./Docs/guied.md) | 黄皮子积分体系、项目管理、荣誉激励、风控 |
| 数据模型 | [`Docs/architecture/data-model.md`](./Docs/architecture/data-model.md) | 全部表结构、约束、索引、ER 图 |
| 前端 | [`Docs/architecture/frontend.md`](./Docs/architecture/frontend.md) | Vue3 后台模块、鉴权、构建 |

### 工程规范
| 文档 | 路径 | 说明 |
|---|---|---|
| 贡献与发布规范 | [`CONTRIBUTING.md`](./CONTRIBUTING.md) | 分支模型 / Conventional Commits / 各组件独立 SemVer / MCDR 插件发布（参考 MCDR 标准） |
| 开发指令速查表 | [`Docs/Cheatsheets/dev-cheatsheet.md`](./Docs/Cheatsheets/dev-cheatsheet.md) | 日常开发高频运维 / 调试指令（Docker / MCDR / 后端 / 前端）速查 |
| 运维手册 | [`Docs/RUNBOOK.md`](./Docs/RUNBOOK.md) | dev/staging 部署 / 健康检查 / 排错 / 回滚流程 |

### 各服务文档
| 服务 | 路径 |
|---|---|
| MCDR 插件 | [`Docs/architecture/services/mcdr-plugin.md`](./Docs/architecture/services/mcdr-plugin.md) |
| user-service | [`Docs/architecture/services/user-service.md`](./Docs/architecture/services/user-service.md) |
| project-service | [`Docs/architecture/services/project-service.md`](./Docs/architecture/services/project-service.md) |
| scoring-service | [`Docs/architecture/services/scoring-service.md`](./Docs/architecture/services/scoring-service.md) |
| title-service | [`Docs/architecture/services/title-service.md`](./Docs/architecture/services/title-service.md) |
| wiki-service | [`Docs/architecture/services/wiki-service.md`](./Docs/architecture/services/wiki-service.md) |
| alert-service | [`Docs/architecture/services/alert-service.md`](./Docs/architecture/services/alert-service.md) |
| notification-service | [`Docs/architecture/services/notification-service.md`](./Docs/architecture/services/notification-service.md) |
| markdown-service | [`Docs/architecture/services/markdown-service.md`](./Docs/architecture/services/markdown-service.md) |

> 各服务文档权威、完整；子服务目录下的 `CLAUDE.md` 只是其**雷点摘要 + 局部导航**，详情永远以上表文档为准。

### API 参考
| 文档 | 路径 | 说明 |
|---|---|---|
| sheets API | [`Docs/architecture/api/sheets.md`](./Docs/architecture/api/sheets.md) | sheets HTTP 端点 / 鉴权 / 行状态机（认领·交付·解除·打回·贡献·进度）/ 权限矩阵 / 错误码 / CSV 列 |
| parsing API | [`Docs/architecture/api/parsing.md`](./Docs/architecture/api/parsing.md) | `POST /parsing/litematic` 投影解析 + 中文翻译 + ABC 架构 / `POST /sheets/from-items` 批量建表 |

---

## 6. 分布式 CLAUDE.md 体系

| 层级 | 位置 | 职责 | 维护方式 |
|---|---|---|---|
| 根 | `PCHSystem/CLAUDE.md`（本文件） | 全项目统一规范 | 手动 / `persistent-context` skill |
| 子服务 | `<Service>/CLAUDE.md` | 该服务雷点、关键要素、文档索引 | **`service-claude-md` skill** |

**新建 / 更新子服务 CLAUDE.md 时**：调用 `service-claude-md` skill（传入服务名，自动读取对应架构文档按统一模板生成或增量更新），**不要手写**。

---

## 7. 当前任务状态

**已完成（2026-07-01）**：
- [x] 建立根规范（本文件）
- [x] 禁用与本项目冲突的全局 skill（`naming-conventions`、`persistent-context`，备份于 `~/.claude/.skill-backups/20260701/`）
- [x] 建立 `service-claude-md` skill（子服务 CLAUDE.md 唯一维护入口）

**已完成（2026-07-02）**：
- [x] 后端 M1+M2：`users` schema（players / auth_tokens / jwt_revocations）+ auth 链路（`/auth/token`·`/auth/exchange`·`/auth/refresh`·`/me`）
- [x] 后端双通道鉴权 `get_current_player`（Bearer JWT 优先，否则 service-token + `X-Player-UUID` 代玩家）
- [x] 后端 sheets 协作（迁移 `0004`/`0005`）+ notifications（迁移 `0006`）
- [x] 前端 F1–F4：`/auth` 兑换 · `/me` · 路由守卫 · axios 拦截器（含 sheets 列表/详情轮询）
- [x] MCDR 插件：`!!PCH login/bind` + sheets 命令树 + 通知轮询（`@new_thread`）
- [x] 子服务 CLAUDE.md：Frontend / McdrPlugin 已由 skill 生成；Backend 为导航待拆分
- [x] 投影解析生成表格：`POST /parsing/litematic`（上传 `.litematic` → litemapy 解析 → 中文翻译 → 预览，不落库）+ `POST /sheets/from-items`（批量建表+行，`mode` 默认 lock）；仅 Web 端；解析/翻译为 ABC，见 [`api/parsing.md`](./Docs/architecture/api/parsing.md)

**已完成（2026-07-03，v0.3.0 首次正式打 tag）**：
- [x] sheets progress 多人贡献者：迁移 `0007`（`sheet_row_contributors` 表）+ `0008`（`contributed_qty`）；progress 行不再单人锁定，任意玩家 `POST /contribute` 增量上交；owner `PATCH /progress` 覆写绝对值
- [x] MCDR deliver 按 mode 分流（progress→`contribute` 增量 / lock→`delivery` 绝对值）+ 贡献者显示
- [x] MCDR sheet view 特权按钮按查看者身份显隐（对齐后端 RBAC）
- [x] 前端 sheets 列表/详情轮询自动刷新（`usePolling` composable，后台暂停/退避/草稿保护）+ progress 上交/调整 UI
- [x] 通知体验修复（文案补清单名 / ack 防越权 body 加 `player_uuid` / 空列表按钮 / 默认轮询 2s 对齐）
- [x] 三组件分别打 tag `backend-v0.3.0` / `mcdr-v0.3.0` / `frontend-v0.3.0`（首个真正打 git tag 的版本）

**已完成（2026-07-03，sheet 升级为项目）**：
- [x] 项目三阶段生命周期：迁移 `0009_sheets_lifecycle`（`sheets.sheets` 加 `status` collecting/constructing/archived + `archived_path`/`archived_at` + 双 CHECK `ck_sheets_status_*` + `ix_sheets_status`，可逆）
- [x] 后端 `POST /sheets/{id}/advance?to=constructing|archived`（owner/admin，缺省按状态机推进；`to=archived` 走归档服务写盘+通知）+ `GET /sheets/{id}/archive`（返 `text/markdown`）+ `GET /sheets?status=collecting|constructing|archived|active` 过滤；archived 终态只读（repo `_assert_writable` 守卫 → 409）
- [x] markdown_render Route C 抽象（`Backend/app/services/markdown_render/`：`SectionRenderer` Protocol + `TemplateSection`/`FunctionSection` frozen + `MarkdownDocument` 不可变 register+有序聚合；参考 PromptStore 风格但抛弃 template/dispatch/WILD_CARD/body-fallback，零依赖）+ 归档服务 `Backend/app/services/archive/`（渲染→原子写盘→DB+通知→commit，失败 cleanup+rollback）+ `aggregate_contributor_totals` 贡献者精确排行
- [x] 三端 UI 适配：前端文案「表格」→「项目」+ SheetList tab 进行中/已归档 + SheetEditor 阶段横幅/流转按钮/archived 只读/归档 `<pre>` 预览；MCDR `!!PCH sheet advance <sheet_id> [constructing|archived]` + 阶段横幅 + owner footer 流转按钮 + 回执含归档路径
- [x] 归档文件系统持久化：docker-compose backend 加 `ARCHIVE_ROOT=/app/archive` + `./Archive:/app/archive` volume；`.env.example` 加 `ARCHIVE_ROOT`/`MARKDOWN_FRAGMENTS_DIR`；`Archive/` 目录骨架（.gitkeep + .gitignore 忽略产物）

**待处理**：
- [ ] 后端拆分为 `user_service/` 等子目录后，用 `service-claude-md` 生成各子服务 CLAUDE.md
- [ ] wiki.js 纳入部署 + wiki 内容 git 仓 host 选型（GitHub/Gitea/GitLab，未决；当前 compose 仅 postgres + backend，wiki.js 独立部署、不入本仓 compose）
- [ ] 拍板待确认参数（积分 `k / α / β / r`、赛季周期等，见 arch §9）

---

*最后更新：2026-07-03（R-8 重写为 wiki.js git 双向同步 + 归档产物升级：贡献者聚合含 lock、去材料清单、每项目独立文件夹、matplotlib 贡献占比饼图、asset 端点 + wiki git publisher；详见各服务文档）*
