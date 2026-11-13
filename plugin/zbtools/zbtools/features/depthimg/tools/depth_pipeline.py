#!/usr/bin/env python3
"""贴图 → 深度图(外部管线;与插件**零耦合**:只吃 argv、只打 stdout、只写文件)。

被「图片转深度图」(depthimg)功能调用:读选中模型的贴图 → 这里算深度 → 送回 ZBrush。

为什么要独立成外部进程:ZBrush 自带的 Python 里没有 torch,而深度估计要 torch。
插件只负责拼命令行、把 stdout 转给用户,并读回 ``--report-json``。

引擎(``--engine``):

    auto  有可用的 DA V2 权重就 dav2,否则若装了 depth_anything_3 就 da3,都没有 → 报错退出 2
    dav2  Depth Anything V2(面板上那四档规模 V2-S/B/L/G;只有 Small 可商用;模型定义在
          vendored/,需要 torch)
    da3   Depth Anything 3(官方 depth_anything_3 包;需要 torch + 那个包 + 权重)
    luma  亮度直接当高度(零依赖:只要 numpy + pillow)。已经是高度图的贴图用它最快
    sdf   距离场圆润浮雕:把图案(alpha 或阈值化的亮度)向里做倒角
          —— 平铺图案/贴花这种**没有真实景深**的图,这条比 AI 深度对路(实测)

关键约定(踩过坑才定下来的):

    * **输出尺寸 = 输入尺寸**(不是 518)。UV 是按贴图的像素比例铺的,尺寸一变,图案就错位;
      DA 系列内部会缩到 518 的倍数推理,然后再插值回原尺寸。
    * DA 系列输出是**近处亮**的视差式深度。要"高出亮/低处亮"反过来时用 ``--invert``。
    * ``--flip-v`` 是**给 ZBrush 用的朝向**:ZBrush 的图像是自下而上存的,原样送进去会上下颠倒
      (实测),所以插件默认加这个开关;代价是在普通看图软件里这张 PNG 看着是倒的。
    * 输入带 alpha 且透明区 RGB 是脏数据时(实测那张 PNG 透明区 RGB 均值只有 75,看着像黑的),
      直接喂 RGB 会让模型把透明区当阴影 —— 所以默认**合成到白底**再推理(``--bg``)。
    * 归一化按**百分位裁剪**而不是纯 min/max:一个极远像素就能把整张图压成灰糊。

用法::

    depth_pipeline.py --in tex.png --out depth.png --engine auto [--model foo.safetensors]
        [--encoder vitl] [--device auto|mps|cpu] [--size 518] [--invert] [--clip 0.5]
        [--bg white|black|none] [--sdf-radius 8] [--sdf-threshold 0.5] [--report-json r.json]

退出码:``0`` 成功;``2`` 缺少依赖或权重(错误里带怎么装);``1`` 其它失败。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from PIL import ImageFilter

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDORED = os.path.join(_HERE, "vendored")

#: 上游 dpt.py 的构造参数(编码器 → 参数量),safetensors 文件名里带哪个就用哪个
ENCODER_CONFIGS: dict[str, dict] = {
    "vits": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
    "vitb": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
    "vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
    "vitg": {"encoder": "vitg", "features": 384, "out_channels": [1536, 1536, 1536, 1536]},
}

_MEAN = (0.485, 0.456, 0.406)          # ImageNet 归一化(上游同款)
_STD = (0.229, 0.224, 0.225)


class MissingDependency(RuntimeError):
    """依赖/权重缺失 —— 退出码 2(插件据此给出可照抄的安装命令)。"""


def log(text: str) -> None:
    print(text, flush=True)


def progress(value: float) -> None:
    """报一次**真进度**(0~1)。格式是插件的约定(见 zbtools/ui.py 的 PROGRESS_PREFIX):
    插件认出这一行就喂给 ZBrush 顶部状态条,并且**不**把它当日志打给用户。
    为什么要显式报:vits@1554 一次推理 0.7s、vitg@1554 要十几秒 —— 中间一行日志都没有,
    插件只能靠"按时间爬升";有真进度就不必猜。
    """
    print(f"[zbtools-progress] {max(0.0, min(1.0, float(value))):.3f}", flush=True)


# --------------------------------------------------------------------------- #
# 图片读写
# --------------------------------------------------------------------------- #
def load_rgb(path: str, bg: str) -> tuple["object", dict]:
    """读贴图 → float32 RGB [0,1] 数组;返回 (数组, 报告片段)。

    ``bg`` 处理带 alpha 的图:``white``/``black`` 先合成到该底色再推理(默认),
    ``none`` 表示不看 alpha、直接用 RGB(透明区的 RGB 可能是脏数据)。
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as handle:
        bands = handle.getbands()
        size = handle.size
        rgba = np.asarray(handle.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = rgba[..., 3]
    transparent = float((alpha < 8.0 / 255.0).mean())
    info = {"size": [int(size[0]), int(size[1])], "had_alpha": "A" in bands,
            "transparent_ratio": round(transparent, 4), "composited_over": ""}
    if transparent > 0.001 and bg != "none":
        # 透明区的 RGB 常常是脏数据(实测那张 PNG 透明区 RGB 均值只有 75,像一团黑),
        # 直接喂给模型会被当成阴影/暗部 —— 所以先按所选底色合成。
        base = {"white": 1.0, "black": 0.0}[bg]
        rgb = rgba[..., :3] * alpha[..., None] + base * (1.0 - alpha[..., None])
        info["composited_over"] = bg
        log(f"[深度图] 输入带透明({transparent * 100:.1f}% 像素全透明)→ 先合成到 {bg} 底再推理")
    else:
        rgb = rgba[..., :3]
    info["alpha"] = alpha
    return np.asarray(rgb, dtype=np.float32), info


def save_gray(values, path: str, invert: bool, clip: float,
              bit_depth: int = 16) -> dict:
    """把浮点高度场写 8 位灰度 PNG(尺寸与输入一致 —— UV 靠这个对齐)。

    ``clip``:两端各裁掉这么多百分比再拉伸。纯 min/max 会被一个极值像素毁掉对比度
    (实测:一个远处的像素会让整张图变成一片灰)。
    """
    import numpy as np
    from PIL import Image

    array = np.asarray(values, dtype=np.float32)
    if invert:
        array = -array
    low_pct, high_pct = (float(clip), 100.0 - float(clip)) if clip > 0 else (0.0, 100.0)
    low = float(np.percentile(array, low_pct))
    high = float(np.percentile(array, high_pct))
    if high - low < 1e-9:
        log(f"[深度图] ⚠ 高度场几乎是常量(范围 {low:.6g}~{high:.6g}),输出会是纯色")
        high = low + 1e-9
    norm = np.clip((array - low) / (high - low), 0.0, 1.0)
    # 8 位只有 256 级 → 拿去做浮雕会出现**台阶/等值线**(用户反馈的"锯齿"之一);
    # 所以默认再写一张 16 位 TIFF(ZBrush 的位移/置换要的就是 16 位),PNG 保持 8 位
    # 以便继续当贴图/Alpha 用(ZBrush 的贴图就是 8 位)。
    high_path = ""
    if int(bit_depth) >= 16:
        base, _ext = os.path.splitext(path)
        high_path = base + "_16bit.tif"
        Image.fromarray((norm * 65535.0 + 0.5).astype(np.uint16)).save(high_path)
    png = (norm * 255.0 + 0.5).astype(np.uint8)
    Image.fromarray(png).save(path)
    return {
        "raw_min": round(float(array.min()), 6),
        "raw_max": round(float(array.max()), 6),
        "raw_mean": round(float(array.mean()), 6),
        "clip_low": round(low, 6),
        "clip_high": round(high, 6),
        "out_min": int(png.min()),
        "out_max": int(png.max()),
        "out_mean": round(float(png.mean()), 2),
        "out_size": [int(png.shape[1]), int(png.shape[0])],
        "inverted": bool(invert),
        "bit_depth": int(bit_depth),
        "levels": int(np.unique(png).size),
        "high_precision_path": high_path,
    }


# --------------------------------------------------------------------------- #
# safetensors(内置读取器:不给外部解释器再加一个依赖)
# --------------------------------------------------------------------------- #
def read_safetensors(path: str) -> dict:
    """读 safetensors 权重 → {名字: numpy 数组}。

    格式很简单(8 字节头长度 + JSON 头 + 原始数据),所以这里自己读 ——
    一是给外部解释器省掉 ``safetensors`` 依赖,二是让读取器**不依赖 torch**
    (测试在没有 torch 的解释器里也能验证它)。调用方转 torch 张量时用
    ``torch.from_numpy``(零拷贝)。支持 fp32/fp16/bf16/int8 等常见权重类型。
    """
    import numpy as np

    dtype_map = {"F32": np.float32, "F16": np.float16, "I8": np.int8, "U8": np.uint8,
                 "I32": np.int32, "I64": np.int64, "F64": np.float64}
    with open(path, "rb") as handle:
        header_len = int.from_bytes(handle.read(8), "little")
        if not 0 < header_len < 100_000_000:
            raise ValueError(f"{path} 不像 safetensors(头部长度 {header_len})")
        header = json.loads(handle.read(header_len).decode("utf-8"))
        base = 8 + header_len
        out: dict = {}
        for name, spec in header.items():
            if name == "__metadata__":
                continue
            kind = spec.get("dtype")
            if kind not in dtype_map and kind != "BF16":
                raise ValueError(f"不支持的权重类型 {kind}({name})")
            shape = tuple(int(v) for v in spec["shape"])
            begin, end = (int(v) for v in spec["data_offsets"])
            handle.seek(base + begin)
            raw = np.frombuffer(handle.read(end - begin), dtype=np.uint8).copy()
            if kind == "BF16":            # numpy 没有 bfloat16:高 16 位补零升到 fp32
                wide = raw.view(np.uint16).astype(np.uint32) << 16
                out[name] = wide.view(np.float32).reshape(shape)
            else:
                out[name] = raw.view(dtype_map[kind]).reshape(shape)
    return out


def encoder_from_name(path: str) -> str:
    """从权重文件名认出编码器(``depth_anything_v2_vitl_fp32.safetensors`` → ``vitl``)。"""
    name = os.path.basename(path).lower()
    for encoder in ENCODER_CONFIGS:
        if encoder in name:
            return encoder
    return ""


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
def _check_torch() -> None:
    try:
        import torch                                                     # noqa: F401
    except Exception as exc:                                             # noqa: BLE001
        raise MissingDependency(
            f"这个引擎需要 torch,但当前解释器里没有({type(exc).__name__}: {exc})。\n"
            f"解释器:{sys.executable}\n"
            f"装法:{sys.executable} -m pip install torch\n"
            f"(或者把 depthimg__interpreter 指到一个已经有 torch 的解释器,"
            f"比如 ComfyUI 自带的那个 venv)") from exc


def _resize_array(array, width: int, height: int):
    """浮点数组的双线性缩放(PIL,F 模式)。"""
    import numpy as np
    from PIL import Image
    if array.shape[::-1] == (width, height):
        return array.astype(np.float32)
    image = Image.fromarray(array.astype(np.float32))
    return np.asarray(image.resize((width, height), Image.BILINEAR), dtype=np.float32)


def guided_upsample(depth_low, guide_low, guide_high, radius: int = 4, eps: float = 1e-4):
    """**以原图为引导**把低分辨率深度放大到原尺寸(He 的引导滤波,快版)。

    为什么:直接双线性放大会把边缘糊掉/放大成台阶;引导滤波让边缘跟着原图走,
    浮雕的轮廓明显更干净。做法是在**低分辨率**上算出线性系数 (a,b),把它们放大后
    直接套在高分辨率的原图上 —— 这就是"快引导滤波",O(N) 且只用一次双线性。
    """
    import numpy as np
    from scipy import ndimage

    mean_i = ndimage.uniform_filter(guide_low, radius)
    mean_p = ndimage.uniform_filter(depth_low, radius)
    var_i = ndimage.uniform_filter(guide_low * guide_low, radius) - mean_i * mean_i
    cov_ip = ndimage.uniform_filter(guide_low * depth_low, radius) - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    height, width = guide_high.shape[:2]
    a_up = _resize_array(ndimage.uniform_filter(a, radius), width, height)
    b_up = _resize_array(ndimage.uniform_filter(b, radius), width, height)
    return a_up * guide_high + b_up


def run_dav2(args, rgb, info: dict) -> tuple[object, dict]:
    """Depth Anything V2(vendored 模型定义 + safetensors 权重)。"""
    _check_torch()
    import numpy as np
    import torch

    model_path = args.model
    if not model_path:
        raise MissingDependency(
            "没有可用权重的路径(--model 为空)。\n"
            "在 settings.json 里用 depthimg__model 指定一个 "
            "depth_anything_v2_*_fp32.safetensors 的**全路径**即可\n"
            "(ComfyUI 的 models/depthanything/ 下就有;也可以让插件自动找)")
    if not os.path.exists(model_path):
        raise MissingDependency(f"权重文件不存在:{model_path}")
    encoder = args.encoder or encoder_from_name(model_path)
    if encoder not in ENCODER_CONFIGS:
        raise MissingDependency(
            f"认不出权重是哪个编码器(vits/vitb/vitl/vitg):{os.path.basename(model_path)}\n"
            f"文件名里要带编码器名字,或者用 --encoder 指定")

    device = _pick_device(args.device, torch)
    if _VENDORED not in sys.path:
        sys.path.insert(0, _VENDORED)
    from depth_anything_v2.dpt import DepthAnythingV2

    height, width = rgb.shape[:2]
    wanted = int(args.size) or auto_size(width, height, int(args.size_max))
    log(f"[深度图] 引擎 dav2({encoder}),权重 {os.path.basename(model_path)}"
        f"({os.path.getsize(model_path) / 2 ** 30:.2f} GiB),设备 {device};"
        f"推理边长 {wanted}"
        + ("(自动:跟贴图分辨率走)" if not int(args.size) else ""))
    t0 = time.time()
    model = DepthAnythingV2(**ENCODER_CONFIGS[encoder])
    # from_numpy 是零拷贝(共享内存),load_state_dict 会把数据拷进模型参数里
    state = {name: torch.from_numpy(array)
             for name, array in read_safetensors(model_path).items()}
    model.load_state_dict(state, strict=True)          # 键名对不上就直接炸(别静默出错)
    del state
    model = model.to(device).eval()
    log(f"[深度图] 权重加载 {time.time() - t0:.1f}s")
    progress(0.15)

    new_w, new_h = _input_size(width, height, wanted)
    small = _resize(rgb, new_w, new_h)
    batch = torch.from_numpy(small.transpose(2, 0, 1)[None])
    batch = ((batch - torch.tensor(_MEAN).view(1, 3, 1, 1))
             / torch.tensor(_STD).view(1, 3, 1, 1)).to(device)
    t0 = time.time()
    with torch.no_grad():
        depth = model.forward(batch)
    if device == "mps":
        torch.mps.synchronize()
    log(f"[深度图] 推理 {time.time() - t0:.1f}s @ {new_w}x{new_h}")
    progress(0.60)
    low = depth.detach().to("cpu").float().numpy()
    low2 = low[0] if low.ndim == 3 else low
    if args.no_guided_upsample:
        array = _resize_array(low2, width, height)
        how = "双线性"
    else:
        # 引导图:低分辨率用"模型实际看到的那张",高分辨率用原图
        guide_low = (0.299 * small[..., 0] + 0.587 * small[..., 1]
                     + 0.114 * small[..., 2]).astype(np.float32)
        guide_high = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
                      + 0.114 * rgb[..., 2]).astype(np.float32)
        array = guided_upsample(low2, guide_low, guide_high)
        how = "引导滤波(跟着原图边缘)"
    log(f"[深度图] 放大回原尺寸:{how}")
    progress(0.78)
    return array, {"model": model_path, "encoder": encoder, "device": device,
                   "infer_size": [new_w, new_h], "upsample": how}


def _import_da3():
    """导入 Depth Anything 3 的 API 类(拿到 (类, 轻装说明))。

    ⚠ 官方包的 ``api.py`` 会顺手把**导出用**的重依赖全拉起来(moviepy / open3d / plyfile /
    evo …),而我们只要一张深度图。这些依赖在 macOS 上还特别难装(xformers 根本没有轮子),
    所以:正常 import 失败时,把用不到的两个子模块换成**一被调用就报错**的空壳再试一次 ——
    于是"轻装 venv"(torch + `depth-anything-3 --no-deps` + 少量依赖)就能跑。
    真要用到导出/位姿对齐时它会立刻报错,不会静默给出错误结果。
    """
    # DA3 自己那套 logger 会往 stdout 打带 ANSI 颜色码的 INFO(实测会混进 ZBrush 控制台);
    # 它读的是 DA3_LOG_LEVEL 环境变量,所以在 import 之前就压到 WARN。
    os.environ.setdefault("DA3_LOG_LEVEL", "WARN")
    try:
        from depth_anything_3.api import DepthAnything3
        _quiet_da3_logger()
        return DepthAnything3, ""
    except Exception as exc:                                             # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"
    import types

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("DA3 的这个功能没装(导出/位姿对齐的重依赖);单图深度不需要它")

    for name in ("depth_anything_3.utils.export",
                 "depth_anything_3.utils.export.gs",
                 "depth_anything_3.utils.export.glb",
                 "depth_anything_3.utils.export.colmap",
                 "depth_anything_3.utils.export.npz",
                 "depth_anything_3.utils.export.depth_vis",
                 "depth_anything_3.utils.export.feat_vis"):
        module = types.ModuleType(name)
        module.export = unavailable
        for attribute in ("export_to_glb", "export_to_npz", "export_to_mini_npz",
                          "export_to_gs_ply", "export_to_gs_video", "export_to_colmap",
                          "export_to_depth_vis"):
            setattr(module, attribute, unavailable)
        sys.modules[name] = module
    pose = types.ModuleType("depth_anything_3.utils.pose_align")
    pose.align_poses_umeyama = unavailable
    sys.modules["depth_anything_3.utils.pose_align"] = pose
    log(f"[深度图] DA3 的导出/位姿依赖没装({note})→ 用空壳顶掉它们"
        f"(单图深度用不到)")
    from depth_anything_3.api import DepthAnything3
    _quiet_da3_logger()
    return DepthAnything3, "轻装(导出/位姿依赖用空壳顶掉)"


def _quiet_da3_logger() -> None:
    """把 DA3 自带的日志压到 WARN(它是个自己打印的 Logger 类,不是标准 logging)。"""
    try:
        from depth_anything_3.utils.logger import LOG_LEVELS, logger as da3_logger
        da3_logger.level = LOG_LEVELS["WARN"]
    except Exception:                                                    # noqa: BLE001
        pass


def run_da3(args, rgb, info: dict) -> tuple[object, dict]:
    """Depth Anything 3(官方 ``depth_anything_3`` 包)。

    ⚠ DA3 直接预测 **depth**(越大越远),与 DA V2 的视差式输出(越大越近)**方向相反**;
    为了让两个引擎给人的感觉一致(亮 = 近/高),这里对 DA3 的结果做一次倒相。
    """
    _check_torch()
    import numpy as np
    from PIL import Image
    import torch

    try:
        DepthAnything3, note = _import_da3()
    except Exception as exc:                                             # noqa: BLE001
        raise MissingDependency(
            f"没有装 Depth Anything 3 的包({type(exc).__name__}: {exc})。\n"
            f"解释器:{sys.executable}\n"
            f"⚠ 别把它装进 ComfyUI 的 venv:它的 requirements 里有 numpy<2 / open3d /\n"
            f"   pycolmap / xformers,会把 ComfyUI 的环境弄坏(而且 xformers 在 macOS\n"
            f"   上没有轮子)。**单开一个 venv** 装:见 docs/INSTALL.md 第 6.1 节") from exc

    repo = args.model or "depth-anything/DA3MONO-LARGE"
    device = _pick_device(args.device, torch)
    # 实测:504 时细发丝会被并进背景(黑色小岛 16 块),1008 → 1 块,1512 → 0 块。
    # 所以默认跟着输入分辨率走(DA V2 不一样:它 518 → 1554 只多 6% 细节,所以那边不自动)。
    image_size = Image.open(args.source).size
    wanted_res = int(args.da3_res) or auto_da3_res(*image_size)
    log(f"[深度图] 引擎 da3({repo}),设备 {device}"
        + (f";{note}" if note else ""))
    t0 = time.time()
    model = DepthAnything3.from_pretrained(repo)
    if hasattr(model, "to"):
        model = model.to(device=device)
    log(f"[深度图] 权重加载 {time.time() - t0:.1f}s")

    image = Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8))
    t0 = time.time()
    # 不传 export_dir → 不触发导出(那会用到被顶掉的重依赖)
    kwargs = {"process_res": wanted_res}
    log(f"[深度图] DA3 process_res {wanted_res}"
        + ("(自动:跟输入长边走)" if not int(args.da3_res) else ""))
    prediction = model.inference([image], **kwargs)
    log(f"[深度图] 推理 {time.time() - t0:.1f}s")
    depth = prediction.depth
    array = np.asarray(
        depth.detach().float().cpu().numpy() if hasattr(depth, "detach") else depth,
        dtype=np.float32)
    if array.ndim == 3:
        array = array[0]
    height, width = rgb.shape[:2]
    if array.shape != (height, width):     # DA3 输出的是它内部处理后的分辨率
        array = np.asarray(Image.fromarray(array.astype(np.float32)).resize(
            (width, height), Image.BILINEAR), dtype=np.float32)
    return -array, {"model": repo, "device": device,
                    "infer_size": [int(array.shape[1]), int(array.shape[0])],
                    "orientation": "已倒相(DA3 直接预测 depth;统一成亮=近)"}


def run_luma(args, rgb, info: dict) -> tuple[object, dict]:
    """亮度直接当高度(零依赖)。贴图本来就是高度图/灰阶图案时用它。"""
    import numpy as np
    array = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    log("[深度图] 引擎 luma(亮度当高度;零依赖、瞬时)")
    return array, {"model": "(亮度,无模型)", "device": "cpu"}


def run_sdf(args, rgb, info: dict) -> tuple[object, dict]:
    """距离场圆润浮雕:图案(alpha 或阈值化亮度)向里倒角 → 像"压出来"的浮雕。

    平铺图案这类**没有真实景深**的贴图,AI 深度只会给一团模糊(实测);
    距离场反而干净:边缘倒角、笔画中间最高,等价于 ZBrush 里对遮罩做 Inflate 的效果。
    """
    import numpy as np
    try:
        from scipy import ndimage
    except Exception as exc:                                             # noqa: BLE001
        raise MissingDependency(
            f"这个引擎需要 scipy,但当前解释器里没有({type(exc).__name__})。\n"
            f"装法:{sys.executable} -m pip install scipy") from exc

    alpha = info.get("alpha")
    if alpha is not None and float((alpha < 0.5).mean()) > 0.001:
        mask = alpha > 0.5
        source = "alpha"
    else:
        luma = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        mask = luma > float(args.sdf_threshold)
        source = f"亮度 > {float(args.sdf_threshold):g}"
    # 硬阈值的边界是像素级的 → 距离场是**整数** → 斜坡上会出现台阶(用户看到的锯齿)。
    # 两个补丁:① 用模糊后的 mask 值给出**亚像素**修正;② 最后轻微平滑 0.7px。
    inside = ndimage.distance_transform_edt(mask)
    outside = ndimage.distance_transform_edt(~mask)
    signed = inside - outside
    soft = ndimage.gaussian_filter(mask.astype(np.float32), 0.8) - 0.5
    signed = signed + np.clip(soft * 2.0, -0.5, 0.5)     # 边界附近补出亚像素
    radius = _sdf_radius(args.sdf_radius, mask.shape)
    array = (_smoothstep(np.clip(signed, 0, None) / radius)
             - 0.5 * _smoothstep(np.clip(-signed, 0, None) / radius)).astype(np.float32)
    array = ndimage.gaussian_filter(array, 0.7)          # 抹掉最后一点像素级台阶
    log(f"[深度图] 引擎 sdf(图案判据 {source};倒角半径 {radius:g}px"
        f"{'(自动:短边 ' + f'{min(mask.shape[:2])}' + 'px 的 0.6%)' if float(args.sdf_radius or 0) <= 0 else '(手动指定)'};"
        f"图案占比 {float(mask.mean()) * 100:.1f}%)")
    return array, {"model": f"(距离场,判据 {source})", "device": "cpu",
                   "sdf_radius": radius,
                   "sdf_radius_auto": float(args.sdf_radius or 0) <= 0}


def _median3x3(array):
    """3x3 中值滤波(numpy + PIL,不需要 scipy —— 深度引擎的解释器里可能没装 scipy)。"""
    import numpy as np
    from PIL import Image
    # 先归一化到 0..255 再做中值(8 位精度对这种"去孤立极值"完全够)
    low, high = float(array.min()), float(array.max())
    span = (high - low) or 1.0
    small = ((array - low) / span * 255.0).astype(np.uint8)
    return (np.asarray(Image.fromarray(small).filter(ImageFilter.MedianFilter(3)),
                       dtype=np.float32) / 255.0 * span + low)


def despike(array, robust: float = 4.0):
    """去掉**孤立的极值像素**(返回 (结果, 被换掉的像素数))。

    实测(DA3 在插画上):头发/边缘会蹦出远离邻域的深度值,经过"百分位拉伸 + 8 位量化"后
    就是用户看到的一**片片纯黑**(= 高度直接掉到 0)。
    做法:对"与 3x3 中值的差"做稳健离群判定(1.4826×MAD),只换掉真正孤立的那些像素。
    ⚠ 只用 numpy + PIL:AI 引擎的解释器(比如那个轻装 da3 venv)不一定有 scipy。
    """
    import numpy as np

    array = np.asarray(array, dtype=np.float32)
    median = _median3x3(array)
    diff = array - median
    mad = float(np.median(np.abs(diff - np.median(diff))))
    scale = 1.4826 * mad
    # ⚠ 大片平坦的图上 MAD 会退化成 0(一半以上像素的差都是 0)→ 就检测不出任何极值。
    # 所以给一个下限:深度的 (p99-p1) 的 0.2%(实测不影响真实图上的判定,只兜住平坦区)。
    span = float(np.percentile(array, 99) - np.percentile(array, 1))
    if span <= 0:                    # 整张图都一个值(只剩几个孤点)时,退回 min/max 的跨度
        span = float(array.max() - array.min())
    scale = max(scale, 0.002 * span)
    if scale <= 0:
        return array, 0
    bad = np.abs(diff) > robust * scale
    count = int(bad.sum())
    if count == 0:
        return array, 0
    out = array.copy()
    out[bad] = median[bad]
    return out, count


def add_image_detail(depth, rgb, amount: float):
    """把**原图的高频细节**按比例叠到高度上(解决"AI 深度太平滑 → 浮雕朦胧")。

    单目深度模型输出的是平滑深度场(V2-S 尤其),浮雕想要"看清线条"就得借原图的细节 ——
    这是浮雕常用的做法(bump / 细节转移),不是伪造深度:量级按深度自身的动态范围缩放,
    比例由 ``--detail`` 控制(0 = 完全不叠)。同样只用 numpy + PIL。
    """
    import numpy as np
    from PIL import Image

    if amount <= 0:
        return depth
    lum = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    blurred = np.asarray(Image.fromarray((lum * 255.0).astype(np.uint8))
                         .filter(ImageFilter.GaussianBlur(2.0)), dtype=np.float32) / 255.0
    high = lum - blurred
    span = float(np.percentile(depth, 99) - np.percentile(depth, 1)) or 1.0
    return depth + amount * high * span


def _sdf_radius(wanted: float, shape) -> float:
    """sdf 的倒角半径。

    **默认按分辨率自动**(0.6% 短边,最小 8px):实测同一张 2749x3684 的插画,
    半径 8px 只剩轮廓线、24px 才是能雕的圆浮雕 —— 固定像素值在大图上必然偏细,
    而图案的笔画宽度本来就随贴图分辨率一起变大。
    """
    height, width = shape[0], shape[1]
    auto = max(8.0, round(min(height, width) * 0.006, 1))
    if float(wanted or 0) > 0:
        return float(wanted)
    return auto


def _smoothstep(x):
    import numpy as np
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


# --------------------------------------------------------------------------- #
# 杂项
# --------------------------------------------------------------------------- #
def _pick_device(wanted: str, torch) -> str:
    if wanted != "auto":
        return wanted
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def auto_da3_res(width: int, height: int, cap: int = 1512) -> int:
    """DA3 的自动 process_res:跟输入长边走(14 的倍数),夹在 [504, cap]。

    为什么 DA3 要自动而 dav2 不要:实测 DA3 在 504 下会把**细发丝/细结构并进背景**
    (归一化后是一片片纯黑),1008 → 黑块从 16 处降到 1 处,1512 → 0 处;
    而 dav2 从 518 提到 1554 细节只涨 6%。两者短板不同,所以默认策略也不同。
    """
    return max(504, min(int(cap), int(round(max(width, height) / 14)) * 14))


def auto_size(width: int, height: int, cap: int) -> int:
    """自动推理边长:跟着贴图分辨率走(上限 cap)。

    实测:518 是模型的训练尺寸,但贴图 2749 宽时等于"只有 518 个深度采样"再放大 5 倍。
    放大到 1036/1554 细节略增(高频 3.26 → 3.45)、代价是前向变慢(0.5s → ~4.5s);
    所以**按贴图长边取 14 的倍数**,并夹在 [518, cap] 之间。
    """
    return max(518, min(int(cap), int(round(max(width, height) / 14)) * 14))


def _round14(value: int) -> int:
    """归整到 14 的倍数(DINOv2 的 patch 大小)—— 手填 `--size` 不是 14 的倍数会直接崩。"""
    return max(14, int(round(int(value) / 14)) * 14)


def _input_size(width: int, height: int, size: int) -> tuple[int, int]:
    """等比缩放到 14 的倍数(上游 Resize(ensure_multiple_of=14, lower_bound) 的等价实现)。"""
    size = _round14(size)
    if width > height:
        new_w, new_h = size, int(size * height / width / 14) * 14
    else:
        new_w, new_h = int(size * width / height / 14) * 14, size
    return max(14, new_w), max(14, new_h)


def _resize(rgb, width: int, height: int):
    import numpy as np
    from PIL import Image
    image = Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8))
    if image.size != (width, height):
        image = image.resize((width, height), Image.BICUBIC)
    return np.asarray(image, dtype=np.float32) / 255.0


def pick_engine(args) -> str:
    """``auto`` 的判定:有权重 → dav2;装了 da3 包 → da3;否则报错(别静默降级)。"""
    if args.engine != "auto":
        return args.engine
    if args.model and os.path.exists(args.model):
        return "dav2"
    try:
        import importlib.util
        if importlib.util.find_spec("depth_anything_3") is not None:
            return "da3"
    except Exception:                                                    # noqa: BLE001
        pass
    raise MissingDependency(
        "引擎是 auto,但既没找到 Depth Anything V2 的权重(--model / "
        "depthimg__model),也没装 Depth Anything 3 的包。\n"
        "二选一:\n"
        "  * 指定权重(面板上那四档 V2-S/B/L/G;只有 S 可商用):"
        "depthimg__model = ~/.zbtools/models/depth_anything_v2_vits_fp32.safetensors\n"
        "  * 或者用零依赖引擎:depthimg__engine = luma(亮度)| sdf(图案浮雕)")


ENGINES = {"dav2": run_dav2, "da3": run_da3, "luma": run_luma, "sdf": run_sdf}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="贴图 → 深度图")
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--out", dest="out", required=True)
    parser.add_argument("--engine", default="auto", choices=["auto", *ENGINES])
    parser.add_argument("--model", default="")
    parser.add_argument("--encoder", default="")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--size", type=int, default=0,
                        help="DA 系列的推理边长;0 = 自动(跟贴图分辨率走,上限 --size-max)")
    parser.add_argument("--size-max", type=int, default=1554,
                        help="自动推理边长的上限(默认 1554 = 518×3)")
    parser.add_argument("--da3-res", type=int, default=0,
                        help="DA3 的 process_res;0 = 自动(跟输入长边走,夹在 [504, 1512])。"
                             "实测 504 时它会**把细发丝并进背景**,归一化后就是一片片纯黑")
    parser.add_argument("--invert", action="store_true", help="反过来(亮=远)")
    parser.add_argument("--flip-v", action="store_true",
                        help="输出前上下翻转(ZBrush 的图像是自下而上存的,原样导入会颠倒)")
    parser.add_argument("--clip", type=float, default=0.5, help="两端各裁掉的百分位(%%)")
    parser.add_argument("--bit-depth", type=int, default=8, choices=[8, 16],
                        help="输出位深;16 = 另写一张 16 位 TIFF(做位移/置换时才需要)。"
                             "实测提高位宽对糊没有帮助,只影响台阶")
    parser.add_argument("--despike-robust", type=float, default=6.0,
                        help="去极值的判定倍数(越大越温和;默认 6,4 会在噪声图上过度替换)")
    parser.add_argument("--no-despike", action="store_true",
                        help="关掉「去孤立极值」(默认开:DA3 会在头发/边缘蹦出远深度,归一化后是纯黑色块)")
    parser.add_argument("--detail", type=float, default=-1.0,
                        help="把原图细节按比例叠加到高度上;-1 = 自动(AI 引擎 0.3,零依赖引擎 0),"
                             "0 = 不叠。AI 深度输出平滑(V2-S 尤其),浮雕靠这个才不朦胧")
    parser.add_argument("--no-guided-upsample", action="store_true",
                        help="关掉「以原图为引导」的上采样(默认开,边缘更干净)")
    parser.add_argument("--bg", default="white", choices=["white", "black", "none"])
    parser.add_argument("--sdf-radius", type=float, default=0.0,
                        help="sdf 的倒角半径(像素);0 = 自动(按图像短边的 0.6%%,最小 8px)")
    parser.add_argument("--sdf-threshold", type=float, default=0.5)
    parser.add_argument("--report-json", default="")
    args = parser.parse_args(argv)

    t0 = time.time()
    if not os.path.exists(args.source):
        log(f"[深度图] 贴图不存在:{args.source}")
        return 1
    try:
        engine = pick_engine(args)
        log(f"[深度图] 输入 {os.path.basename(args.source)}"
            f"({os.path.getsize(args.source) / 1024 / 1024:.2f} MB)→ {args.out}")
        import numpy as np
        rgb, info = load_rgb(args.source, args.bg)
        array, extra = ENGINES[engine](args, rgb, info)
        if not args.no_despike:
            array, replaced = despike(np.asarray(array, dtype=np.float32),
                                      robust=float(args.despike_robust))
            if replaced:
                log(f"[深度图] 去掉 {replaced} 个孤立极值像素"
                    f"({replaced / max(1, array.size) * 100:.2f}%)—— 不去掉的话它们会变成纯黑色块")
            extra["despiked"] = replaced
        amount = float(args.detail)
        if amount < 0:                      # -1 = 自动:AI 引擎叠一点,零依赖引擎不叠
            amount = 0.3 if engine in ("dav2", "da3") else 0.0
        if amount > 0:
            array = add_image_detail(np.asarray(array, dtype=np.float32), rgb, amount)
            log(f"[深度图] 叠加原图细节 {amount:g}"
                f"{'(自动:AI 深度太平滑,浮雕靠它才清楚)' if float(args.detail) < 0 else ''}")
            extra["detail"] = amount
        if args.flip_v:
            # 实测:ZBrush 的图像(V 轴)是自下而上存的,原样导入会**上下颠倒** —— 所以默认翻一次
            array = array[::-1]
        progress(0.92)                       # 归一化 + 写盘(16 位 TIFF 也要一会儿)
        stats = save_gray(array, args.out, args.invert, args.clip, args.bit_depth)
        report = {"ok": True, "engine": engine, "source": args.source, "out": args.out,
                  "flipped_v": bool(args.flip_v),
                  "seconds": round(time.time() - t0, 2), "interpreter": sys.executable,
                  **{k: v for k, v in info.items() if k != "alpha"}, **extra, **stats}
        if args.flip_v:
            log("[深度图] 已上下翻转(给 ZBrush 的朝向;在预览/PS 里看这张 PNG 会是倒的,正常)")
        if stats.get("high_precision_path"):
            log(f"[深度图] 另存 16 位 TIFF(位移/置换用,不会出现 8 位的台阶):"
                f"{stats['high_precision_path']}")
        log(f"[深度图] 完成:{stats['out_size'][0]}x{stats['out_size'][1]} 灰度 PNG,"
            f"用时 {report['seconds']}s;像素 {stats['out_min']}~{stats['out_max']}"
            f"(均值 {stats['out_mean']})")
    except MissingDependency as exc:
        log(f"[深度图] ✗ {exc}")
        return 2
    except Exception as exc:                                             # noqa: BLE001
        import traceback
        log(f"[深度图] ✗ {type(exc).__name__}: {exc}")
        log(traceback.format_exc())
        return 1

    if args.report_json:
        try:
            with open(args.report_json, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            log(f"[深度图] 写报告失败(不影响结果):{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
