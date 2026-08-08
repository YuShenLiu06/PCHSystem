"""construction_tracker 单测。

验证 _flush_once 的幂等策略：
- 首见建基（不报历史放置）
- 2xx 推进 baseline（不重复上报）
- 失败 / 哨兵不推进（下轮重试，不丢不翻倍）
- 多项目（heuristic_eligible=False）跳过上报但推进 baseline（丢弃增量）
- 分块上报：成功块推进、失败块不推进

mock 四处：``notifier._snapshot_online`` / ``stats_reader.read_stats_file`` /
``construction_client.get_active_sheets`` / ``construction_client.report_placements``。
不 mock 真实文件系统 —— 用 ``tempfile.TemporaryDirectory`` 提供 ``world_stats_dir``。
"""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.construction_tracker as ct  # noqa: E402
from pch_system.config import PchSystemConfig  # noqa: E402

UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "22222222-3333-4444-5555-666666666666"


def _doc(used: dict) -> dict:
    """构造 stats json dict（``used_counts`` 会从 ``stats.minecraft:used`` 取）。"""
    return {"stats": {"minecraft:used": dict(used)}}


def _active_ok() -> dict:
    """单 constructing 项目，heuristic_eligible=True。"""
    return {"sheets": [{"id": 1}], "heuristic_eligible": True}


def _active_multi() -> dict:
    """两个 constructing 项目，heuristic_eligible=False（无启发式归因）。"""
    return {"sheets": [{"id": 1}, {"id": 2}], "heuristic_eligible": False}


class FlushOnceTest(unittest.TestCase):
    """_flush_once 单轮逻辑 + 幂等策略。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = PchSystemConfig()
        self.cfg.world_stats_dir = self._tmp.name
        self.cfg.http_retries = 0
        ct._reset()
        ct.configure(self.cfg)

    def tearDown(self):
        self._tmp.cleanup()

    # --- 1. disabled ---

    def test_disabled(self):
        """construction_enabled=False → outcome=disabled，不动 baseline。"""
        self.cfg.construction_enabled = False
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value={}):
            result = ct._flush_once(self.cfg)
        self.assertEqual(result["last_outcome"], "disabled")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["baselined_players"], 0)

    # --- 2. stats_dir_missing ---

    def test_stats_dir_missing(self):
        """world_stats_dir 指向不存在路径 → outcome=stats_dir_missing。"""
        self.cfg.world_stats_dir = "/definitely/does/not/exist/__pch_test__"
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value={"A": UUID_A}):
            result = ct._flush_once(self.cfg)
        self.assertEqual(result["last_outcome"], "stats_dir_missing")
        self.assertFalse(result["stats_dir_ok"])
        self.assertEqual(result["online"], 1)

    # --- 3. no_online ---

    def test_no_online(self):
        """在线字典空 → outcome=no_online。"""
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value={}):
            result = ct._flush_once(self.cfg)
        self.assertEqual(result["last_outcome"], "no_online")
        self.assertEqual(result["online"], 0)
        self.assertEqual(result["baselined_players"], 0)

    # --- 4. fetch_failed + 第二轮 baseline 不变 ---

    def test_fetch_failed_baseline_holds(self):
        """active-sheets 拉取失败 → fetch_failed；baseline 不推进（下轮重试）。"""
        online = {"Alice": UUID_A}

        # 第一轮：active 正常，首见建基 stone:5
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            r1 = ct._flush_once(self.cfg)
        self.assertEqual(r1["last_outcome"], "no_placements")
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})

        # 第二轮：stats 进化到 stone:8，active 返 None → baseline 不变
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=None):
            r2 = ct._flush_once(self.cfg)
        self.assertEqual(r2["last_outcome"], "fetch_failed")
        self.assertEqual(r2["last_error"], "network")
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})

    # --- 5. 首见建基 ---

    def test_first_sight_baseline(self):
        """玩家首见 stats{stone:5} → 当轮不报（placements 空），baseline 已建为 5。"""
        online = {"Alice": UUID_A}
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements") as rp:
            result = ct._flush_once(self.cfg)
        self.assertEqual(result["last_outcome"], "no_placements")
        rp.assert_not_called()  # 首见不报
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})

    # --- 6. 正常增量 + 推进 ---

    def test_normal_delta_advance(self):
        """首见 stone:5 → 第二轮 stone:8（delta 3），report 2xx → baseline 推进到 8。"""
        online = {"Alice": UUID_A}

        # 第一轮：首见建基
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        # 第二轮：delta=3
        captured = {}

        def _capture(_cfg, p, sheet_id=None):
            captured["placements"] = list(p)
            return {"totals": {"accepted": 1, "skipped": 0}, "outcomes": []}

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_capture):
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "ok")
        self.assertEqual(result["last_accepted"], 1)
        self.assertEqual(result["last_skipped"], 0)
        self.assertEqual(result["last_reported"], 1)
        # placements 形态
        self.assertEqual(captured["placements"], [
            {"player_uuid": UUID_A, "registry_id": "minecraft:stone",
             "placed_qty": 3, "broken_qty": 0},
        ])
        # baseline 推进到 8
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})

    # --- 7. 失败不推进 + 第三轮成功不翻倍（幂等核心）---

    def test_failure_then_retry_no_doubling(self):
        """失败下轮重试：delta 不翻倍，只补计一次。"""
        online = {"Alice": UUID_A}

        # 第一轮：首见 stone:5
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        # 第二轮：delta=3，report 返 None（网络失败）→ baseline 不变
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements",
                                return_value=None):
            r2 = ct._flush_once(self.cfg)
        self.assertEqual(r2["last_outcome"], "report_failed")
        self.assertEqual(r2["last_error"], "network")
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})

        # 第三轮：stats 仍 stone:8（delta 重算=3），report 200 → 推进到 8（不翻倍）
        captured3 = []

        def _cap(_cfg, p, sheet_id=None):
            captured3.append(list(p))
            return {"totals": {"accepted": 1, "skipped": 0}, "outcomes": []}

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            r3 = ct._flush_once(self.cfg)
        self.assertEqual(r3["last_outcome"], "ok")
        # 仅补计一次 delta=3（不翻倍）
        self.assertEqual(captured3[0], [
            {"player_uuid": UUID_A, "registry_id": "minecraft:stone",
             "placed_qty": 3, "broken_qty": 0},
        ])
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})

    # --- 8. 多项目跳过推进 ---

    def test_skipped_no_attribution_advances(self):
        """heuristic_eligible=False → 跳过上报，但推进 baseline（丢弃 delta）。"""
        online = {"Alice": UUID_A}

        # 第一轮：首见
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        # 第二轮：heuristic_eligible=False → 不上报但推进
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "report_placements") as rp:
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "skipped_no_attribution")
        self.assertEqual(result["last_reported"], 1)
        self.assertEqual(result["heuristic_eligible"], False)
        rp.assert_not_called()
        # baseline 推进到 8（丢弃 delta，不堆积）
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})

    # --- 9. rate_limited 不推进 ---

    def test_rate_limited_holds(self):
        """report 返 RATE_LIMITED 哨兵 → outcome=rate_limited，baseline 不变。"""
        online = {"Alice": UUID_A}

        # 第一轮：首见
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        # 第二轮：rate_limited → baseline 不变
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements",
                                return_value=ct.construction_client.RATE_LIMITED):
            result = ct._flush_once(self.cfg)
        self.assertEqual(result["last_outcome"], "rate_limited")
        self.assertIsNone(result["last_error"])  # 哨兵 outcome 已透出，error 不重复
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})

    # --- 10. 分块 ---

    def test_chunked_partial_advance(self):
        """max_batch=2，4 条 → 2 块；第 1 块 200 推进 Alice，第 2 块 None 不推进 Bob。"""
        self.cfg.construction_max_batch = 2
        online = {"Alice": UUID_A, "Bob": UUID_B}

        # 第一轮：两个玩家都首见
        docs1 = [
            _doc({"minecraft:stone": 5, "minecraft:dirt": 3}),           # Alice
            _doc({"minecraft:cobblestone": 2, "minecraft:oak_log": 1}),  # Bob
        ]
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file", side_effect=docs1), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        # 第二轮：Alice 2 条 + Bob 2 条 = 4 条 placements → 2 块（每块 2 条）
        # dict 插入顺序保证 placements 顺序：Alice-stone, Alice-dirt, Bob-cobblestone, Bob-oak_log
        docs2 = [
            _doc({"minecraft:stone": 8, "minecraft:dirt": 6}),           # Alice +3, +3
            _doc({"minecraft:cobblestone": 5, "minecraft:oak_log": 4}),  # Bob +3, +3
        ]
        calls = []

        def _cap(_cfg, p, sheet_id=None):
            calls.append(list(p))
            if len(calls) == 1:
                return {"totals": {"accepted": 2, "skipped": 0}, "outcomes": []}  # 第 1 块 OK
            return None  # 第 2 块网络失败

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file", side_effect=docs2), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            result = ct._flush_once(self.cfg)

        self.assertEqual(len(calls), 2, "should call report_placements twice")
        self.assertEqual(result["last_outcome"], "report_failed")
        self.assertEqual(result["last_accepted"], 2)  # 仅第 1 块
        # 第 1 块 = [Alice-stone, Alice-dirt] → 推进
        self.assertEqual(ct._baselines[UUID_A],
                          {"minecraft:stone": 8, "minecraft:dirt": 6})
        # 第 2 块 = [Bob-cobblestone, Bob-oak_log] → 不推进，仍是首轮 baseline
        self.assertEqual(ct._baselines[UUID_B],
                          {"minecraft:cobblestone": 2, "minecraft:oak_log": 1})

    # --- 11. get_status 契约 ---

    def test_get_status_contract(self):
        """flush 后 get_status() 返回含全部契约键的 dict；从未 flush 返回 {}。"""
        online = {"Alice": UUID_A}

        # 从未 flush
        self.assertEqual(ct.get_status(), {})

        # 一次 flush
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 5})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={}), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        status = ct.get_status()
        expected_keys = {
            "enabled", "stats_dir", "stats_dir_ok",
            "online", "active_sheets", "heuristic_eligible",
            "flush_interval", "last_at", "last_outcome",
            "last_reported", "last_accepted", "last_skipped",
            "last_error", "baselined_players",
        }
        self.assertEqual(set(status.keys()), expected_keys)
        self.assertEqual(status["enabled"], True)
        self.assertEqual(status["stats_dir"], self._tmp.name)
        self.assertEqual(status["stats_dir_ok"], True)
        self.assertEqual(status["online"], 1)
        self.assertEqual(status["active_sheets"], 1)
        self.assertEqual(status["heuristic_eligible"], True)
        self.assertEqual(status["flush_interval"], 30.0)  # PchSystemConfig 默认
        self.assertEqual(status["last_outcome"], "no_placements")
        self.assertEqual(status["baselined_players"], 1)


class FlushOnceRoutingTest(unittest.TestCase):
    """v0.10.0 按玩家路由三态测试：
    1. 已加入显式 sheet_id（lookup_active_by_uuids 命中）→ 桶到该 sheet_id；
    2. 未加入 + 恰 1 个 constructing（heuristic_eligible=True）→ fallback 桶到该 sheet；
    3. 未加入 + 0/>1 个 constructing（heuristic_eligible=False）→ skip 推进 baseline；
    4. lookup 网络失败 → mappings={} 降级（fallback/skip-all 仍按 heuristic 决定）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = PchSystemConfig()
        self.cfg.world_stats_dir = self._tmp.name
        self.cfg.http_retries = 0
        ct._reset()
        ct.configure(self.cfg)

    def tearDown(self):
        self._tmp.cleanup()

    def _prime_baseline(self, online, doc_a, active=None, mappings=None):
        """首轮首见建基。active/mappings 默认空（首见不报，参数无关紧要）。"""
        if active is None:
            active = _active_ok()
        if mappings is None:
            mappings = {}
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file", side_effect=[doc_a]), \
             mock.patch.object(ct.construction_client, "get_active_sheets", return_value=active), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids", return_value=mappings), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

    # --- 1. 显式 sheet_id 路由 ---

    def test_显式sheet_id路由_桶到指定sheet(self):
        """玩家已 join sheet 7（lookup 命中）→ placements 桶到 sheet_id=7，不走 fallback。"""
        online = {"Alice": UUID_A}
        # 首见建基
        self._prime_baseline(online, _doc({"minecraft:stone": 5}))

        captured = []  # 每次调 report_placements 的 (sheet_id, placements)

        def _cap(_cfg, p, sheet_id=None):
            captured.append((sheet_id, list(p)))
            return {"totals": {"accepted": 1, "skipped": 0}, "outcomes": []}

        # 第二轮：lookup 命中 UUID_A → sheet_id=7
        # active 设为「2 个 constructing」证明不走 heuristic fallback
        active_two = {"sheets": [{"id": 7}, {"id": 9}], "heuristic_eligible": False}
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=active_two), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={UUID_A: 7}), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "ok")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], 7)  # sheet_id=7（显式）
        # placements 形态
        self.assertEqual(captured[0][1], [
            {"player_uuid": UUID_A, "registry_id": "minecraft:stone",
             "placed_qty": 3, "broken_qty": 0},
        ])
        # baseline 推进到 8
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})

    def test_多玩家不同sheet_id_各桶分块(self):
        """Alice→sheet 7、Bob→sheet 9 → 两次 report_placements 各带一个 sheet_id。"""
        # 分两步首见建基（每次仅一玩家在线，避免 side_effect 长度匹配问题）
        self._prime_baseline({"Alice": UUID_A}, _doc({"minecraft:stone": 5}))
        self._prime_baseline({"Bob": UUID_B}, _doc({"minecraft:dirt": 2}))
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 5})
        self.assertEqual(ct._baselines[UUID_B], {"minecraft:dirt": 2})

        # 第二轮：双在线 + Alice→7，Bob→9，多 constructing（验显式覆盖 fallback）
        online = {"Alice": UUID_A, "Bob": UUID_B}
        captured = []

        def _cap(_cfg, p, sheet_id=None):
            captured.append((sheet_id, list(p)))
            return {"totals": {"accepted": len(p), "skipped": 0}, "outcomes": []}

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[
                                    _doc({"minecraft:stone": 8}),    # Alice +3
                                    _doc({"minecraft:dirt": 6}),     # Bob +4
                                ]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={UUID_A: 7, UUID_B: 9}), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            ct._flush_once(self.cfg)

        # 按 sheet_id 排序后分别桶到 7 / 9
        self.assertEqual(sorted(c[0] for c in captured), [7, 9])
        # 每个 chunk 一条 placement
        sid_to_count = {sid: len(p) for sid, p in captured}
        self.assertEqual(sid_to_count, {7: 1, 9: 1})
        # 两玩家 baseline 都推进
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})
        self.assertEqual(ct._baselines[UUID_B], {"minecraft:dirt": 6})

    # --- 2. 启发式 fallback：未 join + 恰 1 个 constructing ---

    def test_启发式fallback_未join单constructing_桶到唯一sheet(self):
        """lookup 未命中 + 恰 1 个 constructing → 桶到该唯一 sheet（过渡期兼容）。"""
        online = {"Alice": UUID_A}
        self._prime_baseline(online, _doc({"minecraft:stone": 5}))

        captured = []

        def _cap(_cfg, p, sheet_id=None):
            captured.append((sheet_id, list(p)))
            return {"totals": {"accepted": 1, "skipped": 0}, "outcomes": []}

        # lookup 返回空 mappings（玩家未 join）；active-sheet 单 constructing
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 9})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={}), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "ok")
        self.assertEqual(captured, [(1, [  # sheets[0].id=1，沿用 _active_ok() 的 id
            {"player_uuid": UUID_A, "registry_id": "minecraft:stone",
             "placed_qty": 4, "broken_qty": 0},
        ])])
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 9})

    # --- 3. 无显式 + heuristic_ineligible → skip 但推进 baseline ---

    def test_无显式多constructing_skip推进baseline(self):
        """未 join + 多 constructing → skip 上报，但推进 baseline（C-7 不堆积）。"""
        online = {"Alice": UUID_A}
        self._prime_baseline(online, _doc({"minecraft:stone": 5}))

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 12})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={}), \
             mock.patch.object(ct.construction_client, "report_placements") as rp:
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "skipped_no_attribution")
        self.assertEqual(result["last_reported"], 1)  # 1 条增量被丢弃
        rp.assert_not_called()
        # baseline 推进到 12（不堆积）
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 12})

    def test_混合_有显式和无显式_分桶skip共存(self):
        """Alice 已 join sheet 7（多 constructing 仍能上报）；Bob 未 join → skip 推进。

        验证路由按玩家独立判定，不因整轮 heuristic_ineligible 全 skip。
        """
        online = {"Alice": UUID_A, "Bob": UUID_B}
        # 首见建基
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[
                                    _doc({"minecraft:stone": 5}),    # Alice 首见
                                    _doc({"minecraft:dirt": 2}),     # Bob 首见
                                ]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={}), \
             mock.patch.object(ct.construction_client, "report_placements"):
            ct._flush_once(self.cfg)

        captured = []

        def _cap(_cfg, p, sheet_id=None):
            captured.append((sheet_id, list(p)))
            return {"totals": {"accepted": len(p), "skipped": 0}, "outcomes": []}

        # 第二轮：Alice join 7（显式），Bob 未 join（多 constructing skip）
        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[
                                    _doc({"minecraft:stone": 8}),    # Alice +3
                                    _doc({"minecraft:dirt": 9}),     # Bob +7
                                ]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value={UUID_A: 7}), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "ok")  # Alice 上报成功
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], 7)
        # 两玩家 baseline 都推进（Alice 2xx 推进，Bob skip 推进）
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 8})
        self.assertEqual(ct._baselines[UUID_B], {"minecraft:dirt": 9})

    # --- 4. lookup 网络失败降级 ---

    def test_lookup失败降级_mappings空但heuristic仍可用(self):
        """lookup 返 None（网络）→ mappings={}；单 constructing → fallback 路径仍上报。"""
        online = {"Alice": UUID_A}
        self._prime_baseline(online, _doc({"minecraft:stone": 5}))

        captured = []

        def _cap(_cfg, p, sheet_id=None):
            captured.append((sheet_id, list(p)))
            return {"totals": {"accepted": 1, "skipped": 0}, "outcomes": []}

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 8})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_ok()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value=None), \
             mock.patch.object(ct.construction_client, "report_placements",
                                side_effect=_cap):
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "ok")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], 1)  # fallback 到 sheets[0].id=1

    def test_lookup失败降级_多constructing_全skip推进(self):
        """lookup 失败 + 多 constructing → 全 skip 但推进 baseline。"""
        online = {"Alice": UUID_A}
        self._prime_baseline(online, _doc({"minecraft:stone": 5}))

        with mock.patch.object(ct.notifier, "_snapshot_online", return_value=online), \
             mock.patch.object(ct.stats_reader, "read_stats_file",
                                side_effect=[_doc({"minecraft:stone": 11})]), \
             mock.patch.object(ct.construction_client, "get_active_sheets",
                                return_value=_active_multi()), \
             mock.patch.object(ct.construction_client, "lookup_active_by_uuids",
                                return_value=ct.construction_client.RATE_LIMITED), \
             mock.patch.object(ct.construction_client, "report_placements") as rp:
            result = ct._flush_once(self.cfg)

        self.assertEqual(result["last_outcome"], "skipped_no_attribution")
        rp.assert_not_called()
        self.assertEqual(ct._baselines[UUID_A], {"minecraft:stone": 11})


class RunLoopTest(unittest.TestCase):
    """run() 循环骨架的轻量验证（_flush_once 已由 FlushOnceTest 覆盖）。"""

    def setUp(self):
        ct._reset()

    def test_run_exits_immediately_on_stop_event(self):
        """stop_event 预先 set → run 立即退出，不调 _flush_once。"""
        cfg = PchSystemConfig()
        cfg.construction_flush_interval_seconds = 5.0
        server = mock.Mock()
        stop = threading.Event()
        stop.set()
        with mock.patch.object(ct, "_flush_once") as flush:
            ct.run(server, cfg, stop)
        flush.assert_not_called()

    def test_run_warns_when_stats_dir_missing(self):
        """启动时 world_stats_dir 不是目录 → server.logger.warning 被调一次。"""
        cfg = PchSystemConfig()
        cfg.world_stats_dir = "/definitely/not/exist/__pch_run_test__"
        cfg.construction_flush_interval_seconds = 5.0
        server = mock.Mock()
        stop = threading.Event()
        stop.set()
        ct.run(server, cfg, stop)
        server.logger.warning.assert_called_once()

    def test_run_skips_flush_when_server_not_running(self):
        """server.is_server_running() False → continue，不调 _flush_once。"""
        cfg = PchSystemConfig()
        cfg.construction_flush_interval_seconds = 5.0
        server = mock.Mock()
        server.is_server_running.return_value = False
        stop = threading.Event()

        # 模拟 stop.wait：第 1 次返回 False（继续循环），第 2 次返回 True（退出）
        call_count = {"n": 0}

        def _fake_wait(timeout):
            call_count["n"] += 1
            return call_count["n"] >= 2

        stop.wait = _fake_wait
        with mock.patch.object(ct, "_flush_once") as flush:
            ct.run(server, cfg, stop)
        flush.assert_not_called()


class RunBackoffTest(unittest.TestCase):
    """run() 退避状态机：连续网络失败 → 指数增大间隔 + logger 提示；恢复 → 重置。"""

    def setUp(self):
        ct._reset()

    def test_backoff_on_network_failures_and_recovery(self):
        cfg = PchSystemConfig()
        cfg.construction_flush_interval_seconds = 5.0
        cfg.backoff_max_seconds = 60.0
        server = mock.Mock()
        server.is_server_running.return_value = True

        flush_results = [
            {"last_outcome": "fetch_failed"},
            {"last_outcome": "fetch_failed"},
            {"last_outcome": "ok"},
        ]

        call_count = {"n": 0}
        intervals = []

        def _fake_wait(timeout):
            intervals.append(timeout)
            call_count["n"] += 1
            return call_count["n"] >= 4

        stop = threading.Event()
        stop.wait = _fake_wait

        with mock.patch.object(ct, "_flush_once", side_effect=flush_results):
            ct.run(server, cfg, stop)

        # 进入退避时 warning（含「后端不可达」）
        warning_calls = [
            c for c in server.logger.warning.call_args_list
            if "后端不可达" in str(c)
        ]
        self.assertEqual(len(warning_calls), 1)

        # 恢复时 info（含「恢复」）
        info_calls = [
            c for c in server.logger.info.call_args_list
            if "恢复" in str(c)
        ]
        self.assertEqual(len(info_calls), 1)

        # 验证间隔递增：5.0(base) → 10.0(2^1) → 20.0(2^2) → 5.0(recovered)
        self.assertAlmostEqual(intervals[0], 5.0)
        self.assertAlmostEqual(intervals[1], 10.0)
        self.assertAlmostEqual(intervals[2], 20.0)
        self.assertAlmostEqual(intervals[3], 5.0)

    def test_non_network_outcome_does_not_trigger_backoff(self):
        """outcome=no_online → 非网络失败，不触发退避日志。"""
        cfg = PchSystemConfig()
        cfg.construction_flush_interval_seconds = 5.0
        server = mock.Mock()
        server.is_server_running.return_value = True

        call_count = {"n": 0}

        def _fake_wait(timeout):
            call_count["n"] += 1
            return call_count["n"] >= 2

        stop = threading.Event()
        stop.wait = _fake_wait

        with mock.patch.object(ct, "_flush_once", return_value={"last_outcome": "no_online"}):
            ct.run(server, cfg, stop)

        backoff_warnings = [
            c for c in server.logger.warning.call_args_list
            if "后端不可达" in str(c)
        ]
        self.assertEqual(len(backoff_warnings), 0)


if __name__ == "__main__":
    unittest.main()
