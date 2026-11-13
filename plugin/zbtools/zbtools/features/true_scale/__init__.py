"""功能:按屏幕实际大小显示模型(1:1)。显示名可改(title),key 保持 `true_scale`。

一个功能包只需要对外提供:
    FEATURE = Feature(key=..., title=..., build=...)
  * key    —— 稳定标识(用于配置前缀 / 诊断),改名不要动它
  * title  —— ZBrush 面板上显示的名字(随便改)
  * build  —— 往面板挂自己的控件

依赖方向:features/* → zbtools.ui / zbtools.zbapi / 本包的 service;功能之间互不依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import zbrush.commands as zbc

from ... import ui
from . import service


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    build: Callable[[ui.Panel, dict[str, Any]], None]


#: 面板上的标签(每段不能含 ASCII 冒号)
LABELS: dict[str, str] = {
    "apply": "显示实际大小",
    "verify": "核验显示比例",
    "diagnostics": "诊断信息",
}


def paths(plugin_title: str = "ZBTools") -> dict[str, str]:
    """该功能在面板里的全部 item path(供测试与文档引用)。"""
    return {k: f"{plugin_title}:{FEATURE.title}:{v}" for k, v in LABELS.items()}


#: 本包内共享的运行时状态(最近一次报告/视角)
STATE: dict[str, Any] = {"report": None, "view": None, "applied": None}


# --------------------------------------------------------------------------- #
# 交互
# --------------------------------------------------------------------------- #
def _show_true_size(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    STATE["view"] = service.canvas.remember_view()
    STATE["report"] = service.apply_true_size(cfg, remember=STATE["view"])
    STATE["applied"] = service.canvas.remember_view()
    ui.note(service.summary_text(STATE["report"], cfg))


def _verify(cfg: dict[str, Any]) -> None:
    canvas_w, canvas_h = service.canvas.canvas_size()
    per_pixol = service.screen.mm_per_pixol(cfg)
    expected_w, expected_h = canvas_w * per_pixol, canvas_h * per_pixol
    answer = zbc.ask_string(
        f"{expected_w:.1f}",
        f"量画布边框宽度(mm)。按当前屏幕参数应为 {expected_w:.1f}"
        f"(高 {expected_h:.1f});若你量到的不同,请直接改成实测值",
    )
    text = (answer or "").strip().replace(",", ".")
    if not text:
        raise service.PluginError("没有输入值,已取消。")
    try:
        measured = float(text)
    except ValueError as exc:
        raise service.PluginError(f"无法解析 '{text}'") from exc

    _factor, message = service.verify_bridge(cfg, measured)
    service.settings.save(cfg)
    print(f"[ZBTools] {message}")
    _show_true_size(None, cfg)          # type: ignore[arg-type]
    ui.note(message)


def _diagnostics(cfg: dict[str, Any]) -> None:
    text = service.diagnostics(cfg, STATE["report"])
    if service.copy_to_clipboard(text):
        ui.note("诊断信息已复制到剪贴板,直接 Cmd+V 粘贴即可")
        print("[ZBTools] 诊断信息已复制到剪贴板")
    else:
        ui.note("警告:复制到剪贴板失败,请看 Python 控制台里的诊断输出")


def build(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """往插件面板挂本功能的控件(4 个按钮)。"""
    panel.button("apply", LABELS["apply"],
                 "按当前视图显示真实物理尺寸(不改旋转、不动透视、不动位置,只改尺寸)",
                 lambda _s: ui.guard("显示实际大小", lambda: _show_true_size(panel, cfg)),
                 width=1.0)
    panel.button("verify", LABELS["verify"],
                 "量画布边框宽度(mm),核对屏幕映射(输入框已预填探测值)",
                 lambda _s: ui.guard("核验显示比例", lambda: _verify(cfg)))
    panel.button("diagnostics", LABELS["diagnostics"],
                 "三段数据链(模型 / 屏幕 / 映射)打印并复制到剪贴板",
                 lambda _s: ui.guard("诊断信息", lambda: _diagnostics(cfg)))


FEATURE = Feature(key="true_scale", title="实际大小", build=build)
