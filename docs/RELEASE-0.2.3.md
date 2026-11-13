# ZBTools 0.2.3 发行说明

- 构建时间:2026-11-04
- 目标:ZBrush 2026(Python SDK 2026.1,内嵌 CPython 3.11.9)
- **本轮只出 macOS 包**:`dist/ZBTools-0.2.3-macos.zip`(Windows 不动)

## 修复:关掉 ZBrush 再正常打开,插件就没了

**现象**:用 `launch/Launch ZBrush with ZBTools.command` 启动时有面板;
完全退出后从 Dock/访达正常打开 ZBrush,面板消失。

**根因**:这个启动器只把 `PYTHONPATH` 传给了**那一次**子进程,ZBrush 进程退出即失效。
macOS 的 GUI 应用不继承 shell 环境变量,所以"点原来的 ZBrush 图标"和"点启动器"
启动的根本不是同一个环境。之前把它当成"装好了"是误导。

**修法**:新增一次性安装脚本,把自动加载变成登录级持久设置:

- `launch/Enable Autoload.command`
  1. 写 `~/.zbtools/pluginpath/init.py` —— 只有它一个 `.py` 的 shim,指向当前插件目录;
  2. 写 `~/Library/LaunchAgents/com.zbtools.zbrush-env.plist` —— 登录时执行
     `launchctl setenv ZBRUSH_PLUGIN_PATH <shim>`(只有 ZBrush 读这个变量,不污染其它 Python);
  3. 立刻 `launchctl bootstrap` + `setenv` 一次,当前会话就生效;
  4. 启动前打印实际使用的插件路径与登录项路径。
- `launch/Disable Autoload.command`:移除登录项与 shim(不删插件、不删配置)。

之后**完全退出 ZBrush(Cmd+Q)再正常启动**,从 Dock/访达也能加载,重启电脑后由
LaunchAgent 自动重新设置。搬动插件目录后需要重跑 Enable(否则 shim 还指旧路径)。

保留 `launch/Launch ZBrush with ZBTools.command` 作为"只启动这一次"的临时方案。

**未改**:插件本体、面板、配置位置(`~/.zbtools/settings.json`)、功能逻辑。
**Windows**:未构建、未修改。

## 验证

- `Enable Autoload.command`:`zsh -n` 通过;实际运行后
  - `launchctl getenv ZBRUSH_PLUGIN_PATH` → `/Users/.../.zbtools/pluginpath`
  - `plutil -lint ~/Library/LaunchAgents/com.zbtools.zbrush-env.plist` → OK
  - `launchctl print gui/$UID/com.zbtools.zbrush-env` → `state = running`
  - shim `init.py` 内容指向包目录,且目录里只有这一个 `.py`
- `tests/run_all.py`:全部通过
- 打包 + `verify_package.py`、解压后再验收:通过

## 升级

0.2.1/0.2.2 → 0.2.3:删旧 `ZBTools` 目录、解压新包,再运行一次 `Enable Autoload.command`
(旧登录项还在,会指向旧 shim;重跑会覆盖)。配置不受影响。
