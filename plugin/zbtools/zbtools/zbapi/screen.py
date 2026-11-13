from __future__ import annotations

import os
from typing import Any

import zbrush.commands as zbc

from ..errors import PluginError
import json
import math
import subprocess
from . import canvas

MM_PER_INCH = 25.4

#: 外部辅助脚本目录(在 ZBrush 之外以子进程运行,可用 pyobjc / tkinter)
_TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools",
)

#: 显示器探测脚本(读 EDID 物理尺寸)
_HELPER = os.path.join(_TOOLS_DIR, "display_info.py")

#: 屏幕真尺浮层窗口
RULER_HELPER = os.path.join(_TOOLS_DIR, "ruler_window.py")

#: 探测结果缓存(整个会话复用)
_DISPLAY_CACHE: dict[str, Any] | None = None


def detect_displays(cfg: dict[str, Any], force: bool = False) -> dict[str, Any] | None:
    """调用外部脚本读显示器参数(CoreGraphics + EDID 物理尺寸),失败返回 None。"""
    global _DISPLAY_CACHE
    if _DISPLAY_CACHE is not None and not force:
        return _DISPLAY_CACHE

    interpreter = str(cfg.get("interpreter") or "")
    if not interpreter or not os.path.exists(interpreter):
        print(f"[ZBTools] 探测解释器不存在:{interpreter!r},改为手动参数模式")
        return None

    try:
        completed = subprocess.run(
            [interpreter, _HELPER],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - 探测失败不应影响主体功能
        print(f"[ZBTools] 显示器探测失败:{exc}")
        return None

    try:
        data = json.loads(completed.stdout or "{}")
    except Exception:
        print(f"[ZBTools] 显示器探测输出无法解析:{completed.stdout[:200]!r} {completed.stderr[:200]!r}")
        return None

    if not data.get("ok"):
        print(f"[ZBTools] 显示器探测脚本报错:{data.get('error')}")
        return None

    _DISPLAY_CACHE = data
    return data


def resolve_screen(cfg: dict[str, Any]) -> dict[str, Any]:
    """确定"当前实际使用哪块屏"以及它的全部参数。"""
    data = detect_displays(cfg)
    displays = (data or {}).get("displays") or []
    if True:                                                   # 始终自动探测;失败才用手动参数兜底
        if displays:
            wanted = int(cfg.get("display_id") or 0)
            chosen = None
            if wanted:
                chosen = next((d for d in displays if int(d["id"]) == wanted), None)
            if chosen is None:
                active = (data or {}).get("active_display_id")
                chosen = next((d for d in displays if int(d["id"]) == active), None)
            if chosen is None:
                chosen = next((d for d in displays if d.get("is_main")), displays[0])
            return {
                "source": "auto(CoreGraphics+EDID)",
                "name": chosen["name"],
                "display_id": chosen["id"],
                "diagonal_inch": float(chosen["diagonal_inch"]),
                "native_px": list(chosen["native_px"]),
                "logical_pt": list(chosen["logical_pt"]),
                "ppi": float(chosen["ppi"]),
                "logical_ppi": float(chosen["logical_ppi"]),
                "physical_mm": list(chosen.get("physical_mm") or []),
            }

    # 手动模式:用户给的就是「对角线英寸 + PPI」这两个参数
    diagonal = float(cfg["diagonal_inch"])
    ppi = float(cfg.get("ppi") or 0.0)
    pixel_scale = float(cfg.get("pixel_scale") or 2.0)
    native = [float(v) for v in cfg["native_px"]]
    logical = [float(v) for v in cfg["logical_pt"]]

    if ppi <= 0.0:
        if diagonal <= 0.0:
            raise PluginError("屏幕参数不完整:请给出对角线英寸或 PPI(至少一个)。")
        ppi = math.hypot(native[0], native[1]) / diagonal
    logical_ppi = ppi / pixel_scale if pixel_scale else ppi

    return {
        "source": "manual(手动参数)",
        "name": f"手动 {diagonal:.2f} 英寸 {int(native[0])}x{int(native[1])}",
        "display_id": 0,
        "diagonal_inch": diagonal,
        "native_px": cfg["native_px"],
        "logical_pt": cfg["logical_pt"],
        "ppi": ppi,
        "logical_ppi": logical_ppi,
        "physical_mm": [],
    }


def cross_check(cfg: dict[str, Any]) -> str:
    """用「对角线英寸」和「PPI」互相核验(两个参数必须自洽)。"""
    try:
        info = resolve_screen(cfg)          # 自动模式 = 探测值;手动模式 = 你填的两参数
        diagonal = float(info["diagonal_inch"])
        ppi = float(info["ppi"])
        native = [float(v) for v in info["native_px"]]
    except Exception:
        return ""
    if diagonal <= 0.0 or ppi <= 0.0:
        return "⚠ 屏幕参数不完整(对角线或 PPI 为 0),无法核验"

    implied = math.hypot(native[0], native[1]) / diagonal
    deviation = abs(ppi - implied) / implied
    text = (
        f"屏幕参数核验:{ppi:.2f} ppi vs 由 {int(native[0])}x{int(native[1])} ÷ {diagonal:.2f}\" "
        f"反推的 {implied:.2f} ppi(偏差 {deviation * 100:.1f}%)"
    )
    if deviation > 0.03:
        text += "\n⚠ 两个参数不自洽:可能屏幕尺寸或分辨率填错了,1:1 尺寸会有对应偏差"
    return text


def screen_report(cfg: dict[str, Any]) -> str:
    """多行文本:列出探测到的所有显示器 + 当前用的是哪块。"""
    lines: list[str] = []
    data = detect_displays(cfg)
    if data and data.get("displays"):
        lines.append(f"探测到 {data['count']} 块显示器(-> 表示当前使用):")
        try:
            current = resolve_screen(cfg)
            current_id = current["display_id"]
        except PluginError:
            current_id = None
        for display in data["displays"]:
            mark = "->" if int(display["id"]) == current_id else "  "
            main = " [主屏]" if display.get("is_main") else ""
            lines.append(
                f"  {mark} #{display['id']} {display['name']}{main}:"
                f" {display['diagonal_inch']:.2f} 英寸"
                f" | 原生 {display['native_px'][0]}x{display['native_px'][1]}"
                f" | 逻辑 {display['logical_pt'][0]}x{display['logical_pt'][1]}"
                f" | ppi {display['ppi']:.1f}"
                f" | 逻辑点/英寸 {display['logical_ppi']:.2f}"
                f" | 倍率 {display['pixel_scale']}"
            )
        if data.get("active_display_id") is None:
            lines.append("  (ZBrush 窗口未匹配到显示器,已回落到主屏)")
    else:
        lines.append("自动探测不可用,使用手动参数。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 画布 / 模型数据
# --------------------------------------------------------------------------- #


def mm_per_pixol(cfg: dict[str, Any], zoom: float | None = None) -> float:
    """一个画布 pixol 在屏幕上对应的物理毫米数。

    屏幕侧数据:ppi、原生÷逻辑(倍率)都来自显示器探测;
    桥接项(bridge_factor)默认 1.0,可用「核验显示比例」锁定。
    """
    zoom = canvas.canvas_zoom() if zoom is None else zoom
    info = resolve_screen(cfg)
    pixel_scale = float(cfg.get("pixel_scale") or info["ppi"] / info["logical_ppi"])
    bridge = float(cfg.get("bridge_factor", 1.0) or 1.0)
    physical_px_per_pixol = zoom * pixel_scale * bridge
    return physical_px_per_pixol * MM_PER_INCH / info["ppi"]


def pt_per_mm(cfg: dict[str, Any], orientation: str = "horizontal") -> dict[str, Any]:
    """屏幕上 1 毫米等于多少**逻辑点**(浮层真尺的唯一依据)。

    与 mm_per_pixol 的区别:这里不涉及 ZBrush 的画布与模型,纯粹是显示器物理换算
    —— 所以尺子刻度不受画布缩放、模型、透视影响。

    **口径**:用 ppi(原生像素/英寸),和 mm_per_pixol 同一套,保证"尺子"与
    "显示实际大小"不会各说各话。EDID 的物理宽高只当**交叉核验**:
    按它算出来的横竖每毫米点数会差 1.2%,而屏幕像素是方的、不该有差 ——
    所以它只能用来发现问题,不能当基准(两者通常差 0.3% 以内)。

    ``cfg["ruler__calibration"]`` 是给实体尺子实测后的微调系数(默认 1.0)。
    """
    info = resolve_screen(cfg)
    px_per_inch = float(info["logical_ppi"])                  # 逻辑点/英寸
    scale = float(cfg.get("pixel_scale") or info["ppi"] / info["logical_ppi"])
    calibration = float(cfg.get("ruler__calibration", 1.0) or 1.0)
    value = px_per_inch / MM_PER_INCH * calibration

    cross_check: list[dict[str, Any]] = []
    physical = list(info.get("physical_mm") or [])
    logical = list(info.get("logical_pt") or [])
    if len(physical) == 2 and len(logical) == 2 and all(physical):
        for index, axis in enumerate(("宽", "高")):
            by_edid = float(logical[index]) / float(physical[index])
            cross_check.append({
                "axis": axis,
                "pt_per_mm": by_edid,
                "diff_pct": (by_edid - value) / value * 100.0,
            })
    return {
        "pt_per_mm": value,
        "source": (f"ppi 口径:{px_per_inch:.2f} 逻辑点/英寸"
                   f"{'' if calibration == 1.0 else f'(含校准 {calibration:g})'}"),
        "screen": info,
        "pixel_scale": scale,
        "cross_check": cross_check,
        "orientation": "vertical" if orientation == "vertical" else "horizontal",
    }
