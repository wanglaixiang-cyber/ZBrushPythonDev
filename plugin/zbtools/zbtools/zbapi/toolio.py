"""对 ZBrush 的"工具/贴图文件"操作:导出、找贴图、导入贴图、挂到 SubTool。

为什么放在 zbapi 而不是某个功能里:导出 SubTool、找模型带出的贴图、把一张图导入 Texture
面板 —— 这几件事**多个功能都要用**(贴图转色要导出低模/高模,图片转深度图要读模型贴图、
再把深度图送回 ZBrush)。功能之间不允许互相 import,所以共用代码放这里。
"""

from __future__ import annotations

import glob
import os
import time
from typing import Any, Callable

import zbrush.commands as zbc

from ..errors import PluginError

#: 贴图导入/挂载用的 item path 候选。
#: ZBrush 的 Python SDK **没有贴图 API**(145 个命令里一个 texture 都没有),只能按
#: ``父面板:子面板:项`` 的命名习惯试,并用 ``zbc.exists()`` 核对存在性 ——
#: 所以这里是"候选 + 探测",不是硬编码某一条(不同 ZBrush 版本路径会变)。
# ⚠ 下面这几张表是**真机实测过的**(ZBrush 2026.2,见 scripts/probe_texture_paths.py 的输出):
#    存在的:Texture:Import / Texture:Export / Texture:New / Texture:Remove /
#            Tool:Texture Map / Tool:Texture Map:Texture Map / Tool:Texture Map:Texture On /
#            Tool:Texture Map:Import / Tool:Texture Map:Export / Tool:UV Map:Texture Map
#    不存在的:Texture:Texture On、Tool:Texture Map:Texture Map Slot、Tool:Texture Map:Texture、
#            Tool:Texture Map:Texture Map On、Tool:Texture:Texture、Tool:Display Properties:Texture
#   (还是留着"候选表 + exists() 探测"的形式:别的 ZBrush 版本路径会变,探测一下能兼容。)
#: 导入到 **Texture 面板**(不是 SubTool 的贴图槽!):
#: ⚠ Tool:Texture Map:Import 是"导进当前 SubTool 的贴图槽"那条(见 import_texture_to_subtool),
#: 不是面板导入 —— 早先把它当兜底混在这里,会让"追加到面板"变成"悄悄替换模型",已分开。
TEXTURE_IMPORT_PATHS = ("Texture:Import",)
#: 导贴图:先试 **Tool 层的** Export(它导的是这个 SubTool 的贴图),再退回 Texture 面板的
TEXTURE_EXPORT_PATHS = ("Tool:Texture Map:Export", "Texture:Export")
#: 贴图槽:官方文档叫它 "Texture Map thumbnail",ZBrushCentral 上的现成脚本用
#: **Tool:Texture Map:TextureMap**(中间没空格);我们的探针在 2026.2 上按带空格的写法也 exists ✓
#: —— 两种都留着试。
TEXTURE_ASSIGN_PATHS = (
    "Tool:Texture Map:TextureMap",
    "Tool:Texture Map:Texture Map",
)
#: Texture 面板顶部的"当前贴图"滑条:值 = 贴图编号,title = 贴图名
#: (官方 SDK 例子里就是用 zbc.set("Tool:Item Info", …) 这种写法选工具的)
TEXTURE_ITEM_INFO = "Texture:Item Info"
#: "Clone Txtr" —— 官方文档 Tool > Texture Map 里写明:
#: "It is necessary to clone a texture like this **if you wish to export it from ZBrush**"
#: 也就是:模型挂的贴图如果**不在 Texture 面板里**(没 Clone 过),ZBrush 根本导不出它 ——
#: 这时候 Tool:Export 写出来的可能是**面板里那张别的贴图**(换模型后深度图还是上一个模型的,
#: 就是这么来的)。所以导不出贴图时,先 Clone 再导一次。
TEXTURE_CLONE_PATHS = ("Tool:Texture Map:Clone Txtr", "Tool:Texture Map:CloneTxtr",
                       "Tool:Texture Map:Clone Texture")
#: Texture 面板里的"上下翻转"按钮(ZScript 里是 Texture:FlpV)
TEXTURE_FLIPV_PATHS = ("Texture:FlpV",)
TEXTURE_ON_PATHS = (
    "Tool:Texture Map:Texture On",           # 让模型显示贴图的开关(实测存在,值 1.0)
)
#: 贴图方块 —— 它的 **title 就是贴图名**、**tooltip 里带尺寸**(实测)
TEXTURE_SLOT_PATHS = ("Tool:Texture Map:Texture Map",)


def press(item_path: str) -> str:
    """按下界面项,返回错误文本(空串 = 没报错)。

    ⚠ 实测:``press`` 有时会在**命令已经排进队列**的同时抛出
    ``SystemError: <built-in method press ...> returned a result with an exception set``
    (PyCapsule 包装层的报错,不是命令没执行 —— 用户那边就是"投射成功但弹异常")。
    所以这里不当致命错误:记录下来,由调用方**轮询实际效果**(文件出现 / Tool 名改变)
    来判断成败;只有效果没出现时,才把这条报错一起抛出去。
    """
    try:
        zbc.press(item_path)
        return ""
    except Exception as exc:                                             # noqa: BLE001
        print(f"[ZBTools] press({item_path}) 抛异常(命令通常已进队列,继续按效果判断):"
              f"{type(exc).__name__}: {exc}")
        return f"{type(exc).__name__}: {exc}"


def wait_for_file(path: str, timeout: float,
                  on_tick: Callable[[], None] | None = None) -> bool:
    """等 ZBrush 把文件写盘(press 是命令队列,不是同步调用)。

    ``on_tick`` 每轮(0.2 秒)调一次 —— 大网格导出要几十秒,进度条就挂在这里
    (实测:ZBrush 顶部状态条在脚本回调里会自己刷新,见 ``scripts/probe_notebar.py``)。
    """
    deadline = time.time() + max(0.1, float(timeout))
    while time.time() < deadline:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True
        except OSError:
            pass
        if on_tick is not None:
            on_tick()
        time.sleep(0.2)
    return False


def export_active(dest_obj: str, timeout: float = 300.0,
                  on_tick: Callable[[], None] | None = None) -> None:
    """把当前 SubTool 导出成 OBJ(官方路线:set_next_filename + Tool:Export)。

    带 UV 的网格,ZBrush 会**顺手把贴图写在旁边**(``名.bmp`` + ``名.mtl``)——
    这就是"读出这个模型的贴图"的可靠办法(SDK 里没有读贴图的 API)。

    ``on_tick`` 透传给 :func:`wait_for_file`(导出等待的进度反馈)。
    """
    if os.path.exists(dest_obj):
        os.remove(dest_obj)                     # 免得等到上一次的旧文件误判成功
    zbc.set_next_filename(dest_obj)
    press_err = press("Tool:Export")
    if not wait_for_file(dest_obj, timeout, on_tick):
        raise PluginError(
            f"导出超时:ZBrush 没有写出 {dest_obj}\n"
            "可能原因:① 弹出了「另存为」对话框(点确认即可);"
            "② 当前没有选中可导出的 SubTool(先点一下模型);"
            f"③ 网格太大,把等导出的秒数调大。"
            + (f"\npress 的报错:{press_err}" if press_err else ""))
    if press_err:
        print("[ZBTools] 导出其实成功了(文件已写出),press 的那条报错可以忽略")


def find_texture(obj_path: str) -> str:
    """找导出 OBJ 时 ZBrush 顺手写出的贴图。

    实测两种命名:
      * 带 UV 的网格 → 写 ``<名>.mtl`` + ``<名>.bmp``(mtl 里有 map_Kd)
      * 没 UV 的网格 → 写 ``<名>_TXTR.tif``
    所以先读 mtl(map_Kd 最权威),再退回候选文件名。
    """
    base, _ext = os.path.splitext(obj_path)
    mtl = base + ".mtl"
    if os.path.exists(mtl):
        try:
            with open(mtl, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.strip().lower().startswith("map_kd"):
                        name = line.split(None, 1)[1].strip()
                        candidate = name if os.path.isabs(name) else os.path.join(
                            os.path.dirname(obj_path), name)
                        if os.path.exists(candidate):
                            return candidate
        except OSError as exc:
            print(f"[ZBTools] 读 {mtl} 失败:{exc}")
    candidates: list[str] = []
    for pattern in (base + ".bmp", base + "_TXTR.tif", base + ".tif",
                    base + ".png", base + "_TXTR.bmp", base + "_TXTR.png", base + ".jpg"):
        candidates.extend(glob.glob(pattern))
    candidates = [c for c in candidates if os.path.getsize(c) > 0]
    return max(candidates, key=os.path.getmtime) if candidates else ""


def export_has_uv(obj_path: str) -> bool:
    """导出的 OBJ 里有没有 UV(`vt` 行)—— "它能不能当低模"的权威判据。

    为什么不用 ZBrush 的查询:UV 框读数在各版本上不一定可靠,而导出文件是下游管线
    真正要吃的东西。OBJ 里 `vt` 在 `f` 之前,所以**一见到 `f` 还没见到 `vt` 就能下结论**,
    不用读完整个文件(高模导出 44MB,早退很重要)。
    """
    if not os.path.exists(obj_path):
        return False
    try:
        with open(obj_path, "r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if line.startswith("vt "):
                    return True
                if line.startswith("f "):
                    return False
                if index > 4_000_000:                 # 保险:异常文件别读爆
                    break
    except OSError as exc:
        print(f"[ZBTools] 读导出文件失败:{exc}")
        return False
    return False


def first_existing(candidates: tuple[str, ...]) -> str:
    """候选 item path 里第一个**真实存在**的(没有就返回空串)。"""
    for path in candidates:
        try:
            if zbc.exists(path):
                return path
        except Exception:                                                # noqa: BLE001
            continue
    return ""


def texture_slot_value() -> float | None:
    """读"SubTool 的贴图方块"的**数值**。

    ⚠ 实测(ZBrush 2026.2):这个值**永远是 0.0**,挂没挂贴图都是 0 —— 所以它没用,
    别拿它判断"挂没挂"(那是上一版踩过的坑:凭一个没有意义的读数下结论)。
    真正有用的是下面 :func:`texture_slot_info`。
    """
    path = first_existing(TEXTURE_SLOT_PATHS)
    if not path:
        return None
    try:
        return float(zbc.get(path))
    except Exception:                                                    # noqa: BLE001
        return None


def texture_slot_info() -> dict[str, Any]:
    """读"当前 SubTool 挂的是哪张贴图" —— **这条实测可用**。

    实测(ZBrush 2026.2,选中一个挂了 2749x3684 贴图的模型)::

        get_title("Tool:Texture Map:Texture Map") → '323'
        get_info ("Tool:Texture Map:Texture Map") → 'Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)'

    也就是说:不用导出网格,**名字和尺寸都能直接读到**。
    返回 ``{"path", "name", "width", "height", "depth", "raw"}``;读不到返回 ``{}``(别当成"没有")。
    """
    import re

    path = first_existing(TEXTURE_SLOT_PATHS)
    if not path:
        return {}
    info: dict[str, Any] = {"path": path}
    try:
        info["name"] = str(zbc.get_title(path))
    except Exception:                                                    # noqa: BLE001
        info["name"] = ""
    try:
        raw = str(zbc.get_info(path))
    except Exception:                                                    # noqa: BLE001
        raw = ""
    info["raw"] = raw
    for key, pattern in (("width", r"Width\s*=\s*(\d+)"),
                         ("height", r"Height\s*=\s*(\d+)"),
                         ("depth", r"Depth\s*=\s*(\d+)")):
        found = re.search(pattern, raw)
        info[key] = int(found.group(1)) if found else 0
    return info


def export_current_texture(dest_base: str, timeout: float = 8.0) -> str:
    """把 **Texture 面板当前选中的那张贴图**导出成文件(快路径)。

    省掉"导出整个 SubTool"这一步:大模型不必等网格导出,贴图本身也就几十毫秒。
    ``.png`` / ``.tif`` / ``.bmp`` 依次试(ZBrush 各版本支持的导出格式不一样),
    成功返回文件路径,全失败返回空串。

    ⚠ 它导的是**面板当前选中的那张**,不保证等于某个 SubTool 挂的那张 —— 所以这是"快路径",
    要先用 ``scripts/probe_texture_paths.py`` 在真机上确认"选中 SubTool 时面板会跟着切",
    才敢当默认。调用方负责兜底。
    """
    path = first_existing(TEXTURE_EXPORT_PATHS)
    if not path:
        return ""
    for ext in (".png", ".tif", ".bmp"):
        target = dest_base + ext
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        zbc.set_next_filename(target)
        press_err = press(path)
        if wait_for_file(target, timeout):
            if press_err:
                print(f"[ZBTools] 贴图导出其实成功了({os.path.basename(target)}),"
                      f"press 的那条报错可以忽略")
            return target
    return ""


def select_newest_texture() -> dict[str, Any]:
    """把 Texture 面板的当前贴图切到**最新导入的那张**,返回它的信息。

    做法来自 ZBrushCentral 的现成配方(ZScript):``[ISet,Texture:Item Info,[IGetMax,Texture:Item Info]]``
    —— 再把 title 读出来就是贴图名(末尾可能带一个句点,要去掉)。

    返回 ``{"index", "name", "raw"}(``读不到时 index=-1/name=""``)。
    """
    info: dict[str, Any] = {"index": -1, "name": "", "raw": ""}
    if not first_existing((TEXTURE_ITEM_INFO,)):
        return info
    try:
        top = float(zbc.get_max(TEXTURE_ITEM_INFO))
        zbc.set(TEXTURE_ITEM_INFO, top)
        pump(0.4)
        info["index"] = int(top)
    except Exception as exc:                                             # noqa: BLE001
        print(f"[ZBTools] 选中最新贴图失败:{type(exc).__name__}: {exc}")
        return info
    try:
        raw = str(zbc.get_title(TEXTURE_ITEM_INFO))
    except Exception:                                                    # noqa: BLE001
        raw = ""
    info["raw"] = raw
    info["name"] = raw[:-1] if raw.endswith(".") else raw
    return info


def texture_name_candidates(name: str, image_path: str) -> list[str]:
    """弹窗里那条贴图**可能叫什么** —— 挨个试(名字格式各版本不一样,猜一个不如试几个)。"""
    base = os.path.basename(image_path)
    stem = os.path.splitext(base)[0]
    candidates = [name, name.rstrip(), base, stem]
    if name.endswith("."):
        candidates.append(name[:-1])
    out: list[str] = []
    for candidate in candidates:
        candidate = str(candidate).strip()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def assign_texture_via_popup(names: list[str], wait: float = 1.5) -> tuple[bool, str]:
    """按官方文档的做法挂贴图:**按贴图槽 → 在弹出的列表里点那张贴图**。

    官方文档原文:"Click on the Texture Map thumbnail to select a texture map from the main
    Texture palette **pop-up**" —— 所以光按方块是**没用的**(用户实测印证),必须在弹窗里点一条。
    配方来自 ZBrushCentral(见 README 的实测结论):``[IPress,Tool:Texture Map:TextureMap]``
    之后 ``[IClick,"PopUp:<贴图名>"]``。

    返回 (成功?, 说明)。核对:贴图槽的 title 变成那张贴图的名字。
    """
    slot = first_existing(TEXTURE_ASSIGN_PATHS)
    if not slot:
        return False, f"没找到贴图槽(试过 {list(TEXTURE_ASSIGN_PATHS)})"
    before = texture_slot_info()
    for name in names:
        try:
            press(slot)                       # 打开贴图列表弹窗
            pump(0.6)
            zbc.click(f"PopUp:{name}")        # 在弹窗里点那张贴图(对应 ZScript 的 IClick)
            pump(wait)
        except Exception as exc:                                         # noqa: BLE001
            return False, f"按 {slot} / 点 PopUp:{name} 失败:{type(exc).__name__}: {exc}"
        after = texture_slot_info()
        name_after, name_before = str(after.get("name") or ""), str(before.get("name") or "")
        if name_after and name_after != name_before:
            return True, (f"按 {slot} 弹出列表 → 点 PopUp:{name};"
                          f"贴图槽:{name_before or '?'} → {name_after}(名字变了 = 换上了)")
    # 名字都没对上 → 弹窗可能还开着,别把那个列表留在屏幕上(按 Esc 关掉,失败也不影响)
    for key in ("Esc", "Escape"):
        try:
            zbc.press_key(key)
            pump(0.2)
            break
        except Exception:                                                # noqa: BLE001
            continue
    return False, (f"按了 {slot} 并试过 PopUp:{names}(都没让贴图槽改名,弹窗已按 Esc 关掉;"
                   f"当前 {before.get('name') or '?'})")


def import_texture(image_path: str, timeout: float = 30.0) -> tuple[bool, str]:
    """把一张图导入 ZBrush 的 Texture 面板。返回 (成功, 说明)。

    效果判据:导入后 Texture 面板里多一张贴图 —— SDK 没有查询接口,所以这里只用
    "命令是否真的按下去(该项存在)"来判断,并让调用方把结果打印给用户核对。
    """
    if not os.path.exists(image_path):
        raise PluginError(f"要导入的图不存在:{image_path}")
    path = first_existing(TEXTURE_IMPORT_PATHS)
    if not path:
        return False, (f"找不到「导入贴图」的界面项(试过 {list(TEXTURE_IMPORT_PATHS)})——"
                       f"ZBrush 版本不同路径可能不一样;请手工 Texture > Import 这张图:"
                       f"{image_path}")
    zbc.set_next_filename(image_path)
    press_err = press(path)
    if press_err:
        print(f"[ZBTools] 贴图导入的 press 报过一条({press_err.split(':')[0]}),可忽略")
    # 导入之后把"当前贴图"切到**最新这张**(面板顶部的 Item Info 滑条推到最大)——
    # 这样后面挂贴图时才知道该点哪一条
    newest = select_newest_texture()
    note = f"已按 {path} 导入:{os.path.basename(image_path)}"
    if newest.get("name"):
        note += f"(面板当前贴图:{newest['name']},编号 {newest['index']})"
    return True, note


def image_size(path: str) -> tuple[int, int] | None:
    """纯 Python 读 PNG / BMP / TIFF 的宽高。

    为什么不用 PIL:插件的这部分代码跑在 **ZBrush 自带的 Python** 里,那里没有 PIL。
    (探针脚本里有同一份实现 —— 测试会核对两者结果一致。)
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    try:
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
        if head[:2] == b"BM":
            return (abs(int.from_bytes(head[18:22], "little", signed=True)),
                    abs(int.from_bytes(head[22:26], "little", signed=True)))
        if head[:2] in (b"II", b"MM"):
            endian = "little" if head[:2] == b"II" else "big"
            offset = int.from_bytes(head[4:8], endian)
            with open(path, "rb") as handle:
                handle.seek(offset)
                count = int.from_bytes(handle.read(2), endian)
                wanted: dict[int, int] = {}
                for _ in range(count):
                    entry = handle.read(12)
                    if len(entry) < 12:
                        break
                    tag = int.from_bytes(entry[0:2], endian)
                    kind = int.from_bytes(entry[2:4], endian)
                    value = entry[8:12]
                    if tag in (256, 257):
                        if kind == 3:
                            wanted[tag] = int.from_bytes(value[0:2], endian)
                        elif kind == 4:
                            wanted[tag] = int.from_bytes(value, endian)
                if 256 in wanted and 257 in wanted:
                    return (wanted[256], wanted[257])
    except Exception:                                                     # noqa: BLE001
        return None
    return None


def count_obj_vertices(obj_path: str) -> int:
    """数 OBJ 里的顶点行(`v `)—— 用来核对"导出的到底是不是当前选中的那个模型"。

    实测:ZBrush 导出的 OBJ 里 `v` 的行数 == 这个 SubTool 的点数(UV 接缝不会把顶点点拆开,
    只多写 `vt`),所以这是一个便宜又可靠的指纹。顶点都在面之前,所以一见到 `f ` 就能停。
    """
    if not os.path.exists(obj_path):
        return -1
    count = 0
    try:
        with open(obj_path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("v "):
                    count += 1
                elif line.startswith("f "):
                    break
    except OSError as exc:
        print(f"[ZBTools] 数顶点失败:{exc}")
        return -1
    return count


def subtool_fingerprint() -> dict[str, Any]:
    """当前 SubTool 的指纹(点数 + 包围盒)—— 用来核对导出的是不是它。"""
    info: dict[str, Any] = {"points": -1, "box": None}
    try:
        info["points"] = int(zbc.query_mesh3d(0)[0])
    except Exception:                                                     # noqa: BLE001
        pass
    try:
        raw = [float(v) for v in zbc.query_mesh3d(2, 1)]
        if len(raw) >= 6:
            info["box"] = [round(abs(raw[3] - raw[0]), 4), round(abs(raw[4] - raw[1]), 4),
                           round(abs(raw[5] - raw[2]), 4)]
    except Exception:                                                     # noqa: BLE001
        pass
    return info


def clean_texture_name(name: str) -> str:
    """把贴图名规整一下(实测两种写法都见过:``323`` 与 ``depthimg_depth.``)。"""
    return str(name or "").strip().rstrip(".").strip()


def _palette_current() -> str:
    """Texture 面板"当前贴图"的名字(读不到返回空串)。"""
    try:
        return clean_texture_name(str(zbc.get_title(TEXTURE_ITEM_INFO)))
    except Exception:                                                     # noqa: BLE001
        return ""


def scan_texture_palette(limit: int = 120, step_wait: float = 0.05) -> dict[str, int]:
    """扫一遍 Texture 面板:``{贴图名: 编号}``(靠 ``Texture:Item Info`` 滑条逐格读名字)。

    为什么要扫:ZBrush 导出 OBJ 时写出来的贴图是**面板当前选中**的那张 —— 插件上一次会把
    生成的深度图导入面板并选中它,于是**下一次换模型**导出时写出来的还是那张深度图
    (用户实测:换了模型、贴图也不同,深度图还是同一张;而且两张同尺寸时连尺寸核对都拦不住)。
    所以要先把面板"当前贴图"挪到**这个模型自己那张**上,再导出。
    """
    names: dict[str, int] = {}
    if not first_existing((TEXTURE_ITEM_INFO,)):
        return names
    try:
        top = int(float(zbc.get_max(TEXTURE_ITEM_INFO)))
    except Exception:                                                     # noqa: BLE001
        return names
    for index in range(0, min(top, limit) + 1):
        try:
            zbc.set(TEXTURE_ITEM_INFO, float(index))
            pump(step_wait)
            name = _palette_current()
        except Exception:                                                 # noqa: BLE001
            break
        if name and name not in names:
            names[name] = index
    return names


def focus_model_texture(slot_name: str) -> tuple[bool, str]:
    """把 Texture 面板的"当前贴图"挪到**当前 SubTool 挂的那张**上。返回 (确认成功?, 说明)。

    步骤:① 在面板里按名字找(扫 ``Texture:Item Info``),找到就选中它;
    ② 找不到说明这张贴图不在面板里 → 按 ``Clone Txtr``(官方文档:没 Clone 的贴图导不出来)
    再选最新那张;③ 核对面板当前贴图的名字 == 模型挂的贴图名。
    """
    want = clean_texture_name(slot_name)
    if not want:
        return False, "读不到这个模型的贴图名,跳过(后面还有尺寸核对兜底)"
    before = _palette_current()
    if before and before.lower() == want.lower():
        return True, f"面板当前贴图已经是它了({want})"
    try:
        names = scan_texture_palette()
    except Exception as exc:                                              # noqa: BLE001
        return False, f"扫 Texture 面板失败:{type(exc).__name__}: {exc}"
    hit = names.get(want)
    if hit is None:                       # 名字大小写/前后缀对不上的情况也认一下
        for name, index in names.items():
            if name.lower() == want.lower() or name.lower().endswith(want.lower()) \
                    or want.lower().endswith(name.lower()):
                hit = index
                break
    if hit is not None:
        try:
            zbc.set(TEXTURE_ITEM_INFO, float(hit))
            pump(0.4)
        except Exception as exc:                                          # noqa: BLE001
            return False, f"选中 {want}(编号 {hit})失败:{type(exc).__name__}"
        now = _palette_current()
        ok = bool(now) and (now.lower() == want.lower() or now.lower().endswith(want.lower()))
        return ok, (f"面板里找到「{want}」(编号 {hit}),已选中;现在面板当前是「{now}」"
                    if ok else f"选中编号 {hit} 后面板当前是「{now}」,和「{want}」对不上")

    cloned, clone_note = clone_texture()
    if not cloned:
        return False, (f"面板里**没有**「{want}」这张贴图({len(names)} 张:{list(names)[:8]}),"
                       f"而且 {clone_note}")
    after = _palette_current()
    ok = bool(after)
    return ok, (f"面板里原来没有「{want}」→ {clone_note};现在面板当前是「{after}」"
                if ok else f"{clone_note},但读不到面板当前贴图")


def clone_texture() -> tuple[bool, str]:
    """按 "Clone Txtr"(把模型挂的贴图复制一份到 Texture 面板)。

    官方文档:"It is necessary to clone a texture like this if you wish to export it from ZBrush"
    —— 没 Clone 过的贴图 ZBrush 导不出来,而它可能改写面板里那张**别的**贴图(实测症状:
    换模型后得到的深度图还是上一个模型的)。
    """
    path = first_existing(TEXTURE_CLONE_PATHS)
    if not path:
        return False, f"没有 Clone 这一项(试过 {list(TEXTURE_CLONE_PATHS)})"
    try:
        press(path)
        pump(1.5)
    except Exception as exc:                                              # noqa: BLE001
        return False, f"按 {path} 失败:{type(exc).__name__}: {exc}"
    return True, f"已按 {path}(把模型贴图复制进 Texture 面板,不然 ZBrush 导不出来)"


def pump(seconds: float) -> None:
    """让 ZBrush 把命令队列跑完。

    ``press`` 只是把命令排进队列(实测),所以"按完立刻读结果"会读到旧值 ——
    必须一边 ``update()`` 一边等。这里是最朴素的轮询,超时就返回(调用方自己核对结果)。
    """
    deadline = time.time() + max(0.0, float(seconds))
    while True:
        try:
            zbc.update(1, True)
        except Exception:                                                # noqa: BLE001
            pass
        if time.time() >= deadline:
            return
        time.sleep(0.1)


def refresh_and_read_slot() -> dict[str, Any]:
    """重新选一次当前 SubTool 再读贴图槽 —— 有些 ZBrush 版本要"动一下"才会刷新槽上的名字。

    踩过的坑:挂上了但读到的还是旧名字 → 核对判成失败,用户看到"没能自动挂上"。
    """
    try:
        index = int(zbc.get_active_subtool_index())
        if index >= 0:
            zbc.select_subtool(index)
            pump(0.4)
    except Exception:                                                     # noqa: BLE001
        pass
    return texture_slot_info()


def _slot_changed(before: dict[str, Any], expected_size: tuple[int, int] | None = None
                  ) -> tuple[bool, str]:
    """贴图槽是不是真的换成了我们要的那张 —— **只能靠名字**(踩过的坑见下)。

    ⚠ 别用尺寸判:实测(用户那边)深度图是 2749x3684,而模型**原来那张贴图也是
    2749x3684**(深度图就是照原贴图算出来的,尺寸当然一样)——用尺寸核对会**永远判成成功**。
    所以:只有"贴图槽的名字变了"才算换上了;名字读不到就老实说"无法核对"。
    """
    after = refresh_and_read_slot()
    name_before, name_after = str(before.get("name") or ""), str(after.get("name") or "")
    detail = (f"贴图槽:{name_before or '?'} → {name_after or '?'}"
              f"({after.get('width') or 0}x{after.get('height') or 0})")
    if name_after and name_before and name_after != name_before:
        return True, detail + "(名字变了 = 换上了)"
    if not name_after:
        return False, detail + "(名字读不到,无法核对)"
    return False, detail + "(没变)"


def import_texture_to_subtool(image_path: str, expected_size: tuple[int, int] | None = None,
                              wait: float = 3.0) -> tuple[bool, str]:
    """**直接把这图导进当前 SubTool 的贴图槽**(就是"替换模型贴图")。返回 (成功, 说明)。

    用 ``Tool:Texture Map:Import``(实测存在)。比"先导入 Texture 面板、再按贴图方块"少一步,
    而且**可核对**:导完读贴图槽的 tooltip,尺寸变成这张图的尺寸就说明真换上了。
    """
    if not os.path.exists(image_path):
        raise PluginError(f"要导入的图不存在:{image_path}")
    path = first_existing(("Tool:Texture Map:Import",))
    if not path:
        return False, "没有 Tool:Texture Map:Import 这一项(旧版 ZBrush?)"
    before = texture_slot_info()
    try:
        zbc.set_next_filename(image_path)
        press_err = press(path)
        pump(wait)
    except Exception as exc:                                             # noqa: BLE001
        return False, f"按 {path} 失败:{type(exc).__name__}: {exc}"
    ok, how = _slot_changed(before, expected_size)
    note = f"按了 {path}" + (f"(press 报了 {press_err.split(':')[0]},可忽略)" if press_err else "")
    return ok, f"{note};{how}"


def assign_texture_to_subtool(expected_size: tuple[int, int] | None = None,
                              wait: float = 3.0) -> tuple[bool, str]:
    """把**当前活动**的贴图挂到当前 SubTool 上(按 ``Tool > Texture Map`` 的贴图方块)。

    ⚠ 实测(用户反馈):**光按这个方块不管用** —— 贴图进了 Texture 面板,模型的贴图没变。
    所以这条路只在 :func:`import_texture_to_subtool` 走不通时兜底,而且**必须核对**:
    核对不上就老实说没成(不假装成功)。
    """
    before = texture_slot_info()
    notes: list[str] = []
    slot = first_existing(TEXTURE_ASSIGN_PATHS)
    if not slot:
        return False, f"没找到贴图方块的界面项(试过 {list(TEXTURE_ASSIGN_PATHS)})"
    try:
        press_err = press(slot)
        pump(wait)
        notes.append(f"按下 {slot}" + (f"(press 报了 {press_err.split(':')[0]},可忽略)"
                                       if press_err else ""))
    except Exception as exc:                                             # noqa: BLE001
        return False, f"按 {slot} 失败:{type(exc).__name__}: {exc}"
    on_path = first_existing(TEXTURE_ON_PATHS)
    if on_path:
        try:
            zbc.set(on_path, 1)               # 让模型显示贴图
            notes.append(f"打开 {on_path}")
        except Exception as exc:                                         # noqa: BLE001
            notes.append(f"打开 {on_path} 失败({type(exc).__name__})")
    ok, how = _slot_changed(before, expected_size)
    return ok, ";".join(notes) + (";" + how if how else ";贴图槽没变(没换上去)")
