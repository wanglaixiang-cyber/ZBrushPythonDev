"""通用 UI 与提示(功能模块只通过这里建控件 / 弹提示)。

为什么要这层:
  * 统一 item path 生成(ZBrush 用 ASCII 冒号分层级,名字里不能带冒号 —— 曾有整面板空白的事故)
  * 统一校验每个 add_* 的返回值(静默失败 = 面板空白,必须报出来)
  * 统一错误兜底与提示样式(警告换底色;详见 notes.py)
  * 功能模块之间不互相 import,只依赖这一层与 zbapi
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable, Sequence

import zbrush.commands as zbc

from . import notes
from .errors import PluginError

#: 悬停提示(官方叫 **bubble help**,参数名就是 ``info``)的字数上限。
#:
#: 为什么要卡:**ZBrush 的气泡提示只有一行**;更要命的是官方明文警告它的 Python 字符串
#: 连反斜杠都渲染不对 —— 连 ``\n`` / ``\t`` 都受影响
#: (原文见 ``sdk/docs/partials/api_zbrush_string_bug_backslash.html``:
#: "This also impacts the ability to correctly render special characters such as \n and \t"),
#: 所以"写长一点靠换行"这条路根本走不通,只能写短。
#: 参照官方示例:它们 33 处 ``info`` 的中位数是 19 字、最长 37 字。
#: 超了不会拦(仍能创建控件),但控制台会告警,``tests/test_architecture.py`` 会直接红。
INFO_MAX = 40


def _check_info(path: str, info: str) -> None:
    """提示太长 / 带换行时提醒(见 :data:`INFO_MAX`)。"""
    if len(info) > INFO_MAX:
        print(f"[UI] ⚠ 提示太长({len(info)} 字 > {INFO_MAX}):{path}\n"
              f"     {info[:60]}…\n"
              f"     气泡提示只有一行、且 ZBrush 渲染不了 \\n —— 理由/实测请写进该功能的 README")
    if "\n" in info or "\t" in info:
        print(f"[UI] ⚠ 提示里带了换行/制表符:{path}(ZBrush 官方已知渲染 bug,请改成一句话)")


#: 外部管线往 stdout 打的进度行前缀(两个管线的约定:``[zbtools-progress] 0.42``)。
#: 插件认它、**不往控制台转**(否则日志会被刷屏),而是喂给 :class:`Progress`。
PROGRESS_PREFIX = "[zbtools-progress]"


def parse_progress(line: str) -> float | None:
    """从管线的一行 stdout 里取出进度(0.0~1.0);不是进度行返回 ``None``。"""
    text = (line or "").strip()
    if not text.startswith(PROGRESS_PREFIX):
        return None
    try:
        value = float(text[len(PROGRESS_PREFIX):].strip())
    except ValueError:
        return None
    return min(1.0, max(0.0, value))


#: 当前"活着"的进度条(一次只可能有一个:脚本是单线程跑的)。
#: 用途见 :func:`clear_stuck_progress` —— 异常路径上要有人负责把它清掉。
_ACTIVE_PROGRESS: "Progress | None" = None


class Progress:
    """把"外部等待"映射到 ZBrush 顶部的进度条(``set_notebar_text`` 的第二个参数)。

    实测依据(``scripts/probe_notebar.py``,ZBrush 2026.2,报告见 ``_probe/``):

      * ``zbc.set_notebar_text(text, progress)`` **就是**进度条(官方原话:
        "the equivalent of a progress bar in other applications");
      * 在脚本回调里循环更新它**会自动刷新**(T1 与 T2 表现一样)→ **不必**每帧搭
        ``zbc.update(1, redraw_ui=True)``(每帧省 ~5ms);
      * **等子进程期间也能刷新**(T5)→ 把外部管线/导出等待映射进来是有效的;
      * 单次调用 ~4ms(T9)→ 节流 100ms 足够,整个等待期开销 <5%;
      * 文字超过 ~80 字会被截(T7)→ 这里按 :data:`TEXT_MAX` 自动截。

    **爬升(creep)**:阶段之间往往没有输出(最典型:一次 dav2 推理要 10 秒以上、期间一行日志
    都没有),所以按时间线性往上爬 —— 但受 ``ceiling`` 限制,**永远不会越过下一个里程碑**,
    不假报进度。``ceiling`` 由 :meth:`stage` 或 :meth:`set_ceiling` 给出。
    """

    #: 状态条文字长度上限(实测 ~80 字以内完整显示)
    TEXT_MAX = 80

    def __init__(self, label: str = "", value: float = 0.0, eta: float = 30.0,
                 throttle: float = 0.1) -> None:
        global _ACTIVE_PROGRESS
        _ACTIVE_PROGRESS = self
        self.label = label
        self.value = _clamp01(value)
        self.eta = max(0.5, float(eta))          # 整个任务大概多少秒(决定爬升速率)
        self.throttle = max(0.0, float(throttle))
        self.ceiling = _clamp01(self.value + 0.05)
        self._text = label
        self._last_render = 0.0
        self._last_tick = time.monotonic()
        self._render(force=True)

    # --- 给调用方 ---
    def stage(self, text: str, value: float) -> None:
        """到一个**里程碑**:强制刷新一次,并把爬升天花板设成"再往上一点点"。

        ⚠ **只增不减**:如果传进来的位置比当前值小,就保持不动(实测踩到过一次 ——
        管线的真进度行与插件自己的阶段表尺度不一致,两条映射叠起来会让状态条倒退;
        进度条倒退在任何情况下都是 bug,所以在这一层兜住)。
        """
        self.value = max(self.value, _clamp01(value))
        self._text = text
        self.ceiling = _clamp01(self.value + 0.05)
        self._last_tick = time.monotonic()
        self._render(force=True)

    def set_ceiling(self, ceiling: float) -> None:
        """告诉它:在下一个里程碑之前,最多允许爬到 ``ceiling``(爬升用)。"""
        self.ceiling = max(self.value, _clamp01(ceiling))

    def creep(self) -> None:
        """按时间往上爬一点(子进程不打字时挂在 ``on_tick`` 上)。"""
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.value < self.ceiling:
            self.value = min(self.ceiling, self.value + dt / self.eta)
            self._render()

    def done(self, text: str = "") -> None:
        """收尾:默认**清空**状态条(别留一条卡在 100%)。

        ⚠ 这里直接调 ``set_notebar_text(text, 0.0)``,不走 :meth:`_compose` ——
        因为 ``text=""`` 才是"清空"(实测),而 ``_compose`` 会在空文字时回落到 ``label``,
        那就变成"清不掉、留一条 0% 挂在那儿"。
        """
        global _ACTIVE_PROGRESS
        if _ACTIVE_PROGRESS is self:
            _ACTIVE_PROGRESS = None
        self._text = text
        self.value = 1.0 if text else 0.0
        try:
            zbc.set_notebar_text(text, 0.0)
        except Exception:                                             # noqa: BLE001
            pass
        self._last_render = time.monotonic()

    # 上下文写法:`with ui.Progress("...") as prog:` —— 中途抛异常也会自动清掉
    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.done()
        return False

    # --- 内部 ---
    def _compose(self) -> str:
        suffix = f" {self.value * 100:.0f}%"
        text = self._text or self.label
        room = self.TEXT_MAX - len(suffix)
        if len(text) > room:
            text = text[: max(4, room - 1)] + "…"
        return f"{text}{suffix}"

    def _render(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_render < self.throttle:
            return
        self._last_render = now
        try:
            zbc.set_notebar_text(self._compose(), self.value)
        except Exception:                                             # noqa: BLE001
            pass                          # 状态条更新失败不该影响正事


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def clear_stuck_progress() -> None:
    """兜底:把还活着的进度条清掉(:func:`guard` 在异常路径上会调它)。

    为什么需要:进度条**卡住**比"没有进度条"更误导 —— 用户看到"推理中 12%"会以为还在跑,
    于是等着;而实际上脚本早就报错退出了。所以不管从哪儿抛出来的,都要有人负责清掉。
    """
    if _ACTIVE_PROGRESS is not None:
        _ACTIVE_PROGRESS.done()


class Panel:
    """插件面板的写入句柄:功能只拿它来挂控件,不直接碰 zbc 的面板接口。

    ⚠ **每个控件都要显式给 ``width=1.0``**(占满一行)。
      ``width`` 是 |0,1] 的相对宽度(API 文档写"像素"是错的),官方 GUI 示例里明确建议:
      "always set a relative width of 1.0 for elements that should occupy a full line
      and **not let ZBrush decide automatically (as it can get it wrong)**"。
      实测:不写 width 的 slider / switch / button 会被 ZBrush 排到一行里**重叠显示**。
    """

    def __init__(self, palette: str, title: str) -> None:
        self.palette = palette
        self.sub = f"{palette}:{title}"
        self.paths: dict[str, str] = {}
        self.failures: list[str] = []

    # --- 路径与命名 ---
    def path(self, key: str, label: str) -> str:
        """登记一个控件的 item path(每段都不能含 ASCII 冒号)。"""
        if ":" in label:
            raise PluginError(f"控件名不能含冒号:{label!r}")
        path = f"{self.sub}:{label}"
        self.paths[key] = path
        return path

    def _try(self, make: Callable[[], bool], what: str) -> bool:
        try:
            ok = bool(make())
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f"{what} 抛异常:{exc}")
            return False
        if not ok:
            self.failures.append(f"{what} 返回 False")
            return False
        return True

    # --- 控件 ---
    def button(self, key: str, label: str, info: str, fn: Callable[[str], None], **kw: Any) -> str:
        path = self.path(key, label)
        _check_info(path, info)
        self._try(lambda: zbc.add_button(path, info, fn, **kw), f'add_button("{path}")')
        return path

    def switch(self, key: str, label: str, state: bool, info: str,
               fn: Callable[[str, float], None], **kw: Any) -> str:
        path = self.path(key, label)
        _check_info(path, info)
        self._try(lambda: zbc.add_switch(path, state, info, fn, **kw), f'add_switch("{path}")')
        return path

    def slider(self, key: str, label: str, value: float, resolution: int, low: float, high: float,
               info: str, fn: Callable[[str, float], None], **kw: Any) -> str:
        path = self.path(key, label)
        _check_info(path, info)
        self._try(lambda: zbc.add_slider(path, value, resolution, low, high, info, fn, **kw),
                  f'add_slider("{path}")')
        return path

    # --- 动态启用 / 禁用 ---
    def set_value(self, key: str, value: float) -> bool:
        """直接改控件的值(开关的 0/1)。

        为什么需要它:**ZBrush 没有 radio(单选)控件** —— 单选组只能用一组互斥开关做,
        点其中一个时必须把**其它几个**关掉,那就得能直接写别人的值。
        (踩过的坑:`zbc.set` 只对已登记的项有效,所以只能在 build 之后调用。)
        """
        path = self.paths.get(key)
        if not path:
            return False
        return self._try(lambda: zbc.set(path, float(value)), f'set("{path}")')

    def set_enabled(self, key: str, enabled: bool) -> bool:
        """按登记过的 key 启用/禁用控件(灰掉、点不动)。

        用途:有的功能只在特定状态下才有意义(例如「文件夹布尔预检」要求当前
        SubTool 在 SubTool 文件夹里)。ZBrush 没有"选择变化"回调,所以调用方
        需要自己找时机刷新 —— 约定:注册时刷一次 + 每次点本功能的按钮时再刷。
        """
        path = self.paths.get(key)
        if not path:
            self.failures.append(f'set_enabled("{key}") —— 这个 key 没登记过')
            return False
        # ⚠ set_status 是无返回值的 API(-> None),不能走 _try 的"必须返回真值"校验
        try:
            zbc.set_status(path, bool(enabled))
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f'set_status("{path}", {bool(enabled)}) 抛异常:{exc}')
            return False
        return True


# --------------------------------------------------------------------------- #
# 提示与错误兜底
# --------------------------------------------------------------------------- #
def note(text: str) -> None:
    """弹提示(按内容自动选样式:警告换底色)。"""
    zbc.show_note(text, **notes.note_style(text))


def guard(action: str, fn: Callable[[], None]) -> None:
    """统一兜底:异常显示到界面 + 控制台,不把异常抛回 ZBrush。

    ``finally`` 里顺手把还活着的进度条清掉:失败路径上留一条"卡在 12%"的状态条,
    比没有进度条更糟 —— 用户会以为程序还在跑(见 :func:`clear_stuck_progress`)。
    """
    try:
        fn()
    except PluginError as exc:
        print(f"[{action}] 失败:{exc}")
        note(f"警告:{action}失败\n{exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{action}] 异常:{exc}\n{traceback.format_exc()}")
        note(f"警告:{action}异常:{exc}(详见 Python 控制台)")
    finally:
        clear_stuck_progress()


# --------------------------------------------------------------------------- #
# 装配入口
# --------------------------------------------------------------------------- #
def _pump(rounds: int = 2) -> None:
    """给 ZBrush 一点机会处理界面命令队列(界面操作和 press 一样是队列,不是同步的)。"""
    for _ in range(max(1, rounds)):
        try:
            zbc.update(1, True)
        except Exception:                                             # noqa: BLE001
            pass
        time.sleep(0.05)


def close_item(item_path: str, tries: int = 5) -> bool:
    """关掉一个界面项,**并等它真的消失**。

    ⚠ 这里不能"调一次 close 就当它没了":ZBrush 会把它排进命令队列,
    紧接着用同名路径再 add 会失败(实测:热更新时 add_palette 返回 False,
    只能重启 ZBrush —— 那正是"必须关软件再 Load"的原因)。
    """
    if not zbc.exists(item_path):
        return True
    for _ in range(max(1, tries)):
        try:
            zbc.close(item_path)
        except Exception as exc:                                      # noqa: BLE001
            print(f"[zbtools] close({item_path}) 抛异常:{exc}")
            return False
        _pump()
        if not zbc.exists(item_path):
            return True
    print(f"[zbtools] {item_path} 关不掉(exists 仍为真)—— 界面状态可能卡住,"
          f"必要时重启 ZBrush")
    return False


def create_palette(palette: str) -> bool:
    """建立插件的顶层面板(全新的界面才走这里)。"""
    if zbc.exists(palette):
        # 名字已被占:先试关掉再等它消失;实在关不掉就交给 ensure_palette 走复用
        if not close_item(palette):
            print(f"[{palette}] 顶层面板已存在且关不掉 → 交给 ensure_palette 复用")
            return True
    try:
        ok = bool(zbc.add_palette(palette, docking_bar=1))
    except Exception as exc:  # noqa: BLE001
        print(f"[{palette}] add_palette 异常:{exc}")
        return False
    if not ok:
        try:
            still = bool(zbc.exists(palette))
        except Exception:                                             # noqa: BLE001
            still = "读取失败"
        print(f"[{palette}] add_palette 返回 False(exists({palette}) = {still})")
        print(f"[{palette}] 原因通常是:面板名被上一次注册占着 —— "
              f"ZBrush 关掉脚本面板后名字不会立刻释放。"
              f"处理:把右侧菜单栏的 {palette} 面板关掉再 Load,或重启 ZBrush")
    return ok


def ensure_palette(palette: str, sub_names: Sequence[str] = ()) -> bool:
    """建面板;已经存在就**复用**它,只把上一次注册留下的子面板重建。

    为什么热更新要复用顶层面板(实测结论):
      * ZBrush 的界面操作是**命令队列** —— ``close`` 之后名字不会立刻释放,
        紧接着同名 ``add_palette`` 就返回 False;而 ``delete_interface_item``
        官方明确说**对 palette / sub-palette 无效**(只能用 ``close``)。
      * 于是"关掉顶层再重建"这条路在同一个 ZBrush 会话里走不通,
        只有重启 ZBrush(全新界面)才行 —— 也就是"必须关软件再 Load"的现象。
    复用顶层面板的代价只是:顶层保留(连 docking 状态一起,反而是好事),
    每次 Load 重建各功能的子面板 —— 增删控件都能立刻生效。

    若某个功能的**标题改名**了,旧分区会留到重启为止(拿不到"枚举子面板"的 API)。
    """
    if not zbc.exists(palette):
        if not create_palette(palette):
            return False
        return True
    print(f"[{palette}] 检测到已存在的面板 → 复用顶层,重建子面板(热更新)"
          + (f"{len(sub_names)} 个" if sub_names else ""))
    failed = [name for name in sub_names if not close_item(f"{palette}:{name}")]
    if failed:
        print(f"[{palette}] ⚠ 这些旧子面板没关掉:{failed}(它们的旧控件可能还留着)")
    return True


def open_palette(palette: str) -> None:
    try:
        zbc.press(palette)
    except Exception as exc:  # noqa: BLE001
        print(f"[{palette}] 自动展开面板失败(可手动点右侧菜单栏):{exc}")
