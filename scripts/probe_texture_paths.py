"""探测脚本:找出「导入贴图」和「把贴图挂到 SubTool 上」在你这台 ZBrush 上的**真实** item path。

为什么要探:ZBrush 的 Python SDK 里**没有任何贴图 API**(145 个命令里没有一个 texture),
所以「图片转深度图」功能只能按 ``父面板:子面板:项`` 的命名习惯试候选路径,并用
``zbc.exists()`` 探测。不同 ZBrush 版本路径会变 —— 这个脚本把真实存在的那几条打出来,
我据此把候选表钉死(现在用的候选见 zbtools/zbapi/toolio.py)。

用法:ZBrush > ZScript 面板 > Python Scripting > Load > 本文件,然后看 Python 控制台,
把输出**整段**发我即可。它会:

  1. 逐个 exists() 报告候选路径存不存在、能读到什么值
  2. 现场生成一张 64x64 灰度测试图,真的按一次「导入贴图」(Texture 面板会多一张,可删)
  3. 报告导入前后"贴图方块"的值,以及再按一次方块之后的值变化
  4. **快路径验证**:能不能直接"读出选中模型的贴图"而不用导出网格 ——
     读贴图方块的值、用 Texture:Export 导一张、再和 Tool:Export 带出的那张对比(尺寸/指纹)

⚠ 跑第 4 步之前:**请先点中那个「有 UV + 挂了贴图」的模型**(低模),
   这样对比才说明问题。第 2/3 步会往 Texture 面板里塞一张测试图,
   跑完可以在 Texture 面板里删掉它(名字带 zbtools_probe)。
"""

from __future__ import annotations

import os
import struct
import tempfile
import zlib

import zbrush.commands as zbc

#: 候选路径:先列**可能真的存在**的那几条,再列一些用来发现命名规律的
#: 第一轮实测(ZBrush 2026.2)已经确认的:
#:   ✓ Texture:Import / Texture:Export / Texture:New / Texture:Remove
#:   ✓ Tool:Texture Map / Tool:Texture Map:Texture Map / Tool:Texture Map:Texture On
#:   ✓ Tool:Texture Map:Import / Tool:Texture Map:Export / Tool:UV Map:Texture Map
#:   × Texture:Texture On、…:Texture Map Slot、…:Texture、…:Texture Map On、
#:     Tool:Texture:Texture、Tool:Display Properties:Texture
CANDIDATES = [
    "Texture:Import",
    "Texture:Export",
    "Texture:New",
    "Texture:Remove",
    "Tool:Texture Map",
    "Tool:Texture Map:Texture Map",
    "Tool:Texture Map:Texture On",
    "Tool:Texture Map:Import",
    "Tool:Texture Map:Export",
    "Tool:UV Map:Texture Map",
]

#: 这些项的 tooltip 会说明它到底干什么(第二轮要看的)
TOOLTIP_TARGETS = [
    "Tool:Texture Map:Texture Map",
    "Tool:Texture Map:Export",
    "Tool:Texture Map:Import",
    "Texture:Export",
    "Texture:Import",
]


def write_test_png(path: str, size: int = 64) -> None:
    """写一张 64x64 灰度渐变 PNG(纯标准库 —— ZBrush 自带 Python 里没有 PIL)。"""
    rows = b""
    for y in range(size):
        rows += b"\x00" + bytes(min(255, (x + y) * 2) for x in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", header))
        handle.write(chunk(b"IDAT", zlib.compress(rows)))
        handle.write(chunk(b"IEND", b""))


def image_size(path: str) -> tuple[int, int] | None:
    """纯标准库读 PNG / BMP / TIFF 的宽高(探针跑在 ZBrush 自带 Python 里,没有 PIL)。"""
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
                        if kind == 3:                    # SHORT,值就放在前两个字节
                            wanted[tag] = int.from_bytes(value[0:2], endian)
                        elif kind == 4:                  # LONG
                            wanted[tag] = int.from_bytes(value, endian)
                if 256 in wanted and 257 in wanted:
                    return (wanted[256], wanted[257])
    except Exception:                                                 # noqa: BLE001
        return None
    return None


def read_value(path: str) -> str:
    try:
        return "值=" + repr(zbc.get(path))
    except Exception as exc:                                          # noqa: BLE001
        return f"get 失败({type(exc).__name__})"


def main() -> None:
    print("=" * 70)
    print("ZBTools 贴图路径探测")
    print("=" * 70)

    print("\n--- 1) 候选路径 exists() / get() ---")
    existing = []
    for path in CANDIDATES:
        try:
            ok = bool(zbc.exists(path))
        except Exception as exc:                                      # noqa: BLE001
            print(f"  ?  {path}  exists 抛异常({type(exc).__name__})")
            continue
        print(f"  {'✓' if ok else '×'}  {path}" + (f"   {read_value(path)}" if ok else ""))
        if ok:
            existing.append(path)

    print("\n--- 2) 贴图方块当前的值(导入前)---")
    for path in ("Tool:Texture Map:Texture Map", "Tool:Texture Map"):
        print(f"  {path} → {read_value(path)}")

    print("\n--- 3) 真的导入一张测试图 ---")
    tmp = tempfile.mkdtemp(prefix="zbtools_probe_")
    png = os.path.join(tmp, "zbtools_probe_texture.png")
    write_test_png(png)
    print(f"  测试图:{png}")
    import_path = next((p for p in ("Texture:Import", "Texture:Import Texture",
                                    "Texture:New") if _exists(p)), "")
    if not import_path:
        print("  ✗ Texture 面板里没找到 Import 这类项 —— 请手工看一眼 Texture 面板底部有哪些按钮")
    else:
        try:
            zbc.set_next_filename(png)
            zbc.press(import_path)
            print(f"  已按 {import_path}(队列命令;下面会 update 一下让它跑)")
            zbc.update(1, True)
            print(f"  get_last_used_filename → {_safe(zbc.get_last_used_filename)}")
            print(f"  get_last_typed_filename → {_safe(zbc.get_last_typed_filename)}")
        except Exception as exc:                                      # noqa: BLE001
            print(f"  导入时抛异常:{type(exc).__name__}: {exc}")

    print("\n--- 4) 贴图方块(导入后)+ 按一次方块 ---")
    for path in ("Tool:Texture Map:Texture Map", "Tool:Texture Map"):
        print(f"  {path} 导入后 → {read_value(path)}")
    print("  ↑ 值没有信息量(实测它挂不挂都是 0);真正有用的是下面这两条:")
    for path in ("Tool:Texture Map:Texture Map",):
        print(f"  get_title({path}) 导入后 → {_safe(lambda: zbc.get_title(path))}")
        print(f"  get_info ({path}) 导入后 → {_safe(lambda: zbc.get_info(path))}")
    print("  (如果这里的名字/尺寸变了 → **导入就会顺手替换当前模型的贴图**,不用再按方块)")
    for path in [p for p in ("Tool:Texture Map:Texture Map",
                             "Tool:Texture Map:Texture Map Slot",
                             "Tool:Texture Map:Texture") if _exists(p)]:
        try:
            zbc.press(path)
            zbc.update(1, True)
            print(f"  按了 {path} → 现在 {read_value('Tool:Texture Map:Texture Map')}")
        except Exception as exc:                                      # noqa: BLE001
            print(f"  按 {path} 抛异常:{type(exc).__name__}: {exc}")

    print("\n--- 5) 不用导出网格读贴图 / 替换贴图:到底哪条路成立 ---")
    print("  (这一步请先点中那个「有 UV + 挂了贴图」的模型!)")
    slot = "Tool:Texture Map:Texture Map"
    title_before = _safe(lambda: zbc.get_title(slot))
    info_before = _safe(lambda: zbc.get_info(slot))
    print(f"  贴图槽现在挂着:title={title_before}")
    print(f"                  info ={info_before}")
    print(f"  get_status(方块)→ {_safe(lambda: zbc.get_status(slot))}")

    print("\n  5a) 关键项 tooltip(看它们各自到底干什么):")
    for path in TOOLTIP_TARGETS:
        print(f"    {path} → {_safe(lambda p=path: zbc.get_info(p))}")

    name = ""
    try:
        name = str(zbc.get_title(slot))
    except Exception:                                                 # noqa: BLE001
        pass
    if name:
        print(f"\n  5b) 能不能按名字选中某张贴图?(名字 = {name!r})")
        for guess in (f"Texture:{name}", f"Texture:Texture {name}",
                      f"Texture:{name}.bmp", f"Tool:Texture Map:{name}"):
            print(f"    exists({guess}) → {_exists(guess)}")

    print("\n  5c) 快路径候选:直接导当前贴图(导出后跟「导出 SubTool 带出的那张」对比)")
    fast_dir = os.path.join(tmp, "fast")
    os.makedirs(fast_dir, exist_ok=True)
    fast_files: list[str] = []
    for item in ("Tool:Texture Map:Export", "Texture:Export"):
        if not _exists(item):
            print(f"    {item} 不存在,跳过")
            continue
        for ext in (".bmp", ".tif", ".png"):
            target = os.path.join(fast_dir, item.replace(":", "_") + ext)
            if os.path.exists(target):
                os.remove(target)
            try:
                zbc.set_next_filename(target)
                zbc.press(item)
                for _ in range(25):
                    zbc.update(1, True)
                    if os.path.exists(target) and os.path.getsize(target) > 0:
                        break
                    import time as _t
                    _t.sleep(0.2)
            except Exception as exc:                                  # noqa: BLE001
                print(f"    {item}({ext}) 抛异常:{type(exc).__name__}: {exc}")
            if os.path.exists(target) and os.path.getsize(target) > 0:
                print(f"    ✓ {item} 写出了 {ext}({os.path.getsize(target) / 1024:.0f} KB,"
                      f"尺寸 {image_size(target)})")
                fast_files.append(target)
                break
            print(f"    × {item} 没写出 {ext}")

    print("\n  5d) 对照:导出 SubTool 时 ZBrush 顺手写出的那张")
    obj_path = os.path.join(tmp, "zbtools_probe_model.obj")
    try:
        zbc.set_next_filename(obj_path)
        zbc.press("Tool:Export")
    except Exception as exc:                                          # noqa: BLE001
        print(f"    Tool:Export 抛异常:{type(exc).__name__}: {exc}")
    for _ in range(200):
        zbc.update(1, True)
        if os.path.exists(obj_path) and os.path.getsize(obj_path) > 0:
            break
        import time as _t
        _t.sleep(0.2)
    sidecar = ""
    for candidate in (os.path.splitext(obj_path)[0] + ".bmp",
                      os.path.splitext(obj_path)[0] + "_TXTR.tif",
                      os.path.splitext(obj_path)[0] + ".tif"):
        if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            sidecar = candidate
            break
    if sidecar:
        print(f"    ✓ {os.path.basename(sidecar)}  尺寸 {image_size(sidecar)}  "
              f"sha256 {_sha256(sidecar)}")
    else:
        print(f"    × 没等到(OBJ 有没有写出来:{os.path.exists(obj_path)})")

    if fast_files and sidecar:
        fast_bmp = [p for p in fast_files if p.lower().endswith(".bmp")]
        side_bmp = sidecar.lower().endswith(".bmp")
        for got in fast_files:
            print(f"    {os.path.basename(got)}: 尺寸 {image_size(got)} sha256 {_sha256(got)}")
        if fast_bmp and side_bmp:
            same = _sha256(fast_bmp[0]) == _sha256(sidecar)
            print(f"    → 字节{'完全一致' if same else '**不一致**'}:"
                  f"{'快路径可信,可以设成默认(秒出)' if same else '快路径导出来的不是同一张,继续用老办法'}")
        else:
            print("    → 有一边不是 .bmp,没法逐字节比;请把上面的尺寸/指纹发我")

    print("\n--- 6) 挂贴图:官方文档说贴图槽是「弹窗里选一张」,按这个配方试 ---")
    print("  ⚠ 会用探针图真的改掉当前 SubTool 的贴图;跑完把你自己的贴图挂回去。")
    print(f"  探针图:{os.path.basename(png)}  {image_size(png)}")
    print("  判据:**贴图槽的名字变了**(深度图和原贴图同尺寸,看尺寸会永远判成功)")

    slot = "Tool:Texture Map:Texture Map"
    print("\n  6a) 相关界面项存在吗:")
    for path in ("Texture:Item Info", "Texture:FlpV", "Texture:Import",
                 "Tool:Texture Map:TextureMap", "Tool:Texture Map:Texture Map",
                 "Tool:Texture Map:Texture On", "Tool:Texture Map:Import"):
        print(f"    {'✓' if _exists(path) else '×'}  {path}")

    def slot_name() -> str:
        try:
            return str(zbc.get_title(slot))
        except Exception:                                             # noqa: BLE001
            return ""

    def item_info() -> tuple[str, str]:
        try:
            return (str(zbc.get("Texture:Item Info")), str(zbc.get_title("Texture:Item Info")))
        except Exception as exc:                                      # noqa: BLE001
            return (f"(读不到 {type(exc).__name__})", "")

    before = slot_name()
    print(f"\n  6b) 动手前:贴图槽名字 = {before!r};Texture:Item Info = {item_info()}")

    try:
        zbc.set_next_filename(png)
        zbc.press("Texture:Import")
        for _ in range(20):
            zbc.update(1, True)
            import time as _t
            _t.sleep(0.2)
        top = zbc.get_max("Texture:Item Info")
        zbc.set("Texture:Item Info", top)
        zbc.update(1, True)
    except Exception as exc:                                          # noqa: BLE001
        print(f"    × 导入/选最新贴图抛异常:{type(exc).__name__}: {exc}")
    raw_title, name = "", ""
    try:
        raw_title = str(zbc.get_title("Texture:Item Info"))
        name = raw_title[:-1] if raw_title.endswith(".") else raw_title
    except Exception as exc:                                          # noqa: BLE001
        print(f"    × 读贴图名失败:{type(exc).__name__}: {exc}")
    print(f"    导入后:Item Info = {item_info()};拿到的贴图名 = {name!r}(原样 {raw_title!r})")

    print("\n  6c) 按贴图槽 → 在弹窗里点那张贴图(官方文档 + ZBrushCentral 配方)")
    for slot_path in ("Tool:Texture Map:TextureMap", "Tool:Texture Map:Texture Map"):
        if not _exists(slot_path):
            print(f"    × {slot_path} 不存在,跳过")
            continue
        for candidate in (name, raw_title, os.path.splitext(os.path.basename(png))[0]):
            if not candidate:
                continue
            try:
                zbc.press(slot_path)
                zbc.update(1, True)
                import time as _t
                _t.sleep(0.3)
                zbc.click("PopUp:" + candidate)
                for _ in range(10):
                    zbc.update(1, True)
                    _t.sleep(0.2)
            except Exception as exc:                                  # noqa: BLE001
                print(f"    × {slot_path} / PopUp:{candidate} 抛异常:"
                      f"{type(exc).__name__}: {exc}")
                continue
            now = slot_name()
            print(f"    {slot_path} + PopUp:{candidate} → 贴图槽名字 = {now!r}"
                  f"{'  ✓ 换上了!' if now and now != before else '  × 没变'}")
            if now and now != before:
                break

    print("\n  6c2) 挂上之后再「动一下」SubTool,看贴图槽的名字会不会刷新")
    try:
        index = int(zbc.get_active_subtool_index())
        print(f"    当前 SubTool #{index};重新选一次它…")
        zbc.select_subtool(index)
        zbc.update(1, True)
        now = str(zbc.get_title("Tool:Texture Map:Texture Map"))
        print(f"    重新选中后:贴图槽名字 = {now!r}"
              f"{'  ✓ 刷出来了(说明之前是「读得太早」)' if now and now != before else ''}")
    except Exception as exc:                                          # noqa: BLE001
        print(f"    × 抛异常:{type(exc).__name__}: {exc}")

    print("\n  6d) 备选:Texture:FlpV(把当前贴图上下翻转 —— 如果它管用,可以不用改文件)")
    if _exists("Texture:FlpV"):
        try:
            zbc.press("Texture:FlpV")
            zbc.update(1, True)
            print("    按了 Texture:FlpV(效果要在 ZBrush 里看:贴图是否上下翻了)")
        except Exception as exc:                                      # noqa: BLE001
            print(f"    × 抛异常:{type(exc).__name__}: {exc}")
    else:
        print("    × 没有这一项")

    print("\n  6e) 这几项的 tooltip(它们到底干什么):")
    for path in ("Tool:Texture Map:Texture Map", "Tool:Texture Map:Import",
                 "Tool:Texture Map:Export", "Texture:Import", "Texture:Item Info"):
        print(f"    {path} → {_safe(lambda p=path: zbc.get_info(p))}")

    print("\n--- 7) 换模型时的核对(实测 bug:换了模型,深度图还是上一个模型的) ---")
    print("  ⚠ 请**点中那个你要用的模型**再跑这一节;换模型后可以再跑一次对比。")
    print(f"  相关项:Clone Txtr 存在吗 → "
          f"{_exists('Tool:Texture Map:Clone Txtr')}")
    points, box = -1, None
    try:
        points = int(zbc.query_mesh3d(0)[0])
        raw = [float(v) for v in zbc.query_mesh3d(2, 1)]
        box = [round(abs(raw[3] - raw[0]), 3), round(abs(raw[4] - raw[1]), 3),
               round(abs(raw[5] - raw[2]), 3)]
    except Exception as exc:                                          # noqa: BLE001
        print(f"  × 读不到点数/包围盒:{type(exc).__name__}: {exc}")
    print(f"  当前 SubTool:{points} 点 / 包围盒 {box}")
    slot_title, slot_info = "", ""
    try:
        slot_title = str(zbc.get_title("Tool:Texture Map:Texture Map"))
        slot_info = str(zbc.get_info("Tool:Texture Map:Texture Map"))
    except Exception as exc:                                          # noqa: BLE001
        print(f"  × 读不到贴图槽:{type(exc).__name__}: {exc}")
    slot_size = image_size_of_tooltip(slot_info)
    print(f"  它挂的贴图:{slot_title!r}  tooltip 里的尺寸:{slot_size}")

    print("\n  7a) 扫一遍 Texture 面板(看这个模型的贴图在不在里面、编号是多少)")
    if not _exists("Texture:Item Info"):
        print("    × 没有 Texture:Item Info 这一项(旧版?),跳过")
    else:
        try:
            top = int(float(zbc.get_max("Texture:Item Info")))
            print(f"    面板里共 {top + 1} 张贴图;逐格读名字:")
            found = -1
            for index in range(0, min(top, 60) + 1):
                zbc.set("Texture:Item Info", float(index))
                zbc.update(1, True)
                import time as _t
                _t.sleep(0.05)
                title = str(zbc.get_title("Texture:Item Info"))
                mark = ""
                if slot_title and title.rstrip(".").strip() == slot_title.strip():
                    mark, found = "  ← 就是它(模型挂的这张)", index
                print(f"      #{index}: {title!r}{mark}")
            print(f"    → 模型挂的贴图 {slot_title!r} 在面板里"
                  f"{'编号 ' + str(found) if found >= 0 else '**没找到**(没 Clone 过?)'}")
        except Exception as exc:                                      # noqa: BLE001
            print(f"    × 扫面板失败:{type(exc).__name__}: {exc}")

    check_obj = os.path.join(tmp, "zbtools_probe_model2.obj")
    for stale in (check_obj, os.path.splitext(check_obj)[0] + ".bmp",
                  os.path.splitext(check_obj)[0] + ".mtl"):
        if os.path.exists(stale):
            os.remove(stale)
    try:
        zbc.set_next_filename(check_obj)
        zbc.press("Tool:Export")
    except Exception as exc:                                          # noqa: BLE001
        print(f"  × Tool:Export 抛异常:{type(exc).__name__}: {exc}")
    for _ in range(200):
        zbc.update(1, True)
        if os.path.exists(check_obj) and os.path.getsize(check_obj) > 0:
            break
        import time as _t
        _t.sleep(0.2)

    vertices = 0
    if os.path.exists(check_obj):
        with open(check_obj, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("v "):
                    vertices += 1
                elif line.startswith("f "):
                    break
    print(f"  导出的 OBJ:{os.path.basename(check_obj)}  {vertices} 个顶点"
          f"{'  ✓ 和 SubTool 点数一致' if vertices == points else '  **❌ 和 SubTool 点数不一致(就是这个问题!)**'}")
    side = ""
    base = os.path.splitext(check_obj)[0]
    for candidate in (base + ".bmp", base + "_TXTR.tif", base + ".tif", base + ".png"):
        if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            side = candidate
            break
    if side:
        side_size = image_size(side)
        print(f"  带出的贴图:{os.path.basename(side)}  尺寸 {side_size}"
              f"{'' if (side_size and slot_size and side_size == slot_size) else '  **❌ 和贴图槽说的尺寸不一致(= 导出的不是这个模型那张)**'}")
    else:
        print("  带出的贴图:**一张都没有** —— 官方文档说没 Clone 过的贴图 ZBrush 导不出来:"
              "按一下 Tool > Texture Map 的 Clone Txtr 再跑这一节")

    print("\n--- 结论 ---")
    print(f"  存在的候选:{existing}")
    print("  把上面整段发我,我据此把 toolio.py 的候选表钉死。")
    print("  (记得在 Texture 面板里删掉 zbtools_probe 那张测试图)")
    print("=" * 70)


def _report_paths() -> list[str]:
    """报告落盘位置:优先写到项目里的 _probe/ 下(这样我自己就能读到,你不用手抄)。"""
    places = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        places.append(os.path.join(os.path.dirname(here), "_probe",
                                   "probe_texture_paths_report.txt"))
    except NameError:                       # 被 ZBrush 用别的方式执行时可能没有 __file__
        pass
    places.append(os.path.join(tempfile.gettempdir(), "zbtools_probe_texture_paths.txt"))
    return places


def _run_and_report() -> None:
    """跑一遍并把整段输出:① 打到控制台 ② 写盘 ③ 塞进剪贴板(省得你手抄)。"""
    import contextlib
    import io
    import subprocess
    import sys

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main()
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

    for command in (["pbcopy"], ["clip"]):          # macOS / Windows
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True)
            print("[ZBTools] 报告已复制到剪贴板(直接粘给我即可)")
            break
        except Exception:                                            # noqa: BLE001
            continue
    sys.stdout.flush()


def image_size_of_tooltip(info: str) -> tuple[int, int] | None:
    """从贴图槽的 tooltip 里抠出 Width=/Height=(实测格式:
    'Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)')。"""
    import re
    width = re.search(r"Width\s*=\s*(\d+)", info or "")
    height = re.search(r"Height\s*=\s*(\d+)", info or "")
    if width and height:
        return (int(width.group(1)), int(height.group(1)))
    return None


def slot_now_quiet() -> tuple[int, int] | None:
    """只读尺寸,不打日志(轮询用)。"""
    try:
        return image_size_of_tooltip(zbc.get_info("Tool:Texture Map:Texture Map"))
    except Exception:                                                 # noqa: BLE001
        return None


def _sha256(path: str) -> str:
    import hashlib
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError as exc:
        return f"(读失败 {exc})"


def _exists(path: str) -> bool:
    try:
        return bool(zbc.exists(path))
    except Exception:                                                 # noqa: BLE001
        return False


def _safe(fn) -> str:
    try:
        return repr(fn())
    except Exception as exc:                                          # noqa: BLE001
        return f"失败({type(exc).__name__})"


_run_and_report()
