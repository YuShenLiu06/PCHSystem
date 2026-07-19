"""!!PCH sheet 子命令回调集合。

每回调统一四步：
  ① @new_thread（RS-6）；
  ② player_uuid = uuid_api_remake.get_uuid(player)（RS-8）；
  ③ 调 sheet_client；
  ④ 按 Union[dict|list, str 哨兵, HttpError, None] 分支 server.tell 回执（RS-11 禁静默）。

错误码翻译（与 sheets.md §8 对齐）：
  403 → 权限不足或非认领人
  404 → 表或行不存在
  409 → 状态非法
  422 → 参数有误（带 detail）
  其他 4xx/5xx → 服务暂不可用提示（重试无益，提示玩家稍后再试）
"""
import uuid_api_remake  # RS-8：get_uuid(name)->str

from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RAction

from . import sheet_client, scanner
from .view_args import paginate_rows, parse_view_args
from .config import PchSystemConfig
from .qty import format_qty_safe
from .messages import (
    SHEET_SERVICE_DOWN,
    SHEET_RATE_LIMITED,
    SHEET_FORBIDDEN,
    SHEET_NOT_FOUND,
    SHEET_CONFLICT,
    SHEET_BAD_REQUEST,
    SHEET_UUID_FAIL,
    SHEET_HEAD,
    SHEET_LIST_EMPTY,
    SHEET_LIST_ITEM,
    SHEET_LIST_MINE,
    SHEET_LIST_FLAG_UNKNOWN,
    SHEET_DETAIL_TITLE,
    SHEET_DETAIL_EMPTY,
    SHEET_VIEW_NO_MATCH,
    SHEET_VIEW_ARG_UNKNOWN,
    SHEET_LAST_EMPTY,
    rtext_button,
    format_row_clickable,
    format_owner_footer,
    format_submit_footer,
    format_view_footer,
    format_pagination_footer,
    format_search_hint,
    format_section_separator,
    format_centered_text,
    format_phase_label,
    SHEET_OK_CREATED,
    SHEET_OK_RENAMED,
    SHEET_OK_DELETED,
    SHEET_OK_ROW_ADDED,
    SHEET_OK_ROW_UPDATED,
    SHEET_OK_ROW_DELETED,
    SHEET_OK_CLAIMED,
    SHEET_OK_DELIVERED,
    SHEET_OK_PROGRESS_SET,
    SHEET_OK_RELEASED,
    SHEET_OK_REJECTED,
    SHEET_OK_ADVANCED_CONSTRUCTING,
    SHEET_OK_ARCHIVED,
    SHEET_ARCHIVED_READONLY,
    SHEET_ARCHIVE_UNCONFIGURED,
    SHEET_BAD_TARGET,
    SHEET_DELIVER_HINT,
    SHEET_NOTIFY_EMPTY,
    format_notification,
    SHEET_NO_DATA_API,
    SHEET_ADDHAND_NEED_HAND,
    SHEET_SETREG_NEED_HAND,
    SHEET_OK_ADDHAND,
    SHEET_OK_SETREG,
    SHEET_SUBMIT_HEAD,
    SHEET_SUBMIT_NO_API,
    SHEET_SUBMIT_NO_ROWS,
    SHEET_SUBMIT_DONE_LINE,
    SHEET_SUBMIT_PROGRESS_LINE,
    SHEET_SUBMIT_SKIP_LINE,
    SHEET_SUBMIT_DONE_HEAD,
    SHEET_SUBMIT_SKIP_HEAD,
    SHEET_SUBMIT_FOLDED_LINE,
    SHEET_SUBMIT_READY_FOLDED_LINE,
    SHEET_SUBMIT_FAIL,
)

# 由 __init__.py 在 on_load 中注入
CONFIG: PchSystemConfig = PchSystemConfig()


def configure(cfg: PchSystemConfig) -> None:
    global CONFIG
    CONFIG = cfg


# === 工具：解析 sheet_client 返回，统一回执；返回处理后的 Python 值（成功 dict/list）或 None（已回执/失败）===

def _resolve(server, player_name, outcome, *, on_success=None):
    """分支解析返回。on_success(value) 在成功时被调用做自定义回执；返回 True 表示已回执。

    成功（dict/list）→ 调 on_success；on_success 返回 None 表示未自行回执，本函数兜底。
    RATE_LIMITED / REMOVED / HttpError / None → 各自回执，返回 None。

    错误码翻译（与 sheets.md §8 对齐）：
      403 → 权限不足或非认领人
      404 → 表或行不存在
      409 归档（detail 含「归档」/「archiv」）→ 项目已归档，只读
      409 其他 → 状态非法
      422 → 参数有误（带 detail）
      其他 4xx/5xx → 服务暂不可用提示（重试无益，提示玩家稍后再试）
    """
    if outcome is None:
        server.tell(player_name, SHEET_SERVICE_DOWN)
        return None
    if outcome == sheet_client.RATE_LIMITED:
        server.tell(player_name, SHEET_RATE_LIMITED)
        return None
    if outcome == sheet_client.REMOVED:
        server.tell(player_name, SHEET_FORBIDDEN)
        return None
    if isinstance(outcome, sheet_client.HttpError):
        err = outcome
        if err.status == 404:
            server.tell(player_name, SHEET_NOT_FOUND)
        elif err.status == 409:
            # 归档只读（detail 含「归档」或「archiv」）单独译；其余 409 为行/状态非法
            if "归档" in (err.detail or "") or "archiv" in (err.detail or "").lower():
                server.tell(player_name, SHEET_ARCHIVED_READONLY)
            else:
                server.tell(player_name, SHEET_CONFLICT)
        elif err.status == 422:
            server.tell(player_name, SHEET_BAD_REQUEST.format(detail=err.detail))
        elif err.status == 403:
            server.tell(player_name, SHEET_FORBIDDEN)
        else:
            server.tell(player_name, SHEET_SERVICE_DOWN)
        return None
    # 成功 dict/list
    if on_success is not None:
        on_success(outcome)
    return outcome


def _require_player(src):
    """非玩家执行返回提示并 None。"""
    if not src.is_player:
        src.reply("§c!!PCH sheet 只能玩家在游戏内执行")
        return None
    return src.player


def _find_row_or_tell(server, player_name, player_uuid, sheet_id, row_id):
    """先 view_sheet 定位行，返回 row dict；失败/行不在时回执并返回 None。

    deliver/done 在执行前需知道行的 mode（lock 走 delivery 绝对值；progress 走 contribute 增量），
    故先拉一次 view。view 成功但行不在 → SHEET_NOT_FOUND；view 返错（404/403/409/None）→ _resolve 回执。
    """
    view_outcome = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id)
    if isinstance(view_outcome, dict):
        for r in view_outcome.get("rows", []):
            if int(r.get("id", -1)) == int(row_id):
                return r
        server.tell(player_name, SHEET_NOT_FOUND)
        return None
    _resolve(server, player_name, view_outcome)
    return None


# === sheet 总览（!!PCH sheet 不带子命令时）===

def _sheet_root(src, ctx):
    """!!PCH sheet（无子命令）→ 列出全部子命令清单。"""
    if not src.is_player:
        src.reply("§c!!PCH sheet 只能玩家在游戏内执行")
        return
    # 命令名列宽：ASCII 命令名按列宽补空格 → 描述起列跨分组对齐（MC 字体下 ASCII 等宽；
    # CJK 参数不进左列，避免破坏对齐）。色板见 McdrPlugin/CLAUDE.md §6。
    name_w = len("release")  # 7，本菜单最长命令名（release / deliver / add/set）

    def _line(name, desc, suggest, hover):
        if isinstance(desc, str):
            desc = RText(desc, color=RColor.gray)
        return RTextList(
            RText("  " + name.ljust(name_w), color=RColor.aqua)
            .c(RAction.suggest_command, suggest)
            .h(RText(hover, color=RColor.yellow)),
            RText("- ", color=RColor.gray),
            desc,
            RText("\n", color=RColor.gray),
        )

    src.reply(RTextList(
        RText(SHEET_HEAD),
        RText(" 子命令（!!PCH sheet <子命令>）：\n", color=RColor.gold),
        RText("查看：\n", color=RColor.aqua),
        _line("list", "列表（默认进行中，自己参与的优先）", "!!PCH sheet list ", "list [-m|-c|-t|-a|-l]  旗标过滤（可组合如 -ma）；或完整 --mine 等"),
        _line("view", "查看表详情与行", "!!PCH sheet view ", "view <表id>  查看指定表"),
        RText("建表 / 改表（拥有者）：\n", color=RColor.aqua),
        _line("create", "新建表", "!!PCH sheet create ", "create <标题>  新建一张表"),
        _line(
            "add", "新建行（拥有者）", "!!PCH sheet add ",
            "add <表id> <物品名> <数量> [lock|progress] [排序]  同名已存在→报错",
        ),
        _line(
            "set", "改行数量（拥有者）", "!!PCH sheet set ",
            "set <表id> <行号> <数量> [排序]  按行号改 need/排序（id 主轴，不改名/模式）",
        ),
        _line("rename", "改表标题", "!!PCH sheet rename ", "rename <表id> <新标题>"),
        _line("delete", "删表", "!!PCH sheet delete ", "delete <表id>"),
        _line("delrow", "删行", "!!PCH sheet delrow ", "delrow <表id> <行号>"),
        _line(
            "addsub", "建子行（拥有者）", "!!PCH sheet addsub ",
            "addsub <表id> <父行号> <倍数> [lock|progress] [排序]  手持该子物品提供 registry_id",
        ),
        _line("delsub", "删子行", "!!PCH sheet delsub ", "delsub <表id> <子行号>"),
        _line(
            "setsub", "改子行", "!!PCH sheet setsub ",
            "setsub <表id> <子行号> <倍数> [lock|progress] [排序]",
        ),
        _line(
            "advance", "阶段流转（拥有者）", "!!PCH sheet advance ",
            "advance <表id> [constructing|archived]  收集→施工→归档",
        ),
        RText("一键 / 物品 id：\n", color=RColor.aqua),
        _line(
            "submit",
            "一键提交（扫背包匹配行）",
            "!!PCH sheet submit ",
            "submit <表id>  扫描背包，按 registry_id 匹配行批量上报（纯申报，不清背包）",
        ),
        _line(
            "addhand",
            "用手持物品建行",
            "!!PCH sheet addhand ",
            "addhand <表id> <数量> [lock|progress] [排序]  手持物物品 id 自动建行",
        ),
        _line(
            "setreg",
            "改行物品 id",
            "!!PCH sheet setreg ",
            "setreg <表id> <行号> [registry_id]  更新指定行的物品 id（缺省走手持物品）",
        ),
        RText("认领 / 交付：\n", color=RColor.aqua),
        _line("claim", "认领", "!!PCH sheet claim ", "claim <表id> <行号>"),
        _line(
            "deliver",
            RTextList(
                RText("上报交付（", color=RColor.gray),
                RText("绝对值", color=RColor.yellow),
                RText("）", color=RColor.gray),
            ),
            "!!PCH sheet deliver ",
            "deliver <表id> <行号> <数量>  数量为绝对值，先 view 看当前",
        ),
        _line("done", "标备齐（lock 模式快捷）", "!!PCH sheet done ", "done <表id> <行号>"),
        _line(
            "progress",
            RTextList(
                RText("调整进度（拥有者，", color=RColor.gray),
                RText("绝对值", color=RColor.yellow),
                RText("）", color=RColor.gray),
            ),
            "!!PCH sheet progress ",
            "progress <表id> <行号> <绝对值>  progress 模式拥有者专用",
        ),
        _line("release", "解除锁定", "!!PCH sheet release ", "release <表id> <行号>  认领人/拥有者"),
        _line("reject", "打回", "!!PCH sheet reject ", "reject <表id> <行号>  认领人/拥有者"),
        RText("通知：\n", color=RColor.aqua),
        _line("notify", "查看自己的通知", "!!PCH sheet notify list ", "notify list  查看未读通知"),
    ))


# === 表级 ===

def _sheet_list(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    _sheet_list_impl(src.get_server(), player_name, mine=False, status="active")


def _sheet_list_default(src, ctx):
    """!!PCH sheet list（无 flags）→ 进行中表（status=active），参与优先。"""
    player_name = _require_player(src)
    if not player_name:
        return
    _sheet_list_impl(src.get_server(), player_name, mine=False, status="active")


# list 旗标简写映射（单字母 → 完整旗标）。
# collecting 与 constructing 首字母同为 c，故 constructing 取 t（construcT）避免冲突。
_LIST_FLAG_SHORT = {
    "m": "--mine",
    "c": "--collecting",
    "t": "--constructing",
    "a": "--archived",
    "l": "--all",
}


def _parse_list_flag_tokens(tokens):
    """解析 list 旗标 token 列表 → (mine, status, unknown_token)。

    支持两种形式（可混用）：
    - 完整：--mine / --collecting / --constructing / --archived / --all
    - 简写：-m / -c / -t / -a / -l；可组合，如 -ma = --mine --archived

    返回 (mine: bool, status: str|None, unknown: str|None)；
    unknown 非 None 表示遇到非法 token（应由调用方回显 SHEET_LIST_FLAG_UNKNOWN）。
    默认 status="active"（进行中 = collecting + constructing），与 _sheet_list_default 一致。
    """
    mine = False
    status = "active"  # 默认进行中（与 _sheet_list_default 一致）
    for token in tokens:
        # 展开简写组合：-ma → ["--mine", "--archived"]
        if token.startswith("--"):
            forms = [token]
        elif token.startswith("-") and len(token) > 1:
            forms = []
            for ch in token[1:]:
                mapped = _LIST_FLAG_SHORT.get(ch)
                if mapped is None:
                    return None, None, token  # 非法简写字母
                forms.append(mapped)
        else:
            return None, None, token  # 裸 token（非旗标）
        for form in forms:
            if form == "--mine":
                mine = True
            elif form == "--collecting":
                status = "collecting"
            elif form == "--constructing":
                status = "constructing"
            elif form == "--archived":
                status = "archived"
            elif form == "--all":
                status = None  # None = 不过滤（后端返回全部）
            else:
                return None, None, token  # 完整形式拼写错
    return mine, status, None


def _sheet_list_flags(src, ctx):
    """!!PCH sheet list <flags...> —— 旗标过滤（支持简写）。

    完整：--mine / --collecting / --constructing / --archived / --all
    简写：-m / -c / -t / -a / -l（可组合，如 -ma）；解析见 _parse_list_flag_tokens。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    flags_str = ctx.get("flags", "")
    tokens = flags_str.split() if flags_str else []
    mine, status, unknown = _parse_list_flag_tokens(tokens)
    if unknown is not None:
        src.get_server().tell(player_name, SHEET_LIST_FLAG_UNKNOWN.format(token=unknown))
        return
    _sheet_list_impl(src.get_server(), player_name, mine=mine, status=status)


def _sheet_list_impl(server, player_name, *, mine: bool, status: str | None):
    @new_thread('pch_sheet_list')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.list_sheets(CONFIG, player_uuid, mine=mine, status=status)

        def _show(data):
            if not data:
                line = SHEET_LIST_MINE + " " + SHEET_LIST_EMPTY if mine else SHEET_LIST_EMPTY
                server.tell(player_name, RTextList(
                    RText(SHEET_HEAD),
                    RText(line),
                    RText("\n"),
                    rtext_button(
                        "[建表]", "!!PCH sheet create ",
                        color=RColor.aqua, hover="新建一张表（续输标题）",
                    ),
                ))
                return
            parts = [RText(SHEET_HEAD)]
            if mine:
                parts.append(RText(SHEET_LIST_MINE))
            parts.append(RText("\n"))
            for s in data:
                sid = s.get("id")
                # 每行渲染阶段标签（format_phase_label 已有 § 颜色码）
                # or "collecting" 防 None 兜底，与 _render_sheet_detail 一致
                status_label = format_phase_label(s.get("status") or "collecting")
                parts.append(RTextList(
                    RText(SHEET_LIST_ITEM.format(
                        id=sid,
                        owner=s.get("owner_name") or "?",
                        status=status_label,
                        title=s.get("title") or "",
                    )),
                    RText("  "),
                    rtext_button(
                        "[查看表格]", f"!!PCH sheet view {sid}",
                        color=RColor.aqua, hover=f"查看 #{sid} 详情",
                    ),
                    RText("\n"),
                ))
            server.tell(player_name, RTextList(*parts))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


# 分页 / 参数解析纯函数见 view_args.py（stdlib-only，单测可按路径独立加载）


def _render_sheet_detail(server, player_name, player_uuid, sheet_id, *, page: int = 1, search: str | None = None):
    """渲染表详情（供 _sheet_view / _sheet_quick / _sheet_view_args 复用）。

    行为：调 ``view_sheet(search)`` → 按 outcome 分支 → 渲染 RText 详情并 tell。
    后端已按查看玩家五档优先级排序 + （search 非空时）过滤；本函数仅做**客户端分页** + 常驻按钮。
    错误路径由 _resolve 统一翻译（404/403/409/422/RATE_LIMITED/REMOVED/None）。
    """
    outcome = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id, search=search)

    def _show(data):
        rows = data.get("rows") or []
        status = str(data.get("status") or "collecting")
        # 软判断拥有者：仅控按钮可见性；真实 RBAC 以后端 403 为准（R-9）。
        # R-5 account 级：viewer_uuids 含当前玩家 → 同 account 任一 UUID 建的表都算 owner；
        # 名字兜底兼容历史数据 / 缺 uuid 场景。
        owner_uuid = str(data.get("owner_uuid") or "")
        owner_name = data.get("owner_name") or ""
        viewer_uuids = {str(u) for u in (data.get("viewer_uuids") or [])}
        is_owner = (bool(player_uuid) and player_uuid in viewer_uuids) or (owner_name == player_name)
        parts = [RText(SHEET_DETAIL_TITLE.format(
            id=data.get("id"),
            title=data.get("title") or "",
            owner=owner_name or "?",
        )), RText("\n")]
        # 项目阶段横幅（三阶段生命周期：collecting/constructing/archived）
        parts.append(RTextList(
            RText("§7[阶段: ", color=RColor.gray),
            RText(format_phase_label(status)),
            RText("§7]§r", color=RColor.gray),
            RText("\n"),
        ))
        # 物品列表主分隔符：无论空表与否都渲染（空表也需「物品列表」标题锚定，
        # 否则（无行）提示顶在阶段横幅下，且与底部「列表管理」分隔符不对称）。
        parts.append(format_section_separator("物品列表"))  # 需求1 主分隔符
        parts.append(RText("\n"))
        if not rows:
            # 空表 / 搜索无匹配：仅居中提示，不渲染快捷栏与分页栏
            hint = SHEET_VIEW_NO_MATCH if search else SHEET_DETAIL_EMPTY
            parts.append(format_centered_text(hint))
        else:
            if search:
                parts.append(format_search_hint(search, sheet_id))  # §7搜索: kw + [清除]
            page_rows, total_pages, _total = paginate_rows(rows, page)
            for r in page_rows:
                parts.append(format_row_clickable(
                    r, sheet_id,
                    is_owner=is_owner,
                    viewer_uuids=viewer_uuids,
                    player_name=player_name,
                    player_uuid=player_uuid,
                ))
            parts.append(RText("\n"))
            parts.append(format_view_footer(sheet_id))  # 常驻公开快捷按钮：[一键提交] [搜索]
            if total_pages > 1:
                parts.append(format_pagination_footer(sheet_id, page, total_pages, search=search))
        if is_owner:
            parts.append(RText("\n"))  # 物品区/底栏 与「列表管理」之间空行
            parts.append(format_section_separator("列表管理"))  # 与「物品列表」对称
            parts.append(RText("\n"))
            parts.append(format_owner_footer(sheet_id, status))
        server.tell(player_name, RTextList(*parts))

    _resolve(server, player_name, outcome, on_success=_show)


def _sheet_view(src, ctx):
    """!!PCH sheet view <sheet_id> —— 查看指定表详情。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('pch_sheet_view')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        _render_sheet_detail(server, player_name, player_uuid, sheet_id)

    _do()


def _sheet_view_args(src, ctx):
    """!!PCH sheet view <sheet_id> <args...> —— 分页/搜索（GreedyText 捕获 -p/-s）。

    解析 -p N / --page N / 裸页码 与 -s <kw> / --search <kw>；非法 token 回显用法。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    tokens = ctx.get("args", "").split() if ctx.get("args") else []
    page, search, unknown = parse_view_args(tokens)
    if unknown is not None:
        server.tell(player_name, SHEET_VIEW_ARG_UNKNOWN.format(token=unknown))
        return

    @new_thread('pch_sheet_view')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        _render_sheet_detail(server, player_name, player_uuid, sheet_id, page=page, search=search)

    _do()


def _sheet_quick(src, ctx):
    """!!sheet / !!PCH sheet last —— 快速重开上次查看的表（无参）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()

    @new_thread('pch_sheet_quick')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.get_last_sheet(CONFIG, player_uuid)

        def _on_last(value):
            sheet_id = value.get("sheet_id")
            if sheet_id is None:
                server.tell(player_name, SHEET_LAST_EMPTY)
                return
            _render_sheet_detail(server, player_name, player_uuid, sheet_id)

        _resolve(server, player_name, outcome, on_success=_on_last)

    _do()


def _sheet_create(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    title = ctx["title"]

    @new_thread('pch_sheet_create')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.create_sheet(CONFIG, player_uuid, title)

        def _show(data):
            server.tell(player_name, SHEET_OK_CREATED.format(id=data.get("id"), title=data.get("title", title)))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_rename(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    title = ctx["title"]

    @new_thread('pch_sheet_rename')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.rename_sheet(CONFIG, player_uuid, sheet_id, title)

        def _show(data):
            server.tell(player_name, SHEET_OK_RENAMED.format(id=data.get("id", sheet_id), title=data.get("title", title)))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_delete(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('pch_sheet_delete')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.delete_sheet(CONFIG, player_uuid, sheet_id)

        def _show(_data):
            server.tell(player_name, SHEET_OK_DELETED.format(id=sheet_id))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


# === 阶段流转（advance）===
# MCDR Literal 节点不存入 context（仅 Argument 子类存值，见 MCDR 命令树文档），
# 故 to=constructing/archived/缺省 三分支各注册一个轻量包装回调，把 to 值硬编码传入。
# S-1：https://docs.mcdreforged.com/en/latest/plugin_dev/command.html §Context

def _sheet_advance_default(src, ctx):
    """!!PCH sheet advance <sheet_id> —— 缺省 to，按后端状态机默认推进。"""
    _sheet_advance_impl(src, ctx, to=None)


def _sheet_advance_to_constructing(src, ctx):
    """!!PCH sheet advance <sheet_id> constructing。"""
    _sheet_advance_impl(src, ctx, to="constructing")


def _sheet_advance_to_archived(src, ctx):
    """!!PCH sheet advance <sheet_id> archived。"""
    _sheet_advance_impl(src, ctx, to="archived")


def _sheet_advance_impl(src, ctx, *, to):
    """advance 共享实现：调 advance_sheet → 按错误码 / 新状态译中文回执。

    advance 端点独有的错误码（先于 _resolve 兜底）：400 非法 to / 503 归档未配置。
    其余（含 409 归档只读、409 状态非法、403/404/429/网络失败）统一复用 _resolve。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('pch_sheet_advance')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.advance_sheet(CONFIG, player_uuid, sheet_id, to)

        # advance 专属错误码先于 _resolve 兜底处理（409 归档/状态非法统一走 _resolve）
        if isinstance(outcome, sheet_client.HttpError):
            err = outcome
            if err.status == 400:
                server.tell(player_name, SHEET_BAD_TARGET)
                return
            if err.status == 503:
                server.tell(player_name, SHEET_ARCHIVE_UNCONFIGURED)
                return

        def _show(data):
            new_status = str(data.get("status") or "")
            if new_status == "archived":
                path = data.get("archived_path") or "(路径缺失)"
                server.tell(player_name, SHEET_OK_ARCHIVED.format(id=data.get("id", sheet_id), path=path))
            elif new_status == "constructing":
                server.tell(player_name, SHEET_OK_ADVANCED_CONSTRUCTING.format(id=data.get("id", sheet_id)))
            else:
                # 兜底：未知新状态（后端未来扩展），回执通用成功 + 当前阶段原值
                server.tell(player_name, "§a项目 #{id} 已流转至 [{phase}]".format(
                    id=data.get("id", sheet_id),
                    phase=data.get("status", "?"),
                ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


# === 行级 ===

def _sheet_upsert(src, ctx):
    """add：按 item_name **新建**行（严格 INSERT，撞名 → 409；issue #20 后不再覆盖同名）。

    mode 可选（默认 lock；字面量 lock/progress），sort 可选（默认 0）。
    注意：``set`` 改行数量走 ``_sheet_set``（按 row_id），不再复用本回调。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    item = ctx["item"]
    need = ctx["need"]
    mode = 1 if ctx.get("mode") == "progress" else 0
    sort = ctx.get("sort", 0)

    @new_thread('pch_sheet_upsert')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.upsert_row(CONFIG, player_uuid, sheet_id, item, need, mode, sort)

        def _show(data):
            mode_label = "progress" if mode else "lock"
            server.tell(player_name, SHEET_OK_ROW_ADDED.format(
                item=data.get("item_name", item),
                need=format_qty_safe(data.get("need_qty", need)),
                mode=mode_label,
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_set(src, ctx):
    """set：按 row_id 更新已有行的 need/sort（id 主轴，不改 item_name；issue #20）。

    mode 字面量节点不存入 ctx（MCDR 已知限制），故本命令只改 need（+ 可选 sort），
    mode 保持不变（改 mode 请用 Web 编辑器）。行不存在 → 后端 404 → 回执。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]
    need = ctx["need"]
    sort = ctx.get("sort")  # Integer 节点存入 ctx；缺省 None → 不改

    @new_thread('pch_sheet_set')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=None, need=need, mode=None, sort=sort, row_id=row_id,
        )

        def _show(data):
            mode_label = "progress" if data.get("mode") == 1 else "lock"
            server.tell(player_name, SHEET_OK_ROW_UPDATED.format(
                item=data.get("item_name", "?"),
                need=format_qty_safe(data.get("need_qty", need)),
                mode=mode_label,
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_delrow(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_delrow')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.delete_row(CONFIG, player_uuid, sheet_id, row_id)

        def _show(_data):
            server.tell(player_name, SHEET_OK_ROW_DELETED.format(row_id=row_id))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_addsub(src, ctx, *, mode=None):
    """子物品：在父行下建子行（issue #19）。

    参数：sheet_id, parent_row_id, qty_per_unit(>0), [mode], [sort]。
    registry_id 取自手持物品（依赖 minecraft_data_api）。
    父 lock → 子强制 lock；父 progress → 子可 lock/progress（默认继承）。
    need_qty 被忽略（后端派生 = qty_per_unit × 父行.need_qty）。

    mode 由命令树分支闭包烘入（MCDR Literal 不入 ctx，见 _sheet_advance_* 注释；
    S-1：https://docs.mcdreforged.com/en/latest/code_references/command.html §Literal）：
    None=裸 addsub → mode 不下发，后端建子行时继承父行；1=progress；0=lock。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    parent_row_id = ctx["parent_row_id"]
    qty_per_unit = ctx["qty_per_unit"]
    if qty_per_unit is None or qty_per_unit <= 0:
        server.tell(player_name, "§c倍数必须 > 0")
        return
    sort = ctx.get("sort", 0)

    @new_thread('pch_sheet_addsub')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return

        # 手持扫描取 registry_id（复用 addhand 范本）
        api = server.get_plugin_instance("minecraft_data_api")
        if api is None:
            server.tell(player_name, SHEET_NO_DATA_API)
            return
        held = scanner.read_held_item(api, player_name)
        if held is None:
            server.tell(player_name, SHEET_ADDHAND_NEED_HAND)
            return
        registry_id = held[0]

        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=None, need=None, mode=mode, sort=sort,
            registry_id=registry_id, parent_row_id=parent_row_id, qty_per_unit=qty_per_unit,
        )

        def _show(data):
            # 从后端返回取实际生效 mode（mode=None 继承父行时也能正确回显）；与 setsub 一致，None 安全
            mode_label = "progress" if data.get("mode") == 1 else "lock"
            server.tell(player_name, "§a已建子行 [{reg}] ×{qty}/件（{mode}）".format(
                reg=data.get("registry_id", registry_id),
                qty=data.get("qty_per_unit", qty_per_unit),
                mode=mode_label,
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_delsub(src, ctx):
    """删子行：复用 DELETE /rows/{row_id}（与 delrow 同端点，子行 row_id）。

    约束：只能删子行（parent_row_id 非空）；删父行由 delrow 处理。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_delsub')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.delete_row(CONFIG, player_uuid, sheet_id, row_id)

        def _show(_data):
            server.tell(player_name, "§a已删子行 #{row_id}".format(row_id=row_id))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_setsub(src, ctx, *, mode=None):
    """改子行：按 row_id 更新 qty_per_unit（mode/sort 可选）。

    子行更新可改 qty_per_unit/mode/sort；need_qty 被忽略（后端重算派生）。

    mode 由命令树分支闭包烘入（MCDR Literal 不入 ctx，见 _sheet_advance_* 注释）：
    None=裸 setsub → mode 不下发，后端不改 mode（避免误翻 contributors）；1=progress；0=lock。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]
    qty_per_unit = ctx["qty_per_unit"]
    if qty_per_unit is None or qty_per_unit <= 0:
        server.tell(player_name, "§c倍数必须 > 0")
        return
    sort = ctx.get("sort")  # Integer 节点存入 ctx；缺省 None → 不改

    @new_thread('pch_sheet_setsub')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=None, need=None, mode=mode, sort=sort,
            row_id=row_id, qty_per_unit=qty_per_unit,
        )

        def _show(data):
            mode_label = "progress" if data.get("mode") == 1 else "lock"
            server.tell(player_name, "§a已更新子行 #{row_id} [{reg}] ×{qty}/件（{mode}）".format(
                row_id=row_id,
                reg=data.get("registry_id", "?"),
                qty=data.get("qty_per_unit", qty_per_unit),
                mode=mode_label,
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_claim(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_claim')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.claim_row(CONFIG, player_uuid, sheet_id, row_id)

        def _show(data):
            server.tell(player_name, SHEET_OK_CLAIMED.format(item=data.get("item_name", "")))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_deliver(src, ctx):
    """上报交付：progress 行走增量上交（任意玩家，不要求认领）；lock 行走绝对值（认领人）。

    qty 语义随模式：progress=本次新增量（≥1）；lock=delivered_qty 绝对值。故需先 view 拉行判 mode。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]
    qty = ctx["qty"]

    @new_thread('pch_sheet_deliver')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        row = _find_row_or_tell(server, player_name, player_uuid, sheet_id, row_id)
        if row is None:
            return
        mode = int(row.get("mode", 0))
        if mode == 1:  # progress：增量上交，任意玩家，不查认领人
            outcome = sheet_client.contribute_row(CONFIG, player_uuid, sheet_id, row_id, qty)
        else:  # lock：绝对值，认领人维护
            outcome = sheet_client.deliver_row(CONFIG, player_uuid, sheet_id, row_id, qty)

        def _show(data):
            server.tell(player_name, SHEET_OK_DELIVERED.format(
                item=data.get("item_name", ""),
                delivered=format_qty_safe(data.get("delivered_qty", qty)),
                need=format_qty_safe(data.get("need_qty", "?")),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_progress(src, ctx):
    """拥有者调整 progress 行进度（绝对值，可增可减）。先 view 验 mode==progress。

    delivered_qty 是绝对值（与后端 schema ge=0 一致）；lock 行 → 后端 409，故先 view 拦截。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]
    qty = ctx["delivered_qty"]

    @new_thread('pch_sheet_progress')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        row = _find_row_or_tell(server, player_name, player_uuid, sheet_id, row_id)
        if row is None:
            return
        if int(row.get("mode", 0)) != 1:  # 非 progress：后端会 409，提前拦截省一次往返
            server.tell(player_name, SHEET_CONFLICT)
            return
        outcome = sheet_client.set_row_progress(CONFIG, player_uuid, sheet_id, row_id, qty)

        def _show(data):
            server.tell(player_name, SHEET_OK_PROGRESS_SET.format(
                item=data.get("item_name", ""),
                delivered=format_qty_safe(data.get("delivered_qty", qty)),
                need=format_qty_safe(data.get("need_qty", "?")),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_done(src, ctx):
    """标备齐快捷：lock→deliver(need) 绝对值；progress→contribute(need-delivered) 补齐差额。

    先 view 拿 mode/need/delivered，再按模式分流。progress 已齐（delta=0）则直接回显。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_done')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        row = _find_row_or_tell(server, player_name, player_uuid, sheet_id, row_id)
        if row is None:
            return
        need = int(row.get("need_qty", 0))
        delivered = int(row.get("delivered_qty", 0))
        mode = int(row.get("mode", 0))
        if mode == 1:  # progress：补齐差额（增量上交）；已齐则无需操作
            delta = max(need - delivered, 0)
            if delta == 0:
                server.tell(player_name, SHEET_OK_DELIVERED.format(
                    item=row.get("item_name", ""), delivered=format_qty_safe(delivered), need=format_qty_safe(need)))
                return
            outcome = sheet_client.contribute_row(CONFIG, player_uuid, sheet_id, row_id, delta)
        else:  # lock：绝对值设 need
            outcome = sheet_client.deliver_row(CONFIG, player_uuid, sheet_id, row_id, need)

        def _show(data):
            server.tell(player_name, SHEET_OK_DELIVERED.format(
                item=data.get("item_name", ""),
                delivered=format_qty_safe(data.get("delivered_qty", need)),
                need=format_qty_safe(data.get("need_qty", need)),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_release(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_release')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.release_row(CONFIG, player_uuid, sheet_id, row_id)

        def _show(data):
            server.tell(player_name, SHEET_OK_RELEASED.format(item=data.get("item_name", "")))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_reject(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('pch_sheet_reject')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.reject_row(CONFIG, player_uuid, sheet_id, row_id)

        def _show(data):
            server.tell(player_name, SHEET_OK_REJECTED.format(item=data.get("item_name", "")))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_notify_list(src, ctx):
    """主动拉取自己近期未读通知并分页回显（不上报 ack，留给轮询器统一 ack）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()

    @new_thread('pch_sheet_notify_list')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.pending_notifications(CONFIG, player_uuid, CONFIG.notify_max_per_poll)

        def _show(items):
            if not items:
                server.tell(player_name, RTextList(RText(SHEET_HEAD), RText(SHEET_NOTIFY_EMPTY)))
                return
            parts = [RText(SHEET_HEAD), RText("\n")]
            for n in items:
                parts.append(RText(format_notification(n)))
                parts.append(RText("\n"))
            server.tell(player_name, RTextList(*parts))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


# === 一键提交 / 手持建行 / 改 registry_id ===

def _sheet_submit_impl(server, player_name, player_uuid, sheet_id):
    """一键提交共享体：扫背包 → 按 registry_id 匹配 → 串行上报 → 汇总回执。

    纯申报语义（RS-4 衍生）：只读背包 + HTTP 上报，绝不清背包、不 data merge / clear。
    单行失败不阻断其他行；汇总回执一次 tell（绿色 done/累计 + 黄色跳过原因）。
    复用 scanner.skip_is_ready 折叠已备齐/进度已满的跳过行（玩家本次无可操作），
    scanner.skip_is_noise 折叠与本人无关的跳过行（他人认领的 lock 行、progress 未携带项）。
    """
    api = server.get_plugin_instance("minecraft_data_api")
    if api is None:
        server.tell(player_name, SHEET_SUBMIT_NO_API)
        return
    inventory = scanner.scan_inventory(api, player_name)
    view = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id)
    if not isinstance(view, dict):
        _resolve(server, player_name, view)
        return
    if str(view.get("status", "")) == "archived":
        # 归档终态只读：整体短路，避免逐行写返 409 刷「交付失败（状态变化）」
        server.tell(player_name, SHEET_ARCHIVED_READONLY)
        return
    rows = view.get("rows") or []
    viewer_uuids = {str(u) for u in (view.get("viewer_uuids") or [])}
    actions = scanner.match_rows(rows, inventory, player_uuid=player_uuid, viewer_uuids=viewer_uuids)

    done_lines: list = []
    shown_skips: list = []
    ready_folded = 0  # 折叠计数（已备齐/进度已满，玩家本次无可操作）
    folded = 0  # 折叠计数（与本人无关的跳过行）

    for action in actions:
        if action.action == "deliver":
            # lock 行已认领且自己为认领人，直接 deliver(need) 绝对值 → done
            deliv_out = sheet_client.deliver_row(
                CONFIG, player_uuid, sheet_id, action.row_id, action.qty)
            if isinstance(deliv_out, dict):
                done_lines.append(RText(SHEET_SUBMIT_DONE_LINE.format(
                    item=action.item_name, qty=format_qty_safe(action.qty))))
            else:
                shown_skips.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                    item=action.item_name, reason="交付失败（状态变化）")))
        elif action.action == "contribute":
            contrib_out = sheet_client.contribute_row(
                CONFIG, player_uuid, sheet_id, action.row_id, action.qty)
            if isinstance(contrib_out, dict):
                delivered = int(contrib_out.get("delivered_qty", 0))
                need = int(contrib_out.get("need_qty", 0))
                done_lines.append(RText(SHEET_SUBMIT_PROGRESS_LINE.format(
                    item=action.item_name, delivered=format_qty_safe(delivered), need=format_qty_safe(need))))
            else:
                shown_skips.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                    item=action.item_name, reason="上交失败（状态变化）")))
        else:  # skip
            # 已备齐/进度已满优先归「备齐」桶（更准确），其次与本人无关折叠，其余逐行
            if scanner.skip_is_ready(action):
                ready_folded += 1
            elif scanner.skip_is_noise(action):
                folded += 1
            else:
                shown_skips.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                    item=action.item_name, reason=action.reason)))

    # 汇总回执（一次 tell，避免刷屏）
    if not done_lines and not shown_skips and ready_folded == 0 and folded == 0:
        server.tell(player_name, RTextList(RText(SHEET_SUBMIT_HEAD), RText(SHEET_SUBMIT_NO_ROWS)))
        return
    parts: list = [RText(SHEET_SUBMIT_HEAD), RText("\n")]
    if done_lines:
        parts.append(RText(SHEET_SUBMIT_DONE_HEAD.format(n=len(done_lines))))
        for ln in done_lines:
            parts.append(ln)
            parts.append(RText("\n"))
    if shown_skips:
        parts.append(RText(SHEET_SUBMIT_SKIP_HEAD.format(n=len(shown_skips))))
        for ln in shown_skips:
            parts.append(ln)
            parts.append(RText("\n"))
    if ready_folded > 0:
        parts.append(RText(SHEET_SUBMIT_READY_FOLDED_LINE.format(n=ready_folded)))
    if folded > 0:
        parts.append(RText(SHEET_SUBMIT_FOLDED_LINE.format(n=folded)))
    server.tell(player_name, RTextList(*parts))


def _submit_safe(server, player_name, player_uuid, sheet_id):
    """``_sheet_submit_impl`` 的异常兜底（RS-6 失败回执）。

    ``_sheet_submit_impl`` 跑在 ``@new_thread`` 内，未捕获异常会被 MCDR 静默吞掉
    （仅后台日志、玩家零回执）——这正是历史上漏 import 导致 NameError 时
    「一点提交信息都没有」的同根 failure mode。本 wrapper 确保任何意外异常
    都给玩家一条失败回执并记完整堆栈，绝不静默。
    """
    try:
        _sheet_submit_impl(server, player_name, player_uuid, sheet_id)
    except Exception as e:
        server.tell(player_name, SHEET_SUBMIT_FAIL.format(err=e))
        server.logger.exception("_sheet_submit_impl failed")


def _sheet_submit_oneclick(src, ctx):
    """!!PCH sheet submit <id> / !!submit <id> —— 指定表格一键提交。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('pch_sheet_submit')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        _submit_safe(server, player_name, player_uuid, sheet_id)

    _do()


def _submit_quick(src, ctx):
    """!!submit —— 重开上次查看的表并一键提交（无参）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()

    @new_thread('pch_sheet_submit')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.get_last_sheet(CONFIG, player_uuid)

        def _on_last(value):
            sheet_id = value.get("sheet_id")
            if sheet_id is None:
                server.tell(player_name, SHEET_LAST_EMPTY)
                return
            _submit_safe(server, player_name, player_uuid, sheet_id)

        _resolve(server, player_name, outcome, on_success=_on_last)

    _do()


def _sheet_addhand(src, ctx):
    """用手持物品的 registry_id 建行（item_name 由后端据 registry_id 翻译补中文）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    need = ctx["need"]
    mode = 1 if ctx.get("mode") == "progress" else 0
    sort = ctx.get("sort", 0)

    @new_thread('pch_sheet_addhand')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        api = server.get_plugin_instance("minecraft_data_api")
        if api is None:
            server.tell(player_name, SHEET_NO_DATA_API)
            return
        held = scanner.read_held_item(api, player_name)
        if held is None:
            server.tell(player_name, SHEET_ADDHAND_NEED_HAND)
            return
        registry_id = held[0]
        # item_name=None：后端按 registry_id 走翻译表补中文（A2）
        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=None, need=need, mode=mode, sort=sort,
            registry_id=registry_id,
        )

        def _show(data):
            mode_label = "progress" if mode else "lock"
            server.tell(player_name, SHEET_OK_ADDHAND.format(
                item=data.get("item_name") or registry_id,
                need=format_qty_safe(data.get("need_qty", need)),
                mode=mode_label,
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_setreg(src, ctx):
    """改指定行的 registry_id（按行现有 item_name 定位，其余字段透传，仅 registry_id 是新值）。

    registry_id 可选：缺省时读玩家手持物品的 registry_id（与 addhand 一致）。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]
    registry_id = ctx.get("registry_id")

    @new_thread('pch_sheet_setreg')
    def _do():
        nonlocal registry_id
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        # registry_id 缺省 → 读手持物品（与 addhand 一致）
        if not registry_id:
            api = server.get_plugin_instance("minecraft_data_api")
            if api is None:
                server.tell(player_name, SHEET_NO_DATA_API)
                return
            held = scanner.read_held_item(api, player_name)
            if held is None:
                server.tell(player_name, SHEET_SETREG_NEED_HAND)
                return
            registry_id = held[0]
        row = _find_row_or_tell(server, player_name, player_uuid, sheet_id, row_id)
        if row is None:
            return
        # 按 row_id 更新：仅传 registry_id，need/mode/sort 不传 → 后端部分更新保留原值（issue #20）
        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=None, need=None, mode=None, sort=None,
            registry_id=registry_id, row_id=row_id,
        )

        def _show(data):
            server.tell(player_name, SHEET_OK_SETREG.format(
                row_id=row_id,
                registry_id=data.get("registry_id", registry_id),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()
