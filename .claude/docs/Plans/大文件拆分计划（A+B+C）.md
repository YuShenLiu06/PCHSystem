# 大文件拆分计划（A + B + C）

> 状态：**已选定方案**（A 拆 `sheet_commands.py` + B 拆 `sheet_repo.py` + C 抽 `common.sh` 的 Docker 安装段）
> 动因：`coding-style.md` 文件 <800 行硬上限，全项目 4 文件超标（实测见 §1）
> 目标：3 个超标文件归零、1 个临界文件暂缓；**纯移动/重排，零行为变更**

---

## 1. 背景：实测超标清单（cloc 2026-07-25）

| 文件 | 行数 | 超标 | 引用面 |
|---|---:|---|---|
| `McdrPlugin/pch_system/sheet_commands.py` | **1,553** | +94% | 仅 `__init__.py:9` 一处 `from .sheet_commands import (...)` |
| `Backend/app/repositories/sheet_repo.py` | **1,122** | +40% | 6 文件 × 18 处（`api/sheets/*` + `services/archive` + `services/sheet_row_order`）|
| `Scripts/lib/common.sh` | **820** | +2% | 被 `install.sh` / `update.sh` source |
| `Frontend/src/composables/useSheetDetail.ts` | 734 | 临界（未超）| `SheetEditor.vue` 单点 |

**已具备的有利结构**：
- `sheet_commands.py` 内部已用 `# === 区段名 ===` 按特性切好（表级 / 协管员 / 阶段流转 / 行级 / 一键提交），天然可按区段抽模块。
- `sheet_repo.py` 是**模块级 async 函数**（非类），按聚合（表 / 行 CRUD / 行状态机 / 贡献者 / CSV）天然可分。
- `api/sheets/` 已是包化先例（`__init__/_shared/sheets_crud/rows/collab/lifecycle/managers`），B 沿用同一范式。

---

## 2. 方案 A：`sheet_commands.py` → `sheet_commands/` 包（M，主拆）

### A.1 目标结构

```
McdrPlugin/pch_system/sheet_commands/
├── __init__.py        # re-export 全部 handler + configure（保 import 面零改）
├── _shared.py         # configure / _resolve / _require_player / _find_row_or_tell
├── overview.py        # _sheet_root（!!PCH sheet 总览）
├── table.py           # list / view / quick / create / rename / delete + 旗标解析
├── managers.py        # _format_manager_list + manager list/add/remove
├── lifecycle.py       # advance_default / _to_constructing / _to_archived / _impl
├── rows.py            # upsert / set / delrow / addsub / delsub / setsub（行 CRUD + 子物品）
├── row_state.py       # claim / deliver / progress / done / release / reject / notify_list
└── submit.py          # submit_impl / _submit_safe / submit_oneclick / _submit_quick / addhand / setreg
```

### A.2 区段 → 模块映射（按现有 `# ===` 区段，行号为现状参考）

| 区段 | 现行号 | 目标模块 |
|---|---|---|
| 工具（configure/_resolve/_require_player/_find_row_or_tell）| 95–171 | `_shared.py` |
| sheet 总览（_sheet_root）| 173–279 | `overview.py` |
| 表级（list/view/quick/create/rename/delete + 旗标）| 281–646 | `table.py` |
| 协管员 | 648–757 | `managers.py` |
| 阶段流转 advance | 759–827 | `lifecycle.py` |
| 行级 CRUD（upsert/set/delrow/addsub/delsub/setsub）| 829–1071 | `rows.py` |
| 行级状态机（claim/deliver/progress/done/release/reject/notify_list）| 1073–1305 | `row_state.py` |
| 一键提交 / 手持建行 / setreg | 1307–1553 | `submit.py` |

### A.3 import 面处理（关键：零改）

`__init__.py:9` 现为：

```python
from .sheet_commands import (  # 一长串名字
```

新 `sheet_commands/__init__.py` 显式 re-export 该「一长串名字」**原样不变**：

```python
from ._shared import configure, _resolve, _require_player, _find_row_or_tell
from .overview import _sheet_root
from .table import (_sheet_list, _sheet_list_default, _parse_list_flag_tokens,
                    _sheet_list_flags, _sheet_list_impl, _render_sheet_detail,
                    _sheet_view, _sheet_view_args, _sheet_quick,
                    _sheet_create, _sheet_rename, _sheet_delete)
# ... 其余区段同理
```

→ `__init__.py` 顶层那一行 `from .sheet_commands import (...)` **一字不改**。

### A.4 红线核对

- **S-1（MCDR 联网验证）**：MCDR 要求 `id` = 顶层插件文件夹名 = entrypoint 包名。本次拆的是 **`pch_system` 包内部的子模块**，entrypoint 仍为 `pch_system`，**不触红线**。但合入前必须在 mc-test 实跑 `!!MCDR plugin reload pch_system` 验证 reload 链（`multi_file_plugin.py` 依赖解析）无断裂。
- **R-7（MCDR 纯客户端）**：纯移动，行为不变。

### A.5 风险

- 跨模块 helper（`_resolve` / `_require_player` / `_find_row_or_tell`）被多区段调用 → 全放 `_shared.py`，各模块 `from ._shared import ...`。
- `view_args.py` 注释提到「绕过 `__init__.py` 的 mcdreforged import」——拆包后各子模块若需 mcdreforged API，沿用同一规避（直接 import，不经包 `__init__`）。
- 区段内函数**非完全连续**（个别 helper 散落），需按函数整体搬迁，不可按行号机械切片。

---

## 3. 方案 B：`sheet_repo.py` → `sheets/` 包（M）

### B.1 目标结构（镜像 `api/sheets/` 范式）

```
Backend/app/repositories/sheets/
├── __init__.py        # re-export 全部公开函数 + 异常 + 常量
├── _shared.py         # SheetRowConflict / SheetArchived / _assert_writable / MODE_* / STATUS_*
├── sheet_queries.py   # 表级：advance / create / get / list / collect_participant_uuids / delete / list_all_with_rows
├── row_repo.py        # 行 CRUD：list / get / upsert / _validate_parent_for_sub / create / _recompute_after_edit / update / delete
├── row_state.py       # 行状态机：_lock / claim / set_delivery / set_progress / release / reject / contribute
├── contributors.py    # list / clear / aggregate_contributor_totals
└── csv_export.py      # _row_to_csv_record / export_csv / export_all_csv
```

### B.2 函数 → 模块映射（现行号）

| 函数 | 行号 | 目标模块 |
|---|---|---|
| `SheetRowConflict` / `SheetArchived` / `_assert_writable` | 54–80 | `_shared.py` |
| `advance_sheet` / `create_sheet` / `get_sheet` / `list_sheets` / `collect_participant_uuids` / `delete_sheet` / `list_all_sheets_with_rows` | 82,138,147,165,242,1068,1076 | `sheet_queries.py` |
| `list_rows` / `get_row` / `upsert_row` / `_validate_parent_for_sub` / `create_row` / `_recompute_after_edit` / `update_row` / `delete_row` | 285,338,361,417,443,501,536,1059 | `row_repo.py` |
| `_lock_row` / `claim_row` / `set_row_delivery` / `set_row_progress` / `release_row` / `reject_row` / `contribute_row` | 680,691,743,761,787,839,857 | `row_state.py` |
| `list_contributors` / `clear_contributors` / `aggregate_contributor_totals` | 898,970,977 | `contributors.py` |
| `_row_to_csv_record` / `export_csv` / `export_all_csv` | 1088,1104,1114 | `csv_export.py` |

### B.3 import 面处理（6 文件，机械替换）

两种引用形态，全在 `Backend/app` 内：

```python
# 形态 1（module 用法，6 文件）
from app.repositories import sheet_repo          →  from app.repositories.sheets import sheet_repo
sheet_repo.list_sheets(...)                       #  调用点不变

# 形态 2（named 用法，6 文件）
from app.repositories.sheet_repo import SheetArchived, SheetRowConflict
                                                  →  from app.repositories.sheets import SheetArchived, SheetRowConflict
from app.repositories.sheet_repo import MODE_LOCK, STATUS_DONE
                                                  →  from app.repositories.sheets import MODE_LOCK, STATUS_DONE
```

涉及文件：`api/sheets/{collab,lifecycle,sheets_crud,_shared,rows}.py`、`services/sheet_row_order.py`、`services/archive/service.py`。

> **测试侧**：`Backend/tests/**` 对 repo 的引用同步替换；现有权限矩阵 M01–M26 + identity 26 条 + scanner 等测试即回归基线。

### B.4 风险

- 函数**严重非连续**（如 `delete_row`@1059 夹在 `aggregate_contributor_totals` 与 `delete_sheet` 之间）——必须按函数整体搬，不可按行切片。
- 跨模块内部依赖（如 `row_state.contribute_row` 调 `_assert_writable`）→ `from ._shared import _assert_writable`；`row_repo._recompute_after_edit` 可能调 contributors 重算 → `from .contributors import ...`。搬迁时按依赖拓扑排序，避免循环 import（`_shared` ← `sheet_queries`/`row_repo` ← `row_state` ← `contributors`，单向无环）。

---

## 4. 方案 C：`common.sh` 抽 `docker_install.sh`（XS）

### C.1 动作

仅超 20 行，最小拆分：把 Docker 安装段整体抽出。

```
Scripts/lib/
├── common.sh            # 820 → ~655（删掉下述 6 函数 + source 一行）
└── docker_install.sh    # 新增 ~110：ensure_docker / install_docker /
                         #   _ensure_curl / _install_docker_native /
                         #   _install_compose_plugin / _post_install_docker
```

`common.sh` 顶部补一行（在其余 source 旁）：

```bash
# shellcheck source=lib/docker_install.sh
source "${BASH_SOURCE[0]%/*}/docker_install.sh"
```

### C.2 边界

- 抽出的 6 函数（现 `common.sh:162-260`）**互相内聚**、仅被 `install.sh::start_stack` 链路调用，无外部依赖回灌。
- 其余函数（镜像探测 `probe_url`/`pick_github_mirror`/`ensure_docker_registry_mirrors`、部署状态读写、git/dirty 检查）**全部留在 `common.sh`**——它们被 `update.sh` 高频使用，且彼此耦合更深。
- `install.sh` / `update.sh` **不改**：它们只 source `common.sh`，间接获得 `docker_install.sh`（透传）。

### C.3 风险

- 极低。唯一注意点：`docker_install.sh` 必须在 `common.sh` **使用其函数之前**被 source（放文件顶部 utility 区之后即可）。

---

## 5. 实施顺序（先易后难、先绿后合）

1. **C 先行**（XS，~15 min，shellcheck + 干跑 install/update 验证）——立即消除一个超标项，热身。
2. **B 次之**（M，~2 h，纯后端、现有 pytest 全量回归兜底）——拆完跑 `pytest Backend/tests` 全绿即合。
3. **A 最后**（M，~2 h，MCDR 需 mc-test 实 reload 验证）——拆完跑 MCDR 单测 + mc-test `!!MCDR plugin reload pch_system` 实操全部命令。

> A、B 互不依赖，可并行；但建议串行以便各自回归隔离归因。

## 6. 验证步骤

| 项 | 命令 / 动作 | 预期 |
|---|---|---|
| 后端单测 | `cd Backend && python -m pytest tests/` | 现有全量（权限 M01–M26 + identity + scanner 等）全绿 |
| 后端迁移 | `alembic upgrade head`（无 schema 变更，应为 no-op） | 无新 revision，无报错 |
| MCDR 单测 | `cd McdrPlugin && python -m pytest` | 336 passed 保持 |
| MCDR reload | mc-test 容器内 `!!MCDR plugin reload pch_system` 后实操 `!!PCH sheet list/view/manager/advance/claim/deliver/submit/addhand/setreg` | 全部命令正常回执，无 import / 依赖解析错误 |
| 前端（不动，仅确认未波及）| `cd Frontend && npm run test && vue-tsc` | 103 passed 保持 |
| Scripts | `shellcheck Scripts/lib/*.sh Scripts/*.sh`；`install.sh --dry-run` 若有 / `update.sh` 在干净树干跑 | 无 warning，部署流程不破坏 |
| 行数复核 | `cloc McdrPlugin/pch_system Backend/app/repositories Scripts/lib --exclude-dir=__pycache__` | 三个原超标文件均 <800；新分包单文件均 <350 |

## 7. 复杂度与工时

| 方案 | 尺码 | 工时 | 收益 |
|---|---|---|---|
| C 抽 docker_install.sh | XS | ~15 min | `common.sh` 820→~655 达标 |
| B 拆 sheet_repo 包 | M | ~2 h（含 6 文件 import 替换 + pytest 回归）| `sheet_repo.py` 1,122→0，6 子模块各 <350 |
| A 拆 sheet_commands 包 | M | ~2 h（含 mc-test reload 验证）| `sheet_commands.py` 1,553→0，8 子模块各 <300 |
| **A+B+C 合计** | **M+** | **~0.5 天** | **3/4 超标项归零，零行为变更** |

## 8. 不做 / 暂缓的事

- **D `useSheetDetail.ts`（734，临界）暂缓**：未超 800 上限；整段位于单一 `useSheetDetail()` 闭包内，拆分需重构 return shape，风险 > 收益（YAGNI）。仅在下次新增逻辑致其突破 800 时立项；届时可抽纯函数（`rowEqual` / draft init/reset）到 `utils/sheetDetail.ts`。
- **不修「Literal 字面量未入 ctx」已知 bug**（见根 `CLAUDE.md` §7 待处理）——属行为修复，与本次纯结构重构无关，单独立项。
- **不改任何公开 API / 函数签名 / 异常类型**——`__init__.py` re-export 保持原命名空间。
- **不顺手重构 `sheet_repo` 内部逻辑**（如 `list_sheets` 的参与优先排序、`_recompute_after_edit` 的级联重算）——纯搬移，逻辑改动留作独立 PR 以隔离归因。
- **不为拆分新增测试**——现有测试已覆盖行为，搬移后全绿即证明等价（TDD 红→绿针对的是新行为，非结构重排）。
- **不动 `api/sheets/` 已有包结构**——它是 B 的范式参考，不是本次目标。
