"""屏幕真尺的业务逻辑:新建 / 恢复 / 收起尺子,以及核验信息。

为什么必须是外部浮层:ZBrush 的 Python API **没有任何画布绘图能力**(没有画线/画矩形/
画字),插件 UI 也只能待在左右面板或作为居中模态 Note。而且真尺要能**任意角度旋转**,
而窗口本身不能被旋转 —— 只能开一个透明窗口在里面旋转绘制,并且做到「鼠标不在尺子上时
点击穿透」,这些都超出 ZBrush 插件的能力范围,所以交给 `tools/ruler_window.py`。

一把尺子 = 一个子进程(互相独立、互不干扰),几何状态由每把尺子自己写进状态文件:
    live[id]  当前画面上的尺子(位置 / 角度 / 长度)
双击某把尺子 = 只删这一把;面板「删除尺子」= 全部删掉(不留记录)。
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from typing import Any

from ...zbapi import screen

#: 状态目录(放用户目录,不进项目):每把尺子写自己的 <id>.json,插件写 saved.json
#: —— 一把尺子一个文件,所以多把尺子同时启动也不会互相覆盖(单文件"读-改-写"会撞车)
STATE_DIR = os.path.expanduser("~/.zbtools_rulers")
SAVED_FILE = os.path.join(STATE_DIR, "saved.json")
STATE_VERSION = 2

#: 正在运行的尺子:ruler_id -> Popen
STATE: dict[str, Any] = {"procs": {}}

DEFAULT_LENGTH_MM = 200.0
MIN_LENGTH_MM = 20.0
MAX_LENGTH_MM = 500.0


def _cfg(cfg: dict[str, Any], key: str, default: Any = None) -> Any:
    return cfg.get(f"ruler__{key}", default)


def length_mm(cfg: dict[str, Any]) -> float:
    value = float(_cfg(cfg, "length_mm", DEFAULT_LENGTH_MM) or DEFAULT_LENGTH_MM)
    return max(MIN_LENGTH_MM, min(MAX_LENGTH_MM, value))


# --------------------------------------------------------------------------- #
# 状态文件
# --------------------------------------------------------------------------- #
def _alive(pid: int) -> bool:
    if pid <= 0:
        return True                                    # 老格式没写 pid:不武断删
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_live(ids: list[str] | None = None, prune: bool = True) -> dict[str, dict]:
    """读各把尺子自己写的几何(按最近修改排序)。

    顺手做两件必须做的事:
      * 跳过 `*.status.json`(那是运行状态,不是几何)
      * **剔除死进程留下的记录**:否则"新建尺子"会继承一把早已不存在的尺子的位置/角度
        (踩过:新建的尺子继承了 225°、原点在屏幕外)
    """
    out: dict[str, dict] = {}
    try:
        names = [n for n in os.listdir(STATE_DIR)
                 if n.endswith(".json") and n != "saved.json" and not n.endswith(".status.json")]
    except Exception:                                                  # noqa: BLE001
        return out
    for name in sorted(names, key=lambda n: os.path.getmtime(os.path.join(STATE_DIR, n))):
        ruler_id = name[:-5]
        if ids is not None and ruler_id not in ids:
            continue
        try:
            with open(os.path.join(STATE_DIR, name), "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:                                              # noqa: BLE001
            continue
        if int(data.get("version") or 0) != STATE_VERSION:
            continue
        if prune and not _alive(int(data.get("pid") or 0)):
            forget(ruler_id)
            continue
        out[ruler_id] = data
    return out


def read_status() -> dict[str, dict]:
    """读每把尺子的运行状态(status 文件)。"""
    out: dict[str, dict] = {}
    try:
        names = [n for n in os.listdir(STATE_DIR) if n.endswith(".status.json")]
    except Exception:                                                  # noqa: BLE001
        return out
    for name in names:
        try:
            with open(os.path.join(STATE_DIR, name), "r", encoding="utf-8") as handle:
                out[name[: -len(".status.json")]] = json.load(handle)
        except Exception:                                              # noqa: BLE001
            continue
    return out


def forget(ruler_id: str) -> None:
    """删掉某把尺子的记录(收起/删除时用)。"""
    try:
        os.unlink(os.path.join(STATE_DIR, f"{ruler_id}.json"))
    except Exception:                                                  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 进程
# --------------------------------------------------------------------------- #
def running() -> dict[str, bool]:
    """当前活着的尺子(顺手清掉已退出的)。"""
    procs: dict[str, Any] = STATE.setdefault("procs", {})
    for ruler_id in list(procs):
        if procs[ruler_id].poll() is not None:
            procs.pop(ruler_id, None)
    return {key: True for key in procs}


def command(cfg: dict[str, Any], ruler_id: str, origin: tuple[float, float],
            angle_deg: float, length: float) -> list[str]:
    """拼出启动一把尺子的命令行(纯函数,便于测试)。"""
    interpreter = str(cfg.get("interpreter") or "")
    if not interpreter or not os.path.exists(interpreter):
        raise RuntimeError(f"解释器不可用:{interpreter!r}(需要能跑 AppKit 的 Python)")
    if not os.path.exists(screen.RULER_HELPER):
        raise RuntimeError(f"找不到尺子浮层脚本:{screen.RULER_HELPER}")
    scale = screen.pt_per_mm(cfg)
    return [
        interpreter, screen.RULER_HELPER,
        "--pt-per-mm", f"{scale['pt_per_mm']:.6f}",
        "--length-mm", f"{length:g}",
        "--angle-deg", f"{angle_deg:g}",
        "--origin", f"{origin[0]:.1f},{origin[1]:.1f}",
        "--ruler-id", ruler_id,
        "--state-dir", STATE_DIR,
        "--parent-pid", str(os.getpid()),
        "--host-pid", str(os.getpid()),        # 插件跑在 ZBrush 里,所以这就是 ZBrush
    ]


def _spawn(cfg: dict[str, Any], ruler_id: str, origin: tuple[float, float],
           angle_deg: float, length: float) -> None:
    argv = command(cfg, ruler_id, origin, angle_deg, length)
    print(f"[真尺] 启动:{' '.join(argv)}")
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STATE.setdefault("procs", {})[ruler_id] = proc


def _default_origin(cfg: dict[str, Any], offset_index: int) -> tuple[float, float]:
    """新尺子的落点:接着上一把错开;都没有就按屏幕比例放在中上方。

    offset_index 用"已经活着的尺子数",而不是读文件 —— 刚启动的尺子要过一会儿
    才写下自己的记录,只靠文件会让同时新建的几把叠在同一点。
    """
    live = read_live()
    if live:
        last = list(live.values())[-1]
        base = (float(last.get("x", 100.0)), float(last.get("y", 100.0)))
    else:
        try:
            info = screen.resolve_screen(cfg)
            logical = list(info.get("logical_pt") or [1440, 900])
            base = (logical[0] * 0.28, logical[1] * 0.52)
        except Exception:                                               # noqa: BLE001
            base = (300.0, 500.0)
    dx, dy = geo_cascade(offset_index)
    x, y = base[0] + dx, base[1] + dy
    # 夹进屏幕可见范围:尺子可能被拖到屏幕外,别让新尺子接着跑出去
    try:
        info = screen.resolve_screen(cfg)
        width, height = [float(v) for v in (info.get("logical_pt") or [1440, 900])]
        margin = 120.0
        x = min(max(x, margin), width - margin)
        y = min(max(y, margin), height - margin)
    except Exception:                                                   # noqa: BLE001
        pass
    return (x, y)


def geo_cascade(index: int, step: float = 34.0) -> tuple[float, float]:
    """新尺子的错开偏移(第 n 把偏 (n·step, -n·step),6 把一循环)。"""
    offset = (index % 6) * step
    return (offset, -offset)


def create(cfg: dict[str, Any]) -> str:
    """新建一把尺子(位置/角度接着上一把,没有就按屏幕给个默认)。"""
    live = read_live()
    last = list(live.values())[-1] if live else None
    origin = _default_origin(cfg, len(running()))          # 活着的把数(含刚启动、还没写文件的)
    angle = float((last or {}).get("angle", 0.0))
    ruler_id = uuid.uuid4().hex[:8]
    target = length_mm(cfg)
    _spawn(cfg, ruler_id, origin, angle, target)
    return (f"已新建尺子 #{len(running())}({target:g} mm,{angle:.0f}°)\n"
            f"左键拖动移动 / 右键拖动旋转(按住 Shift 吸附 45°)/ 双击删除\n"
            f"读数前提:模型是 1:1 显示(「实际大小」功能)")


def delete_all() -> str:
    """删掉所有尺子(不留记录)。

    既杀内存里的句柄,**也杀状态文件里记录的 pid** —— 热重载插件会丢掉 Popen 句柄,
    只靠内存里的表就会留下一批杀不掉的孤儿尺子。
    """
    import signal
    procs: dict[str, Any] = STATE.setdefault("procs", {})
    live = read_live(prune=False)
    count = 0
    for ruler_id in list(procs):
        try:
            procs[ruler_id].terminate()
            count += 1
        except Exception:                                              # noqa: BLE001
            pass
    for ruler_id, entry in live.items():
        if ruler_id in procs:
            continue
        pid = int(entry.get("pid") or 0)
        if pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
                count += 1
            except Exception:                                          # noqa: BLE001
                pass
    for ruler_id in set(list(procs) + list(live)):
        forget(ruler_id)
        forget(f"{ruler_id}.status")
    procs.clear()
    return f"已删除 {count} 把尺子" if count else "本来就没有尺子在显示"


# --------------------------------------------------------------------------- #
# 核验
# --------------------------------------------------------------------------- #
def verify_text(cfg: dict[str, Any]) -> str:
    """核验刻度:拿实体尺子比这一行数字,或跑自动核验脚本。"""
    scale = screen.pt_per_mm(cfg)
    info = scale["screen"]
    physical = list(info.get("physical_mm") or [])
    pt = float(scale["pt_per_mm"])
    lines = [
        f"屏幕 {info['name']}",
        f"依据 {scale['source']}",
        f"1 mm = {pt:.4f} 逻辑点 = {pt * scale['pixel_scale']:.4f} 原生像素",
    ]
    if physical:
        lines.append(f"屏幕物理尺寸 {physical[0]:.2f} x {physical[1]:.2f} mm")
    for item in scale.get("cross_check") or []:
        lines.append(f"交叉核验(EDID {item['axis']}):{item['pt_per_mm']:.4f} pt/mm"
                     f",与本口径差 {item['diff_pct']:+.2f}%")
    lines.append(f"当前尺长 {length_mm(cfg):g} mm = {length_mm(cfg) * pt:.1f} 逻辑点")
    lines.append(f"自动核验:.venv/bin/python scripts/verify_ruler_overlay.py {pt:.4f} --pid <PID>")
    return "\n".join(lines)


def diagnostics_text(cfg: dict[str, Any]) -> str:
    """诊断与核验:只留排查需要的 + 刻度依据。

    ① 刻度依据(拿实体尺子核对用)  ② 每把尺子的实时状态与判定依据
    ③ 每把尺子最近的状态切换(排查"集体隐藏/点不中"这类间歇问题)
    """
    lines = list(verify_text(cfg).splitlines())          # ① 刻度依据
    lines.append("")
    procs: dict[str, Any] = STATE.setdefault("procs", {})
    live, statuses = read_live(prune=False), read_status()
    extra = [rid for rid in live if rid not in procs]                  # 热重载后的孤儿
    lines.append(f"② 尺子 {len(live)} 把(本会话持有 {len(procs)}"
                 f"{f',孤儿 {len(extra)}' if extra else ''}) | ZBrush pid {os.getpid()}")
    lines.append(f"   目录 {STATE_DIR}")
    for ruler_id, geometry in live.items():
        proc = procs.get(ruler_id)
        status = statuses.get(ruler_id, {})
        state = ("运行中" if proc and proc.poll() is None
                 else (f"已退出({proc.poll()})" if proc else "非本会话(孤儿)"))
        lines.append(
            f"[{ruler_id}] pid={geometry.get('pid', '?')} {state} | "
            f"({geometry.get('x', '?')}, {geometry.get('y', '?')}) "
            f"{geometry.get('angle', '?')}° {geometry.get('length_mm', '?')}mm | "
            f"{'可见' if status.get('visible') else '隐藏'}"
            f"/{'可交互' if status.get('interactive') else '穿透'} | {status.get('why', '无状态')}")
        for entry in (status.get("log") or [])[-6:]:
            lines.append(f"      {entry}")                             # ③ 切换历史
    return "\n".join(lines)


def status_text() -> str:
    current = running()
    if not current:
        return "尺子未显示"
    return f"正在显示 {len(current)} 把尺子"
