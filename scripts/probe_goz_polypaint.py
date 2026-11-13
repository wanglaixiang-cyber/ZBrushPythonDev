"""探针:低模贴图 → 高模 polypaint 的「落地通道」验证。

背景(为什么需要这个探针)
    目标:低模(有 UV + 贴图)→ 高模 DynaMesh,**只要 color**,不要几何。
    官方原生路线 ``Tool:Polypaint:Polypaint From Texture`` 是**逐顶点**采样贴图,
    所以官方文档要求「把模型细分到接近贴图像素数」—— 这就是我们想绕开的瓶颈。
    绕开的唯一办法:在**外部**按高模自己的顶点去采样贴图(不用细分低模),
    然后把逐顶点颜色塞回 ZBrush。本探针验证的就是"塞回去"的三条通道:

      A. GoZ 往返:ZBrush 导出 .goz → 外部改里面的 MRGB 块 → 导回
         (GoZ 是原生格式:导入对话框过滤器 *.zpr;*.ztl;*.goz;*.obj 里有它,
          且二进制里有 GOZ_TAG_MRGB_LIST / "READING GOZ FILE...")
      B. OBJ 的 #MRGB 块:导出带 polypaint 的 OBJ → 外部改 → 导回能不能读回
         (ZBrush 自己会写 "#MRGB" + MMRRGGBB 十六进制,64 个一行)
      C. 原生按钮的 item path(Project All / Morph Target / AUVTiles / Polypaint …)
         —— 如果 B/A 通道不通,退回「目标模型自己转 polypaint」的原生组合拳时要用

用法
    ZBrush > ZScript > Python Scripting > Load > 本文件
    **先保存工程**(末尾会问要不要试导入;导入会占用当前 Tool 槽)。
    结果:控制台打印 + 自动复制到剪贴板;导出的文件落在
    ``<项目>/_probe/`` 里(把那个目录整个发我,或只报文件名)。

    想快一点:先选中**低模**(有 UV 的那个)跑一遍 —— 格式信息完全一样,文件小很多;
    确认通道后再对高模跑正式流程。
"""

from __future__ import annotations

import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "plugin", "zbtools"))

import zbrush.commands as zbc                                        # noqa: E402

OUT_DIR = os.path.join(PROJECT, "_probe")
WAIT_S = 180.0                       # 大网格导出慢:等写盘的最长时间
LINES: list[str] = []

#: 原生按钮候选(item path 随版本/本地化变,靠 exists() 一次性问清)
CANDIDATES: dict[str, list[str]] = {
    "贴图→polypaint(原生)": [
        "Tool:Polypaint:Polypaint From Texture",
        "Tool:Polypaint:Polypaint On",
        "Tool:Polypaint:Colorize",
        "Tool:Polypaint:Fill Object",
    ],
    "polypaint→贴图(反向,对照用)": [
        "Tool:Texture Map:New From Polypaint",
        "Tool:Texture Map:Create From Polypaint",
        "Tool:Texture Map:Clone Texture",
        "Tool:Texture Map:Texture On",
    ],
    "投影(Project All,只投 color 靠 Morph)": [
        "Tool:SubTool:Project:Project All",
        "Tool:Subtool:Project:Project All",
        "Tool:SubTool:Project All",
        "Tool:SubTool:Project:Dist",
        "Tool:SubTool:Project:PA Blur",
        "Tool:SubTool:Project:Mean",
    ],
    "Morph Target(投完回滚几何、留下 color)": [
        "Tool:Morph Target:StoreMT",
        "Tool:Morph Target:Switch",
        "Tool:MorphTarget:StoreMT",
        "Tool:Morph Target:DelMT",
    ],
    "UV(给高模临时 UV 用)": [
        "Tool:UV Map:AUVTiles",
        "Tool:UV Map:Group UVTiles",
        "Tool:UV Map:UV Master:Unwrap",
        "Tool:UV Map:UV Master:Work on Clone",
        "Zplugin:UV Master:Unwrap",
        "Zplugin:UV Master:Work on Clone",
    ],
    "导入导出 / GoZ": [
        "Tool:Import",
        "Tool:Export",
        "Tool:Export:Scale",
        "GoZ:Export",
        "GoZ:Export All",
        "GoZ:Export Visible",
        "GoZ:Import",
        "Zplugin:GoZ:Export",
        "Zplugin:GoZ:Export All",
        "Zplugin:GoZ:Import",
    ],
}


def log(text: str) -> None:
    LINES.append(text)
    print(f"[polypaint探针] {text}")


def try_call(fn, default=None):
    try:
        return True, fn()
    except Exception as exc:                                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def tool_list() -> list[tuple[int, str]]:
    ok, count = try_call(zbc.get_tool_count)
    if not ok:
        return []
    out = []
    for index in range(int(count or 0)):
        ok2, name = try_call(lambda i=index: zbc.get_tool_path(i))
        out.append((index, str(name) if ok2 else "<读取失败>"))
    return out


def mesh_counts() -> tuple[int, int, str]:
    """当前 SubTool 的点数 / 面数 / 是否有 UV。"""
    ok_p, points = try_call(lambda: int(zbc.query_mesh3d(0)[0]))
    ok_f, faces = try_call(lambda: int(zbc.query_mesh3d(1)[0]))
    ok_uv, uv_box = try_call(lambda: [round(float(v), 4) for v in zbc.query_mesh3d(3)])
    uv_text = f"UV 框 {uv_box}" if ok_uv else "无 UV(或读取失败)"
    return (int(points) if ok_p else -1), (int(faces) if ok_f else -1), uv_text


def wait_for_file(path: str) -> bool:
    """等 ZBrush 把文件写盘(press 是命令队列,不是同步调用)。"""
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def inspect_file(path: str, tags: tuple[str, ...]) -> str:
    """读文件头/标签位置:告诉我们 MRGB 块到底有没有、在哪。"""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        blob = handle.read()
    parts = [f"{os.path.basename(path)} = {size / 1024:.1f} KB"]
    for tag in tags:
        needle = tag.encode()
        at = blob.find(needle)
        if at < 0:
            parts.append(f"  缺 {tag!r}")
            continue
        tail = blob[at:at + 160]
        payload = tail.decode("latin-1").replace("\r", " ").replace("\n", " ")
        parts.append(f"  有 {tag!r} @ 偏移 {at}:{payload[:120]}")
    return "\n".join(parts)


def export_as(path: str, label: str) -> bool:
    """官方路线:set_next_filename(全路径) + press(Tool:Export)。"""
    log(f"导出({label})→ {path}")
    ok, err = try_call(lambda: zbc.set_next_filename(path))
    if not ok:
        log(f"  set_next_filename 失败:{err}")
        return False
    ok, err = try_call(lambda: zbc.press("Tool:Export"))
    if not ok:
        log(f"  press('Tool:Export') 失败:{err}")
        return False
    if not wait_for_file(path):
        log(f"  超时:{WAIT_S:.0f} 秒内没写出文件(可能弹了「另存为」对话框,或网格太大)")
        return False
    log(f"  写出成功")
    return True


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"项目目录:{PROJECT}")
    log(f"输出目录:{OUT_DIR}")

    ok, active = try_call(zbc.get_active_tool_path)
    points, faces, uv_text = mesh_counts()
    log(f"当前 Tool:{active if ok else '<读取失败>'}")
    log(f"当前 SubTool 点数 / 面数:{points} / {faces};{uv_text}")
    log(f"Tool 列表:{tool_list()}")

    log("---- ① item path 探测(原生按钮到底叫什么)----")
    for group, paths in CANDIDATES.items():
        log(f"[{group}]")
        for path in paths:
            ok, found = try_call(lambda p=path: bool(zbc.exists(p)))
            if not ok:
                log(f"  ?    {path}(exists 抛异常:{found})")
                continue
            if not found:
                log(f"  --   {path}")
                continue
            ok_v, value = try_call(lambda p=path: zbc.get(p))
            extra = f"= {value}" if ok_v else "(不可取值,可能是按钮)"
            ok_t, title = try_call(lambda p=path: zbc.get_title(p))
            log(f"  OK   {path} {extra};title={title if ok_t else '?'}")

    log("---- ② 导出 .goz(通道 A:MRGB 块在不在)----")
    goz_plain = os.path.join(OUT_DIR, "plain.goz")
    goz_exported = export_as(goz_plain, "goz/未上色")
    if goz_exported:
        log(inspect_file(goz_plain, ("GOZ_TAG_MRGB_LIST", "GOZ_TAG_POINT_LIST",
                                     "GOZ_TAG_FACE4", "GOZ_TAG_UV4", "GoZMesh_")))

    log("---- ③ 导出 .obj(通道 B:#MRGB 块在不在)----")
    obj_plain = os.path.join(OUT_DIR, "plain.obj")
    obj_exported = export_as(obj_plain, "obj/未上色")
    if obj_exported:
        log(inspect_file(obj_plain, ("#MRGB", "vt ", "f ")))

    log("---- ④ 对照实验:涂几笔 polypaint 后再导出一次 ----")
    log("   (会弹两个确认框;没兴趣就点 No 跳过,②③ 的信息已经够我判断格式)")
    ok, answer = try_call(lambda: bool(zbc.message_yes_no(
        "对照实验(可跳过)\n\n"
        "接下来请手动做 3 件事:\n"
        "  1. Tool > Polypaint 打开 Polypaint / Colorize(有 Colorize 才看得见颜色)\n"
        "  2. 在当前 SubTool 上随便涂几笔(或用 Polypaint From Texture 上一遍贴图色)\n"
        "  3. 回到这个对话框点 Yes,我再导出一次做 A/B 对比\n\n"
        "目的:搞清 ZBrush 到底怎么把 polypaint 写进 .goz / .obj(有没有 MRGB 块、"
        "块前面是什么长度字段)。AI 靠这个才能做「外部算颜色 → 写进文件 → 导回」。",
        "polypaint 对照实验")))
    if ok and answer:
        goz_paint = os.path.join(OUT_DIR, "painted.goz")
        obj_paint = os.path.join(OUT_DIR, "painted.obj")
        if export_as(goz_paint, "goz/已上色"):
            log(inspect_file(goz_paint, ("GOZ_TAG_MRGB_LIST", "GOZ_TAG_MASK8_LIST")))
        if export_as(obj_paint, "obj/已上色"):
            log(inspect_file(obj_paint, ("#MRGB",)))
        log("请人工确认:高模/低模上现在有颜色吗?(有 → polypaint 生效了)")
    else:
        log("已跳过对照实验")

    log("---- ⑤ 导回测试(可选,会占用当前 Tool 槽)----")
    if goz_exported:
        ok, answer = try_call(lambda: bool(zbc.message_yes_no(
            f"要不要试导入 {os.path.basename(goz_plain)}?\n\n"
            "实测过:导入会**占用当前 Tool 槽**(名字变成 !:路径),不是追加 Subtool。\n"
            "所以请先保存工程,或者用一个随便的测试工具。\n\n"
            "这一步验证的是「GoZ 导回能不能恢复带色的网格」—— 通道 A 的最后一块拼图。",
            "GoZ 导回测试")))
        if ok and answer:
            log(f"导入前 Tool 列表:{tool_list()}")
            ok2, err = try_call(lambda: zbc.set_next_filename(goz_plain))
            if ok2:
                ok2, err = try_call(lambda: zbc.press("Tool:Import"))
            if not ok2:
                log(f"导入失败:{err}")
            else:
                time.sleep(3.0)
                p2, f2, uv2 = mesh_counts()
                log(f"导入后 Tool 列表:{tool_list()}")
                log(f"导入后点数 / 面数:{p2} / {f2}(导入前 {points} / {faces});{uv2}")
                log("请人工确认:新 Tool 上有没有颜色?→ 有 = 通道 A 完全打通")
        else:
            log("已跳过导回测试")

    log("---- 完成 ----")
    log(f"把 {OUT_DIR} 里的文件发我(或只报文件名+上面这段日志)")


try:
    main()
except Exception as exc:                                              # noqa: BLE001
    import traceback
    print(f"[polypaint探针] 未捕获异常:{exc}\n{traceback.format_exc()}")

try:
    from zbtools.zbapi.clipboard import copy_to_clipboard              # noqa: PLC0415
    text = "\n".join(LINES)
    print("[polypaint探针] 复制到剪贴板:" + ("成功" if copy_to_clipboard(text) else "失败"))
except Exception as exc:                                              # noqa: BLE001
    print(f"[polypaint探针] 剪贴板不可用:{exc}")
