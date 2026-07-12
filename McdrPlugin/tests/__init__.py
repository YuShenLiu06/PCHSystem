"""tests 包初始化：在导入被测模块前安装 mcdreforged / uuid_api_remake 替身。"""
from . import _stubs  # noqa: F401

_stubs.install_stubs()

# 让被测包可被 import：将含 pch_system 包的目录加入 sys.path
# 结构（拍平后）：McdrPlugin/pch_system/<源码>（包），故顶层 = McdrPlugin
import os
import sys

_PLUGIN_TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PLUGIN_TOP not in sys.path:
    sys.path.insert(0, _PLUGIN_TOP)
