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
from dataclasses import dataclass
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


# === 行匹配（纯函数）===

@dataclass
class MatchAction:
    """一行匹配结果。``action`` ∈ {"deliver", "contribute", "skip"}。

    * deliver：lock 行已认领且自己为认领人，have≥need，``deliver_row(need)`` 绝对值 → done。``qty=need``。
    * contribute：progress 行未满，``contribute(min(have, need-delivered))`` 封顶到 need。
    * skip：不符合条件（``reason`` 给中文原因供回执）。
    """

    row_id: int
    registry_id: str
    item_name: str
    mode: int
    action: str
    qty: int = 0
    reason: str = ""


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def match_rows(rows: list, inventory: dict, player_uuid: str = "") -> list:
    """按 registry_id 精确匹配表行 → ``list[MatchAction]``。

    无 registry_id 的行不参与（不产生 action）；每个匹配行恰好一个 action（含 skip）。

    lock 模式必须**已是认领人**才进入提交：``player_uuid`` 与行的 ``claimant_uuid`` 匹配
    且 status=claimed、have≥need → ``deliver``；其余 lock 行 → ``skip``。
    ``player_uuid`` 默认空串（等价于"非认领人"），保持向后兼容。
    """
    actions: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get("registry_id")
        if not isinstance(rid, str) or not rid:
            continue
        row_id = _to_int(r.get("id"))
        item_name = r.get("item_name") or rid
        mode = _to_int(r.get("mode"))
        status = r.get("status")
        need = _to_int(r.get("need_qty"))
        delivered = _to_int(r.get("delivered_qty"))
        have = _to_int(inventory.get(rid, 0))

        if mode == 0:  # lock
            is_claimant = (
                bool(player_uuid)
                and str(r.get("claimant_uuid") or "") == player_uuid
            )
            if is_claimant and status == "claimed" and need > 0 and have >= need:
                actions.append(MatchAction(row_id, rid, item_name, mode, "deliver", need))
            else:
                actions.append(MatchAction(
                    row_id, rid, item_name, mode, "skip", 0,
                    _skip_reason_lock(status, need, have, is_claimant),
                ))
        else:  # progress
            if need > 0 and status != "done" and delivered < need and have > 0:
                qty = min(have, need - delivered)
                actions.append(MatchAction(row_id, rid, item_name, mode, "contribute", qty))
            else:
                actions.append(MatchAction(
                    row_id, rid, item_name, mode, "skip", 0,
                    _skip_reason_progress(status, need, delivered, have),
                ))
    return actions


def _skip_reason_lock(status, need: int, have: int, is_claimant: bool = False) -> str:
    if status == "open":
        return "需先认领"
    if status == "claimed" and not is_claimant:
        return "已被他人认领"
    if status == "done":
        return "已备齐"
    if need <= 0:
        return "无需求"
    return f"数量不足（{have}/{need}）"


def _skip_reason_progress(status, need: int, delivered: int, have: int) -> str:
    if need <= 0:
        return "无需求"
    if status == "done" or delivered >= need:
        return "已备齐"
    if have <= 0:
        return "背包没有此物"
    return "不满足上交条件"
