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
| **双向绑定出码/消费（`!!PCH bind`，game_init/web_init）** | |
| **协管员管理（`!!PCH sheet manager`，account 级授权）** | |
| **前后端连接自检（`!!PCH status` + on_load）** | |
| **清箱功能** | **已废弃（v0.8.0 起，根 R-3/R-4）：插件不再提供清箱功能；`!!PCH sheet submit` 为纯申报，扫描背包后不清除** |

**定位**：纯游戏内客户端 + HTTP 客户端。所有业务结果来自后端 API，本地只存配置与少量缓存。

## 2. 对外接口（游戏内命令）

| 命令 | 权限 | 说明 |
|---|---|---|
| `!!PCH bind` | user | 申请 Web 绑定短码（game_init，无参；后端 POST `/bind/token`） |
| `!!PCH bind <code>` | user | 消费 Web 绑定短码（web_init，带 code 参数；后端 POST `/bind/consume`，双头代玩家） |
| `!!PCH status` | 控制台/玩家 | 前后端连接自检（嗅探后端/令牌/前端，分档回显可点击文档与 release 链接） |
| `!!PCH sheet manager <id> [list\|add\|remove] <玩家名>` | tier A 授权/ tier B 自撤销 | 协管员管理（list 全员可见/add 仅 tier A/remove 可 tier B 自撤销；后端 GET/POST/DELETE `/sheets/{id}/managers`，account 级） |
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
- **清箱功能（已废弃，v0.8.0 起）**：插件不再提供清箱功能（对齐根 R-3/R-4）。`!!PCH sheet submit` 为**纯申报**，扫描背包后不清除；归档项目操作短路返回「项目已归档，只读」（issue #7）。
- **背包/手持扫描（只读）**：用于 `!!submit hand` 与信息展示，详见 §3.3。

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
- **阻塞型 HTTP 必须放 `@new_thread('name')`**（如 `@new_thread('pch_system sheet')`）—— 卸载到 daemon 线程，`server.tell` 线程安全。
- ⚠️ `server.schedule_task(...)` 的回调跑在 **TaskExecutor = 主线程**，**不可**用于卸载阻塞工作（RS-6）。它仅用于协程调度 / 延迟到主线程下一 tick / 从后台线程回主线程。误用会卡住整个 MCDR 主循环（命令/事件/控制台输出解析全停滞）。
- 含超时（≤10s）+ 重试 + 失败回执给玩家。
- 哨兵字符串必须回执玩家（RS-11）：`__RATE_LIMITED__`（限频）/`__REMOVED__`（玩家被移出白名单）/`None`（服务不可用）。

证据：[MCDR `@new_thread`](https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html)、[PluginServerInterface `schedule_task`](https://docs.mcdreforged.com/zh-cn/latest/code_references/ServerInterface.html)（task executor = 主线程，S-1 联网核实）。

#### 3.6.1 service-token + `X-Player-UUID` 代玩家写

对需要以玩家身份写的端点（sheets 全套写、未来认领/交付类、bind 消费短码），MCDR 不持有 JWT，改用**双头代玩家**：

```python
from mcdreforged.api.decorator import new_thread

@new_thread('pch_system sheet')
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

- **sheets 全套写**：后端 `get_current_player` 双通道：Bearer JWT 优先，否则 `X-Service-Token` + `X-Player-UUID`（校验 token 后用 UUID 查 Player 注入，复用现有 RBAC，**与 JWT 写等价**）。
- **bind 双头**：
  - `POST /bind/token`（game_init）：**仅 service-token 单头**（`!!PCH bind` 无参，玩家为自己申请码）
  - `POST /bind/consume`（web_init）：**service-token + `X-Player-UUID` 双头代玩家消费短码**（`!!PCH bind <code>`）
- **account 主锚（v0.8.0 起，R-5）**：JWT `sub` 由 `player_uuid` 改 `web_account_id`（破坏性，旧会话失效）；MCDR 端 service-token 通道不变，但后端聚合按 account（`viewer_uuids` = 同账号所有 UUID）。
- `/sheets/export` 仍独占 service-token-only（不带身份）。
- 详细：[`api/sheets.md`](../api/sheets.md) §2 鉴权表。

### 3.7 sheets 命令树

`!!PCH sheet …` 全套命令树收敛到现有 `!!PCH` 父节点（与 `__init__.py` 一致），完整命令↔HTTP 端点↔角色映射表见 [`api/sheets.md`](../api/sheets.md) §11。要点：

- `!!PCH sheet deliver <qty>` 用**绝对值**（与后端/前端契约一致）；progress 模式玩家先 `view` 看当前 delivered 再决定。
- 权限文案在 help 里说明；真实 RBAC 以后端 403/409 为准（R-9）。
- 新增模块：`pch_system/sheet_client.py`（HTTP + 哨兵）、`pch_system/sheet_commands.py`（`@new_thread` + `server.tell` 回执）、`pch_system/notifier.py`（通知轮询）。

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
| 后台轮询 | `on_load` 启动 `@new_thread('pch_sheet_notifier')` 循环；`on_unload` 设停止位退出；每 `notify_poll_interval_seconds`（默认 2.0）对每个在线玩家调 `GET /notifications/pending?player_uuid=<uuid>&limit=notify_max_per_poll` |
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
| `!!PCH sheet submit <sheet_id>` | 1) MinecraftDataAPI 取完整背包（含潜影盒嵌套：**1.20.4-** 走 `tag.BlockEntityTag.Items` / **1.20.5+** 走 `components."minecraft:container"`）；2) 按 `registry_id` 聚合可用量；3) 拉表 → 对每行按 `registry_id` **精确匹配**（无 `registry_id` 的行跳过）；4) lock 行 `open ∧ have≥need` → `claim` + `deliver(need)` → done；progress 行 `contribute`（增量封顶到 need）；5) **纯申报，不清背包**；6) 回执汇总——与本人相关的行逐行展示（命中 / 本人认领的 lock 行未命中 / progress 已备齐或无需求），其余跳过行（他人认领的 lock 行、progress 未携带项）折叠为末尾一行计数（降噪，fix a619510） |
| `!!PCH sheet addhand <sheet_id> <need> [lock\|progress] [sort]` | 取手持物品 `registry_id` → `PUT /sheets/{sheet_id}/rows`（带 `registry_id`，`item_name` 留空让后端翻译补中文名）新建行 |
| `!!PCH sheet setreg <sheet_id> <row_id> <registry_id>` | owner 给已有行补 `registry_id`（保留原 `item_name`，让该行可被一键提交匹配） |

> **命令入口**：除 `!!PCH sheet submit <sheet_id>` 外，另有 **`!!submit` 第三命令根**（feat a619510，镜像 `!!sheet` 多根注册）：`!!submit` 无参 = 重开上次查看的表格并直接提交（复用 `GET /me/last_sheet` 存储，后端零改动）；`!!submit <sheet_id>` 指定表格。两者与 `!!PCH sheet submit <id>` 共用 `_sheet_submit_impl` 实现，回执折叠规则同上。详见 [`api/sheets.md`](../api/sheets.md) §11。

> **匹配键**：一键提交只按 `registry_id` 精确匹配，不看 `item_name`（自由文本不可靠）。**block id ≠ item id**（如 `minecraft:wall_torch` vs `minecraft:torch`），v1 不做归一化，多数建材不受影响（见 §6）。

### 3.10 数量显示换算（三端统一）

游戏内 sheet 各显示点的物品数量复用项目**三端统一换算规约 `format_qty`**，从「原始整数」改为「个/组/盒」友好单位：

- **阈值**：`>=1728` → `X盒`（潜影盒 27×64）、`>=64` → `X组`、否则 → `X个`；`round(x,2):g` 去尾零（如 `64.5`、`2`）。
- **三端对齐**：权威源 = 后端 `Backend/app/core/qty.py`；前端 `Frontend/src/utils/qty.ts` 已对齐；MCDR 端新增 `pch_system/qty.py` 作为**第三端**（`STACK`/`SHULKER`/`format_qty` 逐字照抄后端，三端字节级一致），另加 `format_qty_safe` 守护显示点传入 `"?"`/缺失字段时回退原值。
- **覆盖显示点（11 处）**：`format_row_line`（sheet view 全行）+ 8 处命令回执 `_show`（add/set/deliver/progress/done/addhand/submit）+ `format_notification` 3 个通知模板。
- **不换算**：HTTP 写调用（`upsert_row`/`deliver_row` 等上报数量）保持原始数字，后端契约不变；`scanner.py` 一键提交的「数量不足（X/Y）」诊断亦保持原样——scanner 是纯模块，测试用 `importlib` 按文件路径加载绕过包 init，加相对导入会 ImportError，且诊断场景原始数字对玩家更有用。
- **纯显示层**：不入库、不进 API（红线 R-1/R-7 不变）。

### 3.11 健康自检（`!!PCH status` + on_load）

前后端可达性嗅探 + 自检报告，在插件加载时控制台输出一次（best-effort，不阻塞 on_load），玩家可随时游戏内执行 `!!PCH status` 复检。

**四探针**（纯函数，便于单测）：

- **plugin**：从 MCDR `get_plugin_metadata("pch_system")` 取自身版本号（S-1 联网核实），失败回落 "unknown" + 作者名。
- **backend**：`GET /info`（404 回退 `/healthz`），拿 `version`/`web_base_url`/`web_online`/`web_version`，1 次尝试不重试，best-effort 吞异常。
- **token**：`GET /notifications/pending?player_uuid=<nil>&limit=1` 带 `X-Service-Token`，**真 401 = token 不一致**（区别于 `/info` 公开端点只能证可达性）；非 401 = token 被接受；网络失败 = 无法判定（不噪声误报）。不再靠 `service_token` 占位启发式判定。
- **frontend**：优先信后端 `/info` 的 `web_online`（同 compose 网络探服务名最可靠，避 localhost 在插件容器内命中容器自身）；后端未上报（`web_online=None`）→ 回退自探 `web_base_url`（回环地址→None 未知，非回环→GET 任意响应=在线/异常=离线）。

**状态矩阵**（severity → rank → 渲染）：

| severity | rank | 控制台前缀 | 游戏色 | 游戏符号 |
|---|---|---|---|---|
| ok | 0 | [OK] | green | ✓ |
| warn | 1 | [WARN] | yellow | ⚠ |
| error | 2 | [ERROR] | red | ✗ |

**on_load 自检**：`on_load` 启动 `@new_thread('pch_health_check')` 后台线程跑控制台版，全 ok → `server.logger.info`，有 warn/error → `server.logger.warning`；外层 try/except 吞所有异常——探针失败绝不影响插件加载（reload 不炸）。

**游戏内 `!!PCH status`**：跑游戏内版，回执 RText 状态表 + 可点击链接段（复用 `messages.rtext_link`，green + bold + `RAction.open_url`），含插件版本/后端版本/令牌一致性/前端可达性 + 文档与 release 链接 + 作者页脚。

证据：[`PluginServerInterface.get_plugin_metadata`](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html)、[`@new_thread`](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html)（S-1 联网核实）。

### 3.12 双向绑定（bind）

一个 Web 账号可绑多个 MC 身份，支持**双向出码**（game_init / web_init），满足「玩家游戏内发起」与「Web 端发起」两种场景。

**game_init（玩家游戏内发起）**：

1. 玩家游戏内执行 `!!PCH bind`（无参）。
2. MCDR 调 `bind_client.request_bind_token` POST `/bind/token`（仅 service-token 单头，不带 `X-Player-UUID`），后端生成 6 位短码 + TTL（默认 10 分钟）。
3. 后端返 `{"short_code": "ABC123", "expires_in": 600}`，MCDR 回执整行 `§7` 灰（敏感信息规则，禁 `§` 高亮）：`§7收到绑定短码：ABC123（有效期 10 分钟），请在网页端输入完成绑定`。
4. 玩家 Web 端输入短码，后端 POST `/bind/confirm`（JWT 鉴权）完成绑定。

**web_init（Web 端发起）**：

1. 玩家 Web 端发起绑定（`POST /bind/issue`，JWT 鉴权），后端生成 6 位短码 + TTL。
2. 玩家游戏内执行 `!!PCH bind <code>`（带 code 参数）。
3. MCDR 调 `bind_client.consume_bind_code` POST `/bind/consume`（**service-token + `X-Player-UUID` 双头代玩家消费短码**）。
4. 后端校验短码有效 + 未用 + 未过期，完成绑定，返回 `{"status": "ok", "account": {...}, "player": {...}}`。
5. MCDR 回执：`§a绑定成功：账号 <用户名> 已绑定当前身份 <UUID>`。

**短码回执格式**：整行 `§7` 灰（敏感信息规则，禁 `§` 高亮），MC 聊天纯文本不可点击，无复制风险。

**HTTP 客户端契约**（复用 sheet_client 双头通道与哨兵机制）：

- 返回类型约定：成功 dict / 哨兵字符串 `__RATE_LIMITED__`（429）/ `__REMOVED__`（403）/ `HttpError(status, detail)` 对象 / 网络失败 None。
- 超时 + 重试 + 哨兵 + HttpError 统一处理。
- 详细：`pch_system/bind_client.py`。

## 4. 依赖的其他服务（HTTP API）

| 调用 | 接口 | 时机 |
|---|---|---|
| user-service | `POST /bind/token`（game_init，仅 service-token） | `!!PCH bind` 无参 |
| user-service | `POST /bind/consume`（web_init，service-token + `X-Player-UUID` 双头） | `!!PCH bind <code>` |
| user-service | `GET /players/me`（按 UUID） | `!!info` |
| user-service | `POST /auth/login` | （预留，密码登录） |
| user-service | `POST /web-accounts/register` | （预留，临时→永久） |
| user-service | `GET /web-accounts/me` | （预留，账号信息） |
| user-service | `POST /bind/issue` | （预留，Web 端发起） |
| user-service | `POST /bind/confirm` | （预留，Web 端输入） |
| user-service | `POST /bind/claim` | （预留，临时账号凭据） |
| project-service | `GET /projects/{id}` | `!!project info` |
| scoring-service | `POST /submissions` | `!!submit` |
| title-service | `GET/POST /players/me/titles` | `!!title` |
| sheets | `GET/POST/PATCH/DELETE /sheets/*`（service-token + `X-Player-UUID` 代玩家） | `!!PCH sheet …` |
| sheets | `GET /sheets/{id}/managers`（service-token + `X-Player-UUID` 代玩家） | `!!PCH sheet manager <id> list` |
| sheets | `POST /sheets/{id}/managers {player_uuid}`（service-token + `X-Player-UUID` 代玩家） | `!!PCH sheet manager <id> add <玩家名>` |
| sheets | `DELETE /sheets/{id}/managers {web_account_id}`（service-token + `X-Player-UUID` 代玩家） | `!!PCH sheet manager <id> remove <玩家名>` |
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
| **未绑 web_account_id 的玩家权限回退** | 未绑定 Web 账号的玩家 `web_account_id` 为 NULL，权限判定回退 `player.role` 旧值（默认 "user"），可能低于账号级 role（admin/owner） | **已修复（v0.8.0）**：`_is_superuser` 切 `_resolve_role`（account 级 role 权威），未绑玩家仍回退 `player.role` 默认值；全量绑定后自然消除 |

> 待确认：服务端 RCON 已启用且端口/密码配置；Carpet 是否影响 `/data get block` 输出格式。

---

## 增量日志

**2026-07-12**：插件 id `htcmc_auth → pch_system`（MCDR 硬性要求 `id` = 文件夹名 = 包名，S-1 联网核实 [catalogue](https://docs.mcdreforged.com/en/latest/plugin_dev/plugin_catalogue.html)「id 需与 plugin_info.json 所在目录同名」+ [metadata](https://docs.mcdreforged.com/en/latest/plugin_dev/metadata.html)「entrypoint 缺省 = id」）；git mv `McdrPlugin/htcmc_auth → McdrPlugin/pch_system` + 内部包 `htcmc_auth → pch_system` + 类 `HtcmcAuthConfig → PchSystemConfig`；全仓 import / 路径 / logger 名 / 线程名（`htcmc_sheet_* → pch_sheet_*`、`htcmc_health_check → pch_health_check`）/ 脚本 / 编排（`TestServer` Dockerfile + compose + config 文件名）/ 活跃文档对齐；`!!PCH status` + on_load 健康自检落地（四探针：plugin/backend/token/frontend，状态矩阵 + 可点击链接）。

**2026-07-19**：`!!PCH bind` 双向绑定（game_init/web_init）落地，支持「玩家游戏内发起」与「Web 端发起」两种场景；新增 `bind_client.py`（复用 sheet_client 双头通道与哨兵机制，`request_bind_token` 单头 / `consume_bind_code` 双头）；`commands.py` 加 `_bind`/`_bind_consume`（镜像 `_login` 的 `@new_thread('pch_system bind')` 模板）；`__init__.py` 命令树替换 `_not_impl("bind")` stub 为 `Literal("bind").runs(_bind).then(Text("code").runs(_bind_consume))`；短码回执整行 `§7` 灰（敏感信息规则，禁 `§` 高亮）；**身份主锚升级为 Web 账号（R-5）**：JWT `sub` 由 `player_uuid` 改 `web_account_id`（破坏性，旧会话失效）；MCDR 端 service-token 通道不变，但后端聚合按 account（`viewer_uuids` = 同账号所有 UUID）；未绑玩家权限回退 `player.role` 默认值，**已由 `_is_superuser` 切 `_resolve_role` 修复**（account 级 role 权威）。

**2026-07-19**：协管员 `!!PCH sheet manager` 升 account 锚（迁移 0016，`SheetManager` 锚 `web_account_id`，同账号任一 UUID 继承）；`sheet_commands.py` 加 `_sheet_manager_list`/`_sheet_manager_add`/`_sheet_manager_remove`（先 `uuid_api_remake.get_uuid(玩家名)` 转 UUID，后端解析 account）；`__init__.py` 命令树加 `manager` 子树；`_render_sheet_detail` 的 `is_manager` 改 account 级——新增 `_is_manager(managers, viewer_uuids)` 单一 helper（N2 DRY，`any(uuid in viewer_uuids for m in managers for uuid in (m.get("member_uuids") or []))`）；行级 `[改][-][子][调]` 与底部 `[进入施工][新增物品]` 按钮对协管员可见（tier B），归档/改名/删表按钮仅 owner 可见（tier A）；提交回执折叠（`scanner.skip_is_ready`/`skip_is_noise`）与归档写短路（issue #7，返「项目已归档，只读」）落地。

---

*最后更新：2026-07-21*
