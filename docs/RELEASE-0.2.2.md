# ZBTools 0.2.2 发行说明

- 构建时间:2026-11-04
- 目标:ZBrush 2026(Python SDK 2026.1,内嵌 CPython 3.11.9)
- **本轮只出 macOS 包**:`dist/ZBTools-0.2.2-macos.zip`(Windows 不动)

## 修复:macOS 启动器找不到 `ZBrush.app`

**症状**(来自真实安装现场):

```
/Users/.../ZBTools/launch/Launch ZBrush with ZBTools.command:9: no matches found:
/Applications/Maxon ZBrush *.app
找不到 ZBrush.app:请把本文件里的 PYTHONPATH 手动改成你的安装位置。
```

**根因**:启动器假设 app 直接叫 `/Applications/Maxon ZBrush 2026.app`,但官方安装器的实际布局是
**版本目录里再套 app**:

```
/Applications/Maxon ZBrush 2026/ZBrush.app
/Applications/Maxon ZBrush 2025/ZBrush.app     ← 多版本时两个都在
```

`/Applications/Maxon ZBrush *.app` 这个 glob 永远匹配不到;而且 zsh 在无匹配时会直接
`no matches found` 退出,`2>/dev/null` 也拦不住。错误提示又让人去改 `PYTHONPATH`,
方向完全不对(问题在找 ZBrush,不在找插件)。

**修法**(启动器现在会):

1. 优先用 `ZBRUSH_APP`(显式指定,可指向任意位置);
2. 否则用 `find /Applications "$HOME/Applications" -maxdepth 3 -name "ZBrush.app" -type d`,
   覆盖 `/Applications/Maxon ZBrush 2026/ZBrush.app`、`/Applications/ZBrush.app`、
   自建目录等情况;多个版本按路径倒序 → **优先新版**(2026 高于 2025);
3. 找到后校验 `Contents/MacOS/ZBrush` 可执行;
4. 找不到才报错,并给出正确的指定方式:
   `ZBRUSH_APP=/path/to/ZBrush.app`;
5. 启动前打印 `ZBTools:` 与 `ZBrush:` 两行实际路径,便于确认加载的是哪个目录/哪一版。

**未改**:插件本体、配置位置、面板与功能;Windows 包完全不动。

## 验证

- 在真机路径上跑发现逻辑:返回 `/Applications/Maxon ZBrush 2026/ZBrush.app`
  (同一台机器还有 2025,倒序优先 2026)
- `tests/run_all.py`:全部通过
- 打包 + `verify_package.py`:通过(字节码 magic、源码泄漏、两个入口、MANIFEST)
- zip `unzip -t` 无错误;解压后再验收通过

## 升级

- 配置仍在 `~/.zbtools/settings.json`,不受影响
- 若已装 0.2.1:关 ZBrush → 删掉旧 `ZBTools` 目录 → 解压 0.2.2(不要覆盖)
- 若配置还在旧包内(0.2.0),先复制到 `~/.zbtools/settings.json` 再删目录
