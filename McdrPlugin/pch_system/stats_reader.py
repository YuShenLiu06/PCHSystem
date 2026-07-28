"""施工进度采集：读取玩家 stats json + 解析 ``used``/``mined`` 计数（纯函数，可单测）。

不依赖 mcdreforged / minecraft_data_api —— 统计存独立文件 ``<世界>/stats/<uuid>.json``，
``minecraft_data_api`` 读不到（其 API 仅 Player NBT，S-1 联网核实：
https://github.com/Fallen-Breath/MinecraftDataAPI ）。

stats 文件结构（S-1 真机核实点：实际键见 https://zh.minecraft.wiki/w/统计信息 ）::

    {"stats": {"minecraft:used": {"minecraft:stone": 5}, "minecraft:mined": {...}},
     "DataVersion": 2586}

- ``minecraft:used`` 键 = 物品 registry id（放置 ≈ 物品「使用」次数），天然对齐后端
  ``sheet_rows.registry_id``（R-6），无需归一化。
- ``minecraft:mined`` 键 = 方块 id（可能与物品 id 不一致，如 ``wall_torch`` vs ``torch``）；
  本期 ``construction_track_breaking=false`` 默认不读（见 construction_tracker）。

设计约束（同 scanner.py）：
  * **纯函数 + 直接文件 IO**——不 import mcdreforged，可在无 MCDR 运行时单测；
  * **只读不写**——stats 文件由服务端维护，插件绝不改。
"""
import json
from pathlib import Path
from typing import Optional

# stats json 顶层 ``stats`` 下的类别键（S-1：Minecraft Wiki 统计信息）
USED_KEY = "minecraft:used"   # 物品「使用」计数（放置近似）
MINED_KEY = "minecraft:mined"  # 方块「挖掘」计数（block id，本期默认不用）


def stats_path_for(world_stats_dir: str, player_uuid: str) -> Path:
    """``<world_stats_dir>/<uuid>.json``（uuid 为带连字符字符串）。"""
    return Path(world_stats_dir) / f"{player_uuid}.json"


def read_stats_file(path) -> Optional[dict]:
    """读取 stats json → 完整 dict；文件不存在 / JSON 非法 / 非 dict → None。

    ``path`` 接受 str | Path（调用方既有 str 也有 Path，统一兼容）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _category(stats_doc: dict, key: str) -> dict:
    """从 stats_doc 取 ``stats.<key>`` 计数字典，防御性导航；缺失/非 dict → {}。

    值强制 int（原版存 int，防御 float/str 异常输入）；非数值条目跳过。
    """
    if not isinstance(stats_doc, dict):
        return {}
    inner = stats_doc.get("stats")
    if not isinstance(inner, dict):
        # 容错：少数老版本/变种可能没有外层 "stats" 包裹，直接顶层取
        inner = stats_doc
    cat = inner.get(key)
    if not isinstance(cat, dict):
        return {}
    out: dict = {}
    for k, v in cat.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def used_counts(stats_doc: dict) -> dict:
    """``minecraft:used`` → ``{物品 registry_id: 累积量}``。"""
    return _category(stats_doc, USED_KEY)


def mined_counts(stats_doc: dict) -> dict:
    """``minecraft:mined`` → ``{方块 id: 累积量}``（本期默认不启用）。"""
    return _category(stats_doc, MINED_KEY)


def diff_counts(current: dict, baseline: dict) -> dict:
    """纯函数差值：``current - baseline``，仅保留 > 0 的增量。

    baseline 缺该键视作 0（新出现的物品全额计入）。**首见整体建基** 由调用方
    （construction_tracker）处理：首次见到玩家时 baseline = current，故首轮 diff 为空，
    避免把历史放置量当本轮增量上报（plan §幂等策略）。
    """
    result: dict = {}
    for rid, cur in current.items():
        try:
            cur_i = int(cur)
        except (TypeError, ValueError):
            continue
        try:
            base_i = int(baseline.get(rid, 0))
        except (TypeError, ValueError):
            base_i = 0
        delta = cur_i - base_i
        if delta > 0:
            result[rid] = delta
    return result
