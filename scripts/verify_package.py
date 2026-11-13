#!/usr/bin/env python3
"""验收一个打包好的 ZBTools 发行包(**不需要打开 ZBrush**)。

四个检查项:

1. **字节码魔数**:每个 ``.pyc`` 的头 4 字节必须等于当前解释器的 magic。
   打包用的是 CPython 3.11.9(与 ZBrush 2026 内嵌 VM 一致),所以这条等价于
   "这个包能在 ZBrush 里被导入"。
2. **源码泄漏**:除了 ``tools/`` 下的外部脚本(它们本来就该是源码 —— 要跑在
   **别的** Python 版本里),包内不应再有 ``.py``。
3. **入口可执行**:在临时副本里装一个假 ``zbrush`` 模块,执行 ``init.py`` / ``init.pyc``,
   跑完 ``register()`` 必须成功(返回 True,且没有"控件创建失败")。
4. **清单校验**:包内有 ``MANIFEST.sha256`` 就逐文件核对。

用法::

    .venv/bin/python scripts/verify_package.py dist/ZBTools-0.2.0-macos
    .venv/bin/python scripts/verify_package.py dist/ZBTools-0.2.0-windows --json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

#: 这些位置是**源码**,不是泄漏:
#:   * ``init.py`` —— ZBrush 只按这个名字找入口,必须是 .py;
#:   * 外部工具脚本 —— 由用户配置的外部 Python 执行,3.11 的 .pyc 在 3.9/3.13 里跑不了;
#:   * ``manual/`` —— 手动加载器,要能被 ZBrush 的 Load 对话框选中。
SOURCE_KEPT_FILES = ("init.py",)
SOURCE_KEPT_PREFIXES = ("tools/", "manual/", "zbtools/features/tex2pp/tools/",
                        "zbtools/features/depthimg/tools/")

#: 入口在包里的名字(源代码包 / 字节码包)
ENTRY_NAMES = ("init.py", "init.pyc")


def is_source_kept(rel: str) -> bool:
    """这个相对路径是否属于"必须保持源码"的部分。"""
    normalized = rel.replace(os.sep, "/")
    return normalized in SOURCE_KEPT_FILES or any(
        normalized.startswith(prefix) for prefix in SOURCE_KEPT_PREFIXES)


def collect(root: str) -> list[str]:
    files: list[str] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name == ".DS_Store":
                continue
            files.append(os.path.relpath(os.path.join(current, name), root).replace(os.sep, "/"))
    return sorted(files)


def check_bytecode(root: str, files: list[str]) -> list[str]:
    errors: list[str] = []
    magic = importlib.util.MAGIC_NUMBER
    for rel in files:
        if not rel.endswith(".pyc"):
            continue
        with open(os.path.join(root, rel), "rb") as handle:
            head = handle.read(4)
        if head != magic:
            errors.append(f"{rel}: 字节码版本不匹配(头 {head!r} != {magic!r})")
    return errors


def check_sources(files: list[str], bytecode: bool) -> list[str]:
    errors: list[str] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        if is_source_kept(rel):
            continue
        errors.append(f"{rel}: 不应随包发布源码(.pyc 发布模式下)")

    if not any(rel.endswith("init.py") for rel in files):
        errors.append("缺少入口 init.py(ZBrush 只按这个名字找自动加载入口)")
    return errors


def check_manifest(root: str, files: list[str]) -> list[str]:
    manifest = os.path.join(root, "MANIFEST.sha256")
    if not os.path.exists(manifest):
        return []
    errors: list[str] = []
    listed: set[str] = set()
    with open(manifest, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                digest, rel = line.split("  ", 1)
            except ValueError:
                errors.append(f"MANIFEST.sha256 行格式不对:{line!r}")
                continue
            listed.add(rel)
            full = os.path.join(root, rel)
            if not os.path.exists(full):
                errors.append(f"清单里有、包里没有:{rel}")
                continue
            with open(full, "rb") as item:
                actual = hashlib.sha256(item.read()).hexdigest()
            if actual != digest:
                errors.append(f"sha256 不匹配:{rel}")
    extra = [rel for rel in files
             if rel not in listed and rel not in ("MANIFEST.sha256",)]
    for rel in extra:
        errors.append(f"清单里没有:{rel}")
    return errors


#: 在子进程里跑的入口验收脚本(避免把假 zbrush 装进当前进程)。
_RUNNER = r'''
import importlib.util
import io
import json
import contextlib
import os
import sys

pkg, tests, entry_rel = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, tests)          # tests/fake_zbrush.py
sys.path.insert(0, pkg)            # 插件包

import fake_zbrush

# fake_zbrush 在 import 时会把**开发仓库**的 plugin 目录插到 sys.path[0];
# 验收要的是包里的代码,必须把它撤掉,否则 register() 跑的是源码而不是 .pyc。
while fake_zbrush.PLUGIN in sys.path:
    sys.path.remove(fake_zbrush.PLUGIN)
sys.path.insert(0, pkg)

import zbtools

fake = fake_zbrush.FakeCommands()
fake_zbrush.install(fake)

# 额外确认:真的导入的是包里的 zbtools,而不是开发仓库那份
if not os.path.realpath(zbtools.__file__).startswith(os.path.realpath(pkg)):
    raise SystemExit(f"导入的不是包内代码:{zbtools.__file__}")

entry = os.path.join(pkg, entry_rel)

buf = io.StringIO()
error = None
try:
    with contextlib.redirect_stdout(buf):
        if entry.endswith(".pyc"):
            from importlib.machinery import SourcelessFileLoader
            loader = SourcelessFileLoader("_zbtools_entry", entry)
        else:
            from importlib.machinery import SourceFileLoader
            loader = SourceFileLoader("_zbtools_entry", entry)
        spec = importlib.util.spec_from_loader("_zbtools_entry", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
except Exception as exc:                                   # noqa: BLE001
    error = f"{type(exc).__name__}: {exc}"

print(json.dumps({"error": error, "entry": entry, "output": buf.getvalue()}))
'''


def check_entry(root: str, project: str) -> list[str]:
    """在临时副本里执行入口,确认 register() 成功、界面元素全部建成。

    两个入口都验:``init.py(c)``(自动加载)与 ``manual/Load ZBTools.py``(手动 Load)。
    """
    errors: list[str] = []
    entries = [name for name in ENTRY_NAMES if os.path.exists(os.path.join(root, name))]
    manual = "manual/Load ZBTools.py"
    if os.path.exists(os.path.join(root, manual)):
        entries.append(manual)
    if not entries:
        return ["找不到入口 init.py(c) / manual/Load ZBTools.py"]

    tests_dir = os.path.join(project, "tests")
    with tempfile.TemporaryDirectory(prefix="zbtools-verify-") as tmp:
        copy_root = os.path.join(tmp, "ZBTools")
        shutil.copytree(root, copy_root)
        for entry_rel in entries:
            errors += _run_entry(copy_root, tests_dir, entry_rel)
    return errors


def _run_entry(copy_root: str, tests_dir: str, entry_rel: str) -> list[str]:
    errors: list[str] = []
    completed = subprocess.run(
        [sys.executable, "-c", _RUNNER, copy_root, tests_dir, entry_rel],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if completed.returncode != 0:
        errors.append(f"[{entry_rel}] 验收子进程退出 {completed.returncode}:\n"
                      f"{completed.stderr[-1500:]}")
        return errors
    try:
        data: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:                                # noqa: BLE001
        errors.append(f"[{entry_rel}] 验收输出无法解析:{exc}\n{completed.stdout[-500:]}")
        return errors
    output = data.get("output") or ""
    if data.get("error"):
        errors.append(f"[{entry_rel}] 执行入口失败:{data['error']}")
    # 屏幕探测在验收环境里本来就不可用(没有 ZBrush 窗口),不算失败
    for line in output.splitlines():
        if "失败" not in line and "异常" not in line:
            continue
        if "探测" in line or "屏幕" in line:
            continue
        errors.append(f"[{entry_rel}] 装配失败:{line.strip()}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="验收 ZBTools 发行包")
    parser.add_argument("package", help="打包目录(例如 dist/ZBTools-0.2.0-macos)")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    root = os.path.abspath(args.package)
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(root):
        print(f"✗ 不是目录:{root}")
        return 1

    files = collect(root)
    info: dict[str, Any] = {}
    info_path = os.path.join(root, "BUILD-INFO.json")
    if os.path.exists(info_path):
        try:
            info = json.load(open(info_path, "r", encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            info = {}
    bytecode = bool(info.get("bytecode", any(rel.endswith(".pyc") for rel in files)))

    errors: list[str] = []
    errors += check_bytecode(root, files)
    errors += check_sources(files, bytecode)
    errors += check_manifest(root, files)
    errors += check_entry(root, project)

    summary = {
        "package": root,
        "version": info.get("version"),
        "platform": info.get("platform"),
        "features": info.get("features"),
        "files": len(files),
        "bytecode": bytecode,
        "errors": errors,
        "ok": not errors,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"验收 {root}")
        print(f"  · 版本 {info.get('version')} / 平台 {info.get('platform')} / "
              f"{len(files)} 个文件 / {'字节码' if bytecode else '源码'}发布")
        if errors:
            print("  ✗ 以下检查未通过:")
            for item in errors:
                print(f"    - {item}")
        else:
            print("  ✓ 字节码版本、源码泄漏、入口执行、清单一致性全部通过")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
