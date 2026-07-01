# {服务中文名} · 子服务 CLAUDE.md

> 本文件由 `service-claude-md` skill 生成 / 维护，**禁止手写**。
> 全局统一规范见根 [`CLAUDE.md`]({ROOT}/CLAUDE.md)；本服务完整架构见架构文档。

<!--
  占位符约定（生成时由 service-claude-md skill 替换）：
    {ROOT}        = 回到项目根的相对路径（顶层服务目录用 .. ；Backend/xxx_service/ 用 ../.. ）
    {服务名}      = 形如 user-service / mcdr-plugin / frontend
    {架构文档路径} = Docs/architecture/services/{服务名}.md  或  Docs/architecture/frontend.md
    {schema}      = 该服务所属的数据 schema（如 scoring / projects / titles）
-->

---

## 1. 服务定位

{一句话说明该服务的职责与在三端架构中的位置。
 例：scoring-service 是后端积分引擎，负责材料提交入库、放置贡献记录、黄皮子积分计算与榜单更新。}

> 完整架构：[`{架构文档路径}`]({ROOT}/Docs/architecture/services/{服务名}.md)

---

## 2. 职责边界

| 管 | 不管 |
|---|---|
| {该服务负责的事 1} | {交给别的服务 / 端的事 1} |
| {该服务负责的事 2} | {交给别的服务 / 端的事 2} |

---

## 3. 雷点·红线（服务特有）

> 全局红线见根 CLAUDE.md §3（R-1~R-12）。此处只列**本服务特有**或对本服务**特别需要强调**的约束。

| # | 红线 | 说明 |
|---|---|---|
| **RS-1** | {服务特有约束} | {为什么 / 违反后果} |
| **RS-2** | {服务特有约束} | {为什么 / 违反后果} |

<!-- 对全局红线在本服务的具体化，用「遵守 R-x：…」写法，例如：
| **RS-3** | 遵守 R-3 清箱时序 | 本服务（MCDR）必须在 POST /submissions 返回成功后才执行 `data merge block {Items:[]}`；失败绝不清箱。 |
-->

---

## 4. 关键要素

### 核心实体 / 数据表
- `{表名}`（{schema} schema）：{一句说明}
- `{表名}`（{schema} schema）：{一句说明}

> 完整 DDL 与约束见 [`Docs/architecture/data-model.md`]({ROOT}/Docs/architecture/data-model.md)。

### 对外接口
| 接口 / 命令 | 说明 |
|---|---|
| {接口或游戏内命令} | {用途} |

### 依赖的其他服务
- **{服务名}**：{依赖关系 / 调用方向}

---

## 5. 文档索引

| 文档 | 路径 | 说明 |
|---|---|---|
| 本服务架构 | `{ROOT}/Docs/architecture/services/{服务名}.md` | 完整职责 / 接口 / 流程 |
| 数据模型 | `{ROOT}/Docs/architecture/data-model.md` | 相关表与约束 |
| 工程总览 | `{ROOT}/Docs/architecture.md` | 三端架构与跨服务流程 |
| 根规范 | `{ROOT}/CLAUDE.md` | 统一命名 / 红线 / 索引 |

---

## 6. 与根规范的关系

- 遵守根 [`CLAUDE.md`]({ROOT}/CLAUDE.md) 的命名分层（§1）与全局红线（§3 R-1~R-12）。
- 本文件的 RS-x 红线是**服务特有**补充，不覆写全局红线。
- 命名 / 全局红线 / 技术栈若有冲突，以根 CLAUDE.md 为准并修正本文件。

---

*最后更新：{YYYY-MM-DD}（由 service-claude-md skill 维护）*
