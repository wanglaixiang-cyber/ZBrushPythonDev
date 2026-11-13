> 0.2.2 修复了 macOS 启动器找不到 `ZBrush.app` 的问题,当前版本见 `RELEASE-0.2.2.md`。

# ZBTools 0.2.1 发行说明

- 构建时间:2026-11-04
- 目标:ZBrush 2026(Python SDK 2026.1,内嵌 CPython 3.11.9)
- **本轮只出 macOS 包**:`dist/ZBTools-0.2.1-macos.zip`(Windows 版暂不构建,
  0.2.0 的 Windows 包保留在 `dist/` 里不动)

## 这版改了什么

### 1. 配置移出插件目录(升级不再丢设置)

- 配置位置从 `<包>/zbtools/settings.json` 改为 **`~/.zbtools/settings.json`**
  (与 `~/.zbtools/models`、`~/.zbtools_rulers` 统一)。
- 原因:插件目录可能只读(Program Files、root 的 `/Applications`),且升级要整目录替换;
  配置跟着包走会写不进去或被覆盖。
- **自动迁移**:新位置还没有配置、而包内旧 `zbtools/settings.json` 存在时,首次启动会读旧文件
  并写新位置(旧文件保留,可手动删)。只对默认路径生效;`ZBTOOLS_CONFIG` 显式指定的路径不会被迁移。
  注意:迁移要求旧文件还在原位 —— 按“先删旧目录”流程升级时不会自动迁移,要先手动复制
  (见上面第 2 节的第 2 步)。
- 新增环境变量:`ZBTOOLS_HOME`(状态根目录,默认 `~/.zbtools`)、`ZBTOOLS_CONFIG`(配置全路径)。

### 2. 升级说明改成“先删目录,再解压”

旧说明让用户“整体覆盖”,但旧版本的 `.pyc`、已从新包剔除的功能目录会残留
(例如旧 `init.pyc`、Windows 上的 `ruler/`)。现在 `INSTALL.md` 明确要求:

1. 关 ZBrush
2. 从 0.2.0 升级的话,先把旧包里的 `zbtools/settings.json` 复制到 `~/.zbtools/settings.json`
3. **删除整个旧 `ZBTools` 目录**
4. 解压新包

### 3. 统一命名:不再有 `TrueScale` 残留

- 所有日志前缀 `[TrueScale]` → `[ZBTools]`(settings / screen / model / canvas / clipboard / true_scale)
- 删除 `TrueScaleError`(历史别名),统一用 `errors.PluginError`;
  `ui.guard` 捕获 `PluginError` 的行为不变
- 重写 `features/true_scale/README.md`:面板名、配置路径、加载方式、文件结构、测试命令
  全部改成当前事实(旧文里的 `plugin/TrueScale/`、`truescale/config.py`、
  `scripts/truescale_dev.py`、`tests/test_core.py` 都已不存在)
- 诊断标题从 `=== TrueScale 数据链 ===` 改为 `=== ZBTools 实际大小 数据链 ===`

## 验证记录

- `.venv/bin/python tests/run_all.py`:全部通过
  (架构约束 / true_scale / ui / ruler / tex2pp / depthimg / proc)
- `scripts/package_plugin.py --platform macos`:打包 + `verify_package.py` 通过
  (字节码 magic、源码泄漏、`init.py` 与 `manual/Load ZBTools.py` 两个入口、
  `MANIFEST.sha256`)
- zip `unzip -t` 无错误;解压后再验收通过

## 已知限制(不变)

- macOS 首次使用仍需指定一个能跑 pyobjc 的 `interpreter`(或用 `ZBTOOLS_PYTHON`),
  否则屏幕自动探测退回手动参数模式
- `depthimg` 权重不随包发布;只有 V2-S 可商用
- 内部 `.pyc` 只兼容 CPython 3.11(ZBrush 2026 内嵌版本)
- `meshtools` 仍是空占位
- Windows 包未在真实 Windows + ZBrush 上验证;本轮不构建,也不改动 0.2.0 的 Windows 产物
