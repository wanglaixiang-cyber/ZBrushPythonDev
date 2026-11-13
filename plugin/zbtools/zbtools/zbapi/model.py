from __future__ import annotations

from typing import Any

import zbrush.commands as zbc
from . import canvas, screen
from ..errors import PluginError
from . import canvas


UNIT_FACTORS: dict[str, float] = {"mm": 1.0, "cm": 10.0, "in": 25.4}


SCALE_MASTER = "Zplugin:Scale Master"
#: 面板上的单位开关 -> 1 unit 等于多少毫米


def scalemaster_unit() -> tuple[str, float] | None:
    """读 Scale Master 里选中的单位(mm / cm / in)。"""
    for name, factor in UNIT_FACTORS.items():
        path = f"{SCALE_MASTER}:{name}"
        if not zbc.exists(path):
            continue
        try:
            if float(zbc.get(path)) > 0.5:
                return name, factor
        except Exception:
            continue
    return None


def display_unit(cfg: dict[str, Any]) -> tuple[str, float]:
    """**显示**单位:跟随 Scale Master 里选中的单位(只影响数值怎么显示,不影响真实尺寸)。

    实测语义(用户澄清 + 截图):Scale Master 的单位开关只是换算显示 ——
    同一个模型 mm 选中时显示 2,cm 选中时显示 0.2(= 2mm),英寸时 0.079。
    所以它**不改变模型的真实大小**,只改变读数单位。
    """
    found = scalemaster_unit()
    if found:
        return found
    name = str(cfg.get("display_unit") or cfg.get("unit_name") or "mm").lower()
    if name not in UNIT_FACTORS:
        name = "mm"
    return name, UNIT_FACTORS[name]


def unit_factor(cfg: dict[str, Any]) -> float:
    """**尺寸口径**:1 ZBrush unit = ? mm。恒为 1(即 1 unit = 1 mm),
    与 Scale Master 选哪个单位无关(那个只是显示换算)。"""
    try:
        value = float(cfg.get("mm_per_unit") or 1.0)
    except Exception:
        value = 1.0
    return value if value > 0 else 1.0


def tool_effective_size() -> tuple[tuple[float, float, float], float, float] | None:
    """直接读 ZBrush 的模型有效尺寸(unit)。

    实测发现(探测脚本):
        Tool:Geometry:X Size = 网格本身尺寸
        Tool:Export:Scale    = ZBrush scale(用户的缩放就存在这里,Scale Master 管的就是它)
        有效尺寸 = 网格尺寸 × Export:Scale  —— 与 Scale Master 的读数完全吻合
    这条路是**实时**的,不依赖 Scale Master 的读数是否新鲜,也不动用户模型。
    返回 (每轴有效尺寸, Export:Scale, 网格X尺寸)。
    """
    sizes: list[float] = []
    for axis in ("X", "Y", "Z"):
        path = f"Tool:Geometry:{axis} Size"
        if not zbc.exists(path):
            return None
        try:
            sizes.append(abs(float(zbc.get(path))))
        except Exception:
            return None
    if not sizes or sizes[0] <= 0:
        return None

    export_scale = 1.0
    scale_path = "Tool:Export:Scale"
    if zbc.exists(scale_path):
        try:
            value = abs(float(zbc.get(scale_path)))
            if value > 0:
                export_scale = value
        except Exception:
            pass
    return (tuple(v * export_scale for v in sizes), export_scale, sizes[0])  # type: ignore[return-value]


def read_model_size_mm(cfg: dict[str, Any]) -> tuple[tuple[float, float, float], str] | None:
    """从 ZBrush 自己的报告读模型**有效尺寸**(mm)。

    做法:先点 Scale Master 的「Sliders to Subtool Size」让它按当前模型刷新,
    再读 X/Y/Z(它选中的单位),换算成 mm。

    为什么需要它:你缩放模型时,缩放可能落在画布变换、子工具变换或已烘焙进网格等
    不同层里 —— 只有 ZBrush 自己的报告能把这层"隐藏缩放"算进去。
    """
    if not bool(cfg.get("read_size_from_scalemaster", True)):
        return None
    # 只读,不按任何按钮:避免动到用户模型(这里以前会点 "Sliders to Subtool Size",风险太大)
    if bool(cfg.get("press_scalemaster_refresh", False)):
        refresh_path = f"{SCALE_MASTER}:Sliders to Subtool Size"
        if zbc.exists(refresh_path):
            try:
                zbc.press(refresh_path)
            except Exception as exc:
                print(f"[ZBTools] 刷新 Scale Master 失败:{exc}")

    unit_name, unit_mult = None, None
    for name, factor in UNIT_FACTORS.items():
        path = f"{SCALE_MASTER}:{name}"
        if not zbc.exists(path):
            continue
        try:
            if float(zbc.get(path)) > 0.5:
                unit_name, unit_mult = name, factor
                break
        except Exception:
            continue
    if unit_name is None:
        unit_name, unit_mult = "mm", 1.0

    values: list[float] = []                                   # 原始读数,单位 = Scale Master 选中的单位
    for axis in ("X", "Y", "Z"):
        path = f"{SCALE_MASTER}:{axis}"
        if not zbc.exists(path):
            return None
        try:
            values.append(abs(float(zbc.get(path))))
        except Exception:
            return None
    if not values or all(v <= 0 for v in values):
        return None
    return {
        "raw": (values[0], values[1], values[2]),          # 原始读数(Scale Master 的单位)
        "unit_name": unit_name,
        "unit_factor": unit_mult,                          # 该单位 -> mm(调用方乘一次,别乘两次)
        "mm": tuple(round(v * unit_mult, 6) for v in values),
        "label": f"Scale Master 读数({unit_name})",
    }


def mesh_bounds(index: int = 1, cfg: dict[str, Any] | None = None) -> tuple[float, float, float, float, float, float]:
    """取包围盒 ``(min_x, min_y, min_z, max_x, max_y, max_z)``,单位 = ZBrush 几何单位。

    ⚠ 关键坑:``query_mesh3d`` 的读数会受**画布变换(transform scale)**影响 ——
    Scale Master 的 "ZBrush Scale Unify" 把 scale 归一到 1 之后读数就会变,
    导致同一模型报出不同尺寸(实测过:报成 2mm)。

    因此这里先把 transform 的 scale 临时设为 1 再读,读完立即还原。
    这样无论读数本来是"几何空间"还是"画布空间",拿到的都是真正的几何尺寸;
    而且 scale 已经是 1 时不会做任何多余操作。
    """
    restore: list[float] | None = None
    normalize = True if cfg is None else bool(cfg.get("normalize_bbox_read", True))
    if normalize:
        try:
            current = [float(v) for v in zbc.get_transform()]
            if len(current) >= 9 and abs(current[3] - 1.0) > 1e-9:
                restore = current
                zbc.set_transform(
                    current[0], current[1], current[2],
                    1.0, 1.0, 1.0,
                    current[6], current[7], current[8],
                )
        except Exception as exc:
            print(f"[ZBTools] 归一 transform 失败(继续按原样读):{exc}")

    try:
        raw = zbc.query_mesh3d(2, index)
    except Exception as exc:
        raise PluginError(
            f"query_mesh3d 取包围盒失败({exc})。请确认当前选中的是多边形网格模型"
            "(Tool > Make PolyMesh3D 之后的对象)。"
        ) from exc
    finally:
        if restore is not None:
            try:
                zbc.set_transform(*restore)
            except Exception as exc:
                print(f"[ZBTools] 还原 transform 失败:{exc}")

    values = [float(v) for v in (raw or [])]
    if len(values) < 6:
        raise PluginError("没取到包围盒数据。请在 Tool > Subtool 里选中一个多边形网格模型后重试。")
    return (values[0], values[1], values[2], values[3], values[4], values[5])


def bounds_size(bounds: tuple[float, ...]) -> tuple[float, float, float]:
    return (abs(bounds[3] - bounds[0]), abs(bounds[4] - bounds[1]), abs(bounds[5] - bounds[2]))


def bounds_center(bounds: tuple[float, ...]) -> tuple[float, float, float]:
    return (
        (bounds[0] + bounds[3]) / 2.0,
        (bounds[1] + bounds[4]) / 2.0,
        (bounds[2] + bounds[5]) / 2.0,
    )


def size_in_mm(
    cfg: dict[str, Any], bounds: tuple[float, ...], unit_scale: float | None = None
) -> tuple[float, float, float]:
    """模型包围盒换算成毫米(即它的实际尺寸)。"""
    scale_value = unit_factor(cfg) if unit_scale is None else float(unit_scale)
    return tuple(v * scale_value for v in bounds_size(bounds))  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# 透视
# --------------------------------------------------------------------------- #


def hub_readout(bounds: tuple[float, ...]) -> list[tuple[str, float]]:
    """读 3D Print Hub 当前显示的 mm 尺寸(仅供参考)。

    ⚠ 这是"上次点过 Update Size Ratios 时"的值,不一定对应当前模型;
    要拿它当依据,必须先在 3D Print Hub 里对当前模型点一次 Update Size Ratios。
    """
    out: list[tuple[str, float]] = []
    for axis in ("X", "Y", "Z"):
        path = f"Zplugin:3D Print Hub:{axis}(mm)"
        if not zbc.exists(path):
            continue
        try:
            out.append((axis, float(zbc.get(path))))
        except Exception:
            continue
    return out


def probe_print_hub(bounds: tuple[float, ...], unit_scale: float | None = None) -> str:
    """把 3D Print Hub 的读数与本次算出的尺寸并排列出,纯对照,不做任何自动换算。"""
    readout = hub_readout(bounds)
    if not readout:
        return "  3D Print Hub 未打开(它显示的也是「上次刷新」的值,仅供参照)"
    size_units = bounds_size(bounds)
    scale_value = float(unit_scale) if unit_scale is not None else 0.0
    index_of = {"X": 0, "Y": 1, "Z": 2}
    parts = []
    for axis, hub_mm in readout:
        mine = size_units[index_of[axis]] * scale_value
        parts.append(f"{axis}: Hub {hub_mm:.2f} mm / 本插件 {mine:.2f} mm")
    return "  " + " | ".join(parts) + "(Hub 值是上次刷新的旧值,只作对照)"


CONVENTIONS: list[tuple[float, str]] = [
    (1.0, "1 unit = 1 mm(毫米建模)"),
    (2.54, "1 unit = 2.54 mm(= 0.1 英寸)"),
    (12.7, "1 unit = 12.7 mm(2 units = 1 英寸,3D Print Hub 常用)"),
    (25.4, "1 unit = 25.4 mm(1 unit = 1 英寸)"),
]


def convention_table(bounds: tuple[float, ...], current: float) -> str:
    """列出常见单位约定下本模型的真实尺寸,便于你选定。"""
    size = bounds_size(bounds)
    lines = []
    for factor, label in CONVENTIONS:
        mark = "  <= 当前" if abs(factor - current) < 1e-9 else ""
        lines.append(
            f"        {label:<44} -> {size[0] * factor:8.2f} x {size[1] * factor:8.2f} x {size[2] * factor:8.2f} mm{mark}"
        )
    return "\n".join(lines)


def resolve_unit_scale(cfg: dict[str, Any], bounds: tuple[float, ...] | None = None) -> tuple[float, str]:
    """尺寸口径:1 ZBrush unit = ? mm(恒为 1;Scale Master 的单位开关只是显示换算)。"""
    return unit_factor(cfg), "1 unit = 1 mm"


# --------------------------------------------------------------------------- #
# 换算
# --------------------------------------------------------------------------- #


def scale_for_true_size(
    cfg: dict[str, Any], zoom: float | None = None, unit_scale: float | None = None
) -> float:
    """让模型 1:1 显示所需的 transform scale(每 unit 占多少 pixol)。"""
    per_pixol = screen.mm_per_pixol(cfg, zoom)
    if per_pixol <= 0.0:
        raise PluginError("换算结果为 0:请检查屏幕参数(ppi)与渲染桥接系数。")
    scale_value = unit_factor(cfg) if unit_scale is None else float(unit_scale)
    return scale_value / per_pixol
