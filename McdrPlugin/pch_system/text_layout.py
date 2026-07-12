"""tellraw 文本像素宽度估算 + 对齐填充工具。

Minecraft tellraw 无原生「右对齐 / 居中」能力，本模块用字符 advance 像素宽度表 + 空格填充
近似实现聊天行内的对齐，供 messages.py 的 sheet view 渲染调用。

== 字符 advance 宽度依据（Minecraft 1.20.4 默认字体，联网核实）==
- 空格 U+0020 = 4px（space.json）
- ASCII（U+0021–U+007E）= ascii.png 字形宽 +1；多数 6px，窄字符（!'"(),./:;[]`ilt{|} 等）2-5px，@=7px
- 粗体（§l / RStyle.bold）= 原字形右偏 1px 重绘 → 每字符 advance +1px
- HUD 聊天默认行宽 = 320px（options.chatWidth 滑块 0–1 → 40–320px）
- CJK / Box Drawing / 全角符号 ≈ 9px（unifont 全宽 cell，size_overrides right:15）

== §码处理 ==
- § + 下一字符整体 0 宽（颜色/样式控制码不占显示宽度）
- §l 开粗体态；§r 及任何颜色码（§0-9、§a-f）重置粗体态（与游戏渲染一致：颜色段隐式清粗体）
- §k §m §n §o 不影响宽度也不改粗体态

== 局限（重要）==
1. ``CJK_ADVANCE_PX = 9`` 是经验值，未真机像素级校准；中文密集行可能偏移 ±5-10px。
2. 玩家改 ``chatWidth < 1.0`` 或 GUI scale 极大会导致行换行、对齐塌陷（tellraw 固有限制，无解）。
3. ``forceUnicodeFont = true`` 会改变 CJK advance；本表按 false（默认）校准。
4. 自定义字体资源包不兼容（按 vanilla 1.20.4 字体估算）。
5. 行文本（长中文物品名 + 多按钮）超 320px 时，``right_align_suffix`` 触发兜底退化为双空格
   （仍可读，放弃右对齐），绝不返回负数空格。

== 真机校准方法 ==
测试服（mc-test 容器）发一条已知文本（如 10 个中文字符），截图量像素 → 反推单字符 advance，
更新 ``CJK_ADVANCE_PX``。同理可校准 Box Drawing 字符（═ ─）。

来源：
- https://mcasset.cloud/1.20.4/assets/minecraft/font/include/space.json
- https://mcasset.cloud/1.20.4/assets/minecraft/font/include/default.json
- https://mcasset.cloud/1.20.4/assets/minecraft/font/include/unifont.json
- https://minecraft.wiki/w/Font
- https://github.com/IdreesInc/Monocraft/issues/167 （粗体 +1px）
- https://github.com/sp614x/optifine/issues/1959 （chat width 40-320px）
"""
from __future__ import annotations

# === 像素宽度常量 ===

SPACE_ADVANCE_PX: int = 4
"""空格（U+0020）advance，源自 1.20.4 space.json，权威值。"""

CJK_ADVANCE_PX: int = 9
"""CJK / Box Drawing / 全角符号 advance，经验值（unifont 全宽 cell），需真机校准。"""

CHAT_LINE_PX: int = 320
"""HUD 聊天默认行宽（options.chatWidth=1.0）。玩家改窄会导致换行。"""

# ASCII（U+0021–U+007E）advance 表：仅列「≠ 6」的字符，其余查表默认 6。
# 源自社区测量 ascii.png 字形宽 +1（1.13+ 稳定至今，1.20.4 同布局）。
ASCII_ADVANCE_PX: dict[str, int] = {
    " ": 4,           # 走 SPACE_ADVANCE_PX 同值，放表里统一查表
    "!": 2,
    '"': 5,
    "'": 3,
    "(": 4,
    ")": 4,
    ",": 2,
    ".": 2,
    ":": 2,
    ";": 2,
    "<": 5,
    ">": 5,
    "@": 7,
    "I": 4,
    "[": 4,
    "]": 4,
    "`": 3,
    "c": 5,
    "f": 5,
    "i": 2,
    "k": 5,
    "l": 3,
    "r": 5,
    "t": 4,
    "{": 4,
    "|": 2,
    "}": 4,
}

# §码颜色字符集合（遇到即重置粗体态）
_SECTION_COLOR_CODES = frozenset("0123456789abcdef")


def text_width_px(text: str) -> int:
    """估算 ``text`` 在 MC 1.20.4 默认字体下的像素 advance 宽度。

    - §控制码（§+下一字符）整体 0 宽，但解析以追踪粗体态
    - §l 开粗体；§r / 颜色码（§0-9a-f）关粗体（与游戏渲染一致）
    - 粗体态下每字符 +1px
    - 非 ASCII 字符（CJK / Box Drawing / 全角）统一按 ``CJK_ADVANCE_PX`` 估算

    >>> text_width_px("泥土")  # 2 × 9
    18
    >>> text_width_px("[交付]")  # [ + 交 + 付 + ] = 4 + 9 + 9 + 4
    26
    """
    width = 0
    bold = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "§":
            # §码：整体 0 宽（孤立 § 也忽略），仅解析以追踪 bold 态
            if i + 1 < n:
                code = text[i + 1].lower()
                if code == "l":
                    bold = True
                elif code == "r" or code in _SECTION_COLOR_CODES:
                    bold = False
                # §k §m §n §o 不影响宽度也不改 bold 态
                i += 2
            else:
                i += 1
            continue
        if ord(ch) < 0x80:
            w = ASCII_ADVANCE_PX.get(ch, 6)
        else:
            w = CJK_ADVANCE_PX
        if bold:
            w += 1
        width += w
        i += 1
    return width


def make_padding(px: int) -> str:
    """返回填满 ``px`` 像素所需的空格字符串（向下取整，确保不超宽）。

    ``px <= 0`` 返空串（兜底，绝不返回负数空格）。

    >>> make_padding(8)
    '  '
    >>> make_padding(10)  # 向下取整：2 空格 = 8px（少 2px，视觉无感）
    '  '
    >>> make_padding(0)
    ''
    """
    if px <= 0:
        return ""
    return " " * (px // SPACE_ADVANCE_PX)


def right_align_suffix(
    prefix_text: str,
    suffix_text: str,
    *,
    target_px: int = CHAT_LINE_PX,
    min_gap_px: int = 8,
) -> str:
    """返回 ``prefix_text`` 与 ``suffix_text`` 之间的填充空格，使 suffix 右对齐到 ``target_px``。

    gap = target_px - width(prefix) - width(suffix)。
    - gap < ``min_gap_px``（行已超宽或刚好挤一起）→ 返 ``"  "``（双空格兜底，避免负数/挤压）
    - 否则返 ``make_padding(gap)``

    >>> right_align_suffix("abc", "[交付]", target_px=50)  # abc=17, gap=50-17-26=7 < 8
    '  '
    """
    gap = target_px - text_width_px(prefix_text) - text_width_px(suffix_text)
    if gap < min_gap_px:
        return "  "
    return make_padding(gap)


def center_leading(text: str, *, target_px: int = CHAT_LINE_PX) -> str:
    """返回使 ``text`` 在行内居中所需的**前置**填充空格（按钮独占一行，右侧自然空白）。

    gap = target_px - width(text)；``gap <= 0`` 返空串（超宽不补）；否则返 ``make_padding(gap // 2)``。

    >>> center_leading("ab", target_px=20)  # ab=12, gap=8, gap//2=4px → 1 空格
    ' '
    """
    gap = target_px - text_width_px(text)
    if gap <= 0:
        return ""
    return make_padding(gap // 2)
