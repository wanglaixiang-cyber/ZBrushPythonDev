"""功能:贴图转色(把低模的贴图经 UV **投射成高模的 polypaint**)。

面板:
  ① 记住低模        点中「有 UV + 贴图」的那个 SubTool → 导出它 + 找出它的贴图
  ② 烘焙并导回      点中高模(DynaMesh)那个 → 导出高模 → 外部按高模顶点采样贴图
                    → 写 PLY → **直接导回** ZBrush(变成 polypaint)
                    (导入本身由 ZBrush 自己跑,大网格会不响应一会儿)
  投射阈值         最近点距离超过包围盒对角线这个比例就记为「未被覆盖」(百分比)
  诊断信息          解释器 / 依赖 / 路径 / 参数 / 工作目录,复制到剪贴板

(想先把贴图变成**深度图**再用?那是**另一个独立功能**「图片转深度图」——
  它和这里的①②无关,自己读选中模型的贴图、自己把结果送回 ZBrush。)

两个"隐藏开关"(不上面板,需要时改 settings.json):
  ``tex2pp__flip_v``      默认 true。导回后颜色**上下颠倒**时才关掉重烘
  ``tex2pp__triangulate`` 默认 false(保留四边面)。只有排障时才需要全三角化

为什么必须走外部管线(而不是用原生 Polypaint From Texture):
  ZBrush 原生那条是**逐顶点**采样贴图,官方因此要求「把模型细分到接近贴图像素数」;
  低模顶点太少只能得到色块。本功能把采样搬到**高模自己的顶点**上(百万级),
  所以既不用细分低模,也不用给高模做 UV。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from ... import ui
from ...errors import PluginError
from . import service

#: 面板标签(每段不能含 ASCII 冒号)
LABELS: dict[str, str] = {
    "remember": "① 记住低模",
    "flip_v": "UV 翻转",
    "bake": "② 烘焙并导回",
    "max_dist": "投射阈值",
    "diag": "诊断信息",
}

#: 一行放得下两个就用 HALF + HALF(width 是相对宽度,两个 0.5 占满一行);
#: 单独占一行的用 FULL。官方 GUI 示例建议**显式写死**,别交给 ZBrush 自动排版(会排错)。
HALF, FULL = 0.5, 1.0


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    build: Callable[[ui.Panel, dict[str, Any]], None]


def paths(plugin_title: str = "ZBTools") -> dict[str, str]:
    """该功能在面板里的全部 item path(供测试与文档引用)。"""
    return {k: f"{plugin_title}:{FEATURE.title}:{v}" for k, v in LABELS.items()}


def _mesh_counts() -> tuple[int, int]:
    """当前 SubTool 的点数/面数(读不到就返回 -1)。"""
    try:
        return int(service.zbc.query_mesh3d(0)[0]), int(service.zbc.query_mesh3d(1)[0])
    except Exception:                                                 # noqa: BLE001
        return -1, -1


def _refresh(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """按状态刷新控件:记住了低模(文件还在)→ ② 可用,否则灰掉。"""
    panel.set_enabled("bake", service.has_source(cfg))


# --------------------------------------------------------------------------- #
# ① 记住低模
# --------------------------------------------------------------------------- #
def _remember_low(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """① 的动作:把当前选中的模型当作低模导出并记住(同时只记一个,再按就换掉)。

    只做三件事:导出 → 找贴图 → 记录"是哪个模型"。成功只打控制台,出错才弹提示。
    """
    points, _faces = _mesh_counts()
    work = service.reset_work_dir(cfg)
    dest = os.path.join(work, "tex2pp_low.obj")
    tmp = os.path.join(work, "tex2pp_low_new.obj")      # 先导临时名,校验通过再落位
    print(f"[贴图转色] 导出低模 → {tmp}")
    prog = ui.Progress("贴图转色 · 记住低模", eta=30.0)
    prog.stage("导出低模(等 ZBrush 写盘)…", 0.05)
    prog.set_ceiling(0.9)
    service.export_active(tmp, cfg, on_tick=prog.creep)
    prog.done()

    # 没有 UV 的模型当不了低模(② 会拿不到 UV)。判据是**导出文件里有没有 vt 行**
    # —— 这是下游管线真正要吃的东西,比读 UV 框可靠。
    if not service.export_has_uv(tmp):
        service.discard_export(tmp)
        raise PluginError(
            "这个模型**没有 UV**,不能当低模。\n"
            "导出的 OBJ 里没有任何 vt 行 —— 按② 会因为「低模没有 UV」失败。\n"
            "请点中**带 UV + 贴图**的那个模型再按①(上一次记住的低模没有被改动)。")

    texture = service.adopt_export(tmp, dest)
    service.remember_source(cfg, dest, texture, points=points)
    # 检测 alpha 要看「实际用哪张」:如果用户已经用「选择贴图」指了带通道的原图,
    # 就别拿 ZBrush 的 24 位 BMP 去覆盖检测结果(否则再按一次①会把开关又灰回去)。
    effective = service.effective_texture(cfg)
    has_alpha = service.detect_alpha(cfg, effective)[0] if effective else False
    from_settings = bool(str(service.param(cfg, "texture_path")).strip())
    if from_settings:
        print(f"[贴图转色] 注意:当前用的是 settings.json 里指定的贴图(不是模型带出的那张):"
              f"{effective}")
    size = os.path.getsize(dest) / 1024 / 1024
    print(f"[贴图转色] 低模已记住:{dest}({size:.1f} MB,"
          f"{f'{points} 点' if points > 0 else '点数未知'})")
    if texture:
        print(f"[贴图转色] 找到贴图:{texture}")
        extra = ("贴图有可用的透明信息(alpha 交给你在导入前处理)。"
                 if has_alpha else
                 "贴图没有可用的透明信息(纯 RGB)。alpha 转换请你在导入前自行处理;"
                 "想换成自己处理好的那张图,可在 settings.json 里用 "
                 "tex2pp__texture_path 指定")
        print(f"[贴图转色] 已记住低模({points} 点,{size:.1f} MB);"
              f"贴图:{os.path.basename(effective or texture)}"
              + ("(settings.json 指定的)" if from_settings else "")
              + f" —— {extra}")
    else:
        print(f"[贴图转色] ⚠ 已记住低模({points} 点,{size:.1f} MB),但没找到贴图;"
              f"低模导出时 ZBrush 会顺手写出贴图(带 UV 的网格是 名.bmp + 名.mtl)。"
              f"没找到说明这个 SubTool 可能没 UV 或没挂贴图 —— ② 会失败")
    _refresh(panel, cfg)


# --------------------------------------------------------------------------- #
# ② 烘焙并导回
# --------------------------------------------------------------------------- #
def _bake_and_import(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    _refresh(panel, cfg)          # 记住状态可能变了(比如工作目录被清理),顺手刷一下 ②
    if not service.has_source(cfg):
        raise PluginError("还没记住低模 —— 先点中「有 UV + 贴图」的那个 SubTool,再按①。")
    if service.is_current_remembered(cfg):
        # 常见误操作:忘了切回高模,于是把低模的颜色烘到低模自己身上(结果看着像成功,其实没用)
        raise PluginError(
            "当前选中的**就是**被记住的那个低模。\n"
            "② 要在**高模**(DynaMesh)上执行:先点中高模那个 SubTool,再按②。")

    low = service.resolve_source(cfg)
    # ⚠ 这里必须用 effective_texture(而不是 source_texture):
    #   tex2pp__texture_path(隐藏兜底层)以前在这条路上**根本没生效**(真 bug)——
    #   导出时 ZBrush 会把 alpha 剥成 24 位 BMP,用户按文档指回原图却没用上。
    texture = service.effective_texture(cfg)
    if not texture or not os.path.exists(texture):
        texture = service.find_texture(low)
    if not texture or not os.path.exists(texture):
        raise PluginError(
            "找不到低模的贴图。\n低模导出时应由 ZBrush 顺手写出 名.bmp / 名_TXTR.tif;"
            "请确认那个 SubTool 真的挂了贴图,然后重新点①。")
    print(f"[贴图转色] 本次用的图:{texture}"
          f"({service.texture_role(cfg, texture)})")
    # 只有确实要用 alpha 时才花那几百毫秒去检测(平时没必要)
    alpha_fallback = False
    if service.param(cfg, "alpha_only"):
        service.detect_alpha(cfg, texture)
    if service.param(cfg, "alpha_only") and not service.param(cfg, "texture_has_alpha"):
        # 不直接失败(那会变成"② 莫名其妙不能用"):降级成 RGB 烘焙并说清楚原因
        service.set_param(cfg, "alpha_only", False)
        alpha_fallback = True
        print("[贴图转色] 「仅限Alpha」需要**可用的**透明通道,但这张贴图没有 —— "
              "本次按 RGB 烘焙,开关已关掉")

    high = os.path.join(service.work_dir(cfg), "tex2pp_high.obj")
    out_ply = os.path.join(service.work_dir(cfg), "tex2pp_polypaint.ply")
    points, _faces = _mesh_counts()
    print(f"[贴图转色] 导出高模(当前 SubTool,{points} 点)→ {high}")
    t0 = _now()
    prog = ui.Progress("贴图转色 · 烘焙", eta=90.0)
    prog.stage(f"导出高模({points} 点,界面暂时不响应)…", 0.02)
    prog.set_ceiling(0.12)
    service.export_active(high, cfg, on_tick=prog.creep)
    print(f"[贴图转色] 高模导出耗时 {_now() - t0:.1f}s")

    t0 = _now()
    report = service.bake(cfg, low, high, texture, out_ply,
                          progress=prog, base=0.12, span=0.82)
    print(f"[贴图转色] 烘焙耗时 {_now() - t0:.1f}s")
    vertices = int(report.get("vertices") or 0)
    median = float(report.get("median_distance") or 0.0)
    diagonal = float(report.get("diagonal") or 0.0)
    ratio = (median / diagonal * 100.0) if diagonal else 0.0
    colors = int(report.get("unique_colors") or 0)
    far = float(report.get("far_ratio") or 0.0) * 100.0
    print(f"[贴图转色] 烘焙完成:顶点 {vertices:,};最近点中位数 {median:.6g}"
          f"({ratio:.3f}% 对角线);颜色 {colors:,} 种;未被覆盖 {far:.2f}%")

    detail = [f"最近点中位数 {median:.6g}(对角线的 {ratio:.3f}%)",
              f"颜色 {colors:,} 种;未被覆盖 {far:.2f}%"]
    prog.stage("提交导入给 ZBrush…", 0.96)
    result = service.import_ply(out_ply, cfg)
    prog.done()
    print(f"[贴图转色] 导入提交耗时 {result.submitted_s:.1f}s(剩下的由 ZBrush 自己跑)")
    print(f"[贴图转色] 已提交导入:{result.name or '(名字未知)'}"
          + (f";{result.note}" if result.note else ""))
    print(f"[贴图转色] 烘焙完成:{vertices:,} 个高模顶点;{';'.join(detail)}")
    print(f"[贴图转色] ZBrush 接下来自己建 {vertices:,} 个顶点的网格,期间界面会不响应"
          f"(大网格几十秒到几分钟);跑完模型上就是 polypaint")
    print("[贴图转色] 提示:导入占用的是当前 Tool 槽;想并回原来的多 SubTool 工具,"
          "可用 Morph Target + Project All(几何回滚、只留颜色)")
    if alpha_fallback:
        print("[贴图转色] ⚠ 「仅限Alpha」已自动关闭(当前贴图没有可用的透明信息),"
              "本次按 RGB 烘焙")
    _refresh(panel, cfg)


def _now() -> float:
    import time
    return time.time()


def _diagnostics(cfg: dict[str, Any]) -> None:
    from ...zbapi import clipboard
    text = service.diagnostics_text(cfg)
    if clipboard.copy_to_clipboard(text):
        print("[贴图转色] 诊断信息已复制到剪贴板(Cmd+V 粘贴);内容如下:\n" + text)
    else:
        print(text)
        ui.note("警告:复制到剪贴板失败,请看 Python 控制台里的诊断输出")


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """挂控件,紧凑排布(一行两个):

        [① 记住低模      ] [UV 翻转         ]
        [② 烘焙并导回    ] [投射阈值 ▬▬▬     ]
        [诊断信息                          ]

    ② 一定包含导回;阈值是"未被覆盖"的判定阈值(占总对角线百分比)。
    """
    # 面板已删除的开关(alpha_only / aa)旧值必须在**装配时**就清掉:
    # 否则用户先按②、没按过①的话,那些旧值会继续影响烘焙(隐藏行为)。
    service.migrate_once(cfg)
    # ① 是**普通按钮**(和 ② 一样):按一下 = 把当前选中的这个模型当作低模导出并记住。
    # 刻意不做成开关:ZBrush 没有"选择变化"回调,切模型时开关状态没法自动更新,
    # 停在旧状态反而像 bug(踩过);按钮不显示状态就没有这个歧义 ——
    # "记住的是哪个模型"只在控制台/诊断信息里说明。
    panel.button("remember", LABELS["remember"],
                 "导出并记住当前低模(有 UV + 贴图那个);只记一个",
                 lambda _s: ui.guard("记住低模", lambda: _remember_low(panel, cfg)),
                 width=HALF)
    panel.switch("flip_v", LABELS["flip_v"],
                 bool(service.param(cfg, "flip_v")),
                 "导回后颜色上下颠倒时,关掉它再烘一次",
                 lambda _s, value: service.set_param(cfg, "flip_v", bool(value)),
                 width=HALF)
    panel.button("bake", LABELS["bake"],
                 "高模 → 采样低模贴图 → 导回 polypaint;占用当前 Tool 槽",
                 lambda _s: ui.guard("烘焙并导回", lambda: _bake_and_import(panel, cfg)),
                 width=HALF)
    panel.slider(
        "max_dist", LABELS["max_dist"],
        round(float(service.param(cfg, "max_dist_ratio")) * 100), 1, 0, 20,
        "超过「包围盒对角线 × 此比例」的顶点算未被覆盖(默认 2%)",
        lambda _s, value: service.set_param(cfg, "max_dist_ratio",
                                            round(float(value) / 100.0, 4)),
        width=HALF)
    panel.button("diag", LABELS["diag"],
                 "解释器 / 依赖 / 管线脚本 / 已记住的路径 / 参数",
                 lambda _s: ui.guard("诊断信息", lambda: _diagnostics(cfg)),
                 width=FULL)

    # 所有控件挂完之后才能 set_enabled(那时 key 才登记过)
    _refresh(panel, cfg)


FEATURE = Feature(key="tex2pp", title="贴图转色", build=build)
