"""notifier 单测：在线玩家字典维护、rcon 'list' 解析、deliver_for_player 投递+ack。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402

import htcmc_auth.notifier as notifier  # noqa: E402
from htcmc_auth.config import HtcmcAuthConfig  # noqa: E402


def _cfg():
    c = HtcmcAuthConfig()
    c.notify_poll_interval_seconds = 1.0
    c.notify_max_per_poll = 5
    return c


class RconParseTest(unittest.TestCase):
    def test_parse_normal(self):
        raw = "There are 2 of a max 20 players online: Alice, Bob"
        self.assertEqual(notifier._parse_rcon_list(raw), ["Alice", "Bob"])

    def test_parse_single(self):
        raw = "There are 1 of a max 20 players online: Alice"
        self.assertEqual(notifier._parse_rcon_list(raw), ["Alice"])

    def test_parse_empty(self):
        raw = "There are 0 of a max 20 players online: "
        self.assertEqual(notifier._parse_rcon_list(raw), [])

    def test_parse_none(self):
        self.assertEqual(notifier._parse_rcon_list(""), [])
        self.assertEqual(notifier._parse_rcon_list(None), [])

    def test_parse_strips_whitespace(self):
        raw = "online:  Alice ,  Bob , "
        self.assertEqual(notifier._parse_rcon_list(raw), ["Alice", "Bob"])


class OnlineDictTest(unittest.TestCase):
    def setUp(self):
        # 每个测试前清空模块级 dict
        with notifier._online_lock:
            notifier._online_players.clear()

    def test_set_and_pop(self):
        notifier._set_online("Alice", "uuid-a")
        self.assertEqual(notifier._snapshot_online(), {"Alice": "uuid-a"})
        notifier._pop_online("Alice")
        self.assertEqual(notifier._snapshot_online(), {})

    def test_pop_missing_no_error(self):
        notifier._pop_online("Nobody")  # 不应抛

    def test_snapshot_is_copy(self):
        notifier._set_online("Alice", "uuid-a")
        snap = notifier._snapshot_online()
        snap["Alice"] = "mutated"
        # 原字典不受影响
        self.assertEqual(notifier._snapshot_online()["Alice"], "uuid-a")


class OnPlayerJoinedLeftTest(unittest.TestCase):
    def setUp(self):
        with notifier._online_lock:
            notifier._online_players.clear()

    def test_joined_adds_uuid(self):
        server = mock.Mock()
        notifier.on_player_joined(server, "Alice", info=object())
        self.assertIn("Alice", notifier._snapshot_online())

    def test_left_removes(self):
        notifier._set_online("Alice", "uuid-a")
        notifier.on_player_left(mock.Mock(), "Alice")
        self.assertNotIn("Alice", notifier._snapshot_online())

    def test_joined_delivers_pending(self):
        # joined 应立即拉一次 pending（补推离线堆积）
        notifier.configure(_cfg())
        server = mock.Mock()
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=[
            {"id": 1, "category": "sheet_claimed", "payload": {"actor_name": "B", "item_name": "i"}}
        ]):
            with mock.patch.object(notifier.sheet_client, "ack_notifications", return_value={}) as ack:
                notifier.on_player_joined(server, "Alice", info=object())
        # 应 tell 玩家 + 调一次 ack
        self.assertTrue(server.tell.called)
        ack.assert_called_once()
        # ack_notifications(cfg, uuid, ids) —— ids 是第三个位置参数
        args, _ = ack.call_args
        self.assertIn(1, args[2])


class InitFromRconTest(unittest.TestCase):
    def setUp(self):
        with notifier._online_lock:
            notifier._online_players.clear()

    def test_server_not_running_skips(self):
        server = mock.Mock()
        server.is_server_running.return_value = False
        notifier.init_online_from_rcon(server)
        server.rcon_query.assert_not_called()

    def test_parses_online_names(self):
        server = mock.Mock()
        server.is_server_running.return_value = True
        server.rcon_query.return_value = "There are 2 of a max 20 players online: Alice, Bob"
        notifier.init_online_from_rcon(server)
        snap = notifier._snapshot_online()
        self.assertIn("Alice", snap)
        self.assertIn("Bob", snap)

    def test_rcon_none_skips(self):
        server = mock.Mock()
        server.is_server_running.return_value = True
        server.rcon_query.return_value = None
        notifier.init_online_from_rcon(server)
        self.assertEqual(notifier._snapshot_online(), {})


class DeliverForPlayerTest(unittest.TestCase):
    def setUp(self):
        with notifier._online_lock:
            notifier._online_players.clear()

    def test_tells_each_notification_then_acks(self):
        notifier.configure(_cfg())
        server = mock.Mock()
        items = [
            {"id": 1, "category": "sheet_claimed", "payload": {"actor_name": "B", "item_name": "i"}},
            {"id": 2, "category": "sheet_done", "payload": {"actor_name": "B", "item_name": "j"}},
        ]
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=items):
            with mock.patch.object(notifier.sheet_client, "ack_notifications", return_value={}) as ack:
                notifier._deliver_for_player(server, "Alice", "uuid-a", _cfg())
        self.assertEqual(server.tell.call_count, 2)
        ack.assert_called_once()
        args, _ = ack.call_args
        self.assertEqual(sorted(args[2]), [1, 2])

    def test_empty_list_does_nothing(self):
        notifier.configure(_cfg())
        server = mock.Mock()
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=[]):
            with mock.patch.object(notifier.sheet_client, "ack_notifications") as ack:
                notifier._deliver_for_player(server, "Alice", "uuid-a", _cfg())
        server.tell.assert_not_called()
        ack.assert_not_called()

    def test_network_failure_silent(self):
        # pending 返回 None（网络失败）→ 不 tell、不 ack、不抛
        notifier.configure(_cfg())
        server = mock.Mock()
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=None):
            with mock.patch.object(notifier.sheet_client, "ack_notifications") as ack:
                notifier._deliver_for_player(server, "Alice", "uuid-a", _cfg())
        server.tell.assert_not_called()
        ack.assert_not_called()

    def test_ack_failure_does_not_raise(self):
        notifier.configure(_cfg())
        server = mock.Mock()
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=[
            {"id": 1, "category": "sheet_claimed", "payload": {"actor_name": "B", "item_name": "i"}}
        ]):
            with mock.patch.object(notifier.sheet_client, "ack_notifications", return_value=None):
                # 不应抛
                notifier._deliver_for_player(server, "Alice", "uuid-a", _cfg())
        self.assertTrue(server.tell.called)

    def test_ack_http_error_does_not_raise(self):
        # ack 返回 HttpError（如 body 契约偏差致 422）→ 不抛、tell 仍发生、留 warning。
        # 回归用例：曾因 ack body 缺 player_uuid 致 422，delivered_at 永不置位 → 通知刷屏。
        notifier.configure(_cfg())
        server = mock.Mock()
        http_err = notifier.sheet_client.HttpError(status=422, detail="player_uuid missing")
        with mock.patch.object(notifier.sheet_client, "pending_notifications", return_value=[
            {"id": 1, "category": "sheet_claimed", "payload": {"actor_name": "B", "item_name": "i"}}
        ]):
            with mock.patch.object(notifier.sheet_client, "ack_notifications", return_value=http_err):
                # 不应抛
                notifier._deliver_for_player(server, "Alice", "uuid-a", _cfg())
        self.assertTrue(server.tell.called)


if __name__ == "__main__":
    unittest.main()
