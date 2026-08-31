# 后端 FastAPI 模块化单体 · CLAUDE.md

> 本文件是后端整体导航。待拆分为 `user_service/` 等子目录后，各子服务 CLAUDE.md 由 `service-claude-md` skill 接管（根 [`CLAUDE.md`](../CLAUDE.md) §4 已规划）。
> 全局统一规范见根 [`CLAUDE.md`](../CLAUDE.md)。

---

## 1. 服务定位

FastAPI 模块化单体：单库单服务，内部按 schema 隔离（`users / projects / scoring / titles / wiki / alerts`）。是 PCHSystem 的**唯一业务数据拥有者**（根红线 R-1），所有业务读写集中于此。

> 完整架构：[`Docs/architecture/`](../Docs/architecture/)（各服务 `services/*.md`）

---

## 2. 职责边界

| 管 | 不管 |
|---|---|
| 全部业务数据读写（PostgreSQL 独占） | 游戏内命令交互（MCDR 管） |
| RBAC 权限判定（真实权限源） | 前端展示逻辑（前端只控可见性，R-9） |
| JWT 签发与校验、一次性 token 软失效 | wiki.js 内容存储（只单向同步，R-8） |
| Alembic 数据库迁移 · 投影/蓝图解析（litemapy / nbtlib）+ 中文翻译 | .litematic / .nbt 文件存档（仅即时解析、不持久化） |

---

## 3. 雷点·红线（服务特有）

> 全局红线见根 CLAUDE.md §3（R-1~R-12）。此处只列**本服务特有**或对本服务**特别需要强调**的约束。

| # | 红线 | 说明 |
|---|---|---|
| **RS-1** | 遵守 R-1 数据唯一拥有者 | 后端独占 PostgreSQL 读写；MCDR / 前端只走 HTTP API，不直连数据库。 |
| **RS-2** | 遵守 R-2 积分流水 append-only | `score_ledger` 禁止 UPDATE/DELETE；任何积分变动记一条含 `balance_after`。 |
| **RS-3** | 遵守 R-10 模块化单体 | 单一 FastAPI 服务，schema 隔离，不拆独立子服务；跨表事务用单库事务。 |
| **RS-4** | 遵守 R-11 密钥经环境变量 | `POSTGRES_*` / `JWT_SECRET` / `MCDR_SERVICE_TOKEN` 经 `.env` 注入，不进代码库。 |
| **RS-5** | 一次性 token 软失效（soft revoke） | `issue()` 签发新 token 前先 revoke 同 UUID 未使用旧 token（`revoked_at` 置位）；兑换校验 `revoked_at is null`。保留审计痕迹，不硬删。 |
| **RS-6** | RateLimiter 单进程内存实现（MVP） | 当前 `auth_service.rate_limiter` 是进程内字典，多 worker 下失效；生产前需迁 Redis。 |
| **RS-7** | 异步一致性 | SQLAlchemy 2.x async + `pytest-asyncio`；阻塞 IO（如调外部 API）必放 `asyncio` 任务或线程池，不阻塞事件循环。 |
| **RS-8** | 双通道 `get_current_player`（api/deps.py） | Web 走 `Authorization: Bearer <jwt>`；MCDR 走 `X-Service-Token` + `X-Player-UUID` 代理（`secrets.compare_digest` 校验后查 Player 注入）。**业务层零改动**——RBAC 基于 `Player`，与凭证来源无关。`/sheets/export` 与 `/notifications/*` 仍独占 `require_service_token`（无身份）。**H-2**：Authorization 头存在（即便非 Bearer/过期/非法）只走 JWT 通道报 401，**绝不静默降级**到 service-token。代理命中后落 `service_token_proxy` 审计日志（H-1'，不含 token）。 |
| **RS-9** | notification-service 契约入口（services/notification_service.py） | 发通知统一走 `notify(session, ...)`，**必须在调用方写端点同一事务的同一 session 内调用**（R-10：业务改库 + 记通知原子，回滚则通知不落库）。`category` 用 String 按 `<domain>_<event>` 扩展；`Notifier` Protocol 预留 Webhook/Discord 扩展点。**C-1**：`mark_delivered/mark_read` 必须带 `recipient_uuid`，SQL WHERE 限定归属，防越权 ack/read 他人；**M-2/M-3**：入口对 title(≤200)/body(≤500) 限长 + 控制字符清洗、payload 序列化 >8KB 截断。详见 [`Docs/architecture/services/notification-service.md`](../Docs/architecture/services/notification-service.md)。 |
| **RS-10** | sheet 项目阶段生命周期（迁移 0009） | `sheets.sheets.status` ∈ collecting/constructing/archived；**archived = 终态只读**——repo `_assert_writable(session, sheet_id)` 是所有写操作的统一守卫入口（archived → `SheetArchived` → api 409），含 advance/行级 upsert/claim/delivery/contribute/release/reject/progress/删行/删表。`advance_sheet` 用 `SELECT ... FOR UPDATE` 锁行 + 状态机校验（合法：collecting→constructing / collecting→archived / constructing→archived；幂等 `to==当前` → `SheetRowConflict`）。归档经 `services/archive/archive_sheet()`：渲染 md → matplotlib 渲染 `contributions.png`（PNG 贡献占比饼图，CJK 字体 **Noto Sans CJK SC**，需容器装此字体；≤5 人全显 / >5 人 top5+其他）→ `write_atomic` 原子写盘（**事务外**）→ DB 置 archived 三字段 + `notify_many`（全体参与者，触发者同 account 跳过含 owner 自归档；`category="sheet_archived"`）→ 内部 commit；**失败 cleanup 孤儿文件 + rollback**。**进施工通知**：`advance?to=constructing` 同样广播全体参与者（`category="sheet_advanced_constructing"`，触发者同 account 跳过含 manager 自推进），api 层 commit。「全体参与者」= owner + managers（展开 account 全 UUID）+ lock 行认领人 + progress 行贡献者，由 `sheet_repo.collect_participant_uuids` 4 源 UNION 去重（issue #4）。**归档产物 = 每项目独立文件夹** `ARCHIVE_ROOT/projects/{id}/`：`index.md`（归档正文，去逐行材料清单，section 含 📦/🏆/📊/📅 + footer）+ `contributions.png`；`archived_path` 存相对 `ARCHIVE_ROOT` 的 POSIX 路径 `projects/{id}/index.md`。**wiki git publisher**（默认 off，best-effort）：归档成功后 wiki-service 把 `projects/<id>/` 整目录 `git commit + push` 到独立 wiki 内容 git 仓（subprocess git，token 内嵌 push URL 不落盘；失败仅 `notify(category="wiki_publish_failed")`，不抛、不回滚 DB——业务库是权威源）。config 加 `WIKI_GIT_REMOTE_URL`/`WIKI_GIT_BRANCH`/`WIKI_GIT_TOKEN`/`WIKI_GIT_AUTHOR_NAME`/`WIKI_GIT_AUTHOR_EMAIL`，空 `REMOTE_URL` = 不推送。**asset 端点** `GET /sheets/{id}/archive/assets/{filename}` 读 `projects/{id}/{filename}` 返 `image/png`（basename 白名单 + 路径穿越守卫 → 404；鉴权 `get_current_player`）。`archived_path` 是 wiki-service 同步入口（R-8 重写后为 git 双向）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §4.1 / §5.2、[`data-model.md`](../Docs/architecture/data-model.md) §10.4、[`services/wiki-service.md`](../Docs/architecture/services/wiki-service.md)。 |
| **RS-11** | markdown_render Route C 抽象（services/markdown_render/） | 通用结构化 markdown 渲染：`SectionRenderer` Protocol（`@runtime_checkable`，与 `Notifier` 同范式）+ `TemplateSection`/`FunctionSection`（`@dataclass(frozen=True)`）+ `MarkdownDocument`（frozen；`register` 返回新对象 + 同名 override + 按 order 有序聚合，`render` 过滤空白）。**不可变**（项目编码规范）。零依赖（不引 Jinja2）。保留 PromptStore 的「不可变 register + 有序聚合」风格，**抛弃** template 调度 / dispatch / WILD_CARD / body-fallback / `{placeholder}` 引擎（结构化 md 渲染无对应用例）。首个消费者是 sheet 归档服务；未来榜单/报表注册不同 section 集合即可复用。详见 [`services/markdown-service.md`](../Docs/architecture/services/markdown-service.md)。 |
| **RS-12** | 施工进度上报层（迁移 0017，construction + system schema） | `POST /v1/construction/report` 双通道鉴权——**专用 `get_construction_reporter`，不复用 `get_current_player`**（service-token 接受**多玩家** batch，JWT 通道要 `mod_id` claim）：无 `X-Source-Id`→官方 `{mcdr, official}` 锚 / 有→须白名单 `{server_mod, <name>}`（否则 403）/ JWT[mod_id] 强制 `active_uuid`（C-10）。**严格单源**（C-7）：report **不隐式切源**——非活跃源 entries 全 skip（reason `玩家当前由其他源上报`/`无活跃上报源`）；无 `player_sources` 记录时默认活跃 = (mcdr, official)（`official_tracker_enabled` 控制）。落库按 (sheet, account, registry) upsert 聚合 `net_qty = placed − broken`（允许负）；`account_id` 锚 WebAccount（R-5）。切源两显式端点：`switch-server`（admin）/ `switch-self`（玩家 JWT，mode=server\|local）。**归档/结算读契约** `aggregate_placement_totals(sheet_id) → [(account_id, display_name, net_qty)]`（D8，hook 在 `services/archive/service.py` post-commit `# TODO(scoring)`，本轮**未接** settle）。MCDR 默认方块追踪器已落地（v0.9.0，[`api/construction.md`](../Docs/architecture/api/construction.md) §5 C-1~C-10 已兑现）。运行时开关 6 项落 `system.settings` JSONB（`construction.*` 键，DB 无值回退 `config.py` 默认，迭代 4 加 `enforce_single_construction` 第 6 开关）。详见 [`api/construction.md`](../Docs/architecture/api/construction.md) 与 [`flows/construction-progress.md`](../Docs/architecture/flows/construction-progress.md)。**迭代 2 增量**（迁移 0018 `placement_snapshots`）：① **方块清单校验**（`POST /report` 后端加防线，`registry_id` 不在 `sheet_rows.registry_id` 集合含子物品 → skip `方块不在项目材料清单内`，与 C-6 追踪器侧自过滤叠加）；② **时序快照**（每次 report 落 placement 后对**本轮 accepted 的 account** 各写一条 `INSERT...SELECT`，`total_net = sum(net_qty)`，best-effort 失败仅日志）；③ **进度端点扩展** `GET /{id}/progress` 加 `material_completion`（材料完成度，`completion_pct` 视觉封顶 100.0、`need_qty=0→null`、含子物品 `need=ceil(qty_per_unit×父need)`）+ `timeline`（`placement_snapshots` limit 200 升序）；④ **休眠源查询** `GET /source/me` 加 `dormant_sources: [{source_id, last_active_at}]`（曾活跃、当前 `disabled_at` 非空的 client_mod 源，按 source_id 去重取最近 activated_at；严格单源不变，仅作快速切回历史 mod_id 展示）。详见 [`api/construction.md`](../Docs/architecture/api/construction.md) §3.2 / §4 / §4.1 / §6。**迭代 4-5 增量**（迁移 0021 `construction_participants` + 0022 回填 + 0023 `report_events`）：① **加入施工机制**（`participants` 表 + `uq_participants_active` partial unique index 兜底「同账号同时最多 1 个活跃加入」；`enforce_single_construction` 升第 6 运行时开关默认 True）—— `auto` join 由 `sheet_repo._maybe_auto_join` 在 collecting/constructing 阶段 `claim`/`contribute` 触发（已在他项目 silent skip），`manual` join 经 `/me/join`/`/me/switch` 显式入口（enforce=True 冲突 → `ParticipantConflict` → api 409）；`leave_construction` 幂等（UPDATE `left_at`，保留历史行）；归档经 `close_all_participants` 批量退出（同 advance_sheet 事务）。② **按材料封顶**（迁移 0022 回填）：`submit_report` 逐条按 `(sheet_id, registry_id)` 跨账号合计净放置不得超过 `sum(need_qty)`，超量分支 emitted `accepted`(部分) + `skipped`(over)；满额时整条 skip（reason `已达材料上限`）。③ **上报事件流水**（迁移 0023 `report_events`）：`_flush_report_events` 对 bound 玩家逐条落 `accepted` + 所有 `skipped` reason（`/me/report-events` 数据源，让玩家看到「为什么我的上报被拒」），best-effort + SAVEPOINT 隔离失败不阻断主事务。④ **CR 修复**（2026-07-27 review）：`/me/switch` 走显式 leave+join 同事务绕开 enforce=True 的 409（并发竞争仍 409 原子回滚）；`join_construction` 加 `begin_nested` SAVEPOINT + `MAX_JOIN_RETRIES` 有界重试（防 CASCADE 抖动无限递归）；`PlacementSnapshot`/`ReportEvent` 写入 SAVEPOINT 隔离失败不污染外层（否则整次上报回滚、tracker 不推进 baseline → 增量堆积）；`Participant.updated_at` 显式刷新（model 无 onupdate，仿 `PlacementRecord`）。新增 6 端点：`GET /me/construction` / `POST /me/join` / `POST /me/switch` / `POST /me/leave` / `GET /me/report-events` / `POST /active-by-uuids`（批量 UUID→sheet_id，tracker 按玩家路由用，service-token 单头，非敏感）。详见 [`api/construction.md`](../Docs/architecture/api/construction.md) §4.2 / §5 / §7。 |
| **RS-13** | sheet 子物品级联语义（issue #80，`sheet_repo.py`） | 子行与顶层行共用状态机，**无子行守卫**（可单独 claim/release/delivery）；改级联逻辑前必读四规则：① 认领顶层 lock 父行 = 同事务认领所有 open lock 子行（同 claimant）；② **新建继承**——父行 lock 且 claimed/done 时新建 lock 子行落库即 `claimed` + 继承父行认领者 + `delivered=0`（防 open 死行；显式 progress 子行不继承）；③ **release 级联收窄**——解除顶层 lock 父行只级联解除 claimant=父行认领者的 lock 子行，他人认领的保留（父 progress 行 claimant 恒 null，天然零级联）；④ **mode 不级联**——父行 mode 变化只重算子行 `need_qty`（**必须 Decimal**：`Decimal(str(qty_per_unit)) * need_qty` 再 ceil，float 直乘 `0.07×100` 会进位成 8）。子行全 done **不**自动置父行 done（无 done 传导）；⑤ **父行终态冻结**——父行 `done` 后子行协作写全拒（`_assert_parent_not_done` 动态守卫：claim/release/delivery/progress/contribute/reject/update_row 子行分支/reparent/addsub 到 done 父行均 409「父行已备齐，子行已锁定」；batch_submit 整行 skip 同文案）。父行打回或 need 上调（既有 done→claimed）即自动解冻，存量脏数据免迁移；`delsub` 同拦（`delete_row` 先查行再守卫；删 done 顶层父行本身不拦——CASCADE 整体移除）。派生 need 公式收口 `_derived_need_qty()`（三处内联已合并，勿再内联）。协作端点 409 `detail` 透传 `SheetRowConflict` 中文原因（勿改回笼统 `row conflict`，MCDR 玩家回执依赖它）。语义权威见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §5.3 尾注级联规则。 |
| **RS-14** | 测试改 `deps._settings` 必须 monkeypatch 属性登记（勿裸赋值换指针） | `deps._settings = get_settings()` 每次新建对象且不经 monkeypatch 登记、teardown 不还原——先跑的测试文件把运行时 service-token 永久换成测试值，后续文件（construction 系在 collect 阶段读 env 值）顺序性 401「invalid service token」，全量批跑假红（main 曾因此 41 failed）。正确写法：`monkeypatch.setattr(deps._settings, "mcdr_service_token", "svc")`（改属性不换指针）。2026-08-30 已修 13 个文件；新写测试照此范式。 |

---

## 4. 关键要素

### 入口与结构
- 入口：`app/main.py`（FastAPI app + 路由挂载）
- 路由：`app/api/*.py`（`auth` / `me` / `sheets` / `notifications` / `parsing`）
  - **`app/api/sheets/` 包**（Phase 1 重构，2026-07-09）：`sheets.py`（1215 行）拆分为 `__init__.py`（parent router + include 子 router）+ `_shared.py`（共享函数 + 通知 helper）+ `sheets_crud.py`（表级 CRUD + export）+ `rows.py`（行 upsert/delete + 编辑通知）+ `collab.py`（协作状态机 6 端点：claim/delivery/release/reject/contribute/progress）+ `lifecycle.py`（阶段 advance + 归档读）。保持 `router` 为公开符号 → `main.py` import 路径不变。
- **公共翻译**（Phase 1 重构，2026-07-09）：`app/services/translation.py`（`get_translator() -> ItemTranslator` 单例 `LangJsonTranslator.default()` / `resolve_item_name(item_name, registry_id) -> str`：item_name 优先；缺失则翻译 registry_id；两者皆空 raise `ValueError`）。修正 sheets→parsing 反向依赖。
- 数据层：`app/models/`（SQLAlchemy 2.x）+ `app/repositories/`（repo 函数，不返回 ORM 对象给路由层）
- 配置：`app/core/config.py`（pydantic-settings，`auth_token_ttl_seconds` 等）
- 迁移：`alembic/versions/`
- 测试：`tests/`（pytest，AAA 结构）

### 关键接口（已实现）
| 端点 | 用途 |
|---|---|
| `POST /auth/token` | MCDR 调用（带 `X-Service-Token`），签发一次性登录 token；响应含 `login_url` / `expires_in` / `previous_tokens_revoked` |
| `POST /auth/exchange` | 前端调用，一次性 token → JWT pair（access + refresh） |
| `POST /auth/refresh` | refresh token 续签 access |
| `GET /me` | 当前身份（需 Bearer JWT） |
| `GET /notifications/pending` | MCDR 轮询拉取未投递通知（service-token，query `player_uuid`） |
| `POST /notifications/ack` | 批量标**该 player_uuid 名下**通知投递（service-token，body `{player_uuid, ids:[…]}`，C-1 防越权） |
| `POST /notifications/{id}/read` | 标已读（service-token，query `player_uuid` 归属校验，跨玩家 404；L-2 同步幂等置 delivered_at） |
| `POST /parsing/batch` | Web **唯一解析端点**：上传 1..N 个 `.litematic`/`.nbt`（混型）→ 每文件独立预览（成功/失败隔离；只解析、不收 multiplier）。护栏 `parsing_batch_max_files`/`parsing_batch_total_max_bytes`。详见 [`api/parsing.md`](../Docs/architecture/api/parsing.md) §8 |
| `POST /sheets/from-items` | 一次性建表 + 批量行（`mode` 默认 lock），用于「投影解析→生成表格」 |
| `POST /sheets/{id}/advance?to=` | 项目阶段流转（owner/admin，缺省按状态机推进；`to=archived` 走归档服务写盘+通知）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §5.2 |
| `GET /sheets/{id}/archive` | 读归档 markdown（`text/markdown`；未归档/文件缺失 → 404） |
| `GET /sheets/{id}/archive/assets/{filename}` | 读归档资产（如 `contributions.png` 贡献占比饼图，`image/png`；basename 白名单 + 路径穿越守卫 → 404；任意登录玩家可读） |
| sheets CRUD + 协作 | `GET/POST/PATCH/DELETE /sheets*`（`GET` 支持 `?status=collecting\|constructing\|archived\|active` 过滤）+ 行级 `claim`/`delivery`/`contribute`/`release`/`reject`/`progress`（JWT 或 service-token+UUID 代玩家写）—— 全套端点见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) |
| 施工进度上报 | `POST /v1/construction/report`（双通道：service-token 多玩家 / JWT[mod_id] 强制 active_uuid）+ `GET /active-sheets`（归因）+ `GET /{id}/progress`（进度）+ admin `GET/PATCH /settings`（6 开关）+ `/mod-sources` CRUD（白名单）+ `POST /source/switch-server`（admin）/ `switch-self`（玩家）/ `GET /source/me` —— 严格单源（C-7），详见 [`api/construction.md`](../Docs/architecture/api/construction.md) |
| 加入施工 + 上报事件流水（迭代 4-5） | `GET /v1/construction/me/construction`（查自己活跃加入）+ `POST /me/join` / `POST /me/switch` / `POST /me/leave`（加入/切换/退出施工，JWT 通道；`switch` 走显式 leave+join 同事务绕开 enforce=True 的 409）+ `GET /me/report-events`（玩家可见事件流水：accepted + 所有 skip 原因逐条落库，迁移 0023）+ `POST /active-by-uuids`（批量 UUID→sheet_id，MCDR tracker 按玩家路由用，service-token 单头，非敏感）—— 详见 [`api/construction.md`](../Docs/architecture/api/construction.md) §4.2 / §5 / §7 |

### 数据表（users schema）
- `players`：玩家主身（UUID + current_name + role + whitelist_state）
- `auth_tokens`：一次性登录 token（含 `expires_at` / `used_at` / `revoked_at`）
- `jwt_revocations`：JWT 吊销表（refresh 接入待办）

### 数据表（notifications schema）
- `notifications`：统一通知记录（recipient_uuid FK→users.players.uuid ON DELETE CASCADE / category / title / body / payload jsonb / created_at / delivered_at / read_at；索引 `(recipient_uuid, delivered_at)`）

### 数据表（sheets schema）
- `sheets`：表格主表（owner_uuid FK / title / `status` collecting\|constructing\|archived（迁移 0009）/ archived_path / archived_at / created_at / updated_at；双 CHECK `ck_sheets_status_*` + `ix_sheets_status`）
- `sheet_rows`：行（sheet_id FK CASCADE / item_name / registry_id（迁移 0010）/ need_qty / `mode` 0=lock|1=progress / `status` open|claimed|done / claimant_uuid / delivered_qty / sort_order / **`parent_row_id`（迁移 0012，FK 自引用 ON DELETE CASCADE）/ `qty_per_unit`（迁移 0012，子物品单位用量）**；部分唯一索引：顶层 `uq_sheet_rows_top_name`(sheet_id+item_name WHERE parent_row_id IS NULL) / 子行 `uq_sheet_rows_sub_registry`(parent_row_id+registry_id WHERE parent_row_id NOT NULL) + CHECK `ck_sheet_rows_sub_invariants`（子行必须有 registry_id 且 qty_per_unit>0；0013 放宽为小数 numeric(10,2)）+ `ix_sheet_rows_parent`；**不变量**：单层（子只能挂顶层）、模式缺省继承（未显式指定时随父行；显式指定即生效，issue #80 起父行 mode 变化**不**级联改子行 mode）、单位用量级联（子 need = ceil(qty_per_unit × 父 need)，Decimal 精确取整）、子行 item_name 自动加父名前缀「父名-本名」）
- `sheet_row_contributors`：progress 行贡献者聚合（row_id FK CASCADE / player_uuid FK / joined_at / contributed_qty；`UNIQUE(row_id, player_uuid)`；迁移 0007/0008）

> 完整 DDL 见 [`Docs/architecture/data-model.md`](../Docs/architecture/data-model.md) §2（users）/ §10（sheets，含 §10.4 项目阶段状态机 + 归档产物结构）/ §11（notifications）。归档产物落盘 `ARCHIVE_ROOT/projects/{id}/`（`index.md` + `contributions.png`，每项目独立文件夹；config `archive_root` + `WIKI_GIT_*`），渲染见 [`services/markdown-service.md`](../Docs/architecture/services/markdown-service.md)，wiki 推送见 [`services/wiki-service.md`](../Docs/architecture/services/wiki-service.md)。

---

## 5. 开发热重载工作流

> 容器编排见根 [`docker-compose.yml`](../docker-compose.yml)（postgres + backend）。
> **backend 容器已配置源码挂载 + `uvicorn --reload`，改 `.py` 文件无需 rebuild 镜像。**

| 改动类型 | 操作 | 生效方式 |
|---|---|---|
| `app/**/*.py` 源码 | 保存即可 | uvicorn `--reload` 自动重启（docker-compose 挂载 `./Backend/app:/app/app`，监听 `/app/app`） |
| `alembic/versions/*.py` 迁移 | `docker compose exec backend alembic upgrade head` | 手动执行（新增迁移后必跑） |
| `pyproject.toml` 加依赖 | `docker compose build backend && docker compose up -d backend` | rebuild 镜像（仅依赖变更才需要） |
| 跑测试 | `cd Backend && pytest tests/ -v` | 宿主机直接跑（依赖本地 venv）或 `docker compose exec backend pytest` |

### 首次启动 / 配置变更后
```bash
docker compose up -d                                # 启动 postgres + backend
docker compose exec backend alembic upgrade head    # 跑迁移到最新
```

### 验证 backend 健康
```bash
curl -sS http://localhost:8000/me                   # 应返回 401（未带 JWT，证明服务在跑）
```

### 常见排错
- **改了代码但行为没变**：确认 `pchsystem-backend-1` 容器在跑且未挂；`docker logs pchsystem-backend-1 --tail 30` 看 uvicorn 是否收到 reload 事件
- **响应字段缺失（如 `previous_tokens_revoked` 不存在）**：说明容器跑的是旧镜像，需 `docker compose up -d backend --force-recreate` 重建
- **迁移报错 `Target database is not up to date`**：先 `alembic current` 看版本，再 `alembic upgrade head`

---

## 6. 文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| 工程架构总览 | [`../Docs/architecture.md`](../Docs/architecture.md) | 三端架构、ADR、跨服务流程 |
| 数据模型 | [`../Docs/architecture/data-model.md`](../Docs/architecture/data-model.md) | 全部表结构与约束 |
| 各服务架构 | [`../Docs/architecture/services/`](../Docs/architecture/services/) | user/project/scoring/title/wiki/alert 各服务文档 |
| 根规范 | [`../CLAUDE.md`](../CLAUDE.md) | 统一命名 / 红线 / 索引 |

---

## 7. 与根规范的关系

- 遵守根 [`CLAUDE.md`](../CLAUDE.md) 的命名分层（§1：变量/方法 snake_case、类 PascalCase、SQL 表列 snake_case、文档文件 kebab-case 小写）与全局红线（§3 R-1~R-12）。
- 本文件的 RS-x 红线是**服务特有**补充，不覆写全局红线。
- 待后端拆分为 `user_service/` 等子目录后，本文件职责下放给各子服务 CLAUDE.md（由 `service-claude-md` skill 生成）。

---

*最后更新：2026-08-30（issue #80 子物品可独立协作：新增 RS-13 子物品级联语义 + RS-14 测试 deps._settings monkeypatch 范式；§4 sheet_rows 不变量改「模式缺省继承、父行不级联改子行 mode」）*

*增量（2026-08-30，issue #80）：子物品生命周期修复——删 claim/release 子行守卫（子行可单独认领/解除，存量死行自愈无迁移）；新建子行继承认领者（父 lock 且 claimed/done → 落库 claimed + 同认领者 + delivered=0）；release 级联收窄（只解除同认领者 lock 子行）；update_row 删 D7 mode 级联 + 级联重算 float→Decimal 修复；collab.py 六端点 409 detail 透传中文原因。测试：`_svc_token` fixture 裸赋值 service-token 顺序泄漏修复（13 文件改 monkeypatch.setattr，全量 752 passed）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §5.3 / §14、[`submit-extension.md`](../Docs/architecture/api/submit-extension.md)。*

*增量（2026-07-28 迭代 4-5）：RS-12 段补 4 处增量——① 加入施工机制（迁移 0021 `construction_participants` + `uq_participants_active` partial unique index + `enforce_single_construction` 升第 6 运行时开关默认 True，auto join 由 `sheet_repo._maybe_auto_join` 在 claim/contribute 触发、manual join/switch/leave 经 `/me/*` 端点，归档经 `close_all_participants` 批量退出）；② 按材料封顶（迁移 0022 回填，`submit_report` 逐条按 `(sheet_id, registry_id)` 跨账号合计净放置不得超过 `sum(need_qty)`，超量分支 `accepted`(部分) + `skipped`(over)，满额整条 skip）；③ 上报事件流水（迁移 0023 `report_events`，`_flush_report_events` 对 bound 玩家逐条落 `accepted` + 所有 `skipped` reason，best-effort + SAVEPOINT 隔离）；④ CR 修复（`/me/switch` 显式 leave+join 同事务绕开 409 / `join_construction` `begin_nested` + `MAX_JOIN_RETRIES` 有界重试 / `PlacementSnapshot`/`ReportEvent` SAVEPOINT 隔离失败不污染外层 / `Participant.updated_at` 显式刷新）。新增 6 端点：`GET /me/construction`、`POST /me/join`、`POST /me/switch`、`POST /me/leave`、`GET /me/report-events`、`POST /active-by-uuids`。详见 [`api/construction.md`](../Docs/architecture/api/construction.md) §4.2/§5/§7。*

*增量（2026-07-27 迭代 2）：RS-12 段补 4 处增量——方块清单校验（`registry_id` 不在 `sheet_rows.registry_id` 集合含子物品 → skip）+ 迁移 0018 `placement_snapshots` 时序快照表（`INSERT...SELECT` 批量，best-effort）+ `GET /{id}/progress` 加 `material_completion`（视觉封顶 100%）+ `timeline` 字段 + `GET /source/me` 加 `dormant_sources` 休眠源查询（严格单源不变）。详见 [`api/construction.md`](../Docs/architecture/api/construction.md) §3.2/§4/§4.1/§6。*

*增量（2026-07-27）：施工进度上报层落地（迁移 0017 `construction` + `system` 两 schema：`placement_records`/`player_sources`/`player_source_history`/`server_mod_sources` + `system.settings`）+ `app/api/construction.py`（11 端点）+ `app/repositories/construction_repo.py` + `app/api/deps.py::get_construction_reporter`（双通道，专用）+ `app/core/config.py` 5 默认字段。严格单源 + 归因三分支 + 切源两端点 + 归档/结算读契约 `aggregate_placement_totals`（D8 hook，未接 settle）。31 集成测试 + 上报脚本 + [`api/construction.md`](../Docs/architecture/api/construction.md)（含 C-1~C-10 默认追踪器实现契约段）。MCDR 默认追踪器待 S-1 单独 PR。新增 RS-12 红线。*

*增量（2026-07-09）：子物品嵌套行 issue #19 + sheets.py 包化重构（`app/api/sheets/` 包 + `app/services/translation.py`）；`sheet_rows` 加 `parent_row_id`/`qty_per_unit`（迁移 0012）+ 部分唯一索引 + CHECK 不变量（单层/模式继承/级联重算）；详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §14 增量日志。*

*增量（2026-07-07）：§4 端点表补 `POST /parsing/nbt`（Create 蓝图解析，commit f16a00a 遗漏，借前端 .nbt 支持 #5 一并补齐）；§2 职责泛化 litemapy / nbtlib。*
