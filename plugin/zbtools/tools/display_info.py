#!/usr/bin/env python3
"""显示器参数探测(在 ZBrush 之外以子进程运行)。

用 CoreGraphics 读每块屏的**物理尺寸(mm,来自 EDID)**、原生像素、逻辑点,
并算出 ppi / 逻辑点每英寸;还能找出 ZBrush 窗口在哪块屏上。

输出 JSON 到 stdout,供 ZBTools 插件通过 subprocess 调用。

单独运行:
    .venv/bin/python plugin/zbtools/tools/display_info.py   # 在项目根执行
"""

from __future__ import annotations

import json
import math
import sys

APP_NAME = "ZBrush"


def load_quartz():
    try:
        import Quartz  # noqa: PLC0415
        from AppKit import NSScreen  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可读提示
        raise RuntimeError(
            "缺少 pyobjc,请在项目 venv 里执行: .venv/bin/pip install pyobjc-framework-Quartz"
        ) from exc
    return Quartz, NSScreen


def collect_displays() -> list[dict]:
    Quartz, NSScreen = load_quartz()

    names: dict[int, str] = {}
    for screen in NSScreen.screens():
        number = screen.deviceDescription().get("NSScreenNumber")
        if number is not None:
            names[int(number)] = str(screen.localizedName())

    error, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if error != 0:
        raise RuntimeError(f"CGGetActiveDisplayList 失败:{error}")

    main_id = int(Quartz.CGMainDisplayID())
    displays: list[dict] = []

    for display_id in list(ids)[:count]:
        did = int(display_id)
        mm_w, mm_h = Quartz.CGDisplayScreenSize(did)
        bounds = Quartz.CGDisplayBounds(did)
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        px_w = Quartz.CGDisplayModeGetPixelWidth(mode)
        px_h = Quartz.CGDisplayModeGetPixelHeight(mode)
        pt_w = Quartz.CGDisplayModeGetWidth(mode)
        pt_h = Quartz.CGDisplayModeGetHeight(mode)

        diag_mm = math.hypot(mm_w, mm_h)
        diag_in = diag_mm / 25.4 if diag_mm else 0.0
        ppi = math.hypot(px_w, px_h) / diag_in if diag_in else 0.0
        lpi = math.hypot(pt_w, pt_h) / diag_in if diag_in else 0.0
        scale = (px_w / pt_w) if pt_w else 1.0

        displays.append(
            {
                "id": did,
                "name": names.get(did, f"Display {did}"),
                "is_main": did == main_id,
                "physical_mm": [round(mm_w, 2), round(mm_h, 2)],
                "diagonal_inch": round(diag_in, 3),
                "native_px": [px_w, px_h],
                "logical_pt": [pt_w, pt_h],
                "ppi": round(ppi, 2),
                "logical_ppi": round(lpi, 2),
                "pixel_scale": round(scale, 4),
                "origin": [round(bounds.origin.x, 1), round(bounds.origin.y, 1)],
                "size_pt": [round(bounds.size.width, 1), round(bounds.size.height, 1)],
            }
        )
    return displays


def find_app_display(displays: list[dict], app_name: str = APP_NAME) -> int | None:
    """找出 app_name 的窗口主要落在哪块屏上,返回 display id。"""
    try:
        import Quartz  # noqa: PLC0415
    except ImportError:
        return None

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    best_id, best_area = None, 0.0

    for window in windows:
        owner = str(window.get("kCGWindowOwnerName", ""))
        if app_name.lower() not in owner.lower():
            continue
        rect = window.get("kCGWindowBounds") or {}
        x, y = float(rect.get("X", 0)), float(rect.get("Y", 0))
        w, h = float(rect.get("Width", 0)), float(rect.get("Height", 0))
        if w * h <= 0:
            continue
        center_x, center_y = x + w / 2.0, y + h / 2.0
        for display in displays:
            ox, oy = display["origin"]
            dw, dh = display["size_pt"]
            if ox <= center_x <= ox + dw and oy <= center_y <= oy + dh:
                area = w * h
                if area > best_area:
                    best_id, best_area = display["id"], area
    return best_id


def main_mac() -> int:
    try:
        displays = collect_displays()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    active_id = find_app_display(displays)
    payload = {
        "ok": True,
        "count": len(displays),
        "active_display_id": active_id,
        "displays": displays,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0





# --------------------------------------------------------------------------- #
# Windows:纯 ctypes(标准库),不需要任何第三方包
# --------------------------------------------------------------------------- #
def detect_windows() -> dict:
    """用 user32/gdi32 读每块屏的逻辑分辨率、DPI 与物理尺寸(mm)。

    说明:HORZSIZE/VERTSIZE 是显示器自报的物理毫米数(部分驱动会取整),
    所以插件里「核验显示比例」仍然是最终裁决手段。
    """
    import ctypes
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetProcessDPIAware()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor v2
    except Exception:                                          # noqa: BLE001
        pass

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128),
                    ("DeviceKey", wintypes.WCHAR * 128)]

    HORZSIZE, VERTSIZE, LOGPIXELSX, DESKTOPHORZRES, DESKTOPVERTRES = 4, 6, 88, 118, 117
    monitors, index = [], 0
    while True:
        adapter = DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(adapter)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(adapter), 0):
            break
        index += 1
        if not (adapter.StateFlags & 0x1):                     # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            continue
        hdc = user32.GetDC(adapter.DeviceName)
        try:
            mm_w = gdi32.GetDeviceCaps(hdc, HORZSIZE)
            mm_h = gdi32.GetDeviceCaps(hdc, VERTSIZE)
            log_ppi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            native_w = gdi32.GetDeviceCaps(hdc, DESKTOPHORZRES)
            native_h = gdi32.GetDeviceCaps(hdc, DESKTOPVERTRES)
        finally:
            user32.ReleaseDC(adapter.DeviceName, hdc)
        if not native_w or not native_h:                        # 兜底:桌面尺寸
            native_w, native_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        scale = max(1.0, log_ppi / 96.0)                        # 逻辑点 = 原生 / 缩放
        logical_w, logical_h = native_w / scale, native_h / scale
        ppi = (native_w / mm_w * 25.4) if mm_w else 96.0 * scale
        monitors.append({
            "id": len(monitors) + 1, "name": adapter.DeviceString or adapter.DeviceName,
            "is_main": len(monitors) == 0,
            "physical_mm": [round(mm_w, 2), round(mm_h, 2)],
            "diagonal_inch": round(math.hypot(mm_w, mm_h) / 25.4, 3) if mm_w else 0.0,
            "native_px": [int(native_w), int(native_h)],
            "logical_pt": [round(logical_w, 1), round(logical_h, 1)],
            "ppi": round(ppi, 2), "logical_ppi": round(ppi / scale, 2),
            "pixel_scale": round(scale, 3), "origin": [0.0, 0.0],
            "size_pt": [round(logical_w, 1), round(logical_h, 1)],
        })
    if not monitors:
        return {"ok": False, "error": "没探测到显示器"}
    return {"ok": True, "count": len(monitors), "active_display_id": monitors[0]["id"],
            "displays": monitors}


def main() -> int:
    if sys.platform == "win32":
        data = detect_windows()
        print(json.dumps(data, ensure_ascii=False))
        return 0 if data.get("ok") else 1
    return main_mac()


if __name__ == "__main__":
    sys.exit(main())
