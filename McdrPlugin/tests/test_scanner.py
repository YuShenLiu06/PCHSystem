"""scanner.py 单测（纯 Python，不依赖 MCDR 运行时）。

通过 importlib 直接按文件路径加载 scanner.py，绕过 ``pch_system/__init__.py``
（后者会 import mcdreforged，测试环境无该依赖）。scanner 本身只依赖标准库。

覆盖范围（P3 薄壳化后）：
- 背包 / 手持扫描（``expand_items`` / ``scan_inventory`` / ``read_held_item``）—— 一键提交、
  ``addhand`` / ``setreg`` / ``addsub`` 共用；
- 回执折叠谓词（``skip_is_noise`` / ``skip_is_ready``）—— 取基元参数，与后端
  ``batch_submit`` reason 字面量对齐（``REASON_NO_ITEM`` / ``REASON_READY``）。

行级决策（match_rows）已移交后端 ``sheet_repo.batch_submit`` 单一权威实现，本端不再复刻。
"""
import importlib.util
from collections import Counter
from pathlib import Path

# 按文件路径加载 scanner.py 为独立模块
_SPEC = importlib.util.spec_from_file_location(
    "_scanner_under_test",
    Path(__file__).resolve().parent.parent / "pch_system" / "scanner.py",
)
scanner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scanner)

expand_items = scanner.expand_items
_extract_nested_items = scanner._extract_nested_items
scan_inventory = scanner.scan_inventory
read_held_item = scanner.read_held_item
skip_is_noise = scanner.skip_is_noise
skip_is_ready = scanner.skip_is_ready
REASON_NO_ITEM = scanner.REASON_NO_ITEM
REASON_READY = scanner.REASON_READY


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


# ---- 回执折叠谓词（取基元参数；调用方从后端 BatchRowOutcome 抽字段传入）----
# reason 字面量与后端 sheet_repo.batch_submit 逐字对齐：
#   BATCH_REASON_READY = "已备齐"（lock done / progress done 或 delivered>=need）
#   BATCH_REASON_NO_ITEM = "背包没有此物"（progress 未提交此物）
# 后端新增的「行状态变化」「行已删除」属 neither-ready-nor-noise，逐行展示。


class TestSkipIsNoise:
    """skip_is_noise：skip 行是否与本人无关 → 回执折叠（不逐行展示）。

    - lock 行非本人认领（is_claimant=False）→ 折叠；
    - progress 行未携带（reason=REASON_NO_ITEM）→ 折叠；
    其余（本人认领的 lock 未完成、progress 已备齐 / 无需求 / 状态变化）→ 逐行展示。
    """

    def test_lock_非本人认领_折叠(self):
        # 他人认领 / 需先认领 → is_claimant=False
        assert skip_is_noise(mode=0, is_claimant=False, reason="已被他人认领") is True
        assert skip_is_noise(mode=0, is_claimant=False, reason="需先认领") is True

    def test_lock_本人认领_不折叠(self):
        # 本人认领但数量不足 → 逐行展示（提示玩家补货）
        assert skip_is_noise(mode=0, is_claimant=True, reason="数量不足（0/10）") is False

    def test_progress_未携带_折叠(self):
        assert skip_is_noise(mode=1, is_claimant=False, reason=REASON_NO_ITEM) is True

    def test_progress_已备齐_不折叠(self):
        # 已备齐归 ready 桶（skip_is_ready），非 noise
        assert skip_is_noise(mode=1, is_claimant=False, reason=REASON_READY) is False

    def test_progress_无需求_不折叠(self):
        assert skip_is_noise(mode=1, is_claimant=False, reason="无需求") is False

    def test_progress_状态变化_不折叠(self):
        # 后端新增 reason：行状态变化 / 行已删除 → 逐行展示（异常需玩家感知）
        assert skip_is_noise(mode=1, is_claimant=False, reason="行状态变化") is False
        assert skip_is_noise(mode=0, is_claimant=True, reason="行状态变化") is False
        assert skip_is_noise(mode=1, is_claimant=False, reason="行已删除") is False


class TestSkipIsReady:
    """skip_is_ready：skip 行是否已备齐 / 进度已满 → 回执折叠。"""

    def test_ready_reason_折叠(self):
        assert skip_is_ready(REASON_READY) is True

    def test_其它reason_不折叠(self):
        assert skip_is_ready("需先认领") is False
        assert skip_is_ready("已被他人认领") is False
        assert skip_is_ready(REASON_NO_ITEM) is False
        assert skip_is_ready("数量不足（0/10）") is False
        assert skip_is_ready("无需求") is False
        assert skip_is_ready("不满足上交条件") is False
        assert skip_is_ready("行状态变化") is False
        assert skip_is_ready("") is False

    def test_常量值对齐后端(self):
        """REASON_READY / REASON_NO_ITEM 与后端 BATCH_REASON_* 字面量逐字一致。"""
        assert REASON_READY == "已备齐"
        assert REASON_NO_ITEM == "背包没有此物"


def test_reason_常量与后端_batch_reason_逐字对齐():
    """契约测试：前端 scanner.REASON_* 与后端 sheet_repo.BATCH_REASON_* 必须逐字相等。

    折叠判定是字符串硬等（``reason == REASON_READY``），任一端单改字面量会让回执
    静默退化为逐行刷屏（已备齐行不再折叠）。两端是独立 Python 包不能共享常量，
    故用文本正则抓后端字面量断言对齐。漂移时本测即红。
    """
    import re
    repo_path = Path(__file__).resolve().parents[2] / "Backend" / "app" / "repositories" / "sheet_repo.py"
    text = repo_path.read_text(encoding="utf-8")
    be_ready = re.search(r'BATCH_REASON_READY\s*=\s*"([^"]+)"', text)
    be_no_item = re.search(r'BATCH_REASON_NO_ITEM\s*=\s*"([^"]+)"', text)
    assert be_ready is not None, "后端 BATCH_REASON_READY 常量缺失（重命名了？）"
    assert be_no_item is not None, "后端 BATCH_REASON_NO_ITEM 常量缺失（重命名了？）"
    assert be_ready.group(1) == REASON_READY
    assert be_no_item.group(1) == REASON_NO_ITEM
