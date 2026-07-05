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
from .config import HtcmcAuthConfig
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
    SHEET_DETAIL_TITLE,
    SHEET_DETAIL_EMPTY,
    rtext_button,
    format_row_clickable,
    format_owner_footer,
    SHEET_OK_CREATED,
    SHEET_OK_RENAMED,
    SHEET_OK_DELETED,
    SHEET_OK_ROW_SET,
    SHEET_OK_ROW_DELETED,
    SHEET_OK_CLAIMED,
    SHEET_OK_DELIVERED,
    SHEET_OK_PROGRESS_SET,
    SHEET_OK_RELEASED,
    SHEET_OK_REJECTED,
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
)

# 由 __init__.py 在 on_load 中注入
CONFIG: HtcmcAuthConfig = HtcmcAuthConfig()


def configure(cfg: HtcmcAuthConfig) -> None:
    global CONFIG
    CONFIG = cfg


# === 工具：解析 sheet_client 返回，统一回执；返回处理后的 Python 值（成功 dict/list）或 None（已回执/失败）===

def _resolve(server, player_name, outcome, *, on_success=None):
    """分支解析返回。on_success(value) 在成功时被调用做自定义回执；返回 True 表示已回执。

    成功（dict/list）→ 调 on_success；on_success 返回 None 表示未自行回执，本函数兜底。
    RATE_LIMITED / REMOVED / HttpError / None → 各自回执，返回 None。
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
        _line("list", "表格列表（--mine 仅自己拥有）", "!!PCH sheet list ", "list [--mine]  列出表格"),
        _line("view", "查看表详情与行", "!!PCH sheet view ", "view <表id>  查看指定表"),
        RText("建表 / 改表（拥有者）：\n", color=RColor.aqua),
        _line("create", "新建表", "!!PCH sheet create ", "create <标题>  新建一张表"),
        _line(
            "add/set", "增改行（拥有者）", "!!PCH sheet add ",
            "add/set <表id> <物品> <数量> [lock|progress] [排序]",
        ),
        _line("rename", "改表标题", "!!PCH sheet rename ", "rename <表id> <新标题>"),
        _line("delete", "删表", "!!PCH sheet delete ", "delete <表id>"),
        _line("delrow", "删行", "!!PCH sheet delrow ", "delrow <表id> <行号>"),
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
    _sheet_list_impl(src.get_server(), player_name, mine=bool(ctx.get("mine")))


def _sheet_list_mine(src, ctx):
    """带 --mine 的分支。"""
    player_name = _require_player(src)
    if not player_name:
        return
    _sheet_list_impl(src.get_server(), player_name, mine=True)


def _sheet_list_impl(server, player_name, *, mine: bool):
    @new_thread('htcmc_sheet_list')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.list_sheets(CONFIG, player_uuid, mine=mine)

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
                parts.append(RTextList(
                    RText(SHEET_LIST_ITEM.format(
                        id=sid,
                        owner=s.get("owner_name") or "?",
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


def _sheet_view(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('htcmc_sheet_view')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id)

        def _show(data):
            rows = data.get("rows") or []
            # 软判断拥有者：仅控按钮可见性；真实 RBAC 以后端 403 为准（R-9）。
            # 身份锚 = UUID（owner_uuid 为主），名字兜底兼容历史数据 / 缺 uuid 场景。
            owner_uuid = str(data.get("owner_uuid") or "")
            owner_name = data.get("owner_name") or ""
            is_owner = (bool(player_uuid) and owner_uuid == player_uuid) or (owner_name == player_name)
            parts = [RText(SHEET_DETAIL_TITLE.format(
                id=data.get("id"),
                title=data.get("title") or "",
                owner=owner_name or "?",
            )), RText("\n")]
            if not rows:
                parts.append(RText(SHEET_DETAIL_EMPTY))
                parts.append(RText("\n"))
            else:
                for r in rows:
                    parts.append(format_row_clickable(
                        r, sheet_id,
                        is_owner=is_owner,
                        player_name=player_name,
                        player_uuid=player_uuid,
                    ))
            if is_owner:
                parts.append(format_owner_footer(sheet_id))
            server.tell(player_name, RTextList(*parts))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_create(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    title = ctx["title"]

    @new_thread('htcmc_sheet_create')
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

    @new_thread('htcmc_sheet_rename')
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

    @new_thread('htcmc_sheet_delete')
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


# === 行级 ===

def _sheet_upsert(src, ctx):
    """add/set 共用：upsert 行。mode 可选（默认 lock；字面量 lock/progress），sort 可选（默认 0）。"""
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    item = ctx["item"]
    need = ctx["need"]
    mode = 1 if ctx.get("mode") == "progress" else 0
    sort = ctx.get("sort", 0)

    @new_thread('htcmc_sheet_upsert')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        outcome = sheet_client.upsert_row(CONFIG, player_uuid, sheet_id, item, need, mode, sort)

        def _show(data):
            mode_label = "progress" if mode else "lock"
            server.tell(player_name, SHEET_OK_ROW_SET.format(
                item=data.get("item_name", item),
                need=data.get("need_qty", need),
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

    @new_thread('htcmc_sheet_delrow')
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


def _sheet_claim(src, ctx):
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]
    row_id = ctx["row_id"]

    @new_thread('htcmc_sheet_claim')
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

    @new_thread('htcmc_sheet_deliver')
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
                delivered=data.get("delivered_qty", qty),
                need=data.get("need_qty", "?"),
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

    @new_thread('htcmc_sheet_progress')
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
                delivered=data.get("delivered_qty", qty),
                need=data.get("need_qty", "?"),
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

    @new_thread('htcmc_sheet_done')
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
                    item=row.get("item_name", ""), delivered=delivered, need=need))
                return
            outcome = sheet_client.contribute_row(CONFIG, player_uuid, sheet_id, row_id, delta)
        else:  # lock：绝对值设 need
            outcome = sheet_client.deliver_row(CONFIG, player_uuid, sheet_id, row_id, need)

        def _show(data):
            server.tell(player_name, SHEET_OK_DELIVERED.format(
                item=data.get("item_name", ""),
                delivered=data.get("delivered_qty", need),
                need=data.get("need_qty", need),
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

    @new_thread('htcmc_sheet_release')
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

    @new_thread('htcmc_sheet_reject')
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

    @new_thread('htcmc_sheet_notify_list')
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

def _sheet_submit_oneclick(src, ctx):
    """一键提交：扫描背包，按 registry_id 匹配表中行，串行上报。

    纯申报语义（RS-4 衍生）：只读背包 + HTTP 上报，绝不清背包、不 data merge / clear。
    单行失败不阻断其他行；汇总回执一次 tell（绿色 done/累计 + 黄色跳过原因）。
    """
    player_name = _require_player(src)
    if not player_name:
        return
    server = src.get_server()
    sheet_id = ctx["sheet_id"]

    @new_thread('htcmc_sheet_submit')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, SHEET_UUID_FAIL.format(err=e))
            return
        api = server.get_plugin_instance("minecraft_data_api")
        if api is None:
            server.tell(player_name, SHEET_SUBMIT_NO_API)
            return
        inventory = scanner.scan_inventory(api, player_name)
        view = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id)
        if not isinstance(view, dict):
            _resolve(server, player_name, view)
            return
        rows = view.get("rows") or []
        actions = scanner.match_rows(rows, inventory, player_uuid=player_uuid)

        done_lines: list = []
        skip_lines: list = []
        for action in actions:
            if action.action == "deliver":
                # lock 行已认领且自己为认领人，直接 deliver(need) 绝对值 → done
                deliv_out = sheet_client.deliver_row(
                    CONFIG, player_uuid, sheet_id, action.row_id, action.qty)
                if isinstance(deliv_out, dict):
                    done_lines.append(RText(SHEET_SUBMIT_DONE_LINE.format(
                        item=action.item_name, qty=action.qty)))
                else:
                    skip_lines.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                        item=action.item_name, reason="交付失败（状态变化）")))
            elif action.action == "contribute":
                contrib_out = sheet_client.contribute_row(
                    CONFIG, player_uuid, sheet_id, action.row_id, action.qty)
                if isinstance(contrib_out, dict):
                    delivered = int(contrib_out.get("delivered_qty", 0))
                    need = int(contrib_out.get("need_qty", 0))
                    done_lines.append(RText(SHEET_SUBMIT_PROGRESS_LINE.format(
                        item=action.item_name, delivered=delivered, need=need)))
                else:
                    skip_lines.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                        item=action.item_name, reason="上交失败（状态变化）")))
            else:  # skip
                skip_lines.append(RText(SHEET_SUBMIT_SKIP_LINE.format(
                    item=action.item_name, reason=action.reason)))

        # 汇总回执（一次 tell，避免刷屏）
        if not done_lines and not skip_lines:
            server.tell(player_name, RTextList(RText(SHEET_SUBMIT_HEAD), RText(SHEET_SUBMIT_NO_ROWS)))
            return
        parts: list = [RText(SHEET_SUBMIT_HEAD), RText("\n")]
        if done_lines:
            parts.append(RText(SHEET_SUBMIT_DONE_HEAD.format(n=len(done_lines))))
            for ln in done_lines:
                parts.append(ln)
                parts.append(RText("\n"))
        if skip_lines:
            parts.append(RText(SHEET_SUBMIT_SKIP_HEAD.format(n=len(skip_lines))))
            for ln in skip_lines:
                parts.append(ln)
                parts.append(RText("\n"))
        server.tell(player_name, RTextList(*parts))

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

    @new_thread('htcmc_sheet_addhand')
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
                need=data.get("need_qty", need),
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

    @new_thread('htcmc_sheet_setreg')
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
        item_name = row.get("item_name")
        outcome = sheet_client.upsert_row(
            CONFIG, player_uuid, sheet_id,
            item=item_name,
            need=int(row.get("need_qty", 0)),
            mode=int(row.get("mode", 0)),
            sort=int(row.get("sort_order", 0)),
            registry_id=registry_id,
        )

        def _show(data):
            server.tell(player_name, SHEET_OK_SETREG.format(
                row_id=row_id,
                registry_id=data.get("registry_id", registry_id),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()
