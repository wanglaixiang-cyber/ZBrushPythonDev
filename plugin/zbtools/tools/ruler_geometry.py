"""真尺的几何核心(纯函数,不依赖任何 GUI —— 所以能直接单测)。

坐标系约定:**屏幕坐标,y 轴向上**(与 macOS AppKit 一致)。
尺子由三样东西定义:

    origin   —— 0 刻度那个点的屏幕位置(旋转时它不动)
    angle    —— 长度方向与 +x 轴的夹角(度,逆时针为正)
    length   —— 尺身长度(逻辑点);厚度固定为 thickness

于是尺身占据的矩形(在"尺子自己的坐标系"里)是 along ∈ [0, length]、
across ∈ [0, thickness]。沿长度方向的单位向量 u = (cosθ, sinθ),
沿厚度方向的单位向量 v = (-sinθ, cosθ)(即 u 逆时针转 90°)。

这样定义的好处:
  * 旋转 = 只改 angle,0 点钉在原地,量哪一段都从 0 点读起
  * 判断鼠标是否在尺子上、算窗口包围盒,都只是两个投影点积
"""

from __future__ import annotations

import math

#: 尺身厚度(逻辑点)—— 要放得下公制刻度+数字 与 英制刻度+数字 两套且不重叠
THICKNESS_PT = 58.0

#: 角度量化步长(按住 Shift 时用)
SNAP_STEP_DEG = 45.0


def normalize(angle_deg: float) -> float:
    """把角度归一到 [0, 360)。"""
    return float(angle_deg) % 360.0


def axis_vectors(angle_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """返回 (长度方向单位向量 u, 厚度方向单位向量 v)。"""
    rad = math.radians(angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    return (cos, sin), (-sin, cos)


def project(point: tuple[float, float], origin: tuple[float, float],
            angle_deg: float) -> tuple[float, float]:
    """把屏幕点换算到尺子坐标系:(along, across)。"""
    u, v = axis_vectors(angle_deg)
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    return (dx * u[0] + dy * u[1], dx * v[0] + dy * v[1])


def contains_point(point: tuple[float, float], origin: tuple[float, float],
                   angle_deg: float, length_pt: float,
                   thickness_pt: float = THICKNESS_PT,
                   tolerance: float = 0.0) -> bool:
    """鼠标是否落在尺身上(带容差;容差用来让"贴着尺子"也算命中)。"""
    along, across = project(point, origin, angle_deg)
    return (-tolerance <= along <= length_pt + tolerance
            and -tolerance <= across <= thickness_pt + tolerance)


def strip_corners(origin: tuple[float, float], angle_deg: float, length_pt: float,
                  thickness_pt: float = THICKNESS_PT) -> list[tuple[float, float]]:
    """尺身矩形的四个角(屏幕坐标)。"""
    u, v = axis_vectors(angle_deg)
    ox, oy = origin
    return [
        (ox, oy),
        (ox + u[0] * length_pt, oy + u[1] * length_pt),
        (ox + u[0] * length_pt + v[0] * thickness_pt,
         oy + u[1] * length_pt + v[1] * thickness_pt),
        (ox + v[0] * thickness_pt, oy + v[1] * thickness_pt),
    ]


def bounding_box(origin: tuple[float, float], angle_deg: float, length_pt: float,
                 thickness_pt: float = THICKNESS_PT) -> tuple[float, float, float, float]:
    """尺身的最小外接矩形 (x0, y0, x1, y1) —— 就是窗口要多大。"""
    corners = strip_corners(origin, angle_deg, length_pt, thickness_pt)
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def angle_towards(origin: tuple[float, float], point: tuple[float, float],
                  fallback_deg: float = 0.0) -> float:
    """从 0 点指向某个屏幕点的角度(度,0..360);点和 0 点重合时返回 fallback。"""
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return normalize(fallback_deg)
    return normalize(math.degrees(math.atan2(dy, dx)))


def quantize_angle(angle_deg: float, step_deg: float = SNAP_STEP_DEG) -> float:
    """把角度吸附到 step 的整数倍(按住 Shift 时用;45 的倍数同时也覆盖 0/90/180/270)。"""
    if step_deg <= 0:
        return normalize(angle_deg)
    return normalize(round(normalize(angle_deg) / step_deg) * step_deg)


def angle_label(angle_deg: float) -> str:
    """给提示文案用的角度描述。"""
    angle = normalize(angle_deg)
    for value, name in ((0.0, "水平"), (90.0, "竖直"), (180.0, "水平(反向)"), (270.0, "竖直(反向)")):
        if abs(angle - value) < 1e-6:
            return f"{angle:.0f}°({name})"
    return f"{angle:.0f}°"


def cascade_index(count: int, step: float = 30.0) -> tuple[float, float]:
    """新尺子的错开偏移:第 count 把尺子相对上一把偏 (step, -step)(回到 0 点循环)。"""
    offset = (count % 6) * step
    return (offset, -offset)
