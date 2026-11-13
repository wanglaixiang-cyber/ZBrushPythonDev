"""探测脚本:找出 ZBrush 里哪些 item path 存在、值是多少。

用途:确认"子工具的缩放"能不能直接读到(如果能,插件就不必依赖 Scale Master 的读数新鲜度)。

用法:ZBrush > ZScript > Python Scripting > Load > 本文件,然后看 Python 控制台。
把控制台输出整段发给我即可。
"""

from __future__ import annotations

import zbrush.commands as zbc

CANDIDATES = [
    # 子工具的几何变换(官方示例里用过 Position)
    "Tool:Geometry:X Position",
    "Tool:Geometry:Y Position",
    "Tool:Geometry:Z Position",
    "Tool:Geometry:X Scale",
    "Tool:Geometry:Y Scale",
    "Tool:Geometry:Z Scale",
    "Tool:Geometry:Scale",
    "Tool:Geometry:X Size",
    "Tool:Geometry:Size",
    # 工具/子工具层的变换
    "Tool:Transform:X Scale",
    "Tool:Transform:Scale",
    "Tool:Scale",
    "Tool:Size",
    "Tool:SubTool:Scale",
    "Tool:SubTool:Size",
    "Tool:Export:Scale",
    "Tool:Import:Scale",
    # Scale Master 面板(含那个 R 按钮)
    "Zplugin:Scale Master:R",
    "Zplugin:Scale Master:Resize Subtool",
    "Zplugin:Scale Master:Sliders to Subtool Size",
    "Zplugin:Scale Master:ZBrush Scale Unify",
    "Zplugin:Scale Master:mm",
    "Zplugin:Scale Master:cm",
    "Zplugin:Scale Master:in",
    "Zplugin:Scale Master:X",
    "Zplugin:Scale Master:Y",
    "Zplugin:Scale Master:Z",
    # 其他可能的尺寸来源
    "Zplugin:3D Print Hub:X(mm)",
    "Preferences:Transpose Units:Units Scale",
    "Preferences:Transpose Units:Calibration Distance",
    "Document:Zoom",
    "Document:Width",
    "Document:Height",
]


def main() -> None:
    try:
        print(f"[probe] 活动工具:{zbc.get_active_tool_path()}")
    except Exception as exc:
        print(f"[probe] 取活动工具失败:{exc}")

    try:
        bounds = zbc.query_mesh3d(2, 1)
        print(f"[probe] query_mesh3d(2, 1) 包围盒 = {bounds}")
        print(f"[probe] 换算出的几何尺寸 = {[round(abs(bounds[3] - bounds[0]), 4), round(abs(bounds[4] - bounds[1]), 4), round(abs(bounds[5] - bounds[2]), 4)]}")
    except Exception as exc:
        print(f"[probe] query_mesh3d 失败:{exc}")

    try:
        print(f"[probe] get_transform() = {zbc.get_transform()}")
    except Exception as exc:
        print(f"[probe] get_transform 失败:{exc}")

    print("[probe] ---- item path 探测 ----")
    for path in CANDIDATES:
        try:
            if not zbc.exists(path):
                print(f"[probe]   (无)  {path}")
                continue
            try:
                value = zbc.get(path)
            except Exception as exc:
                value = f"<无法取值: {exc}>"
            print(f"[probe]   存在  {path} = {value}")
        except Exception as exc:
            print(f"[probe]   (检查失败) {path}: {exc}")


main()
