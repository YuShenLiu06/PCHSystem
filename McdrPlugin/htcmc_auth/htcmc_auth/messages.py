"""集中消息/色彩常量。色彩标准见 McdrPlugin/CLAUDE.md §6。"""
from typing import Any

from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle, RAction

from .text_layout import (
    CHAT_LINE_PX,
    center_leading,
    right_align_suffix,
    text_width_px,
)

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
SHEET_OK_PROGRESS_SET = "§a已调整进度 [{item}] {delivered}/{need}（拥有者，绝对值）"
SHEET_DELIVER_HINT = "§7提示：deliver 的数量是绝对值，先 !!PCH sheet view 看当前 delivered 再决定"
SHEET_NOTIFY_EMPTY = "§7暂无未读通知"
# 一键提交 / 手持建行 / 改 registry_id
SHEET_NO_DATA_API = "§c未安装 minecraft_data_api 插件，无法扫描背包"
SHEET_ADDHAND_NEED_HAND = "§c请先手持物品再使用 addhand"
SHEET_SETREG_NEED_HAND = "§c请先手持物品或提供 registry_id 参数"
SHEET_OK_ADDHAND = "§a已用手持物品新建/更新行：{item} ×{need}（{mode}）"
SHEET_OK_SETREG = "§a已更新行 #{row_id} 的物品 id 为 {registry_id}"
# submit 汇总回执段
SHEET_SUBMIT_HEAD = "§6§l[PCH 一键提交]§r"
SHEET_SUBMIT_NO_API = "§c未安装 minecraft_data_api 插件，无法扫描背包"
SHEET_SUBMIT_NO_ROWS = "§7表中无可匹配的行（需行已配 registry_id 且背包满足条件）"
SHEET_SUBMIT_DONE_LINE = "§a  {item} ×{qty} §7→ §a完成"
SHEET_SUBMIT_PROGRESS_LINE = "§a  {item} §7累计 §b{delivered}/{need}"
SHEET_SUBMIT_SKIP_LINE = "§e  {item}：{reason}"
SHEET_SUBMIT_DONE_HEAD = "§a已标记 {n} 行：\n"
SHEET_SUBMIT_SKIP_HEAD = "§e跳过 {n} 行：\n"


def _status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, "§f")


# progress 行贡献者显示上限：受 MC 聊天行宽限制，至多 2 位 + 省略号（后端已按 contributed_qty desc 排序）
_CONTRIB_DISPLAY_MAX = 2


def _format_contributors(contributors: list) -> str:
    """progress 行认领者列：按贡献量降序取前 N 位 + 省略号；无贡献者显「未认领」。"""
    names = [c.get("player_name") for c in (contributors or []) if c.get("player_name")]
    if not names:
        return "未认领"
    if len(names) <= _CONTRIB_DISPLAY_MAX:
        return "、".join(names)
    return "、".join(names[:_CONTRIB_DISPLAY_MAX]) + "…"


def format_row_line(row: dict) -> str:
    """格式化单行（RowDetail）为游戏内一行文本。

    progress 行：认领者列改显贡献者（按贡献量降序，至多 2 位 + 省略号）；无则「未认领」。
    lock 行：显 claimant_name。
    """
    mode = _MODE_LABEL.get(int(row.get("mode", 0)), str(row.get("mode")))
    status = str(row.get("status", ""))
    if int(row.get("mode", 0)) == 1:
        claimant = _format_contributors(row.get("contributors"))
    else:
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


# === 可点击操作按钮（suggest_command；MC 1.19+ run_command 失效，统一 suggest）===

def rtext_button(label: str, command: str, *, color=RColor.aqua, hover: str = "") -> RText:
    """可点击操作按钮：点击向聊天栏填入 command（suggest_command），玩家回车执行。

    hover 非空时悬停显示灰色提示。色板见 McdrPlugin/CLAUDE.md §6：
    green=正向 / red=破坏性 / yellow=谨慎 / aqua=中性。
    """
    rt = RText(label, color=color).c(RAction.suggest_command, command)
    if hover:
        rt.h(RText(hover, color=RColor.gray))
    return rt


def format_section_separator(title: str = "物品列表") -> RText:
    """主分隔符：════ title ════（gold + bold 双线，title 居中）。返回不带 ``\\n``。

    用 ``RColor.gold + RStyle.bold``（McdrPlugin/CLAUDE.md §6 色板「重要/标题」语义）。
    两侧 ═ 数量按目标行宽与 title 像素宽求出（向下取整确保不超宽）；
    **粗体每字符 advance +1px**，故 title / 空格 / ═ 的宽度均按粗体态（``§l`` 前缀）估算。
    ═（U+2550 Box Drawing）advance 按 ``CJK_ADVANCE_PX`` 估算（经验值，真机校准）。
    """
    # 粗体态宽度：§l 前缀让 text_width_px 自动对每字符 +1px（见 text_layout.bold 规则）
    title_px = text_width_px(f"§l{title}")
    space_px = text_width_px("§l ")
    bar_px = text_width_px("§l═")
    n_each = max(0, (CHAT_LINE_PX - title_px - 2 * space_px) // 2 // bar_px)
    bar = "═" * n_each
    return RText(f"{bar} {title} {bar}", color=RColor.gold).set_styles(RStyle.bold)



def format_row_clickable(
    row: dict,
    sheet_id,
    *,
    is_owner: bool = False,
    player_name: str = "",
    player_uuid: str = "",
) -> RTextList:
    """格式化单行为可点击 RTextList：行文本 + 按状态/模式/查看者权限追加尾部操作按钮。

    按钮显隐对齐后端 RBAC（红线 R-9：真实权限以后端 403 为准，此处仅控可见性）。
    查看者身份用 UUID 为主、名字兜底（离线模式名↔UUID 1:1，见 R-5）：
      is_claimant = (claimant_uuid == player_uuid) or (claimant_name == player_name)
    lock 模式（单认领人状态机）：
      open      → [认领]（任意）
      claimed   → [标备齐]（仅认领人；delivery 端点 owner 不豁免）/ [解除]（认领人 or owner）
      done      → [打回]（认领人 or owner）
    progress 模式（无认领，任意玩家增量上交；无认领人 → 仅 owner 可解除/重置）：
      open      → [交付]（任意，直接贡献）
      claimed   → [交付][标备齐]（任意；协作）/ [解除]（owner）
      done      → [解除]（owner 重置进度；reject 对 progress 返 409 故无打回）
    拥有者在任意状态额外追加 [调整进度]（仅 progress 行，绝对值覆写）与 [删行]。
    其余状态/未知 → 仅行文本（无按钮）。
    """
    rid = row.get("id")
    status = str(row.get("status", ""))
    mode = int(row.get("mode", 0))
    # 身份锚 = UUID（行上存的 claimant_uuid 是权威锚；claimant_name 仅展示，可变）；
    # 名字兜底防 payload 偶发缺 uuid。默认 player_*="" → is_claimant=False（fails closed）。
    is_claimant = (
        (bool(player_uuid) and str(row.get("claimant_uuid") or "") == player_uuid)
        or (bool(player_name) and row.get("claimant_name") == player_name)
    )
    can_release_or_reject = is_claimant or is_owner
    buttons = []
    if status == "open":
        if mode == 1:  # progress：任意玩家直接上交（无需认领）
            buttons.append(rtext_button(
                "[交付]", f"!!PCH sheet deliver {sheet_id} {rid} ",
                color=RColor.aqua,
                hover="上报本次上交数量（增量，末尾补数量后回车）",
            ))
        else:  # lock：认领
            buttons.append(rtext_button(
                "[认领]", f"!!PCH sheet claim {sheet_id} {rid}",
                color=RColor.green, hover="认领此行（open→claimed）",
            ))
    elif status == "claimed":
        if mode == 1:  # progress：可继续上交 + 一次性补齐（协作，任意玩家）
            buttons.append(rtext_button(
                "[交付]", f"!!PCH sheet deliver {sheet_id} {rid} ",
                color=RColor.aqua,
                hover="上报本次上交数量（增量，末尾补数量后回车）",
            ))
            buttons.append(rtext_button(
                "[标备齐]", f"!!PCH sheet done {sheet_id} {rid}",
                color=RColor.green, hover="一次性补齐到需求量（progress）",
            ))
        elif is_claimant:  # lock：标备齐仅认领人（delivery 端点 owner 不豁免，sheets.py:472）
            buttons.append(rtext_button(
                "[标备齐]", f"!!PCH sheet done {sheet_id} {rid}",
                color=RColor.green, hover="标记此行备齐（lock 快捷）",
            ))
        if can_release_or_reject:  # 解除：认领人自放 或 owner（progress 无认领人 → 仅 owner）
            buttons.append(rtext_button(
                "[解除]", f"!!PCH sheet release {sheet_id} {rid}",
                color=RColor.yellow,
                hover="解除认领/重置进度（→open，progress 清贡献者）",
            ))
    elif status == "done":
        if mode == 1:  # progress：无打回，owner 用解除重置进度（清贡献者）
            if can_release_or_reject:
                buttons.append(rtext_button(
                    "[解除]", f"!!PCH sheet release {sheet_id} {rid}",
                    color=RColor.yellow, hover="重置进度（done→open，清贡献者名单）",
                ))
        elif can_release_or_reject:  # lock：打回（认领人自取消 或 owner）
            buttons.append(rtext_button(
                "[打回]", f"!!PCH sheet reject {sheet_id} {rid}",
                color=RColor.red, hover="打回（done→claimed，delivered 归零，可重做）",
            ))
    if is_owner and mode == 1:  # progress 行 owner 专用：绝对值覆写进度（可增可减）
        buttons.append(rtext_button(
            "[调整进度]", f"!!PCH sheet progress {sheet_id} {rid} ",
            color=RColor.yellow,
            hover="直接修正进度（绝对值，可增可减；末尾补数量后回车）",
        ))
    if is_owner:
        buttons.append(rtext_button(
            "[改ID]", f"!!PCH sheet setreg {sheet_id} {rid} ",
            color=RColor.yellow,
            hover="改行 registry_id（直接回车=手持物品；或空格后输入新 registry_id）",
        ))
        buttons.append(rtext_button(
            "[删行]", f"!!PCH sheet delrow {sheet_id} {rid}",
            color=RColor.red, hover="删除此行（拥有者）",
        ))

    line_text = format_row_line(row)
    if not buttons:
        # 无操作按钮（如非认领人看他人 claimed 行）：直接行文本 + 换行，不填充
        return RTextList(RText(line_text), RText("\n"))
    # 按钮组右对齐到聊天行右边界：行文本 + 计算填充 + 按钮组（按钮间单空格）。
    # 行已超宽时 right_align_suffix 兜底返双空格（与旧行为一致），绝不返回负数空格。
    suffix_text = " ".join(b.to_plain_text() for b in buttons)
    pad = right_align_suffix(line_text, suffix_text)
    seg = [RText(line_text), RText(pad)]
    for i, b in enumerate(buttons):
        if i > 0:
            seg.append(RText(" "))
        seg.append(b)
    seg.append(RText("\n"))
    return RTextList(*seg)


def _center_button_row(buttons, *, pad_color=RColor.gray) -> RTextList:
    """把一组按钮在聊天行内居中：前置填充空格（按按钮组整体宽度算），按钮间单空格，末尾换行。

    兼容任意数量按钮（1 个或多个），便于未来向 owner 栏追加按钮时自动居中。
    超宽（按钮组宽 ≥ 行宽）时 ``center_leading`` 返空串，按钮自然左对齐不崩。
    """
    if not buttons:
        return RTextList(RText("\n", color=pad_color))
    group_text = " ".join(b.to_plain_text() for b in buttons)
    leading = center_leading(group_text)
    parts = [RText(leading, color=pad_color)]
    for i, b in enumerate(buttons):
        if i > 0:
            parts.append(RText(" ", color=pad_color))
        parts.append(b)
    parts.append(RText("\n", color=pad_color))
    return RTextList(*parts)


def format_submit_footer(sheet_id) -> RTextList:
    """公开快捷栏（所有查看者可见）：一键提交——扫背包按 registry_id 匹配行批量上交（纯申报，不清背包）。

    按钮居中（走 ``_center_button_row``）。
    """
    return _center_button_row([
        rtext_button(
            "[一键提交]", f"!!PCH sheet submit {sheet_id}",
            color=RColor.aqua,
            hover="扫背包按 registry_id 匹配行批量上交（纯申报，不清背包；lock 行需已认领）",
        ),
    ])


def format_owner_footer(sheet_id) -> RTextList:
    """拥有者底部管理栏：新增物品（默认走 addhand，手持建行）/ 改标题 / 删表。

    按钮组整体居中（走 ``_center_button_row``），未来追加按钮自动居中。
    """
    return _center_button_row([
        rtext_button(
            "[新增物品]", f"!!PCH sheet addhand {sheet_id} ",
            color=RColor.aqua,
            hover="用手持物品建行（续输：数量 [lock|progress] [排序]）",
        ),
        rtext_button(
            "[改标题]", f"!!PCH sheet rename {sheet_id} ",
            color=RColor.aqua, hover="修改表标题（续输新标题）",
        ),
        rtext_button(
            "[删表]", f"!!PCH sheet delete {sheet_id}",
            color=RColor.red, hover="删除整表（级联删行，谨慎）",
        ),
    ])


# === notifications 文案（按 category 映射）===
# category 清单（与后端 notification_service 一致）：
#   sheet_claimed / sheet_delivered / sheet_done / sheet_released /
#   sheet_rejected / sheet_qty_changed / sheet_row_deleted /
#   sheet_progress_changed（owner 调整 progress 进度→贡献者）/ sheet_progress_reset（owner 解除/换模式清贡献者→贡献者）
# §码：成功 §a、提醒 §e、打回/删除 §c
_NOTIFY_TEMPLATES = {
    "sheet_claimed": "§a{actor} 认领了 [{sheet_title}] 的 [{item}]",
    "sheet_delivered": "§e{actor} 上报交付 {delivered}/{need} [{sheet_title}] 的 [{item}]",
    "sheet_done": "§a{actor} 已备齐 [{sheet_title}] 的 [{item}]",
    "sheet_released": "§e{actor} 取消了对 [{sheet_title}] 的 [{item}] 的认领",
    "sheet_rejected": "§c[{sheet_title}] 的 [{item}] 已打回，delivered 归零，可重做",
    "sheet_qty_changed": "§e[{sheet_title}] 的 [{item}] 所需数量变为 {new}（原 {old}），delivered 已按需封顶",
    "sheet_row_deleted": "§c[{sheet_title}] 的 [{item}] 已被拥有者删除，认领取消",
    "sheet_progress_changed": "§e[{sheet_title}] 的 [{item}] 进度已被 {actor} 调整为 {new}/{need}（原 {old}）",
    "sheet_progress_reset": "§e[{sheet_title}] 的 [{item}] 进度已被 {actor} 重置，贡献清空",
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
        # payload 形如 {sheet_title, item_name, actor_name, old, new, delivered, need}
        try:
            text = tpl.format(
                actor=payload.get("actor_name") or "某人",
                item=payload.get("item_name") or "?",
                sheet_title=payload.get("sheet_title") or "?",
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
