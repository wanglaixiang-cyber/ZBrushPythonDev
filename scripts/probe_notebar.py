"""探测脚本:ZBrush 顶部状态条能不能当"外部处理的进度条"用。

为什么要探(三件事文档里都没有答案):

  1. 官方 SDK 的 ``set_notebar_text(text, progress)`` **就是**进度条 ——
     原文:*"This is the equivalent of a progress bar in other applications, but it is shown
     at the top of the UI instead of the bottom."*,``progress`` 取 ``0.0``(关)~``1.0``。
     「图片转深度图」/「贴图转色」**已经在调用它了**,只是第二个参数一直写死 ``0.0``。
  2. 但**在脚本回调里反复更新它,界面到底会不会重绘?** 要不要每轮搭一次
     ``zbc.update(1, redraw_ui=True)``?ZBrush 的界面操作是命令队列,这一条不明说。
  3. 更关键:真实的等待是**外部子进程**在跑(depth/bake 管线),那段时间里更新还作不作数?
     —— 这决定"要不要把外部管线的进度映射进 ZBrush",所以先用探针定性,别直接写功能。

用法:ZBrush > ZScript 面板 > Python Scripting 子面板 > Load > 本文件。

⚠ 跑的时候请**盯着 ZBrush 顶部那条状态条**(notebar):每一步之前脚本会打印"请看顶部"。
   约 20 秒跑完;报告会落盘(`_probe/probe_notebar_report.txt`)并复制到剪贴板 ——
   请把报告**整段**发我,并把你**实际看到的**填在报告末尾那张表里。

测什么(每步 1~4 秒):

  T0 基线:只设文字(``progress=0``)        → 有文字吗?有条吗?
  T1 只 ``set_notebar_text``,不 update     → 条动不动?
  T2 每轮 ``update(1, redraw_ui=True)``     → 条动不动?
  T3 每轮 ``update(1, redraw_ui=False)``    → 和 T2 有什么区别?
  T4 官方 ``freeze()`` 包住忙循环           → 官方说"冻结 UI 时进度条仍工作",验证一下
  T5 **真实场景**:子进程(sleep 4s)在跑,父进程每 100ms 更新 → 这才是要用的姿势
  T6 反例:``subprocess.run`` 阻塞 3 秒,期间不更新 → 证明"必须换 Popen 才能映射进度"
  T7 长文字:40 / 80 / 120 字各一次          → 被截断在第几个字?(决定阶段文字能写多长)
  T8 清理:``set_notebar_text("", 0)``       → 条能不能正常消失
  T9 开销:100 次 set(带/不带 update)各多久 → 决定刷新节流(50/100/200ms)

本脚本**只用标准库**(它跑在 ZBrush 自带 Python 里,没有 PIL / numpy)。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time

import zbrush.commands as zbc

#: 每一步的观察点会写进报告末尾,方便你对着填
REPORT: list[str] = []

#: 所有等待时间的缩放系数。真机跑保持 1.0(总共约 20 秒,人才看得清);
#: 测试里用环境变量压到很小,免得 CI/单测白等 20 秒。
SCALE = float(os.environ.get("ZBTOOLS_PROBE_SCALE", "1") or 1)


def say(text: str) -> None:
    print(text, flush=True)


def watch(label: str, seconds: float) -> None:
    """给用户一句"请看顶部",然后等一会儿。"""
    say(f"   👉 请看 ZBrush **顶部状态条**:{label}")
    time.sleep(seconds * SCALE)


def step(name: str, note: str) -> None:
    REPORT.append("")
    REPORT.append(f"[{name}] {note}")


def timed(fn) -> tuple[str, float]:
    """跑一个调用并返回 (结果描述, 耗时秒)。异常不让整段探针挂掉。"""
    t0 = time.time()
    try:
        fn()
        return "ok", time.time() - t0
    except Exception as exc:                                          # noqa: BLE001
        return f"{type(exc).__name__}: {exc}", time.time() - t0


def has_update() -> bool:
    return callable(getattr(zbc, "update", None))


def has_freeze() -> bool:
    return callable(getattr(zbc, "freeze", None))


# --------------------------------------------------------------------------- #
# T0~T3:回调里更新到底刷不刷新
# --------------------------------------------------------------------------- #
def t0_baseline() -> None:
    step("T0", "只设文字、progress=0(关)")
    say("* T0 只设文字(progress=0):先看有没有文字、有没有进度条")
    for text in ("ZBTools 探针 T0:只有文字", "ZBTools 探针 T0:半条(progress=0.5)"):
        p = 0.0 if "只有文字" in text else 0.5
        desc, sec = timed(lambda t=text, v=p: zbc.set_notebar_text(t, v))
        say(f"   set_notebar_text({text!r}, {p}) → {desc}({sec * 1000:.1f}ms)")
        watch(text, 1.2)
    REPORT.append("     期望看到:第一句只有文字;第二句应该出现**半条**进度")


def loop_update(label: str, redraw: object, steps: int = 20, delay: float = 0.1) -> None:
    """跑一轮进度循环。``redraw`` 为 None = 不调 update,否则传给 update(redraw_ui=...)。"""
    for i in range(1, steps + 1):
        p = i / steps
        desc, _sec = timed(lambda v=p, i=i: zbc.set_notebar_text(f"探针 {label} {v * 100:.0f}%", v))
        if desc != "ok":
            say(f"   ⚠ set_notebar_text 失败:{desc}")
            break
        if redraw is not None and has_update():
            timed(lambda r=redraw: zbc.update(1, r))
        time.sleep(delay * SCALE)


def t1_no_update() -> None:
    step("T1", "循环里只 set_notebar_text(不调 update),共 20 步 × 100ms")
    say("* T1 不调 update —— 观察顶部:条是**逐格动**、**只在结束时跳一下**、还是**完全不动**?")
    watch("T1 开始(不 update)", 0.4)
    desc, sec = timed(lambda: loop_update("T1", None))
    say(f"   跑完({desc},{sec:.1f}s)")
    REPORT.append(f"     实测总耗时 {sec:.2f}s")


def t2_update_redraw() -> None:
    step("T2", "每轮 set_notebar_text + zbc.update(1, redraw_ui=True),20 步 × 100ms")
    if not has_update():
        say("   ⚠ 这个 ZBrush 里没有 zbc.update,跳过 T2/T3")
        REPORT.append("     (没有 zbc.update,跳过)")
        return
    say("* T2 带 update(redraw_ui=True)—— 和 T1 对比:条**动起来了吗**?")
    watch("T2 开始(带 update 重绘)", 0.4)
    desc, sec = timed(lambda: loop_update("T2", True))
    say(f"   跑完({desc},{sec:.1f}s)")
    REPORT.append(f"     实测总耗时 {sec:.2f}s")


def t3_update_no_redraw() -> None:
    step("T3", "每轮 set_notebar_text + zbc.update(1, redraw_ui=False),20 步 × 100ms")
    if not has_update():
        return
    say("* T3 带 update(redraw_ui=False)—— 和 T2 对比,**不重绘**时条还动吗?")
    watch("T3 开始(update 不重绘)", 0.4)
    desc, sec = timed(lambda: loop_update("T3", False))
    say(f"   跑完({desc},{sec:.1f}s)")
    REPORT.append(f"     实测总耗时 {sec:.2f}s")


def t4_freeze() -> None:
    step("T4", "官方 freeze() 包住忙循环(官方说冻结 UI 时进度条仍工作)")
    if not has_freeze():
        say("   ⚠ 这个 ZBrush 里没有 zbc.freeze,跳过 T4")
        REPORT.append("     (没有 zbc.freeze,跳过)")
        return
    say("* T4 用 zbc.freeze() 包住:如果这里的条**也能动**,说明 freeze 是长任务的正解")
    watch("T4 开始(freeze 包住)", 0.4)
    desc, sec = timed(lambda: zbc.freeze(lambda: loop_update("T4", True, steps=15, delay=0.1)))
    say(f"   跑完({desc},{sec:.1f}s)")
    REPORT.append(f"     实测总耗时 {sec:.2f}s")


# --------------------------------------------------------------------------- #
# T5/T6:真实场景 —— 等子进程的时候能不能更新
# --------------------------------------------------------------------------- #
SLEEP_CMD: list[str] | None = None
for _cand in ("/bin/sleep", "/usr/bin/sleep"):
    if os.path.exists(_cand):
        SLEEP_CMD = [_cand, "4"]
        break


def t5_while_subprocess() -> None:
    step("T5", "真实场景:Popen 跑 sleep 4s,父进程每 100ms 更新 notebar")
    if SLEEP_CMD is None:
        say("   ⚠ 找不到 /bin/sleep(Windows?),跳过 T5/T6 —— 这两条要你自己看的话,"
            "可以把 SLEEP_CMD 换成一个真的会跑几秒的命令")
        REPORT.append("     (没有 /bin/sleep,跳过)")
        return
    say("* T5 子进程在跑、父进程每 100ms 更新 —— 这才是「外部管线进度」的真实姿势")
    watch("T5 开始(子进程 4 秒)", 0.4)
    t0 = time.time()
    proc = subprocess.Popen([SLEEP_CMD[0], str(max(1, int(4 * SCALE)))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = 0
    try:
        while proc.poll() is None and time.time() - t0 < 10 * SCALE:
            p = min(0.99, (time.time() - t0) / max(0.5, 4.0 * SCALE))
            zbc.set_notebar_text(f"探针 T5 子进程运行中 {p * 100:.0f}%", p)
            if has_update():
                zbc.update(1, True)
            frames += 1
            time.sleep(0.1 * SCALE)
    finally:
        proc.wait(timeout=5)
    sec = time.time() - t0
    say(f"   子进程结束:更新了 {frames} 帧,实际 {sec:.2f}s")
    REPORT.append(f"     更新帧数 {frames},实际时长 {sec:.2f}s,"
                  f"子进程返回码 {proc.returncode}")


def t6_blocking_counter_example() -> None:
    step("T6", "反例:subprocess.run 阻塞 3 秒(期间**不**更新)→ 证明必须换 Popen")
    if SLEEP_CMD is None:
        return
    say("* T6 故意用阻塞的 subprocess.run:看顶部是不是**卡在上一帧**")
    zbc.set_notebar_text("探针 T6(下面注释掉更新,只看冻结)", 0.5)
    if has_update():
        zbc.update(1, True)
    watch("T6 开始(3 秒完全不动)", 0.4)
    desc, sec = timed(lambda: subprocess.run([SLEEP_CMD[0], str(max(1, int(3 * SCALE)))], capture_output=True))
    say(f"   阻塞结束({desc},{sec:.1f}s)")
    REPORT.append(f"     实测阻塞 {sec:.2f}s")


# --------------------------------------------------------------------------- #
# T7~T9:能写多长 / 怎么清 / 刷新开销
# --------------------------------------------------------------------------- #
def t7_lengths() -> None:
    step("T7", "文字长度:约 40 / 80 / 120 字各显示一次(看截断在第几个字)")
    samples = [
        (40, "读权重 depth_anything_v2_vitg_fp32(4.7 GB)…"),
        (80, "推理中 1036x1554:正在算深度,这一步最慢,请等它跑完再点别的按钮;"
             "期间 ZBrush 界面会不响应,这是正常的"),
        (120, "推理中 1036x1554:正在算深度,这一步最慢,请等它跑完再点别的按钮;"
              "期间 ZBrush 界面会不响应,这是正常的。之后还有:放大回原尺寸、去孤立极值、"
              "写 16 位 TIFF、导入 Texture 面板、挂到当前 SubTool"),
    ]
    for n, text in samples:
        say(f"   第 {n} 字那句请看顶部(注意它**断在哪里**、有没有换到第二行)")
        zbc.set_notebar_text(text, 0.5)
        if has_update():
            zbc.update(1, True)
        watch(f"T7 {n} 字", 1.6)
        REPORT.append(f"     {n} 字样例:{text}")
    REPORT.append("     请在报告末尾写:每一句实际显示到第几个字 / 是否换行")


def t8_clear() -> None:
    step("T8", "清理:set_notebar_text('', 0) 能不能让条消失")
    say("* T8 清空:看条**能不能正常消失**(别让探针留一条卡在那儿)")
    timed(lambda: zbc.set_notebar_text("", 0.0))
    if has_update():
        timed(lambda: zbc.update(1, True))
    watch("T8 应该已经清空", 1.0)


def t9_cost() -> None:
    step("T9", "刷新开销:100 次只 set / 100 次 set+update(redraw)各多久")
    n = 100
    t0 = time.time()
    for i in range(n):
        zbc.set_notebar_text(f"探针 T9 {i}%", (i + 1) / n)
    only_set = (time.time() - t0) / n * 1000
    with_update = None
    if has_update():
        t0 = time.time()
        for i in range(n):
            zbc.set_notebar_text(f"探针 T9 {i}%", (i + 1) / n)
            zbc.update(1, True)
        with_update = (time.time() - t0) / n * 1000
    zbc.set_notebar_text("", 0.0)
    say(f"   单次 set:{only_set:.2f}ms；"
        + (f"set+update(redraw):{with_update:.2f}ms" if with_update else "无 update"))
    REPORT.append(f"     单次 set {only_set:.2f}ms"
                  + (f";set+update(redraw_ui=True) {with_update:.2f}ms" if with_update else ""))


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def main() -> None:
    say("=" * 62)
    say("ZBTools notebar 探针(顶部状态条能不能当进度条用)")
    say("=" * 62)
    say("⚠ 接下来 ~20 秒请**盯着 ZBrush 顶部那条状态条**;每一步都会提示。")
    say("")
    REPORT.append(f"ZBrush 版本:{_safe(lambda: zbc.zbrush_info(0))}")
    REPORT.append(f"zbc.update 存在:{has_update()};zbc.freeze 存在:{has_freeze()}")
    REPORT.append(f"set_notebar_text 可用:{_safe(lambda: zbc.set_notebar_text('探针就绪', 0.0))}")
    try:
        for fn in (t0_baseline, t1_no_update, t2_update_redraw, t3_update_no_redraw,
                   t4_freeze, t5_while_subprocess, t6_blocking_counter_example,
                   t7_lengths, t8_clear, t9_cost):
            try:
                fn()
            except Exception as exc:                                  # noqa: BLE001
                say(f"   ✗ {fn.__name__} 出错:{type(exc).__name__}: {exc}")
                REPORT.append(f"     ✗ 出错:{type(exc).__name__}: {exc}")
    finally:
        try:
            zbc.set_notebar_text("", 0.0)          # 无论如何都别留一条卡住
        except Exception:                                             # noqa: BLE001
            pass

    say("")
    say("=" * 62)
    say("请把下面这张表**照实填**(这是探针唯一的产出,我看不到你的屏幕):")
    say("=" * 62)
    say("""
  T0 文字显示:      [ ] 有文字        [ ] 没有
  T0 半条(0.5):     [ ] 有条          [ ] 没条
  T1(不 update):    [ ] 逐格动        [ ] 只在结束时动一下   [ ] 完全不动
  T2(update 重绘):  [ ] 逐格动        [ ] 只在结束时动一下   [ ] 完全不动
  T3(update 不重绘):[ ] 和 T2 一样     [ ] 比 T2 差            [ ] 完全不动
  T4(freeze 包住):  [ ] 能动          [ ] 不动
  T5(子进程期间):   [ ] 能动          [ ] 不动
  T6(阻塞反例):     [ ] 卡在上一帧    [ ] 别的表现:
  T7 40 字:         显示到第 ___ 字 / [ ] 完整
  T7 80 字:         显示到第 ___ 字 / [ ] 完整
  T7 120 字:        显示到第 ___ 字 / [ ] 完整
  T8 清空:          [ ] 条消失了      [ ] 没消失
  T9 数字:          见上面(单次 set / set+update 的毫秒)
""")
    say("结论怎么用(我会照你的勾选决定实现方案):")
    say("  * T1/T2 有任何一条能动 + T5 能动 → 值得做「外部进度映射进 ZBrush(阶段级)」;")
    say("  * 只有 T2/T3 能动的差别明显     → 实现里每帧要搭 zbc.update(1, redraw_ui=True);")
    say("  * T5 不动、只有结束才刷新       → 进度条意义不大,退路是「阶段文字进控制台」;")
    say("  * T7 的截断位置                 → 阶段文字写多长才不被截。")
    say("")
    say("另外报告里已带上每步实测耗时(T9),用来定刷新节流(50 / 100 / 200ms)。")


def _safe(fn) -> str:
    try:
        return repr(fn())
    except Exception as exc:                                          # noqa: BLE001
        return f"失败({type(exc).__name__}: {exc})"


def _report_paths() -> list[str]:
    """报告落盘位置:优先写到项目里的 _probe/ 下(这样我自己就能读到,你不用手抄)。"""
    places = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        places.append(os.path.join(os.path.dirname(here), "_probe",
                                   "probe_notebar_report.txt"))
    except NameError:                       # 被 ZBrush 用别的方式执行时可能没有 __file__
        pass
    places.append(os.path.join(tempfile.gettempdir(), "zbtools_probe_notebar.txt"))
    return places


def _run_and_report() -> None:
    """跑一遍并把整段输出:① 打到控制台 ② 写盘 ③ 塞进剪贴板(省得你手抄)。"""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            main()
        except Exception as exc:                                      # noqa: BLE001
            print(f"[ZBTools] 探针崩了:{type(exc).__name__}: {exc}")
        finally:
            for line in REPORT:
                print(line)
    text = buffer.getvalue()
    print(text, end="")

    for target in _report_paths():
        try:
            directory = os.path.dirname(target)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(text)
            print(f"[ZBTools] 报告已写到:{target}")
            break
        except OSError as exc:
            print(f"[ZBTools] 写 {target} 失败:{exc}")

    try:
        import zbrush.utils as zbu
        zbu.clear_output()
        print(text, end="")
    except Exception:                                                 # noqa: BLE001
        pass

    try:                                  # 复制到剪贴板(macOS / Windows 各一条)
        if os.environ.get("ZBTOOLS_PROBE_NO_CLIP"):
            # 测试 / 自动化里跑的时候别动用户的剪贴板(他可能正复制着别的东西)
            print("[ZBTools] (ZBTOOLS_PROBE_NO_CLIP=1:跳过剪贴板,报告已落盘)")
            return
        if os.name == "nt":
            subprocess.run("clip", input=text, text=True, shell=True, check=False)
        else:
            subprocess.run("pbcopy", input=text, text=True, check=False)
        print("[ZBTools] 报告已复制到剪贴板(直接粘给我即可)")
    except Exception as exc:                                          # noqa: BLE001
        print(f"[ZBTools] 复制到剪贴板失败(不影响,报告已落盘):{exc}")


_run_and_report()
