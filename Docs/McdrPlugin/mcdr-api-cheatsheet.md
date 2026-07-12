# MCDReforged API 速查表

> **本项目红线 S-1**（[根 CLAUDE.md](../../CLAUDE.md) §0）：MCDR 相关 API 必须联网核实后再使用，本表所有 API 已于 2026-07-02 联网核对。
> **MCDR 版本**：2.15.7（截至 2026-07-02，最新稳定版）
> **官方文档总入口**：<https://docs.mcdreforged.com/zh-cn/latest/>
>
> 本文件只做「速查」。完整语义、版本变更、参数细节请点击每行附带的官方文档 URL。

---

## 目录

1. [命令节点（Command Node）](#1-命令节点command-node)
2. [节点链式 API](#2-节点链式-api)
3. [CommandSource 常用成员](#3-commandsource-常用成员)
4. [回调签名（动态适配）](#4-回调签名动态适配)
5. [权限等级表](#5-权限等级表)
6. [RText 色彩系统](#6-rtext-色彩系统)
7. [`!!help` 集成](#7-help-集成)
8. [耗时任务（schedule_task）](#8-耗时任务schedule_task)
9. [配置加载（load_config_simple）](#9-配置加载load_config_simple)

---

## 1. 命令节点（Command Node）

**API 包路径**：`mcdreforged.api.command`

所有节点类位于 `mcdreforged.command.builder.nodes`：
- 字面量与基础：`basic.Literal`、`basic.ArgumentNode`、`basic.AbstractNode`
- 参数：`arguments.Text`、`arguments.QuotableText`、`arguments.GreedyText`、`arguments.Number`、`arguments.Integer`、`arguments.Float`、`arguments.Boolean`、`arguments.Enumeration`

**重要约束**：`Literal` 是**唯一**可作为命令树根节点的类型（`register_command(root_node)` 要求 `root_node: Literal`）。详见 [PluginServerInterface.register_command](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html#PluginServerInterface.register_command)。

| 节点类 | 用途 | 示例 | 官方文档 URL |
|---|---|---|---|
| `Literal(literal)` | 字面量节点，**唯一可作为根**；可传单个 str 或可迭代 str（多个等价字面量共享同一节点） | `Literal('!!submit')` / `Literal(['!!reload', '!!r'])` | [command.html#Literal](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.Literal) |
| `Text(name)` | 单字参数，读到空格即止（不支持空格） | `Text('project')` | [command.html#Text](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Text) |
| `QuotableText(name)` | 支持双引号包裹含空格的文本，支持 `\\` 转义 `"` 与 `\\` | `QuotableText('message')` | [command.html#QuotableText](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.QuotableText) |
| `GreedyText(name)` | 贪婪吞掉剩余所有输入（含空格）；后不应再挂子节点 | `GreedyText('reason')` | [command.html#GreedyText](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.GreedyText) |
| `Integer(name)` | 整数参数；非法抛 `InvalidInteger`，越界抛 `NumberOutOfRange` | `Integer('x').at_min(-30000000).at_max(30000000)` | [command.html#Integer](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Integer) |
| `Float(name)` | 浮点参数；非法抛 `InvalidFloat` | `Float('score').at_min(0)` | [command.html#Float](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Float) |
| `Number(name)` | int 或 float 自动识别；非法抛 `InvalidNumber` | `Number('value').in_range(0, 100)` | [command.html#Number](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Number) |
| `Boolean(name)` | 仅接受 `true` / `false`（大小写不敏感）；非法抛 `InvalidBoolean`。v2.3.0+ | `Boolean('confirm')` | [command.html#Boolean](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Boolean) |
| `Enumeration(name, EnumClass)` | 枚举参数，绑定 Python `Enum` 子类；非法抛 `InvalidEnumeration`。v2.3.0+ | `Enumeration('color', MyColor)` | [command.html#Enumeration](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.Enumeration) |

### 节点构造通用关键字参数（ArgumentNode，v2.13.0+ / v2.14.0+）

所有 `ArgumentNode` 子类（`Text`/`Integer`/`Boolean`/`Enumeration` 等）的 `__init__` 都接受：

```python
ArgumentNode(name: str, *, accumulate: bool | None = None, metavar: str | None = None)
```

- `accumulate=True`：同节点多次访问时把值收集为 list（v2.13.0+）
- `metavar="..."`：覆盖命令补全提示中显示的占位名（v2.14.0+）

> 来源：[command.html#ArgumentNode](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.ArgumentNode)

### 数值范围与文本长度约束

| 方法 | 适用类 | 含义 |
|---|---|---|
| `.at_min(min_value)` | `NumberNode` | 下界（含） |
| `.at_max(max_value)` | `NumberNode` | 上界（含） |
| `.in_range(min_value, max_value)` | `NumberNode` | 同时设置上下界（含） |
| `.at_min_length(min_length)` | `TextNode` | 文本长度下界（含） |
| `.at_max_length(max_length)` | `TextNode` | 文本长度上界（含） |
| `.in_length_range(min_length, max_length)` | `TextNode` | 同时设置长度上下界 |

> 来源：[command.html#NumberNode](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.NumberNode) / [command.html#TextNode](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.arguments.TextNode)

### 项目内实战片段

```python
# 对应 Docs/architecture/services/mcdr-plugin.md §2 命令注册
from mcdreforged.api.all import *

server.register_command(
    Literal('!!submit')
    .then(Literal('hand')
          .then(Text('project').runs(submit_hand)))
    .then(Text('project')
          .then(Integer('x').at_min(-30000000).at_max(30000000)
                .then(Integer('y').at_min(-64).at_max(320)
                      .then(Integer('z').at_min(-30000000).at_max(30000000)
                            .runs(submit_box))))))
```

---

## 2. 节点链式 API

所有节点（含 `Literal` 和 `ArgumentNode` 子类）都继承自 `AbstractNode`，链式方法返回 `Self`（可链式调用）。来源：[command.html#AbstractNode](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode)

| 方法 | 用途 | 示例 | 官方文档 URL |
|---|---|---|---|
| `.then(node)` | 附加子节点，返回自身（用于建树） | `Literal('!!email').then(Literal('list'))` | [AbstractNode.then](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.then) |
| `.runs(callback)` | 设置节点回调：命令解析在此结束时调用。回调签名动态适配（见 [§4](#4-回调签名动态适配)） | `Literal('!!ping').runs(lambda src: src.reply('pong'))` | [AbstractNode.runs](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.runs) |
| `.requires tester)` | **要求**：tester 返回 `False` 时抛 `RequirementNotMet` 异常，向玩家报错（节点仍可见） | `node.requires(lambda src: src.has_permission(3))` | [AbstractNode.requires](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.requires) |
| `.precondition(tester)` | **前置条件**：tester 返回 `False` 时**过滤节点**，视为不存在（隐藏，不报错）。v2.14.0+ | `node.precondition(lambda src: src.has_permission(3))` | [AbstractNode.precondition](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.precondition) |
| `.suggests(provider)` | 设置 Tab 补全建议；`Literal` **不支持**此方法 | `Text('player').suggests(lambda: online_players)` | [AbstractNode.suggests](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.suggests) |
| `.on_error(error_type, handler, *, handled=False)` | 当此节点抛出 `error_type`（及其子类）异常时调用 handler | `node.on_error(UnknownArgument, hint_usage)` | [AbstractNode.on_error](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.on_error) |
| `.on_child_error(error_type, handler, *, handled=False)` | 类似 `on_error`，但仅对**子节点**抛出的异常响应 | `root.on_child_error(RequirementNotMet, warn_denied)` | [AbstractNode.on_child_error](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.on_child_error) |
| `.redirects(node)` | 把后续子节点解析重定向到另一个节点（用于短命令、循环重入） | `Literal('!!email here').redirects(email_root)` | [AbstractNode.redirects](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.redirects) |
| `.print_tree(line_writer=print)` | 调试用：打印命令树。v2.6.0+ | `root.print_tree(server.logger.info)` | [AbstractNode.print_tree](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.print_tree) |

### `requires` vs `precondition` —— 关键差异

| 维度 | `requires` | `precondition` |
|---|---|---|
| 失败行为 | 抛 `RequirementNotMet` 异常 | 节点被「过滤」、视为不存在 |
| 玩家可见性 | 节点仍在帮助/补全中可见 | 节点对玩家**完全隐藏** |
| 适用场景 | 权限不足要给出反馈 | 仅在特定条件下才暴露的子命令（如未绑定时不显示 `!!unbind`） |
| 引入版本 | 一直存在 | v2.14.0+ |
| 多次调用 | 多次 `requires` 叠加为「与」逻辑（v2.7.0+） | 同样支持叠加 |

> 来源：[AbstractNode.requires](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.requires) / [AbstractNode.precondition](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.precondition)

### `requires` 自定义失败消息

```python
node.requires(
    lambda src, ctx: is_legal(ctx['target']),
    lambda src, ctx: 'target {} is illegal'.format(ctx['target'])
)
```

第二个可调用对象（`failure_message_getter`）用于生成失败时的提示文本，可以是 `str` 或 `RTextBase`。

### `Requirements` 工具类（v2.6.0+）

来源：[command.html#Requirements](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.tools.Requirements)

| 类方法 | 返回 | 用途 |
|---|---|---|
| `Requirements.has_permission(level)` | `Callable[[CommandSource], bool]` | 权限等级 ≥ level |
| `Requirements.is_player()` | `Callable[[CommandSource], bool]` | 仅玩家可执行 |
| `Requirements.is_console()` | `Callable[[CommandSource], bool]` | 仅控制台可执行 |
| `Requirements.argument_exists(arg_name)` | `Callable[[CommandSource, dict], bool]` | 上下文中已分配该参数 |

```python
from mcdreforged.api.command import Requirements

Literal('!!reload')
    .requires(Requirements.has_permission(3))
    .runs(reload_plugin)
```

---

## 3. CommandSource 常用成员

**API 包路径**：`mcdreforged.api.types`（`CommandSource` 在 `mcdreforged.command.command_source`）

继承树：

```
CommandSource
├── InfoCommandSource
│   ├── PlayerCommandSource       # 玩家
│   └── ConsoleCommandSource      # 控制台
└── PluginCommandSource           # 插件本身
```

来源：[command.html#CommandSource](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.command_source.CommandSource)

| 成员 | 签名 | 用途 |
|---|---|---|
| `is_player` | `property -> bool` | 是否为 `PlayerCommandSource` |
| `is_console` | `property -> bool` | 是否为 `ConsoleCommandSource` |
| `player` | `str`（仅 `PlayerCommandSource`） | 玩家名 |
| `get_server()` | `-> ServerInterface` | 返回 ServerInterface 实例 |
| `get_permission_level()` | `-> int` | 当前 source 的权限等级（0-4） |
| `has_permission(level)` | `(level: int) -> bool` | 是否有 ≥ level 的权限 |
| `has_permission_higher_than(level)` | `(level: int) -> bool` | 是否有 > level 的权限（严格大于） |
| `reply(message, **kwargs)` | `(message: str \| RTextBase, **kwargs) -> None` | 向 source 回复消息（玩家用 tellraw，控制台用 logger） |

### 子类特有方法

| 类 | 方法 | 说明 |
|---|---|---|
| `PlayerCommandSource` | `reply(message, *, encoding=None)` | 接受 `str` 或 `RTextBase`；`encoding` 透传给 `ServerInterface.tell()` |
| `ConsoleCommandSource` | `reply(message, *, console_text=None)` | `console_text` 非空时覆盖 message 用于控制台输出 |
| `InfoCommandSource` | `get_info() -> Info` | 返回触发此 source 的 `Info` 实例 |

> 来源：[command.html#PlayerCommandSource](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.command_source.PlayerCommandSource) / [ConsoleCommandSource](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.command_source.ConsoleCommandSource)

### 实战片段

```python
from mcdreforged.api.all import *

def submit_box(source: CommandSource, ctx: dict):
    if not source.is_player:
        source.reply('该命令仅玩家可用')
        return
    player = source.player            # 玩家名
    uuid = offline_uuid(player)       # 项目内推导，见 mcdr-plugin.md §3.4
    x, y, z = ctx['x'], ctx['y'], ctx['z']
    server = source.get_server()
    # ... 扫描箱子上报 ...
    source.reply(
        RTextList(
            RText('提交成功', RColor.green),
            RText(f' ({x}, {y}, {z})', RColor.gray),
        )
    )
```

---

## 4. 回调签名（动态适配）

**核心机制**：MCDR 对 `runs()` / `requires()` / `precondition()` / `suggests()` / `on_error()` 等所有回调函数采用**动态参数适配**——根据函数声明的参数数量（0、1 或 2）自动注入对应参数。

| 参数数量 | 注入的参数 |
|---|---|
| 0 个 | 无 |
| 1 个 | `CommandSource` |
| 2 个 | `CommandSource`, `CommandContext`（即 `dict` 子类） |

来源：[AbstractNode.runs](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.nodes.basic.AbstractNode.runs)

### 4 种合法的回调写法

```python
# 写法 1：无参（仅做副作用，不关心 source）
def callback_0():
    pass

# 写法 2：只用 source
def callback_1(source: CommandSource):
    source.reply('pong')

# 写法 3：source + context
def callback_2(source: CommandSource, context: dict):
    project = context['project']
    coords = (context['x'], context['y'], context['z'])

# 写法 4：lambda
callback_4 = lambda src: src.reply('pong')

# 以下注册等价
node1.runs(callback_0)
node2.runs(callback_1)
node3.runs(callback_2)
node4.runs(callback_4)
```

### `CommandContext` —— 取参数的字典

来源：[command.html#CommandContext](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.common.CommandContext)

```python
class CommandContext(CommandSource, command: str):
    # 继承自 dict[str, Any]
    source: CommandSource             # 触发命令的 source
    command: str                      # 完整命令字符串
    command_read: str                 # 已解析部分
    command_remaining: str            # 待解析部分
    cursor: int                       # 解析游标位置
    node_path: List[AbstractNode]     # 从根到当前节点的路径
```

**最常用法**：用 `context['arg_name']` 取出 `ArgumentNode` 解析后的值。`arg_name` 就是节点构造时传入的第一个参数 `name`。

```python
# 命令树
Literal('!!submit').then(
    Text('project').then(
        Integer('x').then(
            Integer('y').then(
                Integer('z').runs(submit_box)
            )
        )
    )
)

# 回调
def submit_box(source: CommandSource, ctx: dict):
    project = ctx['project']   # Text 节点的解析值
    x = ctx['x']               # Integer 节点的解析值
    y = ctx['y']
    z = ctx['z']
```

> `CommandContext` 继承 `dict`，所有 dict 方法（`.get()`、`.keys()`、`in` 等）都可用。

---

## 5. 权限等级表

来源：[Permission](https://docs.mcdreforged.com/en/latest/permission.html)（中文：[权限](https://docs.mcdreforged.com/zh-cn/latest/permission.html)）

| 名称 | 整数值 | 说明 |
|---|---|---|
| `owner` | **4** | 最高权限，能访问物理服务器。例如：服务器拥有者 |
| `admin` | **3** | 能控制 MCDR 与 Minecraft 服务器。例如：MC OP |
| `helper` | **2** | 管理员助手。例如：可信成员 |
| `user` | **1** | 普通玩家（**默认等级**） |
| `guest` | **0** | 访客或捣乱者 |

**重要**：**控制台输入**（console）的权限等级**始终是 `owner` (4)**，无论谁在操作。

### 配置与热重载

- 配置文件：`permission.yml`
- `default_level`：新玩家默认等级（默认 `user`）
- 热重载命令：`!!MCDR reload permission`
- 编程接口：
  - `server.get_permission_level(player) -> int`（[doc](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.get_permission_level)）
  - `server.set_permission_level(player, value)`（[doc](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.set_permission_level)）
  - `server.reload_permission_file()`（[doc](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.reload_permission_file)）

### API 用法（项目内推荐写法）

#### 方式 1：`CommandSource.has_permission(level)`

```python
def submit(source: CommandSource, ctx: dict):
    if not source.has_permission(2):    # 至少 helper
        source.reply('权限不足')
        return
```

#### 方式 2：`Requirements.has_permission(level)` + `requires`（最简洁）

```python
from mcdreforged.api.command import Literal, Requirements

Literal('!!reload')
    .requires(Requirements.has_permission(3))   # 至少 admin
    .runs(reload_plugin)
```

#### 方式 3：`precondition` 隐藏命令（不对低权限者暴露）

```python
Literal('!!admin_panel')
    .precondition(lambda src: src.has_permission(3))   # 不足则隐藏
    .runs(show_admin_panel)
```

---

## 6. RText 色彩系统

**API 包路径**：`mcdreforged.api.rtext`

`RText` 是 MCDR 提供的 Minecraft 文本组件库（基于 Pandaria98 的 stext）。所有 `source.reply()` / `server.tell()` / `server.broadcast()` 都接受 `str` 或 `RTextBase`。

来源：[minecraft_tools.html#RText](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html)

### 核心类

| 类 | 用途 | URL |
|---|---|---|
| `RTextBase` | 抽象基类，所有 RText 的父类 | [RTextBase](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.text.RTextBase) |
| `RText(text, color=None, styles=None)` | 普通文本组件 | [RText](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.text.RText) |
| `RTextList(*args)` | 多组件拼接列表（每个子项可独立设色/事件） | [RTextList](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.text.RTextList) |

### `RColor` —— 16 色枚举 + reset

来源：[minecraft_tools.html#RColor](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.style.RColor)

| 字段 | MC 码 | ANSI（控制台） |
|---|---|---|
| `RColor.black` | `§0` | `\x1b[30m` |
| `RColor.dark_blue` | `§1` | `\x1b[34m` |
| `RColor.dark_green` | `§2` | `\x1b[32m` |
| `RColor.dark_aqua` | `§3` | `\x1b[36m` |
| `RColor.dark_red` | `§4` | `\x1b[31m` |
| `RColor.dark_purple` | `§5` | `\x1b[35m` |
| `RColor.gold` | `§6` | `\x1b[33m` |
| `RColor.gray` | `§7` | `\x1b[37m\x1b[2m` |
| `RColor.dark_gray` | `§8` | `\x1b[37m\x1b[2m` |
| `RColor.blue` | `§9` | `\x1b[94m` |
| `RColor.green` | `§a` | `\x1b[92m` |
| `RColor.aqua` | `§b` | `\x1b[96m` |
| `RColor.red` | `§c` | `\x1b[91m` |
| `RColor.light_purple` | `§d` | `\x1b[95m` |
| `RColor.yellow` | `§e` | `\x1b[93m` |
| `RColor.white` | `§f` | `\x1b[37m` |
| `RColor.reset` | `§r` | `\x1b[0m` |

**RGB 自定义色**（MC 1.16+）：`RColorRGB(0xRRGGBB)` 或 `RColorRGB.from_rgb(r, g, b)`。来源：[RColorRGB](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.style.RColorRGB)

> 项目色彩标准详见 [McdrPlugin/CLAUDE.md](../../McdrPlugin/CLAUDE.md)（如已生成）—— 本表只列 MCDR 内置色，项目应统一在 RColor 16 色或 RColorRGB 自定义色中选型。

### `RStyle` —— 文本样式

来源：[minecraft_tools.html#RStyle](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.style.RStyle)

| 字段 | MC 码 | 含义 |
|---|---|---|
| `RStyle.bold` | `§l` | 加粗 |
| `RStyle.italic` | `§o` | 斜体 |
| `RStyle.underlined` | `§n` | 下划线 |
| `RStyle.strikethrough` | `§m` | 删除线 |
| `RStyle.obfuscated` | `§k` | 乱码（遮挡） |

### `RAction`（= `RClickAction`）—— 点击事件

> **注意**：v2.15.0 起新增独立类 `RClickAction`，旧名 `RAction` 保留为别名。**为最大兼容性，项目内统一使用 `RAction`**。来源：[minecraft_tools.html#RClickAction](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.click_event.RClickAction)

| 字段 | 用途 | 备注 |
|---|---|---|
| `RAction.open_url` | 打开 URL | 客户端会弹确认 |
| `RAction.run_command` | 立即执行命令 | MC ≥ 1.19.1 仅允许以 `/` 开头的命令 |
| `RAction.suggest_command` | 把文本填入聊天栏（不执行） | 推荐，玩家可编辑后回车 |
| `RAction.copy_to_clipboard` | 复制到剪贴板 | MC 1.15+ |
| `RAction.open_file` | 打开文件 | 客户端通常拒绝 |
| `RAction.change_page` | 翻页（仅成书） | |
| `RAction.show_dialog` | 打开对话框 | MC 1.21.6+（v2.15.0+） |
| `RAction.custom` | 自定义事件 | MC 1.21.6+（v2.15.0+） |

### `RHoverAction` —— 悬停事件（v2.15.0+）

来源：[minecraft_tools.html#RHoverAction](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.hover_event.RHoverAction)

| 字段 | 用途 |
|---|---|
| `RHoverAction.show_text` | 悬停显示文本 |
| `RHoverAction.show_entity` | 悬停显示实体信息 |
| `RHoverAction.show_item` | 悬停显示物品信息 |

### 链式 API（在 `RTextBase` 上）

| 方法 | 简写 | 用途 |
|---|---|---|
| `.set_color(color)` | | 设置颜色 |
| `.set_styles(styles)` | | 设置样式（单个或可迭代） |
| `.set_click_event(event)` / `.set_click_event(action, value)` | `.c(...)` | 设置点击事件 |
| `.set_hover_event(event)` | | 设置悬停事件 |
| `.set_hover_text(*args)` | `.h(*args)` | 设置悬停文本（便捷） |
| `.copy()` | | 深拷贝 |

> 来源：[RTextBase](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.minecraft.rtext.text.RTextBase)

### 实战片段：多色拼接 + 可点击链接

```python
from mcdreforged.api.all import *

def reply_submit_success(source: CommandSource, project: str, x: int, y: int, z: int):
    """提交成功的多色、可点击回复。"""
    # 主体：绿色「提交成功」 + 灰色坐标
    msg = RTextList(
        RText('✔ 提交成功', RColor.green, RStyle.bold),
        RText(' → ', RColor.dark_gray),
        RText(f'{project}', RColor.aqua)
            .c(RAction.suggest_command, f'!!project info {project}')
            .h(RText(f'点击查看项目 {project} 进度', RColor.yellow)),
        RText(f' @({x}, {y}, {z})', RColor.dark_gray),
    )
    source.reply(msg)
```

**逐行解释**：
1. `RText('✔ 提交成功', RColor.green, RStyle.bold)`：绿色加粗文本
2. `RText(' → ', RColor.dark_gray)`：灰色分隔符
3. `RText(f'{project}', RColor.aqua).c(RAction.suggest_command, ...)`：青色项目名，点击后向聊天栏填入 `!!project info <project>`，悬停显示黄色提示
4. `RTextList(...)` 把所有片段拼成一个组件

### `RTextMCDRTranslation` —— 多语言（v2.1.0+）

来源：[RTextMCDRTranslation](https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html#mcdreforged.translation.translation_text.RTextMCDRTranslation)

```python
# 注册翻译（在 on_load）
server.register_translation('en_us', {'my_plugin.welcome': 'Welcome, {}'})

# 使用
server.rtr('my_plugin.welcome', player_name)
# 返回 RTextMCDRTranslation，根据玩家 preference 自动选语言
```

---

## 7. `!!help` 集成

MCDR 内置 `!!help` 命令，会聚合所有插件注册的帮助消息，并按玩家权限过滤显示。

### API

来源：[PluginServerInterface.register_help_message](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html#PluginServerInterface.register_help_message)

```python
def register_help_message(
    prefix: str,
    message: str | RTextBase | Mapping[str, str | RTextBase],
    permission: int = 0
) -> None
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `prefix` | `str` | 命令前缀，玩家点击消息后会**建议**（suggest）此字符串。建议设为插件入口命令 |
| `message` | `str \| RTextBase \| Mapping[lang, str \| RTextBase]` | 命令描述。可以是纯字符串、RText 富文本，或「语言→描述」的多语言映射 |
| `permission` | `int`（默认 0） | 最低权限等级，玩家权限 < 此值时不显示 |

### 项目内示例

```python
def on_load(server: PluginServerInterface, prev):
    server.register_help_message(
        '!!submit',
        RText('提交物品到项目', RColor.green),
        permission=1,   # user 及以上可见
    )
    server.register_help_message(
        '!!bind',
        {'en_us': 'Bind your web account',
         'zh_cn': '绑定 Web 账号'},
    )
    server.register_help_message(
        '!!admin_panel',
        RText('管理员面板', RColor.gold),
        permission=3,   # admin 及以上可见
    )
```

### `!!help` 命令本身

来源：[!!help command](https://docs.mcdreforged.com/en/latest/command/help.html)（中文：[!!help 命令](https://docs.mcdreforged.com/zh-cn/latest/command/help.html)）

- 玩家输入 `!!help` 后，MCDR 列出该玩家可见的所有插件注册的帮助消息
- 点击某条消息会触发 `prefix` 的 suggest_command 行为
- 按权限等级自动过滤

---

## 8. 耗时任务（schedule_task）

> **项目红线 R-12**（[根 CLAUDE.md](../../CLAUDE.md) §3）：MCDR **阻塞调用必须卸载到后台线程**（`@new_thread`）；HTTP 调用必含**超时 + 重试 + 失败回执**。`schedule_task` **不能**用于卸载阻塞工作（见下方「常见误区」）。

### API

来源：[ServerInterface.schedule_task](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.schedule_task)

```python
def schedule_task(
    callable: Callable[[], T] | Coroutine[Any, Any, T],
    *,
    block: bool = False,
    timeout: float | None = None
) -> Future[T]
```

| 参数 | 说明 |
|---|---|
| `callable` | 无参可调用对象，或无参协程对象 |
| `block` | 默认 `False`；设为 `True` 则阻塞当前线程直到执行完毕 |
| `timeout` | 仅当 `block=True` 时生效，阻塞超时秒数 |
| 返回值 | `concurrent.futures.Future[T]` |

**关键**：`schedule_task` 把任务调度到 **task executor 线程**（同步回调）或 **async task executor 线程**（协程）。事件监听、命令回调默认运行在 task executor 线程，长时间阻塞会卡住 MCDR 主循环。

> ⚠️ **常见误区（曾导致 `!!PCH login` 卡顿的根因）**：`schedule_task` **不卸载**阻塞工作。它的**同步回调就跑在 task executor = MCDR 主线程**上，里面一句 `requests.post(...)` 会卡住整个主循环（命令、事件、server 输出解析全部停滞），玩家观感卡顿。
>
> **正确心智模型**：
> - **阻塞调用（`requests` / RCON / 大文件 I/O）→ `@new_thread`**（`mcdreforged.api.decorator.new_thread`），让它在独立 daemon 线程跑。
> - **`schedule_task` 仅用于**：①把**协程**交给 async executor（不阻塞主线程）；②把任务**延迟到主线程下一 tick**；③从后台线程**回到主线程**执行主线程状态相关的逻辑。
> - `server.tell()` / `source.reply()` **线程安全**（[PluginServerInterface 文档](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html)），可在 `@new_thread` 的后台线程里直接调用，无需再 `schedule_task` 回主线程。

### 项目内 HTTP 调用模板（红线 R-12 实施）

```python
import requests
from mcdreforged.api.all import *   # 含 new_thread

API_URL = 'http://backend:8000'
SERVICE_TOKEN = '...'
TIMEOUT_SECONDS = 10

def _call_backend_sync(path: str, payload: dict) -> dict:
    """同步阻塞式 HTTP 调用 —— 必须在 @new_thread 后台线程内运行，禁止放进 schedule_task。"""
    return requests.post(
        f'{API_URL}{path}',
        json=payload,
        timeout=TIMEOUT_SECONDS,        # 超时
        headers={'X-Service-Token': SERVICE_TOKEN},
    ).json()

def submit_to_backend(server: PluginServerInterface, source: CommandSource, payload: dict):
    """后台线程调度，避免阻塞 MCDR 主循环。"""
    @new_thread('pch submit')           # 派生 daemon 线程；调用立即返回 FunctionThread
    def task():
        try:
            result = _call_backend_sync('/submissions', payload)
            source.reply(RText('提交成功', RColor.green))   # 线程安全，后台线程直接调
            return result
        except requests.RequestException as e:
            # 失败回执 —— 红线 R-12
            source.reply(RText(f'提交失败：{e}', RColor.red))
    task()   # 立即返回；HTTP 在后台线程跑，主循环不阻塞
```

### 同步判断当前是否在 executor 线程

```python
if server.is_on_executor_thread():
    # 同步执行
    ...
else:
    # 调度到 executor 线程
    server.schedule_task(...)
```

> 来源：[is_on_executor_thread](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.is_on_executor_thread) / [is_on_async_executor_thread](https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html#ServerInterface.is_on_async_executor_thread)（v2.14.0+）

### 带重试的完整模板（红线 R-12 全面落地）

```python
import time
import requests
from typing import Callable, TypeVar
from mcdreforged.api.all import *

T = TypeVar('T')

def call_with_retry(
    source: CommandSource,
    action: str,                 # 给玩家看的行为名（如「提交箱子上报」）
    fn: Callable[[], T],
    retries: int = 3,
    base_delay: float = 0.5,
) -> T | None:
    """同步执行 fn，带指数退避重试；失败时给 source 回执。"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except (requests.RequestException, ConnectionError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    # 全部失败 —— 回执
    source.reply(
        RTextList(
            RText(f'[{action}] 失败：', RColor.red),
            RText(str(last_exc)[:200], RColor.dark_red),
        )
    )
    return None

def submit_box_async(server: PluginServerInterface, source: CommandSource, payload: dict):
    """标准模式：@new_thread + 重试 + 回执。"""
    @new_thread('pch submit box')
    def task():
        return call_with_retry(
            source, '提交箱子上报',
            lambda: requests.post(
                f'{CONFIG.api_url}/submissions',
                json=payload,
                timeout=CONFIG.request_timeout,
                headers={'X-Service-Token': CONFIG.service_token},
            ).json(),
            retries=CONFIG.retry_times,
        )
    task()   # 后台线程执行，主循环不阻塞
```

**为什么不用 `requests` 内置的 `urllib3.Retry` / `HTTPAdapter`**：
- MCDR 插件部署在容器内，依赖管理需谨慎；手写 4 行循环更直观、可控
- 失败回执需要直达玩家，`Retry` 不能感知 `CommandSource`

### 哪些操作**必须**卸载到后台线程（`@new_thread`）

| 操作 | 阻塞时长 | 必须卸载到后台线程 |
|---|---|---|
| HTTP 调用（`requests` 同步） | 数十 ms ~ 秒级 | 是 → `@new_thread` |
| HTTP 调用（`httpx` 异步协程） | 数十 ms ~ 秒级 | `schedule_task` 传协程（async executor） |
| 文件 I/O（大文件读写） | 不定 | 是 → `@new_thread` |
| RCON 查询（`server.rcon_query`） | 数 ms ~ 百 ms | 视频率，批量扫描建议 `@new_thread` |
| 网络解析（`amulet-nbt` SNBT 解析） | 微秒级 | 否 |
| 简单字典操作 | 微秒级 | 否 |

> 红线 R-12 出处：[根 CLAUDE.md](../../CLAUDE.md) §3 / 架构 [mcdr-plugin.md §3.6](../architecture/services/mcdr-plugin.md)

---

## 9. 配置加载（load_config_simple）

### API

来源：[PluginServerInterface.load_config_simple](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html#PluginServerInterface.load_config_simple)

```python
def load_config_simple(
    file_name: str | None = None,
    default_config: dict | None = None,
    *,
    in_data_folder: bool = True,
    echo_in_console: bool = True,
    source_to_reply: CommandSource | None = None,
    target_class: Type[SerializableType] | Type[None] | None = None,
    encoding: str = 'utf8',
    file_format: Literal['json', 'yaml'] | None = None,
    failure_policy: Literal['regen', 'raise'] = 'regen',
    data_processor: Callable[[...], bool] | None = None,
    pydantic_model_validate_kwargs: dict | None = None
) -> None | int | float | str | bool | list | dict | SerializableType
```

**关键参数**：

| 参数 | 说明 |
|---|---|
| `file_name` | 配置文件名或路径。默认推断为 `<plugin_id>.json`（实际：`<data_folder>/<file_name>`） |
| `default_config` | dict 形式的默认值；文件缺失时用此生成。若给了 `target_class` 则用类字段默认值 |
| `target_class` | **`Serializable` 子类**（来自 `mcdreforged.api.utils`）或 **`pydantic.BaseModel` 子类**（v2.14.0+）。指定后返回反序列化实例 |
| `in_data_folder` | 默认 `True`，文件相对插件 `config/<plugin_id>/` 目录（[get_data_folder](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html#PluginServerInterface.get_data_folder)） |
| `encoding` | 默认 `utf8` |
| `file_format` | `'json'` / `'yaml'` / `None`（按文件扩展名推断） |
| `failure_policy` | `'regen'`（默认，重新生成） / `'raise'`（直接抛异常） |

### `Serializable` 基类

**API 包路径**：`mcdreforged.api.utils`

来源：[Utilities → Serializable](https://docs.mcdreforged.com/en/latest/code_references/Utils.html)（点击 `Serializable` 类查看完整签名）

`Serializable` 是 MCDR 内置的类型化配置基类，支持嵌套、默认值、list/dict 泛型。子类化后字段自动成为配置键。

### 项目内推荐写法（target_class + Serializable）

```python
from mcdreforged.api.all import *
from mcdreforged.api.utils import Serializable

class PchSystemConfig(Serializable):
    # 字段名 = 配置键（snake_case，遵循项目命名规范）
    api_url: str = 'http://backend:8000'
    service_token: str = ''
    rcon_address: str = '127.0.0.1'
    rcon_port: int = 25575
    rcon_password: str = ''
    request_timeout: float = 10.0
    retry_times: int = 3

    def validate(self) -> 'PchSystemConfig':
        # 项目规范：在系统边界做输入校验
        if self.request_timeout <= 0:
            raise ValueError('request_timeout must be positive')
        if self.retry_times < 0:
            raise ValueError('retry_times must be >= 0')
        return self

CONFIG: PchSystemConfig

def on_load(server: PluginServerInterface, prev):
    global CONFIG
    CONFIG = server.load_config_simple(
        target_class=PchSystemConfig,
    ).validate()
    server.logger.info(f'配置加载完成，API = {CONFIG.api_url}')
```

> 配套文件：`config/pch_system/config.json`（实际路径由 `in_data_folder=True` 决定，参见项目 `TestServer/config/pch_system_config.json`）

### 写回配置

```python
server.save_config_simple(CONFIG, 'config.json')
```

来源：[PluginServerInterface.save_config_simple](https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html#PluginServerInterface.save_config_simple)

### 替代写法（pydantic，v2.14.0+）

```python
from pydantic import BaseModel

class PydanticConfig(BaseModel):
    api_url: str = 'http://backend:8000'
    service_token: str = ''

def on_load(server: PluginServerInterface, prev):
    config = server.load_config_simple(target_class=PydanticConfig)
```

> 项目根规范（[CLAUDE.md](../../CLAUDE.md) §2）后端是 FastAPI 已用 Pydantic，**MCDR 端为减少依赖推荐用 `Serializable`**；若 MCDR 端有跨端共享 Pydantic 模型的需求再用 BaseModel。

---

## 附录：命令异常家族

来源：[command.html#Exceptions](https://docs.mcdreforged.com/en/latest/code_references/command.html#module-mcdreforged.command.builder.exception)

```
CommandErrorBase
├── IllegalNodeOperation              # 不支持的操作（如 Literal.suggests）
└── CommandError
    ├── UnknownCommand                # 解析完但无回调
    ├── UnknownArgument               # 还有剩余输入但无匹配子节点
    ├── RequirementNotMet             # requires 失败
    └── CommandSyntaxError
        └── IllegalArgument
            ├── AbstractOutOfRange
            │   ├── NumberOutOfRange  # Number/Integer/Float 越界
            │   └── TextLengthOutOfRange
            ├── InvalidNumber / InvalidInteger / InvalidFloat
            ├── IllegalEscapesUsage
            ├── UnclosedQuotedString
            ├── EmptyText
            ├── InvalidBoolean
            └── InvalidEnumeration
```

### 项目内常用错误处理

```python
from mcdreforged.api.command import (
    Literal, Integer, Requirements,
    UnknownCommand, UnknownArgument, RequirementNotMet,
    NumberOutOfRange, InvalidInteger,
)

root = (
    Literal('!!submit')
    .then(Integer('x').then(Integer('y').then(Integer('z')
        .runs(submit_box)
        .on_error(NumberOutOfRange, lambda src, err: src.reply(
            RTextList(RText('坐标越界：', RColor.red), str(err)))
        )
        .on_error(InvalidInteger, lambda src, err: src.reply(
            RText('坐标必须是整数', RColor.red)))
    )))
    .on_child_error(UnknownArgument, lambda src, err: src.reply(
        RTextList(
            RText('未知参数。', RColor.yellow),
            RText('用法：!!submit <project> <x> <y> <z>', RColor.gray)
                .c(RAction.suggest_command, '!!submit '),
        )
    ), handled=True)
    .on_child_error(RequirementNotMet, lambda src, err: src.reply(
        RText('权限不足或条件不满足', RColor.red)))
)
```

**关键 API**：

| 异常方法 | 用途 |
|---|---|
| `err.get_parsed_command()` | 已成功解析的命令前缀 |
| `err.get_failed_command()` | 失败时的命令前缀（含出错位置） |
| `err.get_error_segment()` | 出错的命令片段 |
| `err.set_handled()` | 告诉 MCDR 错误已处理，不再显示默认错误消息 |
| `on_error(handler, *, handled=True)` | 自动调用 `set_handled()` |

> 来源：[CommandError](https://docs.mcdreforged.com/en/latest/code_references/command.html#mcdreforged.command.builder.exception.CommandError)

---

## 附录：导入速查

### 一次性导入（推荐）

```python
from mcdreforged.api.all import *
```

来源：[API Packages for Plugins](https://docs.mcdreforged.com/en/latest/plugin_dev/api.html)

> `mcdreforged.api.all` 包含：
> - `mcdreforged.api.command`（命令节点、`Requirements`、`SimpleCommandBuilder`）
> - `mcdreforged.api.rtext`（`RText`/`RTextList`/`RColor`/`RStyle`/`RAction`）
> - `mcdreforged.api.rcon`（`RconConnection`）
> - `mcdreforged.api.types`（`ServerInterface`、`PluginServerInterface`、`CommandSource` 等）
> - `mcdreforged.api.utils`（`Serializable` 等）

### 精确导入（避免命名冲突）

```python
from mcdreforged.api.command import (
    Literal, Text, QuotableText, GreedyText,
    Integer, Float, Number, Boolean, Enumeration,
    Requirements, SimpleCommandBuilder,
    CommandContext, CommandError,
)
from mcdreforged.api.rtext import (
    RText, RTextList, RTextBase,
    RColor, RColorRGB, RStyle, RAction,
    RClickEvent, RHoverEvent,
)
from mcdreforged.api.types import (
    ServerInterface, PluginServerInterface,
    CommandSource, PlayerCommandSource, ConsoleCommandSource, Info,
)
from mcdreforged.api.utils import Serializable
from mcdreforged.api.rcon import RconConnection
```

---

## 附录：关键版本对应

| API | 引入版本 | 备注 |
|---|---|---|
| `Boolean` / `Enumeration` 节点 | v2.3.0 | |
| `tr()` / `rtr()` / `RTextMCDRTranslation` | v2.1.0 | 多语言支持 |
| `SimpleCommandBuilder` | v2.6.0 | 无树命令构建器 |
| `CountingLiteral` | v2.12.0 | 计数字面量 |
| `requires` 多次叠加（与逻辑） | v2.7.0 | |
| `precondition` 方法 | v2.14.0 | 隐藏节点 |
| `pydantic.BaseModel` 配置支持 | v2.14.0 | |
| `RClickAction` / `RHoverAction` 独立类 | v2.15.0 | 旧名 `RAction` 保留为别名 |
| `ArgumentNode` `accumulate` 参数 | v2.13.0 | 同节点多次访问收集为 list |
| `ArgumentNode` `metavar` 参数 | v2.14.0 | 自定义补全占位名 |

> 完整变更史：[Migrate Guide](https://docs.mcdreforged.com/en/latest/plugin_dev/migration.html)

---

## 附录：官方文档导航

| 主题 | URL |
|---|---|
| 文档总入口（中文） | <https://docs.mcdreforged.com/zh-cn/latest/> |
| 文档总入口（英文） | <https://docs.mcdreforged.com/en/latest/> |
| 快速开始 | <https://docs.mcdreforged.com/zh-cn/latest/quick_start.html> |
| 插件开发基础 | <https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/basic.html> |
| 命令树（开发指南） | <https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/command.html> |
| 命令 API（代码参考） | <https://docs.mcdreforged.com/en/latest/code_references/command.html> |
| ServerInterface | <https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html> |
| PluginServerInterface | <https://docs.mcdreforged.com/en/latest/code_references/PluginServerInterface.html> |
| Minecraft Tools（RText/Rcon） | <https://docs.mcdreforged.com/en/latest/code_references/minecraft_tools.html> |
| 权限系统 | <https://docs.mcdreforged.com/zh-cn/latest/permission.html> |
| `!!help` 命令 | <https://docs.mcdreforged.com/zh-cn/latest/command/help.html> |
| 开发技巧 | <https://docs.mcdreforged.com/en/latest/plugin_dev/dev_tips.html> |
| API 包结构 | <https://docs.mcdreforged.com/en/latest/plugin_dev/api.html> |

---

*最后核实日期：2026-07-02 · MCDR 2.15.7*
