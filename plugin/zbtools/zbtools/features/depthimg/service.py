"""图片转深度图(depthimg)的业务逻辑:读模型贴图 → 外部算深度 → 送回 ZBrush。

流程(和「贴图转色」的①②**完全无关**,这里只是一件独立的事):

    选中一个模型
      → 导出当前 SubTool(ZBrush 会顺手把它的贴图写在旁边)= "读出选中模型的贴图"
      → 外部管线转成灰度深度图(tools/depth_pipeline.py)
      → 导入 ZBrush 的 Texture 面板
      → 替换模型贴图(默认):把它挂到当前 SubTool 上;开关关掉就只追加到 Texture 面板

拿到这张深度图之后怎么用由用户决定(当 Alpha / 遮罩 / 置换 / 贴图都行),插件不插手。

**许可**(判据见 :func:`weight_license`):
  * Depth Anything V2:**Small** = Apache-2.0(可商用);Base/Large/Giant = CC-BY-NC-4.0
  * Depth Anything 3 的 Apache-2.0 变体(DA3MONO-LARGE / SMALL / BASE / METRIC-LARGE)
  * luma(亮度当高度)/ sdf(距离场浮雕)—— 自己写的,零依赖
  非商用权重默认**拒绝**(``allow_nc_weights`` 可以显式放开,只给自己用)。

**面板 ↔ 配置**:面板上那四格(V2-S / V2-B / V2-L / V2-G)选的是 DA V2 的**档位**
(写 ``engine=dav2`` + ``variant``);``engine`` 写成 ``da3`` / ``luma`` / ``sdf`` 时
面板四格全灭 —— 那三个引擎不在面板上,但照样能用。既然面板选的是规模,
"规模对不上"就必须**报错**而不是凑合(见 :func:`variant_note`)。
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

import zbrush.commands as zbc

from ... import proc, settings, ui
from ...errors import PluginError
from ...zbapi import toolio

#: 本功能的配置键前缀
PREFIX = "depthimg__"

#: 外部管线脚本所在目录(与插件零耦合:只吃 argv、只打 stdout)
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")

#: 自动找权重时看的目录(按顺序)
_MODEL_GLOBS = ("~/.zbtools/models", "~/ComfyUI-Shared/models/depthanything",
                "~/ComfyUI/models/depthanything",
                "~/Documents/ComfyUI/models/depthanything")

#: 可能装了 torch / depth_anything_3 的解释器候选
_PYTHON_GLOBS = ("~/.venvs/da3/bin/python", "~/.venvs/da3/bin/python3")

#: 找 ComfyUI 根目录的候选(为了复用它的 venv 和已下载的权重)
_COMFY_GLOBS = ("~/ComfyUI", "~/ComfyUI-Installs/*/ComfyUI", "~/ComfyUI-Installs/*",
                "~/Documents/ComfyUI", "~/ComfyUI*", "~/*/ComfyUI")

#: 权重文件名里出现这些 → **不可商用**(CC-BY-NC-4.0)
_NC_WEIGHT_MARKERS = ("vitb", "vitl", "vitg", "metric")
#: DA3 仓库名里出现这些 → **不可商用**(DA3-LARGE / Giant / Nested 系列是 CC-BY-NC-4.0)
_NC_REPO_MARKERS = ("da3-large", "large-1.1", "giant", "nested")

#: DA V2 的四个规模 —— **面板上那四格就是它**(顺序 = 面板顺序:快 → 准)。
#: 面板上不放 fp16 与 metric,理由见 features/depthimg/__init__.py 的 VARIANT_ROWS 注释。
VARIANTS: tuple[str, ...] = ("vits", "vitb", "vitl", "vitg")
#: 规模 → 说法(诊断 / 报错信息里用;面板标签是 V2-S/V2-B/V2-L/V2-G)
VARIANT_TITLES: dict[str, str] = {
    "vits": "V2-S(Small,Apache-2.0)",
    "vitb": "V2-B(Base,CC-BY-NC-4.0)",
    "vitl": "V2-L(Large,CC-BY-NC-4.0)",
    "vitg": "V2-G(Giant,CC-BY-NC-4.0)",
}
#: 规模 → 面板/ComfyUI 里那张 **fp32** 权重的文件名(报错时给照抄的下载命令)。
#: 用 fp32 不用 fp16:上游(Kijai)自己写着 "fp16 reduces quality by a LOT, not recommended"。
#: 四张都在同一批 safetensors 里(项目实测同源),其中 Base 只有这批有。
VARIANT_FILES: dict[str, str] = {
    "vits": "depth_anything_v2_vits_fp32.safetensors",
    "vitb": "depth_anything_v2_vitb_fp32.safetensors",
    "vitl": "depth_anything_v2_vitl_fp32.safetensors",
    "vitg": "depth_anything_v2_vitg_fp32.safetensors",
}

DEFAULTS: dict[str, Any] = {
    # 引擎:dav2 = Depth Anything V2(Apache-2.0 的 Small)
    #       da3  = Depth Anything 3(Apache-2.0 的变体,面板开关切)
    #       luma = 亮度当高度(零依赖,贴图本身就是高度图时用)
    #       sdf  = 距离场圆润浮雕(零依赖,平铺图案/贴花这类没有真实景深的图最对路)
    "engine": "dav2",
    "replace_texture": True,     # 面板开关:把导入的深度图挂到当前 SubTool(= 替换它的贴图)
    # UV 翻转(**默认开**):ZBrush 的图像是自下而上存的,原样送进去上下颠倒(实测)——
    # 所以输出前翻一次。代价:在预览/PS 里看这张 PNG 是倒的。
    "flip_v": True,
    # 读贴图的**快路径**:直接用 Texture:Export 导出"面板当前选中的贴图",不导出网格。
    # 快得多(大模型不用等导出),但它假设"选中 SubTool 时 Texture 面板会跟着切到它挂的那张"
    # —— 这一点得先用 scripts/probe_texture_paths.py 在真机上确认,所以默认关。
    "use_texture_export": False,
    # 读贴图前会先把 Texture 面板的"当前贴图"挪到**这个模型自己那张**上(见 focus_model_texture)
    # —— 万一你这台机器上这一步判断失灵、而老路子本来是对的,可以打开它跳过(只打警告)。
    "skip_texture_focus": False,
    "interpreter": "",           # 跑深度管线的解释器;空 = 自动挑一个**有 torch** 的
    "model": "",                 # dav2 → safetensors 路径;da3 → 仓库 id 或本地目录
    # DA V2 用哪一档规模(**面板上那四格写的就是它**):vits / vitb / vitl / vitg。
    # 四档里只有 vits 是 Apache-2.0(可商用),其余三档要 allow_nc_weights 才放行。
    "variant": "vits",
    "allow_nc_weights": False,   # 放开 CC-BY-NC 权重(vitb/vitl/vitg、DA3-LARGE 等)只自用
    "invert": False,             # DA 系列默认"近处亮";想反过来(亮=远)就开
    "clip": 0.5,                 # 归一化时两端各裁掉的百分位(防一个极值像素压平整张图)
    # dav2 的推理边长。**默认 518 = 模型训练尺寸**。
    # 实测(用户那张 854x1280 的贴图,同一输入三档对比):
    #   518 → 背景噪声 3.56 / 轮廓毛刺 11.15
    #   自动(1274)→ 背景噪声 4.66 / 轮廓毛刺 11.79
    # → **Small 模型提高分辨率只是把噪声一起放大**,所以默认留在 518(想试就写 0 = 自动)。
    "size": 518,
    "size_max": 1554,            # size=0(自动)时的上限
    # 输出位深:8 = 只写 8 位 PNG(够用、不占地);16 = 另写一张 16 位 TIFF(做位移/置换时开)。
    # 实测:提升位宽对"糊"没有帮助,只影响台阶 —— 所以默认关,需要时一行配置打开。
    "bit_depth": 8,
    # da3 的 process_res;**0 = 自动**(跟输入长边走,夹在 [504,1512])。
    # 实测 504 下它会把细发丝并进背景 → 归一化后一片片纯黑(16 处);1008 → 1 处;1512 → 0 处。
    "da3_res": 0,
    # 叠加原图细节的比例;**0 = 不叠(默认)**。
    # ⚠ 实测修正:这对**平涂线稿**有用(局部对比度 +28%),但对**带明暗的图/3D 渲染**有害 ——
    # 那里的"高频"是光照和贴图噪声,不是高度,叠上去就是一层麻点(用户实测:明显劣化)。
    # 想要线条更清楚时,只对线稿开:0.1~0.3。
    "detail": 0.0,
    # 去孤立极值(DA3 会在头发/边缘蹦出远深度 → 纯黑色块);默认开但**温和**
    # (阈值 6:只换真正的极端离群点。4 太激进 —— dav2 上会替换掉 0.74% 的像素,表面发麻)
    "despike": True,
    "despike_robust": 6.0,
    "bg": "white",               # 输入带 alpha 时先合成到这个底色(white|black|none)
    # sdf 引擎的倒角半径(像素);**0 = 自动**(按贴图短边的 0.6%,最小 8px)。
    # 实测:同一张 2749x3684 的插画,8px 只剩轮廓线、24px 才是能雕的圆浮雕 ——
    # 固定像素值在大图上必然偏细,所以默认交给分辨率算。
    "sdf_radius": 0.0,
    "timeout_s": 900.0,          # 深度管线超时
    "io_wait_s": 300.0,          # 等 ZBrush 导出当前 SubTool(大网格要久一点)
    "import_wait_s": 2.0,        # 贴图导入/挂载后最多在脚本里等多久
    "comfy_dir": "",             # ComfyUI 根目录;空 = 自动找
    "work_dir": "",              # 工作目录(持久化,跨重启复用)
    "last_depth": "",            # 上次生成的深度图(诊断里显示)
    "last_report": "",           # 上次的摘要
    "last_assign": "",           # 上次"挂贴图"的结果(用了哪个界面项)
}

_WORK_DIR: str | None = None
#: 解释器自检结果缓存(每次按按钮都开子进程问一遍太浪费)
_MODULE_CACHE: dict[tuple[str, str], bool] = {}


def _cfg(cfg: dict[str, Any], key: str) -> Any:
    value = cfg.get(PREFIX + key)
    return DEFAULTS[key] if value is None else value


def _save(cfg: dict[str, Any], key: str, value: Any) -> None:
    cfg[PREFIX + key] = value
    settings.save(cfg)


# --------------------------------------------------------------------------- #
# 给面板用的读写入口
# --------------------------------------------------------------------------- #
def param(cfg: dict[str, Any], key: str) -> Any:
    return _cfg(cfg, key)


def set_param(cfg: dict[str, Any], key: str, value: Any) -> None:
    _save(cfg, key, value)


def engine(cfg: dict[str, Any]) -> str:
    """当前引擎(空/未知值回落到 dav2 —— 别让一个错字变成"什么都跑不了")。"""
    name = str(_cfg(cfg, "engine") or "").strip().lower()
    return name if name in ("dav2", "da3", "luma", "sdf") else "dav2"


def variant(cfg: dict[str, Any]) -> str:
    """当前 DA V2 档位(空/未知值回落到 vits)。

    面板上那四格(V2-S/V2-B/V2-L/V2-G)点一下就是写这个键;引擎不是 dav2 时它照样有值
    (记录"上次用的哪一档"),只是面板四格会全灭。
    """
    name = str(_cfg(cfg, "variant") or "").strip().lower()
    return name if name in VARIANTS else "vits"


def set_variant(cfg: dict[str, Any], name: str) -> None:
    """把引擎切到 dav2 的那一档(**一次落盘**:engine + variant 必须同时对上)。

    面板点一格 = 用 Depth Anything V2 的这一档;所以引擎也得跟着切回 dav2 ——
    否则会出现"引擎是 da3、档位是 vitg"这种界面和配置对不上的状态。
    """
    cfg[PREFIX + "engine"] = "dav2"
    cfg[PREFIX + "variant"] = name if name in VARIANTS else VARIANTS[0]
    settings.save(cfg)


def variant_of_weight(path: str) -> str:
    """从权重文件名认出规模(``…_vitl_fp32.safetensors`` → ``vitl``);认不出返回空串。

    ⚠ 用 ``_vitl_`` 这种带下划线的形式匹配,别用裸子串:``metric_hypersim_vitl_fp32``
    里也有 ``vitl``,但那不是"面板上的 V2-L"(见 is_metric_weight)。
    """
    low = os.path.basename(path).lower()
    for name in VARIANTS:
        if f"_{name}_" in low:
            return name
    return ""


def is_metric_weight(path: str) -> bool:
    """是不是**米制(metric)**权重(按固定域标定:Hypersim 室内 20m / VKITTI 室外 80m)。

    面板上四格要的是通用**相对深度**,所以 metric 权重一律不参与自动挑选:
    自动挑中它 = 静默按别的域算米数,用户只会看到"深度图怪怪的"(踩过的那类隐蔽错误)。
    要显式用它,只能通过 ``depthimg__model`` 指全路径。
    """
    return "metric" in os.path.basename(path).lower()


def work_dir(cfg: dict[str, Any] | None = None) -> str:
    """工作目录(导出的模型/贴图、深度图都放这里)。

    ⚠ 必须**持久化**:系统 temp 会被清理,而配置里记着上次的路径;目录没了就重建一个,
    免得出现"上次生成的那张图找不到了"。
    """
    global _WORK_DIR
    if _WORK_DIR is None or not os.path.isdir(_WORK_DIR):
        saved = str(_cfg(cfg, "work_dir")).strip() if cfg is not None else ""
        if saved and os.path.isdir(saved):
            _WORK_DIR = saved
            print(f"[图片转深度图] 复用上次的工作目录:{saved}")
        else:
            _WORK_DIR = tempfile.mkdtemp(prefix="zbtools_depthimg_")
            if cfg is not None:
                _save(cfg, "work_dir", _WORK_DIR)
    return _WORK_DIR


# --------------------------------------------------------------------------- #
# 解释器(深度引擎要 torch,而 ZBrush 自带的 Python 里没有)
# --------------------------------------------------------------------------- #
def has_module(python: str, module: str) -> bool:
    """某个解释器里能不能 import 这个模块(缓存)。"""
    key = (python, module)
    if key not in _MODULE_CACHE:
        code = ("import importlib.util as u\n"
                f"print(1 if u.find_spec({module!r}) is not None else 0)\n")
        try:
            done = subprocess.run([python, "-c", code], capture_output=True, text=True,
                                  timeout=60)
            _MODULE_CACHE[key] = (done.stdout or "").strip() == "1"
        except Exception:                                                # noqa: BLE001
            _MODULE_CACHE[key] = False
    return _MODULE_CACHE[key]


def find_comfy(cfg: dict[str, Any]) -> str:
    """ComfyUI 根目录;找不到返回空串。

    为什么要找它:很多机器上 torch 只装在 ComfyUI 的 venv 里,那儿还常常躺着下载好的
    DA V2 权重 —— 复用它们 = 不用装任何东西。配置 ``depthimg__comfy_dir`` 可显式指定。
    """
    explicit = str(_cfg(cfg, "comfy_dir") or "").strip()
    if explicit and os.path.isdir(explicit):
        return explicit
    seen: list[str] = []
    for pattern in _COMFY_GLOBS:
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            if path in seen or not os.path.isdir(path):
                continue
            seen.append(path)
            if (os.path.isdir(os.path.join(path, "models", "depthanything"))
                    or os.path.exists(os.path.join(path, ".venv", "bin", "python"))):
                return path
    return ""


def _comfy_python(root: str) -> str:
    """ComfyUI 自带的解释器(standalone 版是 .venv → standalone-env 的软链)。"""
    for rel in (".venv/bin/python", "standalone-env/bin/python", "venv/bin/python"):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return path
    return ""


def interpreter(cfg: dict[str, Any]) -> str:
    """插件层配置的解释器(缺省回落到当前解释器 = ZBrush 自带的那个)。"""
    explicit = str(_cfg(cfg, "interpreter") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    plugin_level = str(cfg.get("interpreter") or "").strip()
    if plugin_level and os.path.exists(plugin_level):
        return plugin_level
    return sys.executable


def depth_interpreter(cfg: dict[str, Any]) -> str:
    """跑深度管线的解释器。

    规则:显式配置 → 插件解释器(**前提是它有需要的包**)→ 按引擎找候选 → 插件解释器(兜底)。

    为什么要"按引擎找":`dav2` 只要有 torch 就行(ComfyUI 的 venv 正好有),
    而 `da3` 还要 `depth_anything_3` 包(一般装在**另一个** venv 里)—— 引擎一换,
    该用的解释器就跟着换;这里自动配对,省得你改了两个键还改错一个。
    """
    explicit = str(_cfg(cfg, "interpreter") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    name = engine(cfg)
    needed = ("torch", "depth_anything_3") if name == "da3" else ("torch",)
    own = interpreter(cfg)
    root = find_comfy(cfg)
    comfy = _comfy_python(root) if root else ""
    others = [os.path.expanduser(pattern) for pattern in _PYTHON_GLOBS]
    candidates = ([own, *others, comfy] if name == "da3" else [own, comfy, *others])
    for path in candidates:
        if path and os.path.exists(path) and all(has_module(path, m) for m in needed):
            return path
    return own


# --------------------------------------------------------------------------- #
# 引擎与权重(只留可商用的)
# --------------------------------------------------------------------------- #
def weight_license(model: str, name: str = "") -> tuple[bool, str]:
    """这个权重/仓库能不能**商用**。返回 (可商用, 说明)。

    依据(官方 README):
      * Depth Anything V2:Small = Apache-2.0;Base/Large/Giant = CC-BY-NC-4.0
      * Depth Anything 3:SMALL / BASE / METRIC-LARGE / MONO-LARGE = Apache-2.0;
        LARGE / LARGE-1.1 / GIANT / NESTED 系列 = CC-BY-NC-4.0
    """
    if name == "da3":
        low = model.lower()
        for marker in _NC_REPO_MARKERS:
            if marker in low:
                return False, f"DA3 的 {model} 是 CC-BY-NC-4.0(不可商用)"
        return True, f"DA3 的 {model}(Apache-2.0)"
    low = os.path.basename(model).lower()
    for marker in _NC_WEIGHT_MARKERS:
        if marker in low:
            return False, f"{os.path.basename(model)} 属于 CC-BY-NC-4.0 的规模(不可商用)"
    if "vits" in low:
        return True, "Depth Anything V2 Small(Apache-2.0)"
    return True, f"{os.path.basename(model)}(未能判定,按文件名看不是 NC 规模)"


def find_model(cfg: dict[str, Any]) -> str:
    """自动找 dav2 的 safetensors 权重(找不到返回空串)。

    规则(顺序就是优先级):
      1. ``model`` 显式指了全路径 → 就用它(用户说了算);
      2. 按面板上选的**档位**(``variant``)找那一档;同名 fp16/fp32 都在时挑 **fp32**;
      3. 那一档没有 → 返回空串,**绝不拿别的规模凑合**;由 :func:`run_depth` 报
         "缺哪一档"或"这一档被许可挡住了"(旧逻辑的静默降级已删);
      4. NC 权重(vitb/vitl/vitg)默认跳过,``allow_nc_weights`` 打开才参与;
      5. **metric 权重一个都不自动挑**(见 :func:`is_metric_weight`)。
    """
    explicit = str(_cfg(cfg, "model") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    allow_nc = bool(_cfg(cfg, "allow_nc_weights"))
    directories = [os.path.expanduser(p) for p in _MODEL_GLOBS]
    root = find_comfy(cfg)
    if root:
        directories.insert(0, os.path.join(root, "models", "depthanything"))
    directories += [work_dir(cfg), _TOOLS_DIR]
    found: list[str] = []
    for directory in directories:
        if directory and os.path.isdir(directory):
            found += sorted(glob.glob(os.path.join(directory, "depth_anything_v2_*.safetensors")))
    if not found:
        return ""
    usable = [p for p in found if weight_license(p)[0] or allow_nc]
    # ⚠ **metric 一个都不自动挑**:它是按固定域标定的米制权重(Hypersim 室内 20m /
    #   VKITTI 室外 80m),而面板上那四格要的是通用相对深度 —— 自动挑中它 = 静默按别的
    #   域算(用户只会看到"深度图怪怪的")。要显式用只能走 depthimg__model。
    plain = [p for p in usable if not is_metric_weight(p)]
    # ⚠ fp16 要排在 fp32 后面:Kijai 自己写着 "fp16 reduces quality by a LOT, not recommended",
    #   而文件名排序里 fp16 恰好排在 fp32 前面 —— 不显式排一下就会悄悄用上 fp16(实测踩到)。
    fp32 = [p for p in plain if "fp32" in os.path.basename(p).lower()]
    ordered = (fp32 or plain) + [p for p in plain if p not in fp32]
    want = variant(cfg)
    for path in ordered:
        if variant_of_weight(path) == want:
            return path
    return ""


def variant_weight_on_disk(cfg: dict[str, Any], want: str) -> str:
    """不管许可,找磁盘上属于 ``want`` 那一档的权重。

    用来区分两种失败:权重**根本没下** vs 权重下了但它是 CC-BY-NC(默认不放行)。
    第二种如果直接报"没找到",会让人以为磁盘上没有 —— 实际是许可挡住了。
    """
    directories = [os.path.expanduser(p) for p in _MODEL_GLOBS]
    root = find_comfy(cfg)
    if root:
        directories.insert(0, os.path.join(root, "models", "depthanything"))
    directories += [work_dir(cfg), _TOOLS_DIR]
    for directory in directories:
        if directory and os.path.isdir(directory):
            for path in sorted(glob.glob(os.path.join(
                    directory, "depth_anything_v2_*.safetensors"))):
                if variant_of_weight(path) == want and not is_metric_weight(path):
                    return path
    return ""


def download_hint(name: str) -> str:
    """某一档权重"怎么拿到它"(照抄即用的命令 + 放哪儿)。"""
    filename = VARIANT_FILES.get(name, VARIANT_FILES[VARIANTS[0]])
    return (f"下载 {VARIANT_TITLES.get(name, name)} 的权重(fp32,别用 fp16):\n"
            f"  python -m pip install huggingface_hub\n"
            f"  python -c \"from huggingface_hub import hf_hub_download as d;"
            f" print(d('Kijai/DepthAnythingV2-safetensors', '{filename}'))\"\n"
            f"把它放到 {os.path.expanduser(_MODEL_GLOBS[0])}/"
            f"(或 ComfyUI 的 models/depthanything/),也可以用 depthimg__model 指到全路径。")


def variant_note(cfg: dict[str, Any], model: str) -> str:
    """"面板选的档位"和"实际要用的权重"对不上时给一句话;没问题返回空串。

    ⚠ 为什么必须查这个:面板上那四格是**选规模**的。要是点了 V2-G 而磁盘上只有 Small,
    旧逻辑会静默用 Small —— 用户看到的就是"换了档没反应/深度图没区别",而真因是权重没下。
    这正是这个功能踩过的那类隐蔽错误(见 README「实测结论」),所以现在宁可报错。
    """
    if not model or engine(cfg) != "dav2":
        return ""
    want = variant(cfg)
    got = variant_of_weight(model)
    if not got or got == want:
        return ""
    return (f"当前档位是 {VARIANT_TITLES[want]},但磁盘上找到/要用的是 "
            f"{os.path.basename(model)}({VARIANT_TITLES.get(got, got)})")


def nc_weights_found(cfg: dict[str, Any]) -> str:
    """只找到不可商用的权重时,把它们列出来(给错误信息用)。"""
    hits: list[str] = []
    for directory in [os.path.expanduser(p) for p in _MODEL_GLOBS]:
        if os.path.isdir(directory):
            hits += [os.path.basename(p) for p in
                     sorted(glob.glob(os.path.join(directory,
                                                   "depth_anything_v2_*.safetensors")))
                     if not weight_license(p)[0]]
    root = find_comfy(cfg)
    if root:
        folder = os.path.join(root, "models", "depthanything")
        if os.path.isdir(folder):
            hits += [os.path.basename(p) for p in
                     sorted(glob.glob(os.path.join(folder,
                                                   "depth_anything_v2_*.safetensors")))
                     if not weight_license(p)[0]]
    return "、".join(dict.fromkeys(hits))


def model_and_license(cfg: dict[str, Any]) -> tuple[str, bool, str]:
    """按当前引擎给出 (要用的权重/仓库 id, 可商用, 说明)。"""
    name = engine(cfg)
    if name in ("luma", "sdf"):
        # 零依赖引擎不碰模型:别在日志里打一个"用不到"的权重名,那会让人以为模型跑了
        return "", True, "用不到权重(亮度 / 距离场)"
    if name == "da3":
        repo = str(_cfg(cfg, "model") or "").strip() or "depth-anything/DA3MONO-LARGE"
        ok, note = weight_license(repo, "da3")
        return repo, ok, note
    model = find_model(cfg)
    if not model:
        return "", True, "(没找到权重)"
    ok, note = weight_license(model)
    if "fp16" in os.path.basename(model).lower():
        note += "  ⚠ 这是 fp16 权重(上游作者说「质量掉得很多,不推荐」),建议换 fp32 那张"
    return model, ok, note


def depth_pipeline_path() -> str:
    """深度管线的路径:开发态是 .py,发行包里两个都找(.py 优先 —— 见 package_plugin)。"""
    base = os.path.join(_TOOLS_DIR, "depth_pipeline")
    for ext in (".py", ".pyc"):
        if os.path.exists(base + ext):
            return base + ext
    return base + ".py"


def depth_args(cfg: dict[str, Any], source: str, out: str,
               report_json: str) -> list[str]:
    """拼出深度管线的命令行(单独抽出来,便于测试与诊断打印)。"""
    name = engine(cfg)
    args = [depth_interpreter(cfg), depth_pipeline_path(),
            "--in", source, "--out", out,
            "--engine", name, "--device", "auto",
            "--size", str(int(_cfg(cfg, "size"))),
            "--size-max", str(int(_cfg(cfg, "size_max"))),
            "--bit-depth", str(int(_cfg(cfg, "bit_depth"))),
            "--detail", f"{float(_cfg(cfg, 'detail')):g}",
            "--despike-robust", f"{float(_cfg(cfg, 'despike_robust')):g}",
            "--da3-res", str(int(_cfg(cfg, "da3_res"))),
            "--clip", f"{float(_cfg(cfg, 'clip')):g}",
            "--bg", str(_cfg(cfg, "bg")),
            "--sdf-radius", f"{float(_cfg(cfg, 'sdf_radius')):g}",
            "--report-json", report_json]
    if name in ("dav2", "da3"):
        # 只有 AI 引擎才塞权重:luma/sdf 是零依赖引擎,塞进去只会让人以为"用了模型"
        model, _ok, _note = model_and_license(cfg)
        if model:
            args += ["--model", model]
    if _cfg(cfg, "invert"):
        args.append("--invert")
    if _cfg(cfg, "flip_v"):
        args.append("--flip-v")          # 默认开:给 ZBrush 的朝向
    if not _cfg(cfg, "despike"):
        args.append("--no-despike")
    return args


#: 深度管线的日志片段 → (进度条显示文字, 在本段进度里的位置)。
#: 顺序就是阶段顺序;两档之间由 ``Progress.creep`` 按时间慢慢爬(一次推理十几秒没有输出,
#: 不爬的话条会僵在那儿)。**只认这些片段**,别去猜别的输出。
#:
#: ⚠ 这里的数字要跟 ``depth_pipeline.py`` 里 ``progress()`` 报的里程碑**对齐**
#:   (0.15 / 0.60 / 0.78 / 0.92):否则"新管线报真进度 + 阶段表又跳一次"会互相打架。
#:   实测踩到过:阶段表写 0.62、管线报 0.78 → 进度条倒退(``Progress.stage`` 现在会兜住,
#:   但根子上还是要对齐)。
_DEPTH_STAGES: tuple[tuple[str, str, float], ...] = (
    ("权重加载", "推理中(这一步最慢)…", 0.15),
    ("放大回原尺寸", "后处理(放大/去极值)…", 0.78),
    ("完成:", "写盘完成", 0.92),
)


def run_depth(cfg: dict[str, Any], source: str, out: str,
              progress: "ui.Progress | None" = None,
              base: float = 0.0, span: float = 1.0) -> dict[str, Any]:
    """调外部管线把 ``source`` 转成深度图 ``out``;返回管线的 JSON 报告。

    **边跑边喂进度条**:原来用 ``subprocess.run`` 是阻塞的(拿不到中间输出),现在用
    ``proc.stream`` 逐行读 —— 管线日志映射成阶段跳变,阶段之间(最典型:一次推理十几秒
    一行日志都没有)按时间**爬升**但不越过下一个里程碑。进度条为什么能这么用,见
    ``ui.Progress`` 的实测依据。``base`` / ``span`` 让调用方把这段映射到整条进度的哪一段。
    """
    name = engine(cfg)
    model, commercial, note = model_and_license(cfg)
    if name in ("dav2", "da3") and not commercial:
        if not bool(_cfg(cfg, "allow_nc_weights")):
            raise PluginError(
                f"这一档权重不能商用:{note}\n"
                f"可商用的选择:\n"
                f"  * 面板上点 **V2-S**(Depth Anything V2 Small,Apache-2.0,95 MB)\n"
                f"  * Depth Anything 3 的 Apache-2.0 变体(默认 DA3MONO-LARGE)\n"
                f"只给自己用、不在意商用的话:把 settings.json 里的 "
                f"depthimg__allow_nc_weights 设成 true"
                f"(那之后日志每次都会提醒「不可商用」)")
        print(f"[图片转深度图] ⚠ 用的是**不可商用**的权重({note});"
              f"allow_nc_weights 开着,请只用于非商用场合")
    if name == "dav2":
        want = variant(cfg)
        if not model:
            blocked = variant_weight_on_disk(cfg, want)
            if blocked and not bool(_cfg(cfg, "allow_nc_weights")):
                raise PluginError(
                    f"面板选的是 {VARIANT_TITLES[want]},磁盘上也有 "
                    f"{os.path.basename(blocked)},但它属于 CC-BY-NC-4.0(不可商用),"
                    f"插件默认不放行。\n"
                    f"  * 自己用、不在意商用:把 ~/.zbtools/settings.json 的 "
                    f"depthimg__allow_nc_weights 设成 true,重启 ZBrush\n"
                    f"  * 要可商用:点面板上的 **V2-S**(Apache-2.0,95 MB;"
                    f"磁盘上已有就不用下)")
            raise PluginError(
                f"没找到 {VARIANT_TITLES[want]} 的权重。\n"
                f"找过这些目录:{', '.join(_MODEL_GLOBS)} 和 ComfyUI 的 models/depthanything\n"
                + (f"找到的都不合用:{nc_weights_found(cfg)}\n" if nc_weights_found(cfg) else "")
                + download_hint(want))
        mismatch = variant_note(cfg, model)
        if mismatch:
            if str(_cfg(cfg, "model") or "").strip():
                # 显式指了全路径 = 用户说了算,只提醒一次,别把人拦住
                print(f"[图片转深度图] ⚠ {mismatch};depthimg__model 是显式指定的,按它走"
                      f"(想用面板上那一档就把这个键清空)")
            else:
                raise PluginError(
                    f"{mismatch}。\n"
                    f"面板上四格选的是**规模**,所以不会拿别的规模凑合"
                    f"(否则就是「换了档没反应」)。\n" + download_hint(want))

    report_json = out + ".report.json"
    for path in (report_json, out):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    args = depth_args(cfg, source, out, report_json)
    print(f"[图片转深度图] 引擎 {name}({note});生成深度图:{' '.join(args)}")
    prog = progress if progress is not None else ui.Progress("图片转深度图", eta=25.0)
    stops = [base + span * at for _m, _t, at in _DEPTH_STAGES]

    def on_line(line: str) -> None:
        pct = ui.parse_progress(line)
        if pct is not None:
            # 管线自己报的真进度(0~1 映射到本段)
            prog.stage("深度管线运行中", base + span * pct)
            return
        print(f"[图片转深度图]   {line}")
        for idx, (marker, text, _at) in enumerate(_DEPTH_STAGES):
            if marker in line:
                prog.stage(text, stops[idx])
                nxt = stops[idx + 1] if idx + 1 < len(stops) else base + span
                prog.set_ceiling(max(stops[idx], nxt - span * 0.02))
                break

    prog.stage(f"启动深度管线({name})…", base + span * 0.02)
    prog.set_ceiling(base + span * 0.13)
    try:
        code, lines, timed_out = proc.stream(
            proc.unbuffered(args), float(_cfg(cfg, "timeout_s")),
            on_line=on_line, on_tick=prog.creep)
    except OSError as exc:
        raise PluginError(f"启动深度管线失败:{exc}\n解释器:{depth_interpreter(cfg)}") from exc

    if timed_out:
        raise PluginError(
            f"生成深度图超时(超过 {float(_cfg(cfg, 'timeout_s')):.0f} 秒)。\n"
            f"可改配置 {PREFIX}timeout_s;大模型第一次跑要先把权重读进内存\n"
            f"已经收到的最后几行:\n{proc.tail(lines, 6)}")
    if code != 0 or not os.path.exists(out):
        raise PluginError(f"生成深度图失败(返回码 {code}):\n{proc.tail(lines)}")

    report: dict[str, Any] = {}
    try:
        with open(report_json, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception as exc:                                             # noqa: BLE001
        print(f"[图片转深度图] 读深度报告失败({exc}),只按 stdout 判断成功")
    _save(cfg, "last_depth", out)
    _save(cfg, "last_report", summarize(report))
    return report


def summarize(report: dict[str, Any]) -> str:
    """把深度报告压成一行(诊断信息/控制台用)。"""
    if not report:
        return "?"
    out_size = report.get("out_size") or [0, 0]
    return (f"{report.get('engine', '?')} / {os.path.basename(str(report.get('model') or '?'))}"
            f" / {report.get('device', '?')} / {out_size[0]}x{out_size[1]}"
            f" / {report.get('seconds', '?')}s"
            f" / 像素 {report.get('out_min', '?')}~{report.get('out_max', '?')}"
            f"(均值 {report.get('out_mean', '?')})"
            + (" / 反相" if report.get("inverted") else ""))


# --------------------------------------------------------------------------- #
# ZBrush 侧:读模型贴图 → 送回 ZBrush
# --------------------------------------------------------------------------- #
#: 导出当前 SubTool 时用的固定文件名(固定 → 每次覆盖,不会拿到上一次的残留)
_EXPORT_OBJ = "depthimg_model.obj"


def _clean_previous(work: str) -> None:
    """清掉上一次导出的模型/贴图/mtl。

    ⚠ 这一步很重要:文件名是固定的,而 ZBrush **只在这个模型有 UV + 挂贴图时才写出贴图**
    —— 如果不清,换了模型后 ``find_texture`` 可能捡到**上一次那张图**,于是"深度图看着成功了,
    其实是别的模型的贴图"(隐蔽错误)。
    """
    base = os.path.join(work, os.path.splitext(_EXPORT_OBJ)[0])
    for suffix in (".obj", ".mtl", ".bmp", ".tif", ".png", ".jpg",
                   "_TXTR.tif", "_TXTR.bmp", "_TXTR.png"):
        path = base + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:                                        # noqa: BLE001
                print(f"[图片转深度图] 清理 {path} 失败:{exc}")


def slot_info(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """当前 SubTool 挂的贴图信息(名字/尺寸);读不到返回 {}。

    实测:``get_title`` 给贴图名、``get_info`` 的 tooltip 里带 ``Width=/Height=/Depth=``
    —— 所以**不用导出网格**就知道"挂没挂、挂的是哪张、多大"。
    """
    return toolio.texture_slot_info()


def has_texture(cfg: dict[str, Any] | None = None) -> bool | None:
    """当前 SubTool 挂没挂贴图:True / False,读不到时 **None**。

    * 读到了尺寸(``Width=``)且 > 0 → True
    * 读得到 tooltip 但没有尺寸 → False(明确没挂)
    * 连 tooltip 都读不到 → None —— 调用方**不要**把它当 False
      (踩过的坑:凭"读不到"或两个无意义的 -1 相等就下结论)

    ⚠ 别用"贴图方块的值":实测它**永远是 0.0**,没有信息量。
    """
    info = slot_info(cfg)
    if not info:
        return None
    if int(info.get("width") or 0) > 0:
        return True
    if str(info.get("raw") or "").strip():
        return False
    return None


def read_model_texture(cfg: dict[str, Any],
                       on_tick: "Callable[[], None] | None" = None) -> tuple[str, str]:
    """读出**当前选中模型**的贴图,**并核对读出来的确实是这个模型的**。

    换模型后拿到上一个模型的图,是实测踩到的 bug;根因有两个,这里都堵上了:

    * ZBrush 的导出是**命令队列**、而且写的是固定文件名 → 残留文件可能被当成本次结果
      (先清干净 + 查"文件是不是刚写出来的")
    * 官方文档 Tool > Texture Map 写明:"It is necessary to **clone** a texture like this
      if you wish to **export** it from ZBrush" —— 模型挂的贴图如果不在 Texture 面板里
      (没 Clone 过),ZBrush 导出来的可能是**面板里那张别的贴图**(于是深度图是上一个模型的)

    所以流程是:验明正身(点数 + 贴图尺寸)→ 导出 → 核对点数 → 核对 UV → 核对贴图是不是
    新的、尺寸对不对;任何一条对不上就**报错停下**(不猜、不将就)。
    """
    work = work_dir(cfg)
    _clean_previous(work)

    if bool(_cfg(cfg, "use_texture_export")):
        fast = toolio.export_current_texture(os.path.join(work, "depthimg_tex"))
        if fast:
            ok, why = _check_texture(fast, t0=0.0, slot=toolio.texture_slot_info())
            if ok:
                print(f"[图片转深度图] 快路径:Texture:Export 导出当前贴图 → {fast}")
                return "", fast
            print(f"[图片转深度图] 快路径导出的图**核对不过**( {why} )→ 改用导出 SubTool 的老办法")
        else:
            print("[图片转深度图] 快路径(Texture:Export)没成功 → 改用导出 SubTool 的老办法")

    # ---- 动手前:验明正身 ----
    fingerprint = toolio.subtool_fingerprint()
    slot = toolio.texture_slot_info()
    want_points = int(fingerprint.get("points") or -1)
    print(f"[图片转深度图] 动手前:当前 SubTool {want_points} 点 / 包围盒 {fingerprint.get('box')};"
          f"它挂的贴图 {slot.get('name') or '?'}({slot.get('width') or 0}x{slot.get('height') or 0})")
    if slot and int(slot.get("width") or 0) <= 0:
        raise PluginError(
            "这个 SubTool **没有挂贴图**(Tool > Texture Map 的贴图方块是空的)。\n"
            "先给它贴一张图(Texture 面板里导入后,点一下 Tool > Texture Map 的方块),再按一次。")

    # ---- 让 Texture 面板的"当前贴图"指向这个模型自己那张 ----
    # ZBrush 导出 OBJ 时写出来的贴图是**面板当前选中**的那张;插件上一次会把生成的深度图导入
    # 面板并选中它 → 下次换模型导出写出来的还是那张深度图(实测:换模型后深度图还是同一张)。
    focused, focus_note = toolio.focus_model_texture(str(slot.get("name") or ""))
    print(f"[图片转深度图] 面板指向:{focus_note}")
    if not focused:
        if bool(_cfg(cfg, "skip_texture_focus")):
            print("[图片转深度图] ⚠ 没能确认面板指向的是这个模型的贴图(skip_texture_focus 开着,继续)")
        else:
            raise PluginError(
                f"没能把 Texture 面板的\"当前贴图\"挪到这个模型挂的那张上:{focus_note}\n"
                "不这么做的话,ZBrush 导出时写出来的可能是**面板里那张别的贴图**"
                "(上一个模型的、或者上一次生成的深度图),于是你会看到\"换了模型图还是同一张\"。\n"
                "处理:选中这个 SubTool → Tool > Texture Map → 按一下 **Clone Txtr**"
                "(官方文档:没 Clone 的贴图 ZBrush 导不出来),再按一次「转深度图」。\n"
                "(如果你确定以前那样是好的,可以在 settings.json 里把 "
                "depthimg__skip_texture_focus 设成 true 跳过这一步。)")

    t0 = time.time()
    obj = os.path.join(work, _EXPORT_OBJ)
    print(f"[图片转深度图] 导出当前 SubTool → {obj}")
    toolio.export_active(obj, float(_cfg(cfg, "io_wait_s")), on_tick)

    # ---- 校验 1:导出的**就是当前选中的那个模型**(点数对得上)----
    points_in_obj = toolio.count_obj_vertices(obj)
    if want_points > 0 and points_in_obj != want_points:
        raise PluginError(
            f"导出的模型**不是当前选中的那个**:当前 SubTool 有 {want_points} 个顶点,"
            f"导出的 OBJ 里是 {points_in_obj} 个。\n"
            "可能原因:① ZBrush 上一次的导出还没跑完(再按一次就好);"
            "② 中途切了模型/SubTool。这次**没有**生成任何图。")

    # ---- 校验 2:有 UV ----
    if not toolio.export_has_uv(obj):
        raise PluginError(
            "这个模型**没有 UV**(导出的 OBJ 里没有任何 vt 行),读不出贴图。\n"
            "「转深度图」读的是**带 UV + 挂了贴图**那个模型的贴图 —— 请点中它再按一次"
            "(DynaMesh 高模一般没有 UV)。")

    # ---- 校验 3:贴图是刚写出来的,而且是**这个模型挂的那张** ----
    texture = toolio.find_texture(obj)
    if not texture:
        # 官方文档:没 Clone 过的贴图 ZBrush 导不出来 —— 补一次 Clone 再导,别让用户自己猜
        cloned, clone_note = toolio.clone_texture()
        print(f"[图片转深度图] 没找到贴图 → {clone_note}")
        if cloned:
            toolio.export_active(obj, float(_cfg(cfg, "io_wait_s")), on_tick)
            texture = toolio.find_texture(obj)
    if texture:
        ok, why = _check_texture(texture, t0, slot)
        if not ok:
            raise PluginError(
                f"读出来的贴图**不是这个模型挂的那张**:{why}\n"
                "最可能的原因:这个模型的贴图**没有 Clone 到 Texture 面板**里 —— "
                "官方文档写明'没 Clone 的贴图 ZBrush 导不出来',于是导出的是面板里那张别的贴图。\n"
                "处理:选中这个 SubTool → Tool > Texture Map → 按一下 **Clone Txtr**,再按一次"
                "「转深度图」。这次**没有**生成任何图。")
    return obj, texture


def _check_texture(path: str, t0: float, slot: dict[str, Any]) -> tuple[bool, str]:
    """核对一张导出的贴图:是不是刚写出来的、尺寸和"模型挂的那张"对不对得上。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        return False, f"读不到文件时间({exc})"
    if t0 and mtime < t0 - 1.5:
        return False, (f"文件是旧的(写于 {time.strftime('%H:%M:%S', time.localtime(mtime))},"
                       f"本次导出开始于 {time.strftime('%H:%M:%S', time.localtime(t0))})")
    size = toolio.image_size(path)
    want = (int(slot.get("width") or 0), int(slot.get("height") or 0))
    if size and want[0] > 0 and size != want:
        return False, (f"尺寸对不上:导出的图是 {size[0]}x{size[1]},"
                       f"而 ZBrush 说这个 SubTool 挂的是 {want[0]}x{want[1]}")
    return True, (f"{os.path.basename(path)} {size[0] if size else '?'}x"
                  f"{size[1] if size else '?'}")


def send_to_zbrush(cfg: dict[str, Any], depth_png: str,
                   expected_size: tuple[int, int] | None = None) -> tuple[bool, str]:
    """把深度图送回 ZBrush。返回 (是否**核对到**挂上了, 说明)。

    步骤(每一步都打日志,核对不上一律不报成功):

    1. **一定先** ``Texture:Import`` 把图导入 Texture 面板,并把面板的"当前贴图"切到刚导入的
       那张(这也解决了你上一个问题:图一定会在面板里)
    2. 开关「替换模型贴图」开着时,按官方文档的做法挂上去:
       **按贴图槽 → 在弹出的贴图列表里点那张贴图**(``zbc.click("PopUp:<贴图名>")``)
       —— 官方文档写明贴图槽是"弹出列表里选一张",所以**光按方块没用**(你实测印证了)
    3. 核对判据:**贴图槽的名字变了**(⚠ 不能看尺寸:深度图跟原贴图尺寸一样)
    4. 万一弹窗那条路不通,再退:Tool 层导入 / 按方块(都核对)

    ``expected_size`` 只用来打日志,不参与判定。
    """
    notes: list[str] = []
    before = toolio.texture_slot_info()
    notes.append(f"贴图槽(动手前):{before.get('name') or '?'}"
                 f"({before.get('width') or 0}x{before.get('height') or 0})")

    imported, import_note = toolio.import_texture(depth_png)
    notes.append(import_note)
    if expected_size and expected_size[0]:
        notes.append(f"深度图 {expected_size[0]}x{expected_size[1]}")

    if not bool(_cfg(cfg, "replace_texture")):
        return False, ";".join(notes + ["按设置**没有**替换模型贴图(它只是 Texture 面板里多出来的一张)"])

    wait = float(_cfg(cfg, "import_wait_s")) + 1.0
    newest = toolio.select_newest_texture()
    names = toolio.texture_name_candidates(str(newest.get("name") or ""), depth_png)

    # 主路:按贴图槽 → 在弹窗里点那张贴图(官方文档 + ZBrushCentral 配方)
    ok, how = toolio.assign_texture_via_popup(names, wait=wait)
    notes.append("弹窗挂载 " + how)
    if ok:
        return True, ";".join(notes)

    # 兜底 1:Tool 层的贴图导入
    ok1, how1 = toolio.import_texture_to_subtool(depth_png, expected_size, wait=wait)
    notes.append("兜底 1 " + how1)
    if ok1:
        return True, ";".join(notes)

    # 兜底 2:再按一次贴图方块(旧路径)
    ok2, how2 = toolio.assign_texture_to_subtool(expected_size, wait=wait)
    notes.append("兜底 2 " + how2)
    if ok2:
        return True, ";".join(notes)

    notes.append("都没核对到贴图槽变化 —— 图已经在 Texture 面板里了,"
                 "手工挂:点一下 Tool > Texture Map 的贴图方块,在弹出列表里选那张图")
    return False, ";".join(notes)


# --------------------------------------------------------------------------- #
# 诊断
# --------------------------------------------------------------------------- #
def dependencies(interp: str, name: str = "") -> str:
    """检查外部解释器里的依赖 —— **只查当前引擎真正需要的**(PIL 的包名是 pillow)。

    以前把 depth_anything_3 也对 dav2 报"缺少",诊断里看着像出了问题(实测被坑过)。
    """
    needed = {"dav2": ("numpy", "PIL", "torch"), "da3": ("numpy", "PIL", "torch",
                                                        "depth_anything_3"),
              "luma": ("numpy", "PIL"), "sdf": ("numpy", "PIL", "scipy")}.get(name)
    if needed is None:
        needed = ("numpy", "PIL", "torch", "scipy", "depth_anything_3")
    code = ("import importlib.util\n"
            "miss = []\n"
            f"for n in {needed!r}:\n"
            "    try:\n"
            "        if importlib.util.find_spec(n) is None:\n"
            "            miss.append(n)\n"
            "    except Exception:\n"
            "        miss.append(n)\n"
            "print('都有' if not miss else '缺少:' + ','.join(miss))\n")
    try:
        done = subprocess.run([interp, "-c", code], capture_output=True, text=True,
                              timeout=60)
        out = (done.stdout or "").strip() or (done.stderr or "").strip()
        return out or f"返回码 {done.returncode}"
    except Exception as exc:                                             # noqa: BLE001
        return f"检查失败:{exc}"


def _size_text(cfg: dict[str, Any]) -> str:
    """诊断里怎么写"推理边长"(0 = 自动)。"""
    value = int(_cfg(cfg, "size"))
    return str(value) if value else f"自动(上限 {int(_cfg(cfg, 'size_max'))})"


def uniquify_output(path: str) -> str:
    """把结果改名成"带内容短哈希"的名字,并把 16 位 TIFF / 报告一起改名。

    ⚠ 为什么必须这么做(实测踩到):**ZBrush 的 Texture 面板按名字去重** ——
    导入一个与已有贴图同名的文件时,它会用**列表里第一张同名的**(旧的那张),
    新的排在后面不会被选中(ZBrushCentral 上那段现成脚本的作者也踩过并写过这一点)。
    文件名固定 → 换引擎/换参数再跑,面板里还是旧图,表现就是"切了引擎没反应、深度图没区别"。
    名字里带**内容哈希**:内容一样就同名(不会堆垃圾),内容不一样就一定不同名(不会撞)。
    """
    import hashlib
    import re

    if not os.path.exists(path):
        return path
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()[:6]
    base, ext = os.path.splitext(path)
    base = re.sub(r"_[0-9a-f]{6}$", "", base)        # 去掉上一次可能带的哈希
    target = f"{base}_{digest}{ext}"
    moves = [(path, target)]
    for old_suffix in ("_16bit.tif", ".report.json"):
        source = base + old_suffix
        if old_suffix == ".report.json":
            source = path + ".report.json"
        if os.path.exists(source):
            moves.append((source, os.path.splitext(target)[0] + old_suffix))
    for source, dest in moves:
        if os.path.abspath(source) == os.path.abspath(dest):
            continue
        try:
            if os.path.exists(dest):
                os.remove(dest)
            os.replace(source, dest)
        except OSError as exc:                                        # noqa: BLE001
            print(f"[图片转深度图] 改名 {source} → {dest} 失败:{exc}")
    return target


def engine_label(cfg: dict[str, Any]) -> str:
    """输出文件名里的引擎标记:**dav2 带上档位**(``dav2-vits`` / ``dav2-vitb`` / ``dav2-vitl`` / ``dav2-vitg``)。

    为什么必须带档位(实测踩点):面板上那四格是**同一批权重的四个规模**,引擎名都是 ``dav2``。
    只写引擎名的话,"V2-S 换 V2-L" 生成的文件名一模一样,而 **ZBrush 的 Texture 面板按名字去重**
    (见 README 实测结论 #26)—— 名字一样它就用列表里第一张旧的。内容短码虽然也能区分内容,
    但光看名字看不出是哪一档,排障时很坑。da3 / luma / sdf 本来就只有一个,不加后缀。
    """
    name = engine(cfg)
    return f"{name}-{variant(cfg)}" if name == "dav2" else name


def texture_tag(cfg: dict[str, Any]) -> str:
    """给输出文件名用的短标记(取"这个模型挂的贴图名"),拿不到就返回空串。"""
    import re as _re

    name = str(slot_info(cfg).get("name") or "")
    # ⚠ 替换之后模型挂的就是**我们生成的深度图**,它的名字里已经带了这套前后缀 ——
    # 不剥掉的话文件名会一层层套下去(实测出现过 depthimg_depth_depthimg_depth_depthimg__da3_…)。
    # 引擎标记现在可能带档位后缀(dav2-vitl),所以那一档正则要容得下 "-xxx"。
    for pattern in (r"^depthimg_depth_?", r"_[0-9a-f]{6}$",
                    r"_((dav2|da3|luma|sdf)(-[a-z0-9]+)?)$"):
        name = _re.sub(pattern, "", name)
    tag = _re.sub(r"[^0-9A-Za-z_-]", "", name)[:24]
    return f"_{tag}" if tag else ""


def _slot_text(cfg: dict[str, Any]) -> str:
    """把"当前 SubTool 挂的贴图"压成一行(诊断用)。"""
    info = slot_info(cfg)
    if not info:
        return "(读不到贴图方块的信息)"
    if int(info.get("width") or 0) > 0:
        return (f"{info.get('name') or '?'}({info['width']}x{info['height']},"
                f"{info.get('depth') or '?'} 位)")
    return "没挂贴图(贴图方块是空的)"


def diagnostics_text(cfg: dict[str, Any]) -> str:
    name = engine(cfg)
    interp = depth_interpreter(cfg)
    model, commercial, note = model_and_license(cfg)
    lines = [
        "图片转深度图(depthimg)诊断",
        f"引擎:{name}(面板上那四格 = DA V2 的档位;da3/luma/sdf 在 settings.json 的 "
        f"depthimg__engine 里选)",
        f"DA V2 档位:{VARIANT_TITLES[variant(cfg)]}"
        f"{'(面板点的是它)' if name == 'dav2' else '(引擎不是 dav2,面板四格全灭)'}",
        f"权重:{model or '(没找到)'} → {note}"
        f"{'' if commercial else '  ← **不可商用**,需要 depthimg__allow_nc_weights'}",
        f"权重搜索目录:{', '.join(_MODEL_GLOBS)}"
        f"{'; ComfyUI:' + find_comfy(cfg) if find_comfy(cfg) else ''}",
        f"解释器:{interp}",
        f"依赖检查(只查 {name} 需要的):{dependencies(interp, name)}",
        f"管线脚本:{depth_pipeline_path()}",
        f"工作目录:{work_dir(cfg)}",
        f"读贴图:快路径(Tool:Texture Map:Export) "
        f"{'开' if _cfg(cfg, 'use_texture_export') else '关(默认:导出 SubTool)'}",
        "当前 SubTool 挂的贴图:" + _slot_text(cfg),
        f"参数:替换模型贴图 {'开' if _cfg(cfg, 'replace_texture') else '关'}  "
        f"反相 {'开' if _cfg(cfg, 'invert') else '关'}  裁剪 {float(_cfg(cfg, 'clip')):g}%  "
        f"推理边长 {_size_text(cfg)}(da3 {int(_cfg(cfg, 'da3_res')) or '自动'})  "
        f"细节叠加 {float(_cfg(cfg, 'detail')):g}(-1=自动)  去极值 "
        f"{'开' if _cfg(cfg, 'despike') else '关'}  "
        f"位深 {int(_cfg(cfg, 'bit_depth'))}  "
        f"底色 {_cfg(cfg, 'bg')}  倒角 {float(_cfg(cfg, 'sdf_radius')):g}px",
        f"上次生成:{_cfg(cfg, 'last_depth') or '(还没生成)'}"
        f"{'  → ' + str(_cfg(cfg, 'last_report')) if _cfg(cfg, 'last_report') else ''}",
        f"上次送回 ZBrush:{_cfg(cfg, 'last_assign') or '(还没试过)'}",
        "ZBrush 侧要点:SDK 没有贴图 API,导入贴图/挂到 SubTool 都是"
        "「候选 item path + zbc.exists() 探测」后按下去的;实际路径会写在上面的日志里。",
    ]
    for name_ in ("last_depth",):
        path = str(_cfg(cfg, name_) or "")
        if path and os.path.exists(path):
            lines.append(f"  {os.path.basename(path)} = "
                         f"{os.path.getsize(path) / 1024:.0f} KB")
    for entry in sorted(os.listdir(work_dir(cfg))):
        lines.append(f"  工作目录: {entry}")
    return "\n".join(lines)
