#!/usr/bin/env python3
"""核验浮层真尺:量刻度周期,并测出尺子的实际角度(不需要实体尺子)。

为什么用"差分法":尺子窗口是透明的,窗口里除了尺身还有一大片**完全透明**的死区,
死区会透出桌面/别的窗口 —— 直接从截图里挑"深色像素"会被背景污染(试过:测出 178°、
周期差 40%)。所以这里拍两张:有尺子 / 关掉尺子,逐像素求差,差分出来的才是尺身实体。

流程:
  1. 按 PID 找到浮层窗口(尺子可以有好几把,必须锁定自己启动的那把)
  2. 截图 A → 结束该尺子进程 → 截图 B → 差分得到尺身掩码
  3. 对掩码做主成分分析 → 尺子实际角度
  4. 在掩码内取靠边的窄带(毫米刻度那一侧)→ 梳状搜索刻度周期
  5. 与理论值(1 mm = 多少逻辑点)对比

用法:
    .venv/bin/python scripts/verify_ruler_overlay.py 3.1992 --pid 1234
参数是 1 mm 等于多少逻辑点(插件「核验刻度」会给,或 = 逻辑ppi ÷ 25.4)。
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import tempfile

import Quartz
from PIL import Image

TOLERANCE_PCT = 0.5          # 允许的渲染/取整误差
DIFF_THRESHOLD = 24          # 差分阈值(0-255,三通道平均)
DARK = 130                   # 三通道都小于它 = 深色(刻度/文字/边框)


def find_ruler_window(pid: int) -> dict | None:
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    best = None
    for win in windows:
        if int(win.get("kCGWindowOwnerPID") or 0) != pid:
            continue
        bounds = win.get("kCGWindowBounds", {})
        width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
        if width > 150 and height > 30:
            if best is None or width * height > float(best["Width"]) * float(best["Height"]):
                best = dict(bounds)
    return best


def backing_scale() -> float:
    displays = Quartz.CGGetActiveDisplayList(8, None, None)[1]
    mode = Quartz.CGDisplayCopyDisplayMode(displays[0])
    pixel_width = Quartz.CGDisplayModeGetPixelWidth(mode)
    point_width = Quartz.CGDisplayModeGetWidth(mode)
    return pixel_width / point_width if point_width else 2.0


def shoot() -> Image.Image:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = handle.name
    subprocess.run(["/usr/sbin/screencapture", "-x", path], check=False)
    image = Image.open(path).convert("RGB")
    os.unlink(path)
    return image


def strip_mask(before: Image.Image, after: Image.Image, bounds: dict, scale: float,
               before2: Image.Image | None = None):
    """差分出尺身:返回 (掩码图, 原生像素裁剪框)。

    透明死区后面如果有东西在动(光标闪烁、别的窗口刷新),差分会把它们也算进尺身 ——
    所以再多拍一张 before2:只有"两张 before 里几乎一样(背景静止)"的像素才算数。
    """
    x0, y0 = int(bounds["X"] * scale), int(bounds["Y"] * scale)
    x1, y1 = x0 + int(bounds["Width"] * scale), y0 + int(bounds["Height"] * scale)
    box = (max(0, x0 - 4), max(0, y0 - 4),
           min(before.size[0], x1 + 4), min(before.size[1], y1 + 4))
    left, right = before.crop(box), after.crop(box)
    pa, pb = left.load(), right.load()
    pc = before2.crop(box).load() if before2 is not None else None
    width, height = left.size
    mask = Image.new("L", (width, height), 0)
    pm = mask.load()
    for y in range(height):
        for x in range(width):
            ra, ga, ba = pa[x, y][:3]
            rb, gb, bb = pb[x, y][:3]
            if (abs(ra - rb) + abs(ga - gb) + abs(ba - bb)) / 3.0 <= DIFF_THRESHOLD:
                continue
            if pc is not None:                       # 背景在动 → 不算尺身
                rc, gc, bc = pc[x, y][:3]
                if (abs(ra - rc) + abs(ga - gc) + abs(ba - bc)) / 3.0 > 4:
                    continue
            pm[x, y] = 255
    return mask, box


def principal_axis(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """返回 (角度度, 质心x, 质心y);角度已换算成屏幕坐标(y 向上)。"""
    count = len(points)
    mx = sum(p[0] for p in points) / count
    my = sum(p[1] for p in points) / count
    sxx = sum((p[0] - mx) ** 2 for p in points) / count
    syy = sum((p[1] - my) ** 2 for p in points) / count
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points) / count
    angle_image = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return -math.degrees(angle_image) % 180.0, mx, my


def period_by_comb(values: list[float], low: float, high: float,
                   step: float = 0.01) -> tuple[float, float]:
    """梳状搜索:哪一组"等间距尖峰"最能解释这些位置。

    与自相关的区别:自相关会被刻度带里那条连续边框线带偏,梳状搜索只关心等间距尖峰。
    """
    if len(values) < 20:
        return 0.0, 0.0
    low_edge, high_edge = min(values), max(values)
    span = int(high_edge - low_edge) + 2
    bins = [0.0] * span
    for value in values:
        bins[int(value - low_edge)] += 1.0
    period = low
    best = (0.0, -1.0)
    while period <= high:
        phase = 0.0
        while phase < period:
            total, samples, index = 0.0, 0, 0
            while True:
                position = phase + index * period
                if position > span - 1:
                    break
                total += bins[int(position)]
                samples += 1
                index += 1
            if samples:
                score = total / samples
                # 同分优先取更小的周期:刻度彼此相同时,2× 周期会得到一样的均值
                if score > best[1] * 1.02 or (best[1] > 0 and score > best[1] * 0.98
                                              and period < best[0]):
                    best = (period, score)
            phase += 0.5
        period += step
    return best


def main() -> int:
    pid, expected_pt, expected_angle = 0, None, None
    args = sys.argv[1:]
    for index, value in enumerate(args):
        if value == "--pid" and index + 1 < len(args):
            pid = int(args[index + 1])
        elif value == "--angle" and index + 1 < len(args):
            expected_angle = float(args[index + 1])
        elif not value.startswith("--") and expected_pt is None:
            expected_pt = float(value)
    if not pid:
        print("用法:verify_ruler_overlay.py <每毫米逻辑点> --pid <尺子进程PID>")
        return 1

    bounds = find_ruler_window(pid)
    if bounds is None:
        print(f"✗ 没找到 PID {pid} 的浮层窗口")
        return 1
    scale = backing_scale()
    before = shoot()
    before2 = shoot()
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    subprocess.run(["sleep", "0.6"], check=False)
    after = shoot()

    mask, box = strip_mask(before, after, bounds, scale, before2)
    pixels = mask.load()
    points = [(float(x), float(y))
              for y in range(mask.size[1]) for x in range(mask.size[0]) if pixels[x, y]]
    print(f"窗口 {bounds['Width']:.0f}×{bounds['Height']:.0f} 点 @{scale:g}x;"
          f"差分出尺身像素 {len(points)}")
    if len(points) < 200:
        print("✗ 差分到的尺身太少(被别的窗口挡住了?)")
        return 2

    angle, mx, my = principal_axis(points)
    if expected_angle is not None:
        delta = min(abs(angle - expected_angle % 180), 180 - abs(angle - expected_angle % 180))
        print(f"→ 实测角度 {angle:.2f}°(设定 {expected_angle:g}°,偏差 {delta:.2f}°"
              f"{' ✓' if delta < 2.0 else ' ✗'})")
    else:
        print(f"→ 实测角度 {angle:.2f}°(屏幕坐标,0=水平)")

    rad = math.radians(angle)
    ux, uy = math.cos(rad), -math.sin(rad)
    vx, vy = -uy, ux
    along = [(p[0] - mx) * ux + (p[1] - my) * uy for p in points]
    across = [(p[0] - mx) * vx + (p[1] - my) * vy for p in points]
    along_min, along_max = min(along), max(along)
    across_min, across_max = min(across), max(across)
    print(f"→ 尺身 {along_max - along_min:.0f} × {across_max - across_min:.0f} 原生px"
          f"(厚度理论值 {46 * scale:.0f})")

    source = before.crop(box).load()
    dark = []
    for index, point in enumerate(points):
        x, y = int(point[0]), int(point[1])
        r, g, b = source[x, y][:3]
        if r < DARK and g < DARK and b < DARK:
            dark.append((along[index], across[index]))
    print(f"→ 其中深色像素(刻度/文字/边框){len(dark)} 个")

    expected = (expected_pt * scale) if expected_pt else 6.4
    results = []
    band = 3.5 * scale
    for edge, name in ((across_max, "毫米侧"), (across_min, "英寸侧")):
        values = [a for a, c in dark if abs(c - edge) <= band]
        period, score = period_by_comb(values, expected * 0.8, expected * 1.25)
        results.append((name, len(values), period, score))
        print(f"  {name}(跨轴 {edge:+.0f}):{len(values)} 个点,"
              f"周期 {period:.3f} 原生px(得分 {score:.2f})")

    best = max(results, key=lambda item: item[3])
    if not best[2]:
        print("✗ 求不出周期")
        return 3
    print(f"→ 采用「{best[0]}」:每毫米 {best[2] / scale:.4f} 逻辑点")
    if expected_pt:
        error = abs(best[2] - expected_pt * scale) / (expected_pt * scale) * 100.0
        verdict = "✓ 通过" if error <= TOLERANCE_PCT else "✗ 偏差过大"
        print(f"→ 理论 {expected_pt:.4f} 逻辑点/mm,误差 {error:.3f}% {verdict}")
        return 0 if error <= TOLERANCE_PCT else 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
