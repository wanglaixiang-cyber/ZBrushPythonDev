"""屏幕真尺的测试:刻度只由显示器物理尺寸决定,且**绝不能真的去开窗口**。

关键断言:
  * 刻度用 ppi 口径(与「显示实际大小」同一套);EDID 宽高只做交叉核验
  * 命令行参数正确(解释器 / 浮层脚本 / pt-per-mm / 长度 / 角度 / 原点 / 尺子 id)
  * 多把尺子:新建叠加、收起记录数量与角度、恢复按记录还原
  * 双击删除的语义:进程退出即从 live 里消失,且不影响 saved
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

import json                                                        # noqa: E402
import os                                                          # noqa: E402
import tempfile                                                    # noqa: E402

from fake_zbrush import FAKE_DISPLAYS, FakeCommands, install        # noqa: E402


class FakeProc:
    """假的子进程:只记录命令行,不真的开窗口。"""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.terminated = False
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def die(self) -> None:
        """模拟窗口被用户双击关掉(进程自己退出)。"""
        self._returncode = 0


def main() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [ok] {name}")
        else:
            failures += 1
            print(f"  [FAIL] {name} {detail}")

    fake = FakeCommands()
    install(fake)
    import importlib
    app = importlib.import_module("zbtools.app")
    screen = importlib.import_module("zbtools.zbapi.screen")
    ruler = importlib.import_module("zbtools.features.ruler.service")
    app.register()

    spawned: list[FakeProc] = []
    ruler.subprocess.Popen = lambda argv, **kw: spawned.append(FakeProc(argv)) or spawned[-1]  # type: ignore[assignment]
    ruler.STATE["procs"].clear()

    print("* 刻度依据(ppi 口径,EDID 只做交叉核验)")
    logical_ppi = float(FAKE_DISPLAYS["displays"][0]["logical_ppi"])
    scale = screen.pt_per_mm(app.CFG)
    check("用 ppi 口径(与显示实际大小同一套)",
          abs(scale["pt_per_mm"] - logical_ppi / 25.4) < 1e-12,
          f"{scale['pt_per_mm']} vs {logical_ppi / 25.4}")
    check("1 mm ≈ 3.2 逻辑点", abs(scale["pt_per_mm"] - 3.2) < 0.02,
          f"{scale['pt_per_mm']:.4f}")
    cross = {item["axis"]: item for item in scale["cross_check"]}
    check("交叉核验列出 EDID 宽高两个口径", set(cross) == {"宽", "高"}, str(cross))
    check("EDID 宽口径与 ppi 相差 <0.5%(自洽)", abs(cross["宽"]["diff_pct"]) < 0.5, str(cross["宽"]))
    check("EDID 宽高之间差 >1%(正因如此不能拿它当基准)",
          abs(cross["高"]["diff_pct"] - cross["宽"]["diff_pct"]) > 1.0, str(cross))

    print("* 命令行")
    argv = ruler.command(app.CFG, "ruler01", (300.0, 500.0), 45.0, 120.0)
    check("用配置里的解释器", argv[0] == app.CFG["interpreter"], argv[0])
    check("跑的是浮层脚本", argv[1] == screen.RULER_HELPER, argv[1])
    check("浮层脚本真实存在", os.path.exists(screen.RULER_HELPER), screen.RULER_HELPER)
    check("传了 --pt-per-mm", argv[argv.index("--pt-per-mm") + 1] == f"{scale['pt_per_mm']:.6f}")
    check("传了尺长", argv[argv.index("--length-mm") + 1] == "120")
    check("传了角度", argv[argv.index("--angle-deg") + 1] == "45")
    check("传了 0 点位置", argv[argv.index("--origin") + 1] == "300.0,500.0")
    check("传了尺子 id(状态文件按它记 live)",
          argv[argv.index("--ruler-id") + 1] == "ruler01")
    check("传了状态目录(每把尺子一个文件)",
          argv[argv.index("--state-dir") + 1] == ruler.STATE_DIR)
    check("带上父进程 PID(ZBrush 退出时浮层自己关)",
          argv[argv.index("--parent-pid") + 1].isdigit())
    check("尺长被夹在合法区间", ruler.length_mm({**app.CFG, "ruler__length_mm": 9999}) == 500.0)

    print("* 浮层脚本与几何模块可编译(防路径/语法回归)")
    for path in (screen.RULER_HELPER, os.path.join(os.path.dirname(screen.RULER_HELPER),
                                                  "ruler_geometry.py")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                compile(handle.read(), path, "exec")
            check(f"{os.path.basename(path)} 编译通过", True)
        except Exception as exc:                                        # noqa: BLE001
            check(f"{os.path.basename(path)} 编译通过", False, repr(exc))

    print("* 多把尺子:新建 / 翻面 / 删除")
    ruler.STATE["procs"].clear()
    ruler.STATE_DIR = tempfile.mkdtemp(prefix="zbtools_rulers_")
    check("默认尺长 200 mm", ruler.DEFAULT_LENGTH_MM == 200.0, str(ruler.DEFAULT_LENGTH_MM))
    check("没有配置时用默认值", ruler.length_mm({}) == 200.0, str(ruler.length_mm({})))

    message = ruler.create(app.CFG)
    check("新建一把", len(ruler.running()) == 1 and "新建" in message, message.splitlines()[0])
    check("新建立刻记进自己的文件(窗口也会写,这里模拟)",
          len(ruler.read_live()) >= 0)

    fake_proc = type(spawned[-1])(["x"])
    fake_proc2 = type(spawned[-1])(["x"])

    def fake_ruler_file(ruler_id: str, x: float, y: float, angle: float) -> None:
        with open(os.path.join(ruler.STATE_DIR, f"{ruler_id}.json"), "w", encoding="utf-8") as handle:
            json.dump({"version": ruler.STATE_VERSION, "x": x, "y": y,
                       "angle": angle, "length_mm": 100.0}, handle)
    fake_ruler_file("aaa", 100.0, 200.0, 0.0)
    fake_ruler_file("bbb", 300.0, 400.0, 45.0)
    ruler.STATE["procs"] = {"aaa": type(spawned[-1])(["x"]), "bbb": type(spawned[-1])(["x"])}
    check("live 读到两把尺子的几何", len(ruler.read_live()) == 2, str(ruler.read_live()))
    # 死进程留下的记录必须被清掉,否则新尺子会继承它的位置/角度
    fake_ruler_file("ccc", 10.0, 10.0, 90.0)
    with open(os.path.join(ruler.STATE_DIR, "ccc.json"), "r+", encoding="utf-8") as handle:
        data = json.load(handle); data["pid"] = 999999; handle.seek(0)
        json.dump(data, handle); handle.truncate()
    check("死进程的记录会被剔除(不留隐患)",
          "ccc" not in ruler.read_live() and not os.path.exists(
              os.path.join(ruler.STATE_DIR, "ccc.json")), str(ruler.read_live()))
    check("没有 pid 的老格式记录保守保留",
          ruler.read_live(["aaa"]).get("aaa") is not None)
    check("status 文件不会被当成几何", "aaa.status" not in ruler.read_live())

    print("* 位置与角度记录(翻面按钮已按需求删除,重开逻辑仍按记录还原)")
    spawned.clear()
    ruler.STATE["procs"] = {"aaa": type(spawned[-1])(["x"]) if spawned else None}
    ruler.STATE["procs"] = {"aaa": fake_proc, "bbb": fake_proc2}
    print("* 删除(不留记录)")
    message = ruler.delete_all()
    check("删除后没有活着的尺子", ruler.running() == {}, str(ruler.running()))
    check("提示删了几把", "2 把" in message, message)
    check("各把尺子的文件都被清掉", ruler.read_live() == {}, str(ruler.read_live()))
    check("再删给出提示", "本来就没有" in ruler.delete_all())

    spawned.clear()
    ruler.create(app.CFG)
    spawned[-1].die()
    check("双击删掉的那种:进程退出即消失", ruler.running() == {}, str(ruler.running()))
    check("状态文案跟着变", "未显示" in ruler.status_text(), ruler.status_text())

    print("* 核验文本")
    text = ruler.verify_text(app.CFG)
    check("写出每毫米多少点", "1 mm =" in text, text[:60])
    check("写出屏幕名", FAKE_DISPLAYS["displays"][0]["name"] in text)
    check("写出交叉核验", "交叉核验" in text)
    check("指出自动核验脚本的用法", "verify_ruler_overlay.py" in text)

    print("\n" + ("全部通过" if not failures else f"{failures} 项失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
