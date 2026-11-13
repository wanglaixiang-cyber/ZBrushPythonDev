"""面板与交互测试(假 zbc 跑真实回调,不需要开 ZBrush)。

守这些约定:
  1. item path 每段不能含 ASCII 冒号(曾是"整面板空白"的事故)
  2. 面板 = 3 个按钮 + 1 个禁用分组标题:显示实际大小 / 核验显示比例 / 诊断信息
  3. 应用时保留视角、不动透视、不动位置;尺寸逐轴 = 网格 × ZBrush Scale
  4. 模型数值跟随 Scale Master 的显示单位,屏幕/画布尺寸始终 mm
  5. 异常(比例不可信)时提示只显示警告,且换底色
"""

from __future__ import annotations

# ---------------------------------------------------------------- #
# 这是一份**本地测试**,不是 ZBrush 脚本。
# 正确跑法(终端):.venv/bin/python tests/run_all.py
# 想在 ZBrush 里看功能,请 Load:scripts/load_plugin.py
# ---------------------------------------------------------------- #
try:
    from fake_zbrush import FakeCommands  # noqa: F401
except ImportError:  # 说明不在 tests/ 目录下运行 —— 多半是在 ZBrush 里 Load 了
    _MSG = (
        "⚠ 这是本地测试文件,不在 ZBrush 里运行。\n"
        "  本地跑:终端执行 .venv/bin/python tests/run_all.py\n"
        "  在 ZBrush 里看功能:Load  scripts/load_plugin.py"
    )
    print(_MSG)
    try:
        import zbrush.commands as _zbc
        _zbc.show_note(_MSG, display_duration=10.0)
    except Exception:
        pass
    raise SystemExit(0)

from fake_zbrush import FakeCommands, install                                     # noqa: E402
from zbtools import app as plugin, notes                                         # noqa: E402
from zbtools.features import true_scale as feature                               # noqa: E402
from zbtools.features.true_scale import service                                  # noqa: E402
from zbtools.errors import PluginError                                        # noqa: E402  (叶子模块)

PLUGIN_TITLE = "ZBTools"

#: 实时模块(每次 _install 后重新绑定既可用,避免绑定到旧替身)
app = plugin
svc = service
feat = feature
PATHS = feat.paths(PLUGIN_TITLE)


def _install(**kwargs):
    """装好替身后**重新导入**插件模块(否则拿到的是绑定旧替身的模块对象)。"""
    global app, svc, feat
    fake = FakeCommands(**kwargs)
    install(fake)
    import importlib
    app = importlib.import_module("zbtools.app")
    svc = importlib.import_module("zbtools.features.true_scale.service")
    feat = importlib.import_module("zbtools.features.true_scale")
    svc.copy_to_clipboard = lambda text: True              # 测试里不真的调 pbcopy
    app.register()
    return fake


def _reload_test(check) -> None:
    """第二次 Load 不能再 add_palette —— ZBrush 里名字没释放,会失败(踩过的坑)。

    现象:改完代码直接 Load,弹出"面板创建失败",只能关掉 ZBrush 重开。
    原因:界面操作是命令队列,close 之后同名 add 失败,而 delete_interface_item
    官方说明对 palette/sub-palette 无效。
    所以:面板已存在 → 复用顶层 + 重建子面板。
    """
    import importlib
    fake = FakeCommands()
    install(fake)
    # install() 清过 zbtools.* 的模块缓存 → 必须重新导入,否则拿到的是绑在旧替身上的模块
    plugin = importlib.import_module("zbtools.app")
    titles = [f.title for f in plugin.FEATURES]
    fake.existing_items.update({"ZBTools"} | {f"ZBTools:{t}" for t in titles})
    ok = plugin.register()
    check("热更新也装配成功", ok is True, str(fake.notes))
    check("没有再次 add_palette(名字没释放,再 add 会失败)",
          fake.palettes == [], str(fake.palettes))
    check("旧的子面板都被关掉",
          all(f"ZBTools:{t}" in fake.closed for t in titles), str(fake.closed))
    check("每个功能仍然新建了子面板", len(fake.subpalettes) == len(titles),
          str(fake.subpalettes))
    check("关掉之后 exists 变为假(说明真的移除,不是队列里挂着)",
          not any(fake.exists(f"ZBTools:{t}") for t in titles))


def test_item_paths_are_legal():
    for key, path in PATHS.items():
        segments = path.split(":")
        assert len(segments) == 3, f"{key}: 应为 面板:子面板:控件,实际 {segments}"
        assert all(s.strip() for s in segments), f"{key}: 有空层级 {segments}"
    for key, label in feat.LABELS.items():
        assert ":" not in label, f"标签含冒号:{key}={label!r}"
    print(f"  [ok] item path 合法(无内嵌冒号),子面板 = {PLUGIN_TITLE}:{feat.FEATURE.title}")


def _check_notebar_probe(check) -> None:
    """探针 ``scripts/probe_notebar.py`` 的约定 + 真的能在替身下整段跑完。

    为什么要这个探针:官方 ``set_notebar_text(text, progress)`` **就是**进度条
    (第二参数 0.0~1.0),"图片转深度图 / 贴图转色"已经在调用它、但进度一直写死 0.0;
    而"在脚本回调里反复更新到底刷不刷新、等子进程时能不能更新"文档里没有答案 ——
    先探针在真机上定性,再决定要不要把外部管线的进度映射进 ZBrush。

    这里用替身跑一遍(真机约 20 秒):用 ZBTOOLS_PROBE_SCALE 压到几十毫秒、
    不动用户的剪贴板、报告写到临时目录(别脏了 _probe/)。
    """
    import ast as _ast
    import contextlib
    import io
    import os as _os
    import pathlib
    import tempfile

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "probe_notebar.py"
    check("探针脚本在位", path.exists())
    src = path.read_text(encoding="utf-8")
    check("末尾按 ZBrush 的加载方式直接跑", src.rstrip().endswith("_run_and_report()"))
    mods: set[str] = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    allowed = {"os", "subprocess", "tempfile", "time", "contextlib", "io", "zbrush",
               "__future__"}
    check("只用标准库 + zbrush(ZBrush 自带 Python 没第三方)", mods <= allowed,
          str(sorted(mods - allowed)))

    fake = FakeCommands()
    install(fake)
    _os.environ["ZBTOOLS_PROBE_SCALE"] = "0.02"        # SCALE 在 exec 时读取,必须先设
    _os.environ["ZBTOOLS_PROBE_NO_CLIP"] = "1"
    try:
        body = src.rstrip()[: -len("_run_and_report()")]
        ns: dict = {"__name__": "zbtools_probe", "__file__": str(path)}
        exec(compile(body, str(path), "exec"), ns)                 # noqa: S102
        report = pathlib.Path(tempfile.mkdtemp()) / "probe_notebar.txt"
        ns["_report_paths"] = lambda: [str(report)]
        with contextlib.redirect_stdout(io.StringIO()):            # 探针输出很长,别刷屏
            ns["_run_and_report"]()
    finally:
        _os.environ.pop("ZBTOOLS_PROBE_SCALE", None)
        _os.environ.pop("ZBTOOLS_PROBE_NO_CLIP", None)

    check("报告落盘", report.exists())
    check("真的写了状态条", len(fake.notebar) > 10, str(len(fake.notebar)))
    check("喂了非 0 的 progress(不只是文字)",
          any(p > 0.0 for _t, p in fake.notebar), str(fake.notebar[:3]))
    check("走了 update(redraw_ui=True) 这条路",
          any(redraw for _n, redraw in fake.updates), str(fake.updates[:3]))
    check("试过官方 freeze()", len(fake.freezes) == 1, str(fake.freezes))
    check("结束清空了状态条(别留一条卡住)", fake.notebar[-1] == ("", 0.0),
          str(fake.notebar[-1]))
    if report.exists():
        text = report.read_text(encoding="utf-8")
        check("报告里带了要用户勾选的观察表",
              "T5(子进程期间)" in text and "T7 120 字" in text)


def main() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [ok] {name}")
        else:
            failures += 1
            print(f"  [FAIL] {name} {detail}")

    print("* item path 合法性")
    try:
        test_item_paths_are_legal()
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  [FAIL] {exc}")

    print("* 装配(网格 2 × ZBrush Scale 39.379 = 78.76mm,SM 选 mm)")
    fake = _install(mesh=2.0000193119049072, export_scale=39.37917709350586,
                              scalemaster_unit="mm")
    check("面板挂到右侧栏", fake.palettes == [(PLUGIN_TITLE, 1)], str(fake.palettes))
    from zbtools.features import FEATURES
    prefix = f"{PLUGIN_TITLE}:{feat.FEATURE.title}:"
    check("每个功能一个子面板", len(fake.subpalettes) == len(FEATURES), str(fake.subpalettes))
    headers = {p for p in fake.buttons if "----" in p}
    headers_here = {p for p in headers if p.startswith(prefix)}
    check("实际大小子面板 = 3 个按钮 + 1 个分组标题",
          {p for p in fake.buttons if p.startswith(prefix)} == set(PATHS.values()) | headers_here,
          str(list(fake.buttons)))
    check("分组标题被禁用(占位符,不是折叠菜单)",
          len(headers) == len(FEATURES) and all(p in fake.disabled for p in headers),
          str(fake.disabled))
    check("子面板用 title_mode=2(无标题栏、不会折叠)",
          all(mode == 2 for _path, mode in fake.subpalettes), str(fake.subpalettes))
    check("实际大小子面板无开关/滑块",
          not [p for p in fake.sliders if p.startswith(prefix)]
          and not [p for p in fake.switches if p.startswith(prefix)],
          str(list(fake.sliders)))
    check("注册时自动探测屏幕", app.CFG["ppi"] == 162.51, str(app.CFG.get("ppi")))

    print("* 显示实际大小")
    fake.transform[6:] = [15.0, 25.0, 35.0]
    fake.transform[0], fake.transform[1] = 300.0, 700.0
    fake.buttons[PATHS["apply"]]("sender")
    report = feat.STATE["report"]
    check("保留视角", fake.transform[6:] == [15.0, 25.0, 35.0], str(fake.transform[6:]))
    check("不动透视", fake._values["Draw:Perspective"] == 1.0)
    check("不动位置", (fake.transform[0], fake.transform[1]) == (300.0, 700.0), str(fake.transform[:3]))
    check("尺寸逐轴 = 网格 × ZBrush Scale", abs(report["size_mm"][0] - 78.759) < 0.01,
          str(report["size_mm"][0]))
    check("屏幕缩放分母是网格尺寸", abs(report["mesh_units"] - 2.0000193) < 1e-6,
          str(report["mesh_units"]))
    note = fake.notes[-1]
    lines = note.splitlines()
    check("提示 ≤5 行", len(lines) <= 5, str(len(lines)))
    check("第 1 行真实尺寸", lines[0].startswith("真实尺寸:"), lines[0])
    check("第 3 行画布边框且为 mm", lines[2].startswith("画布边框") and lines[2].endswith("mm"), lines[2])
    check("第 4 行视图状态", lines[3] in ("透视视图", "正交视图"), lines[3])
    check("正常提示不显示尺寸依据", "尺寸未变" not in note and "首次" not in note, note)
    check("底部无「可点…」等冗词", "可点" not in note and "1 unit = 1" not in note, note)

    print("* 单位跟随 Scale Master(只影响显示)")
    check("SM=mm 时提示用 mm", " mm" in lines[0], lines[0])
    fake.sm_unit = "cm"
    fake.buttons[PATHS["apply"]]("sender")
    lines_cm = fake.notes[-1].splitlines()
    check("SM=cm 时提示改用 cm(78.76mm → 7.88cm)", "7.88" in lines_cm[0] and "cm" in lines_cm[0],
          lines_cm[0])
    check("尺寸不变(仍 78.76mm)", abs(feat.STATE["report"]["size_mm"][0] - 78.759) < 0.01,
          str(feat.STATE["report"]["size_mm"][0]))
    check("画布仍用 mm", lines_cm[2].endswith("mm"), lines_cm[2])
    fake.sm_unit = "mm"

    print("* 面板按钮齐备(切换视角按钮已按需求删除)")
    check("实际大小面板没有切换视角按钮", "restore" not in PATHS, str(sorted(PATHS)))

    print("* 核验显示比例(预填探测值)")
    expected = svc.canvas.canvas_size()[0] * svc.screen.mm_per_pixol(app.CFG)
    fake.ask_answer = f"{expected:.1f}"
    fake.buttons[PATHS["verify"]]("sender")
    initial, title = fake.asks[-1]
    check("输入框预填探测值", abs(float(initial) - expected) < 0.1, f"{initial} vs {expected:.1f}")
    check("提示语说明单位 mm", "mm" in title, title[:40])
    check("确认预填值 → 系数≈1.0", abs(app.CFG["bridge_factor"] - 1.0) < 0.01,
          str(app.CFG["bridge_factor"]))
    fake.ask_answer = f"{expected * 1.2:.1f}"
    fake.buttons[PATHS["verify"]]("sender")
    check("填实测值 → 系数被修正", app.CFG["bridge_factor"] > 1.1, str(app.CFG["bridge_factor"]))
    app.CFG["bridge_factor"] = 1.0

    print("* 异常时只抛警告 + 换底色")
    fake_bad = _install(mesh=0.0359, export_scale=10000.0, scalemaster_unit="mm")
    fake_bad.buttons[PATHS["apply"]]("sender")
    check("只显示一行警告", fake_bad.notes[-1].startswith("警告:") and len(fake_bad.notes[-1].splitlines()) == 1,
          fake_bad.notes[-1][:50])
    check("警告换底色", fake_bad.note_styles[-1].get("bg_color") == notes.NOTE_BG_WARNING,
          str(fake_bad.note_styles[-1]))
    check("警告停留更久",
          fake_bad.note_styles[-1].get("display_duration") == notes.NOTE_DURATION_WARNING,
          str(fake_bad.note_styles[-1]))
    check("正常提示用默认底色", fake.note_styles[-1].get("bg_color") == notes.NOTE_BG_NORMAL,
          str(fake.note_styles[-1]))

    print("* 三维尺寸逐轴(回归:曾经固定成正方体)")
    fake_box = _install(mesh_xyz=(2.0, 1.0, 0.5), export_scale=1.0, scalemaster_unit="mm")
    fake_box.buttons[PATHS["apply"]]("sender")
    box_line = fake_box.notes[-1].splitlines()[0]
    check("逐轴正确(2 x 1 x 0.5 mm)", "2.00 x 1.00 x 0.50 mm" in box_line, box_line)

    print("* 诊断信息(打印 + 复制到剪贴板)")
    fake.buttons[PATHS["diagnostics"]]("sender")
    check("提示已复制到剪贴板", any("剪贴板" in n for n in fake.notes), str(fake.notes[-1:]))
    text = svc.diagnostics(app.CFG, feat.STATE["report"])
    for kw in ("[1] 模型数据", "[2] 屏幕数据", "[3] 映射", "Tool:Export:Scale",
               "最近一次「显示实际大小」"):
        check(f"诊断包含「{kw}」", kw in text)

    print("* 热更新:面板已存在时**复用顶层**,只重建子面板")
    _reload_test(check)

    print("* 控件创建失败必须报出来")
    failing = _install(fail_button=PATHS["apply"])
    check("register() 返回 False", failing.palettes and any("创建失败" in n for n in failing.notes),
          str(failing.notes[-1:]))

    print("* notebar 探针(scripts/probe_notebar.py):stdlib-only + 替身下能整段跑完")
    try:
        _check_notebar_probe(check)
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  [FAIL] 探针检查抛异常:{type(exc).__name__}: {exc}")

    print("\n" + ("全部通过" if not failures else f"{failures} 项失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
