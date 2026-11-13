> 历史记录:0.2.1 已把配置移出包目录并统一命名,当前版本见 `RELEASE-0.2.1.md`。

# ZBTools 0.2.0 发行说明

- 构建时间:2026-11-04
- 目标:ZBrush 2026(Python SDK 2026.1,内嵌 CPython 3.11.9)
- 产物:
  - `dist/ZBTools-0.2.0-macos.zip`(全功能)
  - `dist/ZBTools-0.2.0-windows.zip`(无 `ruler`)
  - 两个同名目录为未压缩版本;`dist/_legacy/` 是旧的非版本化产物

## 这个"插件"是什么

ZBrush 的 Python 插件 = **一个目录**:入口 `init.py` + Python 包。ZBrush 启动时扫描
`PYTHONPATH` 各目录里的 `init.py` 并执行;面板由插件通过官方 `zbrush.commands` 动态创建。
它和传统 `.zsc`(ZScript,放 `ZStartup/ZPlugs64/`)是两套机制,本包**不**通过 ZPlugs64 加载。

## 本版包含

| 功能 | 显示名 | macOS | Windows |
|---|---|---|---|
| true_scale | 实际大小 | ✅ | ✅ |
| ruler | 屏幕真尺 | ✅ | ❌(AppKit 浮层) |
| meshtools | 网格修复 | 空占位 | 空占位 |
| tex2pp | 贴图转色 | ✅ | ✅ |
| depthimg | 图片转深度图 | ✅ | ✅ |

## 本版打包侧修正(相对旧 `dist/` 产物)

1. **入口改回 `init.py` 源码**:官方自动加载只扫描名为 `init.py` 的文件;旧的
   `init.pyc` 不会被扫描到,自动加载实际上不成立。内部模块仍是 `.pyc`。
2. **修正外部解释器默认值**:旧逻辑回落到 `sys.executable`,在 ZBrush 里那是 ZBrush 自身
   的可执行文件(官方文档明说),会被当成 Python 拉起来。现在是
   `ZBTOOLS_PYTHON` / `ZBRUSH_PYTHON` → 项目 `.venv` → `PATH` 的 `python3`/`python`,
   且只接受文件名含 `python` 的可执行文件;找不到就留空并提示手动配置。
3. **外部脚本一律发源码**(`tools/**`、`features/*/tools/**`):它们跑在用户配置的外部
   Python 上(3.9/3.13 都可能),`.pyc` 与解释器主版本绑定。旧的烘焙管线只发 `.pyc` 是错的。
4. **修 `--features` 子集打包**:`features/__init__.py` 原先把 `tex2pp` 写成硬导入,
   不含该功能的发行包会 import 失败;现在与其它功能一样 `try/except`。
5. Windows 包剔除 `ruler` 功能与其 AppKit 脚本;`ZPlugs64` 的错误安装说明改正。
6. 新增:`launch/` 一键启动器、`manual/Load ZBTools.py`(ZBrush 的 Load 对话框只认 `.py`)、
   `VERSION`、`BUILD-INFO.json`、`MANIFEST.sha256`、`THIRD-PARTY-NOTICES.md`。
7. 新增 `scripts/verify_package.py`,打包后自动验收:字节码 magic、源码泄漏、
   `init.py` 与手动加载器**用包内代码**真实装配面板、清单 sha256 一致。

## 验证记录

- `.venv/bin/python tests/run_all.py`:**全部通过**(架构约束 / true_scale / ui / ruler /
  tex2pp / depthimg / proc)
- 打包:`scripts/package_plugin.py` 两平台均通过 `verify_package.py`
- 压缩包:`unzip -t` 无错误;解压后再次验收通过
- 子集打包(`--features true_scale`)通过(验证第 4 条修正)
- **未验证**:Windows 包只在本机用假 `zbrush` 替身验收,没有在真实 Windows + ZBrush 上装过;
  macOS 包同样没有在真实 ZBrush 会话里点过每个按钮(激活 ZBrush 会打断当前工作)。
  首次真机使用时,建议先在一个可丢弃的工程里逐功能点一遍。

## 已知限制

- 新机器上要手动指定 `interpreter`(macOS 还需要 pyobjc)才能自动探测屏幕 / 用真尺 / 烘焙
- `depthimg` 权重不随包发布;只有 V2-S 可商用
- 内部 `.pyc` 只兼容 CPython 3.11(ZBrush 2026 内嵌版本)
- `meshtools` 是刻意的空占位
