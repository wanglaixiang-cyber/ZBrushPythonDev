# ZBTools —— ZBrush 2026 Python 插件

> **这是一个 vibecoding 项目**:代码与文档主要由 AI 辅助、按本机实测反馈反复迭代。
> 已经在 macOS + ZBrush 2026 上实际使用,但不保证所有版本/环境都稳定;
> 建议先在可丢弃的工程里试用,重要工作注意保存。
> 仓库只含插件源码 —— Maxon 官方 SDK 与安装包都不在这里。

ZBTools 在 ZBrush 右侧菜单栏里加一个面板,里面是几个互相独立的小功能:

| 功能 | 说明 | 平台 |
|---|---|---|
| **实际大小**(`true_scale`) | 选中模型按屏幕物理尺寸 1:1 显示,可核验校准 | macOS / Windows* |
| **屏幕真尺**(`ruler`) | 屏幕上贴一把真实刻度的尺子(可旋转、可多把) | 仅 macOS |
| **贴图转色**(`tex2pp`) | 低模的贴图经 UV 投射成高模的 polypaint,不用细分低模 | macOS / Windows* |
| **图片转深度图**(`depthimg`) | 选中模型的贴图 → 灰度深度图 → 送回 ZBrush;内置 DA V2 四档(仅 V2-S 可商用) | macOS / Windows* |
| **网格修复**(`meshtools`) | 空占位:面板只保留分区标题,暂无控件 | — |

\* Windows 产物未在真实 Windows + ZBrush 上验证;当前发布与实测以 macOS 为准。

## 安装(macOS)

发行包不在本仓库。把它解压到固定目录(例如 `~/Applications/ZBTools`),然后:

1. 双击 `launch/Enable Autoload.command`(首次被 Gatekeeper 拦就右键 → 打开);
2. **完全退出 ZBrush(Cmd+Q)**,再从 Dock/访达正常启动 **ZBrush 2026**。

之后每次启动都会自动加载;搬动插件目录后要重跑一次 Enable。
细节、依赖与排障见 [docs/INSTALL.md](docs/INSTALL.md)。

## 开发

ZBrush 2026 内嵌 CPython 3.11.9。

```bash
# 1) 自备官方 SDK:仓库不含,把 Maxon 的 ZBrush Python SDK 放到 sdk/
# 2) 建 .venv,并用 .pth 把 sdk/api 挂进解释器(见 docs/DEVELOPMENT.md)
# 3) 测试(不需要开 ZBrush)
.venv/bin/python tests/run_all.py
# 4) 热更新:ZBrush > ZScript > Python Scripting > Load > scripts/load_plugin.py
# 5) 打包 macOS 发行版(产物在 dist/,不进仓库)
.venv/bin/python scripts/package_plugin.py --platform macos
```

## 目录

```
plugin/zbtools/  插件本体(init.py + zbtools/ 包 + features/)
scripts/         开发、探针、打包脚本
tests/           假 ZBrush 替身与回归测试
docs/            安装、开发规范、API 索引、发行说明、实测笔记
```

## 文档

- [docs/INSTALL.md](docs/INSTALL.md) —— 安装、依赖、排障
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) —— 结构约定、加功能、打包
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) —— ZBrush Python API 索引
- [docs/RELEASE-0.2.4.md](docs/RELEASE-0.2.4.md) —— 当前版本说明

## 说明

- 插件内部模块以 `.pyc` 发布;入口 `init.py` 与外部工具脚本保持源码(原因见 `docs/DEVELOPMENT.md`)。
- `depthimg` 的 V2-B / V2-L / V2-G 权重是 **CC-BY-NC-4.0**,只适合自用;
  V2-S 是 Apache-2.0,可商用。
- 仓库不含 Maxon SDK、安装包、本地探针产物与环境文件。
