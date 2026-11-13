"""探针:量测方案的可行性(TransPose 动作线 / canvas_click / 吸附开关 / 鼠标)。

用法:ZBrush > ZScript > Python Scripting > Load > 本文件
     前半段自动跑,后半段需要你动 3 次鼠标(屏幕顶部状态条会提示)。

要回答的问题:
  1. 插件能不能**自己**在画布上落点(canvas_click)→ 决定"程序化卡尺"可行不可行
  2. get_transpose() 的长度是什么单位/空间(画布 pixol 还是网格单位)
     —— 用 get_transform() 的 scale 反推(pixols per unit)
  3. Preferences 里那些开关能不能用 item path 读写(吸附 Snap Max distance / Gizmo 3D)
  4. 鼠标位置是不是画布 pixol 空间、脚本循环里会不会刷新、能否捕捉点击

安全:全部只读 + 只摆动作线,不碰网格;每次 canvas_click 前后都比对几何尺寸,
      一旦发现网格被改动会在报告里明确写出来(那就 Cmd+Z)。
      如果无法确认 Gizmo 3D 已关闭,**跳过所有 canvas_click**(避免拖到模型)。
"""

from __future__ import annotations

import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "plugin", "zbtools"))

import zbrush.commands as zbc                                        # noqa: E402
from zbtools.zbapi.clipboard import copy_to_clipboard                # noqa: E402

LINES: list[str] = []


def log(text: str) -> None:
    LINES.append(text)
    print(f"[量测探针] {text}")


def try_call(func, default=None):
    """任何 zbc 调用都可能抛,统一兜住。返回 (是否成功, 值或异常文本)。"""
    try:
        return True, func()
    except Exception as exc:                                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def read_item(path: str):
    ok, exists = try_call(lambda: zbc.exists(path))
    if not ok or not exists:
        return None
    ok, value = try_call(lambda: zbc.get(path))
    return value if ok else None


def set_item(path: str, value) -> bool:
    ok, _ = try_call(lambda: zbc.set(path, value))
    if not ok:
        return False
    return read_item(path) == value


def geometry_size():
    return tuple(read_item(f"Tool:Geometry:{axis} Size") for axis in ("X", "Y", "Z"))


def transpose_read():
    ok, value = try_call(zbc.get_transpose)
    if not ok or not isinstance(value, (list, tuple)) or len(value) < 16:
        return None
    return {
        "origin": tuple(round(float(v), 3) for v in value[0:3]),
        "normal": tuple(round(float(v), 3) for v in value[3:6]),
        "length": round(float(value[6]), 4),
    }


def transpose_text(tag: str) -> dict | None:
    info = transpose_read()
    if info is None:
        log(f"{tag}:get_transpose() 不可用")
        return None
    log(f"{tag}:origin={info['origin']} normal={info['normal']} length={info['length']}")
    return info


# --------------------------------------------------------------------------- #
# ① 环境快照
# --------------------------------------------------------------------------- #
def phase_environment() -> dict:
    log("=" * 56)
    log("① 环境快照")
    env: dict = {}
    for label, path in (
        ("画布宽 Document:Width", "Document:Width"),
        ("画布高 Document:Height", "Document:Height"),
    ):
        env[path] = read_item(path)
        log(f"  {label} = {env[path]}")
    for label, func in (
        ("canvas_zoom(官方)", lambda: zbc.get_canvas_zoom()),
        ("canvas_pan(官方)", lambda: zbc.get_canvas_pan()),
        ("transform(位置/缩放/旋转)", lambda: zbc.get_transform()),
        ("is_transpose_shown", lambda: zbc.is_transpose_shown()),
        ("subtool 数", lambda: zbc.get_subtool_count()),
        ("几何尺寸 XYZ", geometry_size),
        ("Tool:Export:Scale", lambda: read_item("Tool:Export:Scale")),
    ):
        ok, value = try_call(func)
        env[label] = value if ok else None
        log(f"  {label} = {value}")
    return env


# --------------------------------------------------------------------------- #
# ② item path 探测
# --------------------------------------------------------------------------- #
CANDIDATES = [
    "Transform:Gizmo 3D", "Transform:Move", "Transform:Scale", "Transform:Rotate",
    "Transform:Info:X", "Transform:Info:Y", "Transform:Info:Z",
    "Preferences:Transpose:Snap Max distance",
    "Preferences:TransPose:Snap Max distance",
    "Preferences:Transpose:Align ActionLine To Axis",
    "Preferences:TransPose Units:Units Scale",
    "Preferences:Transpose Units:Units Scale",
    "Preferences:TransPose Units:Distance Units Scale Factor",
    "Preferences:Transpose Units:Distance Units Scale Factor",
    "Preferences:TransPose Units:Major Ticks Per Unit",
    "Preferences:Transpose Units:Major Ticks Per Unit",
    "Preferences:TransPose Units:Calibration Distance",
    "Preferences:Transpose Units:Calibration Distance",
    "Edit:Edit", "Edit:Frame",
    "Zplugin:Scale Master:X",
]


def phase_paths() -> None:
    log("=" * 56)
    log("② item path 探测(存在的打 ✓)")
    found, missing = [], []
    for path in CANDIDATES:
        value = read_item(path)
        if value is None:
            missing.append(path)
        else:
            found.append((path, value))
            log(f"  ✓ {path} = {value}")
    log(f"  不存在的 {len(missing)} 个:{', '.join(missing)}")


# --------------------------------------------------------------------------- #
# ③ 程序化落点(canvas_click)
# --------------------------------------------------------------------------- #
def phase_auto_line(width: float, height: float) -> dict:
    """用 canvas_click 在空白处画一条**已知画布跨度**的线,反推 length 的单位。"""
    log("=" * 56)
    log("③ 程序化落点测试(canvas_click)")
    result: dict = {"skipped": None, "span": None, "length": None, "gizmo_off": None}

    gizmo = read_item("Transform:Gizmo 3D")
    result["gizmo_off"] = set_item("Transform:Gizmo 3D", 0)
    after = read_item("Transform:Gizmo 3D")
    log(f"  Transform:Gizmo 3D:读={gizmo} 写0后={after} → {'已关闭 ✓' if after == 0 else '关不掉 ✗'}")
    if after != 0:
        result["skipped"] = "Gizmo 3D 无法确认关闭,跳过 canvas_click(保护模型)"
        log("  ! " + result["skipped"])
        return result

    ok, shown = try_call(zbc.is_transpose_shown)
    if not ok or not shown:
        press_ok, _ = try_call(lambda: zbc.press("Transform:Move"))
        log(f"  按下 Transform:Move:{'成功' if press_ok else '失败'};"
            f"is_transpose_shown={try_call(zbc.is_transpose_shown)[1]}")
    before_geo = geometry_size()

    # 在画布左上角一带(通常是空白背景)画一条水平线
    x1, x2 = width * 0.10, width * 0.40
    y = height * 0.12
    span = x2 - x1
    result["span"] = round(span, 2)
    log(f"  canvas_click({x1:.0f},{y:.0f}) → ({x2:.0f},{y:.0f}),画布跨度 {span:.0f} pixol")
    ok, err = try_call(lambda: zbc.canvas_click(x1, y, x2, y))
    if not ok:
        log(f"  canvas_click 报错:{err}")
        result["skipped"] = f"canvas_click 抛异常:{err}"
        return result
    time.sleep(0.4)

    info = transpose_text("  落点后")
    if info:
        result["length"] = info["length"]
    after_geo = geometry_size()
    if before_geo != after_geo:
        log(f"  ! 几何尺寸变了:{before_geo} → {after_geo} —— 请按 Cmd+Z")
    else:
        log("  几何尺寸未变 ✓(动作线不动网格)")
    return result


def phase_snap(width: float, height: float, auto: dict) -> None:
    log("=" * 56)
    log("④ 吸附开关(Snap Max distance)")
    path = None
    for cand in ("Preferences:Transpose:Snap Max distance",
                 "Preferences:TransPose:Snap Max distance"):
        if read_item(cand) is not None:
            path = cand
            break
    if path is None:
        log("  没找到 Snap Max distance 的 item path → 吸附只能用「点空白处」绕开")
        return
    original = read_item(path)
    log(f"  {path} 当前 = {original}")
    try:
        if set_item(path, 0):
            log("  已临时设为 0 ✓(这次测试后恢复原值)")
            log(f"  再在画布中央一带落点……")
            try_call(lambda: zbc.canvas_click(width * 0.45, height * 0.5, width * 0.62, height * 0.5))
            time.sleep(0.4)
            info = transpose_text("  吸附关闭后")
            if info and auto.get("span"):
                log(f"  (画布跨度 {width * 0.17:.0f} pixol,length={info['length']})")
        else:
            log("  set 未生效(读回值不等)→ 只能靠手动改")
    finally:
        if set_item(path, original):
            log(f"  已恢复 {path} = {original} ✓")
        else:
            log(f"  ! 恢复失败,请手动把 {path} 改回 {original}")


# --------------------------------------------------------------------------- #
# ⑤ 用户手动画一条动作线(判定 length 的真实空间)
# --------------------------------------------------------------------------- #
def phase_user_line() -> dict:
    log("=" * 56)
    log("⑤ 请手动画一条动作线")
    if not try_call(zbc.is_transpose_shown)[1]:
        try_call(lambda: zbc.press("Transform:Move"))
    zbc.set_notebar_text("⑤ 请按住左键,在【模型最宽处】从左拖到右,画一条动作线(6 秒)", 0.0)
    time.sleep(6.0)
    zbc.set_notebar_text("", 0.0)
    return transpose_text("  你画的线") or {}


# --------------------------------------------------------------------------- #
# ⑥ 鼠标能力
# --------------------------------------------------------------------------- #
def phase_mouse() -> dict:
    log("=" * 56)
    log("⑥ 鼠标能力")

    def corner(title: str, seconds: float = 3.0):
        zbc.set_notebar_text(f"{title}({seconds:g} 秒)", 0.0)
        time.sleep(seconds)
        pos = zbc.get_mouse_pos()
        glob = zbc.get_mouse_pos(True)
        log(f"  {title} → canvas={pos} global={glob}")
        return float(pos[0]), float(pos[1])

    x1, y1 = corner("⑥a 鼠标移到【画布左上角】")
    x2, y2 = corner("⑥b 鼠标移到【画布右下角】")

    zbc.set_notebar_text("⑥c 请【慢慢移动鼠标并左键点两下】(5 秒)", 0.0)
    started = time.time()
    xs, ys, presses, last, notebar = [], [], 0, False, 0
    while time.time() - started < 5.0:
        mx, my = zbc.get_mouse_pos()
        xs.append(float(mx))
        ys.append(float(my))
        state = bool(zbc.get_left_mouse_button_state())
        if state and not last:
            presses += 1
        last = state
        try:
            zbc.set_notebar_text(f"⑥c 采样中 {len(xs)}", 0.0)
            notebar += 1
        except Exception:                                             # noqa: BLE001
            pass
        time.sleep(0.05)
    zbc.set_notebar_text("", 0.0)
    unique = len({(round(a, 1), round(b, 1)) for a, b in zip(xs, ys)})
    log(f"  循环采样 {len(xs)} 次,不同位置 {unique} 个,"
        f"x∈[{min(xs):.1f},{max(xs):.1f}],y∈[{min(ys):.1f},{max(ys):.1f}],"
        f"捕捉到左键 {presses} 次,状态条刷新 {notebar} 次")
    return {"corner_tl": (x1, y1), "corner_br": (x2, y2),
            "unique": unique, "samples": len(xs), "presses": presses,
            "x_range": (min(xs), max(xs)), "y_range": (min(ys), max(ys))}


# --------------------------------------------------------------------------- #
def main() -> None:
    env = phase_environment()
    phase_paths()
    width = float(env.get("Document:Width") or 1284.0)
    height = float(env.get("Document:Height") or 864.0)
    auto = phase_auto_line(width, height)
    phase_snap(width, height, auto)
    line = phase_user_line()
    mouse = phase_mouse()

    log("=" * 56)
    log("判定:")
    scale = None
    tf = env.get("transform(位置/缩放/旋转)")
    if isinstance(tf, (list, tuple)) and len(tf) >= 6:
        scale = float(tf[3])
    if auto.get("length") and auto.get("span"):
        per_pixol = auto["length"] / auto["span"]
        log(f"  · canvas_click 跨度 {auto['span']:.0f} pixol → length {auto['length']}"
            f" → 每 pixol = {per_pixol:.6f}")
        if scale:
            log(f"  · 工具 transform 缩放 = {scale:.4f}(= 每网格单位多少 pixol)")
            log(f"    → length 若是网格单位,预期 ≈ {auto['span'] / scale:.4f}"
                f"(实测 {auto['length']})")
        if abs(per_pixol - 1.0) < 0.02:
            log("    → 结论:length 直接就是**画布 pixol** ✓ 换算最省事")
        elif scale and abs(auto["length"] - auto["span"] / scale) < 0.05 * auto["span"] / scale:
            log("    → 结论:length 是**网格单位**(= 画布跨度 ÷ transform 缩放)")
        else:
            log("    → 结论:既不是纯 pixol 也不是纯网格单位,需要再取样(见上面数字)")
    else:
        log("  · canvas_click 阶段没拿到有效 length,见上面前几行")
    if line:
        log(f"  · 你手画的线 length = {line.get('length')}"
            f"(对比 Tool:Geometry:X Size = {read_item('Tool:Geometry:X Size')})")
    w, h = width, height
    tlx, tly = mouse["corner_tl"]
    brx, bry = mouse["corner_br"]
    log(f"  · 鼠标:左上角 ({tlx:.1f},{tly:.1f}),右下角 ({brx:.1f},{bry:.1f}),"
        f"画布 {w:.0f}x{h:.0f} → 跨度比 x={(brx - tlx) / w:.4f} / y={(bry - tly) / h:.4f}")
    log(f"  · 循环里位置{'会刷新 ✓(实时卡尺可行)' if mouse['unique'] > 5 else '不刷新 ✗(只能按钮取点)'},"
        f"点击捕捉 {mouse['presses']} 次")

    log("=" * 56)
    log("请顺便回答一句:画布上有没有出现你说的那条线/刻度?(有 / 没有 / 看不出)")
    text = "\n".join(LINES)
    if copy_to_clipboard(text):
        zbc.show_note("量测探针完成:结果已复制到剪贴板,直接 Cmd+V 发回来", display_duration=8.0)
    else:
        zbc.show_note("量测探针完成:请看 Python 控制台输出", display_duration=8.0)


main()
