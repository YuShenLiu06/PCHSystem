"""施工进度后台 flush 循环 + baseline 幂等管理。

职责：
- 后台线程每 ``construction_flush_interval_seconds`` 秒一轮，对每个在线玩家读
  ``world/stats/<uuid>.json`` 取 ``minecraft:used`` 累积量，与本模块内存 baseline
  差值得到本轮增量，组装 placements 调 ``construction_client.report_placements``
  （sheet_id=None，启发式归因）批量上报。

幂等策略（核心）：
- **首见建基**：首次见到某玩家的 stats → baseline = current，本轮不报
  （避免历史放置量当增量）。下轮才开始 diff。
- **2xx 推进 baseline**：后端 ``submit_report`` 单事务，2xx = 整批确认
  （accepted + skipped 都算确认）。本块 2xx 才推进本块涉及的玩家的 baseline。
- **失败 / 哨兵不推进**：网络失败 None / HttpError / RATE_LIMITED / REMOVED → 不
  推进 baseline，下一轮 ``diff_counts`` 自然重新算（不会丢失或翻倍）。
- **多项目（heuristic_eligible=False）**：跳过上报，但**推进 baseline**（丢弃
  这段增量，避免堆积；归因责任在后端 active-sheets 视图，追踪器不绕过 C-7）。

循环骨架复刻自 ``notifier.run`` 的 ``stop_event.wait(interval)``；本模块自身**不
装饰 @new_thread**，由 ``__init__.py`` 调用方包（R-12 / RS-6）。

红线：
- R-1 / R-7：只 HTTP 上报 + 读本地 stats 文件；内存 baseline 仅缓冲，重启可丢
  （后端 ``placement_records`` 是权威）。
- R-12 / C-8：HTTP 含超时+重试（复用 ``construction_client``，已含）。
- C-7：严格单源，追踪器不切源；玩家切到别的源时后端 skip，追踪器不绕过
  （skip 即推进 baseline）。
- RS-11：失败 / 哨兵记日志（``_log.warning``）+ 经 ``_record`` 透出 outcome/error，
  不静默吞。

不 import mcdreforged（本模块纯标准库 + 项目内模块）。
"""
import datetime
import logging
import os
import threading
from typing import Optional

from . import construction_client
from . import notifier
from . import stats_reader
from .config import PchSystemConfig

_log = logging.getLogger("pch_system.construction_tracker")

# === 模块级状态（flush 线程写、status 命令线程读 → 加锁）===
_lock = threading.Lock()
# player_uuid → {registry_id: 已确认进后端的累积量基线}
_baselines: dict[str, dict[str, int]] = {}
_last_result: dict = {}  # 最近一次 flush 的状态快照（给命令读）

# 当前配置（由 __init__.py on_load 注入；_flush_once 也接受 cfg 参数便于测试驱动）
_CURRENT_CFG: PchSystemConfig = PchSystemConfig()


def configure(cfg: PchSystemConfig) -> None:
    """注入当前配置（由 ``__init__.py`` 调用）。"""
    global _CURRENT_CFG
    _CURRENT_CFG = cfg


def get_status() -> dict:
    """线程安全返回 ``_last_result`` 的副本（空 dict 若从未 flush）。"""
    with _lock:
        return dict(_last_result)


def _reset() -> None:
    """清空 baseline / last_result（测试隔离用；run() 启动时也调）。"""
    with _lock:
        _baselines.clear()
        _last_result.clear()


def run(server, cfg: PchSystemConfig, stop_event: threading.Event) -> None:
    """后台 flush 循环（由 ``__init__.py`` 用 ``@new_thread('pch_construction')`` 启动）。

    复刻 ``notifier.run`` 的 ``stop_event.wait(interval)`` 骨架；单轮失败记 warning
    不中断。R-12：本模块不装饰 @new_thread，由调用方包。
    """
    _reset()
    interval = max(5.0, float(cfg.construction_flush_interval_seconds))
    # 启动时校验 stats 目录（运行中每轮由 _flush_once 再判，这里仅 best-effort 提示）
    if not os.path.isdir(cfg.world_stats_dir):
        server.logger.warning(
            "construction_tracker: world_stats_dir 不是目录：%r", cfg.world_stats_dir
        )
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break  # 被唤醒（停止）
        if not server.is_server_running():
            continue
        try:
            _flush_once(cfg)
        except Exception as e:  # noqa: BLE001 - 单轮失败不中断整个循环
            _log.warning("construction_tracker flush failed: %s", e)


def _flush_once(cfg: PchSystemConfig) -> dict:
    """单轮 flush 逻辑（测试直接驱动它，不需 server）。

    返回本轮 ``_record`` 组装的状态 dict（也写入 ``_last_result``）。严格按 plan
    幂等算法实现：失败/哨兵不推进 baseline，2xx 整批推进，多项目跳过但推进。
    """
    if not cfg.construction_enabled:
        return _record(cfg, outcome="disabled")

    stats_dir_ok = os.path.isdir(cfg.world_stats_dir)
    # DRY：复用 notifier 在线字典（RS-8 UUID 一致）
    online = notifier._snapshot_online()

    if not stats_dir_ok:
        return _record(
            cfg, outcome="stats_dir_missing", stats_dir_ok=False, online=len(online)
        )

    if not online:
        return _record(cfg, outcome="no_online", online=0, stats_dir_ok=True)

    # 取在线玩家做双头代调 active-sheets（全局信息，代谁结果一致）
    proxy_uuid = next(iter(online.values()))
    active = construction_client.get_active_sheets(cfg, proxy_uuid)
    if not isinstance(active, dict):
        # None / HttpError / 哨兵 → 拉取失败，不推进 baseline（下轮重试）
        _log.warning(
            "construction_tracker get_active_sheets failed: %s", _describe(active)
        )
        return _record(
            cfg,
            outcome="fetch_failed",
            online=len(online),
            error=_describe(active),
            stats_dir_ok=True,
        )

    sheets = active.get("sheets") or []
    heuristic_eligible = bool(active.get("heuristic_eligible"))
    active_count = len(sheets) if isinstance(sheets, list) else 0

    # 计算 placements + 首见建基（在 _lock 内）
    placements: list = []
    advanced: dict = {}  # uuid → 该玩家本轮 current 快照（成功才提交为 baseline）
    with _lock:
        for name, uuid_ in online.items():
            doc = stats_reader.read_stats_file(
                stats_reader.stats_path_for(cfg.world_stats_dir, uuid_)
            )
            if doc is None:
                continue  # stats 文件缺失 / 不可读 → 跳过（下轮若出现则首见）
            current = stats_reader.used_counts(doc)
            base = _baselines.get(uuid_)
            if base is None:
                # 首见：建基 = 当前，本轮不报（避免历史放置当增量）
                _baselines[uuid_] = dict(current)
                continue
            delta = stats_reader.diff_counts(current, base)
            if not delta:
                continue
            for rid, qty in delta.items():
                placements.append(
                    {
                        "player_uuid": uuid_,
                        "registry_id": rid,
                        "placed_qty": int(qty),
                        "broken_qty": 0,  # track_breaking 本期不实现，恒 0
                    }
                )
            advanced[uuid_] = dict(current)

    if not placements:
        return _record(
            cfg,
            outcome="no_placements",
            online=len(online),
            active_sheets=active_count,
            heuristic_eligible=heuristic_eligible,
        )

    if not heuristic_eligible:
        # 0 或 >1 个 constructing → 无法归因 → 跳过上报，但推进 baseline
        # （丢弃这段增量，不堆积；C-7：归因责任在后端，追踪器不绕过）
        with _lock:
            for uuid_, snap in advanced.items():
                _baselines[uuid_] = snap
        return _record(
            cfg,
            outcome="skipped_no_attribution",
            online=len(online),
            active_sheets=active_count,
            heuristic_eligible=False,
            reported=len(placements),
        )

    # 启发式归因：sheet_id=None，分块上报
    accepted = skipped = 0
    outcome, error = "ok", None
    for chunk in _chunks(placements, max(1, int(cfg.construction_max_batch))):
        result = construction_client.report_placements(cfg, chunk, sheet_id=None)
        if isinstance(result, dict):
            t = result.get("totals") or {}
            accepted += int(t.get("accepted") or 0)
            skipped += int(t.get("skipped") or 0)
            chunk_players = {p["player_uuid"] for p in chunk}
            with _lock:  # 2xx = 整批确认 → 推进本块玩家的 baseline
                for uuid_ in chunk_players:
                    if uuid_ in advanced:
                        _baselines[uuid_] = advanced[uuid_]
        else:  # 非 2xx → 不推进，后续块也不发（下轮 delta 自然重试）
            outcome, error = _classify(result)
            _log.warning(
                "construction_tracker report failed (outcome=%s): %s",
                outcome,
                _describe(result),
            )
            break

    return _record(
        cfg,
        outcome=outcome,
        online=len(online),
        active_sheets=active_count,
        heuristic_eligible=True,
        reported=len(placements),
        accepted=accepted,
        skipped=skipped,
        error=error,
    )


# === 辅助 ===


def _chunks(lst, n):
    """yield 长度 ≤ n 的连续子 list。"""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _now_iso() -> str:
    """当前 UTC 时刻 ISO 字符串（命令端展示用）。"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _describe(result) -> str:
    """把 construction_client 的返回值翻译为人类可读 error 字符串。"""
    if result is None:
        return "network"
    if isinstance(result, construction_client.HttpError):
        return f"HTTP {result.status}: {result.detail}"
    if result == construction_client.RATE_LIMITED:
        return "RATE_LIMITED"
    if result == construction_client.REMOVED:
        return "REMOVED"
    return str(result)


def _classify(result) -> tuple:
    """根据 construction_client 返回值分类成 (outcome, error) 对。

    用于 report 失败分支：哨兵字符串的 outcome 即其名，error 不重复（哨兵本身
    已通过 outcome 透出）；HttpError/None 的 error 用 ``_describe`` 译出。
    """
    if result is None:
        return ("report_failed", "network")
    if result == construction_client.RATE_LIMITED:
        return ("rate_limited", None)
    if result == construction_client.REMOVED:
        return ("removed", None)
    if isinstance(result, construction_client.HttpError):
        return ("http_error", _describe(result))
    return ("report_failed", _describe(result))


def _record(
    cfg: PchSystemConfig,
    *,
    outcome: str,
    online: int = 0,
    active_sheets: int = 0,
    heuristic_eligible: bool = False,
    stats_dir_ok: bool = True,
    reported: int = 0,
    accepted: int = 0,
    skipped: int = 0,
    error: Optional[str] = None,
) -> dict:
    """组装契约 dict 并写入 ``_last_result``（线程安全），返回该 dict。

    ``baselined_players`` 在 ``_lock`` 内据当前 ``_baselines`` 计入；其它字段由
    参数确定。键名与 ``get_status()`` 契约逐字一致（命令端按此渲染）。
    """
    payload = {
        "enabled": bool(cfg.construction_enabled),
        "stats_dir": str(cfg.world_stats_dir),
        "stats_dir_ok": bool(stats_dir_ok),
        "online": int(online),
        "active_sheets": int(active_sheets),
        "heuristic_eligible": bool(heuristic_eligible),
        "flush_interval": float(cfg.construction_flush_interval_seconds),
        "last_at": _now_iso(),
        "last_outcome": outcome,
        "last_reported": int(reported),
        "last_accepted": int(accepted),
        "last_skipped": int(skipped),
        "last_error": error,
        "baselined_players": 0,  # 占位，进入 _lock 后补
    }
    with _lock:
        payload["baselined_players"] = len(_baselines)
        _last_result.clear()
        _last_result.update(payload)
    return payload
