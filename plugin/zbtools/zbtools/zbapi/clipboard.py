from __future__ import annotations

from typing import Any

import zbrush.commands as zbc
import subprocess


def copy_to_clipboard(text: str) -> bool:
    """把文本复制到系统剪贴板(macOS 用 pbcopy)。

    ZBrush 的 Python 控制台不能选中复制,所以诊断信息走剪贴板最方便。
    """
    try:
        completed = subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                                   timeout=5, check=False)
        if completed.returncode == 0:
            return True
        print(f"[ZBTools] pbcopy 返回码 {completed.returncode}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[ZBTools] 复制到剪贴板失败:{exc}")
        return False
