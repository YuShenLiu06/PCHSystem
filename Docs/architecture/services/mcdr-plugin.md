# 服务文档：MCDR 插件（游戏内端）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **数据模型**：[`../data-model.md`](../data-model.md)（本服务**不直连数据库**）

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 游戏内命令交互（`!!xx`） | 积分计算（交 scoring-service） |
| 箱子/背包/手持物品扫描 | 业务数据持久化（交后端 PG） |
| **完整背包扫描（含潜影盒嵌套，1.20.4- `tag.BlockEntityTag.Items` / 1.20.5+ `components."minecraft:container"`）** | wiki 同步 |
| **一键提交（`!!PCH sheet submit`，按 `registry_id` 精确匹配表行）** | 白名单审核决策 |
| **手持物品新建行（`addhand`，自动填 `registry_id`）/ 给已有行补 `registry_id`（`setreg`）** | 投影解析（交 project-service） |
| 玩家 UUID 推导（离线） | |
| 称号前缀下发（scoreboard） | |
| 向后端 HTTP 上报 | |

**定位**：纯游戏内客户端 + HTTP 客户端。所有业务结果来自后端 API，本地只存配置与少量缓存。

## 2. 对外接口（游戏内命令）

| 命令 | 权限 | 说明 |
|---|---|---|
| `!!bind` | user | 申请 Web 绑定 token，回显短码 |
| `!!submit <项目> <x> <y> <z>` | user | 扫描指定坐标箱子并提交到项目 |
| `!!submit hand <项目>` | user | 手持物品直接提交 |
| `!!project list` / `!!project info <项目>` | user | 项目列表 / 进度查询 |
| `!!score` / `!!rank [分类]` | user | 个人积分 / 榜单（总/赛季/分类） |
| `!!title list` / `!!title set <称号>` | user | 已解锁称号 / 切换展示称号 |
| `!!PCH login` | user | 申请登录链接（已落地） |
| `!!PCH sheet …` | user / owner | sheets 全套（list/view/create/rename/delete/add/set/delrow/claim/deliver/done/release/reject/notify list + **`submit`/`addhand`/`setreg`/`advance`**），详见 [`api/sheets.md`](../api/sheets.md) §11。**设计待办**（§3.7.1）：主节点拟改 `!!PCH project`、文案改「项目」，迁移期双注册 |

## 3. 内部实现要点

### 3.1 命令注册（MCDR API）
```python
from mcdreforged.api.all import *
def on_load(server, prev):
    server.register_command(
        Literal('!!submit')
        .then(Literal('hand').then(Text('project').runs(submit_hand)))
        .then(Text('project').then(Integer('x').then(Integer('y').then(Integer('z').runs(submit_box)))))
    )
```
证据：[MCDR 命令系统](https://docs.mcdreforged.com/zh-cn/latest/)（`server.register_command` + `Literal/Text/Integer` 节点树）。

### 3.2 箱子扫描（核心）
```python
raw = server.rcon_query(f'data get block {x} {y} {z}')
# raw 是 SNBT 字符串，形如 "{Items:[{id:'minecraft:chest',Count:64b,Slot:0b},...]}"
items = parse_snbt_items(raw)   # 用 amulet-nbt 解析 → [{'id':'minecraft:chest','count':64}]
```
- **解析**：复用 [`amulet-nbt`](https://github.com/Amulet-Team/amulet-nbt)，不自研 SNBT。
- **清空箱子**：上报成功后执行 `server.execute(f'data merge block {x} {y} {z} {{Items:[]}}')` —— **不是 `/clear`**（`/clear` 只清玩家背包）。
- **关键顺序**：扫描 → 上报后端 → 后端事务成功 → 才清箱。失败不清箱，玩家可重试。

### 3.3 背包/手持扫描
```python
inv = server.rcon_query(f'data get entity {player} Inventory')
hand = server.rcon_query(f'data get entity {player} SelectedItem')
```
只读，用于 `!!submit hand` 与信息展示。**无法实时监听**物品变化，必须由命令触发。

### 3.4 玩家 UUID 推导（离线模式）
离线模式 UUID 由玩家名确定性推导（等价 Java `OfflinePlayer.nameUUIDFromBytes`）：
```python
import hashlib, uuid
def offline_uuid(name: str) -> str:
    b = bytearray(hashlib.md5(('OfflinePlayer:' + name).encode('utf-8')).digest())
    b[6] = (b[6] & 0x0f) | 0x30   # version 3
    b[8] = (b[8] & 0x3f) | 0x80   # variant IETF
    return str(uuid.UUID(bytes=bytes(b)))
```
玩家名变化 → UUID 变化 → 视为新身份（改名过户由 user-service 处理）。

### 3.5 称号前缀下发（scoreboard）
```python
def apply_prefix(server, player, prefix_text):
    team = f't_{player[:12]}'          # 队名取玩家名片段
    server.execute(f'scoreboard teams add {team}')
    server.execute(f'scoreboard teams option {team} prefix {{"text":"{prefix_text}"}}')
    server.execute(f'scoreboard teams join {team} {player}')
```
- **配套**：安装 [Title Prefix Handler](https://mcdreforged.com/zh-CN/plugin/title_prefix_handler)，修正 team prefix 对 MCDR 玩家名解析的干扰。
- 切换称号时重建 team prefix。

### 3.6 HTTP 客户端（带鉴权）

```python
import requests
def call_backend(server, path, payload):
    return requests.post(
        f'{API_URL}{path}', json=payload, timeout=10,
        headers={'X-Service-Token': SERVICE_TOKEN},   # MCDR↔后端服务密钥
    ).json()
```
- 用 `requests`（或 `aiohttp`）。
- **阻塞型 HTTP 必须放 `@new_thread('name')`**（如 `@new_thread('htcmc_auth sheet')`）—— 卸载到 daemon 线程，`server.tell` 线程安全。
- ⚠️ `server.schedule_task(...)` 的回调跑在 **TaskExecutor = 主线程**，**不可**用于卸载阻塞工作（RS-6）。它仅用于协程调度 / 延迟到主线程下一 tick / 从后台线程回主线程。误用会卡住整个 MCDR 主循环（命令/事件/控制台输出解析全停滞）。
- 含超时（≤10s）+ 重试 + 失败回执给玩家。
- 哨兵字符串必须回执玩家（RS-11）：`__RATE_LIMITED__`（限频）/`__REMOVED__`（玩家被移出白名单）/`None`（服务不可用）。

证据：[MCDR `@new_thread`](https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html)、[PluginServerInterface `schedule_task`](https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html)（task executor = 主线程，S-1 联网核实）。

#### 3.6.1 service-token + `X-Player-UUID` 代玩家写

对需要以玩家身份写的端点（sheets 全套写、未来认领/交付类），MCDR 不持有 JWT，改用**双头代玩家**：

```python
from mcdreforged.api.decorator import new_thread

@new_thread('htcmc_auth sheet')
def _do_claim(server, player, sheet_id, row_id):
    player_uuid = uuid_api_remake.get_uuid(player)          # RS-8：UUID 推导唯一来源
    resp = requests.post(
        f'{API_URL}/sheets/{sheet_id}/rows/{row_id}/claim',
        timeout=10,
        headers={
            'X-Service-Token': SERVICE_TOKEN,
            'X-Player-UUID': player_uuid,                   # 后端据此加载 Player 注入
        },
    )
    # 哨兵 + 403/404/409 → server.tell 友好回执
```

- 后端 `get_current_player` 双通道：Bearer JWT 优先，否则 `X-Service-Token` + `X-Player-UUID`（校验 token 后用 UUID 查 Player 注入，复用现有 RBAC，**与 JWT 写等价**）。
- `/sheets/export` 仍独占 service-token-only（不带身份）。
- 详细：[`api/sheets.md`](../api/sheets.md) §2 鉴权表。

### 3.7 sheets 命令树

`!!PCH sheet …` 全套命令树收敛到现有 `!!PCH` 父节点（与 `__init__.py` 一致），完整命令↔HTTP 端点↔角色映射表见 [`api/sheets.md`](../api/sheets.md) §11。要点：

- `!!PCH sheet deliver <qty>` 用**绝对值**（与后端/前端契约一致）；progress 模式玩家先 `view` 看当前 delivered 再决定。
- 权限文案在 help 里说明；真实 RBAC 以后端 403/409 为准（R-9）。
- 新增模块：`htcmc_auth/sheet_client.py`（HTTP + 哨兵）、`htcmc_auth/sheet_commands.py`（`@new_thread` + `server.tell` 回执）、`htcmc_auth/notifier.py`（通知轮询）。

### 3.7.1 项目语义对齐设计（⚠️ 仅设计，本期不实现）

> 与 [`api/sheets.md`](../api/sheets.md) §13「设计待办」对应。Web 端文案已统一「表格 → 项目」，MCDR 端尚未对齐——本节是 Phase D 的设计记录，落地由主工程师另行实现。

**现状（不对齐）**：

- MCDR 主命令仍是 `!!PCH sheet …`，回执/help 文案大量用「表格 / 表」，与 sheets「sheet = 项目」语义脱节（[`api/sheets.md`](../api/sheets.md) §1 术语演进）。
- `!!PCH project` 已被占用为 `_not_impl` 占位节点（占坑未实现），与「sheet 升级为项目」后的语义命令名正面冲突。

**提议（迁移期设计）**：

| 项 | 现状 | 提议 |
|---|---|---|
| 主命令节点 | `!!PCH sheet …` | 主节点改 `!!PCH project …`（别名 `proj`），下接现有 sheet 子命令树（list/view/create/add/set/delrow/claim/deliver/done/contribute/release/reject/advance/notify） |
| 占位节点 | `!!PCH project` = `_not_impl` | **删占位**（被新主节点复用，语义自然收敛） |
| 文案 | 「表格 / 表」 | 统一改「项目」（help / 回执 / 阶段横幅） |
| 兼容期 | — | **双注册**：迁移期 `sheet` + `project` 两个 Literal 挂同一套子命令树，玩家两种写法都生效；稳定后再评估下线 `sheet` |
| 归档节点 | `!!PCH sheet advance <id> [constructing\|archived]` 已存在 | 节点名对齐 → `!!PCH project advance <id> [constructing\|archived]`（HTTP `/sheets/{id}/advance` URL 不变，YAGNI） |

**风险与约束**：

- **双注册期命令重复**：help 文案需说明「`sheet` / `project` 等价、`project` 为新名」；help 列表会有两条入口（容忍）。
- **worktree 改动不被 reload 看到**：在 worktree 改 `.py` 不会被运行中的 MCDR reload 看到，须先同步到主仓路径再 `!!MCDR reload` 测试（项目已知坑）。
- **S-1 联网验证**：节点树重构（`Literal` 别名 / 子树复用）实现前必须查 [MCDR 官方文档](https://docs.mcdreforged.com/zh-cn/latest/) 核实 API 签名（根红线 S-1，禁止凭记忆臆造）。
- **API 契约不变**：URL 仍是 `/sheets/*`，MCDR 改的只是游戏内命令名与文案，后端零改动。

### 3.8 通知轮询（投递候选池）

后端 notification-service 把通知落库（[`notification-service.md`](./notification-service.md)），MCDR 负责拉取 + 投递 + ack + 离线补推：

| 阶段 | 实现 |
|---|---|
| 在线集合维护 | `on_player_joined(server, player, info)` 加入 / `on_player_left(server, player)` 移出；插件加载时若服务端已启动，用 `server.rcon_query('list')` 解析初始化（兜底，`get_online_players` 不在通用 API） |
| 后台轮询 | `on_load` 启动 `@new_thread('htcmc_sheet_notifier')` 循环；`on_unload` 设停止位退出；每 `notify_poll_interval_seconds`（默认 2.0）对每个在线玩家调 `GET /notifications/pending?player_uuid=<uuid>&limit=notify_max_per_poll` |
| 逐条投递 | `server.tell(player, format_notification(n))` |
| ack | 投递成功后 `POST /notifications/ack {player_uuid, ids}`（幂等） |
| 上线补推 | `on_player_joined` 立即为该玩家拉一次 pending（离线期间堆积的补推） |
| 主动查看 | `!!PCH sheet notify list` 拉取并分页回显 |
| 离线处理 | 通知仅落库后端；MCDR 不持久化，重启后靠上线拉取恢复 |

证据：[`on_player_joined`/`on_player_left`/`register_event_listener`](https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/event.html)、[`rcon_query`/`tell`](https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html)（S-1 联网核实）。

### 3.9 一键提交 / 手持新建行 / 补 registry_id（registry_id 配套）

依赖 [MinecraftDataAPI](https://github.com/Fallen-Breath/MinecraftDataAPI) 插件提供「按玩家取完整背包 NBT」能力（`get_registry_id` / 容器嵌套物品枚举）。三个命令均经 `@new_thread` 卸载、`server.tell` 回执：

| 命令 | 流程 |
|---|---|
| `!!PCH sheet submit <sheet_id>` | 1) MinecraftDataAPI 取完整背包（含潜影盒嵌套：**1.20.4-** 走 `tag.BlockEntityTag.Items` / **1.20.5+** 走 `components."minecraft:container"`）；2) 按 `registry_id` 聚合可用量；3) 拉表 → 对每行按 `registry_id` **精确匹配**（无 `registry_id` 的行跳过）；4) lock 行 `open ∧ have≥need` → `claim` + `deliver(need)` → done；progress 行 `contribute`（增量封顶到 need）；5) **纯申报，不清背包**；6) 回执汇总（每行命中/未命中/不足） |
| `!!PCH sheet addhand <sheet_id> <need> [lock\|progress] [sort]` | 取手持物品 `registry_id` → `PUT /sheets/{sheet_id}/rows`（带 `registry_id`，`item_name` 留空让后端翻译补中文名）新建行 |
| `!!PCH sheet setreg <sheet_id> <row_id> <registry_id>` | owner 给已有行补 `registry_id`（保留原 `item_name`，让该行可被一键提交匹配） |

> **匹配键**：一键提交只按 `registry_id` 精确匹配，不看 `item_name`（自由文本不可靠）。**block id ≠ item id**（如 `minecraft:wall_torch` vs `minecraft:torch`），v1 不做归一化，多数建材不受影响（见 §6）。

## 4. 依赖的其他服务（HTTP API）

| 调用 | 接口 | 时机 |
|---|---|---|
| user-service | `POST /bind/token` | `!!bind` |
| user-service | `GET /players/me`（按 UUID） | `!!info` |
| project-service | `GET /projects/{id}` | `!!project info` |
| scoring-service | `POST /submissions` | `!!submit` |
| title-service | `GET/POST /players/me/titles` | `!!title` |
| sheets | `GET/POST/PATCH/DELETE /sheets/*`（service-token + `X-Player-UUID` 代玩家） | `!!PCH sheet …` |
| notifications | `GET /notifications/pending` / `POST /notifications/ack` / `POST /notifications/{id}/read`（service-token） | 通知轮询 + `!!PCH sheet notify list` |
| alert-service | （被动）后端检测异常后，通过 `!!` 系统消息或 scoreboard 推送告警 | 由后端触发 |

### 4.1 MCDR 端插件依赖（游戏内协作）

| 依赖 | 用途 | 仓库 |
|---|---|---|
| **MinecraftDataAPI** | 提供按玩家取完整背包 NBT（含潜影盒嵌套物品枚举、`registry_id` 提取），供 `!!PCH sheet submit` 一键提交使用 | <https://github.com/Fallen-Breath/MinecraftDataAPI> |

> 安装缺失时 `submit` 命令回执友好提示并降级（其他 sheets 命令不受影响）。

## 5. 所属数据表

**不直连业务库。** 本地仅：
- `config/config.yml`：`api_url`、`service_token`、`rcon` 设置、命令前缀开关、`notify_poll_interval_seconds`（默认 2.0）、`notify_max_per_poll`（默认 20）。
- 可选缓存：玩家信息短时缓存（减少重复 `GET /players/me`）。

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| SNBT 解析边界 | 潜影盒/复杂 NBT | amulet-nbt + 测试用例覆盖 |
| RCON 性能 | 多箱批量扫描 | 串行 + 限频 + 超时熔断 |
| 清箱时机 | 上报失败却清箱会丢材料 | **先上报成功再清箱** |
| scoreboard prefix 显示效果 | Fabric+Carpet 下聊天/Tab 前缀实际渲染 | 待真机验证；不达标则引入 Fabric 前缀 mod |
| Title Prefix Handler 兼容性 | 与 Carpet 等共存 | 部署时回归玩家名解析 |
| 阻塞 HTTP 误用 `schedule_task` | 卡住 MCDR 主循环（RS-6） | 全部走 `@new_thread`；详见 §3.6 |
| 通知轮询延迟 | 默认 2s 周期 | 可调 `notify_poll_interval_seconds`；紧急叠加 webhook（预留） |
| 离线通知补推 | 离线堆积 | 上线 `on_player_joined` 立即拉取 + 分页 |
| **block id ≠ item id** | 一键提交按 `registry_id` 精确匹配；方块 id（如 `minecraft:wall_torch`）与对应 item id（`minecraft:torch`）不一致，会导致部分方块类物品匹配失败 | v1 不归一化，多数建材不受影响；后续在 project-service 做归一化映射 |
| **1.20.5+ 物品组件路径** | 潜影盒嵌套物品 1.20.5+ 走 `components."minecraft:container"`（1.20.4- 走 `tag.BlockEntityTag.Items`），组件路径代码已兼容但**真机只验证 1.20.1** | 升级服务端版本时回归 `submit` 命令；按版本分派读取器（预留） |
| **MinecraftDataAPI 缺失** | `submit` 一键提交依赖该插件取背包 NBT | 安装检测 + 友好降级提示（其他命令不受影响） |
| worktree 改动不被 reload 看到 | 改 `.py` 后 reload 无效 | 须先同步到主仓路径再 `!!MCDR reload` 测试（项目已知坑） |
| 命令名迁移期重复 | `sheet` + `project` 双注册 help 重复 | 迁移期容忍；文案说明「project 为新名」；稳定后评估下线 `sheet`（§3.7.1 仅设计） |

> 待确认：服务端 RCON 已启用且端口/密码配置；Carpet 是否影响 `/data get block` 输出格式。
