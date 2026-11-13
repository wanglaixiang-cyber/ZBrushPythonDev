#!/bin/zsh
# 从终端启动带 ZBTools 插件的 ZBrush。
#
# 为什么需要它:macOS 上从 Dock/Finder 启动的 GUI 应用拿不到你 shell 里的环境变量,
# 所以 PYTHONPATH 方式自动加载插件必须从终端启动(或双击本 .command 文件)。

set -e

PROJECT="/Users/wanglaixiang/PycharmProjects/ZBrushPythonDev"
PLUGIN="$PROJECT/plugin/zbtools"

export PYTHONPATH="$PLUGIN:${PYTHONPATH}"
# 备选机制:该目录下所有 *.py 会在启动时执行
export ZBRUSH_PLUGIN_PATH="$PLUGIN"

echo "PYTHONPATH=$PYTHONPATH"
echo "启动 ZBrush ..."
exec "/Applications/Maxon ZBrush 2026/ZBrush.app/Contents/MacOS/ZBrush" "$@"
