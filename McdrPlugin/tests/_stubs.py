"""测试公共 stub：在缺真实 mcdreforged / uuid_api_remake 的环境下，
注入最小替身使被测模块可 import。

约定：
- mcdreforged.api.command：Literal/Text/Integer/QuotableText 链式 .then/.runs 返回 self
- mcdreforged.api.rtext：RText/RTextList/RColor/RStyle/RAction 真实可用（§码渲染为字符串）
- mcdreforged.api.decorator.new_thread：passthrough（同步执行被装饰函数并返回其结果）
- mcdreforged.api.utils.Serializable：基类，提供从 dict 反序列化的最小能力
- mcdreforged.api.all.PluginServerInterface：占位
- uuid_api_remake.get_uuid(name)：确定性推导 uuid 字符串
"""
import sys
import types
from unittest import mock


def install_stubs() -> None:
    # === mcdreforged.api.rtext ===
    rtext = types.ModuleType("mcdreforged.api.rtext")

    class RColor:
        green = "green"
        red = "red"
        yellow = "yellow"
        aqua = "aqua"
        gold = "gold"
        gray = "gray"
        blue = "blue"

    class RStyle:
        bold = "bold"
        italic = "italic"
        underline = "underline"
        strikethrough = "strikethrough"
        obfuscated = "obfuscated"

    class RAction:
        open_url = "open_url"
        suggest_command = "suggest_command"
        run_command = "run_command"

    class RText:
        def __init__(self, text="", color=None, styles=None):
            self.text = text if isinstance(text, str) else str(text)
            self.color = color
            self.styles = styles or set()

        def set_styles(self, *styles):
            for s in styles:
                if isinstance(s, (list, tuple, set)):
                    self.styles.update(s)
                else:
                    self.styles.add(s)
            return self

        def c(self, action, value):
            self._click_action = action
            self._click_value = value
            return self

        def h(self, *args):
            # set_hover_text 简化替身：仅缓存参数，不影响 __str__
            self._hover = args
            return self

        def __add__(self, other):
            return RTextList(self, other)

        def __str__(self):
            return self.text

        def to_plain_text(self):
            # MCDR 标准 API：返回纯文本（忽略 click/hover），宽度计算用
            return self.text

    class RTextList:
        def __init__(self, *parts):
            self.parts = list(parts)

        def append(self, p):
            self.parts.append(p)

        def __str__(self):
            return "".join(str(p) for p in self.parts)

        def to_plain_text(self):
            return "".join(
                p.to_plain_text() if hasattr(p, "to_plain_text") else str(p)
                for p in self.parts
            )

    rtext.RText = RText
    rtext.RTextList = RTextList
    rtext.RColor = RColor
    rtext.RStyle = RStyle
    rtext.RAction = RAction

    # === mcdreforged.api.command ===
    command = types.ModuleType("mcdreforged.api.command")

    class _Node:
        def __init__(self, name=""):
            self.name = name

        def runs(self, func):
            self._runs = func
            return self

        def then(self, child):
            return self

    class Literal(_Node):
        pass

    class Text(_Node):
        pass

    class Integer(_Node):
        pass

    class QuotableText(_Node):
        pass

    command.Literal = Literal
    command.Text = Text
    command.Integer = Integer
    command.QuotableText = QuotableText

    # === mcdreforged.api.decorator ===
    decorator = types.ModuleType("mcdreforged.api.decorator")

    def new_thread(name):
        def _wrap(func):
            def _inner(*args, **kwargs):
                return func(*args, **kwargs)
            return _inner
        return _wrap

    decorator.new_thread = new_thread

    # === mcdreforged.api.utils ===
    utils = types.ModuleType("mcdreforged.api.utils")

    class Serializable:
        @classmethod
        def deserialize(cls, data):
            obj = cls()
            for k, v in data.items():
                setattr(obj, k, v)
            return obj

    utils.Serializable = Serializable

    # === mcdreforged.api.all ===
    api_all = types.ModuleType("mcdreforged.api.all")
    api_all.PluginServerInterface = object
    api_all.Literal = Literal
    api_all.Text = Text
    api_all.Integer = Integer
    api_all.QuotableText = QuotableText

    # 组装 mcdreforged 包树
    mcdreforged = types.ModuleType("mcdreforged")
    api = types.ModuleType("mcdreforged.api")
    mcdreforged.api = api
    sys.modules["mcdreforged"] = mcdreforged
    sys.modules["mcdreforged.api"] = api
    sys.modules["mcdreforged.api.rtext"] = rtext
    sys.modules["mcdreforged.api.command"] = command
    sys.modules["mcdreforged.api.decorator"] = decorator
    sys.modules["mcdreforged.api.utils"] = utils
    sys.modules["mcdreforged.api.all"] = api_all

    # === uuid_api_remake ===
    uuid_api = types.ModuleType("uuid_api_remake")

    def get_uuid(name):
        import hashlib
        h = hashlib.sha1(name.encode("utf-8")).hexdigest()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    uuid_api.get_uuid = get_uuid
    sys.modules["uuid_api_remake"] = uuid_api
