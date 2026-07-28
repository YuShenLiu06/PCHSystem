"""``!!PCH construction`` 命令回调集合。

v0.10.0 起含四条子命令：
- ``status`` —— 展示 ``construction_tracker`` 后台循环的运行状态（v0.9，运维/玩家均可）；
- ``join [sheet_id]`` —— 加入指定 sheet 的施工（无参时回显当前 / 引导到 Web 端）；
- ``leave`` —— 退出当前活跃加入；
- ``current`` —— 回显当前活跃加入的项目（并入 status 的轻量查询版）。

镜像 ``commands.py::_status`` / ``sheet_commands`` 模板（红线 RS-6 / RS-8 / RS-11）：
- 命令回调外套 ``@new_thread('pch_system construction')`` 后台线程；
- 玩家身份用 ``uuid_api_remake.get_uuid(player)``（RS-8），异常回执红字；
- 调 ``construction_client``，按 ``Union[dict, str 哨兵, HttpError, None]`` 分支回执；
- ``src.reply`` / ``server.tell`` 线程安全（S-1 MCDR CommandSource）。

色板遵循 ``McdrPlugin/CLAUDE.md`` §6：标题 gold+bold、键 aqua、值 gray、
成功 green / 错误 red / 警告 yellow。
"""
import uuid_api_remake  # RS-8：get_uuid(name)->str

from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle

from . import construction_client, construction_tracker
from .config import PchSystemConfig

# 由 __init__.py 在 on_load 中注入（与 sheet_commands.CONFIG 同范式）
CONFIG: PchSystemConfig = PchSystemConfig()


def configure(cfg: PchSystemConfig) -> None:
    """注入当前配置（由 ``__init__.py`` 调用）。"""
    global CONFIG
    CONFIG = cfg


def _construction_status(src, ctx):
    """``!!PCH construction status``：施工进度追踪器状态自检（运维/玩家均可，控制台亦可）。

    镜像 ``commands.py::_status``（RS-6）：``@new_thread`` 包装内层 ``_do()``，
    try 取 ``construction_tracker.get_status()``，异常回执红字错误；
    否则 ``src.reply(_render_status(state))``。``ctx`` 不使用（``status`` 是叶子字面量，
    Literal 不入 context，回调签名按 MCDR 约定保留双参）。
    """
    @new_thread('pch_system construction')
    def _do():
        try:
            state = construction_tracker.get_status()
            src.reply(_render_status(state))
        except Exception as e:
            src.reply(RText(f"§c施工追踪器状态查询失败: {e}§r"))

    _do()


# === join / leave / current（v0.10.0 加入施工机制）===


def _require_player(src):
    """非玩家执行返回提示并 None。镜像 ``sheet_commands._require_player``。"""
    if not src.is_player:
        src.reply(RText("!!PCH construction join/leave/current 只能玩家在游戏内执行", color=RColor.red))
        return None
    return src.player


def _resolve_uuid_or_tell(server, player_name):
    """推导 UUID，失败回执红字。成功返回 UUID 字符串，失败返回 None。"""
    try:
        return uuid_api_remake.get_uuid(player_name)
    except Exception as e:  # noqa: BLE001
        server.tell(
            player_name,
            RText(f"UUID 推导失败: {e}", color=RColor.red),
        )
        return None


def _resolve_outcome(server, player_name, outcome, *, on_success):
    """分支解析 ``construction_client`` 返回（镜像 ``sheet_commands._resolve`` 简化版）。

    成功 dict → 调 ``on_success(value)`` 自行回执；哨兵 / HttpError / None → 统一
    回执并返回 None。错误码翻译与 ``api/construction.md`` §6 对齐：
    401 → 服务暂不可用（token 错）/ 403 → 未绑定 Web 账号 / 404 → 项目不存在 /
    409（含「归档」/「archiv」）→ 项目已归档 / 409 其他 → 已加入其他项目 / 422 → 参数有误。
    """
    if outcome is None:
        server.tell(player_name, RText("服务暂不可用，请稍后再试", color=RColor.red))
        return None
    if outcome == construction_client.RATE_LIMITED:
        server.tell(player_name, RText("操作太频繁，请稍后再试", color=RColor.yellow))
        return None
    if outcome == construction_client.REMOVED:
        server.tell(player_name, RText("访问被拒（权限不足）", color=RColor.red))
        return None
    if isinstance(outcome, construction_client.HttpError):
        status, detail = outcome.status, outcome.detail or ""
        if status == 401:
            server.tell(player_name, RText("服务暂不可用（鉴权失败）", color=RColor.red))
        elif status == 403:
            server.tell(
                player_name,
                RText("未绑定 Web 账号，请先 !!PCH bind 绑定", color=RColor.red),
            )
        elif status == 404:
            server.tell(player_name, RText("项目不存在", color=RColor.red))
        elif status == 409:
            d_lower = detail.lower()
            if "归档" in detail or "archiv" in d_lower:
                server.tell(player_name, RText("项目已归档，只读", color=RColor.red))
            else:
                # 既有活跃加入他项目 / 状态非法 —— detail 已含「先退出或切换」提示
                server.tell(player_name, RText(f"已加入其他项目：{detail}", color=RColor.yellow))
        elif status == 422:
            server.tell(player_name, RText(f"参数有误：{detail}", color=RColor.red))
        else:
            server.tell(player_name, RText("服务暂不可用，请稍后再试", color=RColor.red))
        return None
    # 成功 dict
    on_success(outcome)
    return outcome


def _active_state_dict(payload):
    """从 ``MyConstructionResult`` 形态取 ``active`` dict（容错：缺失/类型异常 → {}）。"""
    if not isinstance(payload, dict):
        return {}
    active = payload.get("active")
    return active if isinstance(active, dict) else {}


def _render_active_card(active, *, title="当前施工项目"):
    """渲染「当前施工项目」卡片：未加入 → gray 空态；已加入 → gold 标题 + aqua/gray 字段。"""
    sheet_id = active.get("sheet_id")
    if sheet_id is None:
        return RTextList(
            RText(f"{title}：", color=RColor.aqua),
            RText("未加入任何项目", color=RColor.gray),
            RText("\n"),
        )
    parts = [
        RText(f"{title}：\n", color=RColor.gold).set_styles(RStyle.bold),
        _kv_line("项目 ID", RText(str(sheet_id), color=RColor.gray)),
        _kv_line("项目标题", RText(str(active.get("sheet_title") or ""), color=RColor.gray)),
        _kv_line("加入时间", RText(str(active.get("joined_at") or ""), color=RColor.gray)),
        _kv_line(
            "加入来源",
            RText(
                "自动（备货触发）" if active.get("join_source") == "auto"
                else "手动" if active.get("join_source") == "manual"
                else "未知",
                color=RColor.gray,
            ),
        ),
    ]
    return RTextList(*parts)


def _construction_join(src, ctx):
    """``!!PCH construction join [sheet_id]`` —— 加入指定 sheet 的施工。

    - 有参：调 ``join_construction`` 显式加入，回执成功/失败。
    - 无参：先 ``get_my_construction``；已加入 → 回显当前并提示「如需切换请先 leave」；
      未加入 → 提示「在 Web 端项目页加入或指定 sheet_id」（**不猜** sheet）。

    RS-6 @new_thread；RS-8 UUID；RS-11 失败回执。控制台拒绝（玩家身份必需）。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    # Integer 节点入 ctx；无参时 ctx 不含该键
    sheet_id = ctx.get("sheet_id")

    @new_thread('pch_system construction')
    def _do():
        player_uuid = _resolve_uuid_or_tell(server, player_name)
        if player_uuid is None:
            return

        if sheet_id is None:
            # 无参：回显当前 / 引导
            outcome = construction_client.get_my_construction(CONFIG, player_uuid)

            def _on_current(value):
                active = _active_state_dict(value)
                if active.get("sheet_id") is None:
                    server.tell(
                        player_name,
                        RTextList(
                            RText("未加入任何施工项目。", color=RColor.gray),
                            RText("请在 Web 端项目页加入，或使用 ", color=RColor.gray),
                            RText("!!PCH construction join <sheet_id>", color=RColor.aqua),
                            RText(" 显式加入。", color=RColor.gray),
                        ),
                    )
                else:
                    server.tell(
                        player_name,
                        RTextList(
                            _render_active_card(active),
                            RText(
                                "如需切换项目，请先 !!PCH construction leave 后再加入。",
                                color=RColor.gray,
                            ),
                        ),
                    )

            _resolve_outcome(server, player_name, outcome, on_success=_on_current)
            return

        # 有参：显式 join
        outcome = construction_client.join_construction(CONFIG, player_uuid, int(sheet_id))

        def _on_joined(value):
            active = _active_state_dict(value)
            if active.get("sheet_id") is None:
                # 后端返回空态（理论上 join 不会发生）—— 兜底提示
                server.tell(
                    player_name,
                    RText("加入未生效，请稍后重试或联系管理员", color=RColor.yellow),
                )
                return
            server.tell(
                player_name,
                RTextList(
                    RText("已加入施工：\n", color=RColor.green).set_styles(RStyle.bold),
                    _render_active_card(active),
                ),
            )

        _resolve_outcome(server, player_name, outcome, on_success=_on_joined)

    _do()


def _construction_leave(src, ctx):
    """``!!PCH construction leave`` —— 退出当前活跃加入（幂等：未加入也回执成功）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()

    @new_thread('pch_system construction')
    def _do():
        player_uuid = _resolve_uuid_or_tell(server, player_name)
        if player_uuid is None:
            return
        outcome = construction_client.leave_construction(CONFIG, player_uuid)

        def _on_left(value):
            active = _active_state_dict(value)
            if active.get("sheet_id") is None:
                server.tell(
                    player_name,
                    RText("已退出施工项目（或本就未加入）", color=RColor.green),
                )
            else:
                # 后端 active 仍有值（异常）—— 兜底，不应发生
                server.tell(
                    player_name,
                    RTextList(
                        RText("退出未生效，当前仍加入：", color=RColor.yellow),
                        _render_active_card(active),
                    ),
                )

        _resolve_outcome(server, player_name, outcome, on_success=_on_left)

    _do()


def _construction_current(src, ctx):
    """``!!PCH construction current`` —— 回显当前活跃加入的项目（轻量查询，并入 status）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()

    @new_thread('pch_system construction')
    def _do():
        player_uuid = _resolve_uuid_or_tell(server, player_name)
        if player_uuid is None:
            return
        outcome = construction_client.get_my_construction(CONFIG, player_uuid)

        def _on_current(value):
            active = _active_state_dict(value)
            if active.get("sheet_id") is None:
                server.tell(
                    player_name,
                    RText("未加入任何施工项目", color=RColor.gray),
                )
            else:
                server.tell(player_name, _render_active_card(active))

        _resolve_outcome(server, player_name, outcome, on_success=_on_current)

    _do()


def _kv_line(key: str, value_rtext) -> RTextList:
    """渲染一行「键：值\\n」——键 aqua，值任意 RText，行尾换行。

    RTextList 嵌套是 MCDR 标准用法（见 ``commands.py::_pch_root`` 的 ``_line``），
    外层 ``RTextList(*parts)`` 会逐项拼接，嵌套 RTextList 自动展开。
    """
    return RTextList(
        RText(f"{key}：", color=RColor.aqua),
        value_rtext,
        RText("\n"),
    )


# ``last_outcome`` 中文映射：键→(颜色, 文案)
# 锚定 construction_tracker.get_status() 返回的 last_outcome 枚举字面量。
_OUTCOME_MAP = {
    "ok": (RColor.green, "成功上报"),
    "disabled": (RColor.gray, "已禁用"),
    "stats_dir_missing": (RColor.red, "stats 目录不存在"),
    "no_online": (RColor.gray, "无在线玩家"),
    "fetch_failed": (RColor.red, "拉取施工项目失败"),
    "no_placements": (RColor.gray, "无新增放置"),
    "skipped_no_attribution": (RColor.yellow, "多项目/无项目，跳过上报（baseline 已推进）"),
    "report_failed": (RColor.red, "上报失败"),
    "rate_limited": (RColor.yellow, "被限频"),
    "removed": (RColor.red, "被移白名单"),
    "http_error": (RColor.red, "HTTP 错误"),
}


def _format_outcome(outcome) -> RText:
    """将 ``last_outcome`` 枚举映射为中文 RText。

    - ``None`` → gray「尚未运行」（已跑过但无结果，例如刚启动）；
    - 已知枚举 → ``_OUTCOME_MAP`` 配色；
    - 未知字面量 → gray 兜底带原值（避免未来扩展静默错配）。
    """
    if outcome is None:
        return RText("尚未运行", color=RColor.gray)
    color, text = _OUTCOME_MAP.get(outcome, (RColor.gray, f"未知结果: {outcome}"))
    return RText(text, color=color)


def _render_status(state: dict) -> RTextList:
    """用 RTextList 多行渲染追踪器状态字典。

    所有键访问用 ``.get(key, default)`` 容错（state 可能为空 dict，
    或 tracker 实现尚未填充某些键）。色板：标题 gold+bold、键 aqua、值 gray，
    状态/结果按语义上色。
    """
    parts = []

    # 标题
    parts.append(RText("施工进度追踪器\n", color=RColor.gold).set_styles(RStyle.bold))

    # 启用状态
    enabled = bool(state.get("enabled", False))
    if enabled:
        parts.append(_kv_line("启用状态", RText("已启用", color=RColor.green)))
    else:
        parts.append(_kv_line(
            "启用状态",
            RText("已禁用（配置 construction_enabled=false）", color=RColor.red),
        ))

    # stats 目录：路径 gray + 存在性 green/red
    stats_dir = str(state.get("stats_dir", ""))
    stats_dir_ok = bool(state.get("stats_dir_ok", False))
    stats_value = RTextList(
        RText(stats_dir, color=RColor.gray),
        RText(
            " 存在" if stats_dir_ok else " 不存在",
            color=RColor.green if stats_dir_ok else RColor.red,
        ),
    )
    parts.append(_kv_line("stats 目录", stats_value))

    # 在线玩家
    online = state.get("online", 0)
    parts.append(_kv_line("在线玩家", RText(str(online), color=RColor.gray)))

    # 当前施工项目：数量 + 启发式归因是否可行
    active_sheets = state.get("active_sheets", 0)
    heuristic_eligible = bool(state.get("heuristic_eligible", False))
    if heuristic_eligible:
        sheets_value = RTextList(
            RText(f"{active_sheets} 个", color=RColor.gray),
            RText(" 可自动归因", color=RColor.green),
        )
    else:
        sheets_value = RTextList(
            RText(f"{active_sheets} 个", color=RColor.gray),
            RText(
                " 0 或 >1，无法自动归因，需 Web 端切客户端模组精确上报",
                color=RColor.yellow,
            ),
        )
    parts.append(_kv_line("当前施工项目", sheets_value))

    # flush 间隔
    flush_interval = state.get("flush_interval", 0.0)
    parts.append(_kv_line("flush 间隔", RText(f"{flush_interval} 秒", color=RColor.gray)))

    # baseline 玩家数
    baselined = state.get("baselined_players", 0)
    parts.append(_kv_line("baseline 玩家数", RText(str(baselined), color=RColor.gray)))

    # 上次结果：从未运行 → gray「尚未运行过一次」；否则 outcome 中文 + 计数 + 错误
    last_at = str(state.get("last_at", ""))
    if not last_at:
        parts.append(_kv_line("上次结果", RText("尚未运行过一次", color=RColor.gray)))
    else:
        outcome_text = _format_outcome(state.get("last_outcome"))
        reported = state.get("last_reported", 0)
        accepted = state.get("last_accepted", 0)
        skipped = state.get("last_skipped", 0)
        detail_parts = [
            outcome_text,
            RText(
                f"（上报 {reported} / 接受 {accepted} / 跳过 {skipped}）",
                color=RColor.gray,
            ),
        ]
        last_error = state.get("last_error")
        if last_error:
            detail_parts.append(RText(f" 错误: {last_error}", color=RColor.red))
        parts.append(_kv_line("上次结果", RTextList(*detail_parts)))

    # 末尾 gray 提示行（多项目归因限制 + 引导）
    parts.append(RText(
        "提示：多项目并发时本追踪器无法归因，需到 Web 端切客户端模组；"
        "详细见 !!PCH status 与项目文档。",
        color=RColor.gray,
    ))

    return RTextList(*parts)
