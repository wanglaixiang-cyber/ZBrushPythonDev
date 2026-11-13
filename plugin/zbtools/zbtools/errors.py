"""统一异常类型。"""

from __future__ import annotations


class PluginError(RuntimeError):
    """插件层面的可预期错误(会在界面上提示,而不是抛到 ZBrush)。

    所有功能都用它;``ui.guard`` 捕获后弹一行提示,其它异常才打印堆栈。
    """
