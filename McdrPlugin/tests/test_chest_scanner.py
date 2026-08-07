"""chest_scanner 单测。

测试维度：
1. ``_direction_vector`` — yaw/pitch → 方向向量（数学正确性）
2. ``parse_rcon_block_items`` — SNBT 解析 + 边界（非容器/空/None）
3. ``parse_block_coords`` — 坐标提取
4. ``_raycast_positions`` — 射线步进去重
5. ``_PARTNER_OFFSET`` — 8 种 (facing, type) → partner 方向正确性
6. ``detect_facing_type`` — mock RCON 枚举 block state
7. ``read_double_chest`` — mock 合并 / 单箱降级 / partner 空
"""
import math
import os
import sys
import unittest

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

from pch_system.chest_scanner import (  # noqa: E402
    _direction_vector,
    _raycast_positions,
    parse_rcon_block_items,
    parse_block_coords,
    detect_facing_type,
    read_double_chest,
    find_targeted_chest,
    _PARTNER_OFFSET,
)


# ── mock 工具 ──────────────────────────────────────────────

class _MockLogger:
    """Mock logger，丢弃所有日志输出。"""
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
    def exception(self, *a, **kw): pass


class _MockServer:
    """Mock MCDR server，按命令字符串映射 RCON 响应。"""

    def __init__(self, responses=None, rcon_running=True):
        self._responses = responses or {}
        self._rcon = rcon_running
        self.logger = _MockLogger()

    def is_rcon_running(self):
        return self._rcon

    def rcon_query(self, command):
        return self._responses.get(command)


_BLOCK_DATA_FMT = (
    'The block at {x},{y},{z} has the following block data: '
    '{{id: "minecraft:chest", Items: [{items}]}}'
)


def _chest_response(x, y, z, items=""):
    return _BLOCK_DATA_FMT.format(x=x, y=y, z=z, items=items)


# ── 1. _direction_vector ──────────────────────────────────

class TestDirectionVector(unittest.TestCase):
    def test_yaw_0_faces_south(self):
        """yaw=0 → looking south (+Z)"""
        dx, dy, dz = _direction_vector(0, 0)
        self.assertAlmostEqual(dx, 0, places=3)
        self.assertAlmostEqual(dy, 0, places=3)
        self.assertAlmostEqual(dz, 1.0, places=3)

    def test_yaw_90_faces_west(self):
        """yaw=90 → looking west (-X)"""
        dx, dy, dz = _direction_vector(90, 0)
        self.assertAlmostEqual(dx, -1.0, places=3)
        self.assertAlmostEqual(dy, 0, places=3)
        self.assertAlmostEqual(dz, 0, places=3)

    def test_yaw_180_faces_north(self):
        """yaw=180 → looking north (-Z)"""
        dx, dy, dz = _direction_vector(180, 0)
        self.assertAlmostEqual(dx, 0, places=3)
        self.assertAlmostEqual(dy, 0, places=3)
        self.assertAlmostEqual(dz, -1.0, places=3)

    def test_yaw_270_faces_east(self):
        """yaw=270 → looking east (+X)"""
        dx, dy, dz = _direction_vector(270, 0)
        self.assertAlmostEqual(dx, 1.0, places=3)
        self.assertAlmostEqual(dy, 0, places=3)
        self.assertAlmostEqual(dz, 0, places=3)

    def test_pitch_90_faces_down(self):
        """pitch=90 → looking straight down (-Y)"""
        dx, dy, dz = _direction_vector(0, 90)
        self.assertAlmostEqual(dy, -1.0, places=3)

    def test_pitch_neg_90_faces_up(self):
        """pitch=-90 → looking straight up (+Y)"""
        dx, dy, dz = _direction_vector(0, -90)
        self.assertAlmostEqual(dy, 1.0, places=3)

    def test_vector_is_normalized(self):
        """方向向量是归一化的（长度≈1）"""
        dx, dy, dz = _direction_vector(45, 30)
        length = math.sqrt(dx**2 + dy**2 + dz**2)
        self.assertAlmostEqual(length, 1.0, places=3)


# ── 2. parse_rcon_block_items ─────────────────────────────

class TestParseRconBlockItems(unittest.TestCase):
    def test_chest_with_items(self):
        raw = (
            'The block at 10,64,20 has the following block data: '
            '{id: "minecraft:chest", Items: [{Slot: 0b, id: "minecraft:stone", Count: 64b}, '
            '{Slot: 1b, id: "minecraft:oak_log", Count: 32b}]}'
        )
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(err)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "minecraft:stone")
        self.assertEqual(items[0]["Count"], 64)

    def test_chest_with_shulker_box(self):
        raw = (
            'The block at 10,64,20 has the following block data: '
            '{id: "minecraft:chest", Items: [{Slot: 0b, id: "minecraft:shulker_box", Count: 1b, '
            'tag: {BlockEntityTag: {Items: [{Slot: 0b, id: "minecraft:diamond", Count: 64b}]}}}]}'
        )
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)

    def test_empty_chest(self):
        raw = (
            'The block at 10,64,20 has the following block data: '
            '{id: "minecraft:chest", Items: []}'
        )
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(err)
        self.assertEqual(items, [])

    def test_empty_chest_no_items_tag(self):
        """MC 1.20.1 空箱子可能省略 Items 标签 → 返回空列表（非 not_container）。"""
        raw = (
            'The block at 10,64,20 has the following block data: '
            '{id: "minecraft:chest"}'
        )
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(err)
        self.assertEqual(items, [])

    def test_non_block_entity(self):
        raw = "10, 64, 20 is not a block entity"
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(items)
        self.assertEqual(err, "not_container")

    def test_block_entity_without_items(self):
        raw = (
            'The block at 10,64,20 has the following block data: '
            '{id: "minecraft:sign", Text1: \'{"text":""}\'}'
        )
        items, err = parse_rcon_block_items(raw)
        self.assertIsNone(items)
        self.assertEqual(err, "not_container")

    def test_empty_raw(self):
        items, err = parse_rcon_block_items("")
        self.assertIsNone(items)
        self.assertEqual(err, "unknown_format")

    def test_unknown_format(self):
        items, err = parse_rcon_block_items("some random error message")
        self.assertIsNone(items)
        self.assertEqual(err, "unknown_format")


# ── 3. parse_block_coords ─────────────────────────────────

class TestParseBlockCoords(unittest.TestCase):
    def test_standard_prefix(self):
        raw = 'The block at 10,64,20 has the following block data: {...}'
        self.assertEqual(parse_block_coords(raw), (10, 64, 20))

    def test_no_at_prefix(self):
        raw = '10, 64, 20 has the following block data: {...}'
        self.assertEqual(parse_block_coords(raw), (10, 64, 20))

    def test_negative_coords(self):
        raw = 'The block at -5,-64,-100 has the following block data: {...}'
        self.assertEqual(parse_block_coords(raw), (-5, -64, -100))

    def test_no_match(self):
        raw = "some random message without coords"
        self.assertIsNone(parse_block_coords(raw))


# ── 4. _raycast_positions ─────────────────────────────────

class TestRaycastPositions(unittest.TestCase):
    def test_horizontal_south(self):
        """水平向南射 6 格 → Z 递增的方块序列"""
        positions = _raycast_positions(0.5, 65.0, 0.5, 0, 0, 1, max_distance=6.0)
        self.assertGreater(len(positions), 0)
        self.assertTrue(all(isinstance(p, tuple) and len(p) == 3 for p in positions))
        z_values = [p[2] for p in positions]
        self.assertEqual(z_values, sorted(z_values))

    def test_no_duplicates(self):
        """DDA 每步进入新方块，无重复"""
        positions = _raycast_positions(0.5, 65.0, 0.5, 0, 0, 1, max_distance=3.0)
        self.assertEqual(len(positions), len(set(positions)))

    def test_vertical_down(self):
        """垂直向下射 → Y 递减"""
        positions = _raycast_positions(0.5, 65.0, 0.5, 0, -1, 0, max_distance=5.0)
        self.assertGreater(len(positions), 0)
        y_values = [p[1] for p in positions]
        self.assertEqual(y_values, sorted(y_values, reverse=True))

    def test_max_distance_limit(self):
        """射程限制：方块数不超过 max_distance（每格 1 block）"""
        positions = _raycast_positions(0.0, 0.0, 0.0, 1, 0, 0, max_distance=10.0)
        self.assertLessEqual(len(positions), 10)

    def test_empty_ray(self):
        """零距离 → 空列表"""
        positions = _raycast_positions(0, 0, 0, 0, 0, 1, max_distance=0)
        self.assertEqual(positions, [])

    def test_diagonal_no_skip(self):
        """对角射线不跳格——DDA 核心保证（旧采样法在此场景会跳格）。"""
        # 从 (-0.4, 0.5) 朝 (-1,-1) 方向射：X,Y 边界接近同时穿越
        positions = _raycast_positions(-0.4, 0.5, 0.0, -1, -1, 0, max_distance=3.0)
        coords = set(positions)
        # DDA 保证 (-1,-1,0) 被访问（旧 step=0.5 采样会跳到 (-2,-1) 漏掉此格）
        self.assertIn((-1, -1, 0), coords)
        self.assertIn((-2, -1, 0), coords)


# ── 5. _PARTNER_OFFSET 正确性 ──────────────────────────────

class TestPartnerOffset(unittest.TestCase):
    """验证偏移表：partner 方向唯一、type 相反、同轴。"""

    def test_all_8_combinations_present(self):
        """4 facing × 2 type = 8 条，无遗漏。"""
        self.assertEqual(len(_PARTNER_OFFSET), 8)

    def test_north_left_partner_east(self):
        self.assertEqual(_PARTNER_OFFSET[("north", "left")], (1, 0))

    def test_north_right_partner_west(self):
        self.assertEqual(_PARTNER_OFFSET[("north", "right")], (-1, 0))

    def test_south_left_partner_west(self):
        """facing=south → 箱子面朝南 → 左手=东 → left 在东 → partner 在西。"""
        self.assertEqual(_PARTNER_OFFSET[("south", "left")], (-1, 0))
        self.assertEqual(_PARTNER_OFFSET[("south", "right")], (1, 0))

    def test_east_west_on_z_axis(self):
        """facing=east/west 时 partner 沿 Z 轴。"""
        self.assertEqual(_PARTNER_OFFSET[("east", "left")], (0, 1))
        self.assertEqual(_PARTNER_OFFSET[("east", "right")], (0, -1))
        self.assertEqual(_PARTNER_OFFSET[("west", "left")], (0, -1))
        self.assertEqual(_PARTNER_OFFSET[("west", "right")], (0, 1))

    def test_partner_is_symmetric(self):
        """A 的 partner 偏移取反 = partner 的 partner 偏移指向 A。"""
        for (facing, chest_type), (dx, dz) in _PARTNER_OFFSET.items():
            partner_type = "right" if chest_type == "left" else "left"
            pdx, pdz = _PARTNER_OFFSET[(facing, partner_type)]
            self.assertEqual((pdx, pdz), (-dx, -dz),
                             f"asymmetry for ({facing},{chest_type})")


# ── 6. detect_facing_type ──────────────────────────────────

class TestDetectFacingType(unittest.TestCase):
    def test_left_north(self):
        x, y, z = 10, 64, 20
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}":
                _chest_response(x, y, z),
        })
        self.assertEqual(detect_facing_type(server, x, y, z), ("north", "left"))

    def test_right_east(self):
        x, y, z = 5, 70, -3
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=right,facing=east]"
            f" run data get block {x} {y} {z}":
                _chest_response(x, y, z),
        })
        self.assertEqual(detect_facing_type(server, x, y, z), ("east", "right"))

    def test_trapped_chest_double(self):
        """陷阱箱（minecraft:trapped_chest）双联也能检测。"""
        x, y, z = 3, 65, 7
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:trapped_chest[type=left,facing=west]"
            f" run data get block {x} {y} {z}":
                _chest_response(x, y, z),
        })
        self.assertEqual(detect_facing_type(server, x, y, z), ("west", "left"))

    def test_single_fast_path(self):
        """单箱快路径：type=single 命中后直接返回 None，不触发后续枚举。"""
        x, y, z = 10, 64, 20
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=single]"
            f" run data get block {x} {y} {z}":
                _chest_response(x, y, z),
        })
        self.assertIsNone(detect_facing_type(server, x, y, z))

    def test_single_chest_returns_none(self):
        x, y, z = 10, 64, 20
        responses = {}
        for facing in ("north", "south", "east", "west"):
            for ct in ("left", "right"):
                responses[
                    f"execute if block {x} {y} {z} minecraft:chest[type={ct},facing={facing}]"
                    f" run data get block {x} {y} {z}"
                ] = None
        server = _MockServer(responses)
        self.assertIsNone(detect_facing_type(server, x, y, z))


# ── 7. read_double_chest ───────────────────────────────────

class TestReadDoubleChest(unittest.TestCase):
    def test_single_chest_no_merge(self):
        """type=single → detect 返回 None → 原样返回。"""
        x, y, z = 10, 64, 20
        responses = {}
        for facing in ("north", "south", "east", "west"):
            for ct in ("left", "right"):
                responses[
                    f"execute if block {x} {y} {z} minecraft:chest[type={ct},facing={facing}]"
                    f" run data get block {x} {y} {z}"
                ] = None
        server = _MockServer(responses)
        primary = {"minecraft:stone": 64}
        result = read_double_chest(server, x, y, z, primary)
        self.assertEqual(result, primary)

    def test_double_chest_merge(self):
        """type=left,facing=north → partner 在东 (+1,0) → 合并。"""
        x, y, z = 10, 64, 20
        px, pz = x + 1, z
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}": _chest_response(x, y, z),
            f"data get block {px} {y} {pz}":
                _chest_response(px, y, pz,
                    '{Slot: 0b, id: "minecraft:diamond", Count: 32b}'),
        })
        primary = {"minecraft:stone": 64}
        result = read_double_chest(server, x, y, z, primary)
        self.assertEqual(result, {"minecraft:stone": 64, "minecraft:diamond": 32})

    def test_partner_empty_returns_primary(self):
        """partner 为空 → 原样返回 primary。"""
        x, y, z = 10, 64, 20
        px, pz = x + 1, z
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}": _chest_response(x, y, z),
            f"data get block {px} {y} {pz}":
                _chest_response(px, y, pz, ""),
        })
        primary = {"minecraft:stone": 64}
        result = read_double_chest(server, x, y, z, primary)
        self.assertEqual(result, primary)

    def test_partner_not_chest_returns_primary(self):
        """partner 不是箱子 → 原样返回 primary。"""
        x, y, z = 10, 64, 20
        px, pz = x + 1, z
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}": _chest_response(x, y, z),
            f"data get block {px} {y} {pz}":
                f"{px}, {y}, {pz} is not a block entity",
        })
        primary = {"minecraft:stone": 64}
        result = read_double_chest(server, x, y, z, primary)
        self.assertEqual(result, primary)

    def test_empty_primary_merges_partner(self):
        """主半为空但 partner 有物品 → 返回 partner 物品（端到端核心场景）。"""
        x, y, z = 10, 64, 20
        px, pz = x + 1, z  # facing=north, type=left → partner 在东
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}": _chest_response(x, y, z),
            f"data get block {px} {y} {pz}":
                _chest_response(px, y, pz,
                    '{Slot: 0b, id: "minecraft:diamond", Count: 64b}'),
        })
        result = read_double_chest(server, x, y, z, {})
        self.assertEqual(result, {"minecraft:diamond": 64})

    def test_same_item_adds_quantities(self):
        """两半有相同物品 → 数量相加。"""
        x, y, z = 10, 64, 20
        px, pz = x - 1, z  # facing=north, type=right → partner 在西
        server = _MockServer({
            f"execute if block {x} {y} {z} minecraft:chest[type=left,facing=north]"
            f" run data get block {x} {y} {z}": None,
            f"execute if block {x} {y} {z} minecraft:chest[type=right,facing=north]"
            f" run data get block {x} {y} {z}": _chest_response(x, y, z),
            f"data get block {px} {y} {pz}":
                _chest_response(px, y, pz,
                    '{Slot: 0b, id: "minecraft:stone", Count: 32b}'),
        })
        primary = {"minecraft:stone": 64}
        result = read_double_chest(server, x, y, z, primary)
        self.assertEqual(result, {"minecraft:stone": 96})


# ── 8. find_targeted_chest ─────────────────────────────────


class _MockApi:
    """Mock minecraft_data_api plugin。"""

    def __init__(self, pos, rot):
        self._pos = pos
        self._rot = rot

    def get_player_info(self, player, path):
        if path == "Pos":
            return self._pos
        if path == "Rotation":
            return self._rot
        return None


class TestFindTargetedChest(unittest.TestCase):
    def test_empty_chest_returns_empty_not_notfound(self):
        """空箱子命中后返回 (None, 'empty')，而非跳过继续遍历 → 'not_found'。"""
        api = _MockApi([0.5, 65.0, 0.5], [0, 0])  # 朝南
        # 射线第一个方块 (0, 66, 1) = 空箱子
        server = _MockServer({
            "data get block 0 66 1": _chest_response(0, 66, 1, ""),
        })
        items, err = find_targeted_chest(api, server, "test_player", max_distance=3.0)
        self.assertIsNone(items)
        self.assertEqual(err, "empty")

    def test_chest_with_items_returns_items(self):
        """有物品的箱子正常返回 items dict。"""
        api = _MockApi([0.5, 65.0, 0.5], [0, 0])  # 朝南
        server = _MockServer({
            "data get block 0 66 1": _chest_response(0, 66, 1,
                '{Slot: 0b, id: "minecraft:stone", Count: 32b}'),
        })
        items, err = find_targeted_chest(api, server, "test_player", max_distance=3.0)
        self.assertIsNone(err)
        self.assertEqual(items, {"minecraft:stone": 32})

    def test_no_chest_in_range_returns_not_found(self):
        """射程内无箱子 → 'not_found'。"""
        api = _MockApi([0.5, 65.0, 0.5], [0, 0])
        # 所有方块返回非箱子
        server = _MockServer({
            "data get block 0 66 1":
                "0, 66, 1 is not a block entity",
        })
        items, err = find_targeted_chest(api, server, "test_player", max_distance=3.0)
        self.assertIsNone(items)
        self.assertEqual(err, "not_found")


if __name__ == "__main__":
    unittest.main()
