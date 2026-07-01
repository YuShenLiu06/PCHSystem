# 服务文档：MCDR 插件（游戏内端）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **数据模型**：[`../data-model.md`](../data-model.md)（本服务**不直连数据库**）

## 1. 职责边界

| 管 | 不管 |
|---|---|
| 游戏内命令交互（`!!xx`） | 积分计算（交 scoring-service） |
| 箱子/背包/手持物品扫描 | 业务数据持久化（交后端 PG） |
| 玩家 UUID 推导（离线） | wiki 同步 |
| 称号前缀下发（scoreboard） | 白名单审核决策 |
| 向后端 HTTP 上报 | 投影解析（交 project-service） |

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
| `!!info` | user | 个人信息：UUID / 绑定状态 / 当前称号 |

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
- 用 `requests`（或 `aiohttp`）；耗时调用放 `server.schedule_task(...)`，避免阻塞 MCDR 主循环。
- 含超时 + 重试 + 失败回执给玩家。

## 4. 依赖的其他服务（HTTP API）

| 调用 | 接口 | 时机 |
|---|---|---|
| user-service | `POST /bind/token` | `!!bind` |
| user-service | `GET /players/me`（按 UUID） | `!!info` |
| project-service | `GET /projects/{id}` | `!!project info` |
| scoring-service | `POST /submissions` | `!!submit` |
| title-service | `GET/POST /players/me/titles` | `!!title` |
| alert-service | （被动）后端检测异常后，通过 `!!` 系统消息或 scoreboard 推送告警 | 由后端触发 |

## 5. 所属数据表

**不直连业务库。** 本地仅：
- `config/config.yml`：`api_url`、`service_token`、`rcon` 设置、命令前缀开关。
- 可选缓存：玩家信息短时缓存（减少重复 `GET /players/me`）。

## 6. 风险与待确认

| 项 | 说明 | 缓解 |
|---|---|---|
| SNBT 解析边界 | 潜影盒/复杂 NBT | amulet-nbt + 测试用例覆盖 |
| RCON 性能 | 多箱批量扫描 | 串行 + 限频 + 超时熔断 |
| 清箱时机 | 上报失败却清箱会丢材料 | **先上报成功再清箱** |
| scoreboard prefix 显示效果 | Fabric+Carpet 下聊天/Tab 前缀实际渲染 | 待真机验证；不达标则引入 Fabric 前缀 mod |
| Title Prefix Handler 兼容性 | 与 Carpet 等共存 | 部署时回归玩家名解析 |

> 待确认：服务端 RCON 已启用且端口/密码配置；Carpet 是否影响 `/data get block` 输出格式。
