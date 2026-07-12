"""tests 包初始化：在导入被测模块前安装 mcdreforged / uuid_api_remake 替身。"""
from . import _stubs  # noqa: F401

_stubs.install_stubs()

# 让被测包可被 import：将插件顶层目录（含内层 pch_system 包）加入 sys.path
# 结构：McdrPlugin/pch_system/pch_system/<源码>，故顶层 = McdrPlugin/pch_system
import os
import sys

_PLUGIN_TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pch_system"))
if _PLUGIN_TOP not in sys.path:
    sys.path.insert(0, _PLUGIN_TOP)
