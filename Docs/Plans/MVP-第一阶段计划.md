# HTCMC PCHSystem 第一阶段 MVP 计划

> **状态**：规划稿。鉴权方案定稿前，身份流程的**进服环节**暂停细化；**表格部分**可先行。
> **日期**：2026-07-01
> **配套**：进服鉴权（防冒名）见 [`无感鉴权方案讨论.md`](./无感鉴权方案讨论.md)

---

## 1. Context（背景与动机）

第一阶段不走原架构的「Web 账号主锚 + 绑定」体系，而是做**大幅简化的身份模型 + 双向可编辑表格**，尽快跑通三端联动闭环。

- **身份简化**：游戏内 UUID 为唯一凭证，**不要 Web 账号、不要密码注册、不要绑定确认**。登录走「游戏内 `!!login` → 带 token 的 URL → 浏览器 → JWT」。
- **UUID 获取**：复用 MCDR 插件 [`MCDR_uuid_api_remake`](https://github.com/gubaiovo/MCDR_uuid_api_remake)（`import uuid_api_remake; uuid_api_remake.get_uuid(name)`）。
- **表格为核心交付物**：固定列清单表（物品名称 / 所需数量自动换算个·组·盒 / 是否备齐 0~1），支持 Web + 游戏内双向编辑。
- **不做**：投影解析、物品自动识别、scoring/title/wiki。

**预期产出**：玩家游戏内 `!!login` → 点 URL → Web 自动登录 → 新建 / 编辑表格 → 游戏内 `!!sheet` 命令也能改同一张表 → 两端数据一致 → 表格可被外部经 CSV/JSON 抽象层读取。

---

## 2. 第一阶段范围

| 在范围内 | 不在范围内（后续阶段） |
|---|---|
| UUID token 登录（MCDR + 后端 + 前端） | Web 账号注册 / 密码 |
| 固定列表格 CRUD（物品名 / 数量 / 备齐） | 投影 `.litematic` 解析 |
| 数量换算显示（个/组/盒） | 物品自动识别 / `!!submit` 扫描 |
| Web 端可编辑表格（el-table） | scoring 积分引擎 |
| 游戏内 `!!sheet` 命令编辑 | title / alert / wiki |
| 存储抽象层（Repository + CSV 导出） | 改名过户 |

> ⚠️ **进服防冒名鉴权**（EasyAuth / McGatekeeper / PSK）见 [鉴权方案文档](./无感鉴权方案讨论.md)，待定稿。本文档假设「进服身份已可信」后的 token 登录与表格流程。

---

## 3. 数据模型

### 3.1 身份（`users` schema）
```sql
CREATE TABLE users.players (
    uuid            uuid PRIMARY KEY,          -- MCDR 上报（uuid_api_remake 产出）
    current_name    text NOT NULL,
    role            text NOT NULL DEFAULT 'user',  -- user / admin / owner
    whitelist_state text NOT NULL DEFAULT 'active',  -- active/under_review/removed；登录前置校验
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users.auth_tokens (
    token           uuid PRIMARY KEY,          -- 随机 uuid4
    player_uuid     uuid NOT NULL REFERENCES users.players(uuid),
    expires_at      timestamptz NOT NULL,      -- 短有效期 10 分钟
    used_at         timestamptz,               -- 标记已兑换（一次性，防重放）
    issued_ip       text,                      -- MCDR 签发时来源 IP（审计）
    exchanged_ip    text,                      -- Web 兑换时 IP（审计）
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users.jwt_revocations (
    jti             uuid PRIMARY KEY,
    player_uuid     uuid NOT NULL REFERENCES users.players(uuid),
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz NOT NULL DEFAULT now()
);
```

### 3.2 表格（`sheets` schema）
```sql
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
    item_name   text NOT NULL,
    need_qty    integer NOT NULL DEFAULT 0,    -- 原始整数
    done_flag   smallint NOT NULL DEFAULT 0,   -- 0~1（0 未备齐 / 1 已备齐）
    sort_order  integer NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sheet_id, item_name)
);
```

---

## 4. 存储抽象层（硬约束：外部统一可读）

`SheetRepository` 协议，PG 为主实现，CSV 为外部读取格式。未来 scoring/project 必须经此层读表格。

```python
class SheetRepository(Protocol):
    def list_sheets(self, owner_uuid: UUID | None = None) -> list[Sheet]: ...
    def get_sheet(self, sheet_id: int) -> Sheet | None: ...
    def create_sheet(self, owner_uuid: UUID, title: str) -> Sheet: ...
    def list_rows(self, sheet_id: int) -> list[Row]: ...
    def upsert_row(self, sheet_id: int, row: Row) -> Row: ...
    def delete_row(self, sheet_id: int, row_id: int) -> None: ...
    def export_csv(self, sheet_id: int) -> str: ...      # 外部读取
    def export_all_csv(self) -> str: ...
```

外部读取途径：`GET /api/sheets/{id}?format=csv`、`GET /api/sheets/export?format=csv`、或直连 PG 查 `sheets.sheet_rows`。

---

## 5. 数量换算（显示层，不存换算结果）

**按数量范围自动切换为单一最合适单位**（个/组/盒三选一，不复合）：

```python
STACK = 64          # 组
SHULKER = 27 * 64   # 盒 = 潜影盒 = 1728

def format_qty(n: int) -> str:
    """3456→'2盒'；2000→'1.16盒'；192→'3组'；100→'1.56组'；63→'63个'"""
    if n >= SHULKER:  return f"{round(n / SHULKER, 2):g}盒"
    if n >= STACK:    return f"{round(n / STACK, 2):g}组"
    return f"{n}个"
```

前端 + MCDR 回显都调用此逻辑。**存原始 int，永不存换算结果。**

---

## 6. 实施路线图

### Phase 0：工程地基（约 0.5 周）
Docker Compose（postgres + backend）+ FastAPI 单体骨架 + Alembic（users/sheets schema）+ 双鉴权中间件（`X-Service-Token` + JWT + `require_role`）。**验证**：容器 up，`/docs` 可访问。

### Phase 1：身份 — token 登录链路（约 3-5 天）
- **后端**：`/auth/token`（MCDR 调，生成 token URL）、`/auth/exchange`（Web 兑换 JWT）、`/me`
- **MCDR**：`!!login`（`uuid_api_remake` 取 UUID → 调 `/auth/token` → 聊天框回显可点击 URL）
- **前端**：`/auth?token=xxx` 路由自动兑换 JWT

> ⚠️ MCDR API 与可点击 URL 文本组件须**联网核实**（红线 S-1）。
> ⚠️ 进服防冒名（见鉴权方案）定稿后，在此 Phase 接入。

### Phase 2：表格 — 后端 + 存储抽象（约 4-5 天）
`SheetRepository` 协议 + PG 实现 + CSV 编解码 + 表格 CRUD API + 权限（owner/admin 编辑）。

### Phase 3：表格 — Web 端可编辑界面（约 4-5 天，与 Phase 2 后半并行）
el-table 可编辑单元格 + 数量换算显示 + 备齐颜色标签 + RBAC 可见性。

### Phase 4：表格 — MCDR 游戏内命令（约 3-5 天）
`!!sheet list/new/show/add/set/del`，复用 `format_qty` 回显。

### Phase 5：三端联动验收
`!!login` 登录 → Web 新建表 → 游戏内加行 → Web 改数 → 游戏内标备齐 → CSV 导出。

---

## 7. 团队并行实施策略（Teammates 模式加速）

三端经 HTTP 契约解耦，按「端」切分，3 个 teammate 并行：

| Teammate | 工作目录 | 职责 |
|---|---|---|
| **backend-dev** | `Backend/` | 身份 API + 表格 API + Repository + 换算 |
| **mcdr-dev** | `McdrPlugin/` | `!!login` + `!!sheet` 命令 + HTTP 客户端 |
| **frontend-dev** | `Frontend/` | 登录页 + el-table 可编辑表格 + 换算 TS |

**串行起点（lead）**：Phase 0 工程地基 + API 契约冻结（Pydantic schema + OpenAPI）。契约定稿后 spawn 3 teammate，各端桩模式自测，Phase 5 联调汇合。

---

## 8. 身份安全基线加固（token 登录部分）

进服鉴权见鉴权方案文档；token 登录本身的基线加固：

| # | 措施 |
|---|---|
| 1 | Service Token 保密（`.env`，不进库，红线 R-11） |
| 2 | `/auth/token` 严格鉴权 + 按 uuid 限频 |
| 3 | token 绑定 UUID，`/auth/exchange` 不信任客户端传 uuid |
| 4 | token 一次性 + 10 分钟短时效 |
| 5 | 白名单前置校验（`whitelist_state != 'removed'`） |
| 6 | HTTPS |
| 7 | JWT 短时效（access 1h / refresh 7d）+ `jwt_revocations` 吊销 |
| 8 | 登录审计（`issued_ip`/`exchanged_ip` + 游戏内回执） |

---

## 9. 验证（端到端）

1. `docker compose up` 启动
2. MCDR `!!login` → 可点 URL
3. 点击 → Web 自动登录，显示 UUID/名
4. Web 新建「仓库需求」→ 加行 `iron_ingot / 192` → 显示「192 (3组)」
5. 游戏内 `!!sheet add 仓库需求 oak_planks 1728` → 显示「1728 (1盒)」
6. Web 刷新看到 oak_planks
7. 游戏内 `!!sheet set 仓库需求 oak_planks done 1` → Web 备齐变绿
8. `curl /api/sheets/1?format=csv` → 标准 CSV
9. 非 owner `!!sheet set` → 403

---

## 10. 待确认

- Service Token 签发策略（按实例独立 vs 全局共享）
- `done_flag` 语义：MVP 按二元 0/1；若需进度则改 `numeric(3,2)`
- 物品名是否强约束 registry id（MVP 自由文本）
- max_stack 是否按物品实际值（MVP 统一 64）
- 进服鉴权方案最终选型（见鉴权文档）
