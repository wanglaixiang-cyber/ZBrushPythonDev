"""真尺几何核心的测试(纯函数,不开窗口、不需要 ZBrush)。

坐标系:屏幕坐标,y 轴向上。尺子 = origin(0 刻度)+ angle(度)+ length + thickness,
尺身在"尺子自己的坐标"里是 along∈[0,length]、across∈[0,thickness]。
"""

from __future__ import annotations

import math
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugin", "zbtools", "tools"))
import ruler_geometry as geo                                        # noqa: E402


def main() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [ok] {name}")
        else:
            failures += 1
            print(f"  [FAIL] {name} {detail}")

    def close(a: float, b: float, tol: float = 1e-9) -> bool:
        return abs(a - b) <= tol

    print("* 轴向向量")
    u, v = geo.axis_vectors(0.0)
    check("0° 时 u=(1,0), v=(0,1)", close(u[0], 1) and close(u[1], 0)
          and close(v[0], 0) and close(v[1], 1), f"{u} {v}")
    u, v = geo.axis_vectors(90.0)
    check("90° 时 u=(0,1), v=(-1,0)", close(u[0], 0, 1e-12) and close(u[1], 1)
          and close(v[0], -1) and close(v[1], 0, 1e-12), f"{u} {v}")
    check("u 与 v 正交", close(u[0] * v[0] + u[1] * v[1], 0.0, 1e-12))

    print("* 命中判断(判断鼠标在不在尺子上 → 决定点击穿透)")
    origin = (500.0, 600.0)
    check("0°:尺身内的点命中", geo.contains_point((600.0, 620.0), origin, 0.0, 300.0, 46.0))
    check("0°:尺身外的点不命中", not geo.contains_point((600.0, 700.0), origin, 0.0, 300.0, 46.0))
    check("0°:超出长度的点不命中", not geo.contains_point((900.0, 620.0), origin, 0.0, 300.0, 46.0))
    check("0°:负方向不命中", not geo.contains_point((400.0, 620.0), origin, 0.0, 300.0, 46.0))
    check("容差生效", geo.contains_point((600.0, 650.0), origin, 0.0, 300.0, 46.0, tolerance=6.0)
          and not geo.contains_point((600.0, 650.0), origin, 0.0, 300.0, 46.0))
    inside_30 = (origin[0] + 100 * math.cos(math.radians(30)) - 23 * math.sin(math.radians(30)),
                 origin[1] + 100 * math.sin(math.radians(30)) + 23 * math.cos(math.radians(30)))
    check("旋转 30° 后尺身中点仍命中", geo.contains_point(inside_30, origin, 30.0, 300.0, 46.0),
          str(inside_30))
    check("旋转 30° 后原来的上方点不再命中",
          not geo.contains_point((600.0, 620.0), origin, 30.0, 300.0, 46.0))

    print("* 包围盒(窗口要开多大)")
    box = geo.bounding_box(origin, 0.0, 300.0, 46.0)
    check("0°:包围盒 = 长 × 厚", close(box[2] - box[0], 300.0) and close(box[3] - box[1], 46.0),
          str(box))
    box = geo.bounding_box(origin, 90.0, 300.0, 46.0)
    check("90°:包围盒 = 厚 × 长", close(box[2] - box[0], 46.0, 1e-9)
          and close(box[3] - box[1], 300.0), str(box))
    box = geo.bounding_box(origin, 45.0, 300.0, 46.0)
    side = 300 * math.cos(math.radians(45)) + 46 * math.sin(math.radians(45))
    check("45°:包围盒两边等长(=(L+T)/√2)",
          close(box[2] - box[0], side, 1e-9) and close(box[3] - box[1], side, 1e-9), str(box))
    check("包围盒一定包含 0 点",
          box[0] <= origin[0] <= box[2] and box[1] <= origin[1] <= box[3], str(box))
    check("四个角都在包围盒上", len(geo.strip_corners(origin, 37.0, 300.0)) == 4)

    print("* 角度")
    check("指向右侧 = 0°", close(geo.angle_towards(origin, (origin[0] + 10, origin[1])), 0.0))
    check("指向上方 = 90°", close(geo.angle_towards(origin, (origin[0], origin[1] + 10)), 90.0))
    check("指向左侧 = 180°", close(geo.angle_towards(origin, (origin[0] - 10, origin[1])), 180.0))
    check("与 0 点重合时保留原角度",
          close(geo.angle_towards(origin, origin, fallback_deg=37.0), 37.0))
    check("量化到 45°:40° → 45°", close(geo.quantize_angle(40.0), 45.0))
    check("量化到 45°:80° → 90°", close(geo.quantize_angle(80.0), 90.0))
    check("量化到 45°:350° → 0°", close(geo.quantize_angle(350.0) % 360, 0.0))
    check("量化到 45°:22° → 0°", close(geo.quantize_angle(22.0), 0.0))
    check("归一到 [0,360)", close(geo.normalize(-90.0), 270.0) and close(geo.normalize(450.0), 90.0))
    check("角度文案:0° 标为水平", "水平" in geo.angle_label(0.0))
    check("角度文案:90° 标为竖直", "竖直" in geo.angle_label(90.0))
    check("角度文案:其他只给数字", geo.angle_label(37.0) == "37°", geo.angle_label(37.0))

    print("* 投影(刻度周期核验脚本也用它)")
    along, across = geo.project((origin[0] + 100, origin[1] + 20), origin, 0.0)
    check("0°:along=x 偏移, across=y 偏移", close(along, 100.0) and close(across, 20.0))
    along, across = geo.project((origin[0], origin[1] + 100), origin, 90.0)
    check("90°:竖直方向变成 along", close(along, 100.0, 1e-12) and close(across, 0.0, 1e-12))

    print("* 错开摆放")
    check("第一把不偏移", geo.cascade_index(0) == (0.0, 0.0))
    check("第二把错开 30", geo.cascade_index(1) == (30.0, -30.0))
    check("第 7 把回到 0(循环)", geo.cascade_index(6) == (0.0, 0.0))

    print("* 尺面布局(防止「改一处牵连另一处」的老问题)")
    import re
    window_src = pathlib.Path(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugin", "zbtools", "tools", "ruler_window.py")).read_text(encoding="utf-8")
    # 直接把常量那几行 exec 出来:MM_LABEL_INSET 现在是**推导表达式**,
    # 这样测的就是真正的推导结果,而不是抄一份数字。
    ns = {"THICKNESS_PT": geo.THICKNESS_PT}
    for line in window_src.splitlines():
        if re.match(r"^(TICK_DEPTH_\w+|TEXT_SIZE_\w+|\w*LABEL_INSET) = ", line):
            exec(line, ns)
    thickness, major = ns["THICKNESS_PT"], ns["TICK_DEPTH_MAJOR"]
    size_mm, size_in = ns["TEXT_SIZE_MM"], ns["TEXT_SIZE_INCH"]
    mm_inset, inch_inset = ns["MM_LABEL_INSET"], ns["INCH_LABEL_INSET"]
    check("公制数字顶端不越过公制刻度末端",
          mm_inset + size_mm <= thickness - major + 0.5,
          f"{mm_inset}+{size_mm} vs {thickness - major}")
    check("公制数字紧贴刻度(留白 ≤ 3 点)",
          thickness - major - (mm_inset + size_mm) <= 3.0,
          f"{thickness - major - (mm_inset + size_mm):.1f}")
    check("英制数字底部不压住英制刻度",
          inch_inset >= major - 0.5, f"{inch_inset} vs {major}")
    check("英制数字紧贴刻度(留白 ≤ 3 点)", inch_inset - major <= 3.0,
          f"{inch_inset - major:.1f}")
    check("两套数字纵向不重叠",
          inch_inset + size_in <= mm_inset + 0.5,
          f"英制 {inch_inset}..{inch_inset + size_in} vs 公制 {mm_inset}..{mm_inset + size_mm}")
    check("两套刻度都放得进尺身", major <= thickness / 2, f"{major} vs {thickness}")

    print("\n" + ("全部通过" if not failures else f"{failures} 项失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
