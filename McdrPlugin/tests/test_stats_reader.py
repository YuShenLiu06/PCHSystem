"""stats_reader.py 单测（纯 Python，不依赖 MCDR 运行时）。

通过 importlib 直接按文件路径加载 stats_reader.py，绕过 ``pch_system/__init__.py``
（后者会 import mcdreforged，测试环境无该依赖）。stats_reader 本身只依赖标准库。

覆盖：
- ``stats_path_for``：目录 + uuid 拼接；
- ``read_stats_file``：正常 / 缺文件 / JSON 非法 / 非 dict；
- ``used_counts`` / ``mined_counts``：类别提取 + 防御性导航 + 非 dict 兜底；
- ``diff_counts``：增量计算、首见全计、负/零不计、非数值跳过。
"""
import importlib.util
import json
from pathlib import Path

# 按文件路径加载 stats_reader.py 为独立模块
_SPEC = importlib.util.spec_from_file_location(
    "_stats_reader_under_test",
    Path(__file__).resolve().parent.parent / "pch_system" / "stats_reader.py",
)
stats_reader = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stats_reader)

stats_path_for = stats_reader.stats_path_for
read_stats_file = stats_reader.read_stats_file
used_counts = stats_reader.used_counts
mined_counts = stats_reader.mined_counts
diff_counts = stats_reader.diff_counts
USED_KEY = stats_reader.USED_KEY
MINED_KEY = stats_reader.MINED_KEY


class TestStatsPathFor:
    def test_拼接目录与uuid文件名(self):
        p = stats_path_for("world/stats", "11111111-2222-3333-4444-555555555555")
        assert p == Path("world/stats") / "11111111-2222-3333-4444-555555555555.json"

    def test_接受path对象目录(self):
        p = stats_path_for(Path("/srv/world/stats"), "abc")
        assert str(p) == "/srv/world/stats/abc.json"


class TestReadStatsFile:
    def test_正常读取(self, tmp_path):
        f = tmp_path / "stats.json"
        f.write_text(json.dumps({"stats": {"minecraft:used": {"minecraft:stone": 3}}}), encoding="utf-8")
        doc = read_stats_file(f)
        assert doc == {"stats": {"minecraft:used": {"minecraft:stone": 3}}}

    def test_文件缺失返回None(self, tmp_path):
        assert read_stats_file(tmp_path / "nope.json") is None

    def test_json非法返回None(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        assert read_stats_file(f) is None

    def test_非dict顶层返回None(self, tmp_path):
        f = tmp_path / "arr.json"
        f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert read_stats_file(f) is None

    def test_接受字符串路径(self, tmp_path):
        f = tmp_path / "stats.json"
        f.write_text(json.dumps({"stats": {}}), encoding="utf-8")
        assert read_stats_file(str(f)) == {"stats": {}}


# 真实 stats 文件样例（含 used + mined 两个类别 + DataVersion）
SAMPLE_DOC = {
    "stats": {
        "minecraft:used": {"minecraft:stone": 10, "minecraft:oak_planks": 4},
        "minecraft:mined": {"minecraft:stone": 3},
    },
    "DataVersion": 2586,
}


class TestUsedCounts:
    def test_提取used类别(self):
        assert used_counts(SAMPLE_DOC) == {"minecraft:stone": 10, "minecraft:oak_planks": 4}

    def test_缺失used返回空(self):
        assert used_counts({"stats": {"minecraft:mined": {"minecraft:stone": 1}}}) == {}

    def test_无外层stats包衰也兼容(self):
        # 防御：少数变种可能直接顶层放类别键
        assert used_counts({"minecraft:used": {"minecraft:dirt": 2}}) == {"minecraft:dirt": 2}

    def test_非dict入参返回空(self):
        assert used_counts(None) == {}
        assert used_counts("x") == {}

    def test_非数值条目跳过(self):
        doc = {"stats": {"minecraft:used": {"minecraft:stone": "abc", "minecraft:dirt": 5}}}
        assert used_counts(doc) == {"minecraft:dirt": 5}


class TestMinedCounts:
    def test_提取mined类别(self):
        assert mined_counts(SAMPLE_DOC) == {"minecraft:stone": 3}

    def test_键独立于used(self):
        assert MINED_KEY == "minecraft:mined"
        assert USED_KEY == "minecraft:used"


class TestDiffCounts:
    def test_正常增量(self):
        cur = {"minecraft:stone": 10, "minecraft:dirt": 5}
        base = {"minecraft:stone": 7, "minecraft:dirt": 5}
        # stone +3, dirt 0（不计）
        assert diff_counts(cur, base) == {"minecraft:stone": 3}

    def test_新物品全额计入(self):
        # baseline 无该键 → 视作 0，全额计入
        cur = {"minecraft:stone": 8}
        assert diff_counts(cur, {}) == {"minecraft:stone": 8}

    def test_零增量不计(self):
        cur = {"minecraft:stone": 5}
        assert diff_counts(cur, {"minecraft:stone": 5}) == {}

    def test_负增量不计(self):
        # 计数回退（理论不会发生，但防御：不计）
        cur = {"minecraft:stone": 3}
        assert diff_counts(cur, {"minecraft:stone": 5}) == {}

    def test_空current返回空(self):
        assert diff_counts({}, {"minecraft:stone": 5}) == {}

    def test_非数值跳过(self):
        cur = {"minecraft:stone": "x", "minecraft:dirt": 4}
        assert diff_counts(cur, {}) == {"minecraft:dirt": 4}

    def test_基线非数值视作0(self):
        cur = {"minecraft:stone": 5}
        # baseline 里 stone 是非数值 → 视作 0
        assert diff_counts(cur, {"minecraft:stone": "bad"}) == {"minecraft:stone": 5}
