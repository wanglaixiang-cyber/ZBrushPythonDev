from __future__ import annotations

from typing import Any

import zbrush.commands as zbc
from ... import notes
from ... import settings
from ...settings import STATE_VERSION
from ...zbapi import canvas, model, screen
from ...errors import PluginError


def apply_true_size(cfg: dict[str, Any], remember: list[float] | None = None) -> dict[str, Any]:
    """按当前视图显示实际大小。

    关键机制(实测得出):
      * 真实尺寸 = **模型固有尺寸** × 面板单位;
      * 屏幕上的画布像素数 = 几何尺寸 G_raw × 画布 scale;
      * **ZBrush Scale Unify 会把画布 scale 烘焙进几何**(几何 ×K,可见大小不变)——
        所以必须区分"用户真的改了模型"和"我们自己的 K 被烘焙了",否则尺寸会被放大 K 倍。

    判定规则(用两个记账量:固有尺寸 G_int、上次设置的画布 scale K_last):
        G_raw ≈ G_int            -> 几何没变,尺寸不变
        G_raw ≈ G_int × K_last   -> 我们的 K 被烘焙进去了,尺寸仍不变
        其它                     -> 用户真的改了模型,固有尺寸 = G_raw
    """
    bounds = model.mesh_bounds(int(cfg["bounds_index"]), cfg)      # G_raw:几何尺寸(scale 归一后读)
    size_units = model.bounds_size(bounds)
    center = model.bounds_center(bounds)
    canvas_w, canvas_h = canvas.canvas_size()
    zoom = canvas.canvas_zoom()
    if bool(cfg.get("auto_redetect_screen", True)):
        try:
            screen.detect_displays(cfg, force=True)
        except Exception as exc:
            print(f"[ZBTools] 重新探测屏幕失败(沿用上次结果):{exc}")
    per_pixol = screen.mm_per_pixol(cfg, zoom)
    unit = model.unit_factor(cfg)          # 尺寸口径:1 unit = 1 mm(与 SM 的单位开关无关)
    if per_pixol <= 0.0:
        raise PluginError("每 pixol 毫米数为 0,请检查屏幕参数。")
    if size_units[0] <= 0.0:
        raise PluginError("几何尺寸为 0,无法计算。")

    try:
        current = [float(v) for v in zbc.get_transform()]
    except Exception:
        current = [canvas_w / 2.0, canvas_h / 2.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
    scale_now = current[3] if len(current) >= 9 else 1.0
    rotation = tuple(current[6:9]) if len(current) >= 9 else (0.0, 0.0, 0.0)

    # ---- 有效尺寸:优先 ZBrush 自己的报告 ----
    # 为什么不能用几何包围盒:用户的缩放可能落在"子工具自身的变换"里,
    # query_mesh3d 读不到那一层(实测:几何 2.0000,而 ZBrush 报 78.759mm)。
    sm_now: float | None = None
    unit_eff = unit
    report_size = None
    effective = size_units[0]
    size_source = "几何包围盒"

    # 首选:直接读 ZBrush 的"网格尺寸 × Export:Scale"(实时、无副作用)
    live = None
    try:
        live = model.tool_effective_size()
    except Exception as exc:
        print(f"[ZBTools] 读取 Tool:Geometry:* Size 失败:{exc}")
    mesh_for_display = size_units[0]          # 实际渲染的几何尺寸(屏幕缩放的分母)
    effective_xyz: tuple[float, float, float] = size_units
    if live:
        effective_xyz = live[0]
        effective = effective_xyz[0]
        mesh_for_display = live[2] if live[2] > 0 else size_units[0]
        size_source = f"网格 {live[2]:.4f} × ZBrush Scale {live[1]:.4f}"

    # 次选:Scale Master 的读数(只读;不新鲜时作为对照)
    try:
        report_size = model.read_model_size_mm(cfg)
    except Exception as exc:
        print(f"[ZBTools] 读取 Scale Master 读数失败:{exc}")
    if not live and report_size:
        effective = report_size["raw"][0]
        unit_eff = float(report_size["unit_factor"])
        size_source = report_size["label"]

    if report_size and live:
        sm_unit = float(report_size["unit_factor"])
        sm_mm = report_size["raw"][0] * sm_unit
        live_mm = effective * unit_eff
        if abs(sm_mm - live_mm) / max(live_mm, 1e-9) > 0.02:
            print(f"[ZBTools] 对照:Scale Master 报 {sm_mm:.2f} mm / 有效尺寸算 {live_mm:.2f} mm"
                  "(若差别大,可能是 Scale Master 没刷新)")

    # ---- 判定"固有尺寸"(识别 Scale Unify 把我们的 K 烘焙进有效尺寸) ----
    g_int = cfg.get("intrinsic_g")
    k_last = cfg.get("last_applied_scale")
    eff_last = cfg.get("eff_at_apply")
    if (g_int is None or k_last is None or eff_last is None
            or float(g_int) <= 0 or float(k_last) <= 0 or float(eff_last) <= 0):
        intrinsic_xyz = effective_xyz
        intrinsic = effective_xyz[0]
        basis = "首次:以当前尺寸为准"
    else:
        g_int = float(g_int)
        k_last = float(k_last)
        eff_last = float(eff_last)
        if abs(effective - eff_last) / eff_last <= 0.01:
            intrinsic_xyz = tuple(float(v) for v in (cfg.get("intrinsic_xyz") or effective_xyz))
            intrinsic = g_int
            basis = "尺寸未变"
        elif abs(effective - eff_last * k_last) / (eff_last * k_last) <= 0.01:
            intrinsic_xyz = tuple(float(v) for v in (cfg.get("intrinsic_xyz") or effective_xyz))
            intrinsic = g_int
            basis = f"识别到 Scale Unify 烘焙 ×{k_last:.3f}:尺寸保持不变"
        else:
            intrinsic_xyz = effective_xyz
            intrinsic = effective
            basis = f"模型已改动 {g_int:.3f} -> {effective:.3f}:尺寸跟随"

    real_mm_xyz = tuple(v * unit_eff for v in intrinsic_xyz)     # 逐轴真实尺寸(对应 SM 的 X/Y/Z)
    real_mm = real_mm_xyz[0]
    target_pixols = real_mm / per_pixol
    # 分母必须是"实际渲染的几何"(Export:Scale 不影响屏幕显示,只决定模型在 ZBrush 里的尺寸标定)
    scale_new = target_pixols / mesh_for_display
    # ZBrush 的 transform scale 有可用范围(实测:要求 32000 时被截断成 4000,显示就小了 8 倍)
    extreme_scale = scale_new > float(cfg.get("max_canvas_scale", 4000.0)) or scale_new < 0.001
    scale_warning = ""
    if extreme_scale:
        scale_warning = (f"所需缩放 {scale_new:.0f} 超出 ZBrush 可用范围(约 4000),"
                         "显示会偏小;请先按 ZBrush Scale Unify 把缩放烘焙进网格")

    if bool(cfg.get("center_model", False)):
        # 居中:把网格包围盒中心移到画布中心(注意 mesh 空间很小时会被 scale 放大成很大偏移)
        center_y = -center[1] if bool(cfg["flip_y"]) else center[1]
        position = (canvas_w / 2.0 - center[0] * scale_new,
                    canvas_h / 2.0 - center_y * scale_new, 0.0)
    else:
        # 默认:保持模型当前在画布上的位置不动(只改尺寸),避免"模型飘走"
        position = (current[0], current[1], 0.0) if len(current) >= 9 else (canvas_w / 2.0, canvas_h / 2.0, 0.0)

    zbc.set_transform(
        position[0], position[1], 0.0,
        scale_new, scale_new, scale_new,
        rotation[0], rotation[1], rotation[2],
    )

    cfg["intrinsic_g"] = intrinsic
    cfg["intrinsic_xyz"] = list(intrinsic_xyz)
    cfg["eff_at_apply"] = effective
    cfg["raw_at_apply"] = size_units[0]
    cfg["last_applied_scale"] = scale_new
    settings.save(cfg)

    canvas_mm = (canvas_w * per_pixol, canvas_h * per_pixol)
    report: dict[str, Any] = {
        "bounds": bounds,
        "size_units": size_units,
        "raw_units": size_units[0],
        "effective_units": effective,
        "mesh_units": mesh_for_display,
        "mesh_xyz": (live[2], live[2], live[2]) if live else size_units,
        "zbrush_scale": (live[1] if live else 1.0),
        "mesh_xyz": tuple(live[0]) if live else size_units,
        "zbrush_scale": float(live[1]) if live else 1.0,
        "intrinsic_units": intrinsic,
        "intrinsic_xyz": intrinsic_xyz,
        "effective_xyz": effective_xyz,
        "sizing_basis": basis,
        "size_mm": real_mm_xyz,
        "size_source": f"{size_source},固有 {intrinsic:.3f}",
        "scale": scale_new,
        "scale_before": scale_now,
        "target_pixols": target_pixols,
        "expected_pixols": mesh_for_display * scale_new,
        "scale_warning": scale_warning,
        "extreme_scale": extreme_scale,
        "canvas": (canvas_w, canvas_h),
        "canvas_zoom": zoom,
        "mm_per_pixol": per_pixol,
        "canvas_mm": canvas_mm,
        "on_canvas_px": tuple(v * scale_new for v in size_units),
        "position": position,
        "rotation": rotation,
        "perspective": canvas.perspective_report(cfg),
        "orthographic": not bool((canvas.perspective_state(cfg)[1] or 0.0) > 0.5),
        "screen": screen.resolve_screen(cfg),
        "fits_canvas": mesh_for_display * scale_new <= canvas_w * 1.001,
        "previous_view": remember,
        "unit_scale": unit_eff,
        "unit_source": "1 unit = 1 mm",
        "report_size": report_size,
    }

    print(
        "[ZBTools] 实际大小已应用 | "
        f"真实尺寸 {intrinsic:.3f} × {unit_eff:g} = {real_mm:.3f} mm | "
        f"网格 {mesh_for_display:.4f} × scale {scale_new:.4f} = {mesh_for_display * scale_new:.1f} 画布像素 | "
        f"{basis} | scale {scale_now:.3f} -> {scale_new:.3f} | "
        f"屏={report['screen']['name']} ppi={report['screen']['ppi']:.1f}"
    )
    return report


def warning_lines(report: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """只挑"**给不出正确测量比例**"的情形 —— 这类才独占提示窗口。

    目前只有一种:所需画布 scale 超出 ZBrush 可用范围(实测会被截断到 4000,
    实际显示比报告值小若干倍,比例已不可信)。

    注意:「模型超出画布」不算警告 —— 比例仍然是对的,只是看不到全部,
    它作为提示里的一行普通信息出现。
    """
    out: list[str] = []
    if report.get("scale_warning"):
        out.append(f"警告:{report['scale_warning']}")
    return out


def note_values(report: dict[str, Any], cfg: dict[str, Any]) -> dict[str, str]:
    """提示模板可用的变量(全部是已格式化的字符串,直接替换进模板即可)。"""
    unit_name, display_factor = model.display_unit(cfg)
    to_unit = (1.0 / display_factor) if display_factor else 1.0        # mm -> 显示单位

    def _dim(value_mm: float) -> str:
        shown = value_mm * to_unit
        return f"{shown:.2f}" if abs(shown) >= 0.01 else f"{shown:.3f}"

    w, h, d = report["size_mm"]
    mesh = tuple(report.get("mesh_xyz") or report.get("size_units") or (0.0, 0.0, 0.0))
    screen = report.get("screen") or {}
    name = str(screen.get("name") or "").replace("手动 ", "").strip()
    return {
        # 模型尺寸(按显示单位)
        "x": _dim(w), "y": _dim(h), "z": _dim(d),
        "unit": unit_name,
        # 模型尺寸(原始 mm)
        "x_mm": f"{w:.2f}", "y_mm": f"{h:.2f}", "z_mm": f"{d:.2f}",
        # 网格尺寸(unit)与 ZBrush Scale
        "mesh_x": f"{mesh[0]:.3f}", "mesh_y": f"{mesh[1]:.3f}", "mesh_z": f"{mesh[2]:.3f}",
        "zbrush_scale": f"{float(report.get('zbrush_scale') or 1.0):.4f}",
        # 屏幕
        "screen": name, "inch": f"{float(screen.get('diagonal_inch') or 0.0):.1f}",
        "ppi": f"{float(screen.get('ppi') or 0.0):.0f}",
        # 画布
        "canvas_mm": f"{report['canvas_mm'][0]:.1f}", "canvas_h_mm": f"{report['canvas_mm'][1]:.1f}",
        "canvas_px": f"{report['canvas'][0]:.0f}", "canvas_px_h": f"{report['canvas'][1]:.0f}",
        # 状态与诊断
        "basis": (str(report.get("sizing_basis") or "")
                  if any(k in str(report.get("sizing_basis") or "") for k in ("烘焙", "改动")) else ""),
        "basis_full": str(report.get("sizing_basis") or ""),
        "perspective": str(report.get("perspective") or ""),
        "source": str(report.get("size_source") or ""),
        "scale": f"{float(report.get('scale') or 0.0):.4f}",
        "target_pixols": f"{float(report.get('target_pixols') or 0.0):.1f}",
        "mesh_for_display": f"{float(report.get('mesh_units') or 0.0):.4f}",
        "scale_before": f"{float(report.get('scale_before') or 0.0):.4f}",
        "mm_per_pixol": f"{float(report.get('mm_per_pixol') or 0.0):.5f}",
        "fits": "" if report.get("fits_canvas", True) else "模型超出画布",
        "scale_warning": str(report.get("scale_warning") or ""),
        "warnings": "\n".join(warning_lines(report, cfg)),
    }


#: 默认提示模板(可在 settings.json 的 note_template 里改)


DEFAULT_NOTE_TEMPLATE: list[str] = [
    "真实尺寸:{x} x {y} x {z} {unit}",
    "屏幕 {screen} {inch}\" {ppi}ppi",
    "画布边框 {canvas_mm}mm",
    "{basis}",          # 仅异常时才有内容(识别到烘焙 / 模型已改动),正常时自动跳过
    "{perspective}",    # 透视视图 / 正交视图
    "{fits}",           # 仅超出画布时才有内容
    "{scale_warning}",  # 仅所需缩放超出 ZBrush 可用范围时才有内容
]


def render_note(template: list[str], values: dict[str, str]) -> str:
    """按模板渲染提示;未知变量原样保留并在控制台提示。"""
    lines: list[str] = []
    unknown: set[str] = set()
    for raw in template:
        if not str(raw).strip():
            continue
        try:
            rendered = str(raw).format_map(_SafeDict(values, unknown))
        except Exception as exc:
            rendered = str(raw)
            print(f"[ZBTools] 提示模板渲染失败:{exc}")
        if rendered.strip():                      # 渲染后为空的行自动跳过(例如没有告警时的 {fits})
            lines.append(rendered)
    if unknown:
        print(f"[ZBTools] 模板里有未知变量(已原样保留):{sorted(unknown)}")
        print(f"[ZBTools] 可用变量:{sorted(values)}")
    return "\n".join(lines)


class _SafeDict(dict):
    """让 {未知变量} 原样保留,不抛 KeyError。"""

    def __init__(self, base: dict[str, str], missing: set[str]) -> None:
        super().__init__(base)
        self._missing = missing

    def __missing__(self, key: str) -> str:  # noqa: D105
        self._missing.add(key)
        return "{" + key + "}"


def summary_text(report: dict[str, Any], cfg: dict[str, Any]) -> str:
    """渲染提示窗口内容。

    模板来自 `~/.zbtools/settings.json` 的 `note_template`(缺省用 DEFAULT_NOTE_TEMPLATE)。
    可用变量见 note_values();诊断信息里会打印"变量 -> 当前值"对照,方便你改。
    """
    # 出现异常时只抛警告,不再显示其它信息
    warnings = warning_lines(report, cfg)
    if warnings:
        return "\n".join(warnings)

    template = cfg.get("note_template") or DEFAULT_NOTE_TEMPLATE
    if not isinstance(template, (list, tuple)):
        template = DEFAULT_NOTE_TEMPLATE
    return render_note([str(v) for v in template], note_values(report, cfg))


def verify_bridge(cfg: dict[str, Any], measured_canvas_width_mm: float,
                  report: dict[str, Any] | None = None) -> tuple[float, str]:
    """核对「ZBrush 渲染桥接项」:用尺子量画布边框宽度,和预期值比对。

    只有这一项 API 不暴露(是否按逻辑点绘制),确认一次即可长期锁定。
    返回 (新的核对系数, 说明文字)。
    """
    if measured_canvas_width_mm <= 0:
        raise PluginError("实测宽度必须大于 0。")

    # 始终按当前设置重算预期值(报告可能来自改动之前的设置,别用它)
    canvas_w, _ = canvas.canvas_size()
    expected = canvas_w * screen.mm_per_pixol(cfg)

    factor = measured_canvas_width_mm / expected
    old = float(cfg.get("bridge_factor", 1.0) or 1.0)
    cfg["bridge_factor"] = old * factor
    text = (
        f"画布宽度:预期 {expected:.1f} mm,实测 {measured_canvas_width_mm:.1f} mm"
        f" → 核对系数 {old:.4f} -> {cfg['bridge_factor']:.4f}"
    )
    if abs(factor - 1.0) <= 0.02:
        text += "(偏差 ≤2%,数据与显示器相符,无需修正)"
    return float(cfg["bridge_factor"]), text


# --------------------------------------------------------------------------- #
# 诊断
# --------------------------------------------------------------------------- #


def diagnostics(cfg: dict[str, Any], last_report: dict[str, Any] | None = None) -> str:
    """按数据链打印:[1] 模型数据(ZBrush) -> [2] 屏幕数据(显示器) -> [3] 映射。"""
    lines: list[str] = ["=== ZBTools 实际大小 数据链 ==="]

    # ---------------- [1] 模型数据(来自 ZBrush) ----------------
    lines.append(f"ZBTools 实际大小 诊断(状态版本 {STATE_VERSION})")
    lines.append("[1] 模型数据(来自 ZBrush)")
    try:
        live = model.tool_effective_size()
    except Exception:
        live = None
    if live:
        lines.append(f"    Tool:Geometry:* Size = "
                     + " x ".join(f"{v:.4f}" for v in (live[2], live[2], live[2]))
                     + f" | Tool:Export:Scale = {live[1]:.4f}")
        lines.append("    ZBrush 有效尺寸(逐轴)= "
                     + " x ".join(f"{v:.4f}" for v in live[0]))
    try:
        bounds = model.mesh_bounds(int(cfg["bounds_index"]), cfg)
    except PluginError as exc:
        bounds = None
        lines.append(f"    包围盒:不可用 —— {exc}")
    if bounds is not None:
        size_units = model.bounds_size(bounds)
        try:
            unit_scale, unit_source = model.resolve_unit_scale(cfg, bounds)  # 只来自你的设置
        except Exception:
            unit_scale, unit_source = model.unit_factor(cfg), f"面板单位 {cfg.get('unit_name')}"
        size_mm = model.size_in_mm(cfg, bounds, unit_scale)
        try:
            lines.append(f"    活动工具:{zbc.get_active_tool_path()}")
        except Exception:
            pass
        lines.append(
            f"    包围盒(unit): {size_units[0]:.4f} x {size_units[1]:.4f} x {size_units[2]:.4f}"
        )
        lines.append(f"    单位换算: 1 unit = {unit_scale:.4f} mm(来源:{unit_source})")
        try:
            current = [float(v) for v in zbc.get_transform()]
            scale_now = current[3]
        except Exception:
            scale_now = 0.0
        lines.append(
            f"    当前画布缩放 scale = {scale_now:.4f} | 记账:固有尺寸 "
            f"{cfg.get('intrinsic_g')} unit(逐轴 {cfg.get('intrinsic_xyz')}),"
            f"上次有效尺寸 {cfg.get('eff_at_apply')},上次设置的 scale "
            f"{cfg.get('last_applied_scale')}"
        )
        lines.append(
            "    (Scale Unify 会把画布 scale 烘焙进几何;插件靠上面两个记账量区分"
            "'我们的 K 被烘焙' 与 '模型真被改动')"
        )
        reported = None
        try:
            reported = model.read_model_size_mm(cfg)
        except Exception as exc:
            lines.append(f"    读 ZBrush 报告尺寸失败:{exc}")
        if reported:
            rm = reported["mm"]
            lines.append(
                f"    参考:Scale Master 报 {rm[0]:.2f} x {rm[1]:.2f} x {rm[2]:.2f} mm"
                "(它读的是含烘焙的几何口径,不参与尺寸计算)"
            )
        hub_text = model.probe_print_hub(bounds, unit_scale)
        if hub_text.strip():
            lines.append("    " + hub_text.strip().replace("\n", "\n    "))
        lines.append("    常见单位约定下本模型的真实尺寸(选一个填到「每单位毫米」):")
        lines.append(model.convention_table(bounds, model.unit_factor(cfg)))
        lines.append(
            f"    => 模型实际尺寸: {size_mm[0]:.2f} x {size_mm[1]:.2f} x {size_mm[2]:.2f} mm"
        )

    # ---------------- [2] 屏幕数据(来自显示器) ----------------
    lines.append("[2] 屏幕数据(显示器 EDID + 当前显示模式)")
    lines.append(screen.screen_report(cfg))
    try:
        info = screen.resolve_screen(cfg)
        native_ratio = (
            info["native_px"][0] / info["logical_pt"][0] if info["logical_pt"][0] else 1.0
        )
        lines.append(
            f"    当前屏:{info['name']} | 物理 {info['diagonal_inch']:.3f} 英寸 | "
            f"原生 {info['native_px'][0]}x{info['native_px'][1]} | "
            f"逻辑 {info['logical_pt'][0]}x{info['logical_pt'][1]}"
        )
        physical = info.get("physical_mm") or []
        if len(physical) == 2:
            lines.append(f"    物理尺寸(EDID): {physical[0]:.1f} x {physical[1]:.1f} mm")
        lines.append(
            f"    => ppi {info['ppi']:.2f} | 逻辑点/英寸 {info['logical_ppi']:.2f} | "
            f"原生÷逻辑 = {native_ratio:.3f}"
        )
    except PluginError as exc:
        lines.append(f"    屏幕参数不可用:{exc}")
        info = None
    check = screen.cross_check(cfg)
    if check:
        lines.append("    " + check.replace("\n", "\n    "))

    # ---------------- [3] 映射(画布 -> 屏幕) ----------------
    lines.append("[3] 映射(画布 pixol -> 屏幕物理像素)")
    zoom = canvas.canvas_zoom()
    canvas_w, canvas_h = canvas.canvas_size()
    pane = zbc.get_canvas_pan()
    bridge = float(cfg.get("bridge_factor", 1.0) or 1.0)
    pixel_scale = float(cfg.get("pixel_scale") or 2.0)
    per_pixol = screen.mm_per_pixol(cfg, zoom)
    lines.append(
        f"    画布 {canvas_w:.0f} x {canvas_h:.0f} pixol | canvas_zoom {zoom:.4f} | pan {pane}"
    )
    lines.append(
        f"    渲染桥接 = canvas_zoom {zoom:.3f} × 原生÷逻辑 {pixel_scale:.3f} × 核对系数 {bridge:.4f}"
        f" = {zoom * pixel_scale * bridge:.4f} 物理像素/pixol"
    )
    lines.append(f"    => 每个 pixol = {per_pixol:.5f} mm")
    lines.append(
        f"    核验(量一次):画布边框应为 {canvas_w * per_pixol:.1f} x {canvas_h * per_pixol:.1f} mm"
        " —— 不一致就用「核验显示比例」输入**你量到的**宽度"
    )
    if info:
        logical = info.get("logical_pt") or [0, 0]
        if logical[0] and (canvas_w > logical[0] * 1.001 or canvas_h > logical[1] * 1.001):
            lines.append(
                f"    ⚠ 文档 {canvas_w:.0f}x{canvas_h:.0f} 比屏幕逻辑分辨率 "
                f"{logical[0]}x{logical[1]} 大:ZBrush 可能会缩放显示画布,"
                "而 get_canvas_zoom() 不一定反映这种自动缩放 —— 这正是'数字对但屏幕不对'的典型原因,"
                "请用「核验显示比例」量一次画布边框来定标"
            )
        else:
            lines.append("    (文档不大于屏幕逻辑分辨率,画布应是 1:1 显示)")
    if abs(bridge - 1.0) > 1e-9:
        lines.append(f"    已应用过桥接校正系数 {bridge:.4f}")
    if screen:
        screen_width_mm = (info.get("physical_mm") or [0])[0]
        if screen_width_mm:
            lines.append(
                f"    画布应占屏幕宽度 {canvas_w * per_pixol / screen_width_mm * 100:.1f}%"
                f"(屏幕宽 {screen_width_mm:.1f} mm)"
            )
    if bounds is not None:
        try:
            unit_scale, _ = model.resolve_unit_scale(cfg, bounds)
        except Exception:
            unit_scale = model.unit_factor(cfg)
        lines.append(
            f"    => 1:1 所需 scale = {model.scale_for_true_size(cfg, zoom, unit_scale):.4f} pixol/unit"
        )
    exists, value = canvas.perspective_state(cfg)
    lines.append(f"    透视项 '{cfg['perspective_path']}':存在={exists} 当前值={value}")
    try:
        transform = zbc.get_transform()
        lines.append(
            "    当前 transform:pos=(" + ", ".join(f"{v:.2f}" for v in transform[:3])
            + ") scale=(" + ", ".join(f"{v:.4f}" for v in transform[3:6])
            + ") rot=(" + ", ".join(f"{v:.1f}" for v in transform[6:9]) + ")"
        )
    except Exception as exc:
        lines.append(f"    读取 transform 失败:{exc}")

    # ---- 提示模板:变量 -> 当前值 对照 ----
    lines.append("")
    lines.append("=== 提示模板与最近一次应用(可在 settings.json 的 note_template 里改)===")
    if last_report:
        lines.append("最近一次「显示实际大小」的提示内容:")
        try:
            for line in summary_text(last_report, cfg).splitlines():
                lines.append("    " + line)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"    (渲染失败:{exc})")
        lines.append("本次应用的关键数值:")
        for label, key in (("网格(渲染分母)", "mesh_units"), ("ZBrush Scale", "zbrush_scale"),
                           ("所需画布像素", "target_pixols"), ("写入的 scale", "scale"),
                           ("写入前 scale", "scale_before"), ("位置", "position"),
                           ("固有尺寸(逐轴)", "intrinsic_xyz"), ("有效尺寸(逐轴)", "effective_xyz")):
            lines.append(f"    {label} = {last_report.get(key)}")
        if last_report.get("scale_warning"):
            lines.append(f"    ⚠ {last_report['scale_warning']}")
        lines.append("可用变量(花括号里就是变量名):")
        try:
            for key, value in sorted(note_values(last_report, cfg).items()):
                lines.append(f"    {{{key}}} = {value}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"    (变量表生成失败:{exc})")
    else:
        lines.append("(还没点过「显示实际大小」;先点一次再收集诊断)")

    text = "\n".join(lines)
    print(text)
    return text

# 供本功能包直接引用的公共入口(避免 feature/__init__ 里到处 import 内部模块)
from ...zbapi.clipboard import copy_to_clipboard          # noqa: E402
from ...zbapi import canvas, model, screen                # noqa: E402
from ... import settings                                  # noqa: E402

__all__ = ["apply_true_size", "summary_text", "verify_bridge", "diagnostics",
           "warning_lines", "note_values", "render_note", "DEFAULT_NOTE_TEMPLATE",
           "copy_to_clipboard", "canvas", "model", "screen", "settings", "PluginError"]
