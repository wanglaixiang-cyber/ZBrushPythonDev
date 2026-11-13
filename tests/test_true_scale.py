"""实际大小功能的逻辑测试(用 tests/fake_zbrush.py 的替身,不需要开 ZBrush)。

屏幕数据用真实探测到的两块屏(P270 4K / 内置 Retina)。
运行:  .venv/bin/python tests/test_core.py
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- #
# 这是一份**本地测试**,不是 ZBrush 脚本。
# 正确跑法(终端):.venv/bin/python tests/run_all.py
# 想在 ZBrush 里看功能,请 Load:scripts/load_plugin.py
# ---------------------------------------------------------------- #
try:
    from fake_zbrush import FakeCommands  # noqa: F401
except ImportError:  # 说明不在 tests/ 目录下运行 —— 多半是在 ZBrush 里 Load 了
    _MSG = (
        "⚠ 这是本地测试文件,不在 ZBrush 里运行。\n"
        "  本地跑:终端执行 .venv/bin/python tests/run_all.py\n"
        "  在 ZBrush 里看功能:Load  scripts/load_plugin.py"
    )
    print(_MSG)
    try:
        import zbrush.commands as _zbc
        _zbc.show_note(_MSG, display_duration=10.0)
    except Exception:
        pass
    raise SystemExit(0)

from fake_zbrush import FAKE_DISPLAYS, FakeCommands, install         # noqa: E402
from zbtools.errors import PluginError                            # noqa: E402
from zbtools.features.true_scale import service                      # noqa: E402
from zbtools.zbapi import canvas as kb, model as mb, screen as sb                      # noqa: E402


def install_fake_zbrush(**kwargs) -> FakeCommands:
    """兼容旧写法:造替身 → 装进 sys.modules → 注入假屏幕。"""
    fake = FakeCommands(**kwargs)
    install(fake)
    return fake


BASE_CFG = {
    "display_id": 0, "ppi": 162.51, "pixel_scale": 2.0,
    "diagonal_inch": 27.111, "native_px": [3840, 2160], "logical_pt": [1920, 1080],
    "interpreter": "/nonexistent/python",
    "mm_per_unit": 1.0, "display_unit": "mm",
    "read_size_from_scalemaster": False, "auto_redetect_screen": False,
    "press_scalemaster_refresh": False,
    "bounds_index": 1, "perspective_path": "Draw:Perspective",
    "bridge_factor": 1.0, "flip_y": False, "ascii_labels": False,
}


def test_screen_detection():
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    screen = sb.resolve_screen(BASE_CFG)
    assert screen["name"] == "P270 (1)" and abs(screen["ppi"] - 162.51) < 1e-6, screen
    alt = sb.resolve_screen(dict(BASE_CFG, display_id=1))
    assert alt["name"] == "Built-in Retina Display" and abs(alt["ppi"] - 255.0) < 1e-6
    print(f"  [ok] 自动选中 ZBrush 所在屏:{screen['name']} {screen['diagonal_inch']:.2f}\" ppi={screen['ppi']}")


def test_bbox_read_is_normalized_to_scale_one():
    """回归:query_mesh3d 读数会随 transform scale 变化 -> 必须先在 scale=1 下读。

    这就是"点了 Scale Master 的 ZBrush Scale Unify 之后尺寸变成 2mm"那个 bug 的根因。
    """
    fake = install_fake_zbrush(bounds=(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0),
                               transform_dependent_bbox=True)
    fake.transform[3] = fake.transform[4] = fake.transform[5] = 40.6   # 上次应用留下的 scale
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb

    # applied_k=40.6 表示"这 40.6 是插件上次为 1:1 乘的",所以你的缩放=1
    cfg = dict(BASE_CFG, unit_name="mm", applied_k=40.6, applied_g=None)
    report = service.apply_true_size(cfg)
    expected_scale = 1.0 * (1.0 / sb.mm_per_pixol(cfg))     # 用户缩放 1 × K
    assert abs(report["scale"] - expected_scale) < 1e-6, (report["scale"], expected_scale)
    assert abs(report["size_mm"][0] - 2.0) < 1e-9, report["size_mm"]
    assert 1.0 in fake.bbox_read_at_scale, f"读包围盒时 scale 应归一到 1,实际 {fake.bbox_read_at_scale}"
    assert abs(fake.transform[3] - report["scale"]) < 1e-9, "读完必须还原并写入新的 scale"
    print(f"  [ok] 读数归一:scale=40.6 时仍读到几何尺寸 2 unit -> {report['size_mm'][0]:.0f} mm")


def test_bbox_read_restores_transform():
    fake = install_fake_zbrush(rotation=(10.0, 20.0, 30.0), transform_dependent_bbox=True)
    fake.transform[3] = fake.transform[4] = fake.transform[5] = 7.5
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb

    mb.mesh_bounds(1, dict(BASE_CFG))
    assert fake.transform[3] == 7.5 and fake.transform[6:] == [10.0, 20.0, 30.0], fake.transform
    print(f"  [ok] 读完立即还原 transform:scale={fake.transform[3]} rot={fake.transform[6:]}")


def test_mm_per_pixol_math():
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    per_pixol = sb.mm_per_pixol(BASE_CFG, zoom=1.0)
    assert abs(per_pixol - 2.0 * 25.4 / 162.51) < 1e-9
    assert abs(per_pixol - 25.4 / 81.26) < 1e-4
    scale = mb.scale_for_true_size(dict(BASE_CFG, mm_per_unit=25.4), zoom=1.0)
    assert abs(scale * 2.0 - 162.51) < 0.01, scale * 2.0
    print(f"  [ok] 每 pixol {per_pixol:.5f} mm;1 英寸物件占 {scale * 2:.2f} 物理像素(应 162.51)")


def test_wrong_screen_ratio_matches_history():
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    ratio = sb.mm_per_pixol(BASE_CFG) / sb.mm_per_pixol(dict(BASE_CFG, display_id=1))
    assert 1.55 < ratio < 1.58, ratio
    print(f"  [ok] 用错屏(内置 255ppi)会偏大 {ratio:.3f} 倍 —— 与历史症状吻合")


def test_manual_two_parameters_and_cross_check():
    """探测失败时的兜底:用手动参数(对角线 + PPI)并做自洽核验。"""
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    sb.detect_displays = lambda cfg, force=False: None        # 模拟探测不可用
    cfg = dict(BASE_CFG, diagonal_inch=27.111, ppi=162.51)
    screen = sb.resolve_screen(cfg)
    assert abs(screen["ppi"] - 162.51) < 1e-9 and abs(screen["logical_ppi"] - 81.255) < 1e-3, screen
    assert "⚠" not in sb.cross_check(cfg)
    assert "不自洽" in sb.cross_check(dict(cfg, diagonal_inch=24.0))
    print("  [ok] 两参数(对角线+PPI)可用且能自洽核验;填错时会告警")


def test_display_unit_does_not_change_size():
    """单位开关只换算显示,不改变真实尺寸(用户澄清 + 截图证实)。

    Scale Master:mm 选中显示 2,cm 选中显示 0.2(= 2mm),in 显示 0.079。
    尺寸口径恒为 1 ZBrush unit = 1 mm。
    """
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb

    cfg = dict(BASE_CFG)
    assert mb.unit_factor(dict(cfg, display_unit="mm")) == 1.0
    assert mb.unit_factor(dict(cfg, display_unit="cm")) == 1.0
    assert mb.unit_factor(dict(cfg, display_unit="in")) == 1.0

    base = service.apply_true_size(dict(cfg, display_unit="mm"))
    cm = service.apply_true_size(dict(cfg, display_unit="cm"))
    assert abs(base["size_mm"][0] - 2.0) < 1e-9, base["size_mm"]
    assert abs(cm["size_mm"][0] - 2.0) < 1e-9, cm["size_mm"]          # 尺寸不随单位变
    assert abs(cm["scale"] - base["scale"]) < 1e-9, (cm["scale"], base["scale"])   # 画布 scale 也不变
    print(f"  [ok] 显示单位 mm/cm/in 不改变尺寸:都是 {base['size_mm'][0]:.2f} mm,"
          f"scale {base['scale']:.4f}(提示里会换算成 cm/in 显示)")


def test_size_follows_model_not_constant():
    install_fake_zbrush(bounds=(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0))
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    small = service.apply_true_size(dict(BASE_CFG, mm_per_unit=10.0))
    install_fake_zbrush(bounds=(-5.0, -5.0, -5.0, 5.0, 5.0, 5.0))
    from zbtools.features.true_scale import service as core2
    from zbtools.zbapi import canvas as canvas2, model as model2, screen as screen2
    big = core2.apply_true_size(dict(BASE_CFG, mm_per_unit=10.0))
    assert abs(small["size_mm"][0] - 20.0) < 1e-9 and abs(big["size_mm"][0] - 100.0) < 1e-9
    assert big["on_canvas_px"][0] > small["on_canvas_px"][0] * 4.9
    print(f"  [ok] 尺寸随模型变化:2 unit -> {small['size_mm'][0]:.0f}mm,10 unit -> {big['size_mm'][0]:.0f}mm")


def test_stale_print_hub_readout_ignored():
    fake = install_fake_zbrush(bounds=(-5.0, -5.0, -5.0, 5.0, 5.0, 5.0))
    for axis in "XYZ":
        fake._values[f"Zplugin:3D Print Hub:{axis}(mm)"] = 2.0
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG))
    assert abs(report["size_mm"][0] - 10.0) < 1e-9, report["size_mm"]
    print(f"  [ok] Hub 旧读数 2mm 被忽略:10 unit x 1 mm = {report['size_mm'][0]:.0f} mm")


def test_absolute_scale_fixes_accumulated_canvas_scale():
    """回归(用户实际 case):画布 scale 被历史版本累积到 316.4 -- 必须绝对归位。

    实测确认:Scale Master 报的是"子工具/几何"口径,不随画布 scale 变化,
    所以不能做"乘法记账",只能绝对设置:scale = 真实尺寸 ÷ 每pixol毫米 ÷ 几何尺寸。
    """
    fake = install_fake_zbrush(scalemaster_unit="mm")          # SM 实时读数 = 几何 × scale
    fake.transform[3] = fake.transform[4] = fake.transform[5] = 316.3991   # 累积残留
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG, read_size_from_scalemaster=True)
    report = service.apply_true_size(cfg)

    expected = (2.0 / sb.mm_per_pixol(cfg)) / 2.0             # (真实尺寸 ÷ mm/pixol) ÷ 几何
    assert abs(report["scale"] - expected) < 1e-9, (report["scale"], expected)
    assert report["scale"] > 3.0 and report["scale"] < 3.3, report["scale"]   # ≈ 3.199,不是 316
    assert abs(report["size_mm"][0] - 2.0) < 1e-9, report["size_mm"]
    print(f"  [ok] 累积 scale 316.40 -> {report['scale']:.4f}(模型占 {report['expected_pixols']:.1f} 画布像素 = 2.000mm)")

    # 幂等:再点一次不应改变
    again = service.apply_true_size(cfg)
    assert abs(again["scale"] - report["scale"]) < 1e-9, (again["scale"], report["scale"])
    print("  [ok] 重复点按幂等(绝对公式天然幂等,不需要记账)")


def test_unified_geometry_79mm():
    """几何被 Scale Unify 烘焙成 79.85 unit 时,应显示 79.85mm。"""
    install_fake_zbrush(bounds=(-39.9245, -39.9245, -39.9245, 39.9245, 39.9245, 39.9245),
                        scalemaster_unit="mm")
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG, read_size_from_scalemaster=True)
    report = service.apply_true_size(cfg)
    assert abs(report["size_mm"][0] - 79.849) < 0.01, report["size_mm"]
    expected = (79.849 / sb.mm_per_pixol(cfg)) / 79.849       # = K
    assert abs(report["scale"] - expected) < 1e-6, (report["scale"], expected)
    print(f"  [ok] 烘焙几何 79.85 unit -> 报告 {report['size_mm'][0]:.2f}mm,scale {report['scale']:.4f}")


def test_small_geometry_still_correct():
    """小几何(2 unit)也一样:scale = K,模型占 6.4 画布像素 = 2mm。"""
    install_fake_zbrush(scalemaster_unit="mm")
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG, read_size_from_scalemaster=True)
    report = service.apply_true_size(cfg)
    assert abs(report["expected_pixols"] - 6.4) < 0.05, report["expected_pixols"]
    assert abs(report["scale"] - 3.199) < 0.01, report["scale"]
    print(f"  [ok] 2 unit -> {report['expected_pixols']:.2f} 画布像素 = {report['size_mm'][0]:.2f}mm,scale={report['scale']:.4f}")


def test_effective_size_from_tool_item_paths():
    """回归(探测脚本实测):有效尺寸 = Tool:Geometry:X Size × Tool:Export:Scale。

    真实数字:网格 2.0000193、Export Scale 39.3791770 -> 78.759 mm
    (与 Scale Master 的读数 78.75900268554688 完全吻合)
    """
    fake = install_fake_zbrush(bounds=(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0), scalemaster_unit="mm")
    fake.geometry_size = 2.0000193119049072
    fake.export_scale = 39.37917709350586
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG)

    report = service.apply_true_size(cfg)
    assert abs(report["size_mm"][0] - 78.759) < 0.01, report["size_mm"]
    assert "ZBrush Scale" in report["size_source"], report["size_source"]
    # 分母是网格尺寸(2.0),不是有效尺寸(78.759):Export:Scale 不影响屏幕显示
    expected_scale = (78.759 / sb.mm_per_pixol(cfg)) / 2.0000193119049072
    assert abs(report["scale"] - expected_scale) < 1e-3, (report["scale"], expected_scale)
    assert abs(report["expected_pixols"] - 78.759 / sb.mm_per_pixol(cfg)) < 0.05, report["expected_pixols"]
    print(f"  [ok] 网格 2.0000 × ZBrushScale 39.379 -> 报告 {report['size_mm'][0]:.3f}mm,"
          f"scale {report['scale']:.3f}(占 {report['expected_pixols']:.1f} 画布像素 = 78.76mm)")

    # Export Scale 变化(用户缩放)-> 尺寸跟着变
    fake.export_scale = 39.37917709350586 * 2
    r2 = service.apply_true_size(cfg)
    assert abs(r2["size_mm"][0] - 157.52) < 0.05, r2["size_mm"]
    print(f"  [ok] Export Scale 翻倍 -> 报告 {r2['size_mm'][0]:.2f}mm(跟随)")


def test_scale_unify_bake_keeps_size():
    """回归(用户实测):点 ZBrush Scale Unify 之后尺寸不应变化。

    Unify 会把画布 scale 烘焙进几何(几何 ×K,可见大小不变),
    所以必须靠记账区分"我们的 K 被烘焙"与"模型真被改动"。
    """
    fake = install_fake_zbrush(bounds=(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0), scalemaster_unit="mm")
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG)

    r1 = service.apply_true_size(cfg)
    assert abs(r1["size_mm"][0] - 2.0) < 1e-9, r1["size_mm"]
    assert abs(r1["scale"] - 3.199) < 0.01, r1["scale"]

    # 模拟 ZBrush Scale Unify:几何 ×scale,S 归 1(可见大小不变)
    baked = fake.transform[3]
    fake.bounds = tuple(v * baked for v in fake.bounds)
    fake.transform[3] = fake.transform[4] = fake.transform[5] = 1.0

    r2 = service.apply_true_size(cfg)
    assert abs(r2["size_mm"][0] - 2.0) < 1e-9, r2["size_mm"]
    assert "烘焙" in r2["sizing_basis"], r2["sizing_basis"]
    assert abs(r2["scale"] - 1.0) < 1e-6, r2["scale"]

    r3 = service.apply_true_size(cfg)                       # 幂等
    assert abs(r3["size_mm"][0] - 2.0) < 1e-9, r3["size_mm"]
    assert abs(r3["scale"] - r2["scale"]) < 1e-9, (r3["scale"], r2["scale"])
    print(f"  [ok] Unify 前 {r1['scale']:.4f} -> 后 {r2['scale']:.4f},尺寸始终 {r2['size_mm'][0]:.3f}mm,幂等")

    # 用户真改模型:在"当前几何"基础上再 ×40 -> 尺寸跟着变
    factor = 40.0
    fake.bounds = tuple(v * factor for v in fake.bounds)
    expected_mm = abs(fake.bounds[3] - fake.bounds[0]) * mb.unit_factor(cfg)
    r4 = service.apply_true_size(cfg)
    assert abs(r4["size_mm"][0] - expected_mm) < 1e-6, (r4["size_mm"][0], expected_mm)
    assert "改动" in r4["sizing_basis"], r4["sizing_basis"]
    print(f"  [ok] 用户真改模型 -> 报告 {r4['size_mm'][0]:.2f}mm(几何 ×{factor:g} 后跟着变)")


def test_apply_preserves_view_and_reports_perspective():
    fake = install_fake_zbrush(rotation=(12.0, 34.0, 56.0), perspective_value=1.0)
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG, mm_per_unit=10.0))
    assert fake.transform[6:] == [12.0, 34.0, 56.0], "必须保留当前视角(不改旋转)"
    assert fake._values["Draw:Perspective"] == 1.0, "不得强行改透视"
    assert abs(fake.transform[3] - report["scale"]) < 1e-9
    text = service.summary_text(report, BASE_CFG)
    assert report["perspective"] == "透视视图" and "透视视图" in text   # 精简提示里只保留透视状态
    print(f"  [ok] 保留视角 rot={fake.transform[6:]};透视未改动,提示:{report['perspective'][:20]}…")
    install_fake_zbrush(perspective_value=0.0)
    from zbtools.features.true_scale import service as core2
    from zbtools.zbapi import canvas as canvas2, model as model2, screen as screen2
    assert "正交" in core2.apply_true_size(dict(BASE_CFG))["perspective"]
    print("  [ok] 透视关闭时提示为「非透视/正交」")


def test_apply_centers_and_scales():
    fake = install_fake_zbrush(bounds=(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0))
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG))
    assert abs(fake.transform[0] - 960.0) < 1e-9 and abs(fake.transform[1] - 540.0) < 1e-9
    physical_px = report["on_canvas_px"][0] * BASE_CFG["pixel_scale"]
    assert abs(physical_px / 162.51 * 25.4 - 2.0) < 0.01, physical_px
    assert report["fits_canvas"] is True
    print(f"  [ok] 居中 + 缩放:scale={report['scale']:.4f},屏幕物理像素={physical_px:.2f}px = 2.0mm")


def test_center_offset_for_offcenter_mesh():
    """仅在 center_model=True 时居中(默认不改位置)。"""
    fake = install_fake_zbrush(bounds=(10.0, 0.0, 0.0, 12.0, 2.0, 2.0))
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG, center_model=True))
    scale = report["scale"]
    assert abs(fake.transform[0] - (960.0 - 11.0 * scale)) < 1e-6
    assert abs(fake.transform[1] - (540.0 - 1.0 * scale)) < 1e-6
    print(f"  [ok] 开启居中时偏心模型被摆正:x={fake.transform[0]:.2f} y={fake.transform[1]:.2f}")


def test_default_keeps_position():
    """回归(用户实测):默认不改位置,避免"模型往一个方向飘、窗口反方向偏"。"""
    fake = install_fake_zbrush(bounds=(10.0, 0.0, 0.0, 12.0, 2.0, 2.0))
    fake.transform[0], fake.transform[1] = 300.0, 700.0          # 模型不在画布中心
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG))
    assert fake.transform[0] == 300.0 and fake.transform[1] == 700.0, fake.transform[:3]
    assert abs(report["scale"] - 3.199) < 0.01
    print(f"  [ok] 默认保持位置不动:pos=({fake.transform[0]:.0f}, {fake.transform[1]:.0f}),scale={report['scale']:.4f}")


def test_extreme_scale_warning():
    """回归(用户工程):mesh 很小 + ZBrush Scale 很大 -> 所需 scale 超出可用范围,必须提示。"""
    fake = install_fake_zbrush(bounds=(-0.018, -0.018, -0.018, 0.018, 0.018, 0.018),
                               scalemaster_unit="mm")
    fake.geometry_size = 0.0359
    fake.export_scale = 10000.0
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG, read_size_from_scalemaster=False)
    report = service.apply_true_size(cfg)
    assert abs(report["size_mm"][0] - 359.0) < 1.0, report["size_mm"]
    assert report["extreme_scale"] is True, report["scale"]
    assert "ZBrush Scale Unify" in report["scale_warning"], report["scale_warning"]
    text = service.summary_text(report, cfg)
    assert text.startswith("警告:") and "超出 ZBrush 可用范围" in text, text
    assert "真实尺寸" not in text and "画布边框" not in text, "比例不可信时不应再显示其它信息"
    assert len(service.warning_lines(report, cfg)) == 1
    print(f"  [ok] 异常时只抛警告:{text.splitlines()[0][:46]}…")


def test_fits_canvas_warning():
    install_fake_zbrush(bounds=(-500.0, -500.0, -500.0, 500.0, 500.0, 500.0))
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    report = service.apply_true_size(dict(BASE_CFG))
    assert report["fits_canvas"] is False
    note = service.summary_text(report, BASE_CFG)
    # 超出画布不是"比例不可信",所以仍显示正常信息 + 一行提示
    assert not note.startswith("警告:"), note
    assert "真实尺寸" in note and "模型超出画布" in note, note
    assert service.warning_lines(report, BASE_CFG) == [], "超出画布不应触发独占警告"
    print(f"  [ok] 超屏告警:1000mm 需 {report['on_canvas_px'][0]:.0f} pixol,画布 1920")


def test_verify_bridge():
    install_fake_zbrush()
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    cfg = dict(BASE_CFG)
    report = service.apply_true_size(cfg)
    expected = report["canvas_mm"][0]
    factor, message = service.verify_bridge(cfg, expected, report)
    assert abs(factor - 1.0) < 1e-9 and "相符" in message, message
    factor2, _ = service.verify_bridge(cfg, expected * 1.2, report)
    assert abs(factor2 - 1.2) < 1e-9, factor2
    print(f"  [ok] 桥接核验:实测=预期 -> 1.000;实测 +20% -> {factor2:.3f}")


def test_diagnostics_data_chain():
    install_fake_zbrush(scalemaster_unit="cm")
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    text = service.diagnostics(dict(BASE_CFG, unit_name="cm", cross_check_scalemaster=True))
    for kw in ("[1] 模型数据", "[2] 屏幕数据", "[3] 映射", "包围盒", "单位", "模型实际尺寸",
               "当前屏", "屏幕参数核验", "画布", "核验"):
        assert kw in text, kw
    print("  [ok] 诊断按 [模型数据] -> [屏幕数据] -> [映射] 三段输出")


def test_mesh_query_failure_message():
    fake = install_fake_zbrush()
    fake.query_mesh3d = lambda prop, index=None: (_ for _ in ()).throw(RuntimeError("no mesh"))
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas as kb, model as mb, screen as sb
    try:
        mb.mesh_bounds(1)
    except PluginError as exc:
        assert "Make PolyMesh3D" in str(exc)
        print("  [ok] 未选中网格时给出中文引导")
        return
    raise AssertionError("应当抛出 PluginError")


def main():
    tests = [
        ("屏幕探测与选择", test_screen_detection),
        ("包围盒读数归一到 scale=1(回归)", test_bbox_read_is_normalized_to_scale_one),
        ("读完还原 transform", test_bbox_read_restores_transform),
        ("换算:每 pixol 毫米数", test_mm_per_pixol_math),
        ("用错屏的历史比例复现", test_wrong_screen_ratio_matches_history),
        ("手动两参数 + 自洽核验", test_manual_two_parameters_and_cross_check),
        ("显示单位不改变尺寸(口径恒 1 unit = 1 mm)", test_display_unit_does_not_change_size),
        ("尺寸随模型变化(回归)", test_size_follows_model_not_constant),
        ("Hub 旧读数被忽略(回归)", test_stale_print_hub_readout_ignored),
        ("有效尺寸 = 网格 × Export Scale(探测实测)", test_effective_size_from_tool_item_paths),
        ("Scale Unify 烘焙不改尺寸(回归)", test_scale_unify_bake_keeps_size),
        ("累积 scale 绝对归位 + 幂等(回归)", test_absolute_scale_fixes_accumulated_canvas_scale),
        ("烘焙几何 79.85 unit -> 79.85mm", test_unified_geometry_79mm),
        ("小几何 2 unit -> 2mm", test_small_geometry_still_correct),
        ("保留视角 + 只提示透视", test_apply_preserves_view_and_reports_perspective),
        ("居中与缩放正确", test_apply_centers_and_scales),
        ("开启居中时偏心模型摆正", test_center_offset_for_offcenter_mesh),
        ("默认不动位置(回归:飘走)", test_default_keeps_position),
        ("所需 scale 超范围时提示(回归)", test_extreme_scale_warning),
        ("超屏告警", test_fits_canvas_warning),
        ("显示比例核验", test_verify_bridge),
        ("诊断三段数据链", test_diagnostics_data_chain),
        ("未选中网格报错", test_mesh_query_failure_message),
    ]
    failed = 0
    for name, fn in tests:
        print(f"* {name}")
        try:
            fn()
        except Exception as exc:
            failed += 1
            import traceback
            print(f"  [FAIL] {exc}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
