"""进度机制测试(不开 ZBrush):``proc`` 流式跑子进程 + ``ui.Progress`` 映射到状态条。

覆盖三块:

  1. **``proc.stream``**:边跑边回调(不是等进程结束才给结果)、超时会杀、返回码/尾部输出
     都拿得到;``unbuffered`` 会给 python 加 ``-u``(不加的话管道块缓冲,进度行会攒到最后
     一次性冒出来 —— 实测踩过的坑)。
  2. **``ui.parse_progress``**:认管线约定 ``[zbtools-progress] 0.42``,越界夹到 0~1,
     别的行返回 None(不能把普通日志误当进度)。
  3. **``ui.Progress``**:节流、阶段跳变、按时间爬升但**永不越过 ceiling**、收尾能清空、
     文字超长自动截(实测状态条约 80 字就被截)。

判据全部来自真机探针 ``scripts/probe_notebar.py``(ZBrush 2026.2):回调里更新会自己刷新、
等子进程期间也能刷新、单次 ~4ms。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(PROJECT, "plugin", "zbtools")
sys.path.insert(0, PLUGIN)

import fake_zbrush                                                   # noqa: E402
from zbtools import proc                                            # noqa: E402


def _ui():
    """**必须**在 ``fake_zbrush.install()`` 之后再取 ``zbtools.ui``。

    ``ui`` 在 import 时就 ``import zbrush.commands`` 并绑定了那个模块对象,所以文件顶部
    提前 import 的话,拿到的是真存根(``set_notebar_text`` 是空实现 → 替身一条都记不到)。
    ``install()`` 会清掉 ``zbtools.*`` 的模块缓存,所以每次重新 import 都会拿到绑新替身的。
    """
    import importlib                                                # noqa: PLC0415
    return importlib.import_module("zbtools.ui")

FAILED: list[str] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {what}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(what)


# --------------------------------------------------------------------------- #
# 1. proc:流式 / 超时 / 尾部
# --------------------------------------------------------------------------- #
def check_unbuffered() -> None:
    print("* proc.unbuffered:给 python 加 -u(不然进度行会被块缓冲)")
    got = proc.unbuffered(["/usr/bin/python3", "pipe.py", "--in", "x"])
    check("python 解释器 → 插到第二位", got == ["/usr/bin/python3", "-u", "pipe.py", "--in", "x"],
          str(got))
    check("已经是 -u 就不重复插",
          proc.unbuffered(["python3", "-u", "a.py"]) == ["python3", "-u", "a.py"])
    check("不是 python 的命令原样返回",
          proc.unbuffered(["/bin/sleep", "3"]) == ["/bin/sleep", "3"])
    check("单元素也不炸", proc.unbuffered(["python3"]) == ["python3"])


def check_stream() -> None:
    print("* proc.stream:边跑边回调(不是等结束才给结果)")
    code = ("import sys, time\n"
            "print('one', flush=True)\n"
            "time.sleep(0.45)\n"
            "print('two', flush=True)\n")
    seen: list[tuple[str, float]] = []
    ticks = {"n": 0}
    t0 = time.monotonic()
    rc, lines, timed_out = proc.stream(
        [sys.executable, "-u", "-c", code], timeout=10.0,
        on_line=lambda line: seen.append((line, time.monotonic() - t0)),
        on_tick=lambda: ticks.__setitem__("n", ticks["n"] + 1), tick=0.05)
    total = time.monotonic() - t0
    check("返回码 0", rc == 0, str(rc))
    check("没超时", timed_out is False)
    check("两行都收到了", [t for t, _ in seen] == ["one", "two"], str(seen))
    check("**第一行在进程结束前就回调了** —— 真的是流式,不是一次性喂",
          bool(seen) and seen[0][1] < total - 0.2,
          f"第一行 {seen[0][1]:.2f}s / 总 {total:.2f}s" if seen else "没收到行")
    check("子进程不打字的空档里 tick 一直在跳(进度条就挂这儿)", ticks["n"] >= 3, str(ticks["n"]))

    print("* proc.stream:超时会被杀掉,并且**已经收到的输出仍然拿得到**")
    slow = ("import time\nprint('started', flush=True)\ntime.sleep(30)\n")
    t0 = time.monotonic()
    rc, lines, timed_out = proc.stream([sys.executable, "-u", "-c", slow], timeout=0.6)
    spent = time.monotonic() - t0
    check("超时标记为 True", timed_out is True)
    check("被杀掉了(返回码 -9)", rc == -9, str(rc))
    check("没真等 30 秒", spent < 5.0, f"{spent:.2f}s")
    check("超时前收到的输出还在(报错要用它)", "started" in lines, str(lines))
    check("proc.tail 取最后几行", proc.tail(["a", "b", "c", "d"], 2) == "c\nd",
          repr(proc.tail(["a", "b", "c", "d"], 2)))

    print("* proc.stream:stdin 接 DEVNULL —— 子进程读输入不该把整件事挂住")
    reader_code = ("import sys\n"
                   "print('before', flush=True)\n"
                   "data = sys.stdin.readline()      # DEVNULL → 立刻拿到 ''\n"
                   "print('after' if data == '' else 'got', flush=True)\n")
    t0 = time.monotonic()
    rc, lines, timed_out = proc.stream([sys.executable, "-u", "-c", reader_code], timeout=5.0)
    spent = time.monotonic() - t0
    check("读 stdin 的子进程没有卡住", timed_out is False and spent < 3.0, f"{spent:.2f}s")
    check("两个标记都打出来了(stdin 是 EOF,不是挂起)",
          lines[-2:] == ["before", "after"], str(lines))

    print("* proc.stream:回调抛异常不会毁掉主流程 / 也不会漏杀进程")
    def boom_tick():
        raise RuntimeError("回调故意炸")
    code2 = ("import time\nprint('one', flush=True)\ntime.sleep(0.3)\nprint('two', flush=True)\n")
    rc, lines, timed_out = proc.stream([sys.executable, "-u", "-c", code2], timeout=5.0,
                                       on_tick=boom_tick, on_line=lambda _l: None)
    check("on_tick 一直抛异常,子进程照样跑完", rc == 0 and lines == ["one", "two"],
          f"rc={rc} lines={lines}")

    print("* proc.stream:失败返回码会传出来(插件据此报错)")
    rc, lines, timed_out = proc.stream(
        [sys.executable, "-u", "-c", "import sys; print('boom', flush=True); sys.exit(3)"],
        timeout=10.0)
    check("返回码 3 传出来", rc == 3, str(rc))
    check("stderr 也并进来了", any("boom" in l for l in lines), str(lines))


# --------------------------------------------------------------------------- #
# 2. 进度行解析
# --------------------------------------------------------------------------- #
def check_parse() -> None:
    print("* ui.parse_progress:只认约定行,别的日志不能误判")
    ui = _ui()
    check("正常值", ui.parse_progress("[zbtools-progress] 0.42") == 0.42)
    check("带空格", ui.parse_progress("  [zbtools-progress]   0.5  ") == 0.5)
    check("超出 1 会夹住", ui.parse_progress("[zbtools-progress] 3") == 1.0)
    check("负数夹到 0", ui.parse_progress("[zbtools-progress] -1") == 0.0)
    check("普通日志 → None", ui.parse_progress("[bake] 读低模 x.obj") is None)
    check("垃圾数字 → None", ui.parse_progress("[zbtools-progress] abc") is None)
    check("空行 → None", ui.parse_progress("") is None)


# --------------------------------------------------------------------------- #
# 3. Progress(状态条)
# --------------------------------------------------------------------------- #
def check_progress() -> None:
    print("* ui.Progress:节流 / 阶段 / 爬升不越界 / 收尾清空")
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    ui = _ui()                      # 必须在 install() 之后(见 _ui 的说明)

    prog = ui.Progress("测试", eta=1.0, throttle=0.0)
    check("创建就写了一次状态条", len(fake.notebar) == 1, str(fake.notebar))
    check("文字带百分比", fake.notebar[-1][0].endswith("0%"), fake.notebar[-1][0])

    prog.stage("阶段 A", 0.2)
    check("stage 是强制刷新(不受节流影响)",
          fake.notebar[-1] == ("阶段 A 20%", 0.2), str(fake.notebar[-1]))
    check("stage 之后天花板只比它高一点(默认 +5%)",
          abs(prog.ceiling - 0.25) < 1e-9, str(prog.ceiling))

    prog.set_ceiling(0.6)
    for _ in range(50):
        prog.creep()
        time.sleep(0.005)
    check("爬升会往上走", prog.value > 0.2, str(prog.value))
    check("**永不越过 ceiling**(不假报进度)", prog.value <= 0.6 + 1e-9, str(prog.value))
    check("喂给 ZBrush 的值就是爬升后的值",
          abs(fake.notebar[-1][1] - prog.value) < 1e-9, str(fake.notebar[-1]))

    prog.stage("阶段 B", 0.6)
    prog.set_ceiling(0.95)
    prog.creep()
    check("stage 之后仍然单调不减", prog.value >= 0.6 - 1e-9, str(prog.value))
    check("tick 不倒退", prog.ceiling >= prog.value)

    long_text = "读权重 " + "x" * 200
    prog.stage(long_text, 0.7)
    rendered = fake.notebar[-1][0]
    check(f"超长文字被截到 {ui.Progress.TEXT_MAX} 字以内", len(rendered) <= ui.Progress.TEXT_MAX,
          f"{len(rendered)} 字")
    check("截断后仍然带百分比", rendered.endswith("70%"), rendered[-8:])

    print("* ui.Progress:**只增不减**(管线真进度与阶段表尺度不一致时也不能倒退)")
    prog2 = ui.Progress("单调", eta=1.0, throttle=0.0)
    prog2.stage("先到 0.8", 0.8)
    prog2.stage("又来一个 0.3(尺度打架)", 0.3)
    check("倒退的 stage 被兜住(值仍是 0.8)", abs(prog2.value - 0.8) < 1e-9, str(prog2.value))
    check("状态条也没被写成 0.3", fake.notebar[-1][1] >= 0.8, str(fake.notebar[-1]))

    prog.done()
    check("done() 真的清空了状态条('' + 0.0)",
          fake.notebar[-1] == ("", 0.0), str(fake.notebar[-1]))

    print("* ui.guard:异常路径要**把进度条清掉**(卡住的进度条比没有更误导)")
    fake.notebar.clear()
    fake_zbrush.install(fake)          # guard 走 ui 模块,换替身要重新 import
    ui2 = _ui()
    from zbtools.errors import PluginError as _PE                      # noqa: PLC0415
    ui2.Progress("会被中断的任务", eta=10.0)
    ui2.guard("测试动作", lambda: (_ for _ in ()).throw(_PE("模拟失败")))
    check("失败后状态条被清空", fake.notebar[-1] == ("", 0.0), str(fake.notebar[-1]))
    fake.notebar.clear()
    ui2.Progress("会被异常打断的任务", eta=10.0)
    ui2.guard("测试动作", lambda: 1 / 0)
    check("普通异常后状态条也被清空", fake.notebar[-1] == ("", 0.0), str(fake.notebar[-1]))
    check("成功路径不会留下脏状态",
          ui2.clear_stuck_progress() is None and fake.notebar[-1] == ("", 0.0))

    print("* ui.Progress:节流(默认 100ms)不会把状态条刷爆")
    fake.notebar.clear()
    throttled = ui.Progress("节流", throttle=10.0)          # 10 秒内只该写一次
    for _ in range(20):
        throttled.creep()
    check("节流生效(20 次 creep 只写了 1 次)", len(fake.notebar) == 1, str(len(fake.notebar)))
    throttled.done()


def _fake_pipeline(path: str, lines: list[str], out_arg: str, report: str | None,
                   exit_code: int = 0) -> None:
    """写一个假的管线脚本:按给定顺序打日志,写出产物(和可选的 JSON 报告),然后退出。"""
    body = ["import sys, os, json, time",
            "args = sys.argv[1:]",
            "def val(flag, default=''):",
            "    return args[args.index(flag) + 1] if flag in args else default",
            "out = val('%s')" % out_arg,
            "time.sleep(0.05)"]
    for line in lines:
        body.append(f"print({line!r}, flush=True)")
        body.append("time.sleep(0.05)")
    body.append("open(out, 'wb').write(b'fake')")
    if report:
        body.append(f"open({report!r}, 'w', encoding='utf-8').write(json.dumps({{'out': out, 'out_size': [48, 48], 'seconds': 0.1, 'engine': 'dav2', 'out_min': 0, 'out_max': 255, 'out_mean': 1.0}}, ensure_ascii=False))")
    body.append(f"sys.exit({exit_code})")
    pathlib.Path(path).write_text("\n".join(body) + "\n", encoding="utf-8")


def check_run_depth_streaming() -> None:
    """run_depth:管线的阶段日志 + 进度行 → 状态条(用假管线真跑一遍,不碰 torch)。"""
    print("* run_depth:边跑边把阶段/进度映射到状态条(假管线,真子进程)")
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.features.depthimg import service                      # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="zbtools_stream_")
    weight = os.path.join(tmp, "depth_anything_v2_vits_fp32.safetensors")
    pathlib.Path(weight).write_bytes(b"stub")
    out = os.path.join(tmp, "depth.png")
    pipe = os.path.join(tmp, "fake_depth_pipeline.py")
    _fake_pipeline(pipe, [
        "[深度图] 引擎 dav2(vits),权重 depth_anything_v2_vits_fp32.safetensors(0.09 GiB),设备 cpu;推理边长 518",
        "[深度图] 权重加载 0.1s",
        "[深度图] 推理 0.2s @ 336x518",
        "[zbtools-progress] 0.60",
        "[深度图] 放大回原尺寸:引导滤波(跟着原图边缘)",
        "[zbtools-progress] 0.78",
        "[深度图] 完成:48x48 灰度 PNG,用时 0.1s;像素 0~255(均值 1.0)",
    ], "--out", out + ".report.json")
    real_path = service.depth_pipeline_path
    service.depth_pipeline_path = lambda: pipe
    try:
        cfg = {"depthimg__engine": "dav2", "depthimg__variant": "vits",
               "depthimg__model": weight, "depthimg__interpreter": sys.executable,
               "depthimg__timeout_s": 60.0}
        report = service.run_depth(cfg, "in.png", out)
    finally:
        service.depth_pipeline_path = real_path

    check("报告读到了", report.get("out_size") == [48, 48], str(report))
    values = [p for _t, p in fake.notebar]
    check("状态条被更新了", len(values) >= 3, str(values))
    check("进度**单调不减**(不会倒着走)", all(b >= a - 1e-9 for a, b in zip(values, values[1:])),
          str(values))
    check("进度都在 0~1 里", all(0.0 <= v <= 1.0 for v in values), str(values))
    check("管线自己报的进度真的用上了(出现了 >0.3 的中间值)",
          any(0.29 < v < 0.95 for v in values), str(values))
    check("**报完真进度、阶段表又跳一次这种行序也不倒退**",
          all(b >= a - 1e-9 for a, b in zip(values, values[1:])), str(values))
    check("阶段文字里带上了阶段名",
          any(("推理" in t) or ("后处理" in t) for t, _p in fake.notebar),
          str([t for t, _p in fake.notebar]))
    check("进度行**没有**被当成日志打给用户(控制台不该出现 [zbtools-progress])",
          not any("zbtools-progress" in t for t, _p in fake.notebar))

    print("* run_depth:管线失败时,报错里带**已经收到的尾部输出**")
    pipe2 = os.path.join(tmp, "fake_fail.py")
    _fake_pipeline(pipe2, ["[深度图] 权重加载 0.1s", "[深度图] ✗ MissingDependency: 需要 torch"],
                   "--out", None, exit_code=3)
    service.depth_pipeline_path = lambda: pipe2
    try:
        service.run_depth(dict(cfg), "in.png", out)
        check("失败时抛 PluginError", False, "没抛错")
    except Exception as exc:                                           # noqa: BLE001
        text = str(exc)
        check("失败时抛 PluginError", type(exc).__name__ == "PluginError", type(exc).__name__)
        check("报错里有返回码", "3" in text, text[:60])
        check("报错里带上了尾部的真实输出(排查要靠它)", "MissingDependency" in text,
              text[-80:])
    finally:
        service.depth_pipeline_path = real_path


def check_bake_streaming() -> None:
    """bake() 同样要流式 + 报进度(第二个功能的同一套机制)。"""
    print("* bake():流式 + 进度映射(假烘焙管线)")
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    from zbtools.features.tex2pp import service                        # noqa: PLC0415

    tmp = tempfile.mkdtemp(prefix="zbtools_bake_")
    out_ply = os.path.join(tmp, "o.ply")
    pipe = os.path.join(tmp, "fake_bake_pipeline.py")
    _fake_pipeline(pipe, [
        "[bake] 读低模 low.obj",
        "[bake] 读高模 high.obj",
        "[bake] 查询最近点(高模每顶点 → 低模表面)…",
        "[zbtools-progress] 0.40",
        "[zbtools-progress] 0.70",
        "[bake] 写出 o.ply  (0.1 MB)",
        "[bake] 总耗时 0.1s",
    ], "--out", out_ply + ".report.json")
    real_path = service.pipeline_path
    service.pipeline_path = lambda: pipe
    try:
        cfg = {"tex2pp__timeout_s": 60.0, "interpreter": sys.executable}
        report = service.bake(cfg, "low.obj", "high.obj", "tex.png", out_ply)
    finally:
        service.pipeline_path = real_path

    values = [p for _t, p in fake.notebar]
    check("烘焙报告读到了", report.get("out_size") == [48, 48], str(report))
    check("状态条被更新了", len(values) >= 3, str(values))
    check("进度单调不减", all(b >= a - 1e-9 for a, b in zip(values, values[1:])), str(values))
    check("采样那段的真进度用上了", any(0.3 < v < 0.9 for v in values), str(values))


def main() -> int:
    check_unbuffered()
    check_stream()
    check_parse()
    check_progress()
    check_run_depth_streaming()
    check_bake_streaming()
    print("\n" + ("全部通过" if not FAILED else f"失败:{', '.join(FAILED)}"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
