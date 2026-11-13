"""跑外部子进程 + **边跑边回调**(进度条靠它)。

为什么要单独一个模块(两个功能都要):
  「图片转深度图」和「贴图转色」都得把**外部管线**的进度喂给 ZBrush 顶部状态条,而原来的
  ``subprocess.run(capture_output=True)`` 是**阻塞**的 —— 拿不到中间输出,也就没法更新进度。
  这里提供两件事:

  * :func:`unbuffered`:给解释器加 ``-u``。**不加会踩坑**:子进程 stdout 接的是管道,
    Python 会改成块缓冲(4~8KB),进度行会攒到最后一次性冒出来 —— 那样进度条只会"最后跳一下"。
  * :func:`stream`:`Popen` + 一个读线程把 stdout 行塞进队列,主线程按 ``tick`` 取,中间还能
    回调 ``on_tick`` 更新进度。**为什么用线程**:一次推理要十几秒、期间子进程一行都不打,
    主线程必须能继续 tick;而 Windows 上 ``select`` 对管道不可用,线程才是可移植的写法。

依赖方向:只用标准库,功能层可以直接用(``features/* → proc``)。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable, Sequence

#: 默认的 tick 间隔(秒):决定了"子进程不打字"时进度条多久能爬一次。
#: 实测(``scripts/probe_notebar.py``,ZBrush 2026.2)单次 notebar 更新约 4ms → 100ms 够用。
TICK_S = 0.1

#: 守卫生生不息的输出(比如某个库刷日志):超过这个行数就不留历史了(仍然逐行回调)。
_MAX_KEEP_LINES = 4000


def unbuffered(args: Sequence[str]) -> list[str]:
    """给 python 解释器加 ``-u``(不是 python 的解释器就原样返回)。

    为什么必须:管道是块缓冲,不加 ``-u`` 的话外部管线的进度行会攒到进程结束才吐出来。
    """
    out = [str(a) for a in args]
    if len(out) >= 2 and os.path.basename(out[0]).startswith("python") and out[1] != "-u":
        return [out[0], "-u", *out[1:]]
    return out


def stream(args: Sequence[str],
           timeout: float,
           on_line: Callable[[str], None] | None = None,
           on_tick: Callable[[], None] | None = None,
           tick: float = TICK_S) -> tuple[int, list[str], bool]:
    """跑子进程并**边跑边回调**;stderr 合并进 stdout 一起按行给。

    Args:
        args: 完整命令行(建议先用 :func:`unbuffered` 处理)。
        timeout: 超过这么多秒就杀掉子进程。
        on_line: 每收到一行就调一次(原样,已去掉行尾换行)。
        on_tick: 每隔 ``tick`` 秒(以及两次输出之间)调一次 —— 进度条就挂这里。
        tick: tick 间隔(秒)。

    Returns:
        ``(returncode, lines, timed_out)``。超时被杀时 ``returncode`` 为 ``-9``、``timed_out=True``,
        并且 ``lines`` 里仍然有已经收到的输出(报错信息要用)。

    几个刻意的选择:
      * **stdin 接到 DEVNULL**:不接的话子进程继承 ZBrush 的 stdin,万一某个依赖去读输入
        (或库自己弹交互提示)就会一直挂着,直到超时 —— 那种"卡住"极难排查。
      * **回调里的异常被吞掉**:``on_line`` / ``on_tick`` 是调用方给的(打日志、刷进度条),
        它们出错不该把子进程变成孤儿(下面的 ``finally`` 会确保杀掉)。
    """
    proc = subprocess.Popen(list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    lines: list[str] = []
    box: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                box.put(raw.rstrip("\r\n"))
        except Exception:                                             # noqa: BLE001
            pass
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:                                         # noqa: BLE001
                pass
            box.put(None)                      # 结束哨(和"空行"区分开)

    thread = threading.Thread(target=_reader, name="zbtools-proc-reader", daemon=True)
    thread.start()
    deadline = time.monotonic() + max(0.1, float(timeout))
    timed_out = False
    reaped = False                         # 已经 wait 过了就别再 kill(避免误杀别人的 pid)
    try:
        while True:
            try:
                item = box.get(timeout=max(0.01, float(tick)))
            except queue.Empty:
                _safe_call(on_tick)
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                continue
            if item is None:                  # 子进程关掉了 stdout
                break
            lines.append(item)
            if len(lines) > _MAX_KEEP_LINES:
                del lines[: len(lines) - _MAX_KEEP_LINES]
            _safe_call(on_line, item)
            if time.monotonic() > deadline:
                timed_out = True
                break

        if timed_out:
            _kill(proc)
            reaped = True
            return -9, lines, True
        try:
            code = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
            reaped = True
        except Exception:                                             # noqa: BLE001
            _kill(proc)
            reaped = True
            code = -9
        return code, lines, False
    finally:
        if not reaped:                        # 回调抛异常等意外路径:别留下孤儿进程
            _kill(proc)


def _safe_call(fn: Any, *args: Any) -> None:
    """调回调并吞掉它的异常(回调是"顺带做事",不该毁掉主流程)。"""
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:                                                 # noqa: BLE001
        pass


def _kill(proc: Any) -> None:
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:                                                 # noqa: BLE001
        pass


def tail(lines: Sequence[str], n: int = 12) -> str:
    """把已收到的输出压成"最后 n 行"(报错信息用)。

    注意:stderr 已经并进 stdout 了,所以这里只需要一个行数。
    """
    return "\n".join(list(lines)[-max(1, int(n)):])
