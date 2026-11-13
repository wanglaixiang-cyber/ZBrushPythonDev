#!/usr/bin/env python3
"""贴图 → 高模逐顶点色 → PLY(ZBrush polypaint 的外部管线)。

**与插件代码零耦合**:只吃命令行参数、只打 stdout(可选写一份 JSON 报告),
不 import 任何 ``zbtools`` 模块 —— 所以它可以单独跑、单独测、单独发行。
运行者必须是**装了 numpy / trimesh / rtree / scipy / pillow 的 Python**
(开发态是项目 ``.venv``;ZBrush 自带解释器没有这些库)。

原理(为什么需要外部管线)
    * ZBrush 原生 `Tool > Polypaint > Polypaint From Texture` 是**逐顶点**采样贴图,
      官方因此要求"把模型细分到接近贴图像素数" —— 低模顶点太少只能得到色块。
    * 绕开办法:让采样发生在**高模自己的顶点**上(高模本来就有百万级顶点)。
    * 高模(DynaMesh)没有 UV,所以走"外部算好逐顶点色 → 写 PLY → 导回"这条路;
      实测 ZBrush 的导入只认 **PLY 顶点色**:OBJ 的 `#MRGB` 块它只写不读,
      `v x y z r g b` 也不认。

用法
    <interpreter> bake_pipeline.py --low 低模.obj --high 高模.obj \
        --texture 贴图.bmp --out 上色.ply [--report-json 报告.json] [--preview 预览.png]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def check_dependencies() -> list[str]:
    """返回缺失的库名(插件拿它给用户一句能照抄的安装命令)。

    ⚠ 必须在 `import numpy` **之前**调用:否则缺库时用户看到的是一堆 traceback,
    而不是"装这个"。
    """
    missing = []
    for name in ("numpy", "trimesh", "PIL", "rtree", "scipy"):
        try:
            __import__(name)
        except Exception:                                              # noqa: BLE001
            missing.append(name)
    return missing


_MISSING = check_dependencies()
if _MISSING:                                # 缺依赖:直接退出,并给出安装命令
    _PKG = {"PIL": "pillow"}
    print(f"[bake] ✗ 缺少依赖:{', '.join(_MISSING)}")
    print("[bake]   安装命令:" + sys.executable + " -m pip install "
          + " ".join(_PKG.get(m, m) for m in _MISSING))
    raise SystemExit(2)

import numpy as np                                              # noqa: E402

# --------------------------------------------------------------------------- #
# OBJ 解析:自己解析而不是交给 trimesh —— UV 接缝要靠**逐角点** vt 索引才对得上
# --------------------------------------------------------------------------- #
def parse_obj(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """返回 (顶点 (n,3) float64, 三角形面 (m,3) int32, 逐角点 UV (m,3,2) 或 None)。"""
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_uvs: list[tuple[int, int, int]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split()
                uvs.append((float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
            elif line.startswith("f "):
                tokens = line.split()[1:]
                vi: list[int] = []
                ti: list[int] = []
                for token in tokens:
                    parts = token.split("/")
                    vi.append(int(parts[0]))
                    ti.append(int(parts[1]) if len(parts) > 1 and parts[1] else 0)
                for k in range(1, len(vi) - 1):          # 多边形按扇形三角化(ZBrush 会导四边面)
                    faces.append((vi[0], vi[k], vi[k + 1]))
                    face_uvs.append((ti[0], ti[k], ti[k + 1]))

    def to_zero_based(idx: list[int], total: int) -> list[int]:
        return [i - 1 if i > 0 else total + i for i in idx]

    v_count = len(verts)
    f_arr = np.asarray([to_zero_based(list(f), v_count) for f in faces], dtype=np.int64)
    if uvs:
        uv_arr = np.asarray([to_zero_based(list(f), len(uvs)) for f in face_uvs], dtype=np.int64)
        corner_uv = np.asarray(uvs, dtype=np.float64)[uv_arr]             # (m,3,2)
    else:
        corner_uv = None
    return np.asarray(verts, dtype=np.float64), f_arr, corner_uv


# --------------------------------------------------------------------------- #
# 贴图采样
# --------------------------------------------------------------------------- #
def _to_unit8(array: np.ndarray) -> np.ndarray:
    """把任意位深归一到 0..255。

    ⚠ 必须做:16 位 TIFF/PNG(或 float 的 EXR)直接 ``astype(float32)`` 会得到 0..65535,
    后面按 0..255 用就**整张变白**(实测容易踩,而且很难看出是位深问题)。
    8 位图原样返回,不会有精度损失。
    """
    if array.dtype == np.uint8:
        return array.astype(np.float32)
    peak = float(array.max()) if array.size else 0.0
    scale = 255.0 / peak if peak > 255.0 else 1.0
    return np.clip(array.astype(np.float32) * scale, 0.0, 255.0)


def load_texture(path: str) -> tuple[np.ndarray, np.ndarray | None]:
    """读贴图 → (RGB (H,W,3) 0..255, alpha (H,W) 或 None)。

    * 位深统一归一到 0..255(见 _to_unit8)
    * alpha 只在**原图真的有 alpha 通道**、而且**真的有透明像素**时才返回:
      没有通道的格式别凭空造(``convert("RGBA")`` 会给全 255);有通道但整片不透明的
      也当没有(做遮罩没意义、只会得到全白)。
    """
    from PIL import Image                                       # noqa: PLC0415
    img = Image.open(path)
    # ⚠ 高位深必须**在 convert("RGB") 之前**归一:convert 会把 >255 的值直接裁掉,
    #   层次就没了(实测:16 位渐变图裁完只剩 6 种颜色、一片接近白)。
    if img.mode in ("I", "I;16", "I;16B", "I;16L", "I;16N", "F"):
        gray = _to_unit8(np.asarray(img))
        if gray.ndim == 2:
            gray = np.repeat(gray[..., None], 3, axis=2)
        print(f"[bake] 贴图是高位深({img.mode})→ 已归一到 0..255")
        return gray[..., :3].astype(np.float32), None
    rgb = _to_unit8(np.asarray(img.convert("RGB")))
    if "A" not in img.getbands():
        return rgb, None
    alpha = _to_unit8(np.asarray(img.convert("RGBA")))[..., 3]
    # ⚠ 有 A 通道 ≠ 有透明信息:很多 32 位文件 A 整片 255(实测 ZBrush 自己也会这样写)。
    #   整片不透明/整片全透明都做不了遮罩,直接当成"没有 alpha",免得用户拿到一张全白。
    lo, hi = float(alpha.min()), float(alpha.max())
    if lo >= 250.0 or hi <= 5.0:
        print(f"[bake] 贴图有 A 通道但极值 {lo:g}~{hi:g}(整片{'不透明' if lo >= 250 else '全透明'})"
              f" → 视为没有可用的透明通道")
        return rgb, None
    return rgb, alpha


def sample_bilinear(image: np.ndarray, uv: np.ndarray, flip_v: bool = True) -> np.ndarray:
    """uv (n,2) 在 [0,1] → (n,3) float。OBJ 的 v 向上、图像行向下,所以默认翻 v。"""
    height, width = image.shape[:2]
    u = np.clip(uv[:, 0], 0.0, 1.0)
    v = np.clip(uv[:, 1], 0.0, 1.0)
    row = (1.0 - v) if flip_v else v
    x = u * (width - 1)
    y = row * (height - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    c00 = image[y0, x0]
    c10 = image[y0, x1]
    c01 = image[y1, x0]
    c11 = image[y1, x1]
    top = c00 * (1.0 - fx) + c10 * fx
    bottom = c01 * (1.0 - fx) + c11 * fx
    return top * (1.0 - fy) + bottom * fy


def sample_aa(image: np.ndarray, uv: np.ndarray, flip_v: bool, radius_px: float,
              samples: int) -> np.ndarray:
    """在 UV 点周围做 ``samples × samples`` 超采样取平均(= 边缘抗锯齿)。

    为什么要它:alpha 边缘在贴图里通常是硬边,逐顶点采样会让 polypaint 的遮罩边缘
    呈锯齿状(沿网格走一圈台阶)。在一个顶点上取邻域平均,边缘那一圈顶点就会拿到
    中间值 → 转成遮罩用时边缘是柔和的。

    偏移量以**像素**为单位换算到 UV(``/ (宽-1)``、``/ (高-1)``);核是对称的,
    所以翻转 v 不影响结果。
    """
    height, width = image.shape[:2]
    if samples <= 1 or radius_px <= 0.0:
        return sample_bilinear(image, uv, flip_v=flip_v)
    offsets = np.linspace(-float(radius_px), float(radius_px), int(samples))
    total = None
    for oy in offsets:
        for ox in offsets:
            shifted = uv + np.array([ox / max(width - 1, 1), oy / max(height - 1, 1)])
            value = sample_bilinear(image, shifted, flip_v=flip_v)
            total = value if total is None else total + value
    return total / float(int(samples) ** 2)


# --------------------------------------------------------------------------- #
# 重心坐标
# --------------------------------------------------------------------------- #
def barycentric(points: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """points (n,3), tri (n,3,3) → 重心坐标 (n,3),顺序对应 tri 的三个角。"""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    v0, v1, v2 = b - a, c - a, points - a
    d00 = np.einsum("ij,ij->i", v0, v0)
    d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1)
    d20 = np.einsum("ij,ij->i", v2, v0)
    d21 = np.einsum("ij,ij->i", v2, v1)
    denom = d00 * d11 - d01 * d01
    safe = np.where(np.abs(denom) < 1e-20, 1.0, denom)
    v = (d11 * d20 - d01 * d21) / safe
    w = (d00 * d21 - d01 * d20) / safe
    u = 1.0 - v - w
    degenerate = np.abs(denom) < 1e-20
    if degenerate.any():                                     # 退化三角形:退回到第一个角
        u = np.where(degenerate, 1.0, u)
        v = np.where(degenerate, 0.0, v)
        w = np.where(degenerate, 0.0, w)
    return np.stack([u, v, w], axis=1)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def parse_obj_polygons(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """只取顶点 + **原始多边形**(不三角化),返回 (顶点, 扁平索引, 每个面的顶点数)。

    为什么要保留多边形:ZBrush 的网格基本是四边面,原样写回 PLY 能保住拓扑
    (三角化会把面数翻倍、把四边面拆掉);而且逐面顶点数不同的写法已被实测过能导入。
    """
    verts: list[tuple[float, float, float]] = []
    flat: list[int] = []
    counts: list[int] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
                total = len(verts)
                idx = [i - 1 if i > 0 else total + i for i in idx]
                flat.extend(idx)
                counts.append(len(idx))
    return (np.asarray(verts, dtype=np.float64), np.asarray(flat, dtype=np.int64),
            np.asarray(counts, dtype=np.uint8))


def _face_records(flat_idx: np.ndarray, counts: np.ndarray) -> bytes:
    """按 PLY 要求**逐面交错**编码:`[顶点数][索引…][顶点数][索引…]`。

    ⚠ 这里踩过两次坑,都写进测试了:
      1. 先写一整块"顶点数"再写一整块索引 —— 文件总长度一样,尺寸校验和 trimesh
         都不报错,但 ZBrush 会拒绝(实测报 error -3);
      2. 每个面的**字节**起点是 `i + 4*前面的顶点总数`,不是顶点偏移本身。
    """
    flat_idx = np.asarray(flat_idx, dtype="<i4")
    counts = np.asarray(counts, dtype=np.uint8)
    face_bytes = 1 + 4 * counts.astype(np.int64)               # 每个面占多少字节
    rec_start = np.zeros(len(counts), dtype=np.int64)          # 每个面的字节起点
    if len(counts) > 1:
        rec_start[1:] = np.cumsum(face_bytes[:-1])
    vertex_start = np.zeros(len(counts), dtype=np.int64)       # 每个面的顶点索引起点
    if len(counts) > 1:
        vertex_start[1:] = np.cumsum(counts[:-1].astype(np.int64))
    buffer = np.zeros(int(face_bytes.sum()), dtype=np.uint8)
    buffer[rec_start] = counts
    within = np.arange(len(flat_idx), dtype=np.int64) - np.repeat(vertex_start, counts)
    index_pos = np.repeat(rec_start + 1, counts) + 4 * within
    buffer[index_pos.reshape(-1, 1) + np.arange(4)] = flat_idx.view(np.uint8).reshape(-1, 4)
    return buffer.tobytes()


def write_ply(path: str, verts: np.ndarray, colors: np.ndarray,
              flat_idx: np.ndarray, counts: np.ndarray) -> None:
    """二进制 PLY(小端):点 + 面 + uchar rgb。面保留原始顶点数(四边面不拆)。"""
    n = len(verts)
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                      ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    block = np.empty(n, dtype=dtype)
    block["x"], block["y"], block["z"] = verts[:, 0], verts[:, 1], verts[:, 2]
    block["red"], block["green"], block["blue"] = colors[:, 0], colors[:, 1], colors[:, 2]
    header = (f"ply\nformat binary_little_endian 1.0\ncomment baked by zbtools "
              f"bake_texture_to_polypaint.py\n"
              f"element vertex {n}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\n"
              f"element face {len(counts)}\n"
              "property list uchar int vertex_indices\n"
              "end_header\n")
    with open(path, "wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(block.tobytes())
        handle.write(_face_records(flat_idx, counts))


def write_preview(path: str, verts: np.ndarray, colors: np.ndarray, size: int = 512) -> None:
    """三视图(前/侧/顶)点云预览:导入 ZBrush 前先肉眼确认 UV/朝向没搞反。"""
    from PIL import Image                                       # noqa: PLC0415
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    span = np.where(hi - lo > 1e-9, hi - lo, 1.0)
    norm = (verts - lo) / span
    views = [(0, 1), (2, 1), (0, 2)]                             # (x,y) (z,y) (x,z)
    panels = []
    for ax, ay in views:
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        px = np.clip((norm[:, ax] * (size - 1)).astype(np.int64), 0, size - 1)
        py = np.clip(((1.0 - norm[:, ay]) * (size - 1)).astype(np.int64), 0, size - 1)
        canvas[py, px] = colors.astype(np.uint8)
        panels.append(canvas)
    Image.fromarray(np.concatenate(panels, axis=1)).save(path)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def bake(low_path: str, high_path: str, texture_path: str, out_path: str, *,
         max_dist_ratio: float = 0.02, fill: tuple[int, int, int] | None = None,
         flip_v: bool = True, preview: str | None = None,
         triangulate: bool = False, alpha_only: bool = False, aa: bool = False,
         aa_radius: float = 1.0, aa_samples: int = 3) -> dict:
    import trimesh                                              # noqa: PLC0415
    from trimesh.proximity import closest_point                 # noqa: PLC0415

    t0 = time.time()
    print(f"[bake] 读低模 {low_path}")
    low_v, low_f, low_uv = parse_obj(low_path)
    if low_uv is None:
        raise SystemExit("低模没有 UV(vt):没有 UV 就无法把贴图对应到表面")
    print(f"[bake]   低模:顶点 {len(low_v):,} / 三角面 {len(low_f):,}(逐角点 UV 已读入)")

    print(f"[bake] 读高模 {high_path}")
    if triangulate:
        high_v, high_f, _ = parse_obj(high_path)
        high_flat = high_f.reshape(-1)
        high_counts = np.full(len(high_f), 3, dtype=np.uint8)
        print(f"[bake]   高模:顶点 {len(high_v):,} / 三角面 {len(high_f):,}(已三角化)")
    else:
        high_v, high_flat, high_counts = parse_obj_polygons(high_path)
        kinds = {int(k): int(v) for k, v in zip(*np.unique(high_counts, return_counts=True))}
        print(f"[bake]   高模:顶点 {len(high_v):,} / 面 {len(high_counts):,}"
              f"(顶点数分布 {kinds} —— 保留原始拓扑,四边面不拆)")

    print(f"[bake] 读贴图 {texture_path}")
    rgb, alpha = load_texture(texture_path)
    has_alpha = alpha is not None
    print(f"[bake]   {rgb.shape[1]}x{rgb.shape[0]}"
          + (f";**有可用的 alpha 通道**(极值 {alpha.min():g}~{alpha.max():g},"
             f"透明像素占 {(alpha < 250).mean() * 100:.1f}%)"
             if has_alpha else ";没有可用的 alpha 通道"))
    if alpha_only and not has_alpha:
        print("[bake] ✗ 这张贴图没有**可用的** alpha 通道(要么没有 A 通道,要么 A 整片"
              "不透明/全透明),无法「仅限Alpha」——请换一张带透明信息的原图,或关掉该选项")
        raise SystemExit(3)
    if aa:
        print(f"[bake]   边缘抗锯齿:开({int(aa_samples)}x{int(aa_samples)} 超采样,"
              f"半径 {float(aa_radius):g} 像素)")
    # 「仅限Alpha」:把 alpha 当亮度源 → 纯黑白(遮罩);否则用 RGB
    channel = alpha[..., None] if alpha_only else rgb

    low_mesh = trimesh.Trimesh(vertices=low_v, faces=low_f, process=False)
    print("[bake] 查询最近点(高模每顶点 → 低模表面)…")
    t1 = time.time()
    # 分块查询并报**真进度**:这一步是整条管线最慢的(高模每个顶点都要在低模上找最近点),
    # 大网格要几十秒甚至几分钟。分块只是为了"能报进度",数值与一次性查询**完全一致**
    # (每个顶点的最近点互相独立,没有跨顶点的状态)。
    # 进度行格式是插件的约定(见 zbtools/ui.py 的 PROGRESS_PREFIX):
    #   [zbtools-progress] 0.42
    # 插件认出它、喂给 ZBrush 顶部状态条,并且**不**把它当日志打给用户。
    chunk = max(1000, len(high_v) // 50)                     # 最多约 50 次刷新
    parts_c, parts_d, parts_t = [], [], []
    for start in range(0, len(high_v), chunk):
        stop = min(len(high_v), start + chunk)
        c, d, t = closest_point(low_mesh, high_v[start:stop])
        parts_c.append(c)
        parts_d.append(d)
        parts_t.append(t)
        done = stop / max(1, len(high_v))
        print(f"[zbtools-progress] {0.22 + 0.60 * done:.3f}", flush=True)
    closest = np.concatenate(parts_c, axis=0)
    distance = np.concatenate(parts_d, axis=0)
    tri_id = np.concatenate(parts_t, axis=0)
    print(f"[bake]   {len(high_v):,} 个顶点耗时 {time.time() - t1:.1f}s;"
          f"距离 中位数 {np.median(distance):.6g} / 95% {np.percentile(distance, 95):.6g} / "
          f"最大 {distance.max():.6g}")

    # 重心坐标 → 插值 UV
    tri_pts = low_v[low_f[tri_id]]                                # (n,3,3)
    weights = barycentric(closest, tri_pts)                       # (n,3)
    tri_uv = low_uv[tri_id]                                       # (n,3,2)
    uv = np.einsum("nk,nkd->nd", weights, tri_uv)
    colors = sample_aa(channel, uv, flip_v, aa_radius, int(aa_samples)) if aa \
        else sample_bilinear(channel, uv, flip_v=flip_v)
    if alpha_only:
        colors = np.repeat(colors[..., :1], 3, axis=1)          # 灰度 → RGB(黑白)

    diagonal = float(np.linalg.norm(high_v.max(axis=0) - high_v.min(axis=0)))
    limit = max_dist_ratio * diagonal
    far = distance > limit
    print(f"[bake] 包围盒对角线 {diagonal:.4g};阈值 {limit:.4g} → 超出阈值 {far.sum():,} 个顶点"
          f"({far.mean() * 100:.2f}%)")
    if fill is not None:
        colors[far] = np.asarray(fill, dtype=np.float32)
        print(f"[bake]   超出阈值的顶点已填成 {fill}")
    else:
        print("[bake]   超出阈值仍沿用最近点颜色(想标出来就加 --fill 255,0,255)")

    colors_u8 = np.clip(colors, 0, 255).round().astype(np.uint8)
    write_ply(out_path, high_v, colors_u8, high_flat, high_counts)
    print(f"[bake] 写出 {out_path}  ({os.path.getsize(out_path) / 1024 / 1024:.1f} MB)")
    if preview:
        write_preview(preview, high_v, colors_u8)
        print(f"[bake] 预览图 {preview}(前/侧/顶三视图,确认朝向与 UV 没搞反)")
    print(f"[bake] 总耗时 {time.time() - t0:.1f}s")
    return {"vertices": len(high_v), "faces": len(high_counts),
            "far_ratio": float(far.mean()), "median_distance": float(np.median(distance)),
            "max_distance": float(distance.max()),
            "has_alpha": bool(has_alpha), "alpha_only": bool(alpha_only),
            "alpha_range": [float(alpha.min()), float(alpha.max())] if has_alpha else None,
            "alpha_transparent_ratio": float((alpha < 250).mean()) if has_alpha else 0.0,
            "aa": bool(aa), "aa_radius": float(aa_radius), "aa_samples": int(aa_samples),
            "diagonal": float(diagonal),
            "unique_colors": int(len(np.unique(colors_u8.reshape(-1, 3), axis=0))),
            "mean_color": [float(v) for v in colors_u8.reshape(-1, 3).mean(axis=0)],
            "ply_mb": round(os.path.getsize(out_path) / 1024 / 1024, 2)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="贴图 → 高模逐顶点色 → PLY(ZBrush polypaint)")
    parser.add_argument("--low", required=True, help="低模 OBJ(必须带 UV)")
    parser.add_argument("--high", required=True, help="高模 OBJ(DynaMesh,不需要 UV)")
    parser.add_argument("--texture", required=True, help="贴图图片(png/jpg/tif)")
    parser.add_argument("--out", required=True, help="输出 PLY")
    parser.add_argument("--max-dist-ratio", type=float, default=0.02)
    parser.add_argument("--fill", default=None, help='超阈值填色,如 "255,0,255"')
    parser.add_argument("--no-flip-v", action="store_true")
    parser.add_argument("--preview", default=None)
    parser.add_argument("--triangulate", action="store_true",
                        help="把高模的面全三角化再写(默认保留四边面拓扑)")
    parser.add_argument("--alpha-only", action="store_true",
                        help="把贴图的 alpha 通道当亮度源 → 黑白 polypaint(转遮罩用);"
                             "贴图必须真的有 alpha 通道")
    parser.add_argument("--aa", action="store_true",
                        help="边缘抗锯齿:在 UV 点邻域超采样取平均(alpha 遮罩边缘更柔和)")
    parser.add_argument("--aa-radius", type=float, default=1.0, help="抗锯齿采样半径(像素)")
    parser.add_argument("--aa-samples", type=int, default=3, help="抗锯齿每轴采样数(3 → 3x3)")
    parser.add_argument("--report-json", default=None,
                        help="把结果摘要写成 JSON(给插件读取,不用去 parse stdout)")
    args = parser.parse_args(argv[1:])
    print(f"[bake] 解释器 {sys.executable}")
    fill = tuple(int(x) for x in args.fill.split(",")) if args.fill else None
    report = bake(args.low, args.high, args.texture, args.out,
                  max_dist_ratio=args.max_dist_ratio, fill=fill,
                  flip_v=not args.no_flip_v, preview=args.preview,
                  triangulate=args.triangulate, alpha_only=args.alpha_only, aa=args.aa,
                  aa_radius=args.aa_radius, aa_samples=args.aa_samples)
    report.update({"out": os.path.abspath(args.out), "low": os.path.abspath(args.low),
                   "high": os.path.abspath(args.high),
                   "texture": os.path.abspath(args.texture),
                   "flip_v": not args.no_flip_v, "triangulate": bool(args.triangulate),
                   "alpha_only": bool(args.alpha_only), "aa": bool(args.aa),
                   "max_dist_ratio": float(args.max_dist_ratio)})
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"[bake] 报告 {args.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
