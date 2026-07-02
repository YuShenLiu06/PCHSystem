"""集中消息/色彩常量。色彩标准见 McdrPlugin/CLAUDE.md §6。"""
from mcdreforged.api.rtext import RText, RColor, RStyle, RAction

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
