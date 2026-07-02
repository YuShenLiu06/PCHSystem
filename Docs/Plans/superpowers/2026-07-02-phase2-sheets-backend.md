> **For agentic workers:** 本文件是 superpowers 可执行计划。L 系列（lead 契约冻结）已✅完成（commit `aa072bb`/`1937bdb`）；B 系列由 `backend-dev` teammate 承接，F 系列由 `frontend-dev` teammate 承接，V 系列由 lead 收尾。任务用 `- [ ]` 复选框跟踪。
>
> **日期**：2026-07-02　**来源**：[`MVP-第一阶段计划.md`](../MVP-第一阶段计划.md) Phase 2（后端+存储抽象）+ Phase 3（Web 可编辑界面）。前置：[Phase 0+1 身份登录](./2026-07-01-phase0-1-auth-login.md) 已完成。

# HTCMC PCHSystem · Phase 2 sheets 子服务（在线表格）Implementation Plan

> **执行方式**：**Teammates 并行**（用户确认）。我作 lead 先串行冻结 sheets API 契约（DDL + Pydantic + OpenAPI 桩），再 spawn `backend-dev` + `frontend-dev` 两个命名 teammate 用 SendMessage 并行推进后端 Phase 2 与前端 Phase 3，最后 lead 联调收尾。MCDR `!!sheet`（Phase 4）不在本次，待后端 API 稳定后单独推进。
> **来源**：`Docs/Plans/MVP-第一阶段计划.md` Phase 2（后端+存储抽象）+ Phase 3（Web 可编辑界面）。Phase 0+1（身份登录）已完成。

---

## Context（背景与动机）

MVP 第一阶段 Phase 0+1（UUID token 登录闭环：B1-B16 / M1-M2 / F1-F4）已完成，三端身份链路打通（21 测试全绿，OpenAPI 5 端点冻结）。下一步是落地 MVP 的**核心交付物：双向可编辑在线表格**。

本计划覆盖 **Phase 2（后端 + 存储抽象 + API）+ Phase 3（Web 可编辑表格 UI）**，用 Teammates 并行加速：lead 冻结契约后，后端与前端同时推进。MCDR `!!sheet`（Phase 4）需真实 MC 服务端联调，成本较高，待后端 API 稳定后单独 spawn `mcdr-dev`。

`MVP-第一阶段计划.md` 是高层路线图（含定稿 DDL、`SheetRepository` Protocol、`format_qty`、§7 团队并行策略），但不是可执行粒度。本计划按 superpowers 方法论转为 TDD 任务，复用 auth 子服务（`Backend/app/{api,core,models,repositories,schemas,services}`）与前端脚手架（`Frontend/`，已含 axios/auth store/router/qty.ts 占位）作为现成模板。

**产出**：`sheets` 后端模块（schema + ORM + Repository + CRUD/导出 API + format_qty + 测试）+ Web 表格界面（列表 + el-table 可编辑 + 数量换算 + 备齐标签 + RBAC 可见性），Web 端可端到端验收。

---

## 范围

| 在范围内（本计划） | 不在范围内（后续） |
|---|---|
| `sheets` schema 迁移（2 表）+ ORM | MCDR `!!sheet` 命令（Phase 4，待后端稳定后单独 spawn） |
| `SheetRepository` Protocol + PG 实现 + CSV 导出 | 投影 `.litematic` 解析 / 物品自动识别 |
| 表格 CRUD API（JWT）+ 权限隔离 + CSV 导出（service token） | scoring / title / wiki / alert |
| `format_qty` 纯函数（后端 + 前端 TS 对齐） | MCDR 代玩家写入通道（service token + body.uuid，随 Phase 4） |
| Web 表格 UI：列表 + el-table 可编辑 + 换算 + 备齐标签 + RBAC | 改名过户 / `done_flag` 升级为进度 |
| 文档：superpowers 计划落盘 + `data-model.md` 补 sheets | |

---

## 关键决策（已与用户对齐 ✅）

| # | 决策 | 依据 |
|---|---|---|
| D-1 | `done_flag` = `smallint` 二元 0/1 | MVP §3.2 默认 + §9 验收 |
| D-2 | `item_name` 自由文本，**不**强制 registry id；红线 **R-6 不覆盖** sheets | MVP §10；sheets 与 `projects.material_lists` 两套体系 |
| D-3 | 权限：**JWT 已登录可读所有表**；**表的 `owner_uuid` 或 admin/owner 角色可写**；**CSV 全量导出走 service token** | MVP §4 + §6 + §9 |
| D-4 | `format_qty` 后端纯函数 + 前端 TS 对齐；API **只返回原始 `int`** | MVP §5「永不存换算结果」 |
| D-5 | **并行范围**：后端 + 前端（MCDR 暂缓） | 用户确认（MCDR 联调贵，Web 端优先可见） |
| D-6 | **编排**：Teammates 命名 agent + SendMessage；目录天然隔离不用 worktree | 用户确认 |

---

## 红线与约束（根 CLAUDE.md §3 + Backend/CLAUDE.md + Frontend/CLAUDE.md）

- **R-1** 后端独占 DB；前端/MCDR 只走 HTTP。**R-5** UUID 身份锚（`sheets.sheets.owner_uuid`→`users.players.uuid`）。
- **R-9** 前端权限**仅可见性**：真实权限以后端 RBAC 为准，前端只控编辑控件显隐。
- **R-10 / RS-3** 模块化单体 —— 新 `sheets` router 挂到现有 FastAPI `app`，不拆服务。
- **R-11 / RS-4** 密钥经 `.env`；本阶段复用已有 `JWT_SECRET` / `MCDR_SERVICE_TOKEN`，**无新密钥**。
- **RS-5** 仅 token 类软失效；sheets 用户主动数据 → **硬删除**（DDL `ON DELETE CASCADE`）。
- **Backend 分层**：`api → services + repositories → models`；**commit 在 api 层**，repo 只 `flush()`；并发用 `with_for_update()`；schema 物理隔离；新模型须在 `alembic/env.py` 显式 `import`。
- **前端**：复用 `Frontend/src/utils/http.ts`（axios + Bearer + 401 跳登录）、`stores/auth.ts`、`router/index.ts`（守卫）；新页挂现有 router，不新开脚手架。

---

## 数据模型（DDL，落地版本）

采用 `MVP-第一阶段计划.md:68-88`，类型按 D-1/D-2 确认：

```sql
-- 0004_sheets_schema.py
CREATE SCHEMA IF NOT EXISTS sheets;
CREATE TABLE sheets.sheets (
    id          bigserial PRIMARY KEY,
    owner_uuid  uuid NOT NULL REFERENCES users.players(uuid),
    title       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sheets.sheet_rows (
    id          bigserial PRIMARY KEY,
    sheet_id    bigint NOT NULL REFERENCES sheets.sheets(id) ON DELETE CASCADE,
    item_name   text NOT NULL,                -- 自由文本（D-2）
    need_qty    integer NOT NULL DEFAULT 0,   -- 原始整数（D-4）
    done_flag   smallint NOT NULL DEFAULT 0,  -- 二元 0/1（D-1）
    sort_order  integer NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sheet_id, item_name)
);
CREATE INDEX ix_sheet_rows_sheet_id ON sheets.sheet_rows (sheet_id);
```
迁移须可逆（`downgrade` 逆序 drop + `DROP SCHEMA IF EXISTS sheets`）。

---

## API 契约（OpenAPI 将冻结，前端据此开发）

**Router**：`APIRouter(prefix="/sheets", tags=["sheets"])`（前端 `/api/sheets` → 后端 `/sheets`，同 `/auth`）。

| 方法 | 路径 | 鉴权 | 权限 | 说明 |
|---|---|---|---|---|
| POST | `/sheets` | JWT | 已登录 | 建表 owner=current；`{title}` → 201 `SheetDetail` |
| GET | `/sheets` | JWT | 已登录 | 列表（所有表）；`?owner=me` 过滤 → `SheetSummary[]` |
| GET | `/sheets/{id}` | JWT | 已登录 | 详情含 rows；`?format=csv` → `text/csv` |
| PATCH | `/sheets/{id}` | JWT | owner/admin/owner角色 | 改 title；非 owner → 403 |
| DELETE | `/sheets/{id}` | JWT | owner/admin/owner角色 | 删表级联 rows → 204 |
| PUT | `/sheets/{id}/rows` | JWT | owner/admin/owner角色 | upsert 行（按 `item_name`）→ `RowDetail` |
| DELETE | `/sheets/{id}/rows/{row_id}` | JWT | owner/admin/owner角色 | 删行 → 204 |
| GET | `/sheets/export` | **service token** | 外部 | 全量 CSV → `text/csv` |

**权限 helper**：`_can_edit(sheet, player) = sheet.owner_uuid == player.uuid or player.role in ("admin","owner")`（复用 `deps.py:45-50` 的 owner 隐式超级语义）。

**Pydantic schemas（`app/schemas/sheet.py`）**：`SheetCreateRequest{title:str}`、`SheetPatchRequest{title:str}`、`SheetSummary{id,owner_uuid,title,created_at,updated_at}`、`RowUpsertRequest{item_name, need_qty≥0, done_flag∈{0,1}=0, sort_order=0}`、`RowDetail{id,item_name,need_qty,done_flag,sort_order,updated_at}`、`SheetDetail = SheetSummary + {rows:list[RowDetail]}`。

---

## format_qty 纯函数（`app/core/qty.py` + 前端 `src/utils/qty.ts` 真实现，D-4）

```python
STACK = 64; SHULKER = 27 * 64  # 1728
def format_qty(n: int) -> str:
    if n >= SHULKER: return f"{round(n / SHULKER, 2):g}盒"
    if n >= STACK:   return f"{round(n / STACK, 2):g}组"
    return f"{n}个"
```
前端 `qty.ts` 当前为占位（`ca016f6` 留的 Phase 2 TODO），本计划替换为与上面对齐的 TS 实现。**API 不附带换算字符串**，前端自行调用 `formatQty`。

---

## 执行编排（Teammates 并行，D-5/D-6）

```
Phase 2-A · lead 串行冻结契约（L1→L4）
  L1 0004 迁移 + ORM → L2 Pydantic + sheets 路由桩(501) + 导出 openapi.json
       → L3 落盘 superpowers 计划 → L4 SendMessage spawn 两 teammate（附契约）
                          ↓ 契约 = DDL + Pydantic + openapi.json 冻结
Phase 2-B · 两 teammate 并行（同一消息 spawn，后台跑）
  backend-dev  (Backend/) : B1 format_qty · B2 Repository · B3 API+鉴权+测试
  frontend-dev (Frontend/): F1 API 客户端 · F2 qty.ts · F3 List · F4 Editor · F5 路由 · F6 vitest
                          ↓
Phase 2-C · lead 联调收尾（V1→V4）
  V1 后端单测+迁移 · V2 前端 build+vitest · V3 端到端 curl+Web · V4 文档/CHANGELOG/HANDOFF
```

- **隔离**：两 teammate 改不同顶层目录（`Backend/` vs `Frontend/`），同工作树不冲突，不用 worktree。
- **协调**：lead 用 SendMessage 派任务、收结果、review；契约冻结后路径/形状不变，无需中途同步；遇阻塞 teammate 回报，lead 调度。
- **MCDR**：本计划不含；API 稳定后单独 spawn `mcdr-dev` 做 Phase 4。

---

## 任务分解（TDD，L/B/F/V 四系列）

### L 系列（lead 串行 · 契约冻结）

**L1：`0004` 迁移 + ORM**（参考 `auth 计划 B5/B9`）
Files: `Backend/alembic/versions/0004_sheets_schema.py`, `Backend/app/models/sheet.py`, mod `Backend/alembic/env.py`
- [ ] `Sheet`/`SheetRow` ORM（镜像 `app/models/user.py:11-68`：`PG_UUID(as_uuid=True)`、`Mapped[]`、`DateTime(timezone=True)+server_default=text("now()")`、`__table_args__={"schema":"sheets"}`、FK `"users.players.uuid"`）
- [ ] `0004` 迁移（`revision="0004_sheets"`、`down_revision="0003_auth_tokens_revoked_at"`；建 schema + 两表 + UNIQUE + `ix_sheet_rows_sheet_id`；可逆 downgrade）
- [ ] `env.py` 加 `from app.models import sheet  # noqa: F401`
- [ ] `.venv/bin/alembic upgrade head` → `0004_sheets (head)`；`downgrade -1`+`upgrade` 可逆；`psql \d sheets.sheet_rows` 验列/约束
- [ ] Commit：`feat(backend): 添加 sheets schema 与 ORM 模型`

**L2：Pydantic schemas + sheets 路由桩 + 冻结 OpenAPI**
Files: `Backend/app/schemas/sheet.py`, `Backend/app/api/sheets.py`(桩), mod `Backend/app/main.py`, `Backend/openapi.json`, mod `Backend/tests/test_openapi_freeze.py`
- [ ] 写 `schemas/sheet.py`（上文 6 个模型，`Field(ge=0,le=1)` 等校验）
- [ ] 写 `api/sheets.py` 桩：全端点签名 + `response_model` 齐全，body `raise HTTPException(501, "not implemented")`；`_can_edit` helper 先落地
- [ ] `main.py` 挂 `include_router(sheets_router)`
- [ ] `test_openapi_freeze.py:1848` 追加 `/sheets` 系列路径断言
- [ ] 重新导出 `openapi.json`（`auth 计划 B16 Step3` 同款命令）
- [ ] Commit：`feat(backend): 冻结 sheets API 契约（Pydantic+OpenAPI 桩）`

**L3：落盘 superpowers 计划** —— 本文件 → `Docs/Plans/superpowers/2026-07-02-phase2-sheets-backend.md`（补 `> For agentic workers` 头注）。Commit：`docs: 落盘 Phase 2+3 superpowers 计划`

**L4：spawn 双 teammate**（一条消息两个 Agent 调用，后台并行）—— 给每个 teammate 传：计划路径 + 其负责的 L/B/F 系列 + openapi.json 契约 + 红线摘要 + 提交规范。

### B 系列（backend-dev · 并行）

**B1：`format_qty` + 测试** Files: `Backend/app/core/qty.py`, `tests/test_qty.py`
- [ ] RED 覆盖：`3456→"2盒"`、`2000→"1.16盒"`、`1728→"1盒"`、`192→"3组"`、`64→"1组"`、`100→"1.56组"`、`63→"63个"`、`0→"0个"`
- [ ] GREEN 实现；`pytest tests/test_qty.py -v` 通过；Commit `feat(backend): 添加 format_qty 纯函数`

**B2：`SheetRepository` + 测试** Files: `Backend/app/repositories/sheet_repo.py`, `tests/test_sheet_repo.py`
- [ ] RED 覆盖 MVP §4 全 8 方法：`create_sheet`/`get_sheet`(含 None)/`list_sheets`(含 owner 过滤)/`list_rows`/`upsert_row`(新建+同名更新)/`delete_row`/`delete_sheet`(级联)/`export_csv`/`export_all_csv`；seed `users.players` 行作 owner
- [ ] GREEN 函数式实现（镜像 `auth_token_repo.py` 的 `select`/`with_for_update`/`flush`）；`upsert_row` 用「先 `select...with_for_update()` 在则改 / 不在则 insert」，并发同名 insert 触发 `IntegrityError` 上抛
- [ ] 通过；Commit `feat(backend): 添加 SheetRepository 与 PG 实现`

**B3：sheets API 真实实现 + 鉴权 + 测试** Files: mod `Backend/app/api/sheets.py`(桩→真), `tests/test_sheets_api.py`, mod `tests/conftest.py`, re-export `openapi.json`
- [ ] RED：`test_sheets_api.py` 覆盖 API 契约表全路径 + 权限分支（建表/列表含他人表/详情/CSV 单表/改标题 owner✓&非 owner 403&admin✓/删表级联/upsert 新建+更新/删行/export 全量 service token/未登录 401）；复用 `test_auth_api.py:14-17` 的 `_svc_token` fixture
- [ ] GREEN：桩替换为真实实现（调 repo、`_can_edit`、`IntegrityError→409`、commit）；`conftest.py:23-31` truncate 追加 `sheets.sheet_rows, sheets.sheets`；重新导出 `openapi.json`
- [ ] `pytest -q` 全绿；Commit `feat(backend): 实现 sheets CRUD API 与 CSV 导出`

### F 系列（frontend-dev · 并行 · Phase 3）

**F1：API 客户端** Files: `Frontend/src/api/sheets.ts`
- [ ] 基于 `Backend/openapi.json` 手写 TS 类型（`Sheet`/`SheetSummary`/`Row`/请求体）+ axios 封装（复用 `utils/http.ts`）：`listSheets/getSheet/createSheet/patchSheet/deleteSheet/upsertRow/deleteRow/exportSheetCSV/exportAllCSV`
- [ ] Commit `feat(frontend): 添加 sheets API 客户端`

**F2：`qty.ts` 真实现** Files: `Frontend/src/utils/qty.ts`
- [ ] 替换占位为与后端对齐的 `formatQty(n)`（STACK=64/SHULKER=1728，`:g` 等价去尾零）
- [ ] vitest 覆盖 B1 同款边界；Commit `feat(frontend): 实现 formatQty 数量换算`

**F3：`SheetList.vue`（列表页）** Files: `Frontend/src/views/sheets/SheetList.vue`
- [ ] `GET /sheets` 列表（el-table：标题/owner/更新时间/行数）；「新建」按钮 → `POST /sheets`（el-dialog 输入 title）；点行进 `/sheets/:id`
- [ ] Commit `feat(frontend): 添加表格列表页`

**F4：`SheetEditor.vue`（el-table 可编辑）** Files: `Frontend/src/views/sheets/SheetEditor.vue`
- [ ] `GET /sheets/{id}` → el-table 显示 rows；可编辑单元格（item_name/need_qty/sort_order）；数量换算列（`formatQty`）；备齐 toggle（done_flag 0/1，el-tag 颜色：绿=已备齐/灰=未）；增行（`PUT upsert`）/删行（`DELETE`）；改标题（`PATCH`）；删表（`DELETE`，二次确认）
- [ ] RBAC 可见性（R-9）：非 `_canEdit`（owner/admin）隐藏编辑/删除/增行控件，只读
- [ ] Commit `feat(frontend): 添加可编辑表格页`

**F5：路由 + 导航** Files: mod `Frontend/src/router/index.ts`, `App.vue`(导航)
- [ ] 加 `/sheets`（列表）、`/sheets/:id`（编辑）路由（`requiresAuth`）；`App.vue` 加入口链接
- [ ] Commit `feat(frontend): 接入 sheets 路由与导航`

**F6：vitest** Files: `Frontend/src/utils/__tests__/qty.spec.ts` 等
- [ ] `npx vitest run` 通过；Commit `test(frontend): 添加 qty 与 sheets 客户端单测`

### V 系列（lead 联调收尾）

**V1：后端验收** —— `cd Backend && JWT_SECRET=test .venv/bin/pytest -q` 全绿；`alembic current`→`0004_sheets (head)`；`downgrade -1`+`upgrade` 可逆。
**V2：前端验收** —— `cd Frontend && npm run build` 通过；`npx vitest run` 通过。
**V3：端到端**（`docker compose up -d` + `alembic upgrade head`）：service token 经 `/auth/token`+`/auth/exchange` 拿 JWT → Web 新建表 → 加行 `iron_ingot/192` 显示「192 (3组)」→ 标备齐变绿 → `curl /sheets/{id}?format=csv` → 非 owner PATCH→403 → `curl /sheets/export`（service token）→ CSV。
**V4：文档** —— `Docs/architecture/data-model.md` 补 sheets 章节；`CHANGELOG.md`（Backend/Frontend Unreleased）；`Docs/Plans/HANDOFF.md` 加 Phase 2+3 完成状态。Commit `docs: Phase 2+3 sheets 验收与 CHANGELOG 更新`。

---

## Verification（验证总结）

| 验证项 | 命令/动作 | 期望 |
|---|---|---|
| 后端全量单测 | `cd Backend && JWT_SECRET=test .venv/bin/pytest -q` | 全 PASS（原 21 + 新 ~25） |
| 迁移 | `.venv/bin/alembic current` | `0004_sheets (head)` |
| 迁移可逆 | `alembic downgrade -1` → `upgrade head` | 无报错 |
| OpenAPI 契约 | `GET /openapi.json` | 含 `/sheets` 全路径 |
| 前端构建 | `cd Frontend && npm run build` | 通过 |
| 前端单测 | `cd Frontend && npx vitest run` | 通过 |
| Web 建表+加行 | JWT → `POST /sheets` → `PUT /rows` → Web 显示 | 换算「192 (3组)」正确 |
| CSV 单表/全量 | `GET /sheets/{id}?format=csv`(JWT) / `GET /sheets/export`(service token) | `text/csv` |
| 权限隔离 | 非 owner `PATCH /sheets/{id}` / Web 隐藏编辑控件 | 403 / 控件隐藏 |
| upsert 语义 | 同 `item_name` 二次 `PUT` | 更新非报错 |

---

## Self-Review（自查）

1. **Spec 覆盖**：MVP §3.2 DDL→L1；§4 SheetRepository 8 方法→B2（含 export_csv/export_all_csv）；§5 format_qty→B1+F2；§6 Phase 2「CRUD+权限」→L2/B3；§6 Phase 3「el-table+换算+备齐+RBAC」→F3/F4；§4 外部统一可读→`GET /sheets/export`(service token)+`?format=csv`；§7 团队并行→L/B/F/V 编排。
2. **红线**：R-1/R-5/R-9/R-10/R-11 + RS-3/RS-4/RS-5 全落实；sheets 硬删除合理（RS-5 不误伤）；新 schema 物理隔离；新模型 `env.py` 注册；FK 全限定；前端只控可见性。
3. **并行可行性**：L 系列是 B/F 的硬前置（契约必须先冻结）；B 与 F 改不同目录无冲突；openapi.json 由 lead 冻结、B3 收尾重新导出，F1 基于冻结形状手写类型，不依赖文件实时同步。
4. **范围克制**：MCDR 写通道（service token + body.uuid）与 `!!sheet` 留给 Phase 4；本阶段 service token 仅做只读导出。
5. **无占位**：所有 Task 有可验证产出，L2 的 501 桩是契约冻结手段（B3 必替换为真实实现，V1 验收会捕获遗漏）。

---

## 执行交接（ExitPlanMode 批准后）

1. **落盘** L3：本文件 → `Docs/Plans/superpowers/2026-07-02-phase2-sheets-backend.md`。
2. **分支**：`git checkout -b feat/sheets`（本地 `main` 当前领先 `origin/feat/backend-phase0-foundation` 22 提交，Phase 0+1 未发版；两端同一分支，目录隔离不冲突）。
3. **L 系列 lead 直接执行**（L1→L2→L3 串行，每步 commit）。
4. **L4 spawn**：一条消息两个 `Agent` 调用（`name: backend-dev` / `name: frontend-dev`，`run_in_background: true`），分别传 B 系列 / F 系列 任务包；我用 SendMessage 派子任务、收结果、review（纯模型/迁移类单 review，API/UI 类加 code quality review，沿用 HANDOFF.md:97-99）。
5. **V 系列 lead 收尾**；V4 完成后纳入首次发版候选（`backend-v0.2.0` / `frontend-v0.2.0`）。
