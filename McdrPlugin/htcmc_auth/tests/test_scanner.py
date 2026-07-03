"""scanner.py 单测（纯 Python，不依赖 MCDR 运行时）。

通过 importlib 直接按文件路径加载 scanner.py，绕过 ``htcmc_auth/__init__.py``
（后者会 import mcdreforged，测试环境无该依赖）。scanner 本身只依赖标准库。
"""
import importlib.util
from collections import Counter
from pathlib import Path

# 按文件路径加载 scanner.py 为独立模块
_SPEC = importlib.util.spec_from_file_location(
    "_scanner_under_test",
    Path(__file__).resolve().parent.parent / "htcmc_auth" / "scanner.py",
)
scanner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scanner)

expand_items = scanner.expand_items
_extract_nested_items = scanner._extract_nested_items
scan_inventory = scanner.scan_inventory
read_held_item = scanner.read_held_item
match_rows = scanner.match_rows


# ---- 1.20.4- NBT 路径（TestServer 1.20.1 真机走此路径）----
INV_1201 = [
    {"Slot": 0, "id": "minecraft:stone", "Count": 32},
    {"Slot": 1, "id": "minecraft:oak_planks", "Count": 64},
    {
        "Slot": 2,
        "id": "minecraft:white_shulker_box",
        "Count": 1,
        "tag": {
            "BlockEntityTag": {
                "Items": [
                    {"Slot": 0, "id": "minecraft:stone", "Count": 32},
                    {"Slot": 1, "id": "minecraft:iron_ingot", "Count": 16},
                ]
            }
        },
    },
]

# ---- 1.20.5+ NBT 路径（代码兼容，真机不验）----
INV_1205 = [
    {"id": "minecraft:stone", "count": 32},
    {
        "id": "minecraft:black_shulker_box",
        "count": 1,
        "components": {
            "minecraft:container": [
                {"slot": 0, "item": {"id": "minecraft:cobblestone", "count": 64}},
            ]
        },
    },
]


class FakeApi:
    """模拟 minecraft_data_api 的 get_player_info。"""

    def __init__(self, inventory=None, selected=None):
        self.inventory = inventory
        self.selected = selected
        self.calls = []

    def get_player_info(self, player, path="", timeout=5):
        self.calls.append((player, path))
        if path == "Inventory":
            return self.inventory
        if path == "SelectedItem":
            return self.selected
        return None


class TestExpandItems:
    def test_普通物品累加(self):
        acc = Counter()
        expand_items([{"id": "minecraft:stone", "Count": 32}], acc)
        assert acc["minecraft:stone"] == 32

    def test_1201_潜影盒嵌套展开_外壳不计(self):
        acc = Counter()
        expand_items(INV_1201, acc)
        # stone: 32（背包）+ 32（盒内）= 64；oak_planks 64；iron_ingot 16
        # white_shulker_box 外壳不计
        assert acc["minecraft:stone"] == 64
        assert acc["minecraft:oak_planks"] == 64
        assert acc["minecraft:iron_ingot"] == 16
        assert "minecraft:white_shulker_box" not in acc

    def test_1205_components_路径容器(self):
        acc = Counter()
        expand_items(INV_1205, acc)
        assert acc["minecraft:stone"] == 32
        assert acc["minecraft:cobblestone"] == 64
        assert "minecraft:black_shulker_box" not in acc

    def test_空容器外壳当普通物品计入(self):
        acc = Counter()
        expand_items([{"id": "minecraft:white_shulker_box", "Count": 1}], acc)
        assert acc["minecraft:white_shulker_box"] == 1

    def test_非法entry跳过(self):
        acc = Counter()
        expand_items([None, "x", {"Count": 5}, {"id": "", "Count": 1}], acc)
        assert len(acc) == 0

    def test_count大小写兼容(self):
        acc = Counter()
        expand_items([{"id": "minecraft:dirt", "count": 10}], acc)  # 1.20.5 小写
        expand_items([{"id": "minecraft:dirt", "Count": 5}], acc)  # 1.20.4 大写
        assert acc["minecraft:dirt"] == 15

    def test_多层嵌套递归(self):
        # 盒中盒：外盒内含一个内盒，内盒含石头
        inner_box = {
            "id": "minecraft:gray_shulker_box",
            "Count": 1,
            "tag": {"BlockEntityTag": {"Items": [{"id": "minecraft:diamond", "Count": 2}]}},
        }
        outer_box = {
            "id": "minecraft:white_shulker_box",
            "Count": 1,
            "tag": {"BlockEntityTag": {"Items": [inner_box]}},
        }
        acc = Counter()
        expand_items([outer_box], acc)
        assert acc["minecraft:diamond"] == 2
        # 两层外壳都不计
        assert "minecraft:white_shulker_box" not in acc
        assert "minecraft:gray_shulker_box" not in acc


class TestExtractNested:
    def test_无嵌套返回None(self):
        assert _extract_nested_items({"id": "minecraft:stone", "Count": 1}) is None

    def test_1201_BlockEntityTag路径(self):
        it = {
            "id": "minecraft:shulker_box",
            "tag": {"BlockEntityTag": {"Items": [{"id": "minecraft:stone", "Count": 1}]}},
        }
        assert _extract_nested_items(it) == [{"id": "minecraft:stone", "Count": 1}]

    def test_1205_components路径(self):
        it = {
            "id": "minecraft:shulker_box",
            "components": {
                "minecraft:container": [
                    {"slot": 0, "item": {"id": "minecraft:dirt", "count": 1}},
                ]
            },
        }
        assert _extract_nested_items(it) == [{"id": "minecraft:dirt", "count": 1}]

    def test_空container返回None(self):
        it = {"id": "minecraft:shulker_box", "components": {"minecraft:container": []}}
        assert _extract_nested_items(it) is None


class TestScanInventory:
    def test_正常扫描含潜影盒(self):
        api = FakeApi(inventory=INV_1201)
        result = scan_inventory(api, "Steve")
        assert result["minecraft:stone"] == 64
        assert result["minecraft:iron_ingot"] == 16
        assert ("Steve", "Inventory") in api.calls

    def test_api为None返回空(self):
        assert scan_inventory(None, "Steve") == {}

    def test_超时返回None当作空(self):
        api = FakeApi(inventory=None)
        assert scan_inventory(api, "Steve") == {}


class TestReadHeldItem:
    def test_手持物品返回rid和数量(self):
        api = FakeApi(selected={"id": "minecraft:stone", "Count": 32})
        assert read_held_item(api, "Steve") == ("minecraft:stone", 32)

    def test_空手返回None(self):
        api = FakeApi(selected=None)
        assert read_held_item(api, "Steve") is None

    def test_1205小写count(self):
        api = FakeApi(selected={"id": "minecraft:dirt", "count": 10})
        assert read_held_item(api, "Steve") == ("minecraft:dirt", 10)

    def test_api为None返回None(self):
        assert read_held_item(None, "Steve") is None


# ---- match_rows ----

ROWS = [
    {"id": 1, "item_name": "石头", "registry_id": "minecraft:stone", "need_qty": 64, "delivered_qty": 0, "mode": 0, "status": "open"},
    {"id": 2, "item_name": "铁锭", "registry_id": "minecraft:iron_ingot", "need_qty": 32, "delivered_qty": 0, "mode": 0, "status": "claimed"},
    {"id": 3, "item_name": "橡木板", "registry_id": "minecraft:oak_planks", "need_qty": 128, "delivered_qty": 0, "mode": 0, "status": "open"},
    {"id": 4, "item_name": "无注册名", "registry_id": None, "need_qty": 10, "mode": 0, "status": "open"},
    {"id": 5, "item_name": "圆石", "registry_id": "minecraft:cobblestone", "need_qty": 100, "delivered_qty": 40, "mode": 1, "status": "claimed"},
    {"id": 6, "item_name": "泥土", "registry_id": "minecraft:dirt", "need_qty": 10, "delivered_qty": 10, "mode": 1, "status": "done"},
]


def _by_row(actions, row_id):
    return next(a for a in actions if a.row_id == row_id)


class TestMatchRows:
    def test_lock_open_够数_claim_deliver(self):
        a1 = _by_row(match_rows(ROWS, {"minecraft:stone": 64}), 1)
        assert a1.action == "claim_deliver"
        assert a1.qty == 64

    def test_lock_已认领_skip(self):
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}), 2)
        assert a2.action == "skip"
        assert "认领" in a2.reason

    def test_lock_不够数_skip(self):
        a3 = _by_row(match_rows(ROWS, {"minecraft:oak_planks": 64}), 3)
        assert a3.action == "skip"
        assert "不足" in a3.reason

    def test_无registry_id不产生action(self):
        actions = match_rows(ROWS, {})
        assert all(a.row_id != 4 for a in actions)

    def test_progress_封顶到need(self):
        # have=64, need=100, delivered=40 → contribute min(64, 60)=60
        a5 = _by_row(match_rows(ROWS, {"minecraft:cobblestone": 64}), 5)
        assert a5.action == "contribute"
        assert a5.qty == 60

    def test_progress_已done跳过(self):
        a6 = _by_row(match_rows(ROWS, {"minecraft:dirt": 99}), 6)
        assert a6.action == "skip"
        assert "备齐" in a6.reason

    def test_progress_背包没有此物跳过(self):
        rows = [{"id": 7, "item_name": "金锭", "registry_id": "minecraft:gold_ingot", "need_qty": 10, "delivered_qty": 0, "mode": 1, "status": "open"}]
        a7 = _by_row(match_rows(rows, {}), 7)
        assert a7.action == "skip"
        assert "没有此物" in a7.reason

    def test_匹配行各一个action(self):
        # row4 无 rid 被排除，其余 5 行各 1 个 action
        actions = match_rows(ROWS, {
            "minecraft:stone": 64,
            "minecraft:iron_ingot": 1,
            "minecraft:oak_planks": 1,
            "minecraft:cobblestone": 64,
            "minecraft:dirt": 1,
        })
        assert sorted(a.row_id for a in actions) == [1, 2, 3, 5, 6]

    def test_lock_need为0跳过(self):
        rows = [{"id": 8, "item_name": "x", "registry_id": "minecraft:stone", "need_qty": 0, "mode": 0, "status": "open"}]
        a8 = _by_row(match_rows(rows, {"minecraft:stone": 99}), 8)
        assert a8.action == "skip"
        assert "无需求" in a8.reason
