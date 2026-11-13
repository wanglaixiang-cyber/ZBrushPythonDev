#!/usr/bin/env python3
"""屏幕真尺(浮层窗口,在 ZBrush 之外以子进程运行)。

它画的刻度是**物理长度**:1 mm = `--pt-per-mm` 个点。这个系数由插件从显示器参数
算好后传进来(见 `zbtools/zbapi/screen.py`),所以尺子的正确性只取决于显示器,
和 ZBrush 的画布缩放、模型、透视都无关。

为什么用 AppKit 而不是 tkinter:
  * 窗口**不能被旋转** —— 旋转只能靠"开一个比尺身大的透明窗口,在里面旋转绘制"
  * 于是窗口有一大片空区,必须做到「鼠标不在尺子上时点击穿透」,否则会挡住画布;
    这一条用 setIgnoresMouseEvents_ + 定时器查询鼠标位置实现(tkinter 做不到)

交互(几何计算都在 tools/ruler_geometry.py,纯函数、可单测):
  * 左键拖动       移动尺子
  * 右键拖动       以 0 刻度为轴旋转,尺子指向鼠标方向
  * 右键拖 + Shift 角度吸附到 45° 的整数倍(覆盖 0/90/180/270)
  * 双击           删除这把尺子,并且**不记录**位置(下次「恢复尺子」不会出现它)

注意:pyobjc 会把 NSView 子类里**任何**可调用属性当成 ObjC 选择器,所以视图类里
只放真正的回调(drawRect_ / mouseDown_ …),其余逻辑一律放模块级函数。

单独运行(调试用):
    .venv/bin/python plugin/zbtools/tools/ruler_window.py \
        --pt-per-mm 3.1992 --length-mm 120 --angle-deg 30 --origin 400,600
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ruler_geometry as geo                                        # noqa: E402

BG = (0.96, 0.95, 0.91, 0.9)        # 尺身:米白,微透明
EDGE = (0.16, 0.16, 0.16, 0.95)
TICK = (0.10, 0.10, 0.10, 0.95)
TEXT = (0.10, 0.10, 0.10, 1.0)
ACCENT = (0.69, 0.19, 0.19, 1.0)    # 每 50 mm 的强调色 + 英寸刻度

TICK_DEPTH_MINOR = 5.0
TICK_DEPTH_MID = 10.0
TICK_DEPTH_MAJOR = 13.0
TEXT_SIZE_MM = 9.0
TEXT_SIZE_INCH = 9.5
#: —— 两套数字的位置一律**推导**出来,不要手写数字 ——
#: 教训:曾经手写 35 / 14,后来把 TICK_DEPTH_MAJOR 从 16 改成 13 只补了一边,
#: 结果"公制好了、英制又变远"。现在约束写在代码里,改厚度/刻度长度会自动跟着对。
#:   · 公制刻度从上边缘往下伸 TICK_DEPTH_MAJOR,数字放在刻度末端**下方**
#:   · 英制刻度从下边缘往上伸 TICK_DEPTH_MAJOR,数字放在刻度末端**上方**
THICKNESS_PT = geo.THICKNESS_PT          # 尺身厚度只有一处定义(几何模块)
MM_LABEL_INSET = THICKNESS_PT - TICK_DEPTH_MAJOR - TEXT_SIZE_MM - 1.0
INCH_LABEL_INSET = TICK_DEPTH_MAJOR + 1.0

STATE_VERSION = 2

#: 运行期共享状态(视图回调与模块级函数都读写它)
S: dict = {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="屏幕真尺浮层")
    parser.add_argument("--pt-per-mm", type=float, required=True)
    parser.add_argument("--length-mm", type=float, default=150.0)
    parser.add_argument("--angle-deg", type=float, default=0.0)
    parser.add_argument("--origin", default="", help="0 刻度的屏幕位置 x,y(y 轴向上);留空=沿用记忆值")
    parser.add_argument("--ruler-id", default="", help="状态文件里的键;空则不写状态")
    parser.add_argument("--state-dir", default="", help="状态目录(每把尺子一个文件)")
    parser.add_argument("--flip", action="store_true", help="翻面:交换公制/英制刻度所在的那一边")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--host-pid", type=int, default=0,
                        help="ZBrush 的进程号;给了它就只在 ZBrush 窗口内显示")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- #
# 状态:每把尺子写自己的文件(谁也不读别人的 → 多把尺子同时启动也不会互相覆盖)
#   <state-dir>/<ruler-id>.json   这把尺子的位置/角度/长度
#   <state-dir>/saved.json        插件点「收起尺子」时记下的一组(由插件写)
# --------------------------------------------------------------------------- #
STATE_VERSION = 2


def ruler_file(directory: str, ruler_id: str) -> str:
    return os.path.join(directory, f"{ruler_id}.json")


def load_ruler_file(directory: str, ruler_id: str) -> dict:
    if not (directory and ruler_id):
        return {}
    try:
        with open(ruler_file(directory, ruler_id), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if int(data.get("version") or 0) == STATE_VERSION else {}
    except Exception:                                                  # noqa: BLE001
        return {}


def save_ruler_file(directory: str, ruler_id: str, payload: dict) -> None:
    if not (directory and ruler_id):
        return
    try:
        os.makedirs(directory, exist_ok=True)
        payload["version"] = STATE_VERSION
        target = ruler_file(directory, ruler_id)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False,
                                         encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            temp = handle.name
        os.replace(temp, target)
    except Exception:                                                  # noqa: BLE001
        pass


def forget_ruler_file(directory: str, ruler_id: str) -> None:
    try:
        os.unlink(ruler_file(directory, ruler_id))
    except Exception:                                                  # noqa: BLE001
        pass


def note_event(text: str) -> None:
    """记一条状态变更(带相对秒),最多留 12 条:诊断信息里会用。"""
    log = S.setdefault("log", [])
    log.append(f"{time.monotonic() - S.get('t0', time.monotonic()):6.1f}s {text}")
    del log[:-12]


def persist(remove: bool = False) -> None:
    """写下(或删掉)这把尺子的几何。"""
    directory, ruler_id = S.get("state_dir"), S.get("ruler_id")
    if not (directory and ruler_id):
        return
    if remove:
        forget_ruler_file(directory, ruler_id)
        return
    save_ruler_file(directory, ruler_id, {
        "pid": os.getpid(),                       # 供插件判断这把尺子是否还活着
        "x": round(S["origin"][0], 2), "y": round(S["origin"][1], 2),
        "angle": round(S["angle"], 3), "length_mm": round(S["length_mm"], 3),
    })


# --------------------------------------------------------------------------- #
# 绘制
# --------------------------------------------------------------------------- #
def _color(rgba):
    from AppKit import NSColor
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba)


def _stroke(points, rgba, width: float = 1.0) -> None:
    from AppKit import NSBezierPath
    from Foundation import NSMakePoint
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(*points[0]))
    for point in points[1:]:
        path.lineToPoint_(NSMakePoint(*point))
    _color(rgba).set()
    path.setLineWidth_(width)
    path.stroke()


def _label(text: str, at, rgba, size: float) -> None:
    from AppKit import NSFont, NSFontAttributeName, NSForegroundColorAttributeName, NSString
    from Foundation import NSMakePoint
    attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(size),
             NSForegroundColorAttributeName: _color(rgba)}
    NSString.stringWithString_(text).drawAtPoint_withAttributes_(NSMakePoint(*at), attrs)


def draw_strip(length_pt: float) -> None:
    """在"尺子自己的坐标系"里画尺身(调用方已把原点平移到 0 点并旋转)。"""
    from AppKit import NSBezierPath
    from Foundation import NSMakeRect
    pt = S["pt_per_mm"]
    flipped = bool(S.get("flipped"))

    def across(value: float) -> float:
        """翻面就是把厚度方向的坐标镜像一下(尺身位置/命中判断都不变)。"""
        return thickness - value if flipped else value

    thickness = THICKNESS_PT                     # 布局用同一份厚度,别再手写数字
    body = NSBezierPath.bezierPathWithRect_(NSMakeRect(0, 0, length_pt, thickness))
    _color(BG).set()
    body.fill()
    _color(EDGE).set()
    body.setLineWidth_(1.0)
    body.stroke()

    def mm_tick(along: float, depth: float, rgba) -> None:
        """公制刻度画在"厚度边"一侧,往内伸 depth。"""
        _stroke([(along, across(thickness)), (along, across(thickness - depth))], rgba)

    def inch_tick(along: float, depth: float, rgba) -> None:
        """英制刻度画在另一边,往内伸 depth。"""
        _stroke([(along, across(0.0)), (along, across(depth))], rgba)

    # 毫米刻度:+厚度那一边(across = thickness),往内伸
    count_mm = int(length_pt / pt)
    for index in range(count_mm + 1):
        along = index * pt
        if index % 10 == 0:
            rgba = ACCENT if index % 50 == 0 else TICK
            mm_tick(along, TICK_DEPTH_MAJOR, rgba)
            _label(str(index), (along + 1.5, across(MM_LABEL_INSET)), TEXT, TEXT_SIZE_MM)
        elif index % 5 == 0:
            mm_tick(along, TICK_DEPTH_MID, TICK)
        else:
            mm_tick(along, TICK_DEPTH_MINOR, TICK)

    # 英寸刻度:across = 0 那一边,往内伸(1/16 小格)
    pt_per_inch = pt * 25.4
    count_16 = int(round(length_pt / pt_per_inch * 16))
    for index in range(count_16 + 1):
        along = index * pt_per_inch / 16.0
        if along > length_pt:
            break
        if index % 16 == 0:
            inch_tick(along, TICK_DEPTH_MAJOR, ACCENT)
            _label(f'{index // 16}"', (along + 1.5, across(INCH_LABEL_INSET)),
                   ACCENT, TEXT_SIZE_INCH)
        elif index % 8 == 0:
            inch_tick(along, TICK_DEPTH_MID, TICK)
        elif index % 4 == 0:
            inch_tick(along, TICK_DEPTH_MID - 2.0, TICK)
        else:
            inch_tick(along, TICK_DEPTH_MINOR, TICK)

    # 0 点标记(旋转轴就在这儿)
    dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(-3.5, -3.5, 7.0, 7.0))
    _color(ACCENT).set()
    dot.fill()


def apply_geometry() -> None:
    """按当前 origin/angle 重算窗口位置与大小,并重绘。"""
    from Foundation import NSMakeRect
    origin = S["origin"]
    x0, y0, x1, y1 = geo.bounding_box((origin[0], origin[1]), S["angle"],
                                      S["length_pt"], geo.THICKNESS_PT)
    S["window"].setFrame_display_(
        NSMakeRect(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0)), True)
    S["offset"] = (origin[0] - x0, origin[1] - y0)
    S["view"].setNeedsDisplay_(True)


def rotate_to_cursor(snap: bool) -> None:
    """让尺子绕"远端"摆动:轴心固定,另一端跟着鼠标转。

    pivot_far=True(抓的是靠近 0 点那一半)→ 轴心 = 远端,尺子从轴心指向鼠标的反方向;
    pivot_far=False(抓的是远端那一半)→ 轴心 = 0 点,尺子从 0 点指向鼠标。
    """
    from AppKit import NSEvent
    mouse = NSEvent.mouseLocation()
    origin = (S["origin"][0], S["origin"][1])
    if S.get("pivot_far"):
        ux, uy = geo.axis_vectors(S["angle"])[0]
        pivot = (origin[0] + ux * S["length_pt"], origin[1] + uy * S["length_pt"])
        angle = geo.angle_towards(pivot, (mouse.x, mouse.y), fallback_deg=S["angle"] + 180.0)
        angle = geo.normalize(angle + 180.0)                        # 尺身从轴心反着长
        if snap:
            angle = geo.quantize_angle(angle)
        S["angle"] = angle
        ux, uy = geo.axis_vectors(angle)[0]
        S["origin"] = [pivot[0] - ux * S["length_pt"], pivot[1] - uy * S["length_pt"]]
    else:
        angle = geo.angle_towards(origin, (mouse.x, mouse.y), fallback_deg=S["angle"])
        if snap:
            angle = geo.quantize_angle(angle)
        S["angle"] = angle
    apply_geometry()


# --------------------------------------------------------------------------- #
def run(opts: argparse.Namespace) -> int:
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSAffineTransform,
                        NSBackingStoreBuffered, NSColor, NSEvent, NSGraphicsContext, NSMakeRect,
                        NSRectFill, NSRunLoop, NSRunLoopCommonModes, NSScreen, NSTimer, NSView,
                        NSWindow, NSWindowStyleMaskBorderless, NSFloatingWindowLevel)

    length_pt = max(20.0, float(opts.length_mm) * float(opts.pt_per_mm))
    S.update({
        "pt_per_mm": float(opts.pt_per_mm),
        "length_mm": float(opts.length_mm),
        "length_pt": length_pt,
        "angle": geo.normalize(float(opts.angle_deg)),
        "origin": [0.0, 0.0],
        "ruler_id": opts.ruler_id,
        "state_dir": opts.state_dir,
        "grab": None,
        "rotating": False,
        "pivot_far": False,
        "last_inside": 0.0,
        "host_rect": None,
        "last_visible": time.monotonic(),
        "next_status": 0.0,
        "log": [],
        "t0": time.monotonic(),
        "visible": True,
        "screen_height": float(NSScreen.mainScreen().frame().size.height),
        "flipped": bool(opts.flip),
        "interactive": True,
    })

    if opts.origin:                                                     # 命令行优先
        try:
            x, y = (float(v) for v in opts.origin.replace(" ", "").split(","))
            S["origin"] = [x, y]
        except Exception:                                              # noqa: BLE001
            pass
    elif opts.ruler_id:                                                 # 其次取记忆值
        remembered = load_ruler_file(opts.state_dir, opts.ruler_id)
        if remembered:
            S["origin"] = [float(remembered.get("x", 0.0)), float(remembered.get("y", 0.0))]

    def on_strip(point: tuple[float, float], tolerance: float) -> bool:
        """点是不是落在尺身上(容差按调用方给)。"""
        return geo.contains_point(point, (S["origin"][0], S["origin"][1]), S["angle"],
                                  S["length_pt"], geo.THICKNESS_PT, tolerance=tolerance)

    class RulerView(NSView):
        """视图坐标 y 轴向上,与 ruler_geometry 一致;这里只放 ObjC 回调。"""

        def isFlipped(self) -> bool:
            return False

        def drawRect_(self, _rect):                                     # noqa: N802
            try:
                NSColor.clearColor().set()
                NSRectFill(self.bounds())
                host = S.get("host_rect")
                if host:
                    frame = window.frame()
                    clip = NSMakeRect(host[0] - frame.origin.x, host[1] - frame.origin.y,
                                      host[2], host[3])
                    from AppKit import NSBezierPath
                    NSBezierPath.bezierPathWithRect_(clip).addClip()
                context = NSGraphicsContext.currentContext()
                context.saveGraphicsState()
                transform = NSAffineTransform.transform()
                transform.translateXBy_yBy_(S["offset"][0], S["offset"][1])
                transform.rotateByDegrees_(S["angle"])
                transform.concat()
                draw_strip(S["length_pt"])
                context.restoreGraphicsState()
            except Exception:                                           # noqa: BLE001
                traceback.print_exc()

        def mouseDown_(self, event):                                    # noqa: N802
            if event.clickCount() == 2:                                 # 双击 = 删除且不记录
                print("[真尺] 双击关闭(不记录位置)", flush=True)
                persist(remove=True)
                NSApplication.sharedApplication().terminate_(None)
                return
            mouse = NSEvent.mouseLocation()
            if not on_strip((mouse.x, mouse.y), 3.0):
                return                                                  # 点在透明死区:不抓
            S["grab"] = (mouse.x - S["origin"][0], mouse.y - S["origin"][1])

        def mouseDragged_(self, _event):                                # noqa: N802
            grab = S["grab"]
            if grab is None:
                # 没有有效的按下(比如点在空白处被拦下)却收到拖动事件 —— 直接忽略。
                # 以前这里用 (0,0) 兜底,等于把 0 刻度吸到鼠标上。
                return
            mouse = NSEvent.mouseLocation()
            S["origin"][0] = mouse.x - grab[0]
            S["origin"][1] = mouse.y - grab[1]
            apply_geometry()

        def mouseUp_(self, _event):                                     # noqa: N802
            S["grab"] = None                                            # 让点击穿透重新生效
            persist()

        def rightMouseDown_(self, _event):                              # noqa: N802
            mouse = NSEvent.mouseLocation()
            if not on_strip((mouse.x, mouse.y), 3.0):                   # 点空白处不旋转
                return
            # 轴心取"离鼠标远的那一端":抓左半边就以右端为轴,抓右半边就以左端为轴
            along, _across_value = geo.project((mouse.x, mouse.y),
                                               (S["origin"][0], S["origin"][1]), S["angle"])
            S["pivot_far"] = along < S["length_pt"] / 2.0
            S["rotating"] = True
            rotate_to_cursor(False)

        def rightMouseDragged_(self, event):                            # noqa: N802
            shift = bool(event.modifierFlags() & (1 << 17))             # NSEventModifierFlagShift
            rotate_to_cursor(shift)

        def rightMouseUp_(self, _event):                                # noqa: N802
            S["rotating"] = False
            persist()
            print(f"[真尺] 角度 = {geo.angle_label(S['angle'])}", flush=True)

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, length_pt, geo.THICKNESS_PT), NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered, False)
    window.setOpaque_(False)
    window.setBackgroundColor_(NSColor.clearColor())
    window.setHasShadow_(False)
    window.setLevel_(NSFloatingWindowLevel)
    window.setReleasedWhenClosed_(False)
    window.setIgnoresMouseEvents_(False)
    view = RulerView.alloc().init()
    window.setContentView_(view)
    S["window"], S["view"], S["offset"] = window, view, (0.0, 0.0)

    apply_geometry()
    window.orderFrontRegardless()                                       # 显示但不抢焦点
    persist()

    def compute_interactive() -> bool:
        """鼠标是否在尺子上(带容差与 0.3 秒迟滞)。拖动/旋转期间保持可交互。"""
        if S["rotating"] or S["grab"]:
            return True
        mouse = NSEvent.mouseLocation()
        # 容差从 12 收到 4 点:只有真点在尺子附近才算"可交互",点空白不再误抓
        inside = geo.contains_point((mouse.x, mouse.y),
                                    (S["origin"][0], S["origin"][1]), S["angle"],
                                    S["length_pt"], geo.THICKNESS_PT, tolerance=4.0)
        if inside:
            S["last_inside"] = time.monotonic()
        return inside or (time.monotonic() - S.get("last_inside", 0.0) < 0.3)

    # 状态全部由一个后台线程驱动:NSTimer 在按住鼠标(event-tracking 模式)时可能不触发,
    # 一旦卡住,尺子就会"怎么点都选不中"。线程里直接落这几个窗口属性是安全的简单调用。
    import threading
    stop = threading.Event()

    def ruler_pids() -> set[int]:
        """状态目录里所有活着的尺子进程 pid(用于可见性判定与孤儿清理)。"""
        pids: set[int] = set()
        try:
            for name in os.listdir(opts.state_dir):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(opts.state_dir, name), "r",
                              encoding="utf-8") as handle:
                        pid = int(json.load(handle).get("pid") or 0)
                except Exception:                                       # noqa: BLE001
                    continue
                if pid:
                    pids.add(pid)
        except Exception:                                               # noqa: BLE001
            pass
        return pids

    def host_window_rect() -> tuple[float, float, float, float] | None:
        """ZBrush 主窗口的屏幕矩形(y 轴向上);拿不到返回 None。"""
        if not opts.host_pid:
            return None
        import Quartz
        best, best_area = None, 0.0
        for info in Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID):
            if int(info.get("kCGWindowOwnerPID") or 0) != opts.host_pid:
                continue
            bounds = info.get("kCGWindowBounds", {})
            width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
            if width * height <= best_area:
                continue
            screen_h = S["screen_height"]
            best_area = width * height
            best = (float(bounds["X"]), screen_h - float(bounds["Y"]) - height,
                    width, height)
        return best

    def poller() -> None:
        next_host = 0.0
        next_guard = 0.0
        while not stop.is_set():
            try:
                if opts.parent_pid and time.monotonic() - next_guard > 0.5:
                    next_guard = time.monotonic()
                    try:
                        os.kill(opts.parent_pid, 0)
                    except OSError:
                        print("[真尺] 父进程已退出,浮层自动关闭", flush=True)
                        persist()
                        os._exit(0)                                   # 线程里退出最干净
                if time.monotonic() - S.get("next_status", 0.0) > 1.0:
                    S["next_status"] = time.monotonic()
                    save_ruler_file(S["state_dir"], f"{S['ruler_id']}.status", {
                        "pid": os.getpid(), "x": round(S["origin"][0], 1),
                        "y": round(S["origin"][1], 1), "angle": round(S["angle"], 1),
                        "length_mm": S["length_mm"], "visible": bool(S.get("visible", True)),
                        "interactive": bool(S.get("interactive", True)),
                        "why": S.get("why", ""),
                        "log": S.get("log", [])[-12:],
                    })
                if time.monotonic() - next_host > 0.3:          # 窗口矩形/前台状态查得慢一点
                    next_host = time.monotonic()
                    rect = host_window_rect()
                    S["host_rect"] = rect
                    visible = True                                      # 判不出来就别隐藏
                    if opts.host_pid:
                        # 可见条件:前台是 ZBrush,或前台是"我们自己这类进程"。
                        # 不能只比自己的 pid —— 点一下某把尺子会让它所在进程变成前台,
                        # 其他尺子就会误判成"ZBrush 不在前台"而集体隐藏(多把尺子的 bug)。
                        from AppKit import NSWorkspace
                        front = int(NSWorkspace.sharedWorkspace()
                                    .frontmostApplication().processIdentifier())
                        # 每把尺子是独立进程,isActive() 只反映被点的那一个 → 不能用。
                        # 判据:前台是 ZBrush,或前台进程**属于某把尺子**(pid 从状态目录读)。
                        # 这比"可执行文件名以 python 开头"严格:别的 Python 程序在前台
                        # 不会让尺子赖着不走。
                        ours = {os.getpid()} | ruler_pids()
                        visible = front == opts.host_pid or front in ours
                        S["why"] = f"front={front} host={opts.host_pid} 尺子进程数={len(ours)}"
                    if visible:
                        S["last_visible"] = time.monotonic()
                    keep_visible = visible or (time.monotonic()
                                               - S.get("last_visible", 0.0) < 0.6)
                    if keep_visible != S.get("visible", True):
                        S["visible"] = keep_visible
                        S["pending_visible"] = keep_visible
                        note_event(f"visible={keep_visible} ({S.get('why', '')})")
                next_state = compute_interactive()
                if next_state != S["interactive"]:
                    S["interactive"] = next_state
                    S["pending"] = next_state             # 交给主线程去设窗口属性
                    note_event(f"interactive={next_state}")
            except Exception:                                           # noqa: BLE001
                traceback.print_exc()
            stop.wait(0.05)

    threading.Thread(target=poller, daemon=True).start()

    def apply_state(_timer=None) -> None:
        """把线程算出的状态落到窗口上 —— **必须在主线程**调 AppKit。"""
        try:
            if S.pop("pending_visible", None) is not None:
                if S["visible"]:
                    window.orderFrontRegardless()
                else:
                    window.orderOut_(None)              # ZBrush 不在前台 → 藏起来
            pending = S.pop("pending", None)
            if pending is not None:
                window.setIgnoresMouseEvents_(not pending)
                print(f"[真尺] 鼠标{'在尺子上(可交互)' if pending else '离开尺子(点击穿透)'}",
                      flush=True)
        except Exception:                                               # noqa: BLE001
            traceback.print_exc()

    timer = NSTimer.timerWithTimeInterval_repeats_block_(0.05, True, apply_state)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    window.setIgnoresMouseEvents_(False)                                # 起步一定可选

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)    # 不在 Dock 出现
    print(f"[真尺] pid={os.getpid()} / {opts.length_mm:g} mm / {geo.angle_label(S['angle'])} / "
          f"0 点在 ({S['origin'][0]:.0f}, {S['origin'][1]:.0f}) / "
          f"1 mm = {opts.pt_per_mm:.4f} 点", flush=True)
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
