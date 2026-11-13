#!/bin/zsh
# 从终端启动带 ZBTools 插件的 ZBrush。
#
# 为什么需要它:macOS 上从 Dock/Finder 启动的 GUI 应用拿不到你 shell 里的环境变量,
# 所以 PYTHONPATH 方式自动加载插件必须从终端启动(或双击本 .command 文件)。

set -e

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN="$PROJECT/plugin/zbtools"

export PYTHONPATH="$PLUGIN${PYTHONPATH:+:$PYTHONPATH}"
# 只设 PYTHONPATH(ZBrush 启动时会扫描其中的 init.py)。
# 不要再设 ZBRUSH_PLUGIN_PATH 指同一目录:两个机制都会执行 init.py,
# 结果是同一次启动里 register() 跑两遍(面板复用能兜住,但日志/子面板会重复)。

echo "PYTHONPATH=$PYTHONPATH"
echo "启动 ZBrush ..."
ZBRUSH_APP="${ZBRUSH_APP:-/Applications/Maxon ZBrush 2026/ZBrush.app}"
if [[ ! -x "$ZBRUSH_APP/Contents/MacOS/ZBrush" ]]; then
  echo "找不到 ZBrush:请用 ZBRUSH_APP=/path/to/ZBrush.app 指定" >&2
  exit 1
fi
exec "$ZBRUSH_APP/Contents/MacOS/ZBrush" "$@"
