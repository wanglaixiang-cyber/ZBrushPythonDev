# 安装 ZBTools(ZBrush Python 插件)

## 一、先弄清楚:ZBrush 插件是什么形式

ZBrush 有两套互不相干的插件机制,本包用的是**第二套**:

| | ZScript 插件 | **Python 插件(本包)** |
|---|---|---|
| 形态 | 一个 `.zsc`(编译过的 ZScript) | **一个目录**:入口 `init.py` + Python 包 |
| 安装位置 | ZBrush 的 `ZStartup/ZPlugs64/`(或 macOS `ZData/ZPlugs64/`) | **`PYTHONPATH` 里的目录** |
| 启动时发生什么 | ZBrush 加载 `.zsc`,往界面里加面板 | ZBrush 扫描 PYTHONPATH 每个目录里的 **`init.py`** 并执行 |
| 官方依据 | 传统 ZScript 插件 | Python SDK 文档 *Python Environment → PYTHONPATH* |

`ZBRUSH_PLUGIN_PATH` 是同一套的补充:它列的目录里**所有 `*.py`** 都会在启动时执行
(注意不是只执行 `init.py`;本包顶层只有 `init.py` 一个 `.py`,所以安全)。

> ⚠ **别把本包拷进 `ZPlugs64`**。那是 `.zsc` 插件目录,Python VM 不会主动去那里找
> `init.py`;旧版 INSTALL 写"拷进 ZPlugs64 就行"是错的。正确做法见下面三种方式。

本包目录结构(安装后长这样):

```
ZBTools/
├── init.py               ← 自动加载入口(ZBrush 只按这个名字扫描并执行)
├── zbtools/              ← 插件本体(内部模块,已编译为 .pyc)
├── tools/                ← 外部脚本(源码:显示器探测 / 屏幕真尺)
├── manual/
│   └── Load ZBTools.py   ← 手动加载用(在 ZBrush 里 Load 这一个文件)
├── launch/               ← 带插件启动 ZBrush 的启动器(双击即可)
├── README.md  INSTALL.md  THIRD-PARTY-NOTICES.md
└── VERSION  BUILD-INFO.json  MANIFEST.sha256
```

## 二、安装

先理解一件事:双击 `launch/Launch ZBrush with ZBTools.command` **只对这一次启动有效** ——
它把环境变量传给了那一个 ZBrush 子进程,进程一退就没了。所以"装完"和"每次都自动加载"
是两件事:想以后从 Dock/访达正常打开也有面板,必须先用**方式 A**启用一次自动加载。

### 方式 A:启用自动加载(推荐,一次搞定)

把整个 `ZBTools` 目录放在**不会乱动的位置**(例如 `~/Applications/ZBTools`;
放桌面/下载里迟早会被移动,搬动后要重跑本脚本),然后:

1. 双击 `launch/Enable Autoload.command`(首次被 Gatekeeper 拦就右键 → 打开)
2. 完全退出 ZBrush(`ZBrush` 菜单 → Quit,或 Cmd+Q;**关窗口不算退出进程**)
3. 从 Dock/访达正常启动 ZBrush —— 以后每次都会自动加载

它做的事(都在用户目录,不需要 sudo):

- 写 `~/.zbtools/pluginpath/init.py`:一个只指向本包的 shim(ZBrush 启动时执行它;
  这个 shim 目录里只有这一个 `.py`,不会误执行别的脚本)
- 写 `~/Library/LaunchAgents/com.zbtools.zbrush-env.plist`:登录时设置
  `ZBRUSH_PLUGIN_PATH`(只有 ZBrush 读这个变量,不影响别的 Python 应用)
- 立刻 `launchctl setenv` 一次,当前会话就生效

停用:`launch/Disable Autoload.command`。**搬动插件目录后要重跑 Enable**,否则 shim 还指旧路径。

### 方式 B:只启动这一次(临时,不装登录项)

双击 `launch/Launch ZBrush with ZBTools.command`。它只对本次子进程生效;
关掉 ZBrush 后再从 Dock 点原来的图标,插件不会加载 —— 这是 macOS GUI 应用不继承
shell 环境变量的必然结果,不是插件没装好。

### 方式 C:手动 Load(不想装任何东西)

ZBrush → 顶部 `ZScript` 面板 → `Python Scripting` 子面板 → `Load` →
选 `ZBTools/manual/Load ZBTools.py`。

- 立刻生效,不需要环境变量、不需要启动器
- **但 ZBrush 每次重启都要再 Load 一次**
- 如果你看到 "面板创建失败" 提示:先把右侧菜单栏里的 `ZBTools` 面板关掉再 Load

## 三、装完怎么确认

1. 右侧菜单栏出现 **ZBTools** 面板(可能要点一下面板名展开)
2. 底部控制台(ZScript → Script Window Mode → **Python Output**)里有类似:
   ```
   === ZBTools v0.2.4(状态版本 13)===
   [ZBTools] 功能「实际大小」(true_scale):
   ...
   ```
3. 任何一项显示"控件创建失败":把控制台里的报错整段复制出来再排查

## 四、依赖(只有"外部脚本"需要,插件本身零依赖)

ZBrush 侧代码只调用官方 `zbrush.commands`,**不需要装任何第三方库**。
但有些功能要把活交给 ZBrush 之外的 Python 进程,那些进程需要一个**独立解释器**:

| 用途 | 需要什么 | 缺了会怎样 |
|---|---|---|
| 显示器参数探测(实际大小) | macOS: 装有 **pyobjc**(Quartz/AppKit)的 Python;Windows: 任意 Python 3(用 stdlib 的 ctypes,无需第三方包) | 自动探测失败 → 退回手动参数模式(面板仍可用,但 ppi/尺寸是默认值,需用「核验显示比例」校准) |
| 屏幕真尺(仅 macOS) | 同一解释器 + pyobjc(要开透明浮层窗口) | 点「新建尺子」报错并提示解释器不可用 |
| 贴图转色(tex2pp) | `numpy trimesh rtree scipy pillow` | 点「烘焙并导回」提示缺哪个库 + 给安装命令 |
| 图片转深度图(depthimg) | 有 **torch** 的解释器 + Depth Anything V2 权重 | 提示装什么/下哪张权重;`luma`/`sdf` 引擎只要 numpy(+scipy) |

**怎么告诉插件用哪个解释器**(按优先级):

1. 环境变量 `ZBTOOLS_PYTHON`(或 `ZBRUSH_PYTHON`)—— 指向 `python.exe` / `bin/python`
2. 用户目录 `~/.zbtools/settings.json` 的 `"interpreter"`(首次运行后自动生成,可直接改)
3. 自动探测:项目 `.venv` → `PATH` 里的 `python3` / `python`

> ⚠ 自动探测**永远不会**把 ZBrush 自己的可执行文件当解释器(官方文档写明
> `sys.executable` 在 ZBrush 里指向 `ZBrush.exe` / `ZBrushMac.app`)。所以发行包里
> 这个值很可能是**空**的,而不是什么"ZBrush 自带解释器"。
> 发到新机器上时,建议第一次就设 `ZBTOOLS_PYTHON` 或填 `~/.zbtools/settings.json`。

macOS 上准备一个"能跑 pyobjc"的解释器(示例):

```bash
python3 -m venv ~/.zbtools-py
~/.zbtools-py/bin/pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa
# 贴图转色再装:
~/.zbtools-py/bin/pip install numpy trimesh rtree scipy pillow
# 然后:launchctl setenv ZBTOOLS_PYTHON ~/.zbtools-py/bin/python
```

Windows 上准备贴图转色用的解释器(示例):

```bat
py -3.11 -m venv %LOCALAPPDATA%\zbtools-py
%LOCALAPPDATA%\zbtools-py\Scripts\pip install numpy trimesh rtree scipy pillow
setx ZBTOOLS_PYTHON %LOCALAPPDATA%\zbtools-py\Scripts\python.exe
```

> **解释器版本与 `.pyc`**:包内 `tools/` 下的外部脚本是**源码**(不是 .pyc),
> 所以外部解释器可以是 3.9 / 3.13 等任意 3.x。插件内部模块是 CPython 3.11 字节码,
> 只跑在 ZBrush 内嵌 VM 里 —— 与外部解释器无关。

## 五、本包包含哪些功能(平台差异)

| 功能 | 显示名 | macOS | Windows |
|---|---|---|---|
| `true_scale` | 实际大小 | ✅ | ✅ |
| `ruler` | 屏幕真尺 | ✅ | ❌(浮层后端只有 AppKit 实现) |
| `meshtools` | 网格修复 | ✅(空占位:只有分区标题,无控件) | ✅(同左) |
| `tex2pp` | 贴图转色 | ✅ | ✅ |
| `depthimg` | 图片转深度图 | ✅ | ✅ |

面板大概是:

```
ZBTools
├── ---- 实际大小 ----        [显示实际大小] [核验显示比例] [诊断信息]
├── ---- 屏幕真尺 ----        [新建尺子] [删除尺子] [尺长 mm] [诊断信息]
├── ---- 网格修复 ----        (空)
├── ---- 贴图转色 ----        [① 记住低模] [② 烘焙并导回] [UV 翻转] [投射阈值] [诊断信息]
└── ---- 图片转深度图 ----    [转深度图] [替换模型贴图] [UV 翻转] [诊断信息] [V2-S][V2-B][V2-L][V2-G]
```

各功能的细节与实测结论在包内各功能目录的 `README.md`
(`zbtools/features/<key>/README.md`);本包不含 `docs/` 里的开发文档。

### 5.1 图片转深度图的权重(不随包发布)

`V2-S`(Small)是 **Apache-2.0 可商用**;`V2-B / V2-L / V2-G` 是 CC-BY-NC-4.0,
插件默认**拒绝**,只给自己用可把 `depthimg__allow_nc_weights` 设成 `true`。
权重不随包发布(体积),按需单独下:

```bash
mkdir -p ~/.zbtools/models && cd ~/.zbtools/models
# 只用 Small(可商用):
curl -L -o depth_anything_v2_vits_fp32.safetensors \
  https://hf-mirror.com/Kijai/DepthAnythingV2-safetensors/resolve/main/depth_anything_v2_vits_fp32.safetensors
```

装了 ComfyUI 的机器上,插件会自动去 `models/depthanything/` 找现成权重,
并复用 ComfyUI 那个带 torch 的 venv。

## 六、第一次使用的建议顺序

1. 选中一个模型 → **显示实际大小**(不对就点 **核验显示比例**,用尺子量画布边框宽度并填进去)
2. 换屏/改系统缩放到新屏幕后,再点一次「显示实际大小」(会自动重新探测)
3. 贴图转色:点中低模按 **① 记住低模** → 点中高模按 **② 烘焙并导回**
4. 图片转深度图:选中带贴图的模型 → 点 **转深度图**(默认替换模型贴图)
5. 任何功能出问题先点它的 **诊断信息**,里面列出了实际用的解释器 / 依赖 / 路径

## 七、发布形态与源码说明

- 插件内部模块(`zbtools/**`)是 **CPython 3.11 字节码**(`optimize=2`,已剥离
  docstring 与断言),只跑在 ZBrush 内嵌 VM 里。能挡住随手翻源码,但**挡不住反编译** ——
  需要更强保护请用 Cython / Nuitka 编译成原生扩展(必须按 CPython 3.11 / 64 位,
  并先在 ZBrush 里验证加载)。
- 入口 `init.py` **必须是源码**:官方自动加载机制只扫描名为 `init.py` 的文件
  (换成 `init.pyc` 不会被扫描到),所以它只有十几行 —— 真正的逻辑都在 `.pyc` 里。
- `tools/**`、`zbtools/features/*/tools/**` 与 `manual/Load ZBTools.py` 是**源码**,
  这是有意为之:它们由 ZBrush 之外的 Python(或 ZBrush 的 Load 按钮)执行,版本不一定
  是 3.11。
- 第三方组件与许可见 `THIRD-PARTY-NOTICES.md`;逐文件校验见 `MANIFEST.sha256`:
  ```bash
  cd ZBTools && shasum -a 256 -c MANIFEST.sha256     # macOS
  certutil -hashfile <file> SHA256                    # Windows 逐文件
  ```

## 八、更新与卸载

- **更新**:关掉 ZBrush → **删掉整个旧的 `ZBTools` 目录**(不要直接覆盖:旧版本的 `.pyc`、
  已从新包剔除的功能目录会残留,可能被当成新功能加载)→ 解压新包 → 用启动器/环境变量重新启动
- 用户配置在 **`~/.zbtools/settings.json`**,删插件目录不会动它
- **从 0.2.0 升级**:旧配置在旧包内的 `zbtools/settings.json`。如果调过参数,先把那个文件
  复制到 `~/.zbtools/settings.json`(目录不存在就 `mkdir -p ~/.zbtools`),再删旧目录
- **卸载**:先运行 `launch/Disable Autoload.command`(移除 LaunchAgent 与 shim),
  再删掉整个 `ZBTools` 目录;配置 `~/.zbtools/settings.json` 留不留都行

## 九、故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 双击启动器提示"找不到 ZBrush.app" | 启动器会自动找 `/Applications` 与 `~/Applications`;仍找不到就 `export ZBRUSH_APP=/你的/ZBrush.app` 后重跑 |
| 正常启动后没有 ZBTools 面板 | 先确认自己是**完全退出** ZBrush 再启动的;然后在终端跑 `launchctl getenv ZBRUSH_PLUGIN_PATH`,应为 `~/.zbtools/pluginpath`;不对就重跑 `launch/Enable Autoload.command`(搬过目录的话尤其要重跑) |
| 控制台说"面板创建失败" | 上一次注册的名字还没释放:关掉右侧 `ZBTools` 面板再 Load 一次,或重启 ZBrush |
| "探测解释器不存在" / 解释器为 `''` | 没设 `ZBTOOLS_PYTHON`;macOS 还需要 pyobjc。设置后重启 ZBrush |
| 屏幕真尺点了没反应 | 当前解释器不能跑 AppKit(macOS 必须有 pyobjc);看诊断信息 |
| 贴图转色提示缺库 | 按提示命令,往 `~/.zbtools/settings.json` 里那个 `interpreter` 装 |
| 深度图提示下载权重 | 见 5.1;四档里只有 V2-S 可商用 |
| 中文提示乱码 | 控制台编码问题;把 `~/.zbtools/settings.json` 的 `ascii_labels` 设成 `true` 可切纯 ASCII 面板标签 |
