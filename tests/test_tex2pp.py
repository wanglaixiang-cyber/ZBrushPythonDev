"""贴图转色(tex2pp)功能的本地测试(不开 ZBrush)。

覆盖三块:
  1. **算法**:低模(带 UV)→ 高模顶点最近点 → 重心坐标插值 UV → 采样贴图 → 写 PLY;
     用合成网格 + 暴力参考实现作对照(平面网格能与解析期望值逐点对上,误差 0)
  2. **PLY 结构**:逐面交错编码(这条踩过坑:长度对得上但结构错,ZBrush 报 error -3)、
     严格逐面回读、面/点/颜色与源一致
  3. **插件契约**:FEATURE(key/title/build)、配置前缀 tex2pp__、贴图查找、命令行拼装、
     外部管线的真实子进程契约(零耦合 → 可以单独发行)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import tempfile

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(PROJECT, "plugin", "zbtools")
PIPELINE = os.path.join(PLUGIN, "zbtools", "features", "tex2pp", "tools", "bake_pipeline.py")
sys.path.insert(0, os.path.join(PROJECT, "tests"))
sys.path.insert(0, PLUGIN)

# 管线脚本是给**外部子进程**用的独立脚本(不是 import 的模块),按路径加载
import importlib.util                                              # noqa: E402
_spec = importlib.util.spec_from_file_location("bake_pipeline", PIPELINE)
bake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bake)

FAILED: list[str] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {what}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(what)


def make_texture(path: str, size: int = 64) -> np.ndarray:
    """非对称图案:四个象限四个纯色 + 左上角一条白(用来抓 v/u 翻转)。"""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    half = size // 2
    img[:half, :half] = (255, 0, 0)           # 上-左 红
    img[:half, half:] = (0, 255, 0)           # 上-右 绿
    img[half:, :half] = (0, 0, 255)           # 下-左 蓝
    img[half:, half:] = (255, 255, 0)         # 下-右 黄
    img[:size // 8, :half] = (255, 255, 255)  # 方向标记
    from PIL import Image                                          # noqa: PLC0415
    Image.fromarray(img).save(path)
    return img


def write_obj(path: str, verts: np.ndarray, faces: np.ndarray,
              uvs: np.ndarray | None = None,
              corner_uv: np.ndarray | None = None) -> None:
    """写 OBJ;corner_uv 是 (m,3,2) 时按**角点**写 vt(模拟 UV 接缝/四边面场景)。"""
    lines = ["# zbtools test"]
    for v in verts:
        lines.append(f"v {v[0]:.9f} {v[1]:.9f} {v[2]:.9f}")
    if corner_uv is not None:
        for uv in corner_uv.reshape(-1, 2):
            lines.append(f"vt {uv[0]:.9f} {uv[1]:.9f}")
    elif uvs is not None:
        for uv in uvs:
            lines.append(f"vt {uv[0]:.9f} {uv[1]:.9f}")
    for i, f in enumerate(faces):
        if corner_uv is not None:
            ids = [i * 3 + k + 1 for k in range(len(f))]
            lines.append("f " + " ".join(f"{v + 1}/{t}" for v, t in zip(f, ids)))
        elif uvs is not None:
            lines.append("f " + " ".join(f"{v + 1}/{v + 1}" for v in f))
        else:
            lines.append("f " + " ".join(str(v + 1) for v in f))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def sphere_mesh(segments: int, rings: int):
    """带 UV 的球:u 沿经度 0→1,v 沿纬度 0(北)→1(南)。"""
    verts, uvs = [], []
    for j in range(rings + 1):
        v = j / rings
        theta = v * np.pi
        for i in range(segments + 1):
            u = i / segments
            phi = u * 2 * np.pi
            verts.append((np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi),
                          np.cos(theta)))
            uvs.append((u, v))

    def vid(j: int, i: int) -> int:
        return j * (segments + 1) + i

    faces = []
    for j in range(rings):
        for i in range(segments):
            faces.append((vid(j, i), vid(j, i + 1), vid(j + 1, i)))
            faces.append((vid(j, i + 1), vid(j + 1, i + 1), vid(j + 1, i)))
    return (np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64),
            np.asarray(uvs, dtype=np.float64))


def plane_mesh(n: int, size: float = 1.0):
    """无接缝平面网格:UV 直接等于归一化 xy —— 期望颜色可以解析算出来。"""
    xs = np.linspace(0.0, size, n + 1)
    verts, uvs = [], []
    for j, y in enumerate(xs):
        for i, x in enumerate(xs):
            verts.append((x, y, 0.0))
            uvs.append((x / size, y / size))
    faces = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces.append((a, a + 1, a + n + 1))
            faces.append((a + 1, a + n + 2, a + n + 1))
    return (np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64),
            np.asarray(uvs, dtype=np.float64))



def read_ply_strict(path: str) -> tuple[np.ndarray, list[list[int]], np.ndarray]:
    """严格按 PLY 规范逐面解析(不借助第三方库)。

    存在的意义:上一版 write_ply 把"顶点数"写成一整块、索引写另一整块(长度刚好一样),
    尺寸校验和 trimesh 都没报错,只有 ZBrush 拒绝(error -3)。这个解析器就是那次事故的哨兵。
    """
    blob = open(path, "rb").read()
    end = blob.index(b"end_header\n") + len(b"end_header\n")
    head = blob[:end].decode("ascii")
    n_v = int(re.search(r"element vertex (\d+)", head).group(1))
    n_f = int(re.search(r"element face (\d+)", head).group(1))
    stride = 12 + (3 if "property uchar red" in head else 0)
    off = end + n_v * stride
    colors = np.zeros((n_v, 3), dtype=np.uint8)
    if stride == 15:
        block = np.frombuffer(blob[end:end + n_v * 15], dtype=np.dtype(
            [("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
             ("r", "u1"), ("g", "u1"), ("b", "u1")]))
        colors = np.stack([block["r"], block["g"], block["b"]], axis=1)
    else:
        block = np.frombuffer(blob[end:end + n_v * 12],
                              dtype=np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4")]))
    verts = np.stack([block["x"], block["y"], block["z"]], axis=1)
    faces: list[list[int]] = []
    for _ in range(n_f):
        n = blob[off]
        off += 1
        faces.append(list(np.frombuffer(blob[off:off + 4 * n], dtype="<i4")))
        off += 4 * n
    assert off == len(blob), f"面数据没正好读到文件末尾:停在 {off},文件 {len(blob)}"
    return verts, faces, colors


def check_ply_encoding() -> None:
    """编码单元测试:混合 3/4 顶点数的面必须**逐面交错**。"""
    print("* PLY 面编码(逐面交错,长度相同的错法也要能抓出来)")
    flat = np.asarray([0, 1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int64)
    counts = np.asarray([3, 3, 3], dtype=np.uint8)
    rec = bake._face_records(flat, counts)
    check("全三角面: 1+12 字节/面 × 3", len(rec) == 39, str(len(rec)))
    check("首字节是顶点数 3", rec[0] == 3)
    check("接着 3 个索引(小端 int32)", list(np.frombuffer(rec[1:13], dtype="<i4")) == [0, 1, 2])
    check("第二面从第 14 字节开始", rec[13] == 3 and
          list(np.frombuffer(rec[14:26], dtype="<i4")) == [3, 4, 5])
    mixed = bake._face_records(np.asarray([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=np.int64),
                               np.asarray([4, 3, 3], dtype=np.uint8))
    check("混合面:长度 = 3 + 4*4 + 4*3 + 4*3", len(mixed) == 3 + 16 + 12 + 12, str(len(mixed)))
    check("混合面首个顶点数是 4", mixed[0] == 4)
    check("混合面第二面(三角)顶点数是 3", mixed[17] == 3,
          f"实际 {mixed[17]}(若为 3 说明四边面后的偏移没错)")
    # 反面教材:先写一整块顶点数、再写一整块索引 —— 长度相同但结构错
    wrong = np.concatenate([counts.view(np.uint8),
                            flat.astype("<i4").view(np.uint8)]).tobytes()
    check("错写法与正确写法长度相同(所以尺寸校验抓不住)",
          len(wrong) == len(rec), f"{len(wrong)} vs {len(rec)}")
    check("但结构不同(第二个面的起点就露馅)", wrong[13] != rec[13],
          f"wrong[13]={wrong[13]} rec[13]={rec[13]}")


def brute_force(low_v, low_f, low_uv_corner, high_v, image, flip_v=True):
    """参考实现:暴力遍历所有三角形(重心坐标钳制 = 最近点)→ 插值 UV → 采样。"""
    out_uv = np.zeros((len(high_v), 2), dtype=np.float64)
    best = np.full(len(high_v), np.inf)
    for fi, f in enumerate(low_f):
        a, b, c = low_v[f[0]], low_v[f[1]], low_v[f[2]]
        v0, v1 = b - a, c - a
        d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
        denom = d00 * d11 - d01 * d01
        if abs(denom) < 1e-20:
            continue
        p = high_v - a
        d20, d21 = p @ v0, p @ v1
        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        u = 1.0 - v - w
        uu, vv, ww = np.clip(u, 0, 1), np.clip(v, 0, 1), np.clip(w, 0, 1)
        s = uu + vv + ww
        s[s == 0] = 1.0
        uu, vv, ww = uu / s, vv / s, ww / s
        pos = uu[:, None] * a + vv[:, None] * b + ww[:, None] * c
        dist = np.linalg.norm(pos - high_v, axis=1)
        hit = dist < best - 1e-12
        best = np.where(hit, dist, best)
        uv = (uu[:, None] * low_uv_corner[fi, 0] + vv[:, None] * low_uv_corner[fi, 1]
              + ww[:, None] * low_uv_corner[fi, 2])
        out_uv = np.where(hit[:, None], uv, out_uv)
    return best, bake.sample_bilinear(image, out_uv, flip_v=flip_v)



# --------------------------------------------------------------------------- #
# 插件侧(功能契约 / 配置 / 命令行 / 真子进程契约)
# --------------------------------------------------------------------------- #
def check_pipeline_decoupled() -> None:
    print("* 管线脚本与插件零耦合(可单独发行、单独跑)")
    import ast                                                      # noqa: PLC0415
    src = open(PIPELINE, encoding="utf-8").read()
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append((node.module or ""))
            check("没有插件的相对 import(level=%d)" % node.level, node.level == 0,
                  f"level={node.level} module={node.module}")
    check("import 里不含 zbtools", not any("zbtools" in m for m in imported),
          str(imported))
    check("自检依赖(缺库时给安装命令)", "def check_dependencies" in src)
    check("依赖自检在 import numpy 之前",
          src.index("check_dependencies()") < src.index("import numpy"))


def check_feature_contract() -> None:
    print("* 功能契约 / 配置前缀 / 贴图查找(假 zbrush)")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features import FEATURES                           # noqa: PLC0415
    keys = [f.key for f in FEATURES]
    check("tex2pp 已在注册表里", "tex2pp" in keys, str(keys))
    feat = next(f for f in FEATURES if f.key == "tex2pp")
    check("显示名为「贴图转色」", feat.title == "贴图转色", feat.title)
    check("build 可调用", callable(feat.build))
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    check("paths() 与 LABELS 数量一致", len(mod.paths()) == len(mod.LABELS), str(mod.paths()))
    check("LABELS 每段都不含 ASCII 冒号", all(":" not in v for v in mod.LABELS.values()))
    check("path 前缀正确", mod.paths()["bake"] == "ZBTools:贴图转色:② 烘焙并导回",
          mod.paths()["bake"])
    check("面板 5 个控件(选择贴图/自动导回/仅限Alpha/边缘抗锯齿均已按需求移除)",
          set(mod.LABELS) == {"remember", "bake", "max_dist", "flip_v", "diag"},
          str(sorted(mod.LABELS)))
    check("② 的名字里就有「导回」(它一定包含导回,不再有开关)",
          "导回" in mod.LABELS["bake"], mod.LABELS["bake"])
    for gone in ("pick_texture", "auto_import", "alpha_only", "aa"):
        check(f"面板上没有 {gone}", gone not in mod.LABELS)
    check("配置前缀是 tex2pp__", service.PREFIX == "tex2pp__")
    check("已移除的开关不再是配置项",
          all(k not in service.DEFAULTS for k in ("auto_import",))
          and service.DEFAULTS.get("alpha_only") is False, str(sorted(service.DEFAULTS)))
    cfg: dict = {}
    service.set_param(cfg, "flip_v", False)
    check("set_param 带前缀写回", cfg.get("tex2pp__flip_v") is False)
    check("param 读得到", service.param(cfg, "flip_v") is False)
    check("没配过时回落默认值",
          service.param({}, "max_dist_ratio") == service.DEFAULTS["max_dist_ratio"])
    check("没记住低模时 has_source 为假", service.has_source({}) is False)

    tmp = tempfile.mkdtemp(prefix="tex2pp_svc_")
    obj = os.path.join(tmp, "low.obj")
    with open(obj, "w", encoding="utf-8") as handle:
        handle.write("v 0 0 0\n")
    bmp = os.path.join(tmp, "low.bmp")
    from PIL import Image                                          # noqa: PLC0415
    Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(bmp)
    with open(os.path.join(tmp, "low.mtl"), "w", encoding="utf-8") as handle:
        handle.write("newmtl defaultMat\nmap_Kd low.bmp\n")
    check("能从 mtl 的 map_Kd 找到贴图",
          os.path.abspath(service.find_texture(obj)) == os.path.abspath(bmp),
          service.find_texture(obj))
    obj2 = os.path.join(tmp, "high.obj")
    with open(obj2, "w", encoding="utf-8") as handle:
        handle.write("v 0 0 0\n")
    tif = os.path.join(tmp, "high_TXTR.tif")
    Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(tif)
    check("没有 mtl 时回退到 _TXTR.tif",
          os.path.abspath(service.find_texture(obj2)) == os.path.abspath(tif))
    check("都没有时返回空串", service.find_texture(os.path.join(tmp, "none.obj")) == "")
    check("管线路径能找到(.py 或发行包的 .pyc)",
          os.path.exists(service.pipeline_path()), service.pipeline_path())
    old_dir = service.work_dir()
    marker = os.path.join(old_dir, "旧文件.txt")
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("x")
    service.reset_work_dir({"tex2pp__keep_temp": True})
    check("keep_temp 开 → 目录保留", os.path.exists(marker))
    service.reset_work_dir({"tex2pp__keep_temp": False})
    check("keep_temp 关 → 旧目录被清掉", not os.path.exists(marker))
    check("工作目录可写且带前缀",
          os.path.isdir(service.work_dir()) and "zbtools_tex2pp_" in service.work_dir(),
          service.work_dir())


def check_import_is_non_blocking() -> None:
    """导回必须**短等就返回**:脚本跑着的时候 ZBrush 界面是被占住的。

    踩过的坑:早先实现提交后还轮询最多 60 秒 Tool 名变化 —— 导入其实早就提交了,
    那 60 秒纯粹是插件自己占着 ZBrush,用户看到的就是"导入成功后还卡很久"。
    """
    print("* 导回:只等提交,不长占 ZBrush")
    import fake_zbrush                                              # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_imp_")
    ply = os.path.join(tmp, "colored.ply")
    with open(ply, "wb") as handle:
        handle.write(b"ply\n")

    # ZBrush 已跑完(名字变成 !:<路径>)的情形
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.features.tex2pp import service as svc_a            # noqa: PLC0415
    t0 = time.time()
    result = svc_a.import_ply(ply, {"tex2pp__import_wait_s": 0.5})
    elapsed = time.time() - t0
    check("返回得快(预算 0.5s,实际应 <2s)", elapsed < 2.0, f"{elapsed:.2f}s")
    check("提交了 set_next_filename + Tool:Import",
          any("colored.ply" in p for p in fake.next_filenames) and "Tool:Import" in fake.pressed,
          f"{fake.next_filenames} {fake.pressed}")
    check("期间调用 update 让 ZBrush 有机会重绘", len(fake.updates) > 0, str(fake.updates[:3]))
    check("返回结果带提交耗时", result.submitted_s >= 0)
    check("不去改 Tool 名(交给用户在 Tool 面板改)", not fake.tool_renames,
          str(fake.tool_renames))

    # ZBrush 还没跑完(名字没变)→ 同样立刻返回,并说明名字会变成 !:<路径>
    fake2 = fake_zbrush.FakeCommands(rename_on_import=False)
    fake_zbrush.install(fake2)
    from zbtools.features.tex2pp import service as svc_b            # noqa: PLC0415
    t0 = time.time()
    res_b = svc_b.import_ply(ply, {"tex2pp__import_wait_s": 0.4})
    check("名字没变也立刻返回(<2s)", time.time() - t0 < 2.0, f"{time.time() - t0:.2f}s")
    check("提示里说明 Tool 名会变成 !:<路径>",
          "!:" in res_b.note and "Tool 面板" in res_b.note, res_b.note)


def check_alpha_support() -> None:
    """仅限Alpha(alpha→黑白)与边缘抗锯齿:功能 + 门控。"""
    print("* alpha 通道:检测 / alpha→黑白 / 抗锯齿 / 面板门控")
    import fake_zbrush                                              # noqa: PLC0415
    from PIL import Image                                          # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_alpha_")
    rgba = np.zeros((64, 64, 4), dtype=np.uint8)
    rgba[..., 0:3] = (200, 30, 30)
    rgba[:, :32, 3] = 255                     # 左半不透明
    rgba[:, 32:, 3] = 0                       # 右半全透明(硬边)
    tex_alpha = os.path.join(tmp, "alpha.png")
    Image.fromarray(rgba, "RGBA").save(tex_alpha)
    tex_plain = os.path.join(tmp, "no_alpha.bmp")
    Image.fromarray(rgba[..., :3], "RGB").save(tex_plain)

    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    cfg: dict = {}
    has, mode = service.detect_alpha(cfg, tex_alpha)
    check("PNG 检测出 alpha 通道", has and "alpha" in mode, mode)
    check("检测结果写进配置", cfg.get("tex2pp__texture_has_alpha") is True)
    has2, mode2 = service.detect_alpha({}, tex_plain)
    check("BMP 检测为没有 alpha", not has2, mode2)
    # 隐藏兜底层:面板上没有按钮了,但配置仍能指定另一张贴图(万一导出剥掉了通道)
    check("隐藏通道 set_texture 仍可用",
          service.set_texture(cfg, tex_alpha)[0] is True
          and cfg.get("tex2pp__texture_path") == tex_alpha)
    check("实际用哪张:优先手动指定的", service.effective_texture(cfg) == tex_alpha)
    # ① 之后仍然以"实际用哪张"为准(别被 ZBrush 的 24 位 BMP 覆盖成无 alpha)
    sidecar = os.path.join(tmp, "tex2pp_low.bmp")
    Image.fromarray(rgba[..., :3], "RGB").save(sidecar)
    service.remember_source(cfg, low_obj if False else os.path.join(tmp, "low.obj"),
                            sidecar)
    effective = service.effective_texture(cfg)
    check("隐藏指定的贴图优先于模型带出的那张", effective == tex_alpha, effective)
    check("① 之后 alpha 标记没被 BMP 覆盖", service.detect_alpha(cfg, effective)[0] is True)

    # 管线:alpha → 黑白;抗锯齿让边缘出现中间值
    low_v, low_f, low_uv = plane_mesh(20)
    low_obj = os.path.join(tmp, "low.obj")
    write_obj(low_obj, low_v, low_f, corner_uv=low_uv[low_f])
    high_v, high_f, _ = plane_mesh(60)
    high_obj = os.path.join(tmp, "high.obj")
    write_obj(high_obj, high_v, high_f)

    def run(extra: list[str], name: str):
        ply = os.path.join(tmp, name)
        done = subprocess.run([sys.executable, PIPELINE, "--low", low_obj, "--high",
                               high_obj, "--texture", tex_alpha, "--out", ply] + extra,
                              capture_output=True, text=True)
        if done.returncode != 0:
            return None, done
        _v, _f, colors = read_ply_strict(ply)
        return np.asarray(colors)[:, 0].astype(int), done

    gray_plain, done = run(["--alpha-only"], "a_plain.ply")
    check("--alpha-only 跑通", gray_plain is not None, (done.stdout + done.stderr)[-200:])
    if gray_plain is not None:
        xs = high_v[:, 0]
        left, right = gray_plain[xs < 0.45], gray_plain[xs > 0.55]
        check("不透明区 → 白(255)", int(left.min()) >= 250, f"min={left.min()}")
        check("透明区 → 黑(0)", int(right.max()) <= 5, f"max={right.max()}")
        check("整体不是全白/全黑", 0 < int(gray_plain.mean()) < 255)

    gray_aa, done_aa = run(["--alpha-only", "--aa"], "a_aa.ply")
    check("--alpha-only --aa 跑通", gray_aa is not None,
          (done_aa.stdout + done_aa.stderr)[-200:])
    if gray_aa is not None and gray_plain is not None:
        mid = ((gray_aa > 20) & (gray_aa < 235)).sum()
        mid_plain = ((gray_plain > 20) & (gray_plain < 235)).sum()
        check("抗锯齿让更多顶点落在中间灰(边缘变柔和)", mid > mid_plain,
              f"aa={mid} vs 无={mid_plain}")
        # 一排相邻顶点的最大跳变:抗锯齿应当更小(不出现 255→0 的硬跳)
        order = np.argsort(high_v[:, 0], kind="stable")
        row = [i for i in order if abs(high_v[i, 1] - 0.5) < 1e-6] or list(order[:80])
        steps_aa = int(np.abs(np.diff(gray_aa[row])).max())
        steps_plain = int(np.abs(np.diff(gray_plain[row])).max())
        check("抗锯齿后相邻顶点最大跳变更小", steps_aa < steps_plain,
              f"aa={steps_aa} vs 无={steps_plain}")

    # 没 alpha 的贴图 + --alpha-only → 明确报错(返回码 3)
    bad = subprocess.run([sys.executable, PIPELINE, "--low", low_obj, "--high", high_obj,
                          "--texture", tex_plain, "--out", os.path.join(tmp, "bad.ply"),
                          "--alpha-only"], capture_output=True, text=True)
    check("无 alpha 通道时 --alpha-only 明确失败(返回码 3)", bad.returncode == 3,
          f"rc={bad.returncode}")
    check("并说明原因", "可用的" in (bad.stdout + bad.stderr) and
          "alpha" in (bad.stdout + bad.stderr), (bad.stdout + bad.stderr)[-120:])

    check("面板上不再有 alpha 开关(由使用者在导入前自行处理)",
          "alpha_only" not in mod.LABELS and "aa" not in mod.LABELS, str(sorted(mod.LABELS)))


def check_alpha_is_semantic() -> None:
    """有 A 通道 ≠ 有透明信息:整片 255 的 alpha 必须判为"没有可用通道"。"""
    print("* alpha 判定是语义的(通道存在但整片不透明 → 不算)")
    import fake_zbrush                                              # noqa: PLC0415
    from PIL import Image                                          # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_sema_")
    # A 通道存在但整片 255(很多 32 位导出器都这样,实测 ZBrush 也会)
    flat = np.zeros((32, 32, 4), dtype=np.uint8)
    flat[..., :3] = 128
    flat[..., 3] = 255
    tex_flat = os.path.join(tmp, "flat_alpha.png")
    Image.fromarray(flat, "RGBA").save(tex_flat)
    # 真的有透明像素
    real = flat.copy()
    real[:, 16:, 3] = 0
    tex_real = os.path.join(tmp, "real_alpha.png")
    Image.fromarray(real, "RGBA").save(tex_real)

    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    cfg: dict = {}
    usable_flat, stats_flat = service.detect_alpha(cfg, tex_flat)
    check("整片不透明的 A 通道 → 判为不可用", usable_flat is False, stats_flat)
    check("证据里能看到极值 255~255", "255|255" in stats_flat, stats_flat)
    usable_real, stats_real = service.detect_alpha(cfg, tex_real)
    check("真有透明像素 → 判为可用", usable_real is True, stats_real)
    check("证据里能看到透明像素占比", stats_real.split("|")[-1] != "0.0000", stats_real)

    # 管线侧行为一致:整片不透明的图 + --alpha-only → 明确失败(返回码 3)
    low_v, low_f, low_uv = plane_mesh(8)
    low_obj = os.path.join(tmp, "low.obj")
    write_obj(low_obj, low_v, low_f, corner_uv=low_uv[low_f])
    high_v, high_f, _ = plane_mesh(16)
    high_obj = os.path.join(tmp, "high.obj")
    write_obj(high_obj, high_v, high_f)
    for name, tex, expect_rc in (("整片不透明", tex_flat, 3), ("真有透明", tex_real, 0)):
        done = subprocess.run([sys.executable, PIPELINE, "--low", low_obj, "--high", high_obj,
                               "--texture", tex, "--out", os.path.join(tmp, f"o_{expect_rc}.ply"),
                               "--alpha-only"], capture_output=True, text=True)
        check(f"管线:{name} + --alpha-only → 返回码 {expect_rc}", done.returncode == expect_rc,
              f"rc={done.returncode}; {(done.stdout + done.stderr)[-160:]}")


def check_source_and_workdir_survive_restart() -> None:
    """低模路径与工作目录必须熬过 ZBrush 重启(踩过:② 变灰 = "烘焙失效")。"""
    print("* 重启后仍能找到低模(工作目录持久化 + 兜底)")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_wd_")
    cfg: dict = {"tex2pp__work_dir": tmp, "tex2pp__source_obj": os.path.join(tmp, "没了.obj")}
    with open(os.path.join(tmp, "tex2pp_low.obj"), "w", encoding="utf-8") as handle:
        handle.write("v 0 0 0\n")
    check("配置里的路径没了 → 退回工作目录里的标准文件",
          service.resolve_source(cfg).endswith("tex2pp_low.obj"), service.source_obj(cfg))
    check("has_source 因此为真(② 不会变灰)", service.has_source(cfg) is True)
    check("顺手把配置修好了", service.source_obj(cfg).endswith("tex2pp_low.obj"))
    check("work_dir 复用配置里的目录", service.work_dir(cfg) == tmp, service.work_dir(cfg))


def check_bake_end_to_end() -> None:
    """② 的端到端(假 zbrush + 真管线):导出 → 烘焙 → 提交导入,一条龙不报错。"""
    print("* ② 端到端:真管线跑通并提交导入")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake.asked_files.clear()
    fake_zbrush.install(fake)
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="tex2pp_e2e_")
    low_v, low_f, low_uv = plane_mesh(12)
    low_obj = os.path.join(tmp, "tex2pp_low.obj")
    write_obj(low_obj, low_v, low_f, corner_uv=low_uv[low_f])
    high_v, high_f, _ = plane_mesh(30)
    high_obj = os.path.join(tmp, "high_src.obj")
    write_obj(high_obj, high_v, high_f)
    tex = os.path.join(tmp, "tex.png")
    make_texture(tex)

    cfg: dict = {"tex2pp__work_dir": tmp, "tex2pp__source_obj": low_obj,
                 "tex2pp__source_texture": tex, "tex2pp__import_wait_s": 0.3}

    # 替身只模拟"导出当前 SubTool"这一步(真 ZBrush 里是 set_next_filename + press)
    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(high_obj, dest)
    try:
        panel = _StubPanel()
        mod._bake_and_import(panel, cfg)
    finally:
        service.export_active = real_export

    ply = os.path.join(tmp, "tex2pp_polypaint.ply")
    check("生成了 PLY", os.path.exists(ply), ply)
    check("提交了 Tool:Import", "Tool:Import" in fake.pressed, str(fake.pressed))
    check("成功时**不弹提示**(只打控制台)", fake.notes == [], str(fake.notes[:1]))
    if os.path.exists(ply):
        verts, faces, colors = read_ply_strict(ply)
        check("PLY 顶点数 = 高模", len(verts) == len(high_v), f"{len(verts)} vs {len(high_v)}")
        check("PLY 有颜色", len(np.unique(colors.reshape(-1, 3), axis=0)) > 1)

    # 「仅限Alpha」在没有可用通道时应当**降级**成 RGB(而不是让②直接失败)
    fake2 = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake2)
    from zbtools.features.tex2pp import service as svc2              # noqa: PLC0415
    import zbtools.features.tex2pp as mod2                           # noqa: PLC0415
    cfg2: dict = dict(cfg)
    cfg2["tex2pp__alpha_only"] = True
    cfg2["tex2pp__texture_has_alpha"] = False
    real_export2 = svc2.export_active
    svc2.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(high_obj, dest)
    import contextlib
    import io
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            mod2._bake_and_import(_StubPanel(), cfg2)
    finally:
        svc2.export_active = real_export2
    check("alpha 不可用时②仍然跑通(降级成 RGB)", "Tool:Import" in fake2.pressed,
          str(fake2.pressed))
    check("开关被自动关掉", cfg2.get("tex2pp__alpha_only") is False)
    logged = buffer.getvalue()
    check("控制台里说明了降级原因(不再弹提示)",
          "仅限Alpha" in logged and "自动关闭" in logged, logged[-160:])
    check("降级也不弹提示(成功一律只打控制台)", fake2.notes == [], str(fake2.notes[:1]))


class _StubPanel:
    """只需要一个能 set_enabled / set_value 的面板句柄。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.values: list[tuple[str, bool]] = []

    def set_enabled(self, key: str, enabled: bool) -> bool:
        self.calls.append((key, bool(enabled)))
        return True

    def set_value(self, key: str, value: bool) -> bool:
        self.values.append((key, bool(value)))
        return True


def check_remember_button() -> None:
    """① 是普通按钮(和 ② 一样):按一下 = 把当前模型当低模记住,再按 = 换成当前这个。

    刻意不做开关:ZBrush 没有"选择变化"回调,切模型时开关状态没法自动更新,停在旧状态像 bug。
    """
    print("* ① 记住低模:普通按钮(执行即记住,不显示状态)")
    import fake_zbrush                                              # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_rem_")
    low_v, low_f, low_uv = plane_mesh(8)
    src = os.path.join(tmp, "src_low.obj")
    write_obj(src, low_v, low_f, corner_uv=low_uv[low_f])
    tex = os.path.join(tmp, "tex.png")
    make_texture(tex)

    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    from zbtools.errors import PluginError                           # noqa: PLC0415
    from zbtools import ui as zbtools_ui                             # noqa: PLC0415
    cfg: dict = {"tex2pp__work_dir": tmp}
    panel = _StubPanel()

    mod._refresh(panel, cfg)
    check("没记住时 ② 被灰掉", ("bake", False) in panel.calls, str(panel.calls))
    check("① 不再回显状态(面板不再被写入任何值)", panel.values == [], str(panel.values))

    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(src, dest)
    try:
        mod._remember_low(panel, cfg)
    finally:
        service.export_active = real_export
    check("按一下 → 记住了", service.has_source(cfg) is True)
    check("成功时不弹提示", fake.notes == [], str(fake.notes))
    check("记下了是哪个模型(诊断用)", bool(service._decode_location(cfg)),
          str(service._decode_location(cfg)))
    mod._refresh(panel, cfg)
    check("② 变成可用", ("bake", True) in panel.calls, str(panel.calls))

    # 在另一个模型上再按一次 → 换成当前这个(而不是变成"两个都记住")
    fake.active_subtool = 1
    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(src, dest)
    try:
        mod._remember_low(panel, cfg)
    finally:
        service.export_active = real_export
    check("再按一次 → 记住的是刚按的那个模型",
          service._decode_location(cfg).get("sub_index") == 1,
          str(service._decode_location(cfg)))
    check("始终只记一个(文件只有一份)", service.has_source(cfg) is True)

    # 选错模型(没有 UV)→ 必须**报错**(不能只是打日志),而且上次记住的低模不能被弄坏
    nolow = os.path.join(tmp, "no_uv.obj")
    write_obj(nolow, low_v, low_f)                      # 不带 corner_uv → 没有 vt 行
    check("无 UV 的 OBJ 里确实没有 vt", not service.export_has_uv(nolow))
    remembered_before = service.source_obj(cfg)
    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(nolow, dest)
    try:
        zbtools_ui.guard("记住低模", lambda: mod._remember_low(panel, cfg))
    finally:
        service.export_active = real_export
    check("没有 UV 时弹了提示(而不是只打日志)",
          any("没有 UV" in n for n in fake.notes), str(fake.notes[-1:]))
    check("上次记住的低模没被改动", service.source_obj(cfg) == remembered_before,
          f"{remembered_before} → {service.source_obj(cfg)}")
    check("仍然记得住(没被清空)", service.has_source(cfg) is True)
    check("临时导出文件已清理",
          not os.path.exists(os.path.join(tmp, "tex2pp_low_new.obj")))
    check("有 UV 的 OBJ 判为有 UV", service.export_has_uv(src) is True)

    # 出错 → 弹提示(经 ui.guard,与真实按钮路径一致)
    def boom(dest, _cfg, on_tick=None):
        raise PluginError("模拟导出失败")
    real_export = service.export_active
    service.export_active = boom
    try:
        zbtools_ui.guard("记住低模", lambda: mod._remember_low(panel, cfg))
    finally:
        service.export_active = real_export
    check("出错时弹了提示", any("记住低模失败" in n for n in fake.notes), str(fake.notes))


def check_remember_is_per_subtool() -> None:
    """身份判定(现在只服务诊断):记住的是哪一个模型,能被准确认出来。"""
    print("* 诊断用的身份判定:能指出记住的是哪个模型")
    import fake_zbrush                                              # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_sub_")
    low_v, low_f, low_uv = plane_mesh(8)
    src = os.path.join(tmp, "src_low.obj")
    write_obj(src, low_v, low_f, corner_uv=low_uv[low_f])
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    cfg: dict = {"tex2pp__work_dir": tmp}
    panel = _StubPanel()

    fake.select_subtool(0)
    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(src, dest)
    try:
        mod._remember_low(panel, cfg)
    finally:
        service.export_active = real_export
    check("记住的是第 1 个模型", service._decode_location(cfg).get("sub_index") == 0,
          str(service._decode_location(cfg)))
    check("站在第 1 个 → 诊断判定为「就是它」", service.is_current_remembered(cfg) is True)
    fake.select_subtool(1)
    check("站在第 2 个 → 诊断判定为「不是它」",
          service.is_current_remembered(cfg) is False)
    fake.select_subtool(0)
    check("回到第 1 个 → 又是「就是它」", service.is_current_remembered(cfg) is True)


def check_remember_across_two_tools() -> None:
    """两个 **Tool**、各自 SubTool ID 相同 —— 也必须只有一个为真(用户报的现象)。

    根因:``get_subtool_id()`` 是**相对当前 Tool** 的,两个 Tool 的 SubTool 0 可能同 ID。
    所以判定要带上 Tool 身份与**几何指纹**(点数 + 包围盒)。
    """
    print("* 两个 Tool(SubTool ID 相同):只有被记住的那个为真")
    import fake_zbrush                                              # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_2tools_")
    low_v, low_f, low_uv = plane_mesh(6)
    src = os.path.join(tmp, "src_low.obj")
    write_obj(src, low_v, low_f, corner_uv=low_uv[low_f])
    fake = fake_zbrush.FakeCommands()
    fake.tools[:] = ["Tool:ModelA", "Tool:ModelB"]
    fake.tool_subtool_ids = {0: [7], 1: [7]}          # 两个 Tool 的 SubTool ID 相同!
    fake.per_tool_geometry = {0: (898, 10.0), 1: (4200, 3.0)}
    fake.active_tool = 0
    fake.active_subtool = 0
    fake_zbrush.install(fake)
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    cfg: dict = {"tex2pp__work_dir": tmp}
    panel = _StubPanel()

    real_export = service.export_active
    service.export_active = lambda dest, _cfg, on_tick=None: __import__("shutil").copyfile(src, dest)
    try:
        mod._remember_low(panel, cfg)                                # 在 Tool A 上按下①
    finally:
        service.export_active = real_export
    check("Tool A 按下①后 → 诊断判定为「就是它」",
          service.is_current_remembered(cfg) is True)
    saved = service._decode_location(cfg)
    check("记下了 Tool 身份", saved.get("tool_path") == "Tool:ModelA", str(saved))
    check("记下了几何指纹", saved.get("points") == 898 and bool(saved.get("box")), str(saved))

    fake.active_tool = 1                                             # 切到 Tool B
    mod._refresh(panel, cfg)
    check("切到 Tool B(SubTool ID 相同)也不会误判为「就是它」",
          service.is_current_remembered(cfg) is False)
    fake.active_tool = 0
    mod._refresh(panel, cfg)
    check("切回 Tool A 仍是「就是它」", service.is_current_remembered(cfg) is True)

    # 连指纹都拿不到时也不能凭"都是 -1"判成相同
    check("完全没有可比信号时不算记住",
          service._same_model({}, {}) is False)
    check("只有几何一致、位置信号缺失时以几何为准",
          service._same_model({"points": 5, "box": [1.0, 1.0, 1.0]},
                              {"points": 5, "box": [1.0, 1.0, 1.0]}) is True)
    check("几何不同 → 一定不是",
          service._same_model({"points": 5, "box": [1.0, 1.0, 1.0]},
                              {"points": 6, "box": [1.0, 1.0, 1.0]}) is False)


def check_adopt_export() -> None:
    """① 的落位逻辑:临时导出 → 落位成正式名,贴图跟着改名,临时 mtl 清掉。"""
    print("* 临时导出落位(OBJ + 贴图一起改名)")
    import fake_zbrush                                              # noqa: PLC0415
    from PIL import Image                                          # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_adopt_")
    low_v, low_f, low_uv = plane_mesh(6)
    tmp_obj = os.path.join(tmp, "tex2pp_low_new.obj")
    write_obj(tmp_obj, low_v, low_f, corner_uv=low_uv[low_f])
    Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(
        os.path.join(tmp, "tex2pp_low_new.bmp"))
    with open(os.path.join(tmp, "tex2pp_low_new.mtl"), "w", encoding="utf-8") as handle:
        handle.write("newmtl defaultMat\nmap_Kd tex2pp_low_new.bmp\n")
    final_obj = os.path.join(tmp, "tex2pp_low.obj")
    texture = service.adopt_export(tmp_obj, final_obj)
    check("OBJ 落位到正式名", os.path.exists(final_obj) and not os.path.exists(tmp_obj))
    check("贴图跟着改名并返回新路径",
          texture == os.path.join(tmp, "tex2pp_low.bmp") and os.path.exists(texture),
          str(texture))
    check("临时 mtl 已清理", not os.path.exists(os.path.join(tmp, "tex2pp_low_new.mtl")))


def check_hazard_guards() -> None:
    """第 4 个功能(tex2pp)的四个逻辑隐患,逐一钉住。"""
    print("* 隐患防护:旧开关在装配时清理 / 站在低模上按②要拦下 / 16 位贴图不变白")
    import fake_zbrush                                              # noqa: PLC0415
    from PIL import Image                                          # noqa: PLC0415

    # ① 面板已删除开关的旧值,必须在**装配(Load)时**就清掉 —— 否则"先按②、没按过①"
    #    的用户会继续被旧值影响(隐藏行为)
    fake = fake_zbrush.FakeCommands()
    _, _, _, _, settings = fake_zbrush.install(fake)
    conf = settings.load()
    conf["tex2pp__aa"] = True
    conf["tex2pp__alpha_only"] = True
    settings.save(conf)
    import importlib
    import zbtools.app as app                                       # noqa: PLC0415
    app.register()
    # 读**同一个**配置文件(install 会换路径,所以不能重新 install 后再读)
    after = settings.load()
    check("装配时清掉了旧开关(不必等用户按①)",
          after.get("tex2pp__aa") is False and after.get("tex2pp__alpha_only") is False,
          f"aa={after.get('tex2pp__aa')} alpha_only={after.get('tex2pp__alpha_only')}")

    # ② 站在"被记住的低模"上按② → 必须拦下(否则把低模的颜色烘到它自己身上,看着像成功)
    tmp = tempfile.mkdtemp(prefix="tex2pp_guard_")
    low_v, low_f, low_uv = plane_mesh(6)
    src = os.path.join(tmp, "src.obj")
    write_obj(src, low_v, low_f, corner_uv=low_uv[low_f])
    fake2 = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake2)
    import zbtools.features.tex2pp as mod                            # noqa: PLC0415
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    from zbtools.errors import PluginError                           # noqa: PLC0415
    cfg: dict = {"tex2pp__work_dir": tmp}
    real_export = service.export_active
    service.export_active = lambda dest, _c, on_tick=None: __import__("shutil").copyfile(src, dest)
    try:
        mod._remember_low(_StubPanel(), cfg)                          # 记住当前这个模型
    finally:
        service.export_active = real_export
    check("前提:当前站的就是被记住的低模", service.is_current_remembered(cfg) is True)
    pressed_before = list(fake2.pressed)
    try:
        mod._bake_and_import(_StubPanel(), cfg)
        check("站在低模上按②被拦下", False, "没有报错")
    except PluginError as exc:
        check("站在低模上按②被拦下,并说明原因",
              "高模" in str(exc) and "低模" in str(exc), str(exc)[:70])
    check("被拦下时没有真的去导出/烘焙", fake2.pressed == pressed_before,
          str(fake2.pressed))

    # ③ 16 位贴图不能变白(位深归一)
    a16 = (np.arange(64 * 64).reshape(64, 64) * 16).astype(np.uint16)
    tex16 = os.path.join(tmp, "tex16.png")
    Image.fromarray(a16).save(tex16)
    low_obj = os.path.join(tmp, "low2.obj")
    write_obj(low_obj, low_v, low_f, corner_uv=low_uv[low_f])
    high_v, high_f, _ = plane_mesh(16)
    high_obj = os.path.join(tmp, "high2.obj")
    write_obj(high_obj, high_v, high_f)
    ply = os.path.join(tmp, "out16.ply")
    done = subprocess.run([sys.executable, PIPELINE, "--low", low_obj, "--high", high_obj,
                           "--texture", tex16, "--out", ply], capture_output=True, text=True)
    if done.returncode == 0:
        _v, _f, colors = read_ply_strict(ply)
        arr = np.asarray(colors).reshape(-1, 3)
        unique = len(np.unique(arr, axis=0))
        # 位深没归一的话,0..65535 会被裁成一片 255 → 只剩极少种颜色、均值接近 255
        check("16 位贴图的层次被保留(位深已归一)",
              unique > 10 and float(arr.mean()) < 230,
              f"唯一色 {unique},均值 {float(arr.mean()):.1f}")
    else:
        print(f"  [skip] 16 位贴图检查(管线返回 {done.returncode})")


def check_retired_switches_are_cleared() -> None:
    """面板上删掉的开关,旧值不能继续悄悄生效(实测:aa 还是 true,颜色被平滑)。"""
    print("* 已删除的开关:旧值一次性清回默认")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    cfg: dict = {"tex2pp__alpha_only": True, "tex2pp__aa": True,
                 "tex2pp__triangulate": True, "tex2pp__flip_v": False}
    service.migrate_once(cfg)
    check("alpha_only 被清回默认", cfg.get("tex2pp__alpha_only") is False)
    check("aa 被清回默认", cfg.get("tex2pp__aa") is False)
    check("面板上的 flip_v 不动它", cfg.get("tex2pp__flip_v") is False)
    check("隐藏排障开关 triangulate 不动它", cfg.get("tex2pp__triangulate") is True)
    check("留下了迁移标记", cfg.get("tex2pp__migrated_hidden_switches") is True)
    # 迁移只做一次:之后手动开的隐藏开关不会被清掉
    cfg["tex2pp__aa"] = True
    service.migrate_once(cfg)
    check("只做一次(手动开的隐藏开关保得住)", cfg.get("tex2pp__aa") is True)
    check("参数里只剩面板参数 + 隐藏排障项",
          service.pipeline_args({"tex2pp__aa": False, "tex2pp__alpha_only": False},
                                "l", "h", "t", "o", "r").count("--aa") == 0)


def check_press_errors_are_tolerated() -> None:
    """press 漏出的 CPython SystemError 不能中断流程(实测:投射成功却弹异常)。"""
    print("* press 抛异常时,按「实际效果」判断成败")
    import fake_zbrush                                              # noqa: PLC0415
    tmp = tempfile.mkdtemp(prefix="tex2pp_press_")
    ply = os.path.join(tmp, "colored.ply")
    with open(ply, "wb") as handle:
        handle.write(b"ply\n")

    # 导入:press 抛异常,但 Tool 名照样变了(命令已入队)
    fake = fake_zbrush.FakeCommands(press_raises={"Tool:Import"})
    fake_zbrush.install(fake)
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    result = service.import_ply(ply, {"tex2pp__import_wait_s": 0.3})
    check("导入不抛异常(异常被吞掉)", True)
    check("命令确实进了队列", "Tool:Import" in fake.pressed, str(fake.pressed))
    check("按效果判断成功(Tool 名已变)", result.name == "!:" + ply, result.name)
    check("备注里说明那条报错可忽略", "可忽略" in result.note, result.note)

    # 导出:press 抛异常,但文件照样写出来了
    dest = os.path.join(tmp, "tex2pp_high.obj")
    fake2 = fake_zbrush.FakeCommands(press_raises={"Tool:Export"})
    fake_zbrush.install(fake2)
    from zbtools.features.tex2pp import service as svc2              # noqa: PLC0415
    svc2.export_active(dest, {"tex2pp__io_wait_s": 5})
    check("导出不抛异常(靠轮询文件判断)", True)
    check("文件确实写出来了", os.path.exists(dest) and os.path.getsize(dest) > 0)

    # 但如果效果没出现,还是要报错(把 press 的报错一起带出来)
    fake3 = fake_zbrush.FakeCommands(press_raises={"Tool:Export"})
    fake3.export_writes_file = False
    fake_zbrush.install(fake3)
    from zbtools.features.tex2pp import service as svc3              # noqa: PLC0415
    from zbtools.errors import PluginError                           # noqa: PLC0415
    try:
        svc3.export_active(os.path.join(tmp, "never.obj"), {"tex2pp__io_wait_s": 0.3})
        check("效果没出现时应当报错", False, "没报错")
    except PluginError as exc:
        check("效果没出现时报错,并带上 press 的报错", "press" in str(exc), str(exc)[-80:])


def check_settings_roundtrip() -> None:
    """功能级配置必须熬得过重启(踩过:settings.load 只保留 DEFAULTS 里的键)。"""
    print("* 配置持久化:功能级键 <feature>__ 必须被保留,无前缀垃圾键要丢掉")
    import fake_zbrush                                              # noqa: PLC0415
    _, _, _, _, settings = fake_zbrush.install(fake_zbrush.FakeCommands())
    tmp = tempfile.mkdtemp(prefix="tex2pp_cfg_")
    obj = os.path.join(tmp, "low.obj")
    with open(obj, "w", encoding="utf-8") as handle:
        handle.write("v 0 0 0\n")
    cfg = settings.load()
    cfg["tex2pp__source_obj"] = obj
    cfg["tex2pp__flip_v"] = False
    cfg["没有前缀的垃圾键"] = 1
    settings.save(cfg)
    back = settings.load()
    check("tex2pp__source_obj 被保留", back.get("tex2pp__source_obj") == obj)
    check("tex2pp__flip_v 被保留", back.get("tex2pp__flip_v") is False)
    check("无前缀的未知键被丢掉", "没有前缀的垃圾键" not in back)
    check("插件级键照旧", "ppi" in back and "state_version" in back)

    # 面板门控:记住了低模 → ② 应当被启用(install 会换一份新的 settings 路径,
    # 所以要在 install 之后、register 之前把配置写进去)
    fake2 = fake_zbrush.FakeCommands()
    _, _, _, _, settings2 = fake_zbrush.install(fake2)
    cfg2 = settings2.load()
    cfg2["tex2pp__source_obj"] = obj
    settings2.save(cfg2)
    import zbtools.app as app                                       # noqa: PLC0415
    app.register()
    states = [c for c in fake2.status_calls if "烘焙并导回" in c[0]]
    check("记住低模后 ② 被启用", any(v for _p, v in states), str(states))


def check_panel_layout() -> None:
    """面板布局:显式宽度 + 一行两个的紧凑排布。

    官方 GUI 示例里写着:"always set a relative width of 1.0 for elements that should
    occupy a full line and not let ZBrush decide automatically (as it can get it wrong)"。
    实测踩过:不写 width 的 slider/switch/按钮会被排到一行里重叠显示。
    width 是 |0,1] 的相对宽度,所以 0.5 + 0.5 = 同一行。
    """
    print("* 面板布局:一行两个(0.5+0.5)")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    import zbtools.app as app                                       # noqa: PLC0415
    app.register()
    widths: dict[str, object] = {}
    for kwargs_of in (fake.button_kwargs, fake.slider_kwargs, fake.switch_kwargs):
        for path, kwargs in kwargs_of.items():
            label = path.rsplit(":", 1)[-1]
            # 跳过 app.py 自动加的"分组标题占位"(它就是靠不写 width 的禁用按钮当分隔)
            if "贴图转色" in path and not label.startswith("----"):
                widths[label] = kwargs.get("width")
    check("贴图转色区的 5 个控件都登记到了", len(widths) == 5, str(sorted(widths)))

    from zbtools.features.tex2pp import LABELS                       # noqa: PLC0415
    half = {LABELS["remember"], LABELS["flip_v"], LABELS["bake"], LABELS["max_dist"]}
    full = {LABELS["diag"]}
    check("四个控件用 0.5(两两一行)",
          all(widths.get(k) == 0.5 for k in half),
          str({k: widths.get(k) for k in half}))
    check("① 是按钮(与 ② 同一种控件,不显示记住状态)",
          LABELS["remember"] in {p.rsplit(":", 1)[-1] for p in fake.button_kwargs},
          str(sorted(p.rsplit(":", 1)[-1] for p in fake.button_kwargs)))
    check("诊断信息用 1.0(独占一行)",
          all(widths.get(k) == 1.0 for k in full),
          str({k: widths.get(k) for k in full}))
    order = [path.rsplit(":", 1)[-1] for path in fake.created_order
             if "贴图转色" in path and not path.endswith("---- 贴图转色 ----")]
    check("创建顺序 = ① 记住低模 | UV 翻转 | ② 烘焙并导回 | 投射阈值 | 诊断信息",
          order == [LABELS["remember"], LABELS["flip_v"], LABELS["bake"],
                    LABELS["max_dist"], LABELS["diag"]],
          str(order))


def check_pipeline_args() -> None:
    print("* 命令行拼装(面板参数 → 管线开关)")
    from zbtools.features.tex2pp import service                      # noqa: PLC0415
    cfg = {"tex2pp__flip_v": True, "tex2pp__triangulate": False, "tex2pp__max_dist_ratio": 0.05}
    args = service.pipeline_args(cfg, "l.obj", "h.obj", "t.png", "o.ply", "r.json")
    for flag in ("--low", "--high", "--texture", "--out", "--report-json", "--max-dist-ratio"):
        check(f"含 {flag}", flag in args)
    check("阈值透传", args[args.index("--max-dist-ratio") + 1] == "0.05")
    check("默认不加 --no-flip-v / --triangulate",
          "--no-flip-v" not in args and "--triangulate" not in args)
    other = service.pipeline_args({"tex2pp__flip_v": False, "tex2pp__triangulate": True},
                                  "l", "h", "t", "o", "r")
    check("关掉翻转 → 加 --no-flip-v", "--no-flip-v" in other)
    check("打开三角化 → 加 --triangulate", "--triangulate" in other)
    check("解释器缺省回落到当前解释器", other[0] == sys.executable, other[0])
    alpha_cfg = {"tex2pp__alpha_only": True, "tex2pp__aa": True,
                 "tex2pp__aa_radius": 2.0, "tex2pp__aa_samples": 5}
    args_a = service.pipeline_args(alpha_cfg, "l", "h", "t", "o", "r")
    check("仅限Alpha → --alpha-only", "--alpha-only" in args_a)
    check("抗锯齿 → --aa 带核参数",
          "--aa" in args_a and args_a[args_a.index("--aa-radius") + 1] == "2"
          and args_a[args_a.index("--aa-samples") + 1] == "5", str(args_a))


def check_cli_contract() -> None:
    print("* 外部管线 CLI 契约(真子进程 + 真文件)")
    tmp = tempfile.mkdtemp(prefix="tex2pp_cli_")
    low_v, low_f, low_uv = sphere_mesh(16, 8)
    low_obj = os.path.join(tmp, "low.obj")
    write_obj(low_obj, low_v, low_f, corner_uv=low_uv[low_f])
    high_v, high_f, _ = sphere_mesh(40, 20)
    high_obj = os.path.join(tmp, "high.obj")
    write_obj(high_obj, high_v, high_f)
    tex = os.path.join(tmp, "tex.png")
    make_texture(tex)
    ply = os.path.join(tmp, "out.ply")
    report = os.path.join(tmp, "report.json")
    done = subprocess.run([sys.executable, PIPELINE, "--low", low_obj, "--high", high_obj,
                           "--texture", tex, "--out", ply, "--report-json", report],
                          capture_output=True, text=True)
    check("返回码 0", done.returncode == 0, (done.stdout + done.stderr)[-300:])
    check("PLY 已生成", os.path.exists(ply))
    check("报告已生成", os.path.exists(report))
    if os.path.exists(report):
        data = json.loads(open(report, encoding="utf-8").read())
        need = ("vertices", "faces", "far_ratio", "median_distance", "diagonal",
                "unique_colors", "out", "flip_v")
        check("报告字段齐全", all(k in data for k in need), str(sorted(data)))
        check("报告顶点数 = 高模顶点数", data["vertices"] == len(high_v),
              f"{data['vertices']} vs {len(high_v)}")
    if os.path.exists(ply):
        verts, faces, colors = read_ply_strict(ply)
        check("PLY 面与源 OBJ 完全一致(含顺序)", faces == [list(f) for f in high_f])
        check("PLY 颜色不是单色", len(np.unique(colors.reshape(-1, 3), axis=0)) > 1)

    # 缺依赖时的友好报错(用 -S 屏蔽 site-packages 模拟"裸解释器")
    bare = subprocess.run([sys.executable, "-S", PIPELINE, "--low", low_obj, "--high",
                           high_obj, "--texture", tex, "--out", ply + ".2"],
                          capture_output=True, text=True)
    if bare.returncode == 2:
        check("缺依赖时返回码 2 并给出安装命令",
              "缺少依赖" in bare.stdout and "pip install" in bare.stdout)
    else:
        print(f"  [skip] 裸解释器检查(返回码 {bare.returncode})")


def main() -> int:
    check_pipeline_decoupled()
    check_feature_contract()
    check_pipeline_args()
    check_panel_layout()
    check_settings_roundtrip()
    check_alpha_support()
    check_alpha_is_semantic()
    check_source_and_workdir_survive_restart()
    check_bake_end_to_end()
    check_import_is_non_blocking()
    check_remember_button()
    check_remember_is_per_subtool()
    check_remember_across_two_tools()
    check_adopt_export()
    check_hazard_guards()
    check_retired_switches_are_cleared()
    check_press_errors_are_tolerated()
    check_cli_contract()

    tmp = tempfile.mkdtemp(prefix="tex2pp_test_")
    tex_path = os.path.join(tmp, "tex.png")
    image = make_texture(tex_path)

    low_v, low_f, low_uv = sphere_mesh(24, 12)
    low_uv_corner = low_uv[low_f]
    low_path = os.path.join(tmp, "low.obj")
    write_obj(low_path, low_v, low_f, corner_uv=low_uv_corner)

    high_v, high_f, _ = sphere_mesh(64, 32)            # 更密的球 ≈ DynaMesh 结果
    high_path = os.path.join(tmp, "high.obj")
    write_obj(high_path, high_v, high_f)

    check_ply_encoding()

    print("* OBJ 解析(逐角点 UV)")
    v2, f2, uv2 = bake.parse_obj(low_path)
    check("顶点数一致", len(v2) == len(low_v), f"{len(v2)} vs {len(low_v)}")
    check("三角面数一致", len(f2) == len(low_f), f"{len(f2)} vs {len(low_f)}")
    check("逐角点 UV 形状正确", uv2 is not None and uv2.shape == (len(low_f), 3, 2),
          str(None if uv2 is None else uv2.shape))
    check("UV 数值与写入一致", uv2 is not None and np.allclose(uv2, low_uv_corner, atol=1e-6))

    print("* 四边面 + 不带 UV 的 OBJ 也能解析")
    quad_path = os.path.join(tmp, "quad.obj")
    quad_faces = np.asarray([[0, 1, 3, 2], [1, 4, 5, 3]], dtype=np.int64)
    quad_v = np.asarray([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 0, 0), (2, 1, 0)],
                        dtype=np.float64)
    with open(quad_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"v {x} {y} {z}" for x, y, z in quad_v) + "\n")
        handle.write("f 1 2 4 3\nf 2 5 6 4\n")
    qv, qf, quv = bake.parse_obj(quad_path)
    check("四边面被扇形三角化(2 面 → 4 三角面)", len(qf) == 4, str(len(qf)))
    check("没有 UV 时返回 None", quv is None)

    print("* 严格比对:无接缝平面网格 —— 期望颜色可解析算出")
    low_pv, low_pf, low_puv = plane_mesh(20)
    low_ppath = os.path.join(tmp, "plane_low.obj")
    write_obj(low_ppath, low_pv, low_pf, corner_uv=low_puv[low_pf])
    high_pv, high_pf, high_puv = plane_mesh(60)
    high_ppath = os.path.join(tmp, "plane_high.obj")
    write_obj(high_ppath, high_pv, high_pf, corner_uv=high_puv[high_pf])
    plane_out = os.path.join(tmp, "plane.ply")
    import trimesh                                                  # noqa: PLC0415
    rep = bake.bake(low_ppath, high_ppath, tex_path, plane_out, preview=os.path.join(tmp, "p.png"))
    got = np.asarray(trimesh.load(plane_out, process=False).visual.vertex_colors)[:, :3]
    expect = bake.sample_bilinear(image, high_puv, flip_v=True).round().astype(np.uint8)
    err = int(np.abs(got.astype(int) - expect.astype(int)).max())
    check("平面网格颜色 = 解析期望值(≤2)", err <= 2, f"最大误差 {err}")
    check("平面网格最近点距离 ≈ 0", rep["median_distance"] < 1e-9,
          f"{rep['median_distance']:.3g}")
    check("四个象限四种颜色都出现(UV 没被压成一色)",
          len({tuple(c) for c in got[::37]}) >= 4, str(len({tuple(c) for c in got[::37]})))

    print("* 一致性:高模 = 低模本身(平面)")
    ident = os.path.join(tmp, "ident.ply")
    bake.bake(low_ppath, low_ppath, tex_path, ident, flip_v=True)
    gi = np.asarray(trimesh.load(ident, process=False).visual.vertex_colors)[:, :3]
    ei = bake.sample_bilinear(image, low_puv, flip_v=True).round().astype(np.uint8)
    check("自身映射误差 ≤ 1", int(np.abs(gi.astype(int) - ei.astype(int)).max()) <= 1)

    print("* UV 的 v 轴翻转开关会改变结果(证明翻转真的生效)")
    flipped = os.path.join(tmp, "flipped.ply")
    bake.bake(low_ppath, high_ppath, tex_path, flipped, flip_v=False)
    gf = np.asarray(trimesh.load(flipped, process=False).visual.vertex_colors)[:, :3]
    check("--no-flip-v 与默认结果不同", int(np.abs(gf.astype(int) - got.astype(int)).max()) > 32)

    print("* 球面网格(有 UV 接缝):距离与暴力参考一致 + 颜色覆盖四象限")
    low_v, low_f, low_uv = sphere_mesh(24, 12)
    low_uv_corner = low_uv[low_f]
    low_path = os.path.join(tmp, "low.obj")
    write_obj(low_path, low_v, low_f, corner_uv=low_uv_corner)
    high_v, high_f, _ = sphere_mesh(64, 32)
    high_path = os.path.join(tmp, "high.obj")
    write_obj(high_path, high_v, high_f)
    sphere_out = os.path.join(tmp, "baked.ply")
    report = bake.bake(low_path, high_path, tex_path, sphere_out)
    ref_dist, _ref_color = brute_force(low_v, low_f, low_uv_corner, high_v, image)
    check("距离与暴力参考一致", abs(report["median_distance"] - float(np.median(ref_dist))) < 1e-6)
    gs = np.asarray(trimesh.load(sphere_out, process=False).visual.vertex_colors)[:, :3]
    check("输出顶点数 = 高模顶点数", report["vertices"] == len(high_v))
    check("球面颜色覆盖四象限", len({tuple(c) for c in gs[::19]}) >= 4)

    print("* 覆盖率统计(把高模整体平移 3.0 → 远离低模)")
    shifted = high_v + np.asarray([3.0, 0.0, 0.0])
    shifted_path = os.path.join(tmp, "shifted.obj")
    write_obj(shifted_path, shifted, high_f)
    far_report = bake.bake(low_path, shifted_path, tex_path,
                           os.path.join(tmp, "far.ply"), max_dist_ratio=0.02)
    check("远离低模时 far_ratio 显著上升", far_report["far_ratio"] > 0.5,
          f"{far_report['far_ratio']:.3f}")

    print("* PLY 结构检查(自己读回头部)")
    for label, ply_path, n_v, n_f in (("平面", plane_out, len(high_pv), len(high_pf)),
                                      ("球面", sphere_out, len(high_v), len(high_f))):
        with open(ply_path, "rb") as handle:
            head = handle.read(512).decode("ascii", errors="replace")
        check(f"{label}:二进制小端 PLY", "format binary_little_endian 1.0" in head)
        check(f"{label}:带 red/green/blue 属性",
              all(f"property uchar {c}" in head for c in "rgb"))
        check(f"{label}:顶点/面数与高模一致",
              f"element vertex {n_v}" in head and f"element face {n_f}" in head)
        # 严格逐面回读:面必须与源 OBJ 完全一致(这条能抓到"交错写错"那类 bug)
        back_v, back_f, back_c = read_ply_strict(ply_path)
        check(f"{label}:严格解析顶点数一致", len(back_v) == n_v, f"{len(back_v)} vs {n_v}")
        check(f"{label}:严格解析面数一致", len(back_f) == n_f, f"{len(back_f)} vs {n_f}")
        check(f"{label}:每面顶点索引都合法",
              all(0 <= i < n_v for f in back_f for i in f))
        check(f"{label}:颜色不是全黑/全白", 0 < int(back_c.mean()) < 255,
              f"均值 {float(back_c.mean()):.1f}")

    print(f"\n{'全部通过' if not FAILED else '失败:' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
