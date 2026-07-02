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
from mcdreforged.api.rtext import RText, RTextList, RColor

from . import sheet_client
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
    format_row_line,
    SHEET_OK_CREATED,
    SHEET_OK_RENAMED,
    SHEET_OK_DELETED,
    SHEET_OK_ROW_SET,
    SHEET_OK_ROW_DELETED,
    SHEET_OK_CLAIMED,
    SHEET_OK_DELIVERED,
    SHEET_OK_RELEASED,
    SHEET_OK_REJECTED,
    SHEET_DELIVER_HINT,
    SHEET_NOTIFY_EMPTY,
    format_notification,
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


# === sheet 总览（!!PCH sheet 不带子命令时）===

def _sheet_root(src, ctx):
    """!!PCH sheet（无子命令）→ 列出全部子命令清单。"""
    if not src.is_player:
        src.reply("§c!!PCH sheet 只能玩家在游戏内执行")
        return
    src.reply(RTextList(
        RText(SHEET_HEAD),
        RText("子命令（输入 !!PCH sheet <子命令>）：\n", color=RColor.gold),
        RText("  list [--mine]                列表（--mine 仅自己拥有）\n"),
        RText("  view <表id>                  查看表详情\n"),
        RText("  create <标题>                建表\n"),
        RText("  add/set <表id> <物品> <数量> [lock|progress] [排序]  增改行（拥有者）\n"),
        RText("  delrow <表id> <行号>         删行（拥有者）\n"),
        RText("  claim <表id> <行号>          认领\n"),
        RText("  deliver <表id> <行号> <数量> 上报交付（绝对值）\n"),
        RText("  done <表id> <行号>           标备齐（lock 模式快捷）\n"),
        RText("  release <表id> <行号>        解除锁定（认领人/拥有者）\n"),
        RText("  reject <表id> <行号>         打回（认领人/拥有者）\n"),
        RText("  notify list                  查看自己的通知\n", color=RColor.gray),
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
                server.tell(player_name, RTextList(RText(SHEET_HEAD), RText(line)))
                return
            parts = [RText(SHEET_HEAD)]
            if mine:
                parts.append(RText(SHEET_LIST_MINE))
            parts.append(RText("\n"))
            for s in data:
                parts.append(RText(SHEET_LIST_ITEM.format(
                    id=s.get("id"),
                    owner=s.get("owner_name") or "?",
                    title=s.get("title") or "",
                ) + "\n"))
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
            parts = [RText(SHEET_DETAIL_TITLE.format(
                id=data.get("id"),
                title=data.get("title") or "",
                owner=data.get("owner_name") or "?",
            )), RText("\n")]
            if not rows:
                parts.append(RText(SHEET_DETAIL_EMPTY))
            else:
                for r in rows:
                    parts.append(RText(format_row_line(r) + "\n"))
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
    """add/set 共用：upsert 行。mode 由子命令字面量（lock/progress）映射，sort 可选。"""
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
        outcome = sheet_client.deliver_row(CONFIG, player_uuid, sheet_id, row_id, qty)

        def _show(data):
            server.tell(player_name, SHEET_OK_DELIVERED.format(
                item=data.get("item_name", ""),
                delivered=data.get("delivered_qty", qty),
                need=data.get("need_qty", "?"),
            ))

        _resolve(server, player_name, outcome, on_success=_show)

    _do()


def _sheet_done(src, ctx):
    """lock 模式快捷标备齐：先 view 拿 need，再 deliver(need)。

    若为 progress 模式，后端按 delivered≥need 判定；这里 deliver 一个安全的大值
    （need，由后端封顶）——需先拉 need。失败回执走 _resolve。
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
        # 1) 先取行详情拿 need
        view_outcome = sheet_client.view_sheet(CONFIG, player_uuid, sheet_id)
        need = None
        if isinstance(view_outcome, dict):
            for r in view_outcome.get("rows", []):
                if int(r.get("id", -1)) == int(row_id):
                    need = int(r.get("need_qty", 0))
                    break
        if need is None:
            # view 失败或行不在：复用 _resolve 把 view 的错误回执（404/403/409/None）
            _resolve(server, player_name, view_outcome)
            return
        # 2) deliver need（绝对值；后端对 ≥need 自动转 done）
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
