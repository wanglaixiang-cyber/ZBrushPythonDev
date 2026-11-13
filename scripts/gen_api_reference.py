"""从本地 SDK 生成 ZBrush Python API 索引(可随时重新生成)。

数据来源:
  * sdk/api/zbrush/commands.py       —— 函数签名与 docstring 首行
  * sdk/docs/api/zbr_cmds_*.html     —— 官方分类(Gui / Modeling / System 及其子分类)

输出:docs/API_REFERENCE.md
用法:.venv/bin/python scripts/gen_api_reference.py
"""

from __future__ import annotations

import html as html_mod
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk"
OUT = ROOT / "docs" / "API_REFERENCE.md"

PAGES = [
    ("Gui API", SDK / "docs/api/zbr_cmds_gui.html"),
    ("Modeling API", SDK / "docs/api/zbr_cmds_modeling.html"),
    ("System API", SDK / "docs/api/zbr_cmds_system.html"),
]


def parse_categories(page: pathlib.Path) -> list[tuple[str, list[str]]]:
    """从 HTML 目录里取 {子分类: [函数名]}。"""
    html = page.read_text(encoding="utf-8")
    tokens = re.findall(r'<li class="nested">.*?<a href="#[^"]*">([^<]+)</a>'
                        r'|<a href="#zbrush\.commands\.([a-z_][\w]*)"', html)
    groups: list[tuple[str, list[str]]] = []
    for title, func in tokens:
        if title:
            groups.append((html_mod.unescape(title).replace("()", "").strip(), []))
        elif func and groups:
            groups[-1][1].append(func)
    return [(name, funcs) for name, funcs in groups if funcs]


def parse_stub(stub: pathlib.Path) -> dict[str, tuple[str, str]]:
    """从存根取 {函数名: (签名, 说明首行)}。"""
    src = stub.read_text(encoding="utf-8")
    out: dict[str, tuple[str, str]] = {}
    for match in re.finditer(r'^def ([a-z_][\w]*)\((.*?)\)\s*->\s*([^\n:]+):\n\s*"""(.*?)"""',
                             src, re.M | re.S):
        name, args, ret, doc = match.groups()
        signature = f"{name}({args}) -> {ret.strip()}"
        first = ""
        for line in doc.strip().splitlines():
            line = line.strip()
            if line and not line.startswith(("Args:", "Returns:", "Example:", "..")):
                first = line
                break
        out[name] = (signature, first)
    return out


def main() -> None:
    stub = parse_stub(SDK / "api/zbrush/commands.py")
    lines: list[str] = [
        "# ZBrush Python API 索引(自动生成)",
        "",
        f"由 `scripts/gen_api_reference.py` 从本地 SDK 生成,共 **{len(stub)}** 个函数。",
        "",
        "## 模块",
        "",
        "```python",
        "import zbrush.commands as zbc            # 主模块:151+ 个命令(GUI / 建模 / 系统)",
        "import zbrush.utils as zbu               # run_path / run_script / clear_output",
        "import zbrush.zscript_compatibility as zbz  # ZScript 兼容层",
        "```",
        "",
        "另有类型:`zbc.Stroke`、`zbc.Strokes`(笔划)、`zbc.IPaletteDockingBar`、"
        "`zbc.ISubPaletteTitleMode`(枚举)。",
        "",
        "> 完整签名与 docstring 见 `sdk/api/zbrush/commands.py`;"
        "每个函数都带示例,见 `sdk/examples/fragments/`。",
        "",
    ]
    for title, page in PAGES:
        groups = parse_categories(page)
        total = sum(len(f) for _n, f in groups)
        lines += [f"## {title}({total} 个)", ""]
        for group, funcs in groups:
            lines += [f"### {group}", ""]
            for name in funcs:
                signature, doc = stub.get(name, (name + "()", ""))
                short = re.sub(r"\s+", " ", signature)
                if len(short) > 110:
                    short = short[:107] + "…"
                lines.append(f"- `{short}`" + (f" — {doc}" if doc else ""))
            lines.append("")
    listed = {n for _t, p in PAGES for _g, fs in parse_categories(p) for n in fs}
    missing = sorted(set(stub) - listed)
    if missing:
        lines += ["## 其他(官方分类页里未单独列出)", ""]
        for name in missing:
            signature, doc = stub.get(name, (name + "()", ""))
            short = re.sub(r"\s+", " ", signature)
            if len(short) > 110:
                short = short[:107] + "…"
            lines.append(f"- `{short}`" + (f" — {doc}" if doc else ""))
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    listed = {n for _t, p in PAGES for _g, fs in parse_categories(p) for n in fs}
    missing = sorted(set(stub) - listed)
    print(f"已写入 {OUT.relative_to(ROOT)}({len(stub)} 个函数)")
    print(f"官方分类未收录(需看存根):{len(missing)} 个 -> {', '.join(missing[:12])}…")


if __name__ == "__main__":
    main()
