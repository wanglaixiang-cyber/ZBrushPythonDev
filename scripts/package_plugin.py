#!/usr/bin/env python3
"""把 ``plugin/zbtools`` 打包成**可安装的 ZBrush Python 插件发行包**。

产物(默认 macOS + Windows 两套)::

    dist/ZBTools-<版本>-<平台>/          ← 插件目录(可直接安装/拷走)
    dist/ZBTools-<版本>-<平台>.zip       ← 发给别人的那一份(zip 内顶层是 ZBTools/)

每套包含:插件代码、按平台裁掉的功能、``README.md`` / ``INSTALL.md`` /
``THIRD-PARTY-NOTICES.md`` / ``VERSION`` / ``BUILD-INFO.json`` / ``MANIFEST.sha256``,
以及 ``launch/`` 下一键带插件的启动器。

两条关键发布规则(踩过坑,别改回去):

* **内部模块只发 ``.pyc``**(optimize=2,不含 docstring),只跑在 ZBrush 内嵌的
  CPython 3.11.9 里。**入口必须叫 ``init.py`` 且必须是源码** —— 官方自动加载机制是
  "扫描 PYTHONPATH 里的 ``init.py``",而且只认这个文件名;改成 ``init.pyc`` 不会被
  扫描到(踩过,别省这十几行)。
* **``tools/`` 下的外部脚本必须发源码** —— 显示器探测 / 屏幕真尺 / 烘焙管线 /
  深度管线由用户配置的外部 Python 执行,**不是** 3.11 也常见(3.9、3.13…),
  而 ``.pyc`` 与解释器主版本绑定,发字节码等于装完就跑不起来。

用法::

    .venv/bin/python scripts/package_plugin.py                 # 双平台,全部功能
    .venv/bin/python scripts/package_plugin.py --platform macos
    .venv/bin/python scripts/package_plugin.py --features true_scale,tex2pp
    .venv/bin/python scripts/package_plugin.py --keep-source    # 调试用:保留 .py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import Any

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_SRC = os.path.join(PROJECT, "plugin", "zbtools")
DIST = os.path.join(PROJECT, "dist")
PKG_NAME = "ZBTools"

#: 功能 key -> 面板标题(只用于打印)
FEATURE_TITLES = {
    "true_scale": "实际大小",
    "ruler": "屏幕真尺",
    "meshtools": "网格修复",
    "tex2pp": "贴图转色",
    "depthimg": "图片转深度图",
}

#: 平台 -> 不随包发布的功能。Windows 没有屏幕真尺的浮层后端(AppKit),
#: 所以 Windows 包不带 ruler(详见 docs/INSTALL.md)。
PLATFORM_EXCLUDES = {"macos": set(), "windows": {"ruler"}}

#: 平台 -> 不随包发布的相对路径(除了功能级剔除之外)。
PLATFORM_FILE_EXCLUDES = {
    "macos": set(),
    # 屏幕真尺的两个外部脚本是 AppKit 实现,Windows 上跑不了也不需要
    "windows": {"tools/ruler_window.py", "tools/ruler_geometry.py"},
}

#: 这些位置**保持源码**:
#:   * ``init.py`` —— 官方只按名字找它(不能是 .pyc);
#:   * ``tools/`` 等 —— 由外部解释器/ZBrush 的 Load 按钮执行,版本不一定是 3.11;
#:   * ``manual/`` —— 手动加载器,要能被 ZBrush 的 Load 对话框选中。
SOURCE_KEPT_FILES = ("init.py",)
SOURCE_KEPT_PREFIXES = ("tools/", "manual/", "zbtools/features/tex2pp/tools/",
                        "zbtools/features/depthimg/tools/")

#: 不随包发布的文件
SKIP_NAMES = {".DS_Store", "settings.json"}
SKIP_DIRS = {"__pycache__"}


def read_version() -> str:
    path = os.path.join(PLUGIN_SRC, "zbtools", "__init__.py")
    with open(path, "r", encoding="utf-8") as handle:
        match = re.search(r'^VERSION\s*=\s*"([^"]+)"', handle.read(), re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def existing_features() -> list[str]:
    """按 FEATURE_TITLES 的顺序列出源码里真实存在的功能目录。"""
    root = os.path.join(PLUGIN_SRC, "zbtools", "features")
    present = {name for name in os.listdir(root)
               if os.path.isdir(os.path.join(root, name)) and not name.startswith("__")}
    return [key for key in FEATURE_TITLES if key in present]


def is_source_kept(rel: str) -> bool:
    """这个相对路径是否必须保持源码。"""
    normalized = rel.replace(os.sep, "/")
    return normalized in SOURCE_KEPT_FILES or any(
        normalized.startswith(prefix) for prefix in SOURCE_KEPT_PREFIXES)


def copy_tree(dest: str, features: set[str], excluded_files: set[str]) -> None:
    """拷插件源码到 dest,按 features 裁掉功能目录(另含平台/缓存过滤)。"""
    os.makedirs(dest, exist_ok=True)
    for root, dirs, files in os.walk(PLUGIN_SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = os.path.relpath(root, PLUGIN_SRC)
        target_root = dest if rel_root == "." else os.path.join(dest, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            if name in SKIP_NAMES or name.endswith(".pyc"):
                continue
            rel = name if rel_root == "." else f"{rel_root.replace(os.sep, '/')}/{name}"
            if rel in excluded_files:
                continue
            parts = rel.split("/")
            # zbtools/features/<key>/... 只保留选中的功能;features/__init__.py 必须留
            if (len(parts) >= 4 and parts[0] == "zbtools"
                    and parts[1] == "features" and parts[2] not in features):
                continue
            shutil.copy2(os.path.join(root, name), os.path.join(target_root, name))


def to_bytecode(root: str) -> tuple[int, int]:
    """编译内部模块为 .pyc 并删掉 .py;外部脚本保持源码。

    Returns:
        ``(编译数, 保留源码数)``
    """
    compiled = kept = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            source = os.path.join(current, name)
            rel = os.path.relpath(source, root)
            if is_source_kept(rel):
                kept += 1
                continue
            try:
                py_compile.compile(source, cfile=source + "c", doraise=True, optimize=2)
                os.unlink(source)
                compiled += 1
            except Exception as exc:                            # noqa: BLE001
                print(f"  ! 编译失败 {rel}:{exc}")
    for current, dirs, _files in os.walk(root, topdown=False):
        for name in list(dirs):
            if name in SKIP_DIRS:
                shutil.rmtree(os.path.join(current, name), ignore_errors=True)
    return compiled, kept


def write_launchers(dest: str, platform: str) -> list[str]:
    """生成带插件的 ZBrush 启动器(macOS 双击 .command / Windows .bat)。"""
    launch_dir = os.path.join(dest, "launch")
    os.makedirs(launch_dir, exist_ok=True)
    written: list[str] = []

    if platform == "macos":
        path = os.path.join(launch_dir, "Launch ZBrush with ZBTools.command")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "#!/bin/zsh\n"
                "# 双击本文件 = 带着 ZBTools 启动 ZBrush。\n"
                "# 为什么需要它:macOS 上从 Dock/访达启动的 GUI 应用拿不到你 shell 里的\n"
                "# 环境变量,官方自动加载机制(ZBrush 启动时扫描 PYTHONPATH 里的 init.py)\n"
                "# 就只有从终端继承环境的启动方式才能生效。\n"
                "set -e\n"
                'HERE="$(cd "$(dirname "$0")/.." && pwd)"\n'
                'export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"\n'
                'APP=""\n'
                '# 显式指定优先:ZBRUSH_APP=/path/to/ZBrush.app\n'
                'if [[ -n "${ZBRUSH_APP:-}" && -x "${ZBRUSH_APP}/Contents/MacOS/ZBrush" ]]; then\n'
                '  APP="$ZBRUSH_APP"\n'
                'fi\n'
                '# 官方安装器常见位置是 /Applications/Maxon ZBrush 2026/ZBrush.app\n'
                '# (版本目录里再套 .app),不是 /Applications/Maxon ZBrush 2026.app。\n'
                '# 装了多个版本时优先用排序靠后的新版;Space 不是问题(find 输出按行读)。\n'
                'if [ -z "$APP" ]; then\n'
                '  while IFS= read -r candidate; do\n'
                '    if [[ -x "$candidate/Contents/MacOS/ZBrush" ]]; then\n'
                '      APP="$candidate"\n'
                '      break\n'
                '    fi\n'
                '  done < <(find /Applications "$HOME/Applications" -maxdepth 3 \\\n'
                '               -name "ZBrush.app" -type d 2>/dev/null | sort -r)\n'
                'fi\n'
                'if [ -z "$APP" ]; then\n'
                '  echo "找不到 ZBrush.app(查过 /Applications 与 ~/Applications)。"\n'
                '  echo "可以指定:ZBRUSH_APP=/path/to/ZBrush.app 后再运行本文件"\n'
                '  read -r "?按回车退出"\n'
                '  exit 1\n'
                "fi\n"
                'echo "ZBTools: $HERE"\n'
                'echo "ZBrush:  $APP"\n'
                'exec "$APP/Contents/MacOS/ZBrush" "$@"\n'
            )
        os.chmod(path, 0o755)
        written.append(os.path.relpath(path, dest))

        # 一次性启用"正常启动也自动加载":LaunchAgent 在登录时设置 ZBRUSH_PLUGIN_PATH,
        # 这样从 Dock/访达启动的 ZBrush 也能继承环境(启动器只对那一次子进程有效)。
        enable = os.path.join(launch_dir, "Enable Autoload.command")
        with open(enable, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "#!/bin/zsh\n"
                "# 一次性运行:让 ZBrush 以后每次正常启动(含 Dock/访达)都自动加载 ZBTools。\n"
                "# 原理:macOS 的 GUI 应用不继承 shell 环境;用 LaunchAgent 在登录(及现在)\n"
                "# 设置 ZBRUSH_PLUGIN_PATH(只有 ZBrush 读这个变量),ZBrush 启动时执行\n"
                "# 该目录里的 init.py。这个 shim 目录里只有 init.py,不会误执行别的脚本。\n"
                "set -e\n"
                'HERE="$(cd "$(dirname "$0")/.." && pwd)"\n'
                'SHIM="$HOME/.zbtools/pluginpath"\n'
                'PLIST="$HOME/Library/LaunchAgents/com.zbtools.zbrush-env.plist"\n'
                'mkdir -p "$SHIM" "$(dirname "$PLIST")"\n'
                "\n"
                'cat > "$SHIM/init.py" <<PYEOF\n'
                '"""ZBrush 自动加载入口(由 ZBTools Enable Autoload 生成)。"""\n'
                "import sys\n"
                'PACKAGE = r"""$HERE"""\n'
                "if PACKAGE not in sys.path:\n"
                "    sys.path.insert(0, PACKAGE)\n"
                "from zbtools.app import register  # noqa: E402\n"
                "register()\n"
                "PYEOF\n"
                "\n"
                'cat > "$PLIST" <<PLISTEOF\n'
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                "<dict>\n"
                "  <key>Label</key><string>com.zbtools.zbrush-env</string>\n"
                "  <key>ProgramArguments</key>\n"
                "  <array>\n"
                "    <string>/bin/sh</string>\n"
                "    <string>-c</string>\n"
                "    <string>/bin/launchctl setenv ZBRUSH_PLUGIN_PATH &#39;$SHIM&#39;</string>\n"
                "  </array>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "</dict>\n"
                "</plist>\n"
                "PLISTEOF\n"
                "\n"
                "# 立即生效(不用等下一次登录);再 setenv 一次覆盖当前会话\n"
                '/bin/launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true\n'
                '/bin/launchctl bootstrap "gui/$(id -u)" "$PLIST"\n'
                '/bin/launchctl setenv ZBRUSH_PLUGIN_PATH "$SHIM"\n'
                "\n"
                'echo "已启用自动加载。"\n'
                'echo "  插件目录: $HERE"\n'
                'echo "  登录项:   $PLIST"\n'
                'echo "  环境变量: ZBRUSH_PLUGIN_PATH=$SHIM"\n'
                'echo "现在完全退出 ZBrush,再从 Dock/访达正常启动即可。搬动插件目录后要重跑本脚本。"\n'
                'read -r "?按回车关闭"\n'
            )
        os.chmod(enable, 0o755)
        written.append(os.path.relpath(enable, dest))

        disable = os.path.join(launch_dir, "Disable Autoload.command")
        with open(disable, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "#!/bin/zsh\n"
                "# 停用自动加载(不会删插件,只移除 LaunchAgent 与 shim)\n"
                "set -e\n"
                'PLIST="$HOME/Library/LaunchAgents/com.zbtools.zbrush-env.plist"\n'
                '/bin/launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true\n'
                '/bin/launchctl unsetenv ZBRUSH_PLUGIN_PATH 2>/dev/null || true\n'
                'rm -f "$PLIST"\n'
                'rm -rf "$HOME/.zbtools/pluginpath"\n'
                'echo "已停用自动加载(已经打开的 ZBrush 需要重启才会生效)。"\n'
                'read -r "?按回车关闭"\n'
            )
        os.chmod(disable, 0o755)
        written.append(os.path.relpath(disable, dest))

    if platform == "windows":
        path = os.path.join(launch_dir, "Launch ZBrush with ZBTools.bat")
        with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                "rem 双击本文件 = 带着 ZBTools 启动 ZBrush。\r\n"
                "rem 官方自动加载机制:ZBrush 启动时扫描 PYTHONPATH 里的 init.py。\r\n"
                "setlocal\r\n"
                'for %%I in ("%~dp0..") do set "ZBTOOLS_HOME=%%~fI"\r\n'
                'set "PYTHONPATH=%ZBTOOLS_HOME%;%PYTHONPATH%"\r\n'
                'set "ZBRUSH="\r\n'
                'for /d %%D in ("%ProgramFiles%\\Maxon ZBrush*") do (\r\n'
                '  if exist "%%D\\ZBrush.exe" set "ZBRUSH=%%D\\ZBrush.exe"\r\n'
                ")\r\n"
                "if not defined ZBRUSH (\r\n"
                '  if exist "%ProgramFiles%\\Maxon ZBrush 2026\\ZBrush.exe" '
                'set "ZBRUSH=%ProgramFiles%\\Maxon ZBrush 2026\\ZBrush.exe"\r\n'
                ")\r\n"
                "if not defined ZBRUSH (\r\n"
                "  echo 找不到 ZBrush.exe:请把本文件里的 PYTHONPATH 手动指向本目录后启动 ZBrush。\r\n"
                "  pause\r\n"
                "  exit /b 1\r\n"
                ")\r\n"
                'start "" "%ZBRUSH%"\r\n'
            )
        written.append(os.path.relpath(path, dest))

    return written


def write_manual_loader(dest: str) -> str:
    """写手动加载器(ZBrush 的 Load 对话框只认 .py,不一定列 .pyc)。"""
    path = os.path.join(dest, "manual", "Load ZBTools.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            '"""手动加载 ZBTools:在 ZBrush 里 Load 本文件即可(每次重启后都要再 Load 一次)。\n\n'
            "自动加载(以后不用手动):用 launch/ 下的启动器,或把包目录加进 PYTHONPATH。\n"
            '详见同目录上一级的 INSTALL.md。\n"""\n\n'
            "import os\n"
            "import sys\n\n"
            'HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
            "if HERE not in sys.path:\n"
            "    sys.path.insert(0, HERE)\n\n"
            "from zbtools.app import register  # noqa: E402\n\n"
            "register()\n"
        )
    return os.path.relpath(path, dest)


def copy_docs(dest: str) -> None:
    """随包带文档(发行版 README 覆盖源码 README)。"""
    mapping = {
        "PACKAGE_README.md": "README.md",
        "INSTALL.md": "INSTALL.md",
        "THIRD-PARTY-NOTICES.md": "THIRD-PARTY-NOTICES.md",
    }
    for source_name, target_name in mapping.items():
        source = os.path.join(PROJECT, "docs", source_name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(dest, target_name))
        else:
            print(f"  ! 缺少文档 docs/{source_name}(跳过)")


def write_build_info(dest: str, version: str, platform: str,
                     features: list[str], bytecode: bool) -> None:
    info: dict[str, Any] = {
        "name": PKG_NAME,
        "version": version,
        "platform": platform,
        "features": features,
        "bytecode": bytecode,
        "interpreter": f"CPython {sys.version.split()[0]}",
        "entry": "init.py",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "builder": "scripts/package_plugin.py",
    }
    with open(os.path.join(dest, "BUILD-INFO.json"), "w", encoding="utf-8") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with open(os.path.join(dest, "VERSION"), "w", encoding="utf-8") as handle:
        handle.write(f"{PKG_NAME} {version}\n"
                     f"platform: {platform}\n"
                     f"features: {', '.join(features)}\n"
                     f"entry: {info['entry']}\n"
                     f"built: {info['built']}\n")


def normalize_modes(root: str) -> None:
    """统一权限:普通文件 644、启动器可执行、目录 755(否则 zip 解出来是 600)。"""
    for current, dirs, files in os.walk(root):
        for name in dirs:
            os.chmod(os.path.join(current, name), 0o755)
        for name in files:
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            executable = rel.startswith("launch/") and not rel.endswith(".bat")
            os.chmod(full, 0o755 if executable else 0o644)


def write_manifest(dest: str) -> list[str]:
    """为包里每个文件写 sha256;返回参与清单的相对路径。"""
    files: list[str] = []
    for current, dirs, names in os.walk(dest):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name in {".DS_Store", "MANIFEST.sha256"}:
                continue
            files.append(os.path.relpath(os.path.join(current, name), dest).replace(os.sep, "/"))
    files.sort()
    lines = []
    for rel in files:
        with open(os.path.join(dest, rel), "rb") as handle:
            lines.append(f"{hashlib.sha256(handle.read()).hexdigest()}  {rel}")
    with open(os.path.join(dest, "MANIFEST.sha256"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return files


def make_zip(dest: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, dirs, names in os.walk(dest):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(names):
                if name == ".DS_Store":
                    continue
                full = os.path.join(current, name)
                rel = os.path.relpath(full, dest).replace(os.sep, "/")
                archive.write(full, f"{PKG_NAME}/{rel}")


def verify(dest: str) -> bool:
    script = os.path.join(PROJECT, "scripts", "verify_package.py")
    completed = subprocess.run([sys.executable, script, dest],
                               capture_output=True, text=True, timeout=600, check=False)
    print(completed.stdout.rstrip())
    if completed.returncode != 0:
        print(completed.stderr.rstrip())
    return completed.returncode == 0


def build_one(platform: str, features: list[str], version: str,
              keep_source: bool, do_zip: bool, do_verify: bool) -> int:
    excluded = PLATFORM_EXCLUDES[platform]
    dropped = [key for key in features if key in excluded]
    active = [key for key in features if key not in excluded]
    # 屏幕真尺不打包时,它那两个 AppKit 外部脚本也没有意义(免得包里留死代码)
    file_excludes = set(PLATFORM_FILE_EXCLUDES.get(platform, set()))
    if "ruler" not in active:
        file_excludes.update({"tools/ruler_window.py", "tools/ruler_geometry.py"})
    if dropped:
        print(f"  · {platform} 平台不支持 {dropped},已从本包剔除")

    directory = f"{PKG_NAME}-{version}-{platform}"
    dest = os.path.join(DIST, directory)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    print(f"→ 打包 {directory}:功能 {active}")

    copy_tree(dest, set(active), file_excludes)
    if keep_source:
        print("  · 保留源码(未编译,调试用)")
        compiled = kept = 0
    else:
        compiled, kept = to_bytecode(dest)
        print(f"  · 已编译 {compiled} 个内部模块 → .pyc(optimize=2),"
              f"{kept} 个外部脚本保留源码")
    write_launchers(dest, platform)
    write_manual_loader(dest)
    copy_docs(dest)
    write_build_info(dest, version, platform, active, bytecode=not keep_source)
    normalize_modes(dest)
    files = write_manifest(dest)

    total = sum(os.path.getsize(os.path.join(dest, rel)) for rel in files)
    sources = [rel for rel in files if rel.endswith(".py")]
    print(f"  · {len(files) + 1} 个文件,{total / 1024:.1f} KB")
    print(f"  · 源码:{len(sources)} 个外部脚本" +
          (f"({', '.join(sources[:4])}{' …' if len(sources) > 4 else ''})" if sources else ""))

    if do_verify and not verify(dest):
        print(f"✗ 验收未通过:{dest}(目录保留,便于排查,未生成 zip)")
        return 1

    if do_zip:
        zip_path = os.path.join(DIST, f"{directory}.zip")
        if os.path.exists(zip_path):
            os.unlink(zip_path)
        make_zip(dest, zip_path)
        print(f"  · 已生成 {os.path.relpath(zip_path, PROJECT)} "
              f"({os.path.getsize(zip_path) / 1024:.1f} KB)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 ZBTools ZBrush 插件发行版")
    parser.add_argument("--platform", choices=("macos", "windows", "all"), default="all")
    parser.add_argument("--features", default="all",
                        help="逗号分隔的功能 key,或 all(默认)")
    parser.add_argument("--version", default="", help="覆盖版本号(默认读 zbtools/__init__.py)")
    parser.add_argument("--keep-source", action="store_true", help="保留 .py(调试用)")
    parser.add_argument("--no-zip", action="store_true", help="只生成目录,不打 zip")
    parser.add_argument("--no-verify", action="store_true", help="跳过打包后验收(不建议)")
    args = parser.parse_args()

    version = args.version.strip() or read_version()
    present = existing_features()
    if args.features.strip() == "all":
        features = present
    else:
        features = [item.strip() for item in args.features.split(",") if item.strip()]
        unknown = [item for item in features if item not in present]
        if unknown:
            print(f"✗ 未知功能 {unknown};源码里现有 {present}")
            return 1
    if not features:
        print("✗ 没有选中任何功能")
        return 1

    platforms = ("macos", "windows") if args.platform == "all" else (args.platform,)
    print(f"ZBTools {version} · 平台 {', '.join(platforms)} · 功能 {features}")
    failed: list[str] = []
    for platform in platforms:
        print("\n" + "=" * 62)
        if build_one(platform, features, version,
                     args.keep_source, not args.no_zip, not args.no_verify):
            failed.append(platform)
    print("\n" + "=" * 62)
    if failed:
        print(f"✗ 失败:{', '.join(failed)}")
        return 1
    if args.no_zip:
        print(f"✓ 完成:dist/{PKG_NAME}-{version}-<平台>/")
    else:
        print(f"✓ 完成:dist/{PKG_NAME}-{version}-<平台>.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
