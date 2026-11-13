# ZBrush Python 开发项目 (2026.2.1 · SDK 2026.1)

基于 Maxon 官方 `ZBrush_Python_SDK_2026_1_0` 的 PyCharm 开发环境。

## 目录结构

```
ZBrushPythonDev/
├── .venv/              # Python 3.11.9(与 ZBrush 内嵌同版本);zbrush_sdk.pth 挂载 sdk/api
├── sdk/                # Maxon 官方 SDK 原件(只读引用:api 存根 / docs / examples)
├── plugin/zbtools/     # 插件本体
│   ├── init.py         #   ZBrush 入口(只做 sys.path + register)
│   └── zbtools/
│       ├── app.py      #   装配:建面板 → 遍历 features 注册
│       ├── ui.py       #   通用 UI + 提示(警告换底色)
│       ├── settings.py #   配置 + 状态版本
│       ├── notes.py errors.py
│       ├── zbapi/      #   共用:screen / model / canvas / clipboard
│       └── features/   #   功能:registry + true_scale/ + ruler/ + tex2pp/ + depthimg/ + meshtools/(空占位)
├── scripts/            # 开发脚本:load_plugin / probe_* / gen_api_reference / 启动器
├── tests/              # run_all / test_architecture / test_true_scale / test_ruler* / test_tex2pp / test_ui / fake_zbrush
├── _backup/            # 改动前的源码快照(tar.gz;不进版本库,作为额外回滚手段)
├── docs/               # DEVELOPMENT.md(规范) + API_REFERENCE.md(自动生成)
└── README.md
```

## 目录约定(重要)

| 目录 | 定位 | 能否修改 |
|---|---|---|
| `sdk/` | 官方 SDK 原件(`api` 类型存根 / `docs` / `examples`) | ❌ **只读引用**,整包替换式更新 |
| `plugin/` `scripts/` `tests/` `docs/` | 你自己的代码与文档 | ✅ 改这里 |
| `.venv/` | Python 3.11.9 环境 | 不进版本库(`.gitignore`) |

- SDK 的接入方式是 **venv 里的 `.pth` 文件**(`.venv/.../site-packages/zbrush_sdk.pth`,
  内容就是 `sdk/api` 的绝对路径)—— 不往 `sdk/` 里写任何东西
- `sdk/api` 是**类型存根**(函数体 `...`),真正的 `zbrush` 模块在 ZBrush 内嵌 VM 里;
  改存根对运行时毫无影响,只会让提示与真实 API 脱节
- 升级 SDK:直接整包覆盖 `sdk/`,没有需要合并的改动
- PyCharm 建议:右键 `sdk` → *Mark Directory as → **Library Root***
  (别选 Sources Root),这样全局搜索/重构不会翻 SDK 里那几百个文件
- `__pycache__/` 是本地 import 产生的副产品,已在 `.gitignore` 中排除

## API 索引(想都看看的话)

`docs/API_REFERENCE.md` —— 从本地 SDK 自动生成的**完整函数索引**(145 个):
按官方分类(Gui 68 / Modeling 38 / System 23)+ 每条的签名与说明。
重新生成:`.venv/bin/python scripts/gen_api_reference.py`

`docs/MEASURING.md` —— ZBrush 原生量测能力调研:TransPose 动作线(= 原生尺子)的参数、
吸附(`Snap Max distance`)、Gizmo 3D 与 Y 键的真实含义、以及 `canvas_click` / `get_transpose` 等可编程接口。

速查:

```python
import zbrush.commands as zbc            # 主模块
import zbrush.utils as zbu               # run_path / run_script / clear_output
import zbrush.zscript_compatibility as zbz
```

- 每个函数都带示例片段:`sdk/examples/fragments/`
- 官方 HTML 文档(可搜索):`sdk/docs/index.html`(用浏览器打开)
- 在线最新版:https://developers.maxon.net/docs/zbrush/py

## 插件与功能

- 插件本体:`plugin/zbtools/`(`README` 见其中);当前功能:
  - `true_scale`(显示名「实际大小」)
  - `ruler`(显示名「屏幕真尺」:屏幕上的物理真尺,1 mm 就是 1 mm)
  - `tex2pp`(显示名「贴图转色」:低模的贴图经 UV **投射成高模的 polypaint** ——
    不用细分低模、不用给高模做 UV;原理/实测结论/排障见
    `plugin/zbtools/zbtools/features/tex2pp/README.md`)
  - `depthimg`(显示名「图片转深度图」:读**选中模型的贴图** → 灰度深度图 → 送回 ZBrush
    (默认替换模型贴图);**面板那四格是 Depth Anything V2 的四档规模**(V2-S/B/L/G,
    只有 S 可商用,其余要开 `allow_nc_weights`);`da3` / `luma`(亮度) / `sdf`(浮雕)
    三个引擎仍在配置里可切;与①②无关,见
    `plugin/zbtools/zbtools/features/depthimg/README.md`)
  - `meshtools`(显示名「网格修复」:**功能已清空,只剩分区占位** —— 面板上仍显示这一区,
    里面没有控件;历史实现与设计见 `docs/DESIGN_MESHTOOLS.md`,源码备份见 `_backup/`)
- 开发规范与结构说明:**`docs/DEVELOPMENT.md`**(依赖方向、命名、怎么加新功能)
- 功能说明:`plugin/zbtools/zbtools/features/true_scale/README.md`、
  `plugin/zbtools/zbtools/features/ruler/README.md`、
  `plugin/zbtools/zbtools/features/meshtools/README.md`

加载(热更新):
```
ZBrush > ZScript 面板 > Python Scripting 子面板 > Load > scripts/load_plugin.py
```

打包发行版(可发给别人安装的正式包):
```bash
.venv/bin/python scripts/package_plugin.py                 # 双平台,全部功能
.venv/bin/python scripts/package_plugin.py --platform macos
.venv/bin/python scripts/package_plugin.py --features true_scale,tex2pp
```
产物在 `dist/ZBTools-<版本>-<平台>/` 与同名 `.zip`,每个包都会自动跑
`scripts/verify_package.py`(校验字节码版本、源码泄漏、`init.py(c)` 与
`manual/Load ZBTools.py` 两个入口能真实装配面板、`MANIFEST.sha256` 一致)。
安装方式(ZBrush 的 Python 插件 = 目录 + `init.py`,靠 `PYTHONPATH` 扫描):
包的 `launch/` 里带一键启动器,细节见包内 `INSTALL.md` / `docs/INSTALL.md`。

测试(不需要开 ZBrush):
```bash
.venv/bin/python tests/run_all.py     # 架构约束 + 逻辑 + 交互
```

## 环境说明

- **解释器**:`.venv`(CPython 3.11.9,与 ZBrush 2026.2 内嵌 VM 完全一致;
  来源 `/Library/Frameworks/Python.framework/Versions/3.11`)
- **存根接入**:`sdk/api` 通过 `site-packages/zbrush_sdk.pth` 挂进 venv,
  项目内任意位置 `import zbrush.commands` 都能获得补全与类型提示。
- **重要概念**:`sdk/api` 里是官方**类型存根**(函数体都是 `...`),
  真正的 `zbrush` 模块只存在于 ZBrush 内嵌 VM 里。
  所以:本地 = 编辑/补全/静态检查;运行 = 必须在 ZBrush 内。

## 如何运行脚本

### 方式 A:ZBrush 界面内(推荐)
1. 打开 ZBrush
2. 顶部 `ZScript` 面板 → `Python Scripting` 子面板 → `Load` → 选择脚本
3. 查看结果:窗口底部打开 Python 控制台输出(`ZScript` → `Script Window Mode` 子面板切到 Python Output)

也可以在 ZScript 面板的 Python 控制台里逐行敲代码测试。

### 方式 B:命令行启动参数(启动即跑)
```bash
# 先退出已运行的 ZBrush,再执行:
"/Applications/Maxon ZBrush 2026/ZBrush.app/Contents/MacOS/ZBrush" -script \
  "/Users/wanglaixiang/PycharmProjects/ZBrushPythonDev/scripts/hello_zbrush.py"
```
> ⚠️ 官方警告:若命令行里还要带 `.ZPR` 工程文件,它必须是**最后一个参数**(不是第一个)。

### 官方示例
```bash
# 把示例换成 sdk/examples/ 里的任意 .py 即可,例如:
#   sdk/examples/ex_hello_world.py
#   sdk/examples/gui/ex_gui_palette.py
#   sdk/examples/modeling/ex_mod_subtool_array.py
```

## PyCharm 配置(已就绪项)

- [x] venv 解释器创建(`.venv`,PyCharm 打开后会自动识别为项目解释器)
- [x] `import zbrush` 补全可用(经 .pth 挂载)
- [ ] 可选:Settings → Project Structure → 把 `sdk/api` 标为 **Sources Root**(更直观)
- [ ] 可选:Settings → Editor → Inspections → 开启 Python 类型检查告警

## 注意事项(官方文档要点)

- ZBrush Python VM 是**持久进程**:全局变量、导入的模块会在多次脚本执行间残留,
  写插件时注意隔离(用 `main()` 函数 + `if __name__ == "__main__"` 是官方推荐风格)。
- **不支持** `multiprocessing`;`subprocess` 不能拿来再启动 ZBrush 自身解释器。
- 第三方库大部分能用但**官方不提供支持**(出问题找包作者/社区)。
- 脚本所在目录**不会**自动进 `sys.path`;需要自己的模块时用 `PYTHONPATH` 或
  `ZBRUSH_PLUGIN_PATH`(启动时扫描目录内全部 `*.py`)。
- Python 目前是 ZScript 命令流的包装层,**不能直接操作 ZBrush 内部数据**。

## 官方资料链接

- 在线文档(最新版):https://developers.maxon.net/docs/zbrush/py
- 官方示例仓库:https://github.com/Maxon-Computer/ZBrush-Python-API-Examples
- SDK 下载入口:https://developers.maxon.net/forum/topic/15246/zbrush-sdk-overview
- 离线文档:本项目的 `sdk/docs/` 或应用内
  `/Applications/Maxon ZBrush 2026/Documentation/python-api/`