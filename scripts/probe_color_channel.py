"""探针(第 3 轮):顶点色通道验证 —— ZBrush 的**导入**到底认哪种顶点色写法。

为什么是这一步
    第 1 轮已知:ZBrush 用 ``Tool:Export`` 导出的 .obj/.goz **不带 polypaint**
    (导出模板决定,默认的 `GoZ Binary.GoZ` 里没有 MRGB 段;OBJ 更是逐字节相同)。
    所以要"把外部算好的颜色塞回 ZBrush",只剩验证**导入侧认哪种写法**:
      A. OBJ + `#MRGB` 注释块(ZBrush 自己导出 polypaint 时用的就是这个)
      B. OBJ + 顶点行带颜色 `v x y z r g b`
      C. PLY 二进制 + 标准 vertex color(ZBrush 自带 ZData/3DFileFormats/IMP_PLY_ply.zsc)
    三份测试文件由本项目的 `scripts/make_color_test_files.py` 生成,
    颜色是**按 Z 轴的彩虹渐变** —— 导进去如果颜色平滑,说明通道通**且顶点顺序一一对应**;
    如果是花哨噪点,说明顺序不对;如果完全没颜色,说明这种写法不认。

前置
    1. 先跑过 `scripts/probe_goz_polypaint.py`(它导出 _probe/plain.obj 作为测试底模)
    2. 再跑 `scripts/make_color_test_files.py`(生成三份测试文件)
    3. **保存工程!** 导入会占用当前 Tool 槽

用法
    ZBrush > ZScript > Python Scripting > Load > 本文件
    每步都会弹框问你要不要做;结果打印到控制台并复制到剪贴板。
"""

from __future__ import annotations

import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, "plugin", "zbtools"))

import zbrush.commands as zbc                                        # noqa: E402

PROBE_DIR = os.path.join(PROJECT, "_probe")
TESTS = [
    ("A: OBJ + #MRGB 块", "mrgb_test.obj"),
    ("B: OBJ + 顶点行带颜色 v x y z r g b", "vrgb_test.obj"),
    ("C: PLY 二进制 + 标准顶点色", "color_test.ply"),
]
FORMAT_DIRS = ["ZData/3DFileFormats", "ZStartup/3DFileFormats",
               "ZData/ExportTemplates", "ZStartup/ExportTemplates"]
LINES: list[str] = []


def log(text: str) -> None:
    LINES.append(text)
    print(f"[颜色通道探针] {text}")


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


def counts() -> str:
    ok_p, points = try_call(lambda: int(zbc.query_mesh3d(0)[0]))
    ok_f, faces = try_call(lambda: int(zbc.query_mesh3d(1)[0]))
    ok_t, tool = try_call(zbc.get_active_tool_path)
    return (f"活动 Tool={tool if ok_t else '?'};点数={points if ok_p else '?'};"
            f"面数={faces if ok_f else '?'}")


def main() -> None:
    log(f"项目目录:{PROJECT}")
    ok, tool = try_call(zbc.get_active_tool_path)
    log(f"当前版本:{try_call(lambda: round(float(zbc.zbrush_info(0)), 2))}")
    log(f"活动 Tool:{tool if ok else '?'}")
    log(f"Tool 列表:{tool_list()}")
    log(f"导入前:{counts()}")

    log("---- ① 「当前导出模板」是什么(ZBrush 用它决定写不写颜色段)----")
    ok, tpl = try_call(zbc.get_next_template_filename)
    log(f"get_next_template_filename() = {tpl!r}" if ok else f"读取失败:{tpl}")
    log("参考:ExportTemplates 目录里有 5 个 GoZ 模板,"
        "其中 GoZ Binary / NoFlags **不含 MRGB**,Complete / Cinema4D / WithMatcaps 含")

    log("---- ② 这台机器认得哪些 3D 格式处理器 ----")
    for rel in FORMAT_DIRS:
        for base in (os.getcwd(), "/Applications/Maxon ZBrush 2026", PROJECT):
            path = os.path.join(base, rel)
            if os.path.isdir(path):
                ok_l, names = try_call(lambda p=path: sorted(os.listdir(p)))
                log(f"{path}:")
                if ok_l:
                    for name in names:
                        log(f"    {name}")
                else:
                    log(f"    <读取失败:{names}>")
                break
        else:
            log(f"{rel}: 三个候选根目录下都没找到")

    log(f"测试文件(由 scripts/make_color_test_files.py 生成于 {PROBE_DIR}):")
    for label, name in TESTS:
        path = os.path.join(PROBE_DIR, name)
        exists = os.path.exists(path)
        size = f"{os.path.getsize(path) / 1024 / 1024:.1f} MB" if exists else "缺失"
        log(f"    {label:<38} {name:<18} {size}")

    log("---- ③ 依次导入测试文件,看颜色认不认 ----")
    if not try_call(lambda: bool(zbc.message_yes_no(
            "接下来要依次导入三份测试文件(OBJ+MRGB / OBJ+顶点色 / PLY)。\n\n"
            "⚠ 导入会**占用当前 Tool 槽**(实测行为,不是追加 Subtool)。所以:\n"
            "  · 已保存工程 → 直接点 Yes 继续\n"
            "  · 没保存 → 先点 No,保存工程后再重新 Load 本脚本\n\n"
            "每一步导入后请**看一眼模型有没有彩虹渐变**(按 Z 轴平滑过渡),"
            "最后把结果告诉我(哪几个有颜色)。", "颜色通道验证")))[1]:
        log("用户取消,未做导入测试")
    else:
        for label, name in TESTS:
            path = os.path.join(PROBE_DIR, name)
            if not os.path.exists(path):
                log(f"{label}:跳过(文件不存在)")
                continue
            ok, answer = try_call(lambda n=label: bool(zbc.message_yes_no(
                f"导入【{n}】?\n\n导入后请观察:模型是彩虹渐变(按 Z 轴平滑),"
                f"还是没颜色/花斑?", f"导入测试:{n}")))
            if not (ok and answer):
                log(f"{label}:用户跳过")
                continue
            log(f"导入 {label} ← {path}")
            before = tool_list()
            ok1, err = try_call(lambda p=path: zbc.set_next_filename(p))
            if ok1:
                ok1, err = try_call(lambda: zbc.press("Tool:Import"))
            if not ok1:
                log(f"  导入失败:{err}(可能这个扩展名 Tool:Import 不接受)")
                continue
            time.sleep(4.0)
            log(f"  导入后:{counts()}")
            after = tool_list()
            log(f"  Tool 列表变化:{before} → {after}")

    log("---- ④ 完成,请回答三个问题(直接回我)----")
    log("   Q1 变体 A(OBJ+#MRGB):有彩虹吗?")
    log("   Q2 变体 B(OBJ+顶点色 v x y z r g b):有彩虹吗?")
    log("   Q3 变体 C(PLY):导入成功了吗?有彩虹吗?")
    log("   Q4 手动 Tool > Export 时,保存对话框的「格式」下拉里有 PLY / OBJ Extended 吗?")


try:
    main()
except Exception as exc:                                              # noqa: BLE001
    import traceback
    print(f"[颜色通道探针] 未捕获异常:{exc}\n{traceback.format_exc()}")

try:
    from zbtools.zbapi.clipboard import copy_to_clipboard              # noqa: PLC0415
    print("[颜色通道探针] 复制到剪贴板:" + ("成功" if copy_to_clipboard("\n".join(LINES)) else "失败"))
except Exception as exc:                                              # noqa: BLE001
    print(f"[颜色通道探针] 剪贴板不可用:{exc}")
