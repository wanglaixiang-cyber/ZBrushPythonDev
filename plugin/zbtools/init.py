"""ZBrush 插件入口(ZBrush 扫描 PYTHONPATH 下的 init.py,或 ZBRUSH_PLUGIN_PATH 里的 *.py)。

只做两件事:把插件目录放进 sys.path、调用装配函数。装配逻辑在 zbtools/app.py。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from zbtools.app import register  # noqa: E402  (必须在 sys.path 处理之后)

register()
