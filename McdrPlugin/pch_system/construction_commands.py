"""``!!PCH construction`` 命令回调集合。

当前仅 ``status`` 子命令——展示 ``construction_tracker`` 后台循环的运行状态。

镜像 ``commands.py::_status`` 模板（红线 RS-6）：
- 命令回调外套 ``@new_thread('pch_system construction')`` 后台线程，
  避免 ``construction_tracker.get_status()`` 与后台循环的潜在锁竞争阻塞主线程；
- 异常回执红字（``§c...§r``），成功回执 ``_render_status`` 多行 RTextList；
- ``src.reply`` 线程安全（``ConsoleSource`` / ``PlayerSource`` 通用，S-1 MCDR CommandSource.reply）。

色板遵循 ``McdrPlugin/CLAUDE.md`` §6：标题 gold+bold、键 aqua、值 gray、
成功 green / 错误 red / 警告 yellow。
"""
from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle

from . import construction_tracker


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
