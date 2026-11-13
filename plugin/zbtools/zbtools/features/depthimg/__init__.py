"""功能:图片转深度图(把**选中模型的贴图**转成灰度深度图,并送回 ZBrush)。

面板:
  转深度图        读出当前选中模型的贴图 → 转成灰度深度图 → 导入 ZBrush 的 Texture 面板
  替换模型贴图     开关(默认开):把导入的那张深度图挂到当前 SubTool 上(= 替换它的贴图);
                  关掉就**只追加**到 Texture 面板,不动模型
  UV 翻转          开关(默认开):输出前上下翻转 —— ZBrush 的图像自下而上存,原样导入会**颠倒**
  V2-S / V2-B / V2-L / V2-G
                  **Depth Anything V2 的四档规模**(ZBrush 没有 radio 控件,所以用一组互斥
                  开关做单选组):**一行四个小按钮**,点一个其它三个自动关掉。点它 =
                  引擎切回 ``dav2`` + 写 ``depthimg__variant``(vits/vitb/vitl/vitg)
  诊断信息        引擎 / 权重与许可 / 解释器 / 依赖 / 路径 / 参数,复制到剪贴板

和「贴图转色」的①②**没有关系**:这里只做"图 → 深度图"这一件事,拿到图之后怎么用
(Alpha / 遮罩 / 置换 / 贴图)由你自己决定。

**许可**(判据见 service.weight_license):这四档里只有 **V2-S** 是 Apache-2.0(可商用);
V2-B / V2-L / V2-G 是 CC-BY-NC-4.0 —— 默认**拒绝**,要在 settings.json 里把
``depthimg__allow_nc_weights`` 设成 true 才会用(那之后日志每次都会提醒"不可商用")。

**另外三个引擎没消失,只是不在面板上了**:``depthimg__engine`` 仍可写 ``da3``(Apache-2.0 变体,
几何更准)/ ``luma``(亮度当高度,零依赖)/ ``sdf``(距离场浮雕,平铺图案/贴花最对路)。
这时面板四格**全灭**(= 当前不是 V2 引擎),点任意一格就切回 V2 的那一档。
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
    "run": "转深度图",
    "replace": "替换模型贴图",
    "flip_v": "UV 翻转",
    "eng_v2s": "V2-S",
    "eng_v2b": "V2-B",
    "eng_v2l": "V2-L",
    "eng_v2g": "V2-G",
    "diag": "诊断信息",
}

#: 一行放两个(width 是相对宽度,两个 0.5 占满一行)
HALF = 0.5
#: 一行放四个(0.25 × 4 = 1.0):四档规模正好占一行
QUARTER = 0.25

#: 面板上那四格:(item key, 标签, 权重规模, 悬停提示)
#:
#: 选的是**同一批 safetensors 的四个规模**(面板上不做 fp16 / metric —— 见 README):
#:   * fp16:上游作者(Kijai)自己写着 "fp16 reduces quality by a LOT, not recommended"
#:   * metric_hypersim / metric_vkitti:按固定域标定的**米制**权重(室内 20m / 室外 80m),
#:     跨域跑会系统性错,而且这里只要"相对深度"(做浮雕/遮罩够用)
#:
#: ⚠ 悬停提示**必须短**(≤ ui.INFO_MAX):ZBrush 的气泡提示只有一行,而且官方明确说
#:   它的 Python 字符串连 `\n` / `\t` 都渲染不正确(见 sdk/docs/partials/
#:   api_zbrush_string_bug_backslash.html)—— 写长了就是被截断/糊在一起。
#:   耗时、许可、实测理由都在 README 里,别往这里塞(有测试钉住上限)。
VARIANT_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("eng_v2s", "V2-S", "vits", "Small,Apache-2.0,最快"),
    ("eng_v2b", "V2-B", "vitb", "Base,比 S 细一档"),
    ("eng_v2l", "V2-L", "vitl", "Large,质量/速度平衡点"),
    ("eng_v2g", "V2-G", "vitg", "Giant,最慢"),
)

#: 档位 key → 权重规模(单选组回调里用)
VARIANT_KEYS: dict[str, str] = {key: variant for key, _label, variant, _info in VARIANT_ROWS}


#: 单选组正在"程序化改值" —— 防止 zbc.set 又回调进来把我们刚关掉的那个打开
#: (ZBrush 的 set 会不会触发控件回调没有官方说明,所以这里防一手;实测两种行为都能正确收场)
_PICKING_ENGINE = False


def _selected_key(cfg: dict[str, Any]) -> str:
    """当前该点亮哪一格。

    空串 = 当前引擎**不是** dav2(引擎可以用 ``depthimg__engine`` 写成 da3/luma/sdf,
    那三个不在面板上)→ 四格全灭,如实表示"现在跑的不是 V2"。
    """
    if service.engine(cfg) != "dav2":
        return ""
    want = service.variant(cfg)
    for key, _label, variant, _info in VARIANT_ROWS:
        if variant == want:
            return key
    return VARIANT_ROWS[0][0]


def _pick_engine(panel: ui.Panel, cfg: dict[str, Any], key: str, value: bool) -> None:
    """单选组的回调:选中一档,把其它三格关掉。

    两种要防的情况:
      * 点**已经选中的**那个(value=0,因为开关被点了一下会翻)→ 把它按回去:单选按钮不该
        能被点灭,不然会出现"一档都没选"的状态;
      * 我们自己 `set` 其它开关时,万一 ZBrush 又回调进来 → 用重入标记直接忽略,
        否则会把刚关掉的那个重新打开(那样单选就永远切不过去了)。
    """
    global _PICKING_ENGINE
    if _PICKING_ENGINE:
        return
    wanted = VARIANT_KEYS.get(key, "")
    if not wanted:
        return
    if not value:
        # value=0 只会发生在"用户点了当前正亮着的那个"(单选不能点灭 → 按回去)。
        # 如果收到的是"没选中的那个被关掉",那就让它保持关着 —— 关键是**别让界面和配置对不上**。
        panel.set_value(key, 1.0 if _selected_key(cfg) == key else 0.0)
        return
    # 点任意一格 = 用 Depth Anything V2 的这一档(也把引擎从 da3/luma/sdf 切回来)
    service.set_variant(cfg, wanted)
    _PICKING_ENGINE = True
    try:
        panel.set_value(key, 1.0)      # 自己按下去(真机点一下本来就是 1,这里写死更确定)
        for other, _label, _variant, _info in VARIANT_ROWS:
            if other != key:
                panel.set_value(other, 0.0)
    finally:
        _PICKING_ENGINE = False
    print(f"[图片转深度图] DA V2 档位切成 {wanted}")


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    build: Callable[[ui.Panel, dict[str, Any]], None]


def paths(plugin_title: str = "ZBTools") -> dict[str, str]:
    """该功能在面板里的全部 item path(供测试与文档引用)。"""
    return {k: f"{plugin_title}:{FEATURE.title}:{v}" for k, v in LABELS.items()}


# --------------------------------------------------------------------------- #
# 「转深度图」
# --------------------------------------------------------------------------- #
def _to_depth(cfg: dict[str, Any]) -> None:
    """主流程:读模型贴图 → 算深度图 → 送回 ZBrush(默认替换模型贴图)。

    整条链路的进度映射(ZBrush 顶部状态条,实测在回调里能刷新 —— 见 ``ui.Progress``):
    ``0.00`` 读贴图(等 ZBrush 导出)→ ``0.15`` 外部管线(推理/后处理)→ ``0.95`` 送回 ZBrush → 清空。
    """
    name = service.engine(cfg)
    model, commercial, note = service.model_and_license(cfg)
    print(f"[图片转深度图] 引擎 {name};{('权重 ' + model) if model else note}")
    prog = ui.Progress(f"图片转深度图 · {name}", eta=25.0)
    prog.stage("读贴图:正在导出模型(界面暂时不响应)…", 0.01)
    prog.set_ceiling(0.14)
    _obj, texture = service.read_model_texture(cfg, on_tick=prog.creep)
    if not texture or not os.path.exists(texture):
        prog.done()
        raise PluginError(
            "读不到这个模型的贴图。\n"
            "插件是**导出当前选中的 SubTool** 来读贴图的(ZBrush 会把贴图写在 OBJ 旁边)——"
            "所以请先点中那个**带 UV + 挂了贴图**的模型;"
            "只有高模(DynaMesh)没有 UV 的话,它导不出贴图。")
    print(f"[图片转深度图] 读到贴图:{texture}"
          f"({os.path.getsize(texture) / 1024 / 1024:.2f} MB)")

    # 输出文件名:带"这个模型的贴图名 + 引擎(含档位) + 内容哈希"。
    # 两个理由:① 换模型/换引擎/换档位时名字不同,一眼能看出是新的;
    #          ② **ZBrush 的 Texture 面板按名字去重**(同名会用旧的那张),所以名字必须能区分内容
    tag = service.texture_tag(cfg)
    engine_name = service.engine_label(cfg)
    out = os.path.join(service.work_dir(cfg),
                       f"depthimg_depth{tag}_{engine_name}.png")
    # 先把上一次的深度图删掉:万一这次失败,别让工作目录里留着一张"看着像新结果"的旧图
    # (实测踩过:换模型后其实报错了,但目录里还是上一个模型那张图,看着就像"生成错了")
    for stale in (out, out + ".report.json", os.path.splitext(out)[0] + "_16bit.tif"):
        if stale and os.path.exists(str(stale)):
            try:
                os.remove(str(stale))
            except OSError:
                pass
    report = service.run_depth(cfg, texture, out, progress=prog, base=0.15, span=0.80)
    if not os.path.exists(out):
        prog.done()
        raise PluginError(f"深度图没有生成出来:{out}")
    print(f"[图片转深度图] 深度图已生成:{out}"
          f"({os.path.getsize(out) / 1024:.0f} KB);{service.summarize(report)}")
    # 先按内容改名(再送回 ZBrush,否则同名会被 ZBrush 用旧的那张)
    renamed = service.uniquify_output(out)
    if renamed != out:
        print(f"[图片转深度图] 结果改名(名字带内容短码,避免 ZBrush 面板同名去重):"
              f"{os.path.basename(renamed)}")
        out = renamed
        report["out"] = renamed
    service.set_param(cfg, "last_depth", out)
    high = str(report.get("high_precision_path") or "")
    if high and not os.path.exists(high):
        # 16 位那张也跟着改名了,重新定位
        candidate = os.path.splitext(out)[0] + "_16bit.tif"
        high = candidate if os.path.exists(candidate) else ""
    if high:
        # 8 位 PNG 只有 256 级,做浮雕会出现台阶 —— 16 位那张才是给位移/置换用的
        print(f"[图片转深度图] 16 位精度版(给 ZBrush 做位移/置换,浮雕不会出台阶):{high}"
              f"({os.path.getsize(high) / 1024 / 1024:.1f} MB)")

    size = report.get("out_size") or [0, 0]
    prog.stage("送回 ZBrush(导入 + 挂到 SubTool)…", 0.97)
    assigned, how = service.send_to_zbrush(cfg, out, (int(size[0]), int(size[1])))
    service.set_param(cfg, "last_assign", how)
    print(f"[图片转深度图] 送回 ZBrush:{how}")
    if assigned:
        print("[图片转深度图] 模型贴图已换成这张深度图(想拿回原来的贴图:"
              "在 Texture 面板里点回原来那张,或重新导入)")
    elif bool(service.param(cfg, "replace_texture")):
        print("[图片转深度图] ⚠ 没能自动把贴图挂到模型上 —— 手工补一下:"
              "选中 SubTool 后,在 Tool > Texture Map 里点一下贴图方块"
              "(深度图已经在 Texture 面板里了)")
        ui.note("提示:深度图已生成并导入 Texture 面板,但没能自动挂到模型上。\n"
                f"手工做法:选中 SubTool → Tool > Texture Map → 点那个贴图方块。\n"
                f"文件:{out}")
    else:
        print("[图片转深度图] 按设置**没有**替换模型贴图:它现在只是 Texture 面板里的一张图")
    print(f"[图片转深度图] 这张 PNG 也可以直接当 Alpha / 置换 / 遮罩用:{out}"
          "(亮 = 近/高;想反过来把 depthimg__invert 设成 true 再按一次)")
    prog.done()


def _diagnostics(cfg: dict[str, Any]) -> None:
    from ...zbapi import clipboard
    text = service.diagnostics_text(cfg)
    if clipboard.copy_to_clipboard(text):
        print("[图片转深度图] 诊断信息已复制到剪贴板(Cmd+V 粘贴);内容如下:\n" + text)
    else:
        print(text)
        ui.note("警告:复制到剪贴板失败,请看 Python 控制台里的诊断输出")


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """挂控件:

        [转深度图        ] [替换模型贴图 (开关) ]
        [UV 翻转 (开关)  ] [诊断信息           ]
        [V2-S] [V2-B] [V2-L] [V2-G]     ← DA V2 四档规模,一行四个(单选组)

    四格是**单选组**(四个互斥开关):点一个,其它自动关掉;点它就是"用这一档权重"。
    当前引擎被配置写成 da3/luma/sdf 时,四格全灭(那三个引擎不在面板上)。
    """
    panel.button("run", LABELS["run"],
                 "选中带 UV + 贴图的模型 → 深度图 → 送回 ZBrush(出错才弹提示)",
                 lambda _s: ui.guard("转深度图", lambda: _to_depth(cfg)),
                 width=HALF)
    panel.switch("replace", LABELS["replace"],
                 bool(service.param(cfg, "replace_texture")),
                 "开 = 替换当前 SubTool 的贴图;关 = 只进 Texture 面板",
                 lambda _s, value: service.set_param(cfg, "replace_texture", bool(value)),
                 width=HALF)
    panel.switch("flip_v", LABELS["flip_v"],
                 bool(service.param(cfg, "flip_v")),
                 "输出前上下翻转(ZBrush 图像自下而上存,不翻会颠倒)",
                 lambda _s, value: service.set_param(cfg, "flip_v", bool(value)),
                 width=HALF)
    panel.button("diag", LABELS["diag"],
                 "引擎 / 权重与许可 / 解释器 / 依赖 / 参数",
                 lambda _s: ui.guard("诊断信息", lambda: _diagnostics(cfg)),
                 width=HALF)
    # DA V2 四档规模:**一行四个小按钮**(0.25 × 4 = 1.0);点一个就把其它三个关掉
    current = _selected_key(cfg)
    for key, label, _variant, info in VARIANT_ROWS:
        panel.switch(key, label, current == key,
                     f"{info}。单选:点它 = 用这一档",
                     lambda _s, value, k=key: _pick_engine(panel, cfg, k, bool(value)),
                     width=QUARTER)


FEATURE = Feature(key="depthimg", title="图片转深度图", build=build)
