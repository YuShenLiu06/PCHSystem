# 服务文档：markdown-service（结构化 markdown 渲染抽象）

> **统一总览**：[`../../architecture.md`](../../architecture.md) §5
> **代码位置**：[`Backend/app/services/markdown_render/`](../../../Backend/app/services/markdown_render/)（4 模块 + `__init__` 公共导出）
> **首个消费者**：sheet 归档服务（[`Backend/app/services/archive/`](../../../Backend/app/services/archive/)）；详见 [`api/sheets.md`](../api/sheets.md) §4.1 / §5.2
> **未来消费者**：榜单 / 报表 / wiki 同步等需要「拼装结构化 markdown 文档」的场景

## 1. 概述

markdown_render 是后端**通用的结构化 markdown 文档渲染抽象**。职责单一：

> **把一份 markdown 文档拆成若干有序「分节」（section），每节由独立 renderer 按 order 渲染，编排层过滤空白后用 `\n\n` 拼接。**

它是「项目归档生成 markdown 落盘」（迁移 0009）催生的第一套文档渲染基础设施。在此之前仓库没有任何 markdown / 模板渲染代码。本服务把渲染能力抽成**可复用的扩展点**——「支持新增一种内容」= 注册一个新的 `SectionRenderer`，未来榜单 / 报表 / wiki 同步等消费者注册不同分节集合即可复用同一抽象。

| 管 | 不管 |
|---|---|
| `SectionRenderer` Protocol（扩展点契约）+ `MarkdownDocument` 编排（有序聚合 + 同名 override） | 数据获取 / 业务判定（context 由调用方预算后注入） |
| `TemplateSection`（静态样板）/ `FunctionSection`（动态函数）两种内置实现 | 复杂模板语法（不引 Jinja2；循环/条件用纯 Python 函数） |
| `load_template_sections_from_dir`（可选：从 JSON 目录加载**静态** TemplateSection 覆盖文案） | 动态 section 的目录化加载（YAGNI，动态逻辑必须进代码） |

**零依赖**：纯 Python 标准库（`dataclasses` / `logging` / `json` / `pathlib`），不引入 Jinja2 或任何模板引擎。

---

## 2. 架构风格：Route C（SectionRenderer Protocol）

### 2.1 设计取舍

原架构讨论曾评估「完整镜像用户自有 PromptStore 的不可变 register + 有序聚合架构」，经审核定为**过度设计**——PromptStore 为 LLM prompt 拼装设计的若干机制在「结构化 markdown 渲染」场景没有对应用例：

| PromptStore 机制 | 是否保留 | 理由 |
|---|---|---|
| 不可变 frozen + `register` 返回新对象 + 有序聚合 + 同名 override | ✅ **保留** | 真正有价值的架构风格；对齐项目 `Notifier` Protocol 范式（RS-9） |
| `template` 调度维度（prompt 模板 A/B 变体） | ❌ 抛弃 | 归档只有一种渲染，无模板变体 |
| `dispatch` 二级仲裁 / WILD_CARD 全局注入 | ❌ 抛弃 | section 内不该制造冲突；无「每 section 都插同一段」用例 |
| body fallback（缺 section 静默回退到 body） | ❌ 抛弃 | section 缺失应渲染空 + warning，不应静默回退 |
| `{placeholder}` 自研占位符引擎 | ❌ 抛弃 | 材料表是 N 行循环 + 条件分支，占位符引擎会把循环逻辑推给调用方、fragment 退化成单占位符；直接用 Python 函数 10 行搞定 |

> **结论**：Route C = 保留「不可变 register + 有序聚合 + Protocol 扩展点」，抛弃不适配机制。静态与动态统一在 `SectionRenderer.render(context) -> str` 接口下（静态 section = 永远返回同字符串的函数；动态 section 是其退化特例的对偶——动态是主，静态是退化特例）。

### 2.2 模块结构

| 模块 | 职责 |
|---|---|
| `protocols.py` | `SectionRenderer` Protocol（`@runtime_checkable`）+ `MarkdownContext = Mapping[str, Any]` |
| `sections.py` | `TemplateSection` / `FunctionSection`（`@dataclass(frozen=True)`，不可变） |
| `document.py` | `MarkdownDocument`（frozen；内部 `_sections: tuple`；`register` / `register_many` / `render`） |
| `loaders.py` | 可选：`load_template_section(path)` / `load_template_sections_from_dir(path)`（仅加载**静态** TemplateSection） |

---

## 3. 核心契约

### 3.1 `SectionRenderer` Protocol（扩展点）

```python
MarkdownContext = Mapping[str, Any]  # 扁平 dict，调用方预算后注入

@runtime_checkable
class SectionRenderer(Protocol):
    name: str            # 分节唯一标识（同名 override 的键，不应重复）
    order: int           # 文档内位置（升序渲染；同 order 按注册顺序兜底，sorted 稳定）
    def render(self, context: MarkdownContext) -> str: ...  # 返回该节 markdown；空/纯空白会被文档层过滤
```

- `@runtime_checkable` 允许 `isinstance` 校验注册对象，便于在 `register` 入口防御性校验。
- 与 `Notifier` Protocol 同范式（RS-9）：Protocol 是扩展点，不强制继承。

### 3.2 内置实现

| 类 | 用途 | `render` 行为 |
|---|---|---|
| `TemplateSection(name, order, template)` | 静态样板（header / status_line / meta / footer） | `str.format_map` 占位符替换；**缺 key → 空串 + `logger.warning`，不抛异常**（缺字段渲染空比中断整篇归档更稳健）；值为 `None` 同样渲染空串 |
| `FunctionSection(name, order, func)` | 动态内容（contributor_stats / contribution_chart / timeline） | `return self.func(context)`；循环 / 条件 / 空表 / None 分支由调用方在纯 Python 函数内处理 |

### 3.3 `MarkdownDocument`（编排，frozen）

```python
@dataclass(frozen=True)
class MarkdownDocument:
    # _sections: tuple[SectionRenderer, ...]（tuple 保证 frozen 可哈希且不可变）

    def register(self, section: SectionRenderer) -> MarkdownDocument:
        """返回【新】document（不可变）；同名 override（移除旧同名，保留新 order）。"""

    def register_many(self, sections) -> MarkdownDocument: ...

    def render(self, context: MarkdownContext | None = None) -> str:
        """按 order 升序渲染各分节，过滤空/纯空白结果，用 '\\n\\n' 连接。"""
```

- **编排层不区分静态/动态**——只调 `render`，统一接口。
- **同名 override**：`register` 同 `name` 的新 section 会移除旧同名（保留新 order），便于运行时替换 / 用 `MARKDOWN_FRAGMENTS_DIR` 加载的静态 section 覆盖内置默认。
- **过滤空白**：避免多余 `\n\n`（空 section 不占位）。

### 3.4 可选 `MARKDOWN_FRAGMENTS_DIR`

`load_template_sections_from_dir(path)` 仅加载**静态 TemplateSection**（JSON 单对象 / 数组 / 递归子目录），用于「产品 / 运营改 header / footer 文案不动代码」。**动态 FunctionSection 不目录化**（逻辑必须进代码，YAGNI）。

非法 JSON / 缺字段 → 抛带来源诊断的 `ValueError`，不静默吞错（对齐项目错误处理规范）。

---

## 4. 配置

| 配置键（`app/core/config.py`） | 默认 | 说明 |
|---|---|---|
| `markdown_fragments_dir` | `""`（空） | 静态 TemplateSection 覆盖目录；空 = 不加载，全用内置默认文案 |

> 归档端点另有 `archive_root`（归档落盘根目录），见 [`api/sheets.md`](../api/sheets.md) §5.2 + 503 错误码；该配置属于归档服务而非本模块。

---

## 5. 内置分节（首版，sheet 归档服务注册）

| name | order | 类型 | 内容 |
|---|---|---|---|
| `header` | 100 | TemplateSection | `# 📦 项目归档：{title}` |
| `status_line` | 200 | TemplateSection | 状态标签（如「已归档」） |
| `meta` | 300 | TemplateSection | 拥有者 / 创建时间 / 归档时间 |
| `contributor_stats` | 500 | FunctionSection | `## 🏆 贡献者统计`：精确排行（`aggregate_contributor_totals` 合并 lock `delivered_qty` + progress `contributed_qty` 按人聚合，`HAVING>0` 剔除零和） |
| `contribution_chart` | 550 | FunctionSection | `## 📊 贡献占比`：引用与 `index.md` 同目录的 `contributions.png`（matplotlib 饼图，无贡献者 → 空串过滤、不生图） |
| `timeline` | 600 | FunctionSection | `## 📅 时间线`（创建 / [进入施工] / 归档） |
| `footer` | 900 | TemplateSection | 页脚文案 |

**「支持多种内容新增」= 注册一个新 section**——这是真实扩展点。未来榜单 / 报表消费者注册不同 section 集合即可复用同一抽象。

---

## 6. 与其他服务的关系

- **sheet 归档服务**（首个消费者）：`Backend/app/services/archive/` 调 `build_sheet_archive_document()` 链式 `register` 上述内置 section → `archive_sheet()` 编排渲染 → `write_atomic()` 写盘 → DB 置 archived + `sheet_archived` 通知 → commit。事务一致性与孤儿文件 cleanup 详见 [`api/sheets.md`](../api/sheets.md) §4.1 / §5.2。
- **wiki-service（git 双向）**：归档产物 = 每项目独立文件夹 `ARCHIVE_ROOT/projects/{id}/`（`index.md` + `contributions.png`）；`archived_path` 存相对 POSIX 路径 `projects/{id}/index.md`。归档成功后 publisher 把整目录 `git commit + push` 到独立 wiki 内容 git 仓（R-8 重写为 git 双向，默认 off、best-effort），wiki.js（独立部署）与该远端双向同步渲染。本模块只负责生成 md 内容，不参与推送。

---

## 7. 红线衔接

- **RS-9（Protocol 扩展点范式）**：`SectionRenderer` 与 `Notifier` 同范式——Protocol 定义契约，不强制继承，`@runtime_checkable` 允许 `isinstance` 校验。
- **R-10（模块化单体）**：本模块是后端单体内部共享基础设施，不拆独立子服务；归档服务与未来消费者直接 `import` 复用。
- **不可变（项目编码规范）**：所有 section / document 皆为 `@dataclass(frozen=True)`；`register` 返回新对象，不改原对象。

---

*最后更新：2026-07-03（markdown_render Route C 抽象首版——SectionRenderer Protocol + TemplateSection/FunctionSection + MarkdownDocument；首个消费者 sheet 归档服务落地）*
