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
skip_is_noise = scanner.skip_is_noise
REASON_NO_ITEM = scanner.REASON_NO_ITEM


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
# lock 行需带 claimant_uuid 用于判定 is_claimant（与后端 _row_dict 字段一致）
ROWS = [
    {"id": 1, "item_name": "石头", "registry_id": "minecraft:stone", "need_qty": 64, "delivered_qty": 0, "mode": 0, "status": "open", "claimant_uuid": None},
    {"id": 2, "item_name": "铁锭", "registry_id": "minecraft:iron_ingot", "need_qty": 32, "delivered_qty": 0, "mode": 0, "status": "claimed", "claimant_uuid": "uuid-A"},
    {"id": 3, "item_name": "橡木板", "registry_id": "minecraft:oak_planks", "need_qty": 128, "delivered_qty": 0, "mode": 0, "status": "claimed", "claimant_uuid": "uuid-A"},
    {"id": 4, "item_name": "无注册名", "registry_id": None, "need_qty": 10, "mode": 0, "status": "open"},
    {"id": 5, "item_name": "圆石", "registry_id": "minecraft:cobblestone", "need_qty": 100, "delivered_qty": 40, "mode": 1, "status": "claimed"},
    {"id": 6, "item_name": "泥土", "registry_id": "minecraft:dirt", "need_qty": 10, "delivered_qty": 10, "mode": 1, "status": "done"},
]


def _by_row(actions, row_id):
    return next(a for a in actions if a.row_id == row_id)


class TestMatchRows:
    def test_lock_open_未认领_skip(self):
        # lock open 行：必须先手动认领，不进入一键提交
        a1 = _by_row(match_rows(ROWS, {"minecraft:stone": 64}, player_uuid="uuid-A"), 1)
        assert a1.action == "skip"
        assert "需先认领" in a1.reason

    def test_lock_claimed_自己认领_够数_deliver(self):
        # lock claimed + 自己是认领人 + have>=need → 直接 deliver（不再 claim）
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-A"), 2)
        assert a2.action == "deliver"
        assert a2.qty == 32

    def test_lock_claimed_他人认领_skip(self):
        # lock claimed 但自己是其他玩家 → skip
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-B"), 2)
        assert a2.action == "skip"
        assert "已被他人认领" in a2.reason

    def test_lock_claimed_自己认领_不够数_skip(self):
        # lock claimed + 自己认领但数量不足 → skip
        a3 = _by_row(match_rows(ROWS, {"minecraft:oak_planks": 64}, player_uuid="uuid-A"), 3)
        assert a3.action == "skip"
        assert "不足" in a3.reason

    def test_lock_未传player_uuid视为非认领人_skip(self):
        # 不传 player_uuid（默认空串）→ lock claimed 行也 skip
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}), 2)
        assert a2.action == "skip"
        assert "已被他人认领" in a2.reason

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
        }, player_uuid="uuid-A")
        assert sorted(a.row_id for a in actions) == [1, 2, 3, 5, 6]

    def test_lock_need为0跳过(self):
        rows = [{"id": 8, "item_name": "x", "registry_id": "minecraft:stone", "need_qty": 0, "mode": 0, "status": "claimed", "claimant_uuid": "uuid-A"}]
        a8 = _by_row(match_rows(rows, {"minecraft:stone": 99}, player_uuid="uuid-A"), 8)
        assert a8.action == "skip"
        assert "无需求" in a8.reason


class TestMatchRowsSubItems:
    """子物品匹配测试（issue #19）。

    子行（parent_row_id 非空）是普通行，按 registry_id 匹配，参与 submit。
    parent_row_id 不影响匹配逻辑（scanner.py 不改）。
    """

    def test_sub_row_lock_mode_自己认领_够数_deliver(self):
        """子行 lock 模式：自己认领 + have>=need → deliver。"""
        rows = [
            {"id": 10, "item_name": "铁棒父", "registry_id": "minecraft:iron_ingot", "need_qty": 64, "delivered_qty": 0, "mode": 0, "status": "open"},
            {"id": 11, "item_name": "铁棒子", "registry_id": "minecraft:iron_ingot", "parent_row_id": 10, "need_qty": 64, "delivered_qty": 0, "mode": 0, "status": "claimed", "claimant_uuid": "uuid-A"},
        ]
        actions = match_rows(rows, {"minecraft:iron_ingot": 64}, player_uuid="uuid-A")
        a11 = _by_row(actions, 11)
        assert a11.action == "deliver"
        assert a11.qty == 64

    def test_sub_row_progress_mode_未满_contribute(self):
        """子行 progress 模式：未满 → contribute。"""
        rows = [
            {"id": 20, "item_name": "圆石父", "registry_id": "minecraft:cobblestone", "need_qty": 100, "delivered_qty": 0, "mode": 1, "status": "open"},
            {"id": 21, "item_name": "圆石子", "registry_id": "minecraft:cobblestone", "parent_row_id": 20, "need_qty": 100, "delivered_qty": 40, "mode": 1, "status": "claimed"},
        ]
        actions = match_rows(rows, {"minecraft:cobblestone": 64})
        a21 = _by_row(actions, 21)
        assert a21.action == "contribute"
        assert a21.qty == 60  # min(64, 100-40)=60

    def test_sub_row_parent_row_id_不影响匹配(self):
        """parent_row_id 字段存在但不影响匹配逻辑。"""
        rows = [
            {"id": 30, "item_name": "木板父", "registry_id": "minecraft:oak_planks", "need_qty": 64, "delivered_qty": 0, "mode": 1, "status": "open"},
            {"id": 31, "item_name": "木板子", "registry_id": "minecraft:oak_planks", "parent_row_id": 30, "need_qty": 32, "delivered_qty": 0, "mode": 1, "status": "open"},
        ]
        # 父行和子行都按 registry_id 匹配
        actions = match_rows(rows, {"minecraft:oak_planks": 96})
        assert sorted(a.row_id for a in actions) == [30, 31]


class TestSkipIsNoise:
    """skip_is_noise 折叠判定测试。"""

    def test_lock_非本人认领_is_claimant_False_折叠(self):
        """lock 行非本人认领（他人认领 / 需先认领）→ 折叠。"""
        # 他人认领的 lock 行
        a = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-B"), 2)
        assert a.action == "skip"
        assert a.is_claimant is False
        assert skip_is_noise(a) is True

    def test_lock_本人认领_is_claimant_True_不折叠(self):
        """lock 行本人认领但数量不足 → 逐行展示（提示玩家去补货）。"""
        a = _by_row(match_rows(ROWS, {"minecraft:oak_planks": 64}, player_uuid="uuid-A"), 3)
        assert a.action == "skip"
        assert a.is_claimant is True
        assert skip_is_noise(a) is False

    def test_lock_open_非认领_is_claimant_False_折叠(self):
        """lock open 行（需先认领）非认领人 → 折叠。"""
        a = _by_row(match_rows(ROWS, {"minecraft:stone": 64}, player_uuid="uuid-A"), 1)
        assert a.action == "skip"
        assert a.is_claimant is False
        assert skip_is_noise(a) is True

    def test_progress_背包没有此物_折叠(self):
        """progress 行 reason=REASON_NO_ITEM → 折叠。"""
        rows = [{"id": 7, "item_name": "金锭", "registry_id": "minecraft:gold_ingot", "need_qty": 10, "delivered_qty": 0, "mode": 1, "status": "open"}]
        a = _by_row(match_rows(rows, {}), 7)
        assert a.action == "skip"
        assert a.reason == REASON_NO_ITEM
        assert skip_is_noise(a) is True

    def test_progress_已备齐_不折叠(self):
        """progress 行已备齐 → 逐行展示（已达成）。"""
        a6 = _by_row(match_rows(ROWS, {"minecraft:dirt": 99}), 6)
        assert a6.action == "skip"
        assert a6.reason == "已备齐"
        assert skip_is_noise(a6) is False

    def test_progress_无需求_不折叠(self):
        """progress 行无需求 → 逐行展示。"""
        rows = [{"id": 8, "item_name": "x", "registry_id": "minecraft:stone", "need_qty": 0, "delivered_qty": 0, "mode": 1, "status": "open"}]
        a = _by_row(match_rows(rows, {}), 8)
        assert a.action == "skip"
        assert a.reason == "无需求"
        assert skip_is_noise(a) is False

    def test_非skip_action_不折叠(self):
        """deliver / contribute action → 不折叠。"""
        # deliver
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-A"), 2)
        assert a2.action == "deliver"
        assert skip_is_noise(a2) is False

        # contribute
        a5 = _by_row(match_rows(ROWS, {"minecraft:cobblestone": 64}), 5)
        assert a5.action == "contribute"
        assert skip_is_noise(a5) is False


class TestMatchRowsIsClaimant:
    """match_rows is_claimant 字段透传测试。"""

    def test_lock_自己认领_is_claimant为True(self):
        """lock 行 claimant_uuid==player_uuid → is_claimant=True。"""
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-A"), 2)
        assert a2.is_claimant is True
        assert a2.action == "deliver"

    def test_lock_他人认领_is_claimant为False(self):
        """lock 行被他人认领 → is_claimant=False。"""
        a2 = _by_row(match_rows(ROWS, {"minecraft:iron_ingot": 99}, player_uuid="uuid-B"), 2)
        assert a2.is_claimant is False
        assert a2.action == "skip"

    def test_progress_is_claimant默认False(self):
        """progress 行的 is_claimant 默认 False（用不到）。"""
        a5 = _by_row(match_rows(ROWS, {"minecraft:cobblestone": 64}), 5)
        assert a5.is_claimant is False
        assert a5.action == "contribute"
