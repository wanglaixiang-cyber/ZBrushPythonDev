"""「图片转深度图」(depthimg)的本地测试(不开 ZBrush)。

覆盖三块:
  1. **许可**:只留可商用的引擎/权重 —— vits(Apache-2.0)与 DA3 的 Apache 变体放行,
     vitb/vitl/vitg(CC-BY-NC-4.0)、DA3-LARGE / Giant / Nested 拒绝(除非显式放开)
  2. **插件契约**:FEATURE(key/title/build)、配置前缀 depthimg__、面板布局、
     开关语义(替换模型贴图 / 引擎 DA3)、诊断
  3. **隐患防护**:
     * 导出文件名固定 → 必须**先清掉上一次的模型/贴图**,否则会拿到别的模型的贴图(隐蔽错误)
     * 没有 UV 的模型(导出里没有 vt 行)直接报错,别拿 ZBrush 写的 _TXTR.tif 去算深度
     * ZBrush 侧没有贴图 API → 导入/挂载都是"候选路径 + exists() 探测",探测不到要说清楚
     * 外部管线的真子进程 CLI 契约(尺寸/灰度/反相/缺依赖报错/DA3 轻装兜底)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import numpy as np

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(PROJECT, "plugin", "zbtools")
FEATURE_DIR = os.path.join(PLUGIN, "zbtools", "features", "depthimg")
PIPELINE = os.path.join(FEATURE_DIR, "tools", "depth_pipeline.py")
VENDORED = os.path.join(FEATURE_DIR, "tools", "vendored")
sys.path.insert(0, os.path.join(PROJECT, "tests"))
sys.path.insert(0, PLUGIN)

# 管线脚本按路径加载(spec.loader 之后可以直接用它里面的纯函数)
import importlib.util                                              # noqa: E402
_spec = importlib.util.spec_from_file_location("depth_pipeline_module", PIPELINE)
depth_pipeline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(depth_pipeline)

FAILED: list[str] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {what}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(what)


class _StubPanel:
    """假面板:只记录 set_enabled 调用(这个功能不需要灰控件)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def set_enabled(self, key: str, value: bool) -> None:
        self.calls.append((key, value))


def make_texture(path: str, size: int = 48) -> np.ndarray:
    """非对称图案(四个象限四色),用来验证深度图确实由这张图算出来。"""
    from PIL import Image                                              # noqa: PLC0415
    img = np.zeros((size, size, 3), dtype=np.uint8)
    half = size // 2
    img[:half, :half] = (255, 0, 0)
    img[:half, half:] = (0, 255, 0)
    img[half:, :half] = (0, 0, 255)
    img[half:, half:] = (255, 255, 0)
    Image.fromarray(img).save(path)
    return img


def write_obj(path: str, uvs: bool = True) -> None:
    """写一个带/不带 UV 的最小 OBJ。"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("v 0 0 0\nv 1 0 0\nv 0 1 0\n")
        if uvs:
            handle.write("vt 0 0\nvt 1 0\nvt 0 1\n")
            handle.write("f 1/1 2/2 3/3\n")
        else:
            handle.write("f 1 2 3\n")


# --------------------------------------------------------------------------- #
# 许可:只留可商用的
# --------------------------------------------------------------------------- #
def check_licenses() -> None:
    print("* 许可:只留可商用的引擎/权重")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.depthimg import service                     # noqa: PLC0415

    commercial = ["depth_anything_v2_vits_fp32.safetensors",
                  "depth_anything_v2_vits_fp16.safetensors"]
    nc = ["depth_anything_v2_vitb_fp32.safetensors",
          "depth_anything_v2_vitl_fp32.safetensors",
          "depth_anything_v2_vitg_fp32.safetensors",
          "depth_anything_v2_metric_hypersim_vitl_fp32.safetensors"]
    for name in commercial:
        ok, note = service.weight_license(name)
        check(f"可商用:{name}(Apache-2.0)", ok, note)
    for name in nc:
        ok, note = service.weight_license(name)
        check(f"拒绝:{name}(CC-BY-NC-4.0)", not ok, note)
    for repo in ("depth-anything/DA3MONO-LARGE", "depth-anything/DA3-SMALL",
                 "depth-anything/DA3-BASE", "depth-anything/DA3METRIC-LARGE"):
        ok, note = service.weight_license(repo, "da3")
        check(f"可商用:{repo}", ok, note)
    for repo in ("depth-anything/DA3-LARGE", "depth-anything/DA3-LARGE-1.1",
                 "depth-anything/DA3NESTED-GIANT-LARGE-1.1"):
        ok, note = service.weight_license(repo, "da3")
        check(f"拒绝:{repo}(CC-BY-NC-4.0)", not ok, note)

    # 自动找权重:优先可商用的 vits,并且**不会**挑 NC 规模
    tmp = tempfile.mkdtemp(prefix="depthimg_lic_")
    models = os.path.join(tmp, "models")
    os.makedirs(models, exist_ok=True)
    nc_path = os.path.join(models, "depth_anything_v2_vitl_fp32.safetensors")
    small_path = os.path.join(models, "depth_anything_v2_vits_fp32.safetensors")
    for path in (nc_path, small_path):
        with open(path, "wb") as handle:
            handle.write(b"\0")
    real_globs = service._MODEL_GLOBS
    real_comfy = service.find_comfy
    service._MODEL_GLOBS = (models,)
    service.find_comfy = lambda _cfg: ""          # 别去搜本机 ComfyUI 的权重目录(测试要可复现)
    try:
        cfg: dict = {}
        check("find_model 优先挑可商用的 vits(即使 vitl 也在)",
              service.find_model(cfg) == small_path, service.find_model(cfg))
        os.remove(small_path)
        check("只剩 NC 权重时 find_model 不给(不静默用不可商用的)",
              service.find_model(cfg) == "", service.find_model(cfg))
        check("错误信息里会点名找到的 NC 权重", "vitl" in service.nc_weights_found(cfg),
              service.nc_weights_found(cfg))
        deny: dict = {}
        try:
            service.run_depth(deny, "in.png", os.path.join(tmp, "o.png"))
            check("没有权重时 run_depth 报错", False, "没抛错")
        except Exception as exc:                                     # noqa: BLE001
            text = str(exc)
            check("没有权重时给出可商用的下载办法",
                  "Small" in text and "Apache-2.0" in text, text[:120])
        # fp16 与 fp32 同时存在时,必须挑 fp32(Kijai 自己说 fp16 质量掉得多;
        # 而文件名排序里 fp16 排在前面 —— 不显式排就会悄悄用上 fp16)
        fp16_path = os.path.join(models, "depth_anything_v2_vits_fp16.safetensors")
        with open(fp16_path, "wb") as handle:
            handle.write(b"\0")
        with open(small_path, "wb") as handle:
            handle.write(b"\0")
        check("同规格 fp16/fp32 都在时,挑 **fp32**",
              service.find_model({}) == small_path, service.find_model({}))
        os.remove(small_path)
        check("只剩 fp16 时会用它,但说明里提醒换 fp32",
              service.find_model({}) == fp16_path
              and "fp16" in service.model_and_license({})[2],
              service.model_and_license({})[2][:60])
        os.remove(fp16_path)

        allow: dict = {"depthimg__model": nc_path, "depthimg__allow_nc_weights": True}
        check("显式放开后允许用 NC 权重(自己用)",
              service.find_model(allow) == nc_path, service.find_model(allow))

        # 面板上那四格 = **四档规模**:放开 NC 之后"写哪一档就挑哪一档"
        with open(small_path, "wb") as handle:      # 上面为了测 fp16 把它删过,补回来
            handle.write(b"\0")
        vitg_path = os.path.join(models, "depth_anything_v2_vitg_fp32.safetensors")
        with open(vitg_path, "wb") as handle:
            handle.write(b"\0")
        for name, path in (("vits", small_path), ("vitl", nc_path), ("vitg", vitg_path)):
            pick = {"depthimg__variant": name, "depthimg__allow_nc_weights": True}
            check(f"档位 {name} → 挑 {os.path.basename(path)}",
                  service.find_model(pick) == path, service.find_model(pick))
        check("那一档不在磁盘上时,不会假装就是它(返回空,由 run_depth 报错)",
              service.find_model({"depthimg__variant": "vitb",
                                  "depthimg__allow_nc_weights": True}) == "",
              service.find_model({"depthimg__variant": "vitb",
                                  "depthimg__allow_nc_weights": True}))
    finally:
        service._MODEL_GLOBS = real_globs
        service.find_comfy = real_comfy


# --------------------------------------------------------------------------- #
# 档位严格性:面板选的是"规模" ⇒ 规模对不上必须报错(不许静默降级)
# --------------------------------------------------------------------------- #
def check_variant_strictness() -> None:
    """点了 V2-G 却跑 Small = 用户眼里的"换了档没反应"。

    面板上那四格从"四个引擎"改成"DA V2 四档规模"之后,"找不到就换别的规模凑合"就从
    "能用"变成了**危险的静默降级**。所以钉两条:
      1. 选中的那一档不在磁盘上 → `run_depth` 报错,并给出**这一档**的下载命令;
      2. metric(米制,按固定域标定)权重**一个都不自动挑**,即使它就在同一个目录里。
    """
    print("* 档位严格性:规模对不上要报错(不静默降级)+ metric 不自动挑")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.errors import PluginError                            # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_variant_")
    models = os.path.join(tmp, "models")
    os.makedirs(models, exist_ok=True)
    names = ("depth_anything_v2_vits_fp32.safetensors",
             "depth_anything_v2_vitl_fp32.safetensors",
             "depth_anything_v2_metric_hypersim_vitl_fp32.safetensors")
    for name in names:
        with open(os.path.join(models, name), "wb") as handle:
            handle.write(b"\0")
    real_globs, real_comfy = service._MODEL_GLOBS, service.find_comfy
    service._MODEL_GLOBS = (models,)
    service.find_comfy = lambda _cfg: ""          # 别去搜本机 ComfyUI 的权重目录(测试要可复现)
    allow = {"depthimg__allow_nc_weights": True}
    try:
        check("V2-L → 挑 vitl",
              os.path.basename(service.find_model({**allow, "depthimg__variant": "vitl"}))
              == "depth_anything_v2_vitl_fp32.safetensors",
              service.find_model({**allow, "depthimg__variant": "vitl"}))
        check("同目录里躺着 metric 权重也不自动挑它(要显式 depthimg__model 才用)",
              not service.is_metric_weight(
                  service.find_model({**allow, "depthimg__variant": "vitl"})),
              service.find_model({**allow, "depthimg__variant": "vitl"}))
        cfg = {**allow, "depthimg__variant": "vitb", "depthimg__interpreter": sys.executable}
        try:
            service.run_depth(cfg, "in.png", os.path.join(tmp, "o.png"))
            check("V2-B 没权重 → 报错(不拿别的规模凑合)", False, "没抛错")
        except PluginError as exc:
            text = str(exc)
            check("V2-B 没权重 → 报错并点名缺哪一档", "V2-B" in text and "vitb" in text,
                  text[:110])
            check("报错里给的是**这一档**的下载命令(不是 Small)",
                  "depth_anything_v2_vitb_fp32.safetensors" in text, text[-140:])
        # 面板选了 NC 档位、但没放开 allow_nc_weights:必须说"许可挡住了",
        # 不能像旧逻辑那样报"磁盘上找到/要用的是 vits"(那会让人以为 vitl 没下)
        try:
            service.run_depth({"depthimg__variant": "vitl"}, "in.png",
                              os.path.join(tmp, "o_nc.png"))
            check("选 V2-L 未放开许可 → 报错", False, "没抛错")
        except PluginError as exc_nc:
            text_nc = str(exc_nc)
            check("未放开许可的报错明说 allow_nc_weights(不再误报找到 Small)",
                  "allow_nc_weights" in text_nc and "vitl" in text_nc, text_nc[:140])

        explicit = os.path.join(models, names[0])
        note = service.variant_note({**allow, "depthimg__variant": "vitg"}, explicit)
        check("显式指定全路径 + 档位不一致 → 有一句提醒(但照用,不拦)",
              "V2-G" in note and "vits_fp32" in note, note[:100])
        check("档位与权重对得上时不提醒",
              service.variant_note({"depthimg__variant": "vits"}, explicit) == "")
        check("luma/sdf 这类零依赖引擎不做规模检查(它们不碰权重)",
              service.variant_note({**allow, "depthimg__engine": "sdf",
                                    "depthimg__variant": "vitg"}, explicit) == "")
    finally:
        service._MODEL_GLOBS, service.find_comfy = real_globs, real_comfy


# --------------------------------------------------------------------------- #
# 插件契约 / 面板
# --------------------------------------------------------------------------- #
def check_feature_contract() -> None:
    print("* 功能契约 / 配置前缀 / 面板布局")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    installed = fake_zbrush.install(fake)
    settings_mod = installed[-1]            # 回调写的是 app 自己的配置,所以要看它存下来的
    from zbtools.features import FEATURES                             # noqa: PLC0415
    keys = [f.key for f in FEATURES]
    check("depthimg 已在注册表里", "depthimg" in keys, str(keys))
    feat = next(f for f in FEATURES if f.key == "depthimg")
    check("显示名为「图片转深度图」", feat.title == "图片转深度图", feat.title)
    check("build 可调用", callable(feat.build))

    import zbtools.features.depthimg as mod                           # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    check("paths() 与 LABELS 数量一致", len(mod.paths()) == len(mod.LABELS))
    check("LABELS 每段都不含 ASCII 冒号", all(":" not in v for v in mod.LABELS.values()))
    check("面板 8 个控件(转深度图 / 替换 / UV 翻转 / DA V2 四档单选 / 诊断)",
          set(mod.LABELS) == {"run", "replace", "flip_v",
                              "eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g", "diag"},
          str(sorted(mod.LABELS)))
    check("配置前缀是 depthimg__", service.PREFIX == "depthimg__")
    check("默认替换模型贴图(开关默认开)", service.DEFAULTS["replace_texture"] is True)
    check("默认引擎是 dav2(面板四格里默认 V2-S)", service.DEFAULTS["engine"] == "dav2")
    check("默认档位是 vits(唯一可商用的规模)", service.DEFAULTS["variant"] == "vits")
    # 实测(同一输入三档):518 背景噪声 3.56/毛刺 11.15,自动(1274)4.66/11.79
    # → Small 模型提高分辨率只是把噪声也放大,默认留在训练尺寸 518
    check("推理分辨率默认 518(训练尺寸);0 才是自动",
          service.DEFAULTS["size"] == 518 and service.DEFAULTS["size_max"] == 1554,
          f"{service.DEFAULTS['size']} / {service.DEFAULTS['size_max']}")
    check("位深默认 8(够用不占地);16 需要时一行配置打开",
          service.DEFAULTS["bit_depth"] == 8, str(service.DEFAULTS["bit_depth"]))
    # 实测:两个 AI 引擎的短板不同 —— DA3 低分辨率会把细结构并进背景(纯黑块),V2-S 是太平滑
    check("DA3 分辨率默认自动(0):实测 504 → 1512 黑岛 16 处 → 0 处",
          service.DEFAULTS["da3_res"] == 0, str(service.DEFAULTS["da3_res"]))
    check("细节叠加默认 0(不叠):对带明暗的图/3D 渲染会变麻点(用户实测劣化)",
          service.DEFAULTS["detail"] == 0.0, str(service.DEFAULTS["detail"]))
    check("去极值默认温和(阈值 6,不是 4)",
          service.DEFAULTS["despike_robust"] == 6.0,
          str(service.DEFAULTS["despike_robust"]))
    check("去孤立极值默认开(DA3 的纯黑色块就是它)",
          service.DEFAULTS["despike"] is True)
    check("UV 翻转**默认开**(ZBrush 里上下颠倒)", service.DEFAULTS["flip_v"] is True)
    check("默认不放开 NC 权重", service.DEFAULTS["allow_nc_weights"] is False)
    check("引擎别名回落到 dav2", service.engine({"depthimg__engine": "乱写"}) == "dav2")
    cfg: dict = {}
    service.set_param(cfg, "engine", "sdf")
    check("set_param 带前缀写回", cfg.get("depthimg__engine") == "sdf")
    check("param 读得到", service.param(cfg, "engine") == "sdf")

    # ⚠ 前面的用例把 engine 改成过 sdf(写在同一个配置里),装配前先恢复默认,
    #   否则"默认引擎"这条断言会因为配置残留而误判
    service.set_param(cfg, "engine", service.DEFAULTS["engine"])
    import zbtools.app as app                                        # noqa: PLC0415
    app.register()
    widths: dict[str, object] = {}
    for kwargs_of in (fake.button_kwargs, fake.slider_kwargs, fake.switch_kwargs):
        for path, kwargs in kwargs_of.items():
            label = path.rsplit(":", 1)[-1]
            if "图片转深度图" in path and not label.startswith("----"):
                widths[label] = kwargs.get("width")
    check("8 个控件都登记到了", len(widths) == 8, str(sorted(widths)))
    check("前四个 0.5(一行两个);四档规模 0.25(**正好占一行**)",
          all(widths.get(mod.LABELS[k]) == 0.5
              for k in ("run", "replace", "flip_v", "diag"))
          and all(widths.get(mod.LABELS[k]) == 0.25
                  for k in ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")), str(widths))
    check("四档规模加起来的宽度 = 1.0(不会再换行)",
          abs(sum(widths.get(mod.LABELS[k]) for k in
                  ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")) - 1.0) < 1e-9,
          str(sum(widths.get(mod.LABELS[k]) for k in
                  ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g"))))
    check("标签短到能塞进 1/4 宽(V2-S / V2-B / V2-L / V2-G)",
          [mod.LABELS[k] for k in ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")]
          == ["V2-S", "V2-B", "V2-L", "V2-G"],
          str([mod.LABELS[k] for k in ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")]))
    order = [path.rsplit(":", 1)[-1] for path in fake.created_order
             if "图片转深度图" in path and not path.endswith("---- 图片转深度图 ----")]
    check("创建顺序 = 转深度图 | 替换 | UV 翻转 | 诊断 | DA V2 四档(单选组在最后一行)",
          order == [mod.LABELS["run"], mod.LABELS["replace"], mod.LABELS["flip_v"],
                    mod.LABELS["diag"], mod.LABELS["eng_v2s"], mod.LABELS["eng_v2b"],
                    mod.LABELS["eng_v2l"], mod.LABELS["eng_v2g"]], str(order))
    check("「转深度图」和「诊断信息」是按钮",
          mod.LABELS["run"] in {p.rsplit(":", 1)[-1] for p in fake.button_kwargs}
          and mod.LABELS["diag"] in {p.rsplit(":", 1)[-1] for p in fake.button_kwargs})

    # 开关语义:面板上那四格 = DA V2 的**档位**(用回调验证)
    callbacks = fake.button_kwargs
    switch_fns = {path.rsplit(":", 1)[-1]: fn
                  for path, fn in fake.switches.items() if "图片转深度图" in path}
    # 四格是**单选组**:四个互斥开关(点一个,其它三个被 set 成 0)
    engine_fns = {mod.LABELS[key]: switch_fns.get(mod.LABELS[key])
                  for key, _label, _variant, _info in mod.VARIANT_ROWS}
    check("四格规模开关都挂了回调",
          all(callable(fn) for fn in engine_fns.values()),
          str({k: callable(v) for k, v in engine_fns.items()}))
    check("默认亮 V2-S(vits,唯一可商用的规模),其它三格关着",
          fake.switch_values.get(mod.paths()["eng_v2s"]) == 1.0
          and all(fake.switch_values.get(mod.paths()[k]) == 0.0
                  for k in ("eng_v2b", "eng_v2l", "eng_v2g")),
          str({k: fake.switch_values.get(mod.paths()[k]) for k in
               ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")}))
    for key, label, want_variant, _info in mod.VARIANT_ROWS:
        fn = engine_fns[label]
        if not callable(fn):
            continue
        fake.set_calls.clear()
        fn("", 1.0)                     # 点这一档
        on = [k for k in ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")
              if fake.switch_values.get(mod.paths()[k]) == 1.0]
        saved = settings_mod.load()
        check(f"点「{label}」→ engine=dav2 + variant={want_variant},其它三格自动关掉",
              saved.get("depthimg__engine") == "dav2"
              and saved.get("depthimg__variant") == want_variant and on == [key],
              f"engine={saved.get('depthimg__engine')} "
              f"variant={saved.get('depthimg__variant')} 亮着={on}")

    # 引擎可以用配置写成 da3/luma/sdf(那三个不在面板上)→ 四格全灭,如实表示"现在不是 V2"
    check("引擎写成 sdf 时四格全灭(= 当前不是 V2 引擎)",
          mod._selected_key({"depthimg__engine": "sdf",
                             "depthimg__variant": "vitl"}) == "")
    check("_selected_key 跟着档位走(默认 → V2-S;vitg → V2-G)",
          mod._selected_key({}) == "eng_v2s"
          and mod._selected_key({"depthimg__engine": "dav2",
                                 "depthimg__variant": "vitg"}) == "eng_v2g")
    fn_back = engine_fns["V2-L"]
    if callable(fn_back):
        config_on_disk = settings_mod.load()
        service.set_param(config_on_disk, "engine", "sdf")     # 模拟"配置里切到 sdf"
        fn_back("", 1.0)
        saved = settings_mod.load()
        check("从 sdf 点回 V2-L → 引擎切回 dav2 + variant=vitl",
              saved.get("depthimg__engine") == "dav2"
              and saved.get("depthimg__variant") == "vitl",
              f"{saved.get('depthimg__engine')} / {saved.get('depthimg__variant')}")

    # 万一 ZBrush 的 zbc.set 也会触发开关回调(官方没说明),单选组必须仍然只亮一个
    fake.fire_switch_callbacks = True
    try:
        fn_v2b = engine_fns["V2-B"]
        if callable(fn_v2b):
            fake.switch_values[mod.paths()["eng_v2b"]] = 1.0     # 模拟用户点下去
            fn_v2b("", 1.0)
        on_after = [k for k in ("eng_v2s", "eng_v2b", "eng_v2l", "eng_v2g")
                    if fake.switch_values.get(mod.paths()[k]) == 1.0]
        check("就算 set 也会回调,单选组仍然只亮一个(有重入保护)",
              on_after == ["eng_v2b"]
              and settings_mod.load().get("depthimg__variant") == "vitb", str(on_after))
    finally:
        fake.fire_switch_callbacks = False

    # 单选按钮不该能被点灭:点的是**当前选中的那个** → 按回去
    fn_on = engine_fns["V2-B"]
    fn_other = engine_fns["V2-G"]
    if callable(fn_on) and callable(fn_other):
        fn_on("", 1.0)                                   # 先选中 V2-B
        fn_on("", 0.0)                                   # 再"点灭"它
        check("点当前选中的那一格 → 被按回去,不允许「一档都没选」",
              fake.switch_values.get(mod.paths()["eng_v2b"]) == 1.0
              and settings_mod.load().get("depthimg__variant") == "vitb",
              f"V2-B={fake.switch_values.get(mod.paths()['eng_v2b'])} "
              f"variant={settings_mod.load().get('depthimg__variant')}")
        fn_other("", 0.0)                                # 收到"没选中的那个被关掉"
        check("收到「没选中的那一格被关掉」→ 保持关着(界面与配置不能对不上)",
              fake.switch_values.get(mod.paths()["eng_v2g"]) == 0.0
              and settings_mod.load().get("depthimg__variant") == "vitb",
              f"V2-G={fake.switch_values.get(mod.paths()['eng_v2g'])}")
    replace_fn = switch_fns.get(mod.LABELS["replace"])
    check("「替换模型贴图」开关挂了回调", callable(replace_fn))
    if callable(replace_fn):
        replace_fn(False, False)
        check("关掉替换 → replace_texture = False",
              settings_mod.load().get("depthimg__replace_texture") is False,
              str(settings_mod.load().get("depthimg__replace_texture")))
    flip_fn = switch_fns.get(mod.LABELS["flip_v"])
    check("「UV 翻转」开关挂了回调", callable(flip_fn))
    if callable(flip_fn):
        flip_fn(False, False)
        check("关掉 UV 翻转 → flip_v = False",
              settings_mod.load().get("depthimg__flip_v") is False,
              str(settings_mod.load().get("depthimg__flip_v")))
    check("四档规模都能在面板上直接选(面板就是「选规模」的地方)",
          all(word in " ".join(mod.LABELS.values())
              for word in ("V2-S", "V2-B", "V2-L", "V2-G")))
    check("面板上不露 fp16 / metric(一个是质量坑、一个是按域标定的米制权重)",
          not any(bad in " ".join(mod.LABELS.values()) for bad in ("fp16", "metric")))


# --------------------------------------------------------------------------- #
# 解释器 / 命令行
# --------------------------------------------------------------------------- #
def check_interpreter_and_args() -> None:
    print("* 解释器自动配对 + 命令行拼装")
    import fake_zbrush                                              # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    from zbtools.features.depthimg import service                     # noqa: PLC0415

    check("显式指定就用它",
          service.depth_interpreter({"depthimg__interpreter": sys.executable})
          == sys.executable)

    tmp = tempfile.mkdtemp(prefix="depthimg_interp_")
    fake_root = os.path.join(tmp, "fake")
    da3_py = os.path.join(fake_root, "da3/bin/python")
    comfy_py = os.path.join(fake_root, "comfy/.venv/bin/python")
    for path in (da3_py, comfy_py):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
    caps = {da3_py: {"torch", "depth_anything_3"}, comfy_py: {"torch"}}
    real_has, real_globs = service.has_module, service._PYTHON_GLOBS
    real_find, real_comfy = service.find_comfy, service._comfy_python
    service.has_module = lambda path, module: module in caps.get(path, set())
    service._PYTHON_GLOBS = (da3_py,)
    # comfy_py = <root>/.venv/bin/python → 往上三层才是 ComfyUI 根目录
    service.find_comfy = lambda _cfg: os.path.dirname(
        os.path.dirname(os.path.dirname(comfy_py)))
    try:
        base = {"depthimg__interpreter": "", "interpreter": sys.executable}
        check("引擎 dav2 → 自动挑有 torch 的环境(ComfyUI 的 venv 就够)",
              service.depth_interpreter({**base, "depthimg__engine": "dav2"}) == comfy_py,
              service.depth_interpreter({**base, "depthimg__engine": "dav2"}))
        check("引擎 da3 → 自动挑装了 depth_anything_3 的那个 venv",
              service.depth_interpreter({**base, "depthimg__engine": "da3"}) == da3_py,
              service.depth_interpreter({**base, "depthimg__engine": "da3"}))
        service._PYTHON_GLOBS = ()
        service.find_comfy = lambda _cfg: ""
        check("候选都不可用 → 回落插件解释器(由管线报缺什么)",
              service.depth_interpreter({"depthimg__interpreter": "",
                                         "interpreter": sys.executable}) == sys.executable)
    finally:
        service.has_module, service._PYTHON_GLOBS = real_has, real_globs
        service.find_comfy, service._comfy_python = real_find, real_comfy

    args = service.depth_args({"depthimg__engine": "sdf",
                               "depthimg__interpreter": sys.executable},
                              "in.png", "out.png", "r.json")
    for flag in ("--in", "--out", "--engine", "--device", "--size", "--da3-res", "--clip",
                 "--bg", "--sdf-radius", "--report-json"):
        check(f"命令行含 {flag}", flag in args)
    check("sdf 时不塞权重(零依赖引擎)", "--model" not in args, str(args))
    check("命令行默认 --detail 0(不叠)且不带 --no-despike,去极值阈值 6",
          args[args.index("--detail") + 1] == "0" and "--no-despike" not in args
          and args[args.index("--despike-robust") + 1] == "6",
          f"detail={args[args.index('--detail') + 1]} "
          f"robust={args[args.index('--despike-robust') + 1]}")
    no_spike = service.depth_args({"depthimg__engine": "sdf", "depthimg__despike": False,
                                   "depthimg__interpreter": sys.executable}, "i", "o", "r")
    check("关掉去极值 → 加 --no-despike", "--no-despike" in no_spike, str(no_spike))
    check("命令行默认 --size 518 / --bit-depth 8(速度优先)",
          args[args.index("--size") + 1] == "518"
          and args[args.index("--bit-depth") + 1] == "8",
          f"{args[args.index('--size') + 1]} / {args[args.index('--bit-depth') + 1]}")
    check("sdf 半径默认 0 = 自动(按分辨率算,不写死像素)",
          args[args.index("--sdf-radius") + 1] == "0",
          args[args.index("--sdf-radius") + 1])
    manual = service.depth_args({"depthimg__engine": "sdf", "depthimg__sdf_radius": 24,
                                 "depthimg__interpreter": sys.executable}, "i", "o", "r")
    check("手动指定半径就照用",
          manual[manual.index("--sdf-radius") + 1] == "24",
          manual[manual.index("--sdf-radius") + 1])
    check("反相默认关", "--invert" not in args)
    check("UV 翻转默认开 → 命令行带 --flip-v", "--flip-v" in args, str(args))
    no_flip = service.depth_args({"depthimg__engine": "sdf", "depthimg__flip_v": False,
                                  "depthimg__interpreter": sys.executable}, "i", "o", "r")
    check("关掉 UV 翻转 → 不带 --flip-v", "--flip-v" not in no_flip, str(no_flip))
    inv = service.depth_args({"depthimg__engine": "sdf", "depthimg__invert": True,
                              "depthimg__interpreter": sys.executable}, "i", "o", "r")
    check("invert 开 → 加 --invert", "--invert" in inv)
    da3 = service.depth_args({"depthimg__engine": "da3",
                              "depthimg__model": "depth-anything/DA3-SMALL",
                              "depthimg__interpreter": sys.executable}, "i", "o", "r")
    check("da3 用仓库 id(不误传本地 safetensors)",
          da3[da3.index("--model") + 1] == "depth-anything/DA3-SMALL"
          and da3.count("--model") == 1, str(da3))
    check("管线脚本能找到", os.path.exists(service.depth_pipeline_path()),
          service.depth_pipeline_path())


# --------------------------------------------------------------------------- #
# 隐患防护:读模型贴图 / 送回 ZBrush
# --------------------------------------------------------------------------- #
def check_texture_read_hazards() -> None:
    print("* 读模型贴图:清残留 / 没有 UV 要拦住")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.errors import PluginError                            # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_read_")
    cfg = {"depthimg__work_dir": tmp, "depthimg__io_wait_s": 1.0}
    slot_state(fake, "323")                  # 贴图槽 + Texture 面板都摆好
    obj = os.path.join(tmp, service._EXPORT_OBJ)
    base = os.path.splitext(obj)[0]
    tex = base + ".bmp"
    make_texture(tex)                     # 假装上一次导出留下的贴图
    write_obj(obj)
    with open(base + ".mtl", "w", encoding="utf-8") as handle:
        handle.write(f"newmtl defaultMat\nmap_Kd {os.path.basename(tex)}\n")
    service._clean_previous(tmp)
    check("清残留:上一次的 OBJ 被删掉", not os.path.exists(obj))
    check("清残留:上一次的贴图被删掉(**不然会拿到别的模型的贴图**)", not os.path.exists(tex))
    check("清残留:mtl 也删掉", not os.path.exists(base + ".mtl"))

    # 真流程:导出(用假 press 写文件)+ 没 UV → 必须报错
    real_export = toolio.export_active

    def export_without_uv(dest: str, _timeout: float, on_tick=None) -> None:
        write_obj(dest, uvs=False)

    toolio.export_active = export_without_uv
    try:
        try:
            service.read_model_texture(cfg)
            check("没有 UV 的模型 → 报错(别拿 _TXTR.tif 去算深度)", False, "没抛错")
        except PluginError as exc:
            check("没有 UV 的模型 → 报错并说明要选带 UV 的模型",
                  "没有 UV" in str(exc) and "UV" in str(exc), str(exc)[:80])
    finally:
        toolio.export_active = real_export

    def export_with_uv(dest: str, _timeout: float, on_tick=None) -> None:
        write_obj(dest, uvs=True)
        make_texture(os.path.splitext(dest)[0] + ".bmp")
        with open(os.path.splitext(dest)[0] + ".mtl", "w", encoding="utf-8") as handle:
            handle.write(f"newmtl defaultMat\nmap_Kd "
                         f"{os.path.basename(os.path.splitext(dest)[0])}.bmp\n")

    toolio.export_active = export_with_uv
    try:
        got_obj, got_tex = service.read_model_texture(cfg)
        check("有 UV → 拿到 OBJ", os.path.basename(got_obj) == service._EXPORT_OBJ, got_obj)
        check("有 UV → 拿到贴图", os.path.basename(got_tex) == "depthimg_model.bmp", got_tex)
        check("拿到的是刚导出的那张(不是残留)",
              os.path.getsize(got_tex) > 0 and os.path.exists(got_tex))
    finally:
        toolio.export_active = real_export


def slot_state(fake, name: str = "323", size: tuple[int, int] = (48, 48)) -> None:
    """把假 ZBrush 的"贴图槽"设成指定状态。

    真机上的核对就是这么做的:读 ``get_title("Tool:Texture Map:Texture Map")``
    —— 挂上之后它会变成**那张贴图的名字**(实测:原来是 "323")。
    ⚠ 尺寸不能当判据:深度图和原贴图**同尺寸**(2749x3684),看尺寸会永远判成功。
    """
    slot = "Tool:Texture Map:Texture Map"
    fake.existing_items.add(slot)
    fake.titles[slot] = name
    fake.infos[slot] = (f"Texture Map\n\n{name}\n\nWidth={size[0]}\nHeight={size[1]}"
                        if name else "Texture Map")
    fake.slot_texture = name
    fake.existing_items.add("Texture:Item Info")
    if name and name not in fake.palette:
        fake.palette.append(name)            # 模型的贴图已经在 Texture 面板里(常见情况)
        fake.palette_index = len(fake.palette) - 1


def check_model_switch_validation() -> None:
    """换模型时必须核对:导出的到底是不是当前这个模型、贴图是不是它挂的那张。

    实测 bug:切到另一个模型后,深度图还是**上一个模型**的 —— 根因是
    ZBrush 的导出一是命令队列(可能拿到残留文件)、二是**没 Clone 过的贴图导不出来**
    (官方文档原文),导出的可能是面板里那张别的贴图。
    """
    print("* 换模型:导出前验明正身 + 导出后逐条核对")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.errors import PluginError                            # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_switch_")
    cfg = {"depthimg__work_dir": tmp, "depthimg__io_wait_s": 1.0}
    slot_state(fake, "323")

    def make_obj(path: str, points: int, uvs: bool = True) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            for index in range(points):
                handle.write(f"v {index} 0 0\n")
            if uvs:
                handle.write("vt 0 0\n")
            handle.write("f 1 2 3\n")

    def export_points(points: int, texture_size: tuple[int, int] | None = (64, 64)):
        def fake_export(dest: str, _timeout: float, on_tick=None) -> None:
            make_obj(dest, points)
            if texture_size:
                base = os.path.splitext(dest)[0]
                from PIL import Image                                  # noqa: PLC0415
                Image.fromarray(np.zeros((texture_size[1], texture_size[0], 3),
                                         dtype=np.uint8)).save(base + ".bmp")
                with open(base + ".mtl", "w", encoding="utf-8") as handle:
                    handle.write(f"newmtl m\nmap_Kd {os.path.basename(base)}.bmp\n")
        return fake_export

    real_export = toolio.export_active
    real_fp, real_slot = toolio.subtool_fingerprint, toolio.texture_slot_info
    real_clone = toolio.clone_texture
    # 这一节专测"点数 / 贴图尺寸"的核对,所以把"面板指向"那一步打桩
    # (它自己有专门的用例,见 check_palette_focus)
    real_focus = toolio.focus_model_texture
    toolio.focus_model_texture = lambda name: (True, "stub:这一节只测点数与尺寸核对")
    try:
        # 1) 正常:点数一致 + 贴图尺寸一致 → 通过
        toolio.subtool_fingerprint = lambda: {"points": 3, "box": [1.0, 1.0, 1.0]}
        toolio.texture_slot_info = lambda: {"name": "323", "width": 64, "height": 64, "depth": 32}
        toolio.export_active = export_points(3)
        _obj, tex = service.read_model_texture(cfg)
        check("点数、贴图尺寸都对得上 → 正常读出", os.path.basename(tex) == "depthimg_model.bmp",
              tex)

        # 2) 换模型后导出的是**上一个模型**(点数对不上)→ 必须报错停下
        toolio.subtool_fingerprint = lambda: {"points": 3, "box": [1.0, 1.0, 1.0]}
        toolio.export_active = export_points(999)
        try:
            service.read_model_texture(cfg)
            check("导出的不是当前模型 → 报错", False, "没抛错")
        except PluginError as exc:
            check("导出的不是当前模型 → 报错并说明原因",
                  "不是当前选中的那个" in str(exc) and "999" in str(exc), str(exc)[:90])
            check("报错时明确说「没有生成任何图」",
                  "没有**生成任何图" in str(exc).replace("**没有**", "没有**"), str(exc)[-60:])

        # 3) 模型对了,但导出的贴图**尺寸不对**(= ZBrush 写出来的是面板里那张别的贴图)
        toolio.subtool_fingerprint = lambda: {"points": 3, "box": None}
        toolio.texture_slot_info = lambda: {"name": "b_model", "width": 128, "height": 128,
                                            "depth": 32}
        toolio.export_active = export_points(3, texture_size=(64, 64))
        try:
            service.read_model_texture(cfg)
            check("贴图尺寸和模型挂的那张不一致 → 报错", False, "没抛错")
        except PluginError as exc:
            text = str(exc)
            check("贴图对不上 → 报错并点明可能是没 Clone",
                  "不是这个模型挂的那张" in text and "Clone" in text, text[:140])
            check("同时给出处理办法(按 Clone Txtr 再试)", "Clone Txtr" in text, text[:200])

        # 4) 一个贴图都没导出来 → 自动补一次 Clone 再导
        cloned = {"called": 0}
        toolio.texture_slot_info = lambda: {"name": "323", "width": 64, "height": 64, "depth": 32}

        def export_without_texture(dest: str, _timeout: float, on_tick=None) -> None:
            make_obj(dest, 3, uvs=True)          # 不写贴图

        state = {"first": True}

        def export_after_clone(dest: str, _timeout: float, on_tick=None) -> None:
            if state["first"]:
                state["first"] = False
                make_obj(dest, 3, uvs=True)
                return
            export_points(3)(dest, _timeout)     # Clone 之后能导出来了

        def fake_clone():
            cloned["called"] += 1
            return True, "已按 Clone"

        toolio.export_active = export_without_texture
        toolio.clone_texture = fake_clone
        state["first"] = True
        try:
            service.read_model_texture(cfg)
            check("没导出贴图时自动 Clone 一次再导", cloned["called"] == 1,
                  str(cloned["called"]))
        except PluginError as exc:
            # 第一次没导出来 → Clone → 第二次导出来 → 应当成功;这里不该抛错
            check("没导出贴图时自动 Clone 一次再导", False, str(exc)[:80])

        # 5) 快路径(Texture:Export)也要核对:它导出的图尺寸和"模型挂的那张"不一致 → 退回老办法
        toolio.texture_slot_info = lambda: {"name": "b_model", "width": 64, "height": 64,
                                            "depth": 32}
        toolio.export_active = export_points(3, texture_size=(64, 64))
        cfg_fast = dict(cfg, depthimg__use_texture_export=True)
        real_fast = toolio.export_current_texture

        def write_fast_png(base: str, size: tuple[int, int] = (64, 64)) -> str:
            from PIL import Image                                  # noqa: PLC0415
            target = base + ".png"
            Image.fromarray(np.zeros((size[1], size[0], 3), dtype=np.uint8)).save(target)
            return target

        # 快路径"导错图"(128x128),而模型挂的是 64x64 → 必须被核对拦下、退回老办法
        toolio.export_current_texture = lambda base, timeout=8.0: write_fast_png(base, (128, 128))
        try:
            _obj2, tex2 = service.read_model_texture(cfg_fast)
            check("快路径导出的图核对不过 → 自动退回导出 SubTool",
                  os.path.basename(tex2) == "depthimg_model.bmp", tex2)
        finally:
            toolio.export_current_texture = real_fast
    finally:
        toolio.export_active = real_export
        toolio.subtool_fingerprint, toolio.texture_slot_info = real_fp, real_slot
        toolio.clone_texture = real_clone
        toolio.focus_model_texture = real_focus


def check_output_naming() -> None:
    """输出文件名必须能区分内容 —— **ZBrush 的 Texture 面板按名字去重**。

    实测 bug(用户反馈"四个引擎切了没反应、深度图没区别"):输出名固定
    `depthimg_depth_323.png`,换引擎再跑还是同名 → ZBrush 导入时用**列表里第一张同名的**(旧图),
    新的排在后面不会被选中(ZBrushCentral 上那段脚本的作者也踩过这一点并写出来了)。
    现在名字里带"引擎 + 内容短哈希":内容一样同名(不堆垃圾),内容不一样必不同名。
    """
    print("* 输出命名:按内容区分(否则 ZBrush 会按名字去重、拿旧图)")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    import zbtools.features.depthimg as mod                           # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_name_")
    slot_state(fake, "323")
    cfg = {"depthimg__work_dir": tmp, "depthimg__replace_texture": False,
           "depthimg__import_wait_s": 0.0}
    stub_tex = os.path.join(tmp, "model.bmp")
    make_texture(stub_tex)
    service.read_model_texture = lambda _cfg, on_tick=None: ("(stub)", stub_tex)
    written = {"bytes": b"AAAA"}
    real_run = service.run_depth

    def fake_run(cfg2, src, out, **kw):          # kw 里会有 progress/base/span
        with open(out, "wb") as handle:
            handle.write(written["bytes"])
        report = {"engine": service.engine(cfg2), "out": out, "out_size": [8, 8],
                  "seconds": 0.1, "out_min": 0, "out_max": 255, "out_mean": 1.0,
                  "model": "x", "device": "cpu"}
        service.set_param(cfg2, "last_depth", out)
        return report

    service.run_depth = fake_run
    try:
        service.set_param(cfg, "engine", "dav2")
        written["bytes"] = b"AAAA"
        mod._to_depth(cfg)
        first = str(cfg.get("depthimg__last_depth"))
        service.set_param(cfg, "engine", "sdf")
        written["bytes"] = b"BBBB"                       # 换了引擎 → 内容不一样
        mod._to_depth(cfg)
        second = str(cfg.get("depthimg__last_depth"))
        check("换了引擎 → 输出文件名不同(不会和面板里同名的撞上)",
              os.path.basename(first) != os.path.basename(second),
              f"{os.path.basename(first)} vs {os.path.basename(second)}")
        check("文件名里带引擎名 + 档位(dav2-vits / sdf)",
              "_dav2-vits_" in os.path.basename(first)
              and "_sdf_" in os.path.basename(second),
              f"{os.path.basename(first)} / {os.path.basename(second)}")
        # 四格共用一个引擎名 dav2 → 光有引擎名分不出档位,名字必须带档位后缀
        # (否则"V2-S 换 V2-L"文件名一样,ZBrush 按名字去重会拿旧图)
        service.set_param(cfg, "engine", "dav2")
        service.set_param(cfg, "variant", "vitg")
        written["bytes"] = b"CCCC"                       # 换档位 → 内容也不一样
        mod._to_depth(cfg)
        by_variant = str(cfg.get("depthimg__last_depth"))
        check("换了档位 → 文件名带的是 dav2-vitg(不是 dav2-vits)",
              "_dav2-vitg_" in os.path.basename(by_variant),
              os.path.basename(by_variant))
        check("换档位 → 文件名与换之前不同",
              os.path.basename(by_variant) != os.path.basename(first),
              f"{os.path.basename(by_variant)} vs {os.path.basename(first)}")
        check("engine_label:四档各不同,dav2 之外不加后缀",
              [service.engine_label({"depthimg__engine": "dav2", "depthimg__variant": v})
               for v in ("vits", "vitb", "vitl", "vitg")]
              == ["dav2-vits", "dav2-vitb", "dav2-vitl", "dav2-vitg"]
              and service.engine_label({"depthimg__engine": "da3"}) == "da3"
              and service.engine_label({"depthimg__engine": "luma"}) == "luma",
              service.engine_label({"depthimg__engine": "dav2", "depthimg__variant": "vitb"}))
        check("文件名里带内容短码(6 位十六进制)",
              bool(__import__("re").search(r"_[0-9a-f]{6}\.png$", os.path.basename(first)))
              and bool(__import__("re").search(r"_[0-9a-f]{6}\.png$",
                                               os.path.basename(second))),
              f"{os.path.basename(first)} / {os.path.basename(second)}")
        service.set_param(cfg, "engine", "dav2")
        service.set_param(cfg, "variant", "vits")         # 回到第一档(上面为测档位后缀改过)
        written["bytes"] = b"AAAA"                       # 同样的引擎+档位+内容 → 同样的名字
        mod._to_depth(cfg)
        third = str(cfg.get("depthimg__last_depth"))
        check("同样的内容 → 同样的名字(重复跑不会在面板里堆同名贴图)",
              os.path.basename(third) == os.path.basename(first),
              f"{os.path.basename(third)} vs {os.path.basename(first)}")
        check("文件真的在磁盘上", os.path.exists(first) and os.path.exists(second))
    finally:
        service.run_depth = real_run


def check_palette_focus() -> None:
    """读贴图前必须把 Texture 面板的"当前贴图"挪到**这个模型自己那张**上。

    实测 bug(用户那边):换模型、贴的图也不一样,可转出来的深度图还是同一张。
    根因:ZBrush 导出 OBJ 写出的贴图是**面板当前选中**的那张,而插件上一次会把生成的深度图
    导入面板并选中它 → 下一次导出写出来的还是那张深度图(两张同尺寸时连尺寸核对都拦不住)。
    """
    print("* 面板指向:导出前先把「当前贴图」对准这个模型自己的那张")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.errors import PluginError                            # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    fake.existing_items.update({"Texture:Item Info", "Tool:Texture Map:Clone Txtr",
                                "Tool:Texture Map:Texture Map"})
    fake.titles["Tool:Texture Map:Texture Map"] = "456"      # 这个模型挂的是 456

    # 1) 面板里本来就有它(常见)→ 直接选中
    fake.palette = ["323", "depthimg_depth_323", "456"]
    fake.palette_index = 1                                   # 上一次生成的深度图还选着
    ok, note = toolio.focus_model_texture("456")
    check("面板里有这个模型的贴图 → 选中它", ok is True and fake.palette_index == 2, note[:90])
    check("说明里写了找到的编号", "编号 2" in note, note[:90])
    check("这一步正是为了修「换模型还是同一张图」",
          toolio._palette_current() == "456", toolio._palette_current())

    # 2) 面板里没有它(没 Clone 过)→ 按 Clone,然后选最新那张
    fake.palette = ["323"]
    fake.palette_index = 0
    fake.slot_texture = "456"
    ok, note = toolio.focus_model_texture("456")
    check("面板里没有 → 按 Clone Txtr 补上再选中",
          ok is True and "Clone" in note and fake.palette[-1] == "456", note[:110])
    check("Clone 之后面板当前就是它", toolio._palette_current() == "456",
          toolio._palette_current())

    # 3) 名字对不上、又没有 Clone 这一项 → 老实失败(调用方报错,而不是拿错图)
    fake.existing_items.discard("Tool:Texture Map:Clone Txtr")
    fake.palette = ["其他贴图"]
    fake.palette_index = 0
    ok, note = toolio.focus_model_texture("456")
    check("找不到又没法 Clone → 返回失败(不猜)", ok is False, note[:110])

    # 4) 读贴图时这个失败要变成**报错**(除非显式跳过)
    #    贴图槽本身是"有贴图"的正常状态(有名字有尺寸),只是面板里找不到它
    tmp = tempfile.mkdtemp(prefix="depthimg_focus_")
    fake.titles["Tool:Texture Map:Texture Map"] = "456"
    fake.infos["Tool:Texture Map:Texture Map"] = ("Texture Map\n\n456\n\n"
                                                  "Width=64\nHeight=64")
    fake.slot_texture = "456"
    cfg = {"depthimg__work_dir": tmp, "depthimg__io_wait_s": 1.0}
    try:
        service.read_model_texture(cfg)
        check("面板对不准 → 报错停下", False, "没抛错")
    except PluginError as exc:
        text = str(exc)
        check("面板对不准 → 报错并给处理办法(Clone Txtr / 逃生开关)",
              "Clone Txtr" in text and "skip_texture_focus" in text, text[:120])
    # 逃生开关:显式跳过时只警告、继续(被后面尺寸核对兜底)
    fake.existing_items.add("Tool:Texture Map:Clone Txtr")
    real_export = toolio.export_active

    def fake_export(dest: str, _timeout: float, on_tick=None) -> None:
        with open(dest, "w", encoding="utf-8") as handle:
            handle.write("v 0 0 0\nvt 0 0\nf 1 1 1\n")
        from PIL import Image                                      # noqa: PLC0415
        base = os.path.splitext(dest)[0]
        Image.fromarray(np.zeros((64, 64, 3), dtype=np.uint8)).save(base + ".bmp")
        with open(base + ".mtl", "w", encoding="utf-8") as handle:
            handle.write("newmtl m\nmap_Kd x.bmp\n")

    toolio.export_active = fake_export
    try:
        cfg_skip = dict(cfg, depthimg__skip_texture_focus=True)
        fake.palette = ["别的图"]
        fake.palette_index = 0
        _obj, tex = service.read_model_texture(cfg_skip)
        check("显式跳过时才继续(靠后面的尺寸核对兜底)", bool(tex), tex)
    finally:
        toolio.export_active = real_export


def check_send_to_zbrush() -> None:
    print("* 送回 ZBrush:按贴图槽 → 在弹窗里点贴图名(官方文档 + 论坛配方)")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_send_")
    png = os.path.join(tmp, "depthimg_depth.png")
    make_texture(png)                       # 48x48
    size = (48, 48)

    # 1) 什么都没探测到 → 老实说没成,并给手工办法
    ok, note = service.send_to_zbrush({"depthimg__import_wait_s": 0.0}, png, size)
    check("探测不到界面项时不假装成功", ok is False, note[:60])
    check("说明里点出试过哪些路",
          "弹窗挂载" in note or "Texture:Import" in note, note[:120])

    # 2) 主路:导入面板 → 选最新贴图 → 按槽 → 点 PopUp:<名字> → 核对名字变了
    fake.existing_items.update({"Texture:Import", "Texture:Item Info",
                                "Tool:Texture Map:TextureMap",
                                "Tool:Texture Map:Texture Map"})
    fake.maximums["Texture:Item Info"] = 7.0
    fake.titles["Texture:Item Info"] = "depthimg_depth."
    fake.popup_assign["Tool:Texture Map:TextureMap"] = "depthimg_depth"
    slot_state(fake, "323")                 # 原贴图叫 323
    ok, note = service.send_to_zbrush({"depthimg__import_wait_s": 0.0,
                                       "depthimg__replace_texture": True}, png, size)
    check("主路核对通过 → 报告挂上了", ok is True, note[:160])
    check("核对判据是**名字变了**", "名字变了" in note, note[:200])
    check("按了贴图槽(弹出列表)", "Tool:Texture Map:TextureMap" in fake.pressed,
          str(fake.pressed))
    check("在弹窗里点了那条贴图(PopUp:<名字>,末尾句点已去掉)",
          "PopUp:depthimg_depth" in fake.clicks, str(fake.clicks))
    check("先导入面板、并把「当前贴图」切到最新那张",
          "Texture:Import" in fake.pressed and fake.palette_index == len(fake.palette) - 1,
          f"{fake.pressed} / 面板 {fake.palette} 当前 #{fake.palette_index}")

    # 3) 弹窗里点不中(名字对不上)→ 不许报成功
    fake.clicks.clear()
    fake.popup_assign.clear()                # 点弹窗里的名字都点不中
    slot_state(fake, "323")
    ok, note = service.send_to_zbrush({"depthimg__import_wait_s": 0.0,
                                       "depthimg__replace_texture": True}, png, size)
    check("点不中 → 不报成功(不假装)", ok is False, note[:120])
    check("试过多条名字候选(名字格式各版本不一样)",
          len([c for c in fake.clicks if c.startswith("PopUp:")]) >= 2,
          str(fake.clicks[:6]))
    check("说明里给了手工办法", "手工" in note, note[:200])
    check("弹窗没关掉时按 Esc 收尾(别把列表留在屏幕上)",
          any(k.lower().startswith("esc") for k in fake.keys), str(fake.keys))

    # 4) 替换开关关掉 → 只导入面板,不按槽、不点弹窗
    fake.pressed.clear()
    fake.clicks.clear()
    ok, note = service.send_to_zbrush({"depthimg__replace_texture": False,
                                       "depthimg__import_wait_s": 0.0}, png, size)
    check("关掉替换 → 只进面板,不碰模型",
          ok is False and "Texture:Import" in fake.pressed and not fake.clicks,
          f"{fake.pressed} / {fake.clicks}")
    check("说明写明没有替换", "没有" in note or "只" in note, note[:90])


# --------------------------------------------------------------------------- #
# 主流程(用 luma 引擎,零依赖、不碰 torch)
# --------------------------------------------------------------------------- #
def check_main_flow() -> None:
    print("* 主流程:读贴图 → 深度图 → 送回 ZBrush(luma 引擎)")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake.existing_items.update({"Texture:Import", "Tool:Texture Map:Texture Map"})
    fake_zbrush.install(fake)
    import zbtools.features.depthimg as mod                           # noqa: PLC0415
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="depthimg_flow_")
    tex = os.path.join(tmp, "model.bmp")
    make_texture(tex)
    fake.existing_items.update({"Texture:Import", "Texture:Item Info",
                                "Tool:Texture Map:TextureMap"})
    fake.maximums["Texture:Item Info"] = 3.0
    fake.titles["Texture:Item Info"] = "depthimg_depth."
    fake.popup_assign["Tool:Texture Map:TextureMap"] = "depthimg_depth"
    slot_state(fake, "323")
    cfg = {"depthimg__work_dir": tmp, "depthimg__engine": "luma", "depthimg__invert": False,
           "depthimg__import_wait_s": 0.0, "depthimg__replace_texture": True}
    service.read_model_texture = lambda _cfg, on_tick=None: ("(stub)", tex)

    mod._to_depth(cfg)
    depth = cfg.get("depthimg__last_depth")
    check("深度图写在配置里,文件名 = 贴图名 + 引擎 + 内容短码",
          bool(depth)
          and bool(__import__("re").match(r"depthimg_depth_323_luma_[0-9a-f]{6}\.png$",
                                         os.path.basename(depth))), str(depth))
    check("文件真的生成了", os.path.exists(depth or ""))
    from PIL import Image                                             # noqa: PLC0415
    if os.path.exists(depth or ""):
        image = Image.open(depth)
        check("是 8 位灰度", image.mode == "L", image.mode)
        check("尺寸 = 贴图尺寸(UV 对齐靠这个)",
              image.size == Image.open(tex).size, f"{image.size} vs {Image.open(tex).size}")
        check("不是单色(真的算出了深度)", len(set(np.asarray(image).reshape(-1).tolist())) > 1)
    check("摘要记了下来", bool(cfg.get("depthimg__last_report")),
          str(cfg.get("depthimg__last_report")))
    check("导入 + 挂载都在日志里", bool(cfg.get("depthimg__last_assign")),
          str(cfg.get("depthimg__last_assign"))[:80])
    check("深度图在 Texture 面板里(你要的「图在 ZBrush 里」就是这个)",
          "Texture:Import" in fake.pressed, str(fake.pressed))
    check("按贴图槽 + 点 PopUp 那条把它挂到了当前 SubTool",
          "PopUp:depthimg_depth" in fake.clicks, str(fake.clicks))
    check("成功路径不弹提示(只有出错才弹)", fake.notes == [], str(fake.notes))
    check("诊断里写了许可与引擎",
          "Apache-2.0" in service.diagnostics_text(cfg)
          or "可商用" in service.diagnostics_text(cfg)
          or "luma" in service.diagnostics_text(cfg))


# --------------------------------------------------------------------------- #
# 外部管线 CLI 契约(真子进程)
# --------------------------------------------------------------------------- #
def check_pipeline_cli() -> None:
    print("* 深度管线 CLI 契约(真子进程 + 真文件)")
    import importlib.util                                             # noqa: PLC0415
    from PIL import Image                                             # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("depth_pipeline", PIPELINE)
    depth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(depth)

    tmp = tempfile.mkdtemp(prefix="depthimg_cli_")
    tex = os.path.join(tmp, "tex.png")
    make_texture(tex)
    out = os.path.join(tmp, "d.png")
    report = os.path.join(tmp, "d.json")
    done = subprocess.run([sys.executable, PIPELINE, "--in", tex, "--out", out,
                           "--engine", "luma", "--report-json", report],
                          capture_output=True, text=True)
    check("luma 返回码 0", done.returncode == 0, (done.stdout + done.stderr)[-200:])
    if os.path.exists(out):
        image = Image.open(out)
        check("输出是 8 位灰度", image.mode == "L", image.mode)
        check("输出尺寸 = 输入尺寸(不是 518)", image.size == Image.open(tex).size,
              str(image.size))
    if os.path.exists(report):
        data = json.loads(open(report, encoding="utf-8").read())
        need = ("engine", "source", "out", "seconds", "interpreter", "out_size",
                "out_min", "out_max", "out_mean", "inverted")
        check("报告字段齐全", all(k in data for k in need), str(sorted(data)))

    inv = os.path.join(tmp, "inv.png")
    subprocess.run([sys.executable, PIPELINE, "--in", tex, "--out", inv,
                    "--engine", "luma", "--invert"], capture_output=True, text=True)
    if os.path.exists(out) and os.path.exists(inv):
        a = np.asarray(Image.open(out)).astype(int)
        b = np.asarray(Image.open(inv)).astype(int)
        check("--invert 就是逐像素取反", int(np.abs((255 - a) - b).max()) <= 1)

    # --flip-v:ZBrush 的图像自下而上存,原样送进去会上下颠倒(用户实测)——
    # 所以插件默认加这个开关;这里钉死它的行为:只翻行、尺寸不变
    flip_off = os.path.join(tmp, "flip_off.png")
    flip_on = os.path.join(tmp, "flip_on.png")
    for target, extra in ((flip_off, []), (flip_on, ["--flip-v"])):
        subprocess.run([sys.executable, PIPELINE, "--in", tex, "--out", target,
                        "--engine", "luma", *extra], capture_output=True, text=True)
    if os.path.exists(flip_off) and os.path.exists(flip_on):
        off = np.asarray(Image.open(flip_off))
        on = np.asarray(Image.open(flip_on))
        check("--flip-v 只翻转行(尺寸不变)", off.shape == on.shape, f"{off.shape} vs {on.shape}")
        check("--flip-v 输出 = 未翻转的上下翻转", bool((off[::-1] == on).all()))
        check("确实翻了(不是原样)", not bool((off == on).all()))
        data = json.loads(open(os.path.join(tmp, "d.json"), encoding="utf-8").read())
        check("报告里有 flipped_v 字段(诊断用)", "flipped_v" in data, str(sorted(data)))

    sdf = os.path.join(tmp, "sdf.png")
    sdf_report = os.path.join(tmp, "sdf.json")
    done = subprocess.run([sys.executable, PIPELINE, "--in", tex, "--out", sdf,
                           "--engine", "sdf", "--sdf-radius", "6",
                           "--report-json", sdf_report],
                          capture_output=True, text=True)
    check("sdf(图案浮雕)返回码 0", done.returncode == 0, (done.stdout + done.stderr)[-200:])
    if os.path.exists(sdf_report):
        data = json.loads(open(sdf_report, encoding="utf-8").read())
        check("手动半径 6 会照用并在报告里写出来",
              data.get("sdf_radius") == 6 and data.get("sdf_radius_auto") is False,
              f"{data.get('sdf_radius')} auto={data.get('sdf_radius_auto')}")
        # 亚像素修正 + 0.7px 平滑:斜坡上不该出现"整数距离"那种大台阶
        if os.path.exists(sdf):
            arr = np.asarray(Image.open(sdf)).astype(int)
            steps = np.abs(np.diff(arr, axis=1))
            check("sdf 的斜坡没有大台阶(亚像素修正生效)",
                  int(np.percentile(steps, 99.9)) <= 40,
                  f"相邻差 99.9 分位 {int(np.percentile(steps, 99.9))}")
    rounded = depth_pipeline._input_size(854, 1280, 1024)
    check("手动推理边长会被归整到 14 的倍数(1024 → 1022,否则 DINOv2 的 patch 会崩)",
          rounded == (672, 1022), str(rounded))
    check("_round14 归整正确(1000 → 994,7 → 14)",
          depth_pipeline._round14(1000) == 994 and depth_pipeline._round14(7) == 14,
          str(depth_pipeline._round14(1000)))

    # 自动半径:大图要更宽(实测 2749x3684 的插画 8px 只剩轮廓线、24px 才好看)
    big = os.path.join(tmp, "big.png")
    from PIL import Image as _Image                                  # noqa: PLC0415
    _Image.fromarray(np.full((600, 800, 3), 255, dtype=np.uint8)).save(big)
    big_report = os.path.join(tmp, "big.json")
    subprocess.run([sys.executable, PIPELINE, "--in", big, "--out",
                    os.path.join(tmp, "big_d.png"), "--engine", "sdf",
                    "--report-json", big_report], capture_output=True, text=True)
    if os.path.exists(big_report):
        data = json.loads(open(big_report, encoding="utf-8").read())
        check("自动半径至少 8px,且随短边放大(800 短边 → 至少 8)",
              data.get("sdf_radius", 0) >= 8 and data.get("sdf_radius_auto") is True,
              str(data.get("sdf_radius")))
        check("自动半径 = 短边 × 0.6%(这里 800 → 8,因为下限就是 8)",
              abs(float(data.get("sdf_radius", 0)) - 8.0) < 0.2,
              str(data.get("sdf_radius")))

    done = subprocess.run([sys.executable, PIPELINE, "--in", tex,
                           "--out", os.path.join(tmp, "a.png"), "--engine", "auto"],
                          capture_output=True, text=True)
    check("auto 找不到权重/包时返回码 2(不静默降级)",
          done.returncode == 2, str(done.returncode))
    check("错误里给出二选一的照抄命令", "二选一" in done.stdout, done.stdout[-160:])

    if importlib.util.find_spec("torch") is None:
        done = subprocess.run([sys.executable, PIPELINE, "--in", tex,
                               "--out", os.path.join(tmp, "t.png"), "--engine", "dav2",
                               "--model", tex], capture_output=True, text=True)
        check("dav2 缺 torch → 返回码 2 并给出 pip 命令",
              done.returncode == 2 and "pip install torch" in done.stdout,
              done.stdout[-160:])
    else:
        print("  [skip] dav2 缺 torch 的报错(当前解释器已经有 torch)")

    done = subprocess.run([sys.executable, PIPELINE, "--in", os.path.join(tmp, "no.png"),
                           "--out", os.path.join(tmp, "n.png"), "--engine", "luma"],
                          capture_output=True, text=True)
    check("输入不存在 → 返回码 1", done.returncode == 1, str(done.returncode))

    # ---- 精度:自动推理边长 / 16 位输出 / 引导滤波上采样 ----
    print("* 精度:自动分辨率 / 16 位输出 / 引导滤波")
    precision_out = os.path.join(tmp, "precise.png")
    precision_report = os.path.join(tmp, "precise.json")
    big_src = os.path.join(tmp, "big_src.png")
    from PIL import Image as _Img                                     # noqa: PLC0415
    _Img.fromarray(np.tile(np.linspace(0, 255, 2000, dtype=np.uint8)[None, :, None],
                           (1400, 1, 3))).save(big_src)               # 2000x1400 的渐变
    done = subprocess.run([sys.executable, PIPELINE, "--in", big_src, "--out", precision_out,
                           "--engine", "luma", "--bit-depth", "16",
                           "--report-json", precision_report],
                          capture_output=True, text=True)
    if os.path.exists(precision_report):
        data = json.loads(open(precision_report, encoding="utf-8").read())
        check("显式要 16 位时才写那张 TIFF(位移/置换用)",
              bool(data.get("high_precision_path")), str(data.get("high_precision_path")))
        check("报告里位深 = 16", data.get("bit_depth") == 16, str(data.get("bit_depth")))
        high = data.get("high_precision_path") or ""
        if high and os.path.exists(high):
            with _Img.open(high) as handle:
                check("那张 TIFF 确实是 16 位", handle.mode in ("I;16", "I"),
                      handle.mode)
        check("8 位 PNG 还在(给 ZBrush 贴图用)",
              os.path.exists(precision_out)
              and _Img.open(precision_out).mode == "L",
              _Img.open(precision_out).mode if os.path.exists(precision_out) else "缺失")
    done = subprocess.run([sys.executable, PIPELINE, "--in", big_src, "--out",
                           os.path.join(tmp, "bd8.png"), "--engine", "luma",
                           "--bit-depth", "8", "--report-json", os.path.join(tmp, "bd8.json")],
                          capture_output=True, text=True)
    if os.path.exists(os.path.join(tmp, "bd8.json")):
        data = json.loads(open(os.path.join(tmp, "bd8.json"), encoding="utf-8").read())
        check("--bit-depth 8 时不写 16 位 TIFF",
              not data.get("high_precision_path") and data.get("bit_depth") == 8,
              str(data.get("high_precision_path")))
    # save_gray 本身必须保住精度:给它一个"细过 8 位"的高度场,16 位输出要留着这些层次。
    # (AI 引擎的浮点深度就是这种场;端到端测它需要 torch,所以这里直接测落盘这一步)
    fine = (np.linspace(0.0, 1.0, 3000, dtype=np.float32)[None, :]
            * np.ones((8, 1), dtype=np.float32))
    fine_out = os.path.join(tmp, "fine.png")
    stats16 = depth_pipeline.save_gray(fine, fine_out, False, 0.0, 16)
    arr16 = np.asarray(_Img.open(stats16["high_precision_path"]))
    arr8 = np.asarray(_Img.open(fine_out))
    check("细过 8 位的高度场:16 位输出保住了层次(>1000 级)",
          int(np.unique(arr16).size) > 1000, str(int(np.unique(arr16).size)))
    check("同一个场写 8 位就只剩 256 级(这就是浮雕台阶的来源)",
          int(np.unique(arr8).size) <= 256, str(int(np.unique(arr8).size)))
    fine8 = os.path.join(tmp, "fine8.png")
    stats8 = depth_pipeline.save_gray(fine, fine8, False, 0.0, 8)
    check("--bit-depth 8 时 save_gray 不写 16 位文件",
          not stats8.get("high_precision_path"), str(stats8.get("high_precision_path")))

    spikes = np.full((60, 60), 0.5, dtype=np.float32)
    spikes[10, 10] = 0.0          # 孤立极值
    spikes[40, 41] = 1.0
    cleaned, replaced = depth_pipeline.despike(spikes)
    # 容差 0.01:中值滤波在 8 位精度上做(去极值够用),所以替换回来的值会差 1/255 以内
    check("去孤立极值:两个孤点都被换掉,其余不动",
          replaced == 2 and abs(float(cleaned[10, 10]) - 0.5) < 0.01
          and abs(float(cleaned[40, 41]) - 0.5) < 0.01
          and abs(float(cleaned[5, 5]) - 0.5) < 0.01, f"replaced={replaced}")
    smooth = np.linspace(0, 1, 60, dtype=np.float32)[None, :] * np.ones((60, 1), np.float32)
    _unused, replaced_smooth = depth_pipeline.despike(smooth)
    check("光滑场不会被误伤(一个像素都不换)", replaced_smooth == 0, str(replaced_smooth))
    detail_base = np.zeros((20, 20), dtype=np.float32)
    guide = np.zeros((20, 20, 3), dtype=np.float32)
    guide[5:15, 5:15] = 1.0
    with_detail = depth_pipeline.add_image_detail(detail_base, guide, 0.3)
    check("细节叠加:原图有内容的地方高度被抬起(浮雕靠这个才清楚)",
          float(with_detail.max()) > 0.0 and float(with_detail[10, 10]) > 0.0,
          f"max={float(with_detail.max()):.4f}")
    check("细节叠加 = 0 时原样返回",
          float(np.abs(depth_pipeline.add_image_detail(detail_base, guide, 0.0)).max()) == 0.0)

    check("自动 DA3 分辨率 = 跟输入长边走并夹在 [504, 1512]",
          depth_pipeline.auto_da3_res(1280, 2252) == 1512
          and depth_pipeline.auto_da3_res(800, 600) == 798
          and depth_pipeline.auto_da3_res(300, 200) == 504,
          str([depth_pipeline.auto_da3_res(*a) for a in ((1280, 2252), (800, 600), (300, 200))]))
    check("自动推理边长 = 跟贴图走并夹在 [518, 上限]",
          depth_pipeline.auto_size(2749, 3684, 1554) == 1554
          and depth_pipeline.auto_size(2000, 1400, 1554) == 1554
          and depth_pipeline.auto_size(800, 600, 1554) == 798
          and depth_pipeline.auto_size(300, 200, 1554) == 518,
          str([depth_pipeline.auto_size(*a) for a in
               ((2749, 3684, 1554), (2000, 1400, 1554), (800, 600, 1554), (300, 200, 1554))]))

    print("* 内置 safetensors 读取器 / 编码器识别")
    blob = os.path.join(tmp, "w.safetensors")
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    header = json.dumps({"t": {"dtype": "F32", "shape": [2, 3],
                               "data_offsets": [0, 24]}}).encode()
    with open(blob, "wb") as handle:
        handle.write(len(header).to_bytes(8, "little"))
        handle.write(header)
        handle.write(values.tobytes())
    state = depth.read_safetensors(blob)
    check("读回形状一致", tuple(state["t"].shape) == (2, 3))
    check("读回数值一致", bool((state["t"] == values).all()))
    check("读取器不依赖 torch", state["t"].__class__.__module__ == "numpy")
    check("坏文件会明确报错", _raises(lambda: depth.read_safetensors(tex)))
    check("从文件名认得出编码器",
          depth.encoder_from_name("x/depth_anything_v2_vits_fp32.safetensors") == "vits"
          and depth.encoder_from_name("x/depth_anything_v2_vitg_fp32.safetensors") == "vitg")


def check_vendored_sources() -> None:
    """vendored 的 DA V2 模型定义必须零外挂(只要 torch,不要 cv2/comfy)。"""
    print("* vendored DA V2 源码:零耦合 + 许可证在位")
    import glob as _glob                                               # noqa: PLC0415
    import py_compile                                                  # noqa: PLC0415
    files = sorted(_glob.glob(os.path.join(VENDORED, "depth_anything_v2", "**", "*.py"),
                              recursive=True))
    check("vendored 源码在位(≥8 个文件)", len(files) >= 8, str(len(files)))
    check("上游 Apache-2.0 许可证在位",
          os.path.exists(os.path.join(VENDORED, "depth_anything_v2", "LICENSE")))
    check("改动说明在位(PATCHES.md)",
          os.path.exists(os.path.join(VENDORED, "depth_anything_v2", "PATCHES.md")))
    bad = []
    for path in files:
        text = open(path, encoding="utf-8").read()
        if "import cv2" in text or "import comfy" in text or "from comfy" in text:
            bad.append(os.path.basename(path))
    check("没有 cv2 / comfy 依赖", not bad, str(bad))
    broken = []
    for path in files:
        try:
            py_compile.compile(path, doraise=True)
        except Exception as exc:                                      # noqa: BLE001
            broken.append(f"{os.path.basename(path)}: {exc}")
    check("全部能编译", not broken, str(broken))


def check_da3_lite_fallback() -> None:
    """DA3 的「轻装」兜底:官方 api.py 会拉起导出用的重依赖(moviepy/open3d/evo)。"""
    print("* DA3 轻装兜底(假包:导出模块 import 就炸)")
    tmp = tempfile.mkdtemp(prefix="depthimg_da3fake_")
    pkg = os.path.join(tmp, "depth_anything_3")
    os.makedirs(os.path.join(pkg, "utils", "export"), exist_ok=True)
    files = {
        os.path.join(pkg, "__init__.py"): "",
        os.path.join(pkg, "utils", "__init__.py"): "",
        os.path.join(pkg, "utils", "export", "__init__.py"):
            "raise ModuleNotFoundError(\"No module named 'moviepy'\")\n",
        os.path.join(pkg, "utils", "pose_align.py"):
            "raise ModuleNotFoundError(\"No module named 'evo'\")\n",
        os.path.join(pkg, "api.py"):
            "from depth_anything_3.utils.export import export\n"
            "class DepthAnything3:\n"
            "    @classmethod\n"
            "    def from_pretrained(cls, name):\n"
            "        return cls()\n",
    }
    for path, text in files.items():
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    code = (
        "import sys\n"
        f"sys.path.insert(0, {tmp!r})\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('depth_pipeline', {PIPELINE!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "cls, note = module._import_da3()\n"
        "print('CLASS', cls.__name__)\n"
        "from depth_anything_3.utils.export import export\n"
        "try:\n"
        "    export(None, 'glb', '.')\n"
        "    print('EXPORT_RAN')\n"
        "except RuntimeError as exc:\n"
        "    print('EXPORT_BLOCKED', str(exc)[:24])\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    out = done.stdout + done.stderr
    check("导出模块 import 失败后仍能导入 API 类",
          "CLASS DepthAnything3" in done.stdout, out[-200:])
    check("被顶掉的功能一调用就报错(不静默)",
          "EXPORT_BLOCKED" in done.stdout, out[-160:])


def check_texture_fast_path() -> None:
    """读贴图的**快路径**(Texture:Export):能用就用,不能就退回导出 SubTool。"""
    print("* 读贴图快路径:失败要能退回老办法")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    slot_state(fake, "323")                 # 贴图槽 + Texture 面板摆好(快路径失败要退回老路)
    tmp = tempfile.mkdtemp(prefix="depthimg_fast_")
    cfg = {"depthimg__work_dir": tmp, "depthimg__use_texture_export": True,
           "depthimg__io_wait_s": 1.0}
    obj = os.path.join(tmp, service._EXPORT_OBJ)
    base = os.path.splitext(obj)[0]

    # 1) 快路径可用(ZBrush 按 Texture:Export 时写出文件)→ 不该再去导出网格
    fake.existing_items.add("Texture:Export")
    real_export = toolio.export_active
    called = {"slow": 0}

    def slow(dest, timeout, on_tick=None):
        called["slow"] += 1
        write_obj(dest)
        make_texture(os.path.splitext(dest)[0] + ".bmp")

    toolio.export_active = slow

    def fake_press(path):
        if path == "Texture:Export":
            target = fake.next_filenames[-1]
            make_texture(target)
        return ""

    real_press = toolio.press
    toolio.press = fake_press
    try:
        got_obj, got_tex = service.read_model_texture(cfg)
        check("快路径成功时不再导出网格", called["slow"] == 0, str(called["slow"]))
        check("快路径拿到的是 Texture:Export 写出的文件",
              os.path.basename(got_tex).startswith("depthimg_tex"), got_tex)

        # 2) 快路径失败(按了但没文件)→ 必须退回老办法,而不是报错
        called["slow"] = 0
        toolio.press = lambda path: ""
        _obj, tex2 = service.read_model_texture(cfg)
        check("快路径失败 → 退回导出 SubTool", called["slow"] == 1, str(called["slow"]))
        check("退回后仍然拿到贴图", os.path.basename(tex2) == "depthimg_model.bmp", tex2)
    finally:
        toolio.export_active = real_export
        toolio.press = real_press

    # 3) 贴图槽读得到但没有尺寸 → 这个 SubTool 没挂贴图 → 直接报错(不猜)
    cfg_off = {"depthimg__work_dir": tmp, "depthimg__io_wait_s": 1.0}
    fake.existing_items.add("Tool:Texture Map:Texture Map")
    fake.titles["Tool:Texture Map:Texture Map"] = "Texture Map"
    fake.infos["Tool:Texture Map:Texture Map"] = "Texture Map"
    toolio.export_active = slow
    try:
        try:
            service.read_model_texture(cfg_off)
            check("贴图槽是空的 → 报错(没挂贴图)", False, "没抛错")
        except Exception as exc:                                     # noqa: BLE001
            check("贴图槽是空的 → 报错并说明先挂贴图",
                  "没有挂贴图" in str(exc), str(exc)[:70])
    finally:
        toolio.export_active = real_export
    check("贴图槽空 → has_texture 明确是 False", service.has_texture() is False,
          str(service.has_texture()))
    real_info = toolio.texture_slot_info
    toolio.texture_slot_info = lambda: {}          # 连 tooltip 都读不到
    try:
        check("读不到 → None(不能凭「读不到」就说「没挂」)",
              service.has_texture() is None, str(service.has_texture()))
    finally:
        toolio.texture_slot_info = real_info


def check_slot_tooltip() -> None:
    """贴图槽的 tooltip 解析 —— 用**真机抓下来的原样字符串**钉死。

    实测(ZBrush 2026.2,选中一个挂了 2749x3684 贴图的模型):
        get_title("Tool:Texture Map:Texture Map") → "323"
        get_info (...) → "Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)"
    这条让插件**不用导出网格**就知道"挂没挂、挂的是哪张、多大"。
    顺带记一句:贴图方块的值(`zbc.get`)实测**永远是 0.0**,不能拿来判断。
    """
    print("* 贴图槽 tooltip 解析(真机字符串)")
    import fake_zbrush                                              # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake.existing_items.add("Tool:Texture Map:Texture Map")
    fake_zbrush.install(fake)
    from zbtools.features.depthimg import service                     # noqa: PLC0415
    from zbtools.zbapi import toolio                                  # noqa: PLC0415

    slot = "Tool:Texture Map:Texture Map"
    fake.titles[slot] = "323"
    fake.infos[slot] = ("Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)")
    info = toolio.texture_slot_info()
    check("贴图名读对(323)", info.get("name") == "323", str(info.get("name")))
    check("尺寸读对(2749x3684)", (info.get("width"), info.get("height")) == (2749, 3684),
          f"{info.get('width')}x{info.get('height')}")
    check("位深读对(32)", info.get("depth") == 32, str(info.get("depth")))
    check("has_texture → True", service.has_texture() is True)
    check("诊断里写得出来", "2749x3684" in service._slot_text({}), service._slot_text({}))

    fake.infos[slot] = "Texture Map"                  # 没挂贴图的样子
    check("没有尺寸 → has_texture False(明确没挂)", service.has_texture() is False,
          str(service.has_texture()))
    check("诊断说「没挂贴图」", "没挂贴图" in service._slot_text({}), service._slot_text({}))

    fake.existing_items.discard(slot)                 # 这一项本身不存在
    check("读不到这一项 → has_texture None(不能说「没挂」)",
          service.has_texture() is None, str(service.has_texture()))

    fake.existing_items.add(slot)
    fake._values[slot] = 0.0
    check("别依赖「贴图方块的值」—— 实测它挂不挂都是 0",
          toolio.texture_slot_value() == 0.0, str(toolio.texture_slot_value()))


def check_probe_helpers() -> None:
    """探针脚本的图片头解析:纯标准库(它跑在 ZBrush 自带 Python 里,没有 PIL)。"""
    print("* 探针脚本:纯标准库解析 PNG / BMP / TIFF 尺寸")
    import ast as _ast                                                # noqa: PLC0415
    import struct                                                     # noqa: PLC0415
    import fake_zbrush                                                # noqa: PLC0415
    fake_zbrush.install(fake_zbrush.FakeCommands())
    path = os.path.join(PROJECT, "scripts", "probe_texture_paths.py")
    source = open(path, encoding="utf-8").read()
    check("探针末尾按 ZBrush 的加载方式直接跑,并把报告落盘/进剪贴板",
          source.rstrip().endswith("_run_and_report()"))
    check("探针不依赖 PIL(ZBrush 自带 Python 没有)",
          "from PIL" not in source and "import PIL" not in source)
    _ast.parse(source)                       # 语法自检(它要在真机上跑,别白跑一趟)
    body = source.rstrip()[: -len("_run_and_report()")]
    namespace: dict = {"__name__": "zbtools_probe"}
    exec(compile(body, path, "exec"), namespace)                      # noqa: S102
    image_size = namespace["image_size"]

    tmp = tempfile.mkdtemp(prefix="depthimg_probe_")
    png = os.path.join(tmp, "a.png")
    namespace["write_test_png"](png, 32)
    check("PNG 尺寸读对(32x32)", image_size(png) == (32, 32), str(image_size(png)))

    bmp = os.path.join(tmp, "b.bmp")
    row = b"\x00" * ((40 * 3 + 3) // 4 * 4)
    data = row * 24
    header = (b"BM" + struct.pack("<IHHI", 14 + 40 + len(data), 0, 0, 54)
              + struct.pack("<IiiHHIIiiII", 40, 40, 24, 1, 24, 0, len(data), 2835, 2835, 0, 0))
    with open(bmp, "wb") as handle:
        handle.write(header + data)
    check("BMP 尺寸读对(40x24)", image_size(bmp) == (40, 24), str(image_size(bmp)))

    tif = os.path.join(tmp, "c.tif")
    entries = b""
    for tag, value in ((256, 16), (257, 8)):
        entries += struct.pack("<HHI", tag, 3, 1) + struct.pack("<HH", value, 0)
    with open(tif, "wb") as handle:
        handle.write(b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 2)
                     + entries + struct.pack("<I", 0))
    check("TIFF 尺寸读对(16x8)", image_size(tif) == (16, 8), str(image_size(tif)))

    # 报告要能落盘(我直接读文件,用户不用手抄)
    captured = {"text": ""}

    def fake_main() -> None:
        print("ZBTools 贴图路径探测")
        print("  ✓  Texture:Import")

    # 探针整段跑一遍不崩(用假 ZBrush;真机上白跑一次太贵)。
    # ⚠ 探针里的 `zbc` 是 exec 时绑定的那个模块,所以要往**同一个** fake 上塞数据
    probe_fake = fake_zbrush.commands
    probe_fake.existing_items.update({"Texture:Import", "Texture:Export", "Texture:New",
                                      "Texture:Remove", "Tool:Texture Map",
                                      "Tool:Texture Map:Texture Map",
                                      "Tool:Texture Map:Texture On",
                                      "Tool:Texture Map:Import",
                                      "Tool:Texture Map:Export",
                                      "Tool:UV Map:Texture Map"})
    probe_fake.titles["Tool:Texture Map:Texture Map"] = "323"
    probe_fake.infos["Tool:Texture Map:Texture Map"] = (
        "Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)")
    try:
        namespace["main"]()
        check("探针整段能跑完(假 ZBrush,不抛异常)", True)
    except Exception as exc:                                          # noqa: BLE001
        check("探针整段能跑完(假 ZBrush,不抛异常)", False,
              f"{type(exc).__name__}: {exc}")

    namespace["main"] = fake_main
    namespace["_report_paths"] = lambda: [os.path.join(tmp, "report.txt")]
    namespace["_run_and_report"]()
    check("报告落盘了", os.path.exists(os.path.join(tmp, "report.txt")))
    if os.path.exists(os.path.join(tmp, "report.txt")):
        content = open(os.path.join(tmp, "report.txt"), encoding="utf-8").read()
        check("报告里有探测结果", "Texture:Import" in content, content[:40])


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:                                                 # noqa: BLE001
        return True
    return False


def main() -> int:
    check_licenses()
    check_variant_strictness()
    check_feature_contract()
    check_interpreter_and_args()
    check_texture_read_hazards()
    check_model_switch_validation()
    check_palette_focus()
    check_output_naming()
    check_texture_fast_path()
    check_slot_tooltip()
    check_probe_helpers()
    check_send_to_zbrush()
    check_main_flow()
    check_pipeline_cli()
    check_vendored_sources()
    check_da3_lite_fallback()
    print(f"\n{'全部通过' if not FAILED else '失败:' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
