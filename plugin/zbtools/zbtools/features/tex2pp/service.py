"""贴图转色(tex2pp)的业务逻辑:导出 → 外部烘焙 → 导回。

流程(为什么两步按钮,而不是一键):
    低模和高模是**同一个 Tool 里的两个 SubTool**,插件只能拿到"当前活动 SubTool"。
    所以:
      ① 你点中低模(有 UV + 贴图那个)→ 点「记住低模」:导出它 + 找出它的贴图
      ② 你点中高模(DynaMesh 那个)→ 点「烘焙并导回」:导出高模 → 外部按高模顶点采样
         低模贴图 → 写 PLY → 导回(变成 polypaint)
    路径都记在配置里(``tex2pp__source_obj`` / ``tex2pp__source_texture``),重启 ZBrush 也还在。

关键实测结论(设计依据):
    * ZBrush 的导入**只认 PLY 顶点色**:OBJ 的 `#MRGB` 块它只写不读,`v x y z r g b` 不认
    * `Tool:Import` / `Tool:Export` 都靠 `set_next_filename(全路径)` + `press(...)`,
      且 press 是**命令队列**(不是同步调用)→ 必须轮询等文件落盘
    * 导入会**占用当前 Tool 槽**(不是追加 Subtool),Tool 名会变成 `!:<路径>`
    * 导出的 OBJ 坐标带 `Auto scale 0.1`,而 PLY 导入的尺度与它一致(实测比例 1.000)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

import zbrush.commands as zbc

from ... import proc, settings, ui
from ...zbapi import toolio
from ...errors import PluginError

#: 本功能的配置键前缀(settings.feature_prefix 会检查)
PREFIX = "tex2pp__"

#: 外部管线脚本所在目录(与插件零耦合:只吃 argv、只打 stdout)
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def pipeline_path() -> str:
    """管线脚本的路径:开发态是 ``bake_pipeline.py``,发行包里是 ``bake_pipeline.pyc``。

    打包器只发字节码(`optimize=2`),而 CPython 可以直接执行 .pyc —— 但要求解释器
    主版本一致。所以这里两个都找,并在诊断里把实际用到的那个打出来。
    """
    base = os.path.join(_TOOLS_DIR, "bake_pipeline")
    for ext in (".py", ".pyc"):
        if os.path.exists(base + ext):
            return base + ext
    return base + ".py"                       # 都不在:返回 .py,让报错信息可读

#: 配置默认值(解释器复用插件级 settings 的 interpreter)
DEFAULTS: dict[str, Any] = {
    "max_dist_ratio": 0.02,     # 最近点距离超过包围盒对角线这个比例 → 记为"未被覆盖"
    "flip_v": True,             # OBJ 的 v 向上、图像行向下 → 默认翻转
    "triangulate": False,       # 默认保留 ZBrush 的四边面拓扑
    "timeout_s": 900.0,         # 烘焙超时(688k 顶点实测约 10 秒)
    "io_wait_s": 300.0,         # 等 ZBrush 把导出的 OBJ 写盘(导出是命令队列,必须轮询)
    # 导入提交后最多在脚本里等多久(秒)。⚠ 脚本运行期间 ZBrush 界面是被占住的,
    # 长等 = 卡死感 —— 所以这里只等"够把命令提交进队列"的时间,剩下的交给 ZBrush 自己跑。
    "import_wait_s": 1.5,
    "keep_temp": True,          # 保留工作目录(PLY ~21MB,便于复查或手动导入)
    "source_obj": "",           # ① 记住的低模 OBJ
    "source_texture": "",       # ① 找到的低模贴图(ZBrush 顺手写出的那张)
    # ① 记住的是**哪一个模型**:位置(Tool + SubTool)与几何指纹都记下来
    "source_location": "",       # JSON:tool_index/tool_path/sub_index/sub_id/fingerprint
    "source_subtool_id": -1,
    "source_subtool_index": -1,
    "source_points": 0,
    # 「选择贴图」指定的原图(ZBrush 导出的 BMP 是 24 位、没有 alpha,要用通道就得指回原图)
    "texture_path": "",
    "texture_has_alpha": False,  # 检测结果(缓存:面板据此启用那两个开关)
    "texture_mode": "",          # 检测到的图像模式(诊断里显示)
    "texture_alpha_stats": "",   # alpha 的实测证据(mode|尺寸|lo|hi|透明占比)
    "migrated_hidden_switches": False,  # 一次性迁移标记(见 migrate_once)
    "work_dir": "",              # 工作目录(持久化 —— 见 work_dir() 的注释)
    "alpha_only": False,         # 仅限Alpha:把 alpha 当亮度源 → 黑白(转遮罩)
    "aa": False,                 # 边缘抗锯齿:UV 邻域超采样
    "aa_radius": 1.0,
    "aa_samples": 3,

}

_WORK_DIR: str | None = None


@dataclass
class ImportResult:
    """导回提交的结果(name = 导入后的 Tool 名,note = 给用户看的说明)。"""
    name: str
    note: str
    submitted_s: float


def _cfg(cfg: dict[str, Any], key: str) -> Any:
    value = cfg.get(PREFIX + key)
    return DEFAULTS[key] if value is None else value


def _save(cfg: dict[str, Any], key: str, value: Any) -> None:
    cfg[PREFIX + key] = value
    settings.save(cfg)


def work_dir(cfg: dict[str, Any] | None = None) -> str:
    """工作目录(所有导出/PLY 都放这里)。

    ⚠ **必须持久化**,不能每次会话新建一个:
      系统 temp 下的目录会被清理,而配置里记着上一次的 ``tex2pp_low.obj`` ——
      重启 ZBrush 后路径失效,``has_source()`` 就变成假,面板上 ② 直接灰掉,
      表现就是"第二步烘焙失效"。现在把目录路径也存进配置,并在目录还在时继续复用。
    """
    global _WORK_DIR
    if _WORK_DIR is None or not os.path.isdir(_WORK_DIR):
        saved = str(_cfg(cfg, "work_dir")).strip() if cfg is not None else ""
        if saved and os.path.isdir(saved):
            _WORK_DIR = saved
            print(f"[贴图转色] 复用上次的工作目录:{saved}")
        else:
            _WORK_DIR = tempfile.mkdtemp(prefix="zbtools_tex2pp_")
            if cfg is not None:
                _save(cfg, "work_dir", _WORK_DIR)
    return _WORK_DIR


def resolve_source(cfg: dict[str, Any]) -> str:
    """拿到**可用的**低模 OBJ;配置里的路径没了就退回工作目录里的标准文件名。

    为什么需要:工作目录在系统 temp 下,重启或系统清理后旧路径会失效 ——
    之前的表现就是"② 变灰/说没记住低模",其实文件可能只是搬到别处了。
    这里用配置里的路径 → 工作目录里的 tex2pp_low.obj 依次兜底,并顺手修好配置。
    """
    path = source_obj(cfg)
    if path and os.path.exists(path):
        return path
    fallback = os.path.join(work_dir(cfg), "tex2pp_low.obj")
    if os.path.exists(fallback):
        print(f"[贴图转色] 配置里的低模路径已失效({path or '空'}),"
              f"改用工作目录里的:{fallback}")
        _save(cfg, "source_obj", fallback)
        return fallback
    return ""


#: 面板上已经删掉、但配置里可能还留着旧值的开关(值回默认,避免"幽灵行为")
_RETIRED_SWITCHES = ("alpha_only", "aa")

#: 迁移标记的配置键(只做一次;之后你要手动开隐藏开关就不会再被清掉)
_MIGRATED_KEY = "migrated_hidden_switches"


def migrate_once(cfg: dict[str, Any]) -> None:
    """把「面板上已被删除的开关」的旧值清回默认。

    为什么需要:这几个开关以前在面板上,用户点过就会存进 settings.json;后来按需求
    把它们从面板撤掉了 —— 如果继续沿用旧值,行为会变得**看不出来由**
    (实测:诊断里发现 ``aa`` 还是 true,颜色会被悄悄平滑)。
    只做一次,之后你手动改 settings.json 开的隐藏开关不会再被清掉。
    """
    if cfg.get(PREFIX + _MIGRATED_KEY):
        return
    changed = [k for k in _RETIRED_SWITCHES if _cfg(cfg, k)]
    for key in _RETIRED_SWITCHES:
        _save(cfg, key, DEFAULTS[key])
    _save(cfg, _MIGRATED_KEY, True)
    if changed:
        print(f"[贴图转色] 面板上已删除的开关回到默认(原来是开着的):{changed}"
              f" —— 需要的话可在 settings.json 里用 tex2pp__<名字> 打开")


def reset_work_dir(cfg: dict[str, Any]) -> str:
    """① 开始前整理工作目录。

    ``keep_temp`` 为假时把上次的目录删掉重建 —— 否则每次会话都会留一个 ~70MB 的
    临时目录(高模 OBJ 44MB + PLY 21MB),越堆越多。为真(默认)时保留,便于复查
    上一次的 PLY / 手工导入。
    """
    global _WORK_DIR
    if not bool(_cfg(cfg, "keep_temp")) and _WORK_DIR and os.path.isdir(_WORK_DIR):
        shutil.rmtree(_WORK_DIR, ignore_errors=True)
        _WORK_DIR = None
        print("[贴图转色] keep_temp 关 → 已清掉上次的工作目录")
    return work_dir(cfg)


# --------------------------------------------------------------------------- #
# 给面板用的读写入口(面板不碰私有函数)
# --------------------------------------------------------------------------- #
def param(cfg: dict[str, Any], key: str) -> Any:
    """公开的参数读取(面板只读这里,不碰私有函数)。"""
    return _cfg(cfg, key)


def source_obj(cfg: dict[str, Any]) -> str:
    return str(_cfg(cfg, "source_obj"))


def source_texture(cfg: dict[str, Any]) -> str:
    return str(_cfg(cfg, "source_texture"))


def has_source(cfg: dict[str, Any]) -> bool:
    """是否记住了低模,而且那份导出文件**还找得到**(含工作目录兜底)。"""
    return bool(resolve_source(cfg))


def export_has_uv(obj_path: str) -> bool:
    """导出的 OBJ 里有没有 UV(实现在 zbapi.toolio;早退读法,见那里的注释)。"""
    return toolio.export_has_uv(obj_path)


def discard_export(obj_path: str) -> None:
    """删掉一次失败的临时导出(OBJ + 它可能带出的 mtl/贴图)。"""
    base = os.path.splitext(obj_path)[0]
    for suffix in (".obj", ".mtl", ".bmp", "_TXTR.tif", "_TXTR.bmp", "_TXTR.png"):
        path = base + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:                                    # noqa: BLE001
                print(f"[贴图转色] 清理 {path} 失败:{exc}")


def adopt_export(tmp_obj: str, final_obj: str) -> str:
    """把**临时**导出落位成正式文件(OBJ + 它的贴图),返回贴图路径(可能为空)。

    为什么要走临时名:① 会覆盖上一次记住的低模导出 —— 如果第一次导出的是个没有 UV 的
    模型,直接写正式名就会**把旧的记录弄坏**(文件被顶掉、② 跟着变灰)。所以先导出到临时名、
    校验通过再落位;失败就把临时文件删干净,旧记录原封不动。
    """
    texture = find_texture(tmp_obj)
    final_base = os.path.splitext(final_obj)[0]
    os.replace(tmp_obj, final_obj)
    if texture:
        target = final_base + os.path.splitext(texture)[1]
        if os.path.abspath(texture) != os.path.abspath(target):
            os.replace(texture, target)
        texture = target
    stale_mtl = os.path.splitext(tmp_obj)[0] + ".mtl"
    if os.path.exists(stale_mtl):
        try:
            os.remove(stale_mtl)
        except OSError:
            pass
    return texture


def _int_call(fn: "Callable[[], int]", default: int = -1) -> int:
    try:
        return int(fn())
    except Exception:                                                 # noqa: BLE001
        return default


def current_location() -> dict[str, Any]:
    """当前"站在哪个模型上"的多重指纹。

    为什么要多重:官方 API 里没有"SubTool 全局唯一 ID"这种东西 ——
    ``get_subtool_id()`` 是**相对当前 Tool** 的,两个 Tool 的 SubTool 0 可能同 ID;
    而**几何指纹**(点数 + 包围盒)是"这个模型到底是不是它"的硬证据,能兜住 ID 不可靠的情况。
    """
    step = 1e-4
    box = None
    try:
        raw = [float(v) for v in zbc.query_mesh3d(2, 1)]              # 当前 SubTool 完整包围盒
        if len(raw) >= 6:
            box = [round(abs(raw[3] - raw[0]) / step) * step,
                   round(abs(raw[4] - raw[1]) / step) * step,
                   round(abs(raw[5] - raw[2]) / step) * step]
    except Exception:                                                 # noqa: BLE001
        box = None
    points = -1
    try:
        points = int(zbc.query_mesh3d(0)[0])
    except Exception:                                                 # noqa: BLE001
        points = -1
    tool_path = ""
    try:
        tool_path = str(zbc.get_active_tool_path())
    except Exception:                                                 # noqa: BLE001
        tool_path = ""
    return {
        "tool_index": _int_call(zbc.get_active_tool_index),
        "tool_path": tool_path,
        "sub_index": _int_call(zbc.get_active_subtool_index),
        "sub_id": _int_call(zbc.get_subtool_id),
        "points": points,
        "box": box,
    }


def _decode_location(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = str(_cfg(cfg, "source_location") or "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:                                                 # noqa: BLE001
        return {}


def _same_model(saved: dict[str, Any], now: dict[str, Any]) -> bool:
    """两个位置记录是不是**同一个模型**。

    规则(宁可漏判成"没记住",也不能错判成"已记住"):
      * 几何指纹(点数 / 包围盒)不同 → 一定不是
      * 指纹相同 → 再看位置:Tool 与 SubTool 的**每一条可比的信号都必须一致**
      * 一条信号都拿不到 → 不算(不能凭"都是 -1"就说相同 —— 这是上一版的真 bug)
    """
    if not saved:
        return False
    sp, np_ = saved.get("points", -1), now.get("points", -1)
    if sp is not None and np_ is not None and int(sp) >= 0 and int(np_) >= 0 and int(sp) != int(np_):
        return False
    sb, nb = saved.get("box"), now.get("box")
    if sb and nb and list(sb) != list(nb):
        return False

    signals: list[bool] = []
    for key in ("tool_index", "sub_index"):
        a, b = int(saved.get(key, -1)), int(now.get(key, -1))
        if a >= 0 and b >= 0:
            signals.append(a == b)
    for key in ("tool_path",):
        a, b = str(saved.get(key, "")), str(now.get(key, ""))
        if a and b:
            signals.append(a == b)
    a, b = int(saved.get("sub_id", -1)), int(now.get("sub_id", -1))
    if a > 0 and b > 0:
        signals.append(a == b)
    if not signals:
        return bool(sb and nb and list(sb) == list(nb))    # 只有几何可比时,以几何为准
    return all(signals)


def is_current_remembered(cfg: dict[str, Any]) -> bool:
    """当前选中的**是不是**被记住的那个低模(① 的复选状态看这个)。"""
    if not has_source(cfg):
        return False
    return _same_model(_decode_location(cfg), current_location())


def remember_source(cfg: dict[str, Any], obj_path: str, texture_path: str,
                    points: int = 0) -> None:
    """① 记住低模:导出的文件 + 它的贴图 + **它是哪一个 SubTool**(唯一 ID)。"""
    here = current_location()
    if int(points or 0) > 0:              # 只在拿到有效点数时才覆盖(别让 -1 盖掉好指纹)
        here["points"] = int(points)
    _save(cfg, "source_location", json.dumps(here, ensure_ascii=False))
    _save(cfg, "source_obj", obj_path)
    _save(cfg, "source_texture", texture_path)
    _save(cfg, "source_subtool_id", here.get("sub_id", -1))
    _save(cfg, "source_subtool_index", here.get("sub_index", -1))
    _save(cfg, "source_points", int(here.get("points") or 0))
    print(f"[贴图转色] 记住的模型位置:Tool {here.get('tool_path')!r}"
          f"(#{here.get('tool_index')}) SubTool #{here.get('sub_index')}"
          f"(ID {here.get('sub_id')}) 几何指纹 {here.get('points')} 点 / {here.get('box')}")


def set_param(cfg: dict[str, Any], key: str, value: Any) -> None:
    """面板上改参数(带前缀写回配置)。"""
    _save(cfg, key, value)


def interpreter(cfg: dict[str, Any]) -> str:
    """跑外部管线的解释器:插件级配置,缺省回落到当前解释器(发行版没有 .venv)。"""
    path = str(cfg.get("interpreter") or "").strip()
    if path and os.path.exists(path):
        return path
    return sys.executable


# --------------------------------------------------------------------------- #
# 导出 / 贴图查找
# --------------------------------------------------------------------------- #
def _press(item_path: str) -> str:
    """按界面项(实现在 zbapi.toolio,与「图片转深度图」功能共用)。"""
    return toolio.press(item_path)




def export_active(dest_obj: str, cfg: dict[str, Any],
                  on_tick: Callable[[], None] | None = None) -> None:
    """把当前 SubTool 导出成 OBJ(实现在 zbapi.toolio;等多久由配置 io_wait_s 决定)。

    ``on_tick`` 给进度条用:大网格导出要几十秒,这段时间同样是"看不见的等待"。
    """
    toolio.export_active(dest_obj, float(_cfg(cfg, "io_wait_s")), on_tick)


def find_texture(obj_path: str) -> str:
    """找导出时 ZBrush 顺手写出的贴图(实现在 zbapi.toolio)。"""
    return toolio.find_texture(obj_path)


def effective_texture(cfg: dict[str, Any]) -> str:
    """实际用来烘焙的那张图。

    ``tex2pp__texture_path``(隐藏的兜底层,不在面板上)优先:ZBrush 导出贴图时会把透明
    通道剥成 24 位 BMP,这时可以用它指回自己处理好的原图;否则用 ① 带出的那张。

    ⚠ 这里以前有个真 bug:② 自己解析贴图、**没走这个函数**,所以那个兜底层从来没生效过。
    """
    manual = str(_cfg(cfg, "texture_path")).strip()
    if manual and os.path.exists(manual):
        return manual
    return source_texture(cfg)


def texture_role(cfg: dict[str, Any], path: str) -> str:
    """给控制台看的"这张图是什么来路"(省得你不知道②到底用了哪张)。"""
    if not path:
        return "无"
    manual = str(_cfg(cfg, "texture_path")).strip()
    if manual and os.path.abspath(path) == os.path.abspath(manual):
        return "settings.json 里指定的原图"
    return "低模带出的贴图"


def detect_alpha(cfg: dict[str, Any], path: str) -> tuple[bool, str]:
    """检测贴图有没有**可用的**透明通道,返回 (可用, 证据文本)。

    ⚠ 只看"有没有 A 通道"会误判(实测踩到):不少导出器 —— 包括 ZBrush 自己 ——
    会写 32 位文件,A 通道**整片 255**,通道在、但没有任何透明信息。
    拿这种图做「仅限Alpha」只会得到一张全白,用户看到的就是"alpha 识别不准"。
    所以这里同时量出 alpha 的**极值**和**透明像素占比**,只有真的有透明像素才算可用。
    """
    if not path or not os.path.exists(path):
        _save(cfg, "texture_has_alpha", False)
        _save(cfg, "texture_mode", "")
        _save(cfg, "texture_alpha_stats", "")
        return False, "没有贴图"
    # ⚠ 别用 f-string 拼这段:大括号会被**真的求值**(踩过)。用 % 格式化最稳。
    code = ("import sys\n"
            "from PIL import Image\n"
            "im = Image.open(sys.argv[1])\n"
            "bands = im.getbands()\n"
            "has = 'A' in bands\n"
            "lo = hi = 255\n"
            "transp = 0.0\n"
            "if has:\n"
            "    a = im.convert('RGBA').getchannel('A')\n"
            "    lo, hi = a.getextrema()\n"
            "    hist = a.histogram()\n"
            "    total = sum(hist) or 1\n"
            "    transp = sum(hist[:250]) / total\n"
            "print('%s|%dx%d|%s|%d|%d|%.4f' % (im.mode, im.size[0], im.size[1],\n"
            "      'alpha' if has else 'noalpha', lo, hi, transp))\n")
    try:
        done = subprocess.run([interpreter(cfg), "-c", code, path],
                              capture_output=True, text=True, timeout=60)
        text = (done.stdout or "").strip()
    except Exception as exc:                                          # noqa: BLE001
        text = ""
        print(f"[贴图转色] alpha 检测失败:{exc}")
    stats = text or "未知"
    fields = text.split("|") if text else []
    has_band = len(fields) >= 6 and fields[2] == "alpha"
    lo = int(fields[3]) if len(fields) >= 6 else 255
    hi = int(fields[4]) if len(fields) >= 6 else 255
    transp = float(fields[5]) if len(fields) >= 6 else 0.0
    # 可用 = 有 A 通道 + 真的存在透明像素 + 也有不透明像素(否则做遮罩没意义)
    usable = bool(has_band and lo < 250 and hi > 5)
    _save(cfg, "texture_has_alpha", usable)
    _save(cfg, "texture_mode", f"{fields[0]}|{fields[1]}" if len(fields) >= 2 else "未知")
    _save(cfg, "texture_alpha_stats", stats)
    detail = (f"A 通道 {lo}~{hi},透明像素 {transp * 100:.1f}%" if has_band
              else "没有 A 通道")
    verdict = "可用的透明通道" if usable else (
        "有 A 通道但**整片不透明**(等于没有透明信息)" if has_band else "没有透明通道")
    print(f"[贴图转色] 贴图检测:{os.path.basename(path)} → {stats};{detail}"
          f" → {verdict}")
    return usable, stats


def set_texture(cfg: dict[str, Any], path: str) -> tuple[bool, str]:
    """指定另一张贴图并立刻检测 alpha(隐藏兜底层,面板上不再有这个按钮)。"""
    _save(cfg, "texture_path", path)
    return detect_alpha(cfg, path)


# --------------------------------------------------------------------------- #
# 外部烘焙
# --------------------------------------------------------------------------- #
def pipeline_args(cfg: dict[str, Any], low: str, high: str, texture: str, out_ply: str,
                  report_json: str) -> list[str]:
    """拼出外部管线的命令行(单独抽出来,便于测试与诊断打印)。"""
    args = [interpreter(cfg), pipeline_path(),
            "--low", low, "--high", high, "--texture", texture, "--out", out_ply,
            "--report-json", report_json,
            "--max-dist-ratio", f"{float(_cfg(cfg, 'max_dist_ratio')):g}"]
    if not _cfg(cfg, "flip_v"):
        args.append("--no-flip-v")
    if _cfg(cfg, "triangulate"):
        args.append("--triangulate")
    if _cfg(cfg, "alpha_only"):
        args += ["--alpha-only"]
    if _cfg(cfg, "aa"):
        args += ["--aa", "--aa-radius", f"{float(_cfg(cfg, 'aa_radius')):g}",
                 "--aa-samples", str(int(_cfg(cfg, "aa_samples")))]
    return args


#: 烘焙管线的日志片段 → (进度条显示文字, 在本段进度里的位置)。
#: 顺序即阶段顺序;"查询最近点"那段管线会打**真进度**(``[zbtools-progress]``),所以两档之间
#: 一般是真数据而不是爬升。这段是最费时间的(高模每个顶点都要查最近点)。
_BAKE_STAGES: tuple[tuple[str, str, float], ...] = (
    ("读低模", "读低模…", 0.04),
    ("读高模", "读高模…", 0.12),
    ("查询最近点", "采样贴图(最慢的一步)…", 0.22),
    ("写出", "写 PLY…", 0.86),
    ("总耗时", "完成", 0.95),
)


def bake(cfg: dict[str, Any], low: str, high: str, texture: str, out_ply: str,
         progress: "ui.Progress | None" = None,
         base: float = 0.0, span: float = 1.0) -> dict[str, Any]:
    """调外部管线烘焙;返回它写的 JSON 报告。

    **边跑边喂进度条**(和「图片转深度图」同一套):``proc.stream`` 逐行读,阶段跳变 +
    阶段之间按时间爬升;管线自己报的真进度(``[zbtools-progress] 0.42``)优先。
    """
    report_json = out_ply + ".report.json"
    if os.path.exists(report_json):
        os.remove(report_json)
    args = pipeline_args(cfg, low, high, texture, out_ply, report_json)
    print(f"[贴图转色] 烘焙:{' '.join(args)}")
    prog = progress if progress is not None else ui.Progress("贴图转色", eta=90.0)
    stops = [base + span * at for _m, _t, at in _BAKE_STAGES]

    def on_line(line: str) -> None:
        pct = ui.parse_progress(line)
        if pct is not None:
            prog.stage("采样贴图(最慢的一步)…", base + span * pct)
            return
        print(f"[贴图转色]   {line}")
        for idx, (marker, text, _at) in enumerate(_BAKE_STAGES):
            if marker in line:
                prog.stage(text, stops[idx])
                nxt = stops[idx + 1] if idx + 1 < len(stops) else base + span
                prog.set_ceiling(max(stops[idx], nxt - span * 0.02))
                break

    prog.stage("启动烘焙管线…", base + span * 0.01)
    try:
        code, lines, timed_out = proc.stream(
            proc.unbuffered(args), float(_cfg(cfg, "timeout_s")),
            on_line=on_line, on_tick=prog.creep)
    except OSError as exc:
        raise PluginError(f"启动外部管线失败:{exc}\n解释器配置:{interpreter(cfg)}") from exc

    if timed_out:
        raise PluginError(
            f"烘焙超时(超过 {float(_cfg(cfg, 'timeout_s')):.0f} 秒)。\n"
            f"可调大配置 {PREFIX}timeout_s;超大网格建议先减面。\n"
            f"已经收到的最后几行:\n{proc.tail(lines, 6)}")
    if code != 0 or not os.path.exists(out_ply):
        hint = ("\n依赖没装?用这个解释器装:numpy trimesh rtree scipy pillow\n"
                f"  {interpreter(cfg)} -m pip install numpy trimesh rtree scipy pillow\n"
                f"(发行包里只有 {os.path.basename(pipeline_path())} —— 外部解释器的主版本"
                f"要与打包时一致)")
        raise PluginError(f"烘焙失败(返回码 {code}):\n{proc.tail(lines, 14)}{hint}")
    try:
        with open(report_json, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:                                          # noqa: BLE001
        print(f"[贴图转色] 读报告失败({exc}),只按 stdout 判断成功")
        return {}


# --------------------------------------------------------------------------- #
# 导回
# --------------------------------------------------------------------------- #
def _tool_name(index: int, fallback: str = "") -> str:
    try:
        return str(zbc.get_tool_path(index)) if index >= 0 else fallback
    except Exception:                                                 # noqa: BLE001
        return fallback


def import_ply(ply_path: str, cfg: dict[str, Any]) -> ImportResult:
    """把上色 PLY 提交给 ZBrush 导入。

    ⚠⚠ 这里必须**短等就返回**,原因实测:
      * 脚本运行期间 ZBrush 的界面基本是占住的(看起来就是卡死;实测**顶部状态条例外**,
        它会刷新 —— 见 scripts/probe_notebar.py);
      * ``press`` 只是把命令排进队列,真正的导入由 ZBrush 在之后执行(大网格要几十秒);
      * 早先的实现提交后还轮询了最多 60 秒 Tool 名变化 —— 用户看到的就是
        "导入成功以后还卡很久",那 60 秒纯粹是我们自己占着不放。

    名字不去动它:ZBrush 导入后会把 Tool 命名成 ``!:<文件路径>``,想改名用 Tool 面板
    (实测"顺手改名"能不能赶上取决于导入速度,做出来行为不一致,不如不做)。
    """
    t0 = time.time()
    if not os.path.exists(ply_path):
        raise PluginError(f"PLY 不存在:{ply_path}")
    index = -1
    old_name = ""
    try:
        index = int(zbc.get_active_tool_index())
        old_name = str(zbc.get_tool_path(index))
    except Exception as exc:                                          # noqa: BLE001
        print(f"[贴图转色] 读原 Tool 名失败(不影响导入):{exc}")

    zbc.set_next_filename(ply_path)
    press_err = _press("Tool:Import")

    deadline = time.time() + max(0.0, float(_cfg(cfg, "import_wait_s")))
    name = old_name
    while time.time() < deadline:
        try:
            zbc.update(1, True)               # 让 ZBrush 处理队列 + 重绘(press 是队列)
        except Exception:                                             # noqa: BLE001
            pass
        name = _tool_name(index, old_name)
        if name != old_name:
            break
        time.sleep(0.1)

    if name == old_name:
        note = ("导入已提交(接下来由 ZBrush 自己跑完)。导入后 Tool 名会变成 "
                "!:/.../tex2pp_polypaint 这种形式;想改名在 Tool 面板里改即可")
        if press_err:
            note += f";press 报过一条 {press_err.split(':')[0]}(命令已进队列,可忽略)"
    else:
        note = "导入已完成" + (f"(press 报过一条 {press_err.split(':')[0]},可忽略)"
                              if press_err else "")
    return ImportResult(name=name, note=note, submitted_s=time.time() - t0)


# --------------------------------------------------------------------------- #
# 诊断
# --------------------------------------------------------------------------- #
def dependencies(interp: str) -> str:
    """检查外部解释器里的依赖(PIL 的包名是 pillow)。"""
    # ⚠ 子进程里必须 **显式** import importlib.util:只 import importlib 时
    #   `importlib.util` 并不存在(实测报 AttributeError,诊断输出里露过这一条)。
    code = ("import importlib.util\n"
            "miss = []\n"
            "for n in ('numpy', 'trimesh', 'PIL', 'rtree', 'scipy'):\n"
            "    try:\n"
            "        if importlib.util.find_spec(n) is None:\n"
            "            miss.append(n)\n"
            "    except Exception:\n"
            "        miss.append(n)\n"
            "print('依赖齐全' if not miss else '缺少:' + ','.join(miss))\n")
    try:
        done = subprocess.run([interp, "-c", code], capture_output=True, text=True,
                              timeout=60)
        out = (done.stdout or "").strip() or (done.stderr or "").strip()
        return out or f"返回码 {done.returncode}"
    except Exception as exc:                                          # noqa: BLE001
        return f"检查失败:{exc}"


def diagnostics_text(cfg: dict[str, Any]) -> str:
    interp = interpreter(cfg)
    source = str(_cfg(cfg, "source_obj"))
    texture = str(_cfg(cfg, "source_texture"))
    lines = [
        "贴图转色(tex2pp)诊断",
        f"外部解释器:{interp}",
        f"依赖检查:{dependencies(interp)}",
        f"管线脚本:{pipeline_path()}",
        f"工作目录:{work_dir(cfg)}",
        f"记住的低模:{source or '(还没记,勾上①)'}",
        f"   记住的模型:{_cfg(cfg, 'source_location') or '(未记录位置)'}"
        f"{' —— ' + ('当前这个就是它' if is_current_remembered(cfg) else '当前这个不是它') if _cfg(cfg, 'source_location') else ''}",
        f"   当前模型位置:{json.dumps(current_location(), ensure_ascii=False)}",
        f"       贴图:{texture or '(没找到;低模导出时 ZBrush 会顺手写贴图)'}",
        f"   实际用哪张:{effective_texture(cfg) or '(无)'}"
        f"({texture_role(cfg, effective_texture(cfg))})",
        f"   检测结果:{_cfg(cfg, 'texture_alpha_stats') or '(还没检测)'}"
        f" → {'有可用的透明信息' if _cfg(cfg, 'texture_has_alpha') else '没有可用的透明信息'}"
        f"(alpha 由你在导入前自行处理)",
        f"   仅限Alpha {'开' if _cfg(cfg, 'alpha_only') else '关'}  "
        f"边缘抗锯齿 {'开' if _cfg(cfg, 'aa') else '关'}"
        f"(半径 {float(_cfg(cfg, 'aa_radius')):g}px,{int(_cfg(cfg, 'aa_samples'))}x"
        f"{int(_cfg(cfg, 'aa_samples'))} 超采样)",
        f"参数(面板):阈值 {float(_cfg(cfg, 'max_dist_ratio')) * 100:g}%  "
        f"UV翻转 {'开' if _cfg(cfg, 'flip_v') else '关'}",
        f"参数(隐藏,改 settings.json):三角化 {_cfg(cfg, 'triangulate')}  "
        f"alpha_only {_cfg(cfg, 'alpha_only')}  aa {_cfg(cfg, 'aa')}"
        f"(半径 {float(_cfg(cfg, 'aa_radius')):g}px × {int(_cfg(cfg, 'aa_samples'))})"
        f"{'  ← 注意:aa 是开着的,会让颜色在 UV 邻域被轻微平滑' if _cfg(cfg, 'aa') else ''}",
        f"导入:最多在脚本里等 {float(_cfg(cfg, 'import_wait_s')):g}s"
        f"(脚本跑的时候 ZBrush 界面被占住,所以只等提交,不等导入跑完)",
    ]
    for path in (source, texture):
        if path and os.path.exists(path):
            lines.append(f"  {os.path.basename(path)} = {os.path.getsize(path) / 1024 / 1024:.1f} MB")
    for name in sorted(os.listdir(work_dir(cfg))):
        lines.append(f"  工作目录: {name}")
    lines.append("ZBrush 侧要点:导出/导入都靠 set_next_filename + press(press 是命令队列,要轮询);"
                 "导入会占用当前 Tool 槽。")
    return "\n".join(lines)
