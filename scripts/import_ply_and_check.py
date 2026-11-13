"""导入烘焙好的 PLY,并**自动核对尺寸与点数**(用来验证 PLY 通道的两个隐患)。

为什么要核对
    1. **尺度**:ZBrush 导出的 OBJ 头部有 `#Auto scale x=0.100000`(自动缩放)。
       我们用导出的坐标写 PLY,如果 PLY 导入不做同样的还原,模型就会差 10 倍。
    2. **点数**:确认导入的确实是同一份网格(688,492 点 / 1,376,980 三角面)。

用法
    ZBrush > ZScript > Python Scripting > Load > 本文件
    跑之前**先点中原来那个高模 SubTool**(脚本要先把它的包围盒记下来),然后:
      1) 脚本打印"导入前"的包围盒
      2) 弹框问你 → 点 Yes 开始导入 `_probe/highpoly_polypaint.ply`
      3) 脚本打印"导入后"的包围盒,并算出比例
    结果会复制到剪贴板。

判断标准
    * 比例 ≈ 1.000 → 尺度一致,PLY 通道完全可用
    * 比例 ≈ 0.1 或 10 → 尺度差 10 倍,我把 PLY 整体缩放一下重新导出即可(10 秒)
    * 点数一致 → 几何没错;不一致说明 PLY 解释有问题(把日志发我)
"""

from __future__ import annotations

import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "plugin", "zbtools"))

import zbrush.commands as zbc                                        # noqa: E402

PLY = os.path.join(PROJECT, "_probe", "highpoly_polypaint.ply")
LINES: list[str] = []


def log(text: str) -> None:
    LINES.append(text)
    print(f"[导入自检] {text}")


def try_call(fn, default=None):
    try:
        return True, fn()
    except Exception as exc:                                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def bbox() -> tuple[float, float, float] | None:
    """活动 SubTool 的完整包围盒尺寸(用 index=1 = 当前 SubTool 完整包围盒)。"""
    ok, box = try_call(lambda: list(zbc.query_mesh3d(2, 1)))
    if not ok or not isinstance(box, list) or len(box) < 6:
        return None
    return (abs(box[3] - box[0]), abs(box[4] - box[1]), abs(box[5] - box[2]))


def counts() -> tuple[int, int]:
    ok_p, points = try_call(lambda: int(zbc.query_mesh3d(0)[0]))
    ok_f, faces = try_call(lambda: int(zbc.query_mesh3d(1)[0]))
    return (int(points) if ok_p else -1, int(faces) if ok_f else -1)


def main() -> None:
    if not os.path.exists(PLY):
        log(f"找不到 {PLY};先跑 bake_texture_to_polypaint.py")
        return

    ok, tool = try_call(zbc.get_active_tool_path)
    ok_i, sub = try_call(zbc.get_active_subtool_index)
    before_box = bbox()
    b_points, b_faces = counts()
    log(f"导入前:Tool={tool if ok else '?'} SubTool#{sub if ok_i else '?'}")
    log(f"        点数={b_points:,} 面数(quad)={b_faces:,}")
    log(f"        包围盒尺寸={None if before_box is None else tuple(round(float(v), 4) for v in before_box)}")
    if before_box is None:
        log("⚠ 读不到包围盒 —— 是不是没点中模型?继续导入也行,但就没有对照值了")

    ok, answer = try_call(lambda: bool(zbc.message_yes_no(
        "现在导入烘焙好的 PLY?\n\n"
        f"文件:{os.path.basename(PLY)}\n"
        "⚠ 导入会**占用当前 Tool 槽**(实测行为)→ 所以请先保存工程。\n\n"
        "导入后请顺便看一眼:\n"
        "  · 模型大小跟原来一样吗\n"
        "  · 颜色朝向对吗(跟低模上贴图的显示对照,有没有上下颠倒/左右镜像)",
        "导入上色 PLY")))
    if not (ok and answer):
        log("用户取消")
    else:
        ok1, err = try_call(lambda: zbc.set_next_filename(PLY))
        if ok1:
            ok1, err = try_call(lambda: zbc.press("Tool:Import"))
        if not ok1:
            log(f"导入失败:{err}")
        else:
            time.sleep(5.0)
            a_points, a_faces = counts()
            after_box = bbox()
            ok2, tool2 = try_call(zbc.get_active_tool_path)
            log(f"导入后:Tool={tool2 if ok2 else '?'}")
            log(f"        点数={a_points:,} 面数(quad)={a_faces:,}")
            log(f"        包围盒尺寸={None if after_box is None else tuple(round(float(v), 4) for v in after_box)}")
            if before_box and after_box:
                ratios = [float(a) / float(b) if abs(float(b)) > 1e-9 else float("nan")
                          for a, b in zip(after_box, before_box)]
                log(f"        尺度比例={tuple(round(r, 4) for r in ratios)}"
                    f"  → {'一致 ✓' if all(abs(r - 1.0) < 0.02 for r in ratios) else '⚠ 不一致,需要缩放 PLY'}")
            if a_points > 0:
                log(f"        点数核对:期望 688,492 → "
                    f"{'一致 ✓' if a_points == 688492 else '不一致(把日志发我)'}")
    log("---- 完成:请回答我三件事 ----")
    log("  1. 尺度比例是否 ≈ 1(或告诉我具体数字)")
    log("  2. 模型大小看起来跟原来一样吗")
    log("  3. 颜色朝向对吗(和低模的贴图显示对照)")


try:
    main()
except Exception as exc:                                              # noqa: BLE001
    import traceback
    print(f"[导入自检] 未捕获异常:{exc}\n{traceback.format_exc()}")

try:
    from zbtools.zbapi.clipboard import copy_to_clipboard              # noqa: PLC0415
    print("[导入自检] 复制到剪贴板:" + ("成功" if copy_to_clipboard("\n".join(LINES)) else "失败"))
except Exception as exc:                                              # noqa: BLE001
    print(f"[导入自检] 剪贴板不可用:{exc}")
