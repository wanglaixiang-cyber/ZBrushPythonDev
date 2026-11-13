"""开发用加载器:在 ZBrush 里热更新插件(不用配环境变量)。

用法:ZBrush > ZScript 面板 > Python Scripting 子面板 > Load > 本文件

每次 Load 都会丢掉旧模块再导入,所以改完代码直接再 Load 一次即可。
"""

from __future__ import annotations

import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(PROJECT, "plugin", "zbtools")

if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

# 热重载:丢掉上一次加载的 zbtools.* (保留叶子模块,避免类对象被重建)
_KEEP = {"zbtools.errors", "zbtools.notes"}
for name in [m for m in list(sys.modules) if m == "zbtools" or m.startswith("zbtools.")]:
    if name not in _KEEP:
        del sys.modules[name]

from zbtools.app import register  # noqa: E402

register()
print("[zbtools] 已加载,改完代码再 Load 本文件即可热更新")
