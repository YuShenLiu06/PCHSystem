"""后台通知轮询器。

职责：
- 维护在线玩家 name→uuid 字典（on_player_joined/left + rcon 'list' 初始化兜底）；
- 后台线程每 notify_poll_interval_seconds 秒，对每个在线玩家拉 pending 通知，
  逐条 server.tell 投递，成功后一次性 ack；
- on_player_joined 立即拉一次（离线堆积补推）。

红线：
- RS-6：全程 @new_thread / 独立线程，含超时；禁用 schedule_task 卸载阻塞。
- RS-11：网络失败静默继续下次（不 tell 玩家，避免刷屏）；通知投递本身是非关键路径。
"""
import logging
import threading

import uuid_api_remake  # RS-8

from mcdreforged.api.decorator import new_thread

from . import sheet_client
from .config import HtcmcAuthConfig
from .messages import format_notification

_log = logging.getLogger("htcmc_auth.notifier")

# 模块级在线玩家字典：name -> uuid（由事件维护 + rcon 初始化）
_online_players: dict = {}
_online_lock = threading.Lock()


def _set_online(name: str, uuid: str) -> None:
    with _online_lock:
        _online_players[name] = uuid


def _pop_online(name: str) -> None:
    with _online_lock:
        _online_players.pop(name, None)


def _snapshot_online() -> dict:
    with _online_lock:
        return dict(_online_players)


def _parse_rcon_list(raw: str) -> list:
    """解析 rcon 'list' 返回，形如：
    "There are 2 of a max 20 players online: Alice, Bob"
    返回玩家名列表（可能为空）。无法解析返回 []。
    """
    if not raw:
        return []
    # 取最后一个冒号后的部分
    if ":" in raw:
        tail = raw.rsplit(":", 1)[1].strip()
    else:
        tail = raw.strip()
    if not tail:
        return []
    return [p.strip() for p in tail.split(",") if p.strip()]


def init_online_from_rcon(server) -> None:
    """插件加载时若服务端已启动，用 rcon 'list' 兜底初始化在线玩家集合。

    rcon_query 返回 str | None；None 时跳过。UUID 推导失败也跳过该玩家。
    """
    if not server.is_server_running():
        return
    try:
        raw = server.rcon_query("list")
    except Exception as e:  # noqa: BLE001 - rcon 故障不致命
        _log.warning("rcon list failed during init: %s", e)
        return
    if not raw:
        return
    for name in _parse_rcon_list(raw):
        try:
            _set_online(name, uuid_api_remake.get_uuid(name))
        except Exception as e:  # noqa: BLE001
            _log.warning("uuid lookup failed for %s during init: %s", name, e)


def on_player_joined(server, player, info) -> None:
    """玩家上线：推导 uuid 存字典；立即拉一次 pending 补推离线堆积。

    本回调跑在 MCDR 事件主线程（mcdr.player_joined → task executor = 主线程），
    故只做轻量的 uuid 推导 + set_online，**补推**（pending+ack 两次 HTTP）必须
    卸载到 @new_thread 后台线程，否则阻塞服务端主 tick（RS-6）。
    """
    try:
        uuid_ = uuid_api_remake.get_uuid(player)
    except Exception as e:  # noqa: BLE001
        _log.warning("uuid lookup failed on join for %s: %s", player, e)
        return
    _set_online(player, uuid_)
    # 补推卸载到后台线程，不阻塞事件回调（RS-6）
    _spawn_join_push(server, player, uuid_)


@new_thread('htcmc_sheet_join_push')
def _spawn_join_push(server, player_name: str, player_uuid: str) -> None:
    """上线补推后台线程：拉一次 pending → tell → ack。"""
    try:
        _deliver_for_player(server, player_name, player_uuid, cfg=_CURRENT_CFG)
    except Exception as e:  # noqa: BLE001
        _log.warning("join push failed for %s: %s", player_name, e)


def on_player_left(server, player) -> None:
    """玩家下线：从字典移除。"""
    _pop_online(player)


# === 当前配置注入（由 __init__.py on_load 设置）===
_CURRENT_CFG: HtcmcAuthConfig = HtcmcAuthConfig()


def configure(cfg: HtcmcAuthConfig) -> None:
    global _CURRENT_CFG
    _CURRENT_CFG = cfg


def _deliver_for_player(server, player_name: str, player_uuid: str, cfg: HtcmcAuthConfig) -> None:
    """拉一次 pending，逐条 tell，成功后一次 ack。失败静默（避免刷屏）。"""
    outcome = sheet_client.pending_notifications(cfg, player_uuid, cfg.notify_max_per_poll)
    if not isinstance(outcome, list) or not outcome:
        return
    ids = []
    for n in outcome:
        try:
            server.tell(player_name, format_notification(n))
        except Exception as e:  # noqa: BLE001 - 单条渲染失败不影响其余
            _log.warning("tell notification failed for %s: %s", player_name, e)
        nid = n.get("id")
        if nid is not None:
            ids.append(nid)
    if ids:
        # ack 失败不致命：下次轮询会再拉到（pending 是 delivered_at IS NULL）
        ack_outcome = sheet_client.ack_notifications(cfg, player_uuid, ids)
        if ack_outcome is None:
            _log.warning("ack failed for %s (ids=%s)", player_name, ids)


def run(server, cfg: HtcmcAuthConfig, stop_event: threading.Event) -> None:
    """后台轮询循环（由 __init__.py 用 @new_thread 启动）。

    每 cfg.notify_poll_interval_seconds 秒：遍历在线 dict，拉 pending → tell → ack。
    网络失败静默继续下次。
    """
    interval = max(1.0, float(cfg.notify_poll_interval_seconds))
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break  # 被唤醒（停止）
        if not server.is_server_running():
            continue
        for name, uuid_ in _snapshot_online().items():
            try:
                _deliver_for_player(server, name, uuid_, cfg)
            except Exception as e:  # noqa: BLE001 - 单玩家失败不中断整轮
                _log.warning("deliver loop failed for %s: %s", name, e)
