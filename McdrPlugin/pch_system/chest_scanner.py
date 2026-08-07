"""箱子内容读取 + 准星射线检测 + 双联箱子合并（纯函数 + I/O 薄壳，可单测）。

数据链路：
  ``api.get_player_info`` Pos/Rotation → ``_direction_vector`` 方向向量
  → ``_raycast_positions`` 逐格步进 → ``data get block`` RCON 读方块
  → ``parse_rcon_block_items`` 剥前缀 + SNBT 预处理 + hjson.loads
  → ``scanner.expand_items`` 复用嵌套展开（潜影盒递归）
  → ``read_double_chest`` 检测双联 partner 合并 54 格
  → ``{registry_id: qty}`` dict（与 ``scan_inventory`` 同构）

设计约束：
  * **纯申报语义**（RS-3 衍生）：只读箱子 NBT，绝不 ``data merge`` 清空。
  * **RS-7**：SNBT 预处理逻辑提取自 MinecraftDataAPI 的 ``MinecraftJsonParser``
    （<https://github.com/Fallen-Breath/MinecraftDataAPI>），非自研。
  * **RS-10**：射线检测与 partner 探测均为串行 RCON。
  * **S-1**：``execute if block[type=,facing=]`` 为 MC 1.20.1 标准命令语法
    （<https://minecraft.wiki/w/Chest> § Block states），已联网核实。

封装层级（用户可复用）：
  * ``read_double_chest(server, x, y, z, primary_items)`` — 传入主半 items + 坐标，
    自动检测 partner 并合并。任何持有箱子坐标的模块均可直接调用。
  * ``detect_facing_type(server, x, y, z)`` — 低级 API，返回 (facing, type) 供其他用途。
"""
import logging
import math
import re
from collections import Counter
from typing import Optional

import hjson

from .scanner import expand_items

_log = logging.getLogger("pch_system.chest_scanner")

# RCON block data 响应前缀（MC 1.20.1）：
#   "The block at 10,64,20 has the following block data: {...}"
#   "10, 64, 20 has the following block data: {...}"
_BLOCK_DATA_PREFIX_RE = re.compile(r'^.*?has the following block data:\s*')

# block data 响应中方块坐标提取（兼容 "at X,Y,Z" 和 "X, Y, Z" 两种格式）
_BLOCK_COORDS_RE = re.compile(r'(-?\d+),\s*(-?\d+),\s*(-?\d+) has the following block data:')

# 非方块实体错误标志（大小写不敏感判定）
_NOT_BLOCK_ENTITY_HINTS = ("not a block entity", "is not a block entity")

# 射线检测参数
_EYE_HEIGHT = 1.62
_DEFAULT_MAX_DISTANCE = 6.0
_DEFAULT_STEP = 0.5

# 双联箱子 partner 偏移表（来源：MC Wiki § Block states, https://minecraft.wiki/w/Chest）
# (facing, type) → (dx, dz)，partner 方向由 facing + type 唯一确定。
# MC 的 type=left/right 是**箱子自身视角**：箱子面朝 `facing` 方向站立时的左手 = left 半。
# 实测验证（2026-08-07）：facing=south → left 在东(+X)、right 在西(-X)。
#   facing=south → 箱子面朝南 → 左手=东(+X) → type=left 在东 → partner(right) 在西 → dx=-1
#   facing=north → 箱子面朝北 → 左手=西(-X) → type=left 在西 → partner(right) 在东 → dx=+1
#   facing=east  → 箱子面朝东 → 左手=南(+Z) → type=left 在南 → partner(right) 在北 → dz=-1
#   facing=west  → 箱子面朝西 → 左手=北(-Z) → type=left 在北 → partner(right) 在南 → dz=+1
_PARTNER_OFFSET: dict[tuple[str, str], tuple[int, int]] = {
    ("north", "left"):  ( 1,  0),   # partner 在东
    ("north", "right"): (-1,  0),   # partner 在西
    ("south", "left"):  (-1,  0),   # partner 在西
    ("south", "right"): ( 1,  0),   # partner 在东
    ("east",  "left"):  ( 0,  1),   # partner 在南
    ("east",  "right"): ( 0, -1),   # partner 在北
    ("west",  "left"):  ( 0, -1),   # partner 在北
    ("west",  "right"): ( 0,  1),   # partner 在南
}


# === 纯函数（无 I/O 依赖，可独立单测）===

def _direction_vector(yaw_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    """MC yaw/pitch（degrees）→ 归一化方向向量 ``(dx, dy, dz)``。

    MC 朝向约定：yaw 0=南(+Z) / 90=西(-X) / 180=北(-Z) / 270=东(+X)；
    pitch 0=水平 / 90=下 / -90=上。
    """
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    dx = -math.sin(yaw) * math.cos(pitch)
    dy = -math.sin(pitch)
    dz = math.cos(yaw) * math.cos(pitch)
    return (dx, dy, dz)


# SNBT 预处理正则（提取自 MinecraftDataAPI 的 MinecraftJsonParser，RS-7）
# 来源：https://github.com/Fallen-Breath/MinecraftDataAPI/blob/master/minecraft_data_api/json_parser.py
_LETTER_AFTER_NUMBER_RE = re.compile(r'(([{\[:,]|^) *[+-]?\d+(\.\d*?)?(E[+-]?\d+)?)([bsLdf])')
_ARRAY_HEADER_RE = re.compile(r'(?<=\[)[IL];')
_FOLDED_TAG_RE = re.compile(r'<\.\.\.>')


def _preprocess_snbt(text: str) -> str:
    """SNBT → JSON-like 文本（提取自 MinecraftDataAPI，RS-7 不自研）。

    1. 移除数字后的类型后缀（``1b`` → ``1``、``2.0d`` → ``2.0``）
    2. 移除数组类型头（``[I; 1,2,3]`` → ``[1,2,3]``）
    3. 移除 1.20.5+ 折叠标记 ``<...>``
    引号内字符串不做替换（保持原样）。
    """
    result: list[str] = []
    i = 0
    while i < len(text):
        dq = text.find('"', i)
        sq = text.find("'", i)
        pos = min(p if p != -1 else len(text) for p in (dq, sq))
        non_quote = text[i:pos]
        non_quote = _LETTER_AFTER_NUMBER_RE.sub(r'\1', non_quote)
        non_quote = _ARRAY_HEADER_RE.sub('', non_quote)
        non_quote = _FOLDED_TAG_RE.sub('', non_quote)
        result.append(non_quote)
        i = pos
        if i == len(text):
            break
        quote = text[i]
        j = i + 1
        while j < len(text):
            slash_pos = text.find('\\', j)
            if slash_pos == -1:
                slash_pos = len(text)
            quote_pos = text.find(quote, j, slash_pos)
            if quote_pos == -1:
                j = slash_pos + 2
            else:
                j = quote_pos + 1
                break
        result.append(text[i:j])
        i = j
    return ''.join(result)


def parse_rcon_block_items(raw: str) -> tuple[Optional[list], Optional[str]]:
    """解析 RCON ``data get block`` 响应 → ``(items_list, None)`` 或 ``(None, error_code)``。

    error_code 取值：
      * ``"not_container"`` — 非容器方块（无 Items 字段 / 非方块实体）
      * ``"parse_error"`` — SNBT 解析失败
      * ``"unknown_format"`` — 未知响应格式

    SNBT 预处理逻辑提取自 MinecraftDataAPI 的 ``MinecraftJsonParser``（RS-7），
    使用 ``hjson.loads`` 做宽松 JSON 解析（``hjson`` 是容器已装的直接依赖）。
    """
    if not raw:
        return None, "unknown_format"

    raw_lower = raw.lower()
    if any(hint in raw_lower for hint in _NOT_BLOCK_ENTITY_HINTS):
        return None, "not_container"

    text = _BLOCK_DATA_PREFIX_RE.sub('', raw)
    if text == raw:
        return None, "unknown_format"

    try:
        text = _preprocess_snbt(text)
        value = hjson.loads(text)
    except Exception as e:
        _log.error("block SNBT parse failed: %s (text=%s)", e, text[:120])
        return None, "parse_error"

    if isinstance(value, dict):
        items = value.get("Items")
        if isinstance(items, list):
            return items, None
        # MC 1.20.1 对空箱子可能省略 Items 标签（wiki: "component is still
        # present on the block entity, even if the tag does not exist"）
        # 对 chest 类方块返回空列表，使调用方能继续双联 partner 检测
        block_id = str(value.get("id", ""))
        if "chest" in block_id.lower():
            return [], None
        return None, "not_container"
    if isinstance(value, list):
        return value, None
    return None, "not_container"


def parse_block_coords(raw: str) -> Optional[tuple[int, int, int]]:
    """从 RCON ``data get block`` 响应前缀解析方块坐标。

    兼容两种格式：
      ``"The block at 10,64,20 has the following block data: ..."`` → (10, 64, 20)
      ``"10, 64, 20 has the following block data: ..."`` → (10, 64, 20)
    """
    m = _BLOCK_COORDS_RE.search(raw)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _raycast_positions(
    ex: float, ey: float, ez: float,
    dx: float, dy: float, dz: float,
    max_distance: float = _DEFAULT_MAX_DISTANCE,
    step: float = _DEFAULT_STEP,  # noqa: ARG — 保留签名兼容，DDA 不使用
) -> list[tuple[int, int, int]]:
    """DDA 体素遍历（Amanatides & Woo）——访问射线路径上的**每个**方块（不跳格）。

    固定步长采样在对角线方向会跳格（射线同时穿过 X+Y 边界时，
    采样点可能落在下一个方块而跳过中间方块）。DDA 按边界穿越顺序
    逐格推进，保证不遗漏。

    纯函数无 I/O，可独立单测。
    """
    bx = math.floor(ex)
    by = math.floor(ey)
    bz = math.floor(ez)

    step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
    step_y = 1 if dy > 0 else (-1 if dy < 0 else 0)
    step_z = 1 if dz > 0 else (-1 if dz < 0 else 0)

    # t 值：到达下一个方块边界的距离
    if dx > 0:
        t_max_x = (bx + 1 - ex) / dx
    elif dx < 0:
        t_max_x = (bx - ex) / dx
    else:
        t_max_x = float('inf')
    if dy > 0:
        t_max_y = (by + 1 - ey) / dy
    elif dy < 0:
        t_max_y = (by - ey) / dy
    else:
        t_max_y = float('inf')
    if dz > 0:
        t_max_z = (bz + 1 - ez) / dz
    elif dz < 0:
        t_max_z = (bz - ez) / dz
    else:
        t_max_z = float('inf')

    t_delta_x = abs(1.0 / dx) if dx != 0 else float('inf')
    t_delta_y = abs(1.0 / dy) if dy != 0 else float('inf')
    t_delta_z = abs(1.0 / dz) if dz != 0 else float('inf')

    positions: list[tuple[int, int, int]] = []
    while True:
        # 推进到最近的方块边界
        if t_max_x < t_max_y and t_max_x < t_max_z:
            bx += step_x
            if t_max_x > max_distance:
                break
            t_max_x += t_delta_x
        elif t_max_y < t_max_z:
            by += step_y
            if t_max_y > max_distance:
                break
            t_max_y += t_delta_y
        else:
            bz += step_z
            if t_max_z > max_distance:
                break
            t_max_z += t_delta_z
        positions.append((bx, by, bz))

    return positions


# === I/O 函数（需 server / api 注入）===

def _read_block_items(server, x: int, y: int, z: int) -> tuple[Optional[dict], Optional[str]]:
    """读单块箱子 items（**不含双联合并**）→ ``({id: qty}, None)`` 或 ``(None, err)``。

    内部薄壳，供 ``scan_chest_rcon`` 和 ``read_double_chest`` 共用，
    避免 partner 读取时递归触发 partner 搜索。
    """
    if not server.is_rcon_running():
        return None, "no_rcon"
    raw = server.rcon_query(f"data get block {x} {y} {z}")
    if raw is None:
        return None, "no_rcon"
    items_list, err = parse_rcon_block_items(raw)
    if items_list is None:
        return None, err
    acc: Counter = Counter()
    expand_items(items_list, acc)
    return dict(acc), None


_CHEST_BLOCK_IDS = ("minecraft:chest", "minecraft:trapped_chest")


def detect_facing_type(server, x: int, y: int, z: int) -> Optional[tuple[str, str]]:
    """检测箱子方块的 ``(facing, type)`` block state（S-1 已联网核实）。

    兼容 ``minecraft:chest`` 与 ``minecraft:trapped_chest``（陷阱箱）。
    先用 ``type=single`` 快路径（最多 2 次 RCON）排除单箱，
    再枚举 4 facing × 2 type 定位双联 ``(facing, type)``。

    RCON 探针：``execute if block <pos> <block_id>[type=X,facing=Y] run data get block <pos>``
    — 条件满足时响应含 ``"block data"``；不满足时响应为空 / None。

    **可复用**：任何需要判断箱子 block state 的模块均可直接调用。
    """
    # 快路径：单箱检测（2 次 RCON，避免后续 8~16 次枚举）
    for block_id in _CHEST_BLOCK_IDS:
        result = server.rcon_query(
            f"execute if block {x} {y} {z}"
            f" {block_id}[type=single]"
            f" run data get block {x} {y} {z}"
        )
        if result and "block data" in result:
            return None  # 单箱
    # 双联箱子：枚举 (facing, type)，先查 chest 再查 trapped_chest
    for block_id in _CHEST_BLOCK_IDS:
        for facing in ("north", "south", "east", "west"):
            for chest_type in ("left", "right"):
                result = server.rcon_query(
                    f"execute if block {x} {y} {z}"
                    f" {block_id}[type={chest_type},facing={facing}]"
                    f" run data get block {x} {y} {z}"
                )
                if result and "block data" in result:
                    return (facing, chest_type)
    return None


def read_double_chest(server, x: int, y: int, z: int, primary_items: dict) -> dict:
    """检测并合并双联箱子 partner（可复用）。

    流程：
      1. ``detect_facing_type`` 判定 ``(facing, type)``，``None`` = 单箱直接返回；
      2. 查 ``_PARTNER_OFFSET`` 得 partner 偏移 ``(dx, dz)``（唯一方向）；
      3. ``_read_block_items`` 读 partner 单块（不递归 partner 搜索）；
      4. ``Counter`` 合并两半 → 54 格完整内容。

    **partner 不存在 / 为空 / 非箱子时**：返回 ``primary_items`` 不变（容错降级）。

    **可复用**：传入坐标 + 主半 items 即可，调用方无需关心 block state 细节。
    """
    ft = detect_facing_type(server, x, y, z)
    if ft is None:
        return primary_items  # 单箱
    dx, dz = _PARTNER_OFFSET[ft]
    partner_items, _err = _read_block_items(server, x + dx, y, z + dz)
    if not partner_items:
        return primary_items  # partner 不存在 / 为空 / 非箱子
    combined: Counter = Counter(primary_items)
    combined.update(partner_items)
    return dict(combined)


def scan_chest_rcon(server, x: int, y: int, z: int) -> tuple[Optional[dict], Optional[str]]:
    """RCON 读箱子（**含双联合并**）→ ``({registry_id: qty}, None)`` 或 ``(None, error_code)``。

    坐标模式入口：读指定坐标箱子 + 自动检测合并双联 partner。

    error_code 取值：
      * ``"no_rcon"`` — RCON 未运行 / 查询失败
      * ``"not_container"`` — 坐标处非容器方块
      * ``"parse_error"`` / ``"unknown_format"`` — 解析失败
      * ``"empty"`` — 容器为空
    """
    items, err = _read_block_items(server, x, y, z)
    if items is None:
        return None, err
    merged = read_double_chest(server, x, y, z, items)
    if not merged:
        return None, "empty"
    return merged, None


def find_targeted_chest(
    api, server, player: str,
    max_distance: float = _DEFAULT_MAX_DISTANCE,
    step: float = _DEFAULT_STEP,
) -> tuple[Optional[dict], Optional[str]]:
    """准星射线检测 + 读箱子（**含双联合并**）→ ``({id: qty}, None)`` 或 ``(None, error_code)``。

    流程：
      1. ``api.get_player_info`` 取 Pos + Rotation；
      2. ``_direction_vector`` 计算方向向量 + 眼部位置；
      3. ``_raycast_positions`` 生成候选方块列表；
      4. 逐方块串行 RCON 探测（RS-10），首个含 Items 的方块即读取；
      5. ``read_double_chest`` 检测合并双联 partner → 54 格完整内容。

    error_code 取值：
      * ``"no_api"`` / ``"no_pos"`` — 无法获取玩家位置
      * ``"no_rcon"`` — RCON 未运行
      * ``"not_found"`` — 射线范围内无容器
      * 其余透传 ``_read_block_items`` 的 error_code
    """
    if api is None:
        return None, "no_api"
    if not server.is_rcon_running():
        return None, "no_rcon"

    pos = api.get_player_info(player, "Pos")
    rot = api.get_player_info(player, "Rotation")
    if not isinstance(pos, (list, tuple)) or len(pos) < 3:
        return None, "no_pos"
    if not isinstance(rot, (list, tuple)) or len(rot) < 2:
        return None, "no_pos"

    dx, dy, dz = _direction_vector(float(rot[0]), float(rot[1]))
    ex, ey, ez = float(pos[0]), float(pos[1]) + _EYE_HEIGHT, float(pos[2])

    positions = _raycast_positions(ex, ey, ez, dx, dy, dz, max_distance, step)

    for bx, by, bz in positions:
        raw = server.rcon_query(f"data get block {bx} {by} {bz}")
        if raw is None:
            continue
        if "block data" not in raw:
            continue
        # 空箱子可能无 Items 标签（MC 1.20.1 省略空列表），
        # 但仍需检测是否为箱子以触发双联 partner 合并
        is_chest = "chest" in raw.lower()
        if "Items" not in raw and not is_chest:
            continue
        items_list, _err = parse_rcon_block_items(raw)
        if items_list is None:
            continue
        acc: Counter = Counter()
        expand_items(items_list, acc)
        primary_items = dict(acc) if acc else {}
        merged = read_double_chest(server, bx, by, bz, primary_items)
        if merged:
            return merged, None
        # 确认命中箱子但合并后为空 → 返回 "empty"（不再继续遍历后续方块）
        return None, "empty"

    return None, "not_found"
