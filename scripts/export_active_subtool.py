"""把**当前 SubTool**导出成 OBJ(供外部烘焙用),并报告点数/面数/UV 情况。

用途(贴图→polypaint 流程的第 1 步)
    低模(有 UV + 贴图)要交给外部脚本做"逐顶点采样",所以先把它导成 OBJ。
    实测:ZBrush 导出 OBJ 时会**顺带写出 `<文件名>_TXTR.tif`**(工具上挂的贴图),
    所以这一次导出同时给了我们"网格 + UV + 贴图"三样东西。

用法
    ZBrush > ZScript > Python Scripting > Load > 本文件
    跑之前**先点一下你要导出的那个 SubTool**(低模),然后看控制台。

输出
    <项目>/_probe/active.obj  +  <项目>/_probe/active_TXTR.tif(如果有贴图)
    想换文件名/目录:改下面的 OUT_NAME(或直接改 OUT_DIR)。
"""

from __future__ import annotations

import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "plugin", "zbtools"))

import zbrush.commands as zbc                                        # noqa: E402

OUT_DIR = os.path.join(PROJECT, "_probe")
OUT_NAME = "active.obj"
WAIT_S = 180.0


def try_call(fn, default=None):
    try:
        return True, fn()
    except Exception as exc:                                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, OUT_NAME)

    ok, tool = try_call(zbc.get_active_tool_path)
    ok_i, index = try_call(zbc.get_active_subtool_index)
    print(f"[导出] 当前 Tool:{tool if ok else '?'};活动 SubTool 序号:{index if ok_i else '?'}")

    ok_p, points = try_call(lambda: int(zbc.query_mesh3d(0)[0]))
    ok_f, faces = try_call(lambda: int(zbc.query_mesh3d(1)[0]))
    ok_u, uv = try_call(lambda: [round(float(v), 5) for v in zbc.query_mesh3d(3)])
    print(f"[导出] 点数={points if ok_p else '?'} 面数={faces if ok_f else '?'} "
          f"UV框={uv if ok_u else '读取失败/无 UV'}")
    if ok_u and uv and abs(uv[2] - uv[0]) < 1e-9:
        print("[导出] ⚠ UV 框宽度为 0 —— 这个 SubTool 很可能没有 UV,别拿它当低模")

    ok, err = try_call(lambda: zbc.set_next_filename(path))
    if ok:
        ok, err = try_call(lambda: zbc.press("Tool:Export"))
    if not ok:
        print(f"[导出] 失败:{err}")
        return

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            break
        time.sleep(0.3)
    else:
        print(f"[导出] 超时:{WAIT_S:.0f}s 内没写出文件(可能弹了另存为对话框,或网格太大)")
        return

    print(f"[导出] 写出 {path}  ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")
    sidecar = os.path.join(OUT_DIR, os.path.splitext(OUT_NAME)[0] + "_TXTR.tif")
    if os.path.exists(sidecar):
        print(f"[导出] 附带贴图 {sidecar}  ({os.path.getsize(sidecar) / 1024 / 1024:.1f} MB)")
    else:
        print(f"[导出] 没有附带贴图({sidecar} 不存在)—— 说明工具上没挂贴图;"
              f"那就把原图文件直接给烘焙脚本 --texture")
    print("[导出] 完成")


try:
    main()
except Exception as exc:                                              # noqa: BLE001
    import traceback
    print(f"[导出] 未捕获异常:{exc}\n{traceback.format_exc()}")
