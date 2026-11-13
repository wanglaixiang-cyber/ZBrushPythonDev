"""插件装配:建面板 → 遍历功能注册 → 打印结果。

这里只做装配,不放任何业务逻辑;功能逻辑在各 features/<key>/ 里。
"""

from __future__ import annotations

from typing import Any

import zbrush.commands as zbc

from . import PLUGIN_TITLE, VERSION, settings, ui
from .features import FEATURES
from .zbapi import screen

#: 当前生效的插件配置(测试与功能模块都可读)
CFG: dict[str, Any] = {}


def _sync_screen(cfg: dict[str, Any]) -> str:
    """探测屏幕并把参数存进配置(插件级,所有功能共用)。"""
    screen.detect_displays(cfg, force=True)
    try:
        info = screen.resolve_screen(cfg)
    except Exception as exc:  # noqa: BLE001
        return f"(屏幕探测不可用:{exc})"
    cfg["diagonal_inch"] = round(info["diagonal_inch"], 3)
    cfg["ppi"] = round(info["ppi"], 2)
    logical = float(info["logical_ppi"] or 0.0)
    cfg["pixel_scale"] = round(float(info["ppi"] / logical), 3) if logical else 2.0
    settings.save(cfg)
    return f"{info['name']} {info['diagonal_inch']:.2f}\" ppi={info['ppi']:.1f}"


def _add_group_header(panel: ui.Panel, title: str) -> None:
    """在子面板顶部放一条不可点的分组标题(禁用按钮)。

    官方文档:add_subpalette 的 title_mode=1 在这版 ZBrush 里仍带折叠三角,
    所以用 title_mode=2(无标题栏)+ 禁用按钮来当分组占位符。
    """
    path = f"{panel.sub}:---- {title} ----"
    try:
        zbc.add_button(path, "", None)
        zbc.disable(path)                       # 灰掉、点不动,只当标题
    except Exception as exc:                                            # noqa: BLE001
        print(f"[ZBTools] 分组标题添加失败({title}):{exc}")


def register() -> bool:
    """ZBrush 启动/加载插件时调用:装配面板并注册所有功能。"""
    global CFG
    palette = PLUGIN_TITLE
    CFG = cfg = settings.load()

    print(f"=== {palette} v{VERSION}(状态版本 {settings.STATE_VERSION})===")
    if not ui.ensure_palette(palette, [feature.title for feature in FEATURES]):
        ui.note(f"警告:{palette} 面板创建失败,详见 Python 控制台\n"
                f"常见原因:面板名被上一次注册占着(ZBrush 关掉脚本面板后名字不会立刻释放)。\n"
                f"处理:关掉右侧菜单栏里的 {palette} 面板后再 Load 一次,或重启 ZBrush。")
        return False

    failures: list[str] = []
    for feature in FEATURES:
        panel = ui.Panel(palette, feature.title)
        try:
            # title_mode=2:子面板**没有标题栏**(也就没有折叠三角),容器永远展开;
            # 分组标题改用一个"禁用按钮"来当占位符(灰掉、点不动,纯装饰)。
            # 这样面板里不会每加一个功能就多一层可折叠菜单。
            body = zbc.add_subpalette(panel.sub, 2)
        except Exception as exc:  # noqa: BLE001
            failures.append(f'add_subpalette("{panel.sub}") 异常:{exc}')
            continue
        if not body:
            failures.append(f'add_subpalette("{panel.sub}") 返回 False')
            continue
        try:
            _add_group_header(panel, feature.title)
            feature.build(panel, cfg)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"功能 {feature.key} build 失败:{exc}")
            continue
        print(f"[{palette}] 功能「{feature.title}」({feature.key}):")
        for key, path in panel.paths.items():
            print(f"[{palette}]   {key:<12} {path}")
        failures.extend(panel.failures)

    if failures:
        print(f"[{palette}] ⚠ 以下界面元素创建失败:")
        for item in failures:
            print(f"[{palette}]   - {item}")

    try:
        print(f"[{palette}] 屏幕:{_sync_screen(cfg)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{palette}] 屏幕探测不可用:{exc}")

    ui.open_palette(palette)
    if failures:
        ui.note(f"警告:{palette} 部分控件创建失败,详见 Python 控制台")
    return not failures
