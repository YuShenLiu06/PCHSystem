"""集中消息/色彩常量。色彩标准见 McdrPlugin/CLAUDE.md §6。"""
from typing import Any

from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle, RAction

# === § 码风格（旧代码兼容）===
MSG_PREFIX_GRAY = "§7"
MSG_SUCCESS = "§a"
MSG_ERROR = "§c"
MSG_WARN = "§e"
MSG_INFO = "§b"
MSG_TITLE = "§6§l"
MSG_LINK = "§9§l"
MSG_RESET = "§r"

# === RText 风格（新代码推荐）===


def rtext_link(url: str, label: str = "[点击此处打开网页登录]") -> RText:
    return (
        RText(label, color=RColor.green)
        .set_styles(RStyle.bold)
        .c(RAction.open_url, url)
    )


def rtext_info(text: str) -> RText:
    return RText(text, color=RColor.gray)


def rtext_warn(text: str) -> RText:
    return RText(text, color=RColor.yellow)


def rtext_error(text: str) -> RText:
    return RText(text, color=RColor.red)


# === 模板消息 ===
NOT_IMPL_TEMPLATE = "§e该功能（{name}）正在开发中§r §7详见 Docs/architecture/services/mcdr-plugin.md"
LOGIN_RATE_LIMITED = "§e操作太频繁，请稍后再试"
LOGIN_SERVICE_DOWN = "§c登录服务暂不可用，请稍后重试"
LOGIN_REMOVED = "§c你已被移出白名单"
LOGIN_UUID_FAIL = "§c获取 UUID 失败: {err}"
PLAYER_ONLY = "§c{cmd} 只能玩家在游戏内执行"

# === sheets 命令回执 ===
SHEET_SERVICE_DOWN = "§c表格服务暂不可用，请稍后重试"
SHEET_RATE_LIMITED = "§e操作太频繁，请稍后再试"
SHEET_FORBIDDEN = "§c权限不足或非认领人（真实权限以后端为准）"
SHEET_NOT_FOUND = "§c表或行不存在"
SHEET_CONFLICT = "§c状态非法（如对已备齐行认领、对未认领行打回），请先 !!PCH sheet view 查看"
SHEET_BAD_REQUEST = "§c参数有误: {detail}"
SHEET_UUID_FAIL = "§c获取 UUID 失败: {err}"
SHEET_HEAD = "§6§l[PCH 表格]§r"
SHEET_LIST_EMPTY = "§7（无表格）"
SHEET_LIST_ITEM = "§a#{id} §7[{owner}] §f{title}"
SHEET_LIST_MINE = "§7（仅看自己）"
SHEET_DETAIL_TITLE = "§6§l#{id} {title}§r §7[owner: {owner}]"
SHEET_DETAIL_EMPTY = "§7（无行）"
# mode 0=lock 1=progress；status open/claimed/done
_MODE_LABEL = {0: "lock", 1: "progress"}
_STATUS_COLOR = {"open": "§7", "claimed": "§e", "done": "§a"}
SHEET_ROW_LINE = "{status_c}#{row_id} §f{item} §7[{mode}] §b{delivered}/{need} §7{claimant}"
SHEET_OK_CREATED = "§a已建表 #{id} {title}"
SHEET_OK_RENAMED = "§a已改标题 #{id} → {title}"
SHEET_OK_DELETED = "§a已删表 #{id}"
SHEET_OK_ROW_SET = "§a已 upsert 行 [{item}] need={need} mode={mode}"
SHEET_OK_ROW_DELETED = "§a已删行 #{row_id}"
SHEET_OK_CLAIMED = "§a已认领 [{item}]（lock 模式请用 !!PCH sheet done 标备齐）"
SHEET_OK_DELIVERED = "§a已上报交付 [{item}] {delivered}/{need}"
SHEET_OK_RELEASED = "§a已解除锁定 [{item}]"
SHEET_OK_REJECTED = "§a已打回/取消备齐 [{item}]（delivered 归零）"
SHEET_DELIVER_HINT = "§7提示：deliver 的数量是绝对值，先 !!PCH sheet view 看当前 delivered 再决定"
SHEET_NOTIFY_EMPTY = "§7暂无未读通知"


def _status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, "§f")


def format_row_line(row: dict) -> str:
    """格式化单行（RowDetail）为游戏内一行文本。"""
    mode = _MODE_LABEL.get(int(row.get("mode", 0)), str(row.get("mode")))
    status = str(row.get("status", ""))
    claimant = row.get("claimant_name") or "未认领"
    return SHEET_ROW_LINE.format(
        status_c=_status_color(status),
        row_id=row.get("id"),
        item=row.get("item_name"),
        mode=mode,
        delivered=row.get("delivered_qty", 0),
        need=row.get("need_qty", 0),
        claimant=claimant,
    )


# === notifications 文案（按 category 映射）===
# category 清单（与后端 notification_service 一致）：
#   sheet_claimed / sheet_delivered / sheet_done / sheet_released /
#   sheet_rejected / sheet_qty_changed / sheet_row_deleted
# §码：成功 §a、提醒 §e、打回/删除 §c
_NOTIFY_TEMPLATES = {
    "sheet_claimed": "§a{actor} 认领了 [{item}]",
    "sheet_delivered": "§e{actor} 上报交付 {delivered}/{need} [{item}]",
    "sheet_done": "§a{actor} 已备齐 [{item}]",
    "sheet_released": "§e{actor} 取消了对 [{item}] 的认领",
    "sheet_rejected": "§c[{item}] 已打回，delivered 归零，可重做",
    "sheet_qty_changed": "§e[{item}] 所需数量变为 {new}（原 {old}），delivered 已按需封顶",
    "sheet_row_deleted": "§c[{item}] 已被拥有者删除，认领取消",
}
_NOTIFY_DEFAULT = "§7{title}"


def format_notification(n: dict) -> Any:
    """把一条通知记录格式化为 RTextBase（游戏内 tell）。

    优先用后端给的 title/body；否则按 category 用 payload 渲染中文文案。
    n 字段（后端 /notifications/pending 契约）：id, category, title, body, payload{...}, created_at。
    """
    category = n.get("category") or ""
    payload = n.get("payload") or {}
    tpl = _NOTIFY_TEMPLATES.get(category)
    if tpl is not None:
        # payload 形如 {item_name, actor_name, old, new, delivered, need}
        try:
            text = tpl.format(
                actor=payload.get("actor_name") or "某人",
                item=payload.get("item_name") or "?",
                delivered=payload.get("delivered", "?"),
                need=payload.get("need", "?"),
                old=payload.get("old", "?"),
                new=payload.get("new", "?"),
            )
        except Exception:
            text = n.get("title") or _NOTIFY_DEFAULT.format(title="(通知)")
        return RText(text)
    # 未知 category：回退到 title
    title = n.get("title") or "(通知)"
    body = n.get("body")
    if body:
        return RTextList(RText(title, color=RColor.gray), RText(f" - {body}", color=RColor.gray))
    return RText(title, color=RColor.gray)
