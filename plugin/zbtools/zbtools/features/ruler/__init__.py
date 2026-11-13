"""功能:屏幕真尺(把一把**物理真实刻度**的尺子贴在屏幕上,平移模型即可比对长短)。

一把尺子可以任意角度摆放,也可以同时摆好几把;依赖:
  * zbapi.screen.pt_per_mm —— 刻度依据(显示器物理换算)
  * tools/ruler_window.py   —— 浮层窗口(AppKit,支持旋转 + 点击穿透)
  * tools/ruler_geometry.py —— 几何计算(纯函数,可单测)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ... import ui
from . import service

#: 面板标签(每段不能含 ASCII 冒号)
LABELS: dict[str, str] = {
    "create": "新建尺子",
    "delete": "删除尺子",
    "length": "尺长 mm",
    "diagnostics": "诊断信息",
}


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    build: Callable[[ui.Panel, dict[str, Any]], None]


def paths(plugin_title: str = "ZBTools") -> dict[str, str]:
    return {k: f"{plugin_title}:{FEATURE.title}:{v}" for k, v in LABELS.items()}


def _save(cfg: dict[str, Any], key: str, value: Any) -> None:
    from ... import settings
    cfg[f"ruler__{key}"] = value
    settings.save(cfg)


def build(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """挂控件:新建 / 翻面 / 删除 + 尺长 + 核验。

    操作都在浮层窗口里完成,面板刻意保持最小;新建和删除**不弹通知**(只打控制台),
    免得每次点一下都要关窗口。
    """
    def on_create(_shortcut: str) -> None:
        ui.guard("新建尺子", lambda: print(f"[真尺] {service.create(cfg).splitlines()[0]}"))

    def on_delete(_shortcut: str) -> None:
        ui.guard("删除尺子", lambda: print(f"[真尺] {service.delete_all()}"))

    def on_length(_shortcut: str, value: float) -> None:
        _save(cfg, "length_mm", float(value))
        print(f"[真尺] 尺长已保存为 {float(value):g} mm(对之后新建的尺子生效)")

    panel.button(
        "create", LABELS["create"],
        "新建真尺:左键拖动移动,右键拖动旋转(Shift 吸附 45°),双击删除",
        on_create,
    )
    panel.button(
        "delete", LABELS["delete"],
        "删掉所有尺子(不留记录)",
        on_delete,
    )
    panel.slider(
        "length", LABELS["length"], service.length_mm(cfg), 1,
        service.MIN_LENGTH_MM, service.MAX_LENGTH_MM,
        "新建尺子用多长(毫米);已摆出来的不受影响",
        on_length,
    )
    panel.button(
        "diagnostics", LABELS["diagnostics"],
        "刻度依据 + 每把尺子的实时状态(拿实体尺子核对用)",
        lambda _s: ui.guard("诊断信息", lambda: ui.note(service.diagnostics_text(cfg))),
    )

FEATURE = Feature(key="ruler", title="屏幕真尺", build=build)
