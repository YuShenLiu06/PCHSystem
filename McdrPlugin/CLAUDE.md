# MCDR 插件（游戏内端） · 子服务 CLAUDE.md

> 本文件由 `service-claude-md` skill 生成 / 维护，**禁止手写**。
> 全局统一规范见根 [`CLAUDE.md`](../CLAUDE.md)；本服务完整架构见架构文档。

---

## 1. 服务定位

MCDR 插件是 PCHSystem 的**纯游戏内客户端**：负责游戏内命令交互、箱子/背包/手持物品扫描、离线 UUID 推导、称号前缀下发，以及向后端 FastAPI 的 HTTP 上报。本地不持久化任何业务数据，所有业务结果来自后端 API。

> 完整架构：[`Docs/architecture/services/mcdr-plugin.md`](../Docs/architecture/services/mcdr-plugin.md)

---

## 2. 职责边界

| 管管 | 不管 |
|---|---|
| 游戏内命令交互（`!!PCH login` / `!!PCH bind` / `!!PCH submit` / `!!PCH score` 等） | 积分计算（交 scoring-service） |
| 箱子 / 背包 / 手持物品扫描（RCON `data get`） | 业务数据持久化（交后端 PostgreSQL） |
| 离线模式 UUID 推导（依赖 `uuid_api_remake` 插件） | wiki 同步 |
| 称号前缀下发（scoreboard team prefix） | 白名单审核决策 |
| 向后端 HTTP 上报（带服务密钥） | 投影解析（交 project-service） |
| token 登录链接生成（`!!PCH login` → Web 兑换） | RBAC / 权限判定（后端为准） |

---

## 3. 雷点·红线（服务特有）

> 全局红线见根 [`CLAUDE.md`](../CLAUDE.md) §3（S-1、S-2、R-1~R-12）。此处只列**本服务特有**或对本服务**特别需要强调**的约束。

| # | 红线 | 说明 |
|---|---|---|
| **RS-1** | **遵守 S-1：MCDR API 必须联网核实** | 任何 `mcdreforged.api.*`（命令节点树、`RText`、`schedule_task`、`PluginServerInterface`、事件监听）、RCON 用法、插件元数据，**实现前**必须查 <https://docs.mcdreforged.com/zh-cn/latest/> 或可靠来源，禁止凭记忆臆造；结论附 URL。 |
| **RS-2** | **遵守 R-1：绝不直连数据库** | 插件只通过 HTTP API 与后端通信；`psycopg2` / `sqlalchemy` 等不得出现在依赖中。 |
| **RS-3** | **遵守 R-3 + R-4：清箱时序与命令** | 扫描 → `POST /submissions` → 后端事务返回成功 → **才**执行 `data merge block x y z {Items:[]}` 清箱；**失败绝不清箱**，玩家可重试。**禁止用 `/clear`**（只清玩家背包，不清箱子）。 |
| **RS-4** | **遵守 R-7：纯客户端定位** | 不做积分计算、不持久化业务数据、不做 wiki 同步、不缓存超过「短时显示用」所需的玩家信息。 |
| **RS-5** | **遵守 R-11：密钥不进代码库** | `service_token` / `api_url` / RCON 密码经 `config.json`（本地）或环境变量注入；`config.json` 已在 `.gitignore`。仓库只提交 `config.json.example`。 |
| **RS-6** | **遵守 R-12：阻塞调用放 `@new_thread`** | 所有 HTTP 调用、RCON 查询、NBT 解析必须用 `@new_thread(...)`（`mcdreforged.api.decorator.new_thread`）卸载到后台线程；`schedule_task` 的同步回调跑在 task executor = 主线程，**禁止**用来卸载阻塞工作（曾导致 `!!PCH login` 卡顿——见 [`Docs/McdrPlugin/mcdr-api-cheatsheet.md`](../Docs/McdrPlugin/mcdr-api-cheatsheet.md) §8「常见误区」）；HTTP 必含超时（≤10s）+ 重试 + 失败回执给玩家（`server.tell` 线程安全，可在后台线程直接回执）。 |
| **RS-7** | **SNBT 解析不自研** | 潜影盒 / 复杂 NBT 一律走 [`amulet-nbt`](https://github.com/Amulet-Team/amulet-nbt)，不为 SNBT 写正则或手写解析器；写完用真实潜影盒样本测试。 |
| **RS-8** | **离线 UUID 推导只依赖 `uuid_api_remake`** | 不要在插件内重写 `OfflinePlayer.nameUUIDFromBytes` —— 复用 `uuid_api_remake.get_uuid(name)`（已在 `htcmc_auth/__init__.py` 验证可用），保证与 `uuid_api_remake.mcdr` 全局一致。 |
| **RS-9** | **称号 scoreboard prefix 兼容性** | team prefix 会干扰 MCDR 玩家名解析；部署时必须配合 [Title Prefix Handler](https://mcdreforged.com/zh-CN/plugin/title_prefix_handler)，并在真机回归玩家名解析。Fabric + Carpet 下聊天/Tab 前缀渲染待真机验证。 |
| **RS-10** | **RCON 串行 + 限频 + 熔断** | 多箱批量扫描必须串行执行，单次超时熔断；禁止并发刷 RCON，避免压垮服务端主线程。 |
| **RS-11** | **`!!login` 限频与白名单状态** | 同玩家短时间内重复 `!!login` 触发 `__RATE_LIMITED__`；被移白名单返回 `__REMOVED__` —— 这些信号必须回执玩家，禁止静默丢弃。 |
| **RS-12** | **插件包名规范** | Python 包用小写（`htcmc_auth/htcmc_auth/`），插件顶层目录用大驼峰（`McdrPlugin/`）；`mcdreforged.plugin.json` 的 `id` 与包名一致，遵循根 CLAUDE.md §1。 |
| **RS-13** | **sheets 代玩家写：service-token + `X-Player-UUID` 双头** | MCDR 不持有玩家 JWT。对需以玩家身份写的端点（`sheets` 全套写、未来认领/交付类），HTTP 头同时带 `X-Service-Token`（复用 `MCDR_SERVICE_TOKEN`）+ `X-Player-UUID`（由 `uuid_api_remake.get_uuid(name)` 推导，RS-8）；**无 `Authorization` 头**。后端 `get_current_player` 双通道：Bearer JWT 优先，否则 service-token+UUID 代玩家加载 Player 注入，复用现有 RBAC（与 JWT 写等价）。命令层**不做硬权限拦截**（R-9：真实权限以后端 403/409 为准），仅在 help 文案说明角色。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §2 鉴权表 / §11 命令映射表。 |
| **RS-14** | **通知轮询全程 `@new_thread`，禁用 `schedule_task` 卸载** | `notifier.py` 后台循环必须用 `@new_thread('htcmc_sheet_notifier')` 启动（RS-6 的具体化）；`on_player_joined`/`on_player_left`（`mcdr.player_joined`/`mcdr.player_left`）维护在线 name→uuid 字典，插件加载时用 `server.rcon_query('list')` 兜底初始化；轮询每 `notify_poll_interval_seconds`（默认 2.0）拉 `GET /notifications/pending` → 逐条 `server.tell` → `POST /notifications/ack {player_uuid, ids}`；`on_player_joined` 立即拉一次（离线堆积补推）。网络失败**静默继续下次**（不 `tell` 玩家，避免刷屏）。端点契约见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §12、[`notification-service.md`](../Docs/architecture/services/notification-service.md)。 |

---

## 4. 关键要素

### 核心实体 / 数据表

**不直连业务库。** 本地仅：
- `config/config.json`：`api_url`、`service_token`、RCON 设置、命令前缀开关。
- 可选缓存：玩家信息短时缓存（减少重复 `GET /players/me`）。

### 对外接口（游戏内命令）

| 命令 | 权限 | 说明 |
|---|---|---|
| `!!PCH login` | user | 申请 Web 登录 token，回显可点击链接（`RAction.open_url`），含 TTL 与上一链接失效提示 |
| `!!PCH bind` | user | 申请 Web 绑定 token，回显短码 |
| `!!PCH submit <项目> <x> <y> <z>` | user | 扫描坐标箱子并提交 |
| `!!PCH submit hand <项目>` | user | 手持物品直接提交 |
| `!!PCH project list` / `!!PCH project info <项目>` | user | 项目列表 / 进度查询 |
| `!!PCH score` / `!!PCH rank [分类]` | user | 个人积分 / 榜单 |
| `!!PCH title list` / `!!PCH title set <称号>` | user | 已解锁称号 / 切换展示 |
| `!!PCH sheet list [--mine]` / `view` / `create` / `add` / `set` / `delrow` / `claim` / `deliver` / `done` / `contribute` / `release` / `reject` / `advance` / `notify list` | user / owner | 在线表格协作 + **项目阶段流转**（service-token + `X-Player-UUID` 代玩家写；`deliver` 按 mode 分流——lock→`/delivery` 绝对值 / progress→`/contribute` 增量；`advance <sheet_id> [constructing\|archived]` owner/admin 流转阶段，缺省 `to` 走后端状态机默认推进，`to=archived` 触发归档回执含相对路径；`view` 显阶段横幅 + owner footer 按 status 渲染流转按钮）；命令↔HTTP↔角色映射见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §11） |
| `!!sheet` / `!!sheet <sheet_id>` / `!!PCH sheet last` | user | **快速重开**（commit 20cdf37）：`!!sheet` 无参 = 重开上次查看的表（经 `GET /me/last_sheet` 取 `last_sheet_id`，无历史回显提示）；`!!sheet <id>` = 直开指定表；`!!PCH sheet last` 等同无参 `!!sheet`。`!!sheet` 为**第二命令根**（独立 help message），与 `!!PCH sheet ...` 子树并存 |
| `!!submit` / `!!submit <sheet_id>` | user | **一键提交新根**（feat a619510）：`!!submit` 为**第三命令根**（独立 help message，镜像 `!!sheet` 多根注册），无参 = 重开上次查看的表格并直接提交（复用 `!!sheet` 的 `GET /me/last_sheet` 存储，后端零改动）；`!!submit <编号>` 指定表格。与 `!!PCH sheet submit <id>` 共用 `_sheet_submit_impl` 实现；回执折叠两类跳过行（不再逐行）：① 已备齐/进度已满（`scanner.skip_is_ready` + `SHEET_SUBMIT_READY_FOLDED_LINE`）；② 与本人无关（他人认领的 lock 行、progress 未携带项，`scanner.skip_is_noise` + `SHEET_SUBMIT_FOLDED_LINE`）；仅逐行展示本人本次可操作的跳过（认领未完成需补货等）。归桶顺序 ready 优先（他人 done 的 lock 行计入「已备齐」更准确） |
| `!!PCH sheet list [-m\|-c\|-t\|-a\|-l]` 简写旗标 | user | list 默认 `active`（进行中=collecting+constructing，排除归档）+ 自己参与的优先（后端参与优先排序）+ 每行阶段标签 `format_phase_label`。旗标单字母简写：`-m`(mine)/`-c`(collecting)/`-t`(constructing)/`-a`(archived)/`-l`(all)，可组合如 `-ma`=mine+archived；完整 `--mine`/`--collecting` 等向后兼容；未知旗标回显助记提示。解析走纯函数 `_parse_list_flag_tokens` |
| `!!PCH sheet addsub <sheet_id> <parent_row_id> <registry_id> <qty_per_unit> [mode] [sort]` / `delsub <sheet_id> <row_id>` / `setsub <sheet_id> <row_id> <qty_per_unit> [mode] [sort]` | owner | **子物品嵌套行**（issue #19，迁移 0012）：`addsub` 给指定父行添加子物品（必须提供 registry_id 与 qty_per_unit≥1，need 派生）；`delsub` 删子行（复用 `delrow` 端点）；`setsub` 修改子行 qty_per_unit/mode/sort（父 need 变时级联重算）。**单层限制**：父行必须是顶层行，否则 409。`messages.py` 缩进渲染子行（1–2 空格）+ 父行 `+N子件` 徽标；按钮紧凑化（单字 `[认][改][-][+]` + RText hover）。详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §11 |
| `!!PCH info` | user | 个人信息：UUID / 绑定状态 / 当前称号 |

### 依赖的其他服务（HTTP API）

- **user-service**：`POST /bind/token`、`POST /auth/token`（`!!PCH login` 链路）、`GET /players/me`
- **project-service**：`GET /projects/{id}`（`!!project info`）
- **scoring-service**：`POST /submissions`（`!!submit`）
- **title-service**：`GET/POST /players/me/titles`（`!!title`）
- **alert-service**：被动接收告警（`!!` 系统消息或 scoreboard 推送）
- **sheets-service**：`GET/POST/PATCH/DELETE /sheets/*`（service-token + `X-Player-UUID` 代玩家写，RS-13）—— `!!PCH sheet …` 全套
- **notification-service**：`GET /notifications/pending` / `POST /notifications/ack` / `POST /notifications/{id}/read`（service-token）—— 通知轮询投递（RS-14）
- **uuid_api_remake**（MCDR 插件依赖）：离线 UUID 推导

### 关键文件

- 入口：`McdrPlugin/htcmc_auth/htcmc_auth/__init__.py`（`on_load` 注册命令树 + 事件监听 + 启动 notifier 线程，`on_unload` 停止）
- 命令回调：`htcmc_auth/commands.py`（`_pch_root` / `_login` / `_not_impl`）
- **sheets 命令回调**：`htcmc_auth/sheet_commands.py`（`@new_thread` + `server.tell` 回执 + 403/404/409 中文文案）
- **通知轮询**：`htcmc_auth/notifier.py`（在线字典 + rcon 初始化 + 后台循环 + 离线补推）
- 消息/色彩常量：`htcmc_auth/messages.py`（含 `format_notification` / `format_row_line`）
- 元数据：`McdrPlugin/htcmc_auth/mcdreforged.plugin.json`
- HTTP 客户端：`htcmc_auth/client.py`（`LoginResult` dataclass）、`htcmc_auth/sheet_client.py`（sheets + notifications HTTP，哨兵 `__RATE_LIMITED__`/`__REMOVED__`/`HttpError`/`None`）
- 配置：`htcmc_auth/config.py`（含 `notify_poll_interval_seconds` / `notify_max_per_poll`）+ `config.json`（仓库只提交 `config.json.example`，RS-5）

---

## 5. 文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| **本服务架构（权威）** | [`../Docs/architecture/services/mcdr-plugin.md`](../Docs/architecture/services/mcdr-plugin.md) | 完整职责 / 接口 / 内部实现 / 风险矩阵 |
| **MCDR API 速查** | [`../Docs/McdrPlugin/mcdr-api-cheatsheet.md`](../Docs/McdrPlugin/mcdr-api-cheatsheet.md) | 命令节点树、RText、schedule_task、事件、RCON 速查（Team B 维护） |
| 数据模型 | [`../Docs/architecture/data-model.md`](../Docs/architecture/data-model.md) | 全局表结构与约束（本服务不直连，仅对照） |
| **sheets API** | [`../Docs/architecture/api/sheets.md`](../Docs/architecture/api/sheets.md) | §2 鉴权 / §11 `!!PCH sheet` 命令映射 / §12 通知端点（RS-13/RS-14 依据） |
| **notification-service 契约** | [`../Docs/architecture/services/notification-service.md`](../Docs/architecture/services/notification-service.md) | 通知抽象层调用契约 / category 枚举 / pending·ack·read 端点（轮询依据） |
| 工程总览 | [`../Docs/architecture.md`](../Docs/architecture.md) | 三端架构与跨服务流程 |
| 根规范 | [`../CLAUDE.md`](../CLAUDE.md) | 统一命名 / 红线 / 索引 |
| MCDR 官方文档 | <https://docs.mcdreforged.com/zh-cn/latest/> | 联网核实 API（S-1 强制） |

---

## 6. 色彩代码使用标准

MCDR 消息支持两种风格：Minecraft `§` 色彩码字符串与 MCDR `RText` + `RColor/RStyle/RAction` 枚举。本项目**双轨并行**：新代码推荐 `RText` 枚举（类型安全、可组合点击事件），旧代码保留 `§` 字符串逐步迁移。

### 语义用途表

| 用途 | § 码 | RColor | 示例 |
|---|---|---|---|
| 成功 | `§a` | `RColor.green` | `§a登录链接已生成` |
| 错误/失败 | `§c` | `RColor.red` | `§c权限不足` |
| 警告/限频 | `§e` | `RColor.yellow` | `§e操作太频繁` |
| 信息提示 | `§b` | `RColor.aqua` | `§b提示：链接 10 分钟内有效` |
| 重要/标题 | `§6§l` | `RColor.gold` + `RStyle.bold` | `§6§l[系统公告]` |
| 次要/灰前缀 | `§7` | `RColor.gray` | `§7收到请求，请：` |
| 链接/可点击 | `§9§l` + `RAction.open_url` | `RColor.blue` + `RStyle.bold` | `§9§l[点击打开]` |

### 样式码表

| 样式 | § 码 | RStyle |
|---|---|---|
| 粗体 | `§l` | `RStyle.bold` |
| 斜体 | `§o` | `RStyle.italic` |
| 下划线 | `§n` | `RStyle.underline` |
| 删除线 | `§m` | `RStyle.strikethrough` |
| 混乱 | `§k` | `RStyle.obfuscated` |
| 重置 | `§r` | （包裹新 `RText` 段） |

### 使用规则

1. **新代码**优先用 `RText(text, color=RColor.xxx).set_styles(RStyle.yyy).c(RAction.open_url, url)`；旧代码保留 `§` 码字符串，逐步迁移。
2. **多段拼接**用 `RTextList(a, b, c)`；**不要**用 f-string 拼接不同色彩的 `RText`（`RText` 没有 `append`，文档已确认；当前 `__init__.py` 用 `RTextList("§7收到...", link)` 即正确）。
3. **必带 `§r` 或新 `RText` 段**：颜色与样式不会自动重置，跨段必须显式重置，避免颜色泄漏到后续文本。
4. **服务密钥 / token / 短码等敏感信息禁止用 `§` 高亮**，统一用 `RText` 灰色（`RColor.gray`）短显示，避免玩家误点击复制风险。

### 示例（双写对照）

```python
# § 码风格（旧）
src.reply("§c权限不足，§7请联系管理员")
src.reply(RText("§a§l[点击此处打开网页登录]").c(RAction.open_url, url))

# RColor/RStyle 风格（推荐）
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle, RAction
src.reply(RTextList(
    RText("权限不足", color=RColor.red),
    RText("，请联系管理员", color=RColor.gray),
))
src.reply(
    RText("[点击此处打开网页登录]", color=RColor.green)
    .set_styles(RStyle.bold)
    .c(RAction.open_url, url)
)
```

完整 API 速查见 [`Docs/McdrPlugin/mcdr-api-cheatsheet.md`](../Docs/McdrPlugin/mcdr-api-cheatsheet.md) §6（RText 色彩系统）。

---

## 7. 开发热重载工作流

> 容器编排见 [`TestServer/docker-compose.yml`](../TestServer/docker-compose.yml)（mc-test 服务，加入 `pchsystem_default` 网络访问 backend）。
> **mc-test 容器已挂载插件源码 `./McdrPlugin/htcmc_auth:/mcdr/plugins/htcmc_auth`，改 `.py` 后游戏内秒级热重载，无需 rebuild 镜像。**

| 改动类型 | 操作 | 生效方式 |
|---|---|---|
| 插件 `.py` 源码（`htcmc_auth/**/*.py`） | 保存后游戏内输入 `!!MCDR plugin reload htcmc_auth` | MCDR 内置热重载，秒级生效（依赖源码挂载） |
| `mcdreforged.plugin.json` 元数据 | `docker compose -f TestServer/docker-compose.yml build mc-test && docker compose -f TestServer/docker-compose.yml up -d mc-test` | rebuild 镜像（仅元数据/依赖变更才需要） |
| 新增插件 Python 依赖 | 同上 rebuild（Dockerfile 里 `pip install`） | rebuild 镜像 |
| `config/htcmc_auth/config.json` | 容器内 `/mcdr/config/htcmc_auth/config.json`，改后 `!!MCDR plugin reload htcmc_auth` | 热重载 |

### 验证插件加载
```bash
docker logs pchsystem-mc-test-1 2>&1 | grep htcmc_auth
# 应看到：registered command with root node Literal '!!PCH' + registered help message
```

### 常见排错
- **改了代码 `!!MCDR plugin reload` 后行为没变**：确认 `pchsystem-mc-test-1` 容器挂载了插件源码（`docker inspect pchsystem-mc-test-1 \| grep -A2 plugins/htcmc_auth`）；若挂载点指向空目录（如误用 `./` 相对路径导致指向 `TestServer/McdrPlugin/`），插件会被空挂载覆盖消失
- **`!!MCDR plugin reload` 报错**：通常是语法错误，先 `python -c "import ast; ast.parse(open('McdrPlugin/htcmc_auth/htcmc_auth/<file>.py').read())"` 本地校验
- **改了 backend 响应字段，MCDR 拿不到**：先确认 backend 容器跑的是新代码（见 [`Backend/CLAUDE.md`](../Backend/CLAUDE.md) §5），再确认 `client.py` 解析了新字段

### 配套：调 backend 联调时
MCDR 调 `http://pchsystem-backend-1:8000`（容器网络）。改 backend 后让 MCDR 看到新响应，只需 backend 热重载（uvicorn --reload 自动），**不需要** reload MCDR 插件——除非 `client.py` 的字段解析逻辑也改了。

---

## 8. 与根规范的关系

- 遵守根 [`CLAUDE.md`](../CLAUDE.md) 的命名分层（§1）与全局红线（§3 S-1~S-2、R-1~R-12）。
- 本文件的 RS-x 红线是**服务特有**补充，不覆写全局红线。
- 命名 / 全局红线 / 技术栈若有冲突，以根 CLAUDE.md 为准并修正本文件。

---

*最后更新：2026-07-11（`!!submit` 回执进一步降噪：已备齐/进度已满行也折叠（`scanner.skip_is_ready` 复用 `REASON_READY` 判定，新增 `SHEET_SUBMIT_READY_FOLDED_LINE`）；归桶 ready 优先于 noise）*

*增量（2026-07-11）：`!!submit` 一键提交新根 + 回执折叠降噪：新增 `!!submit` 第三命令根（无参重开上次表格直接提交，有参指定表），复用 `!!sheet` 的 `GET /me/last_sheet` 存储；与本人无关的跳过行折叠为末尾一行计数 `SHEET_SUBMIT_FOLDED_LINE`）*

*增量（2026-07-09）：子物品嵌套行 issue #19 + sheets.py 包化重构：新增 `addsub`/`delsub`/`setsub` 命令；`messages.py` 缩进渲染子行 + 父行 `+N子件` 徽标；按钮紧凑化（单字 `[认][改][-][+]` + RText hover）；`scanner.py` 不改（子行同列表自动匹配）；后端包化拆分 `sheets/` 包 + `translation.py` 公共翻译；详见 [`api/sheets.md`](../Docs/architecture/api/sheets.md) §14 增量日志）*
