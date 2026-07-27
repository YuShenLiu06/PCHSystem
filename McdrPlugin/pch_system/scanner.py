"""背包 / 手持物品扫描 + 一键提交行匹配（纯函数，可单测）。

依赖 minecraft_data_api（https://github.com/Fallen-Breath/MinecraftDataAPI，S-1 已联网核实）::

    api = server.get_plugin_instance("minecraft_data_api")
    api.get_player_info(player, "Inventory")     → list[item dict] | None（超时）
    api.get_player_info(player, "SelectedItem")  → held item dict | None（空手/超时）

item dict 形态随版本（scanner 两条路径都探；真机只验 1.20.1，1.20.5+ 路径代码兼容）：

  * 1.20.4-（TestServer 1.20.1 走此路径）::
        {"id": "minecraft:stone", "Count": 64, "tag": {...}}
        潜影盒内含物在 ``tag.BlockEntityTag.Items``
  * 1.20.5+ ::
        {"id": "minecraft:stone", "count": 64, "components": {...}}
        潜影盒内含物在 ``components."minecraft:container"``（list of {slot, item}）

设计约束：
  * **纯申报语义**——本模块只读不消耗（不清背包、不 ``data merge``）。
  * **不 import mcdreforged**——``api`` 由调用方注入，可在无 MCDR 运行时的环境单测。
  * 中文名不在此处翻译——后端 ``LangJsonTranslator`` 据 registry_id 自动补。
"""
from collections import Counter
from typing import Optional


# === 嵌套展开 ===

def _extract_nested_items(item: dict) -> Optional[list]:
    """若 item 是带内含物的容器（潜影盒等），返回其内含物列表；否则 None。

    1.20.4- 走 ``tag.BlockEntityTag.Items``；1.20.5+ 走 ``components."minecraft:container"``。
    空容器（无 Items / 空 container）→ None，交由调用方当普通物品计入（空壳子本身计 1）。
    """
    tag = item.get("tag")
    if isinstance(tag, dict):
        block_entity = tag.get("BlockEntityTag")
        if isinstance(block_entity, dict):
            items = block_entity.get("Items")
            if isinstance(items, list):
                return items
    components = item.get("components")
    if isinstance(components, dict):
        container = components.get("minecraft:container")
        if isinstance(container, list):
            # 1.20.5+ entry 形如 {"slot": N, "item": {...}}
            nested = [
                entry["item"]
                for entry in container
                if isinstance(entry, dict) and isinstance(entry.get("item"), dict)
            ]
            return nested if nested else None
    return None


def expand_items(items: list, acc: Counter) -> None:
    """递归展开物品列表到 ``Counter[registry_id]``。

    潜影盒外壳不计入（只累加内含物）；空容器外壳会被当普通物品计入。
    非法 entry（非 dict / 无 id / id 非字符串）跳过。Count 兼容大小写。
    """
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = it.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        nested = _extract_nested_items(it)
        if nested is not None:
            # 容器外壳：递归内含物，外壳本身不计
            expand_items(nested, acc)
            continue
        count = it.get("Count", it.get("count", 1))
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        acc[rid] += count


# === 读取入口（注入 api，便于单测 mock）===

def scan_inventory(api, player: str) -> dict:
    """读玩家完整背包（含嵌套潜影盒）→ ``{registry_id: total_count}``。

    api 未装 / 超时 / 返回非 list → ``{}``（调用方据空结果决定回执）。
    """
    if api is None:
        return {}
    raw = api.get_player_info(player, "Inventory")
    if not isinstance(raw, list):
        return {}
    acc: Counter = Counter()
    expand_items(raw, acc)
    return dict(acc)


def read_held_item(api, player: str) -> Optional[tuple]:
    """读手持物品 → ``(registry_id, count)``；空手 / 超时 / 无效 → None。

    中文名由后端翻译表补，此处只返回 registry_id。
    """
    if api is None:
        return None
    sel = api.get_player_info(player, "SelectedItem")
    if not isinstance(sel, dict):
        return None
    rid = sel.get("id")
    if not isinstance(rid, str) or not rid:
        return None
    count = sel.get("Count", sel.get("count", 1))
    try:
        count = int(count)
    except (TypeError, ValueError):
        return None
    return (rid, count)


# === 回执折叠判定（纯函数）===
#
# 行级决策（lock→deliver / progress→contribute / skip + reason）的权威实现已移交后端
# ``sheet_repo.batch_submit``（单事务逐行 FOR UPDATE）；MCDR ``!!submit`` 薄壳化后只
# 扫背包 + POST /submit-batch + 渲染 outcomes，不再在客户端复刻决策逻辑（消除双份漂移）。
#
# 以下两个谓词供回执渲染折叠跳过行用，取**基元参数**（调用方从后端 ``BatchRowOutcome``
# dict 抽 mode/is_claimant/reason 传入）。reason 字面量与后端 ``BATCH_REASON_*`` 逐字
# 对齐，改字面量会破折叠适配（后端 ``sheet_repo.py:39-43`` 注释为证）。

REASON_NO_ITEM = "背包没有此物"   # progress 行未提交此物（后端 _batch_decide_progress）
REASON_READY = "已备齐"           # lock done / progress done 或 delivered>=need


def skip_is_noise(*, mode: int, is_claimant: bool, reason: str) -> bool:
    """skip 行是否与本人当前无关 → 回执折叠（不逐行展示）。

    - lock 行非本人认领（``is_claimant=False``，含「需先认领」「已被他人认领」）→ 折叠；
    - progress 行未携带（``reason == REASON_NO_ITEM``）→ 折叠；
    其余跳过（本人认领的 lock 未完成、progress 已备齐 / 无需求 / 「行状态变化」「行已删除」
    等后端新增 reason）逐行展示——这些是玩家本次可操作或需感知的项。
    """
    if mode == 0:
        return not is_claimant
    return reason == REASON_NO_ITEM


def skip_is_ready(reason: str) -> bool:
    """skip 行是否已备齐 / 进度已满 → 回执折叠。

    判定锚定在 reason 字面量（``REASON_READY``），与 lock/progress、need=0 既有语义无关——
    后端决策时已把所有「已完成」情形归为同一 reason，客户端无需重算 status/delivered/need。
    """
    return reason == REASON_READY
