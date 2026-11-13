from __future__ import annotations

from typing import Any

import zbrush.commands as zbc

DOC_WIDTH = "Document:Width"
DOC_HEIGHT = "Document:Height"


def canvas_size() -> tuple[float, float]:
    return float(zbc.get(DOC_WIDTH)), float(zbc.get(DOC_HEIGHT))


def canvas_zoom() -> float:
    return float(zbc.get_canvas_zoom())


# --------------------------------------------------------------------------- #
# 单位:自动读 Scale Master 的设置
# --------------------------------------------------------------------------- #


def perspective_state(cfg: dict[str, Any]) -> tuple[bool, float | None]:
    path = str(cfg["perspective_path"])
    if not zbc.exists(path):
        return False, None
    try:
        return True, float(zbc.get(path))
    except Exception:
        return True, None


def perspective_report(cfg: dict[str, Any]) -> str:
    """只提示当前视图是透视还是正交,不做任何改动。"""
    exists, value = perspective_state(cfg)
    if not exists:
        return f"当前视图:未知('{cfg['perspective_path']}' 不存在)"
    if value and value > 0.5:
        return "透视视图"
    return "正交视图"


# --------------------------------------------------------------------------- #
# 视角
# --------------------------------------------------------------------------- #


def remember_view() -> list[float] | None:
    try:
        return [float(v) for v in zbc.get_transform()]
    except Exception as exc:
        print(f"[ZBTools] 读取当前 transform 失败:{exc}")
        return None


def restore_view(transform: list[float] | None) -> bool:
    if not transform or len(transform) < 9:
        return False
    zbc.set_transform(*[float(v) for v in transform[:9]])
    return True


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
