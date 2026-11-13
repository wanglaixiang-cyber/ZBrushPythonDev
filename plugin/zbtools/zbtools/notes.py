"""提示框样式(show_note 只支持 bg_color / 时长)。"""

from __future__ import annotations

from typing import Any


NOTE_BG_NORMAL = -1          # 用 ZBrush 默认底色


NOTE_BG_WARNING = 0x6A2200   # 警告:深橙红


NOTE_DURATION_NORMAL = 9.0


NOTE_DURATION_WARNING = 14.0


def note_style(text: str) -> dict[str, Any]:
    """按内容选提示框样式:警告类用不同底色 + 更长停留时间。

    show_note 的可定制项只有 bg_color / icon_path / display_duration(其余 no effect)。
    """
    warning = str(text).lstrip().startswith(("警告", "⚠"))
    return {
        "bg_color": NOTE_BG_WARNING if warning else NOTE_BG_NORMAL,
        "display_duration": NOTE_DURATION_WARNING if warning else NOTE_DURATION_NORMAL,
    }
