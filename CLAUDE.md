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
| **R-3** | *插件不再提供清箱功能* |  |
| **R-4** | *插件不再提供清箱功能* |  |
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
| parsing API | [`Docs/architecture/api/parsing.md`](./Docs/architecture/api/parsing.md) | `POST /parsing/litematic` 投影解析 + `POST /parsing/nbt` Create 蓝图解析 + 中文翻译 + ABC 架构 / `POST /sheets/from-items` 批量建表 |

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

**已完成（2026-07-03，sheet 升级为项目三阶段 + 归档自动化）**：
- [x] 项目三阶段生命周期：迁移 `0009_sheets_lifecycle`（`sheets.sheets` 加 `status` collecting/constructing/archived + `archived_path`/`archived_at` + 双 CHECK `ck_sheets_status_*` + `ix_sheets_status`，可逆）
- [x] 后端 `POST /sheets/{id}/advance?to=constructing|archived`（owner/admin，缺省按状态机推进；`to=archived` 走归档服务写盘+通知）+ `GET /sheets/{id}/archive`（返 `text/markdown`）+ `GET /sheets?status=collecting|constructing|archived|active` 过滤；archived 终态只读（repo `_assert_writable` 守卫 → 409）
- [x] markdown_render Route C 抽象（`Backend/app/services/markdown_render/`：`SectionRenderer` Protocol + `TemplateSection`/`FunctionSection` frozen + `MarkdownDocument` 不可变 register+有序聚合；参考 PromptStore 风格但抛弃 template/dispatch/WILD_CARD/body-fallback，零依赖）+ 归档服务 `Backend/app/services/archive/`（渲染→原子写盘→DB+通知→commit，失败 cleanup+rollback）+ `aggregate_contributor_totals` 贡献者精确排行
- [x] 三端 UI 适配：前端文案「表格」→「项目」+ SheetList tab 进行中/已归档 + SheetEditor 阶段横幅/流转按钮/archived 只读/归档 `<pre>` 预览；MCDR `!!PCH sheet advance <sheet_id> [constructing|archived]` + 阶段横幅 + owner footer 流转按钮 + 回执含归档路径
- [x] 归档文件系统持久化：docker-compose backend 加 `ARCHIVE_ROOT=/app/archive` + `./Archive:/app/archive` volume；`.env.example` 加 `ARCHIVE_ROOT`/`MARKDOWN_FRAGMENTS_DIR`；`Archive/` 目录骨架（.gitkeep + .gitignore 忽略产物）

**已完成（2026-07-03，sheet registry_id 字段 + 一键提交）**：
- [x] sheet 行加隐式可空 `registry_id`（迁移 0010，`sheet_rows.registry_id TEXT NULL`，down_revision=`0009`）；`RowUpsertRequest`/`SheetItemIn` 的 `item_name` 改可选 + 新增 `registry_id`（model_validator 至少一个，否则 422）；`item_name` 缺失时后端 `LangJsonTranslator` 据 `registry_id` 自动翻译补中文名（复用投影解析翻译表，新增 mod 只需往 `translators/lang/` 丢 `*.zh_cn.json`）
- [x] 4 条 registry_id 写入途径打通：投影解析 `from-items` 透传（前端 map 补 `registry_id: r.item_id`）/ Web 行编辑器可选输入框 / MCDR `addhand` 手持新建 / `setreg` 给已有行补
- [x] MCDR 一键提交 `!!PCH sheet submit`（依赖 minecraft_data_api；扫背包含潜影盒嵌套 → 按 registry_id 精确匹配 → lock claim+deliver(need) / progress contribute 封顶到 need；纯申报不清背包）+ `scanner.py`（1.20.4-/1.20.5+ 双 NBT 路径，纯函数可单测，27 用例）
- [x] CSV 导出列追加 `registry_id`；全端对齐验证：后端 200 测试绿（含 10 条新用例 + 5 处 CSV 表头修正）/ 前端 19 测试绿 + vue-tsc / MCDR scanner 27 测试绿

**已完成（2026-07-06，sheet 快速重开 + list 增强）**：
- [x] 快速重开上次表格：迁移 `0011_players_last_sheet_id`（`users.players` 加 `last_sheet_id INTEGER NULL`，无 FK/无索引，对齐 `registry_id` 先例——表删后自然失效）+ `GET /sheets/{id}` JSON 详情路径 best-effort 写入（csv 导出与 404 不记，失败仅记日志）+ 新增 `GET /me/last_sheet`（双通道鉴权 `get_current_player`，响应 `{sheet_id: int|null}`；`player_repo.set_last_sheet`/`get_last_sheet` flush-only）+ MCDR `!!sheet`（无参重开上次 / `<id>` 直开，第二命令根）/ `!!PCH sheet last`（等同无参 `!!sheet`）
- [x] sheet list 增强：后端 `list_sheets` 加可选 `player_uuid`——参与优先排序（owner / lock 行 claimant / progress 行 contributor 三源 UNION 置顶，组内按 id 升序，`order_by id.in_(involved).desc(), id.asc()`），`GET /sheets` 透传 `player.uuid`，`player_uuid=None` 时按 id 升序向后兼容；`status` 过滤参数后端默认仍 None（由 MCDR 端默认传 `active`）；MCDR `sheet list` 默认进行中（active=collecting+constructing，排除归档）+ 自己参与的优先 + 每行阶段标签（`format_phase_label`）
- [x] MCDR list 简写旗标：`-m`(mine)/`-c`(collecting)/`-t`(constructing)/`-a`(archived)/`-l`(all)，可组合如 `-ma`；完整 `--mine` 等向后兼容；未知旗标回显助记提示（`_parse_list_flag_tokens` 纯函数化单测）

**已完成（2026-07-07，部署脚本）**：
- [x] 一键首次安装 `Scripts/install.sh`（12 步幂等）：检测/装 Docker（`get.docker.com --mirror Aliyun` + 发行版包回退）→ GitHub 连通性探测选镜像 → 同步最新发版 tag（或 `--edge` main）→ 生成 `.env`（`openssl rand` 三密钥，已存在绝不覆盖）+ 生产 override（去 `--reload` + 加 healthcheck，保留源码挂载）→ `docker compose up -d` 等 `/healthz` 200 → `alembic upgrade head`（前 `pg_dump`）→ 前端 `npm run build` → 拷 `htcmc_auth` 到 MCDR `plugins/` 并填同值 token → 持久化 `.pchsystem.deploy.env`
- [x] 一键更新 `Scripts/update.sh`：基于 `git diff` 路径的**智能重建矩阵**（仅 `Backend/Dockerfile` / `pyproject.toml` 变更才 rebuild；`app/**` / `alembic/**` 仅 `--force-recreate` 秒级；无 backend / compose 变更跳过容器操作）+ 迁移前快照不自动 downgrade + dirty 保护（拒跑本地跟踪文件改动）+ `--force` 接管非脚本部署 + `--edge` 临时拉 main
- [x] 国内网络四类镜像自适应（GitHub clone / Docker Hub / PyPI / npm）：探测 → 候选 → best-effort，单一镜像不可用绝不阻断，全失败回退直连
- [x] token 双写校验：`.env` `MCDR_SERVICE_TOKEN` 与插件 `config.json` `service_token` 必须同值——install 复用同一密钥双写、update 每次校验仅 warn 不擅改；密钥轮换流程见 [`Scripts/README.md`](./Scripts/README.md) §8
- [x] 共享函数库 `Scripts/lib/common.sh`（镜像探测 / Docker 安装 / 部署状态读写 / dirty 检查等被两脚本 source）；完整用法与边界见 [`Scripts/README.md`](./Scripts/README.md)

**待处理**：
- [ ] **既有 bug（v0.3.0 起）**：`!!PCH sheet add/set/addhand ... progress` 的 `Literal` 字面量未写入 `ctx`（MCDR 仅 ArgumentNode 入 context，见 mcdr-api-cheatsheet §4），`ctx.get("mode")` 恒 None → 实际建 lock 行；addhand 镜像继承。待统一修（建议字面量节点回调显式传 mode，或改读 command path）
- [ ] 后端拆分为 `user_service/` 等子目录后，用 `service-claude-md` 生成各子服务 CLAUDE.md
- [ ] wiki.js 纳入部署 + wiki 内容 git 仓 host 选型（GitHub/Gitea/GitLab，未决；当前 compose 仅 postgres + backend，wiki.js 独立部署、不入本仓 compose）
- [ ] 拍板待确认参数（积分 `k / α / β / r`、赛季周期等，见 arch §9）

**已完成（2026-07-09，子物品 issue #19 + sheets.py 包化重构）**：
- [x] **Phase 1 重构**：`Backend/app/api/sheets.py`（1215 行）包化拆分 → `sheets/` 包（`__init__/_shared/sheets_crud/rows/collab/lifecycle`）；新增公共翻译 `app/services/translation.py`（`get_translator`/`resolve_item_name`）修正 sheets→parsing 反向依赖；通知 helper（`_row_payload`/`notify_owner_row_event`/`notify_uuids`/`_row_response`）；测试保持绿（行为不变）。
- [x] **Phase 2 子物品（issue #19）**：迁移 0012（`sheet_rows` 加 `parent_row_id`/`qty_per_unit`；部分唯一索引 + CHECK；单层/模式继承/级联重算）；`RowUpsertRequest`/`RowDetail`/CSV 加两字段；MCDR addsub/delsub/setsub + 缩进渲染 + 单字按钮；前端树状渲染 + 子物品内联编辑。详见 [`Docs/architecture/data-model.md`](./Docs/architecture/data-model.md) §10.2 与 [`Docs/architecture/api/sheets.md`](./Docs/architecture/api/sheets.md) §14。

**已完成（2026-07-11，前端部署方案 + MCDR restart 误报修复）**：
- [x] **容器内 web 服务（默认启用）**：`Frontend/Dockerfile`（多阶段 node 构建 → nginx:stable-alpine 托管 dist，烘焙 `Frontend/nginx.conf`：try_files history fallback + `/api/` 反代 compose 服务名 `backend:8000` + `/assets` 长缓存）+ `Frontend/.dockerignore`；compose 加 `web` 服务（`profiles:["web"]`、`${WEB_PORT:-5173}:80`、`depends_on: backend`）—— `.env` `COMPOSE_PROFILES=web` 默认启用、改空即禁用（满足「允许配置是否启用」）；**端口默认 5173**：免 root + 对齐 `WEB_BASE_URL` 默认值，单机 `!!PCH login` 回链开箱即用
- [x] **非容器方案**：`Deploy/Nginx/pchsystem.host.conf.example`（root 占位 + `/api/` 反代 `127.0.0.1:8000/` + try_files + `/assets` 缓存）
- [x] **Scripts 联动**：`common.sh::web_profile_active()` 判定；`install.sh`（`--no-web` 旗标、`ensure_env` 按 `--no-web` 置空 `COMPOSE_PROFILES`、`start_stack` 按 profile 构建 web、`build_frontend` web 激活则跳过宿主 npm、summary Web 行 + `WEB_BASE_URL` 提醒）；`update.sh`（`decide_rebuild` 增 web 镜像重建分支——`Frontend/` 变更且 web 激活则 `compose_build web`+`up -d web`、`update_frontend` web 激活则让位）；`.env.example` 加 `COMPOSE_PROFILES`/`WEB_PORT`/`NPM_REGISTRY`
- [x] **修复 `update.sh` MCDR restart 误报**：`mcdreforged.plugin.json` 任何字段变更（version/dependencies/name/...）只需 `!!MCDR plugin reload`（reload = unload→load→`DependencyWalker` 重校依赖，源码 `plugin_manager.py`/`multi_file_plugin.py` 验证），折叠原 if/else 为统一 reload 消息
- [x] 文档：`Scripts/README.md` §10 双方案 + §12 改「默认托管前端」+ §7/§11 reload 说明；`Docs/architecture/frontend.md` §5

**已完成（2026-07-12，MCDR 插件 id 改名 htcmc_auth → pch_system）**：
- [x] **plugin id 统一为 `pch_system`**（与项目名 PCHSystem / `name: PCH System` 一致；插件不止 auth——含 sheets/submit/notify + 规划中 score/title）：`mcdreforged.plugin.json` 的 `id` 改 `pch_system`。MCDR 硬性要求 `id` = 文件夹名 = 内部包名（S-1 联网核实 [catalogue](https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_catalogue.html)「id 需与 plugin_info.json 所在目录同名」+ [metadata](https://docs.mcdreforged.com/en/latest/plugin_dev/metadata.html)「entrypoint 缺省 = id」），故 `git mv McdrPlugin/htcmc_auth → McdrPlugin/pch_system` + 内部包 `htcmc_auth → pch_system` + 类 `HtcmcAuthConfig → PchSystemConfig`；全仓 import / 路径 / logger 名 / 线程名（`htcmc_sheet_*→pch_sheet_*`、`htcmc_health_check→pch_health_check`）/ 脚本 / 编排（`TestServer` Dockerfile + compose + config 文件名）/ 活跃文档对齐。历史 `Docs/Plans/**`、`CHANGELOG`（`htcmc_auth-v*` tag）保留不动，仅声明新前缀 `pch_system-vX.Y.Z`（版本号不改）
- [x] **已部署实例迁移**：`Scripts/lib/common.sh::migrate_legacy_plugin_name()`——旧 `plugins/htcmc_auth/` 删除（避免与新 `pch_system` 双注册 `!!PCH` 冲突）、`config/htcmc_auth/` 搬到 `config/pch_system/`（保留玩家 `api_url` + `service_token`）；`install.sh` / `update.sh` 部署新插件前调用，幂等

**已完成（2026-07-19，身份主锚升级：Web 账号绑定多 MC UUID / 完善 `!!PCH bind`）**：
- [x] **后端**（迁移 `0014_web_accounts_bind`，down_revision=`0013`）：新表 `users.web_accounts`（id/username UNIQUE NULL/password_hash NULL/role/wiki_user_id/timestamps；`CHECK ((username IS NULL) = (password_hash IS NULL))` → NULL = 临时账号）+ `users.bind_tokens`（token PK/short_code UNIQUE/direction `game_init`|`web_init`/player_uuid/target_account_id/expires_at/used_at；方向一致性 CHECK + 部分索引 `ix_bind_tokens_active WHERE used_at IS NULL`）；`players` 加 `web_account_id` FK（NULL，不回填，下次 `!!PCH login` 自动建临时账号挂接）。`core/security.py`（bcrypt hash/verify + 6 位 Crockford Base32 短码剔除易混字符）。JWT `sub` 由 `player_uuid` 改 `web_account_id`（+ `active_uuid` claim）——**breaking：现有会话失效需重登**；`role` 权威源迁 account 级（`require_role` 重构，未绑玩家回退 `player.role`）。新端点：`POST /auth/login`（密码登录）/ `POST /web-accounts/register`（临时→永久）/ `GET /web-accounts/me` / `POST /bind/token`（service-token，game_init 出码）/ `POST /bind/issue`（JWT，web_init 出码）/ `POST /bind/confirm`（JWT，消费 game_init）/ `POST /bind/consume`（service-token + `X-Player-UUID`，消费 web_init）/ `POST /bind/claim`（JWT 临时，凭永久账号凭据挂接当前 UUID）。聚合查询（`sheet_repo.list_sheets` 参与优先 / `notification_repo.pending` / `aggregate_contributor_totals` 归档排行）改 `JOIN players → GROUP BY web_account_id`（NULL 用 `COALESCE` 回退按 uuid；`min(uuid)` 统一 cast text 规避 PG 限制），业务表零迁移；`score_ledger`（未建）将来建时直接加 `owner_account_id`。pyproject 加 `bcrypt>=4.1`。26 条 identity 集成测试单跑全绿（批量跑受既有 `conftest::_truncate_db` 同步 TRUNCATE 与 async session 死锁 flakiness 影响偶发失败，非本特性引入）。
- [x] **MCDR**：`!!PCH bind`（无参 → game_init 出短码）+ `!!PCH bind <code>`（消费 web_init 码，挂接当前 UUID）；新增 `bind_client.py`（`request_bind_token` 单头 / `consume_bind_code` 双头，哨兵 `__RATE_LIMITED__`/`__REMOVED__`/`HttpError`/`None` 复用 sheet_client 骨架）；`commands.py` 加 `_bind`/`_bind_consume`（镜像 `_login` 的 `@new_thread('pch_system bind')` 模板）；`__init__.py` 命令树替换 `_not_impl("bind")` stub 为 `Literal("bind").runs(_bind).then(Text("code").runs(_bind_consume))`；短码回执整行 `§7` 灰（敏感信息规则，禁 `§` 高亮）；`_pch_root` 帮助文案 bind 移入「已上线」。S-1 联网核实 Text 节点存值入 ctx（[command.html](https://docs.mcdreforged.com/en/latest/code_references/command.html)），与 sheets mode Literal 不进 ctx 的已知 bug 无关。11 条 bind_client 测试覆盖率 100%（总 336 passed）。
- [x] **前端**：新增 `src/api/identity.ts`（8 函数 + 7 类型，顺手封装原 inline 的 `exchangeToken`/`fetchMe`）+ `stores/auth.ts` 加 `account: AccountBrief | null` 字段 + `isTemporaryAccount` getter；新增 5 视图 `views/identity/{Register,Login,BindConfirm,ClaimBind,Identities}.vue`；`Me.vue` 升级为账号 + 绑定 UUID 列表 + `active_uuid` 标记 + 临时账号引导横幅；`AuthExchange.vue` 按账号临时/永久分流到 `/register` 或 `/me`；router 加 5 路由 + `App.vue` nav「身份管理」。14 条新测试（identity.spec.ts + auth.spec.ts），vitest 9 文件 / 103 测试全绿 + `vue-tsc` 干净 + build 通过。

**已完成（2026-07-19，merge origin/main 协管员 + manager 升 account 级）**：
- [x] **迁移链重编号**（两侧都占 0014 号）：`0013_qty_per_unit_float → 0014_web_accounts_bind → 0015_web_account_display_name → 0016_sheet_managers`（单 head 0016；main 原 `0014_sheet_managers` 重编号为 `0016`、down_revision 改写为 `0015_web_account_display_name`；HEAD 的 0014/0015 保持原号）。dev DB 需 `docker compose down -v && up -d` + `alembic upgrade head` 重置重放（项目未上线，无数据迁移负担）。
- [x] **SheetManager 锚 `web_account_id`（account 级 manager，R-5 一致）**：重做 main 原 per-UUID `0014_sheet_managers`——列 `player_uuid UUID` → `web_account_id BIGINT FK→users.web_accounts.id ON DELETE CASCADE`；PK `(sheet_id, web_account_id)` 天然防重复授予；索引 `ix_sheet_managers_account`；`granted_by_uuid` 保留作审计。**同账号任一 UUID 继承 manager**（M11/M12 必测）；`web_accounts` 删除 → `sheet_managers` CASCADE → 该账号 UUID 立即失权（E5）。
- [x] **权限 helper 重构**（`Backend/app/api/sheets/_shared.py`，5 个 helper，**删 `_can_edit` deprecated 别名 B4**，HEAD 7 处调用点机械替换为 `_can_manage`/`_can_operate`）：`_is_owner(sheet, account_uuids)`（`owner_uuid in account_uuids`，不收 player 防误读 `player.uuid`）/`_is_superuser(player)`（★切 `_resolve_role` 与全局 RBAC 一致，避免「绑 account 但 `player.role` 仍是旧值」，B1，M05 必测）/`_can_manage(sheet, player, account_uuids)`（tier A = owner ‖ superuser）/`_is_account_manager(sheet, player)`（`aid=player.web_account_id`；`aid is None → False`，B3；直接读 `sheet.managers` 删 `getattr` 兜底，B5；漏加载显式报错）/`_can_operate(sheet, player, account_uuids)`（tier B = `_can_manage` ‖ `_is_account_manager`）。`_can_operate` 文档字符串固化前置条件：「调用前必须经 `_load_sheet_or_404`/`sheet_repo.get_sheet` 加载以触发 selectin 预加载」（N4）。
- [x] **★行为变化（PR 必写）**：`advance ?to=constructing` 由 HEAD 的 tier A 改采 main 的 tier B（manager 也能推进施工）——HEAD 漏分流修正（M07 必测，owner 收到「施工开始」通知）。
- [x] **新端点**（`Backend/app/api/sheets/managers.py`，按契约重写 account 锚）：`POST /sheets/{id}/managers {player_uuid}`（后端解析 target account：未绑 → 422 B7；与 owner 同账号 → 409 `SheetOwnerCannotBeManager` B7；重复授予幂等 201 不重发通知 M21）+ `DELETE /sheets/{id}/managers {web_account_id}`（self-revoke 放行当 `player.web_account_id is not None and web_account_id == player.web_account_id`，**B6 NULL 守卫** M23 必测；他人 revoke 需 `_can_manage`）+ `GET /sheets/{id}/managers`（返 account 简报，透明全员可读）。`SheetManagerEntry = {web_account_id, display_name, member_uuids, granted_at}`（结构与 `RowContributor` 对齐）；`SheetDetail` 同时挂 `viewer_uuids`（HEAD）+ `managers`（main-account）。`Backend/app/api/players.py` 并入（玩家名→UUID 联想，grant 依赖）。
- [x] **权限矩阵 M01–M26 + E1–E5 落地**：扩充 `Backend/tests/` 权限集成测试（与 identity 既有 26 条互补）+ 整张表搬进 [`Docs/architecture/api/sheets.md`](./Docs/architecture/api/sheets.md) §7.1 作三端权限对账权威。身份档位 8 类（Owner-S/Owner-M/SuperAdmin/Mgr-Other/Mgr-Same/Mgr-Bind/Unbound/Stranger）× 动作 × 期望 × 关键点全覆盖；**tier A/B 均在 account 级 owner 锚下判定，manager 亦 account 级**（非 main 原 per-UUID 措辞）。
- [x] **前端**：`SheetEditor` 的 owner / 认领人 / manager 判定全用 `viewer_uuids`（HEAD 已升）+ `managers[].member_uuids`（main-account，新增）；`isManager` 单一 computed（N2 DRY）封装 `managers.some(m => m.member_uuids.some(u => viewerUuids.includes(u)))`（viewerUuids = auth store 绑定 UUIDs + 当前 UUID）；协管员管理面板 owner 可增删（`display_name` 展示、revoke 传 `web_account_id`），全员可见列表；改名/删表/归档按钮 owner 专属（`canManage` tier A），协管员可见 tier B 按钮。
- [x] **MCDR**：`!!PCH sheet manager <表id> [list|add <玩家名>|remove <玩家名>]`（add/remove 先 `uuid_api_remake.get_uuid(玩家名)` 转 UUID，后端解析 account）；`_render_sheet_detail` 的 `is_manager` 改 account 级——新增 `_is_manager(managers, viewer_uuids)` 单一 helper（N2 DRY，`any(uuid in viewer_uuids for m in managers for uuid in (m.get("member_uuids") or []))`）；行级 `[改][-][子][调]` 与底部 `[进入施工][新增物品]` 按钮对协管员可见，归档/改名/删表按钮仅 owner 可见。`messages.py::format_row_clickable` 签名合并 5 kwarg（`is_owner/is_manager/viewer_uuids/player_name/player_uuid`）。**HEAD 预存 3 个红测随合流修复**：`tests/test_messages.py` contributors fixture 改 account-level shape（`{account_id, display_name, member_uuids, contributed_qty}`），此前 HEAD 的 `_format_contributors` 已改读 `display_name` → 旧 fixture 即红。

---

*最后更新：2026-07-19（merge origin/main 协管员 + manager 升 account 级：迁移链重编号 `0013→0014_web_accounts_bind→0015_web_account_display_name→0016_sheet_managers` 单 head；SheetManager 锚 `web_account_id` 同账号任一 UUID 继承；5 个权限 helper 删 `_can_edit`；`_is_superuser` 切 `_resolve_role`；`advance→constructing` 改 tier B；权限矩阵 M01–M26 落 pytest + sheets.md §7.1；前端/MCDR `is_manager` 用 `managers[].member_uuids ∩ viewer_uuids`。R-5 全面落地）*
