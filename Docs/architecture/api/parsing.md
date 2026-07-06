# parsing API 参考

> 投影文件解析子服务的 HTTP API 权威参考（`Backend/app/api/parsing.py`）。
> 相关：在线表格落点见 [`sheets.md`](./sheets.md)（`POST /sheets/from-items`）。

---

## 1. 概述

上传 Minecraft 投影文件（`.litematic`）→ 后端解析材料清单 → 翻译成中文 → 返回分组预览（**不落库、不持久化文件**）。前端预览确认后，按材料清单调 [`POST /sheets/from-items`](./sheets.md#51-表级) 生成在线表格（`mode` 默认 `lock`），随后即可走现成 sheets 协作流（认领·交付·解除·打回）。

**仅 Web 端**（MCDR 不参与上传）。解析无状态、零迁移、复用 `sheets` schema。

---

## 2. 端点

| 方法 | 路径 | 鉴权 | body | 成功 | 说明 |
|---|---|---|---|---|---|
| POST | `/parsing/litematic` | `get_current_player`（JWT·Web） | `multipart/form-data`：`file`（`.litematic`） | 200 `ParsedMaterialPreview` | 解析 + 翻译，返回方块组 + 容器组预览；不落库 |
| POST | `/sheets/from-items` | `get_current_player` | `SheetFromItemsRequest{title, items[]}` | 201 `SheetDetail` | 一次性建表 + 批量行（mode 默认 lock）。**现透传 `registry_id`**（= `PreviewItem.item_id`），写入 `sheet_rows.registry_id`（迁移 0010）；`item_name` 缺失时后端翻译补中文名。见 [`sheets.md`](./sheets.md) |

- 上限：文件 ≤ `LITEMATIC_MAX_UPLOAD_BYTES`（默认 50MB，经 `.env` 可调）；`items` ≤ 2000（schema 限）。
- 解析为 CPU 密集，跑在 `asyncio.to_thread`（RS-7，不阻塞事件循环）。
- 错误码：401（未鉴权）、400（扩展名非 `.litematic` / 空文件）、413（超限）、422（litemapy 解析失败，如损坏或非 litematic NBT）。

---

## 3. 响应模型 `ParsedMaterialPreview`

```python
class PreviewMeta(BaseModel):
    filename: str
    schematic_name: str = ""
    author: str = ""
    region_count: int          # ≥0
    total_blocks: int          # ≥0（已放置、非空方块总数）
    total_volume: int          # ≥0（区域体积，按 |w|×|h|×|l| 求和）

class PreviewItem(BaseModel):
    item_id: str               # registry id（namespace:path），如 "create:item_vault"
    item_name: str             # 中文显示名；未命中翻译时回退为 item_id
    count: int                 # ≥0

class ParsedMaterialPreview(BaseModel):
    meta: PreviewMeta
    blocks: list[PreviewItem]          # 已放置方块（按数量降序）
    container_items: list[PreviewItem] # 容器内物品（vanilla Items 键）
    untranslated: list[str]            # 未找到中文翻译的 registry id（item_name 已回退为原 id）
```

---

## 4. 架构（ABC，可扩展）

```
上传 bytes
   │  MaterialParser(ABC)              app/services/parsing/parsers/
   ▼   └─ LitematicParser（litemapy）      base.py / litematic.py
ParsedMaterialList(blocks, container_items, meta)   # 纯 registry id + count
   │  ItemTranslator(ABC)             app/services/parsing/translators/
   ▼   └─ LangJsonTranslator（内置 lang JSON）   base.py / lang_json.py
PreviewItem(item_id, item_name, count) + untranslated[]
   │  前端预览 → 确认（生成表时透传 registry_id = PreviewItem.item_id）
   ▼
POST /sheets/from-items   →   sheets 协作流（写入 sheet_rows.registry_id，迁移 0010）
```

- `MaterialParser`（`parsers/base.py`）：文件字节 → 分组清单（**不翻译**）。换格式（`.schem` / `.nbt`）新增子类。
- `ItemTranslator`（`translators/base.py`）：registry id → 中文。换数据源（远端 lang / 手维护映射 / Crowdin）新增子类。
- `preview.build_preview`（`preview.py`）：编排 parser + translator → 预览条目 + 未翻译列表。
- 归一化：`normalize.py` 维护 `SKIP_BLOCKS`（air/水/岩浆/结构空）。R-6（剥离 BlockState properties）由 litemapy 的 `BlockState.id` 天然满足。

### 4.1 解析规则

- 方块：`region[x, y, z].id` 逐体素计数（公开 API，O(volume)）。
- 容器：仅读 tile entity 的 vanilla `Items` 键（`{id, Count, Slot}`，兼容经典 `Count` 与小写 `count`）。

---

## 5. 翻译数据来源（S-1 已联网核实）

包内 `translators/lang/*.zh_cn.json`，`LangJsonTranslator.load_bundled_table()` 合并查表（候选 key：`block.<ns>.<path>` → `item.<ns>.<path>`）。

| 文件 | 来源 | 版本 | 校验 |
|---|---|---|---|
| `minecraft.zh_cn.json` | Mojang 资产索引 `5`（`resources.download.minecraft.net`，经 version manifest + asset index 取 `minecraft/lang/zh_cn.json`） | MC 1.20.1 | SHA1 核验，8200 keys |
| `create.zh_cn.json` | Create 模组 jar（Modrinth `LNytGWDc`）`assets/create/lang/zh_cn.json` | Create 6.0.8（1.20.1，2025-11-02） | jar 解包，3635 keys |

> **版本选择**：样例 `create:item_vault` / `create:packager` / `create:stock_link` 是 Create 0.6+「Post Production」物流方块，0.5.1.x lang 中不存在，故取 Create 6.0.8（1.20.1 最新）以全覆盖。
> 升级 MC/Create 版本时替换对应 JSON 即可（loader 无关，jar 内 lang 通用）。

---

## 6. 限制 / 已知近似

| 项 | 说明 | 扩展点 |
|---|---|---|
| **Create 自有存储** | `create:item_vault` 等 Create 仓储方块的内容**不走 vanilla `Items`**（走 Create 全局存储），其内容暂不可提取。普通箱子/木桶/漏斗的 `Items` 正常读取 | 在 `LitematicParser` 内按 tile entity `id` 分派专用读取器（新增子模块） |
| **多部分方块计数** | 门/床/双层台阶两面共享 `.id`，被计为多块（如一扇门计 2） | `normalize.py` 的 `MERGE_RULES`（预留，形如 `{"minecraft:.*_door": 2}` ÷2 取整） |
| **MC 1.20.5+ 物品格式** | 组件化物品格式与经典 `{id, Count}` 不同 | 容器读取层按版本分派（当前覆盖经典格式） |
| **大文件性能** | 逐体素扫描百万级方块较慢 | 已跑在线程池；必要时切 palette + `numpy.bincount`（私有 `_blocks`，版本脆弱） |
| **翻译未命中** | 三方/冷门方块可能无中文 | 预览返回 `untranslated[]`，`item_name` 回退 registry id；可补 mod lang JSON |

---

## 7. 文件清单

```
Backend/app/services/parsing/
├── __init__.py
├── models.py                 # frozen dataclass：MaterialEntry / ParseMeta / ParsedMaterialList
├── normalize.py              # SKIP_BLOCKS / MERGE_RULES
├── preview.py                # build_preview 编排 + get_default_translator
├── parsers/
│   ├── base.py               # MaterialParser ABC
│   └── litematic.py          # LitematicParser + LitematicParseError
└── translators/
    ├── base.py               # ItemTranslator ABC
    ├── lang_json.py          # LangJsonTranslator + load_bundled_table + lang_key_candidates
    └── lang/
        ├── minecraft.zh_cn.json   # Mojang 1.20.1
        └── create.zh_cn.json      # Create 6.0.8（1.20.1）
```

- 路由：`app/api/parsing.py`；schema：`app/schemas/parsing.py`；配置：`app/core/config.py`（`litematic_max_upload_bytes`）。
- 测试：`tests/test_parsing_unit.py`（parser + translator）、`tests/test_parsing_api.py`（端点 + 鉴权 + 错误码）、`tests/test_sheets_from_items.py`（批量建表）。

---

*最后更新：2026-07-03（`POST /sheets/from-items` 现透传 `registry_id` = `PreviewItem.item_id`，写入 `sheet_rows.registry_id`，迁移 0010；`PreviewItem` 本身不变）*
