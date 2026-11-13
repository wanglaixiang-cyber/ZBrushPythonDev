#!/usr/bin/env python3
"""解剖 ZBrush 的 .goz / .obj,把「颜色块藏在哪、怎么框的」直接打出来。

为什么要有这个工具:
    目标是把外部算好的**逐顶点颜色**塞进 ZBrush 能读回的文件里(polypaint)。
    已知 ZBrush 会把 polypaint 写进两种容器(见二进制里的字符串与 ExportTemplates):
      * GoZ —— 原生导入格式(对话框过滤器 *.zpr;*.ztl;*.goz;*.obj),
               二进制里有 [ZBrush,GOZ_TAG_MRGB_LIST] 与 "READING GOZ FILE..."
      * OBJ —— 导出时会写 "#MRGB" 注释块,每顶点 MMRRGGBB 十六进制、每行 64 个
    但**块的框法(长度字段/计数/点序)只有真实文件能确认**,所以先用
    scripts/probe_goz_polypaint.py 在 ZBrush 里导出真样本,再用本工具解剖。

用法:
    .venv/bin/python scripts/inspect_goz.py                 # 自动扫 _probe/
    .venv/bin/python scripts/inspect_goz.py a.goz b.obj     # 指定文件
"""

from __future__ import annotations

import os
import re
import struct
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(PROJECT, "_probe")

_ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")
_HEX_ENTRY = re.compile(rb"^[0-9A-Fa-f]{8}$")


# --------------------------------------------------------------------------- #
# GoZ
# --------------------------------------------------------------------------- #
def inspect_goz(path: str) -> list[str]:
    blob = open(path, "rb").read()
    out = [f"== {os.path.basename(path)}  ({len(blob):,} 字节) =="]
    out.append(f"magic: {blob[:32]!r}")

    # 头部:magic(32B) + 若干 uint32 + 网格名(16B NUL 结尾)
    head = " ".join(f"{struct.unpack_from('<I', blob, off)[0]}" for off in range(0x20, 0x40, 4))
    out.append(f"头部 uint32(0x20-0x3f): {head}")

    names = [(m.start(), m.group().decode("latin-1"))
             for m in _ASCII_RUN.finditer(blob) if b"GoZ" in m.group()]
    out.append(f"含 GoZ 的 ASCII 串({len(names)} 个):")
    for off, text in names[:12]:
        out.append(f"  0x{off:08x}  {text[:70]}")

    # 关键:标签块是不是以 ASCII "[ZBrush,..." 形式出现
    tags = [(m.start(), m.group().decode("latin-1"))
            for m in _ASCII_RUN.finditer(blob) if m.group().startswith(b"[ZBrush")]
    if tags:
        out.append(f"发现 {len(tags)} 个 [ZBrush,...] 标签块:")
        for off, tag in tags:
            nxt = blob[off + len(tag):off + len(tag) + 40]
            out.append(f"  0x{off:08x}  {tag}  后 40 字节: {nxt.hex(' ')}")
    else:
        out.append("没有 [ZBrush,...] ASCII 标签 → 这是**纯二进制**变体(区块靠隐式顺序排)")

    # 区块标签:实测规律 = 5000*2^k + 1(点表 5001、下一个 10001、面表 20001 ……)
    tag_ids = {5000 * (1 << k) + 1: f"区块#{k}" for k in range(10)}
    out.append("区块标签扫描(值 = 5000*2^k+1):")
    for off in range(0x40, min(len(blob), 0x4000), 4):
        i = struct.unpack_from("<I", blob, off)[0]
        if i in tag_ids:
            after = [struct.unpack_from("<I", blob, off + 4 + 4 * j)[0] for j in range(4)]
            out.append(f"  0x{off:08x}  {tag_ids[i]}  tag={i}  其后 4 个 uint32: {after}")

    # 点块自动定位:找最长的"像坐标"的连续 float 段(每个点 3 个 float)
    def plausible(off: int) -> bool:
        f = struct.unpack_from("<f", blob, off)[0]
        return (f == f) and abs(f) < 1e6 and (f == 0.0 or abs(f) > 1e-6)

    best_start, best_len, run_start, run_len = 0, 0, None, 0
    for off in range(0x40, len(blob) - 4, 4):
        if plausible(off):
            run_start = off if run_len == 0 else run_start
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_len = 0
    if best_len >= 9:
        out.append(f"最长连续坐标段:起点 0x{best_start:08x},{best_len} 个 float ≈ "
                   f"{best_len // 3} 个点(余 {best_len % 3});它的前 5 个 uint32 = "
                   f"{[struct.unpack_from('<I', blob, best_start - 4 * j)[0] for j in (4, 3, 2, 1)]}")
    else:
        out.append("没有找到像坐标的连续 float 段(可能不是网格文件,或已被压缩)")

    # MMRRGGBB 十六进制块(若 ZBrush 用文本方式存颜色)
    hex_hits = 0
    for m in re.finditer(rb"(?:[0-9A-Fa-f]{8}){8,}", blob):
        hex_hits += 1
        if hex_hits <= 3:
            out.append(f"疑似十六进制颜色流 @0x{m.start():08x}: {m.group()[:64].decode('latin-1')}…")
    out.append(f"疑似十六进制颜色流数量:{hex_hits}(0 = 颜色不是以文本十六进制存的)")
    return out


# --------------------------------------------------------------------------- #
# OBJ
# --------------------------------------------------------------------------- #
def inspect_obj(path: str) -> list[str]:
    raw = open(path, "rb").read()
    text = raw.decode("latin-1")
    lines = text.splitlines()
    out = [f"== {os.path.basename(path)}  ({len(raw):,} 字节) =="]
    counts = {"v ": 0, "vt ": 0, "f ": 0, "vn ": 0}
    for line in lines:
        for key in counts:
            if line.startswith(key):
                counts[key] += 1
    out.append(f"元素计数: v={counts['v ']} vt={counts['vt ']} f={counts['f ']} vn={counts['vn ']}")

    mrgb_at = text.find("#MRGB")
    if mrgb_at < 0:
        out.append("没有 #MRGB 块 → 这次导出**没有带 polypaint**(要么模型没上色,要么该导出模式不写颜色)")
        return out

    head = text.find("# The following MRGB block")
    end = text.find("# End of MRGB block", mrgb_at)
    out.append(f"#MRGB 位置:说明文字 @{head},块起始 @{mrgb_at},块结束 @{end}")
    body_start = text.find("\n", mrgb_at) + 1
    body = text[body_start:end if end > 0 else len(text)]
    # ⚠ 实测格式:颜色是**连写的十六进制流**(一行 64 条 = 512 个字符,不加分隔符),
    #   所以必须去掉所有空白后按 8 个字符一条切,不能按空白切分。
    stream = re.sub(r"[\s#]", "", body)
    if re.fullmatch(r"[0-9A-Fa-f]*", stream):
        entries = [stream[i:i + 8] for i in range(0, len(stream) - 7, 8)]
    else:
        entries = [tok for tok in body.split() if _HEX_ENTRY.match(tok.encode())]
    out.append(f"颜色流长度:{len(stream)} 字符 → 条目数 {len(entries)}"
               f"(对比 v 数 {counts['v ']} → "
               f"{'一致 ✓ 一顶点一条' if len(entries) == counts['v '] else '不一致,需要核对点序/分组'})")
    out.append(f"前 4 条:{entries[:4]}")
    out.append("对照:ZBrush 的格式是 MMRRGGBB(第 1 字节=Mask,后 3 字节=RGB)")
    return out


def inspect(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".goz":
            return inspect_goz(path)
        if ext == ".obj":
            return inspect_obj(path)
        return [f"== {os.path.basename(path)} == 不认识的扩展名:{ext}"]
    except Exception as exc:                                              # noqa: BLE001
        return [f"== {os.path.basename(path)} == 解析失败:{type(exc).__name__}: {exc}"]


def main(argv: list[str]) -> int:
    targets = argv[1:]
    if not targets:
        if not os.path.isdir(DEFAULT_DIR):
            print(f"没有 {DEFAULT_DIR};要么先跑探针,要么把文件路径作为参数传进来")
            return 1
        targets = [os.path.join(DEFAULT_DIR, n) for n in sorted(os.listdir(DEFAULT_DIR))
                   if os.path.splitext(n)[1].lower() in (".goz", ".obj")]
    if not targets:
        print(f"{DEFAULT_DIR} 里没有 .goz/.obj")
        return 1
    for path in targets:
        for line in inspect(path):
            print(line)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
