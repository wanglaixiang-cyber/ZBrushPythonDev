"""ZBrush 工具集插件(项目内部代号 zbtools)。

结构约定:
    app.py        装配:建面板 → 遍历 features 注册
    ui.py         通用 UI + 提示框(功能模块只通过它建控件/弹提示)
    settings.py   配置 + 状态版本
    errors.py     统一异常
    notes.py      提示框样式
    zbapi/        对 ZBrush 的薄封装(共用,不含业务):screen / model / canvas / clipboard
    features/     一个功能一个包,互不 import(见 docs/DEVELOPMENT.md)

插件显示名与功能名解耦:改 PLUGIN_TITLE / 各 feature 的 title 即可,不用动 import。
"""

from __future__ import annotations

VERSION = "0.2.4"
#: 插件在 ZBrush 右侧菜单栏里的名字(可自由改)
PLUGIN_TITLE = "ZBTools"
#: 与功能无关的 ASCII 兜底名(排查中文兼容性时可切)
PLUGIN_TITLE_ASCII = "ZBTools"

__all__ = ["VERSION", "PLUGIN_TITLE", "PLUGIN_TITLE_ASCII"]
