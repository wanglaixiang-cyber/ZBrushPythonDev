"""配置与状态持久化(插件级)。

注意:ZBrush 的 Python VM 里工作目录是 ZBrush 应用目录(不是脚本所在目录),
所以所有路径都必须基于 ``__file__`` 计算,不能用相对路径。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 用户状态目录(与 ~/.zbtools/models、~/.zbtools_rulers 同一套约定)。
#: 可以用 ZBTOOLS_HOME 覆盖(测试/便携安装用)。
STATE_HOME = os.environ.get("ZBTOOLS_HOME") or os.path.join(os.path.expanduser("~"), ".zbtools")
#: 配置放**用户目录**,不放插件目录:插件目录可能只读(Program Files、root 拥有的
#: /Applications),而且发行包升级要整目录替换 —— 配置跟着包走会被覆盖或写不进去。
_DEFAULT_CONFIG_PATH = os.path.join(STATE_HOME, "settings.json")
CONFIG_PATH = os.environ.get("ZBTOOLS_CONFIG") or _DEFAULT_CONFIG_PATH
#: 0.2.0 及以前的配置写在包内;首次启动时自动迁移(旧文件保留,方便回滚)
LEGACY_CONFIG_PATH = os.path.join(_HERE, "settings.json")
_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # 开发态 = 仓库根;发行态 = 插件目录的上级

#: 外部工具脚本(显示器探测 / 屏幕真尺 / 烘焙管线)要用的解释器。
#: ⚠ **绝不能**让这个默认值回落到 ``sys.executable`` —— 在 ZBrush 的内嵌 VM 里它指向
#: ZBrush 自己的可执行文件(官方文档原文:"sys.executable points to ZBrush.exe or
#: ZBrushMac.app"),拿它当解释器只会在子进程里再启动一个 ZBrush。
_PYTHON_ENV = ("ZBTOOLS_PYTHON", "ZBRUSH_PYTHON")
_PYTHON_NAMES = ("python3", "python")


def _is_real_python(path: str) -> bool:
    """这个路径是不是一个 Python 解释器(而不是 ZBrush 自己的可执行文件)。

    只认**文件名里带 python** 的可执行文件:``python`` / ``python3`` / ``python.exe``
    (mac 框架版解析符号链接后是 ``.../Python.app/Contents/MacOS/Python``,也满足)。
    ZBrush 的 ``ZBrush`` / ``ZBrush.exe`` 因此不可能被当成解释器 —— 官方文档写明
    内嵌 VM 里 ``sys.executable`` 就指向它,拿它当解释器只会再启动一个 ZBrush。
    """
    if not path:
        return False
    try:
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            return False
        name = os.path.basename(real).lower()
        return "python" in name and "zbrush" not in name
    except Exception:                                           # noqa: BLE001
        return False


def _default_interpreter() -> str:
    """给外部工具脚本挑一个可用的 Python(发行版里没有项目 venv)。

    顺序:环境变量 → 项目 venv(开发态)→ PATH 里的 python3 / python。
    全都没有就返回空字符串 —— 调用方会退到手动参数模式(或给出可读的报错),
    总比拿 ZBrush 自己的可执行文件当好解释器强。
    """
    for env_name in _PYTHON_ENV:
        value = os.environ.get(env_name, "").strip()
        if _is_real_python(value):
            return value
    for candidate in (os.path.join(_PROJECT, ".venv", "bin", "python"),
                      os.path.join(_PROJECT, ".venv", "Scripts", "python.exe")):
        if _is_real_python(candidate):
            return candidate
    for name in _PYTHON_NAMES:
        found = shutil.which(name)
        if found and _is_real_python(found):
            return found
    return ""

#: 逻辑版本:变了就清掉 1:1 记账(避免迭代期间留下的旧状态污染新逻辑)
STATE_VERSION = 13

#: 记账相关的键
_STATE_KEYS = ("intrinsic_g", "intrinsic_xyz", "raw_at_apply", "eff_at_apply", "last_applied_scale")

#: 功能级配置键的写法:<feature_key>__(见 feature_prefix)
#: ⚠ 曾经只保留 DEFAULTS 里的键,于是功能级配置(如 tex2pp 记住的低模路径)
#:   存进 settings.json 后在**下次启动被静默丢掉** —— 实测踩到,所以这里按前缀放行。
_FEATURE_KEY = re.compile(r"^[a-z][a-z0-9]*__")

#: 默认配置。
DEFAULTS: dict[str, Any] = {
    # --- 屏幕 ---
    # 0 = 跟随 ZBrush 窗口;也可填具体 display id(诊断里会列出)
    "display_id": 0,
    # 屏幕参数(自动探测会填好,也可以手动改;拖动滑块即切手动模式)
    "ppi": 162.5,               # 物理像素 / 英寸
    # pixol -> 物理像素 的倍率(2 = 2x 缩放屏 / Retina;1 = 未缩放屏)。
    # 自动探测时按 原生像素/逻辑点 得出,一般不用改。
    "pixel_scale": 2.0,
    # 手动模式(自动探测失败时用):对角线英寸 + 原生像素 + 逻辑点
    "diagonal_inch": 27.0,
    "native_px": [3840, 2160],
    "logical_pt": [1920, 1080],
    # ZBrush 渲染桥接项(pixol -> 物理像素)的核对系数。
    # 这一项 API 不暴露,只能确认一次;默认 1.0 = 按逻辑点绘制。
    "bridge_factor": 1.0,

    # 跑外部工具脚本的解释器(显示器探测要 pyobjc;屏幕真尺要 AppKit;烘焙管线要 numpy 等)。
    # 自动挑:ZBTOOLS_PYTHON / ZBRUSH_PYTHON 环境变量 → 项目 .venv → PATH 里的 python3/python;
    # 发行版里通常要靠环境变量或 settings.json 手动指定(见 INSTALL.md)。
    "interpreter": _default_interpreter(),

    # --- 模型 / 视图 ---
    # 【模型单位】面板上用 mm / cm / in 三个开关直接切换。
    # 含义:1 个 ZBrush unit = 1 个该单位。官方文档原文:
    # "ZBrush does not natively work with units."(ZBrush 单位无量纲,单位由你定)
    # 尺寸口径:1 ZBrush unit = 1 mm(与 Scale Master 选哪个单位无关)
    "mm_per_unit": 1.0,
    # 显示单位:提示里的数值按它换算(Scale Master 选哪个就用哪个)
    "display_unit": "mm",
    # 模型真实尺寸优先取 ZBrush 自己的报告(Scale Master 刷新后的读数),
    # 它能算进"缩放被藏到子工具/网格里"的情况(例如点过 ZBrush Scale Unify 之后)
    "read_size_from_scalemaster": True,

    # 记账状态版本:变了就清掉旧的 1:1 记账
    "state_version": STATE_VERSION,
    # 记账:模型固有尺寸(unit)与上次设置的画布 scale —— 用来识别 Scale Unify 的烘焙
    "intrinsic_g": None,
    "intrinsic_xyz": None,
    "raw_at_apply": None,
    "eff_at_apply": None,
    "last_applied_scale": None,
    # 尺寸以 ZBrush 自己的报告(Scale Master 读数)为准:用户的缩放可能落在
    # "子工具自身的变换"里,query_mesh3d 读不到,只有 ZBrush 的报告能看到
    # 提示窗口模板:花括号里是变量名(可用变量见「诊断信息」输出或 README)
    # 空字符串行会被忽略;未知变量原样保留
    # 提示窗口的布局(可自行改;变量见 core.note_values,{basis}/{fits} 空时自动跳过)
    # 显示实际大小时是否把模型移到画布中心(默认否:保持模型当前位置,避免"飘走")
    "center_model": False,
    # ZBrush transform scale 的可用上限(超过就提示先做 ZBrush Scale Unify)
    "max_canvas_scale": 4000.0,
    "note_template": [
        "真实尺寸:{x} x {y} x {z} {unit}",
        "屏幕 {screen} {inch}\" {ppi}ppi",
        "画布边框 {canvas_mm}mm",
        "{basis}",
        "{perspective}",
        "{fits}",
        "{scale_warning}",
    ],
    # 每次「显示实际大小」前重新探测屏幕(换屏/改缩放能自动跟上)
    "auto_redetect_screen": True,
    # 是否允许按下 Scale Master 的刷新按钮(默认否:只读,避免副作用)
    "press_scalemaster_refresh": False,
    # 包围盒模式:1 = 当前选中 SubTool 完整包围盒;3 = 全部 SubTool
    "bounds_index": 1,
    # 透视开关的 item path(只用来"提示"当前是透视还是正交,不再强制改动)
    "perspective_path": "Draw:Perspective",
    # 居中偏移的 Y 方向修正。若模型明显偏离画布中心,把这项切成 True。
    "flip_y": False,
    # 界面标签用纯 ASCII(排查中文 item path 兼容性时切 true)
    "ascii_labels": False,
}

def load() -> dict[str, Any]:
    """读取配置;文件不存在、损坏或版本变化时回落到默认值。

    保留两类键:插件级(在 DEFAULTS 里)与功能级(``<feature>__`` 前缀)。
    其余未知键丢掉,避免配置文件被历史垃圾撑大。

    0.2.0 的配置在插件目录里(``<包>/zbtools/settings.json``);如果新位置还没有
    配置、旧位置有,就读旧的并**自动写到新位置**(旧文件不删,便于回滚)。
    """
    cfg = dict(DEFAULTS)
    source = CONFIG_PATH
    migrate = False
    if (not os.path.exists(source) and CONFIG_PATH == _DEFAULT_CONFIG_PATH
            and os.path.exists(LEGACY_CONFIG_PATH)):
        source = LEGACY_CONFIG_PATH
        migrate = True
    try:
        with open(source, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            if int(stored.get("state_version") or 0) != STATE_VERSION:
                print("[ZBTools] 逻辑版本变化:已清掉旧的 1:1 记账状态")
                for key in _STATE_KEYS:
                    stored.pop(key, None)
            cfg.update({k: v for k, v in stored.items()
                        if k in DEFAULTS or _FEATURE_KEY.match(k)})
            cfg["state_version"] = STATE_VERSION
            if migrate:
                save(cfg)
                print(f"[ZBTools] 已把包内旧配置迁移到 {CONFIG_PATH}(旧文件保留,可手动删)")
    except FileNotFoundError:
        pass
    except Exception as exc:  # 配置损坏不应导致插件不可用
        print(f"[ZBTools] 配置读取失败,使用默认值:{exc}")
    return cfg

def save(cfg: dict[str, Any]) -> None:
    """写回配置(目录不存在就建;失败只打日志,不让插件崩)。"""
    try:
        parent = os.path.dirname(CONFIG_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"[ZBTools] 配置保存失败({CONFIG_PATH}):{exc}")


# --------------------------------------------------------------------------- #
# 约定(给后续新增功能)
# --------------------------------------------------------------------------- #
#: 功能自己的配置键请统一加前缀 "<feature_key>__",避免不同功能互相踩。
#: 例:true_scale 的 center_model 写成 "true_scale__center_model"。
#: tests/test_architecture.py 会检查每个功能声明的 config_prefix 是否被遵守。
def feature_prefix(feature_key: str) -> str:
    """返回该功能的配置键前缀。"""
    return f"{feature_key}__"
