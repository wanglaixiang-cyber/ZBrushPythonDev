#!/usr/bin/env python3
"""生成"顶点色通道"测试文件:验证 ZBrush 的**导入**认不认外部算出来的颜色。

背景
    ZBrush 原生把贴图变成 polypaint 是**逐顶点**采样(官方要求把模型细分到接近贴图
    像素数),这正是要绕开的瓶颈。绕开的办法是:在外部按高模自己的顶点去采样低模
    贴图,再把颜色写进文件、导回 ZBrush。于是只剩一个问题:
        **ZBrush 的导入到底认哪种顶点色写法?**
    已知的三条候选(本脚本各生成一份):
      A. OBJ + ZBrush 自己的 `#MRGB` 注释块(它导出 polypaint 时就是这么写的)
         → mrgb_test.obj
      B. OBJ + 顶点行直接带颜色 `v x y z r g b`(MeshLab/部分工具认这种)
         → vrgb_test.obj
      C. PLY 二进制 + 标准 vertex color 属性(ZBrush 自带 ZData/3DFileFormats/IMP_PLY_ply.zsc)
         → color_test.ply

    颜色用**按 Z 轴的彩虹渐变**:如果导进去颜色是平滑过渡的,说明通道通、且顶点顺序
    一一对应;如果是花斑噪点,说明顺序不对。

用法(在项目里跑,不需要 ZBrush):
    .venv/bin/python scripts/make_color_test_files.py [_probe/plain.obj]
    默认读 `_probe/plain.obj`(由 scripts/probe_goz_polypaint.py 在 ZBrush 里导出)。
"""

from __future__ import annotations

import os
import sys

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(PROJECT, "_probe", "plain.obj")
OUT_DIR = os.path.join(PROJECT, "_probe")

MRGB_HEADER = (
    "# The following MRGB block contains ZBrush Vertex Color (Polypaint) and masking "
    "output as 4 hexadecimal values per vertex. The vertex color format is MMRRGGBB "
    "with up to 64 entries per MRGB line.")


def rainbow_by_z(points: np.ndarray) -> np.ndarray:
    """按 Z 轴做彩虹渐变,返回 (n, 3) 的 0-255 uint8。"""
    z = points[:, 2]
    span = float(z.max() - z.min()) or 1.0
    t = (z - z.min()) / span                      # 0..1
    hue = t * 360.0
    h = hue / 60.0
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    q, w = 1.0 - f, f
    lut = np.stack([np.stack([np.ones_like(f), w, np.zeros_like(f)], 1),
                    np.stack([q, np.ones_like(f), np.zeros_like(f)], 1),
                    np.stack([np.zeros_like(f), np.ones_like(f), w], 1),
                    np.stack([np.zeros_like(f), q, np.ones_like(f)], 1),
                    np.stack([w, np.zeros_like(f), np.ones_like(f)], 1),
                    np.stack([np.ones_like(f), np.zeros_like(f), q], 1)])
    rgb = np.take_along_axis(lut, i[None, :, None], 0)[0]
    return (np.clip(rgb, 0.0, 1.0) * 255.0).round().astype(np.uint8)


def split_obj(path: str) -> tuple[list[str], list[str], list[str], np.ndarray]:
    """把 OBJ 拆成 (头部注释, 顶点行, 其余行, 顶点坐标数组)。"""
    head: list[str] = []
    verts: list[str] = []
    rest: list[str] = []
    coords: list[tuple[float, float, float]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                verts.append(line.rstrip("\n"))
                parts = line.split()
                coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif line.startswith("f ") or line.startswith("g ") or line.startswith("usemtl"):
                rest.append(line.rstrip("\n"))
            elif line.startswith("#"):
                head.append(line.rstrip("\n"))
            # vn/vt/其他行:本测试用不到,直接丢掉(要保真就得补回 rest)
    return head, verts, rest, np.asarray(coords, dtype=np.float64)


def write_mrgb_obj(dst: str, head: list[str], verts: list[str], rest: list[str],
                   colors: np.ndarray) -> None:
    """变体 A:OBJ + #MRGB 块(每顶点 MMRRGGBB,每行 64 条,连写不加分隔)。"""
    entries = [f"00{int(r):02X}{int(g):02X}{int(b):02X}" for r, g, b in colors]
    lines = ["#MRGB "]
    lines += ["".join(entries[i:i + 64]) for i in range(0, len(entries), 64)]
    lines.append("# End of MRGB block ")
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write("\n".join(head) + "\n")
        handle.write(MRGB_HEADER + "\n")
        handle.write("\n".join(verts) + "\n")
        handle.write("\n".join(lines) + "\n")
        handle.write("\n".join(rest) + "\n")


def write_vrgb_obj(dst: str, head: list[str], verts: list[str], rest: list[str],
                   colors: np.ndarray) -> None:
    """变体 B:顶点行带颜色 `v x y z r g b`(0-1 浮点)。"""
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write("\n".join(head) + "\n")
        for line, (r, g, b) in zip(verts, colors):
            handle.write(f"{line} {r / 255.0:.6f} {g / 255.0:.6f} {b / 255.0:.6f}\n")
        handle.write("\n".join(rest) + "\n")


def write_ply(dst: str, coords: np.ndarray, colors: np.ndarray, faces: list[list[int]]) -> None:
    """变体 C:二进制 PLY(小端),点 + 面 + 标准 red/green/blue 属性。"""
    n = len(coords)
    vertex_dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                             ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    vertex = np.empty(n, dtype=vertex_dtype)
    vertex["x"], vertex["y"], vertex["z"] = coords[:, 0], coords[:, 1], coords[:, 2]
    vertex["red"], vertex["green"], vertex["blue"] = colors[:, 0], colors[:, 1], colors[:, 2]

    counts = np.array([len(f) for f in faces], dtype=np.uint8)
    flat = np.concatenate([np.asarray(f, dtype="<i4") for f in faces])
    face_dtype = np.dtype([("n", "u1"), ("v", "<i4", 4)])       # 4 = 最大顶点数(三角形补 0)
    face = np.zeros(len(faces), dtype=face_dtype)
    face["n"] = counts
    offset = 0
    for i, f in enumerate(faces):
        face["v"][i, :len(f)] = f
        offset += len(f)

    header = (f"ply\nformat binary_little_endian 1.0\ncomment zbtools color test\n"
              f"element vertex {n}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\n"
              f"element face {len(faces)}\n"
              "property list uchar int vertex_indices\n"
              "end_header\n")
    with open(dst, "wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(vertex.tobytes())
        for i in range(len(faces)):
            handle.write(bytes([face["n"][i]]))
            handle.write(face["v"][i, :face["n"][i]].tobytes())
    del flat


def main(argv: list[str]) -> int:
    src = argv[1] if len(argv) > 1 else DEFAULT_SRC
    if not os.path.exists(src):
        print(f"找不到源 OBJ:{src}\n先在 ZBrush 里跑 scripts/probe_goz_polypaint.py 导出一份")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"读入 {src} …")
    head, verts, rest, coords = split_obj(src)
    colors = rainbow_by_z(coords)
    print(f"顶点 {len(coords):,};颜色按 Z 轴彩虹渐变(看是否平滑就知道顶点顺序对不对)")

    faces: list[list[int]] = []
    for line in rest:
        if line.startswith("f "):
            # OBJ 顶点索引是 1 基;PLY 要 0 基
            faces.append([int(tok.split("/")[0]) - 1 for tok in line.split()[1:]])

    tasks = [("mrgb_test.obj", lambda p: write_mrgb_obj(p, head, verts, rest, colors)),
             ("vrgb_test.obj", lambda p: write_vrgb_obj(p, head, verts, rest, colors)),
             ("color_test.ply", lambda p: write_ply(p, coords, colors, faces))]
    for name, fn in tasks:
        dst = os.path.join(OUT_DIR, name)
        fn(dst)
        print(f"  写好 {dst}  ({os.path.getsize(dst) / 1024 / 1024:.1f} MB)")
    print(f"\n面数 {len(faces):,}(全三角形则每面 3 个索引)")
    print("下一步:在 ZBrush 里依次导入这三个文件,看模型是否出现彩虹渐变")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
