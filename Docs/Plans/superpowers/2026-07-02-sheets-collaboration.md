# sheets 协作改进 · 实现计划（点1 名称显示 + 点2 认领/进度协作）

> **执行方式**：本计划分 **后端流（B）** 与 **前端流（F）** 两条独立流，契约由 spec（`Docs/superpowers/specs/2026-07-02-sheets-collaboration-design.md`）单点定义，两流互不依赖，可用两个 Teammate 并行执行；每流内部按 TDD 顺序、频繁 commit。文档流（D）放最后。
> 当前分支：`feat/sheets`。所有 commit 走 Conventional Commits（feat/fix/docs/test），中文描述。

---

## Context（为什么做）

Phase 2+3 sheets 已落地（commit `b4d37f4`），用户 Web 试用后提两点改进：

1. **点1（UX）**：表格所有者目前显示 UUID（`SheetEditor.vue:226` 灰字、`SheetList.vue:71` 列），对人不友好 → 改显游戏名。
2. **点2（功能）**：当前只有拥有者能改、`done_flag` 二元、无协作。落地 `guied.md §三` 协作语义：**非拥有者可认领（锁定）某物品去备货、标记已备齐；拥有者可解除锁定、打回提交**。

完整决策（D-1~D-10，已与用户对齐）见 spec §3。要点：单认领人、拥有者每行手选 mode（lock/progress）、3 态状态机（open/claimed/done）、认领信息上墙 `sheet_rows`（YAGNI，多人时再迁子表）。

**目标产出**：迁移 `0005`、4 个新端点（claim/delivery/release/reject）、`owner_name`/`claimant_name` 显示、前端协作 UI、后端 API 文档、全链路测试绿。

---

## 契约（spec 已定，两流共同遵守）

**`SheetRow` 字段（去 `done_flag`，加 4 列）**：`mode: int(0=lock,1=progress)`、`status: str(open|claimed|done)`、`claimant_uuid: UUID|null`、`delivered_qty: int>=0`。

**3 态状态机**：
```
open --claim(任意登录玩家)--> claimed --set_delivery(>=need, 认领人)--> done
done --reject(拥有者, delivered=0, 认领人不变)--> claimed
claimed|done --release(认领人自放/拥有者, 清claimant+delivered)--> open
```
不变量：`open ⇒ claimant IS NULL ∧ delivered=0`；`claimed ⇒ claimant NOT NULL`；`done ⇒ claimant NOT NULL ∧ delivered>=need`。

**响应字段**：`SheetSummary`/`SheetDetail` 增 `owner_name:str`；`RowDetail` 去掉 `done_flag`，增 `mode/status/claimant_uuid/claimant_name/delivered_qty`。`RowUpsertRequest` 去 `done_flag`、增 `mode`。

**错误码**：非法状态转移 → 409；权限不足 → 403；行/表不存在 → 404。

---

## 后端流（Backend）

### Task B1：迁移 `0005_sheets_collaboration`（可逆）

**Files:** Create `Backend/alembic/versions/0005_sheets_collaboration.py`

`revision="0005_sheets_collab"`, `down_revision="0004_sheets"`。

- [ ] upgrade：对 `sheets.sheet_rows` 加 4 列（`mode smallint NOT NULL DEFAULT 0`、`status text NOT NULL DEFAULT 'open'`、`claimant_uuid uuid FK→users.players.uuid NULL`、`delivered_qty int NOT NULL DEFAULT 0`）→ `UPDATE ... SET status='done' WHERE done_flag=1` → `DROP COLUMN done_flag` → `CREATE INDEX ix_sheet_rows_sheet_status ON sheets.sheet_rows (sheet_id, status)`。
- [ ] downgrade（逆序可逆）：drop index → 加回 `done_flag smallint NOT NULL DEFAULT 0` → `UPDATE SET done_flag=1 WHERE status='done'` → drop 4 个新列（PG 自动级联删 claimant 的 FK）。
- [ ] 验证：`docker compose exec backend alembic upgrade head` 无报错；`alembic downgrade -1` 再 `upgrade head` 往返成功。
- [ ] commit：`feat(backend): 迁移 0005 sheets 行协作字段（mode/status/claimant/delivered）`

> `server_default` 文本列用 `sa.text("'open'")`（匹配 0004 的 `sa.text("now()")`/`sa.text("0")` 风格）。

### Task B2：ORM 模型同步

**Files:** Modify `Backend/app/models/sheet.py`

- [ ] `SheetRow`：删 `done_flag` 列；加 `mode`（SmallInteger, default=0, server_default `text("0")`）、`status`（Text, default="open", server_default `text("'open'")`）、`claimant_uuid`（PG_UUID, ForeignKey→`users.players.uuid`, nullable=True）、`delivered_qty`（Integer, default=0, server_default `text("0")`）。同步更新类 docstring。

### Task B3：Pydantic schemas

**Files:** Modify `Backend/app/schemas/sheet.py`

- [ ] `RowUpsertRequest`：删 `done_flag`，加 `mode: int = Field(default=0, ge=0, le=1)`。
- [ ] `RowDetail`：删 `done_flag`，加 `mode:int`、`status:str`、`claimant_uuid: UUID | None`、`claimant_name: str | None`、`delivered_qty: int`。
- [ ] `SheetSummary`：加 `owner_name: str`（`SheetDetail` 继承之）。

### Task B4：repo 改造（join 取名 + 状态机 + CSV）

**Files:** Modify `Backend/app/repositories/sheet_repo.py`

- [ ] 顶部加常量与异常：
```python
MODE_LOCK, MODE_PROGRESS = 0, 1
STATUS_OPEN, STATUS_CLAIMED, STATUS_DONE = "open", "claimed", "done"

class SheetRowConflict(Exception):
    """行状态非法转移/不变量违反，api 层翻译为 409。"""
```
并 `from app.models.user import Player`。
- [ ] `list_sheets` → 返回 `list[tuple[Sheet, str]]`，inner join `Player` on `owner_uuid` 取 `current_name`。
- [ ] `get_sheet` → 返回 `tuple[Sheet, str] | None`（同上 join）。
- [ ] `list_rows` → 返回 `list[tuple[SheetRow, str | None]]`，**left** join `Player` on `claimant_uuid`。
- [ ] 新增 `get_row(session, sheet_id, row_id) -> tuple[SheetRow, str|None] | None`（left join，给端点构造单行响应/权限判断用）。
- [ ] `upsert_row` 签名改 `(session, sheet_id, item_name, need_qty, mode, sort_order)`：
  - 新建行：`status=STATUS_OPEN, claimant_uuid=None, delivered_qty=0`。
  - 更新行：仅改 `need_qty/mode/sort_order`，**保留** status/claimant/delivered；并按 spec §5.3 封顶：`if delivered>new_need: delivered=new_need`；`if status in (claimed,done) and delivered>=need: status=done`；`elif status==done and delivered<need: status=claimed`。
- [ ] 新状态机函数（均 `select(...).with_for_update()` 锁行后判定+改，返回 SheetRow；行不存在返回 None；非法转移 raise `SheetRowConflict`）：
  - `claim_row(session, sheet_id, row_id, claimant_uuid)`：需 `status==open`；置 claimed/claimant/delivered=0。
  - `set_row_delivery(session, sheet_id, row_id, delivered_qty)`：需 `status in (claimed,done)`；`delivered=delivered_qty`；`status = done if delivered>=need else claimed`。
  - `release_row(session, sheet_id, row_id)`：需 `status in (claimed,done)`；置 open/claimant=None/delivered=0。
  - `reject_row(session, sheet_id, row_id)`：需 `status==done`；置 claimed/delivered=0（claimant 不变）。
- [ ] CSV：`_CSV_HEADER = ["sheet_id","item_name","need_qty","mode","status","claimant_uuid","delivered_qty","sort_order"]`；`_row_to_csv_record` 同步输出新列（claimant_uuid 为 None 时输出空串）。

### Task B5：api 改造（4 新端点 + 响应/权限 + owner_name）

**Files:** Modify `Backend/app/api/sheets.py`

- [ ] helpers 改签名：`_to_summary(sheet, owner_name)`、`_to_detail(sheet, rows_with_names, owner_name)`、`_row_dict(row, claimant_name=None)`（输出含 mode/status/claimant_uuid/claimant_name/delivered_qty，无 done_flag）。
- [ ] `create_sheet`：owner_name 取 `player.current_name`，`_to_detail(sheet, [], player.current_name)`。
- [ ] `list_sheets`/`get_sheet`/`patch_sheet`/`delete_sheet`：解包 repo 返回的 `(sheet, owner_name)`；detail 的 rows 用 `list_rows` 返回的 `(row, name)` 元组。
- [ ] `upsert_row`：去 `done_flag`，传 `mode=body.mode`；成功后 `session.refresh(row)` → `RowDetail(**_row_dict(row, None))`（upsert 不碰 claimant，name 恒 None）。
- [ ] 新增 4 端点（均先 `_load_sheet_or_404` 拿 sheet 判权限/404；transition 返回 None→404 row；`SheetRowConflict`→rollback+409；commit 后用 `get_row` 取 `(row,name)` 构造响应）：
  - `POST /sheets/{sid}/rows/{rid}/claim`：任意登录玩家（无额外权限）→ `claim_row(..., player.uuid)`。
  - `PATCH /sheets/{sid}/rows/{rid}/delivery`：body `{"delivered_qty": int>=0}` → 先 `get_row` 读行，校验 `row.claimant_uuid==player.uuid` 否则 403 → `set_row_delivery`。
  - `POST /sheets/{sid}/rows/{rid}/release`：`get_row` 读行，校验 `row.claimant_uuid==player.uuid or _can_edit(sheet,player)` 否则 403 → `release_row`。
  - `POST /sheets/{sid}/rows/{rid}/reject`：校验 `_can_edit(sheet,player)` 否则 403 → `reject_row`。
- [ ] `delivery` 新增请求 schema `RowDeliveryRequest { delivered_qty: int = Field(ge=0) }`（加在 `schemas/sheet.py`）。

> 分层红线：transition 逻辑+`with_for_update` 在 repo；权限判定+commit 在 api；并发由 repo 行锁兜底（api 的读校验是 best-effort，竞态由 repo 再校验 raise 409）。

### Task B6：后端测试（TDD — 与 B1-B5 交替，先红后绿）

**Files:** Modify `Backend/tests/test_sheet_repo.py`、`Backend/tests/test_sheets_api.py`、`Backend/tests/test_openapi_freeze.py`、`Backend/openapi.json`

- [ ] 全局：`done_flag` 字面量/字段在两测试文件中需替换为新契约（grep 已列出全部 9 处 repo + 13 处 api 行）；CSV header 断言（5 处）改为新 8 列。
- [ ] repo 测试新增：claim 开→claimed、对 claimed 行 claim→`SheetRowConflict`、set_delivery `<need`→claimed / `>=need`→done、release claimed/done→open（claimant+delivered 清零）、reject done→claimed（认领人保留 delivered=0）、reject 非 done→冲突、upsert 改 need 时 delivered 封顶+status 自动 done/回落。
- [ ] api 测试新增（沿用 `_make_player`+`_auth`+`_svc_token` 模式）：claim 任意玩家成功；非认领人 delivery→403；对 done 行 claim→409；release 认领人自放成功；release 拥有者成功；reject 非拥有者→403；reject 非 done→409；owner_name 在 list/detail 出现且等于 `current_name`；RowDetail 含新字段无 done_flag。
- [ ] `test_openapi_freeze.py`：断言 path 列表追加 4 个新路径。
- [ ] 重导出 `Backend/openapi.json`：`cd Backend && python -c "import json;from app.main import create_app;open('openapi.json','w').write(json.dumps(create_app().openapi(),ensure_ascii=False))"`（committed 工件，无 diff 测试，保持同步）。
- [ ] 验证：`cd Backend && pytest tests/ -v` 全绿。
- [ ] commit：`test(backend): sheets 协作状态机/权限/名称显示用例 + openapi 同步`（可与 B5 合并提交）。

---

## 前端流（Frontend，与后端流独立）

### Task F1：API 客户端类型 + 新函数

**Files:** Modify `Frontend/src/api/sheets.ts`

- [ ] `SheetSummary` 加 `owner_name: string`；`RowDetail` 去 `done_flag`，加 `mode/status/claimant_uuid/claimant_name/delivered_qty`；`RowUpsertRequest` 去 `done_flag`，加 `mode?: number`。
- [ ] 新增：`claimRow(id,rowId)`→`POST /sheets/{id}/rows/{rowId}/claim`；`setRowDelivery(id,rowId,deliveredQty)`→`PATCH .../delivery`；`releaseRow(id,rowId)`→`POST .../release`；`rejectRow(id,rowId)`→`POST .../reject`。

### Task F2：列表显名

**Files:** Modify `Frontend/src/views/sheets/SheetList.vue:71`

- [ ] 列 `prop="owner_uuid" label="所有者 UUID"` → `prop="owner_name" label="所有者"`，宽度收窄。

### Task F3：详情页协作 UI

**Files:** Modify `Frontend/src/views/sheets/SheetEditor.vue`

- [ ] 头部 `:226` 所有者改 `sheet.owner_name`。
- [ ] `newRow`（:26-31）与 `rowDrafts`（:38）：`done_flag` → `mode`（默认 0=lock）。
- [ ] `canEdit` 保留（owner/admin）；新增 `isClaimant(row) = auth.player?.uuid === row.claimant_uuid`。
- [ ] `onAddRow`/`onSaveRow`：body 用 `mode` 替换 `done_flag`。
- [ ] 删 `onToggleDone`（:149-163）；新增 `onClaim/onSetDelivery/onRelease/onReject`（调 F1 函数后 `fetchSheet` 刷新；progress 模式上报交付用 `ElMessageBox.prompt` 输入数量）。
- [ ] 表格列重构（按 spec §5.5，R-9 仅可见性）：
  - 物品名/需要数量/换算（`formatQty`）/排序：owner 可编辑（保留）。
  - 新增 **模式** 列：owner 下拉 lock/progress（upsert mode）。
  - 新增 **认领者** 列：显 `claimant_name`，open 显「—」。
  - 新增 **状态** 列：el-tag（open 灰/claimed 蓝/done 绿）。
  - 新增 **交付进度** 列：progress 模式显 `delivered/need`+el-progress；lock 模式隐。
  - 动作列按角色×状态渲染：任意玩家×open→「认领」；认领人×claimed→ lock「标备齐」「放弃」/ progress「上报交付」「放弃」；认领人×done→「取消备齐」；owner×claimed/done→「解除锁定」；owner×done→「打回」。非 owner 非 claimant 旁观者只读。
- [ ] 保持精简（Frontend RS-1）：用最少 Element Plus 组件，不打磨视觉。

### Task F4：前端测试

**Files:** Modify `Frontend/src/api/__tests__/sheets.spec.ts`

- [ ] 现有 fixture/断言里的 `done_flag`/`owner_uuid` 调整为新契约（owner_name、RowDetail 新字段）。
- [ ] 新增 4 个 API 函数的「被以正确参数调用」断言（claimRow/releaseRow/rejectRow 无 body；setRowDelivery 带 `{delivered_qty}`）。
- [ ] 验证：`cd Frontend && npm run test -- --run` 全绿；`npm run build`（vue-tsc）通过。
- [ ] commit：`feat(frontend): sheets 认领/进度协作 UI 与名称显示` + `test(frontend): 覆盖 sheets 协作新端点`。

---

## 文档流（D，实现完成后）

### Task D1：后端 API 文档

**Files:** Create `Docs/architecture/api/sheets.md`（新目录）

- [ ] 内容：概述 + 鉴权（JWT vs service token）+ 全部 sheets 端点表（method/path/auth/request body/response/status codes），含本次 4 个新端点与状态机图、错误码（403/404/409）、CSV 列说明。源据 `openapi.json` + `app/api/sheets.py`。

### Task D2：索引与变更记录

**Files:** Modify `Docs/Plans/HANDOFF.md`、`Docs/Plans/CHANGELOG.md`（若存在）、根 `CLAUDE.md` §5 文档索引

- [ ] HANDOFF/CHANGELOG 追加本次 sheets 协作改进条目；根 CLAUDE.md §5 加一行指向 `Docs/architecture/api/sheets.md`。
- [ ] commit：`docs: 新增后端 sheets API 文档 + 索引/变更记录`

---

## 验证（端到端）

1. **迁移往返**：`alembic upgrade head` → `alembic downgrade -1` → `upgrade head` 无报错。
2. **后端单测**：`cd Backend && pytest tests/ -v` 全绿（覆盖状态机全分支 + 403/404/409 + owner_name）。
3. **OpenAPI**：`test_openapi_freeze.py` 含 4 新路径；`openapi.json` 已重导出。
4. **前端**：`cd Frontend && npm run test -- --run` 全绿；`npm run build` 通过。
5. **端到端（Web 手测，spec §7）**：A 建表加行 → B（非拥有者）认领 → B 标备齐(done) → A 打回(claimed) → B 再备齐 → A 解除锁定(open) → 进度模式行上报交付；全程状态 tag/进度条正确；列表/详情显游戏名不显 UUID；CSV 含新列。

---

## Teammates 执行拆分（推荐）

- **Teammate-Backend**：B1→B2→B3→B4→B5→B6（TDD，频繁 commit）。
- **Teammate-Frontend**：F1→F2→F3→F4（与后端流并行，契约由 spec 锁定，无文件交叉）。
- 两流完成且各自测试绿后，由主控执行 D1/D2 + 端到端手测验证。
- 因两流改不同目录（`Backend/` vs `Frontend/`）、无共同修改文件，并行安全；`openapi.json` 仅后端写。
