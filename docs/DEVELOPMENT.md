# 开发规范与结构

## 一、目录约定

| 目录 | 定位 | 能否改 |
|---|---|---|
| `sdk/` | Maxon 官方 SDK 原件(类型存根 / 文档 / 示例) | ❌ 只读引用,整包替换式更新 |
| `plugin/zbtools/` | 插件本体(要发布的东西) | ✅ |
| `scripts/` | 开发脚本(加载器 / 探针 / 生成器 / 启动器) | ✅ |
| `tests/` | 测试(假 zbc 替身,不需要开 ZBrush) | ✅ |
| `docs/` | 项目文档 + 自动生成的 API 索引 | ✅ |
| `.venv/` | Python 3.11.9 环境(`zbrush_sdk.pth` 挂载 `sdk/api`) | 不进版本库 |

## 二、插件内部结构

```
plugin/zbtools/
├── init.py                    # ZBrush 入口:只做 sys.path + register()
├── tools/                     # ZBrush 之外的辅助子进程(标准库/ctypes/tkinter,尽量零依赖)
│   ├── display_info.py        #   显示器探测(CoreGraphics + EDID)
│   ├── ruler_window.py        #   屏幕真尺浮层窗口(AppKit,可旋转 + 点击穿透)
│   └── ruler_geometry.py      #   尺子几何(纯函数:命中判断/包围盒/角度量化)
└── zbtools/
    ├── app.py                 # 装配:建面板 → 遍历 features 注册(只装配,无业务)
    ├── ui.py                  # 通用 UI:Panel(建控件)/ note(提示)/ guard(错误兜底)
    ├── settings.py            # 配置 + 状态版本(插件级)
    ├── notes.py / errors.py   # 提示样式 / 统一异常(叶子模块,不依赖任何东西)
    ├── proc.py                #   跑外部子进程 + 边跑边回调(进度条靠它;只用标准库)
    ├── zbapi/                 # 对 ZBrush 的薄封装(共用,不含业务)
    │   ├── screen.py          #   显示器探测(EDID)/ ppi / 桥接系数
    │   ├── model.py           #   模型尺寸:网格尺寸 × Tool:Export:Scale、包围盒
    │   ├── canvas.py          #   画布尺寸 / transform / 视角记忆
    │   └── clipboard.py       #   剪贴板(pbcopy)
    └── features/              # ← 一个功能一个包
        ├── __init__.py        #   FEATURES 注册表
        ├── ruler/             #   屏幕真尺(示范:多子进程 + 纯函数几何 + 诊断)
        └── <feature_key>/
            ├── __init__.py    #   FEATURE(key/title/build) + LABELS + paths()
            ├── service.py     #   业务逻辑
            ├── tools/         #   该功能自带的外部脚本(可选)
            └── README.md      #   该功能的说明(可选)
```

## 三、依赖方向(单向,不许回头)

```
init.py → app.py → features/* → ui.py / zbapi/* / settings.py → notes.py / errors.py
```

- **功能之间绝不互相 import**:要复用就往上提,放进 `zbapi/` 或 `ui.py`
- `zbapi/*` 只包 ZBrush 接口,**不含业务判断**(业务在 feature 的 `service.py`)
- `app.py` 只装配;入口 `init.py` 只调 `register()`
- 这三条由 `tests/test_architecture.py` 自动检查,违反了直接测试失败

**判断"共用还是私有"的一条准则**:两个以上功能需要 → 提到 `zbapi/` 或 `ui.py`;只有一个功能需要 → 留在该功能包里。
现在只有一个功能,所以 `screen/model/canvas/clipboard` 已经按"以后一定共用"提前放在了 `zbapi/`。

## 四、命名与身份

| 概念 | 约定 | 例子 |
|---|---|---|
| 插件名 | `zbtools`(包名,稳定,少改) | `plugin/zbtools/` |
| 插件显示名 | `zbtools/__init__.py: PLUGIN_TITLE`(随便改) | `"ZBTools"` |
| 功能 key | 包名,**稳定**,用于配置前缀/诊断 | `true_scale` |
| 功能显示名 | `FEATURE.title`(随便改,面板上就是它) | `"实际大小"` |
| 配置键 | 插件级放 `settings.py: DEFAULTS`;功能级加前缀 `<key>__` | `true_scale__center_model` |

> 显示名可以随便改(`PLUGIN_TITLE` / `FEATURE.title`),`key` 与 import 路径保持稳定。

## 五、加一个新功能(照抄这 4 步)

1. 复制结构:`plugin/zbtools/zbtools/features/my_feature/{__init__.py, service.py}`
2. `__init__.py` 里给 `FEATURE = Feature(key="my_feature", title="我的功能", build=build)`
3. `build(panel, cfg)` 里用 `panel.button/switch/slider` 挂控件(回调里用 `ui.guard` 兜底)
4. 在 `features/__init__.py` 的 `FEATURES = [...]` 里登记
5. 跑 `python tests/run_all.py`(架构测试会检查依赖方向与 FEATURE 契约)

## 六、编码约定

- **item path**:每段都不能含 ASCII 冒号(ZBrush 用冒号分层级;`Panel.path()` 会直接报错拦住),中文可用
- **控件创建必须校验返回值**:`Panel` 已统一校验,失败会汇总打印 —— 静默失败就是"面板空白"的事故
- **每个控件都要给 `width=1.0`**:`width` 是 |0,1] 的相对宽度(API 文档写"像素"是错的),
  显式写 1.0 = 占满一行。官方 GUI 示例明确建议这么做,**不要交给 ZBrush 自动排版** ——
  实测不写时会重叠(踩过:`tex2pp` 的 slider/switch/按钮挤在一起)
- **悬停提示(bubble help,参数名 `info`)≤ `ui.INFO_MAX`(40 字),且不许带 `\n`/`\t`**:
  气泡只有一行,而官方明文警告 ZBrush 的 Python 字符串渲染不了反斜杠,**连 `\n`/`\t` 都受影响**
  (`sdk/docs/partials/api_zbrush_string_bug_backslash.html`,双转义 / raw string 都没用)
  —— 所以"写长点靠换行"走不通,只能写短。官方示例 33 处 `info` 中位 19 字、最长 37 字。
  写法:一句"这一格是什么 + 一个必须当场知道的坑";**实测理由/原理写该功能 README**,
  参数枚举交给【诊断信息】。⚠ 本 SDK **没有 `set_info`**,提示只能在创建控件时给一次
  (改完热重载会重建子面板,所以照样生效)。超长不是硬失败:`ui.py` 会打印告警,
  `tests/test_architecture.py::test_tooltip_budget` 会直接红
- **提示**:普通信息用 `ui.note`;警告以 `警告:` 开头会自动换底色并延长停留时间
- **异常**:业务用 `errors.PluginError`(会弹给用户);其他异常由 `ui.guard` 兜底并打印堆栈
- **长等待可以给进度**(可选,不是硬要求):共用件是 `ui.Progress`(阶段跳变 + 阶段之间
  按时间爬升,受 ceiling 限制、不假报进度)与 `proc.stream`(逐行读子进程输出,顺带让日志
  变实时)。`proc.unbuffered()` 给解释器加 `-u` —— 不加的话管道是块缓冲,进度行会攒到最后
  一次性冒出来。真机实测与结论在 `scripts/probe_notebar.py`
- **配置/状态**:一律经 `settings.load()/save()`;状态改了要升 `STATE_VERSION`(会自动清旧记账)。
  `load()` 保留插件级键与 **`<feature>__` 前缀的功能级键**,其余未知键丢掉 ——
  所以功能配置能熬过重启,但也**必须**用 `settings.feature_prefix(key)` 那套前缀(实测踩过:
  没前缀的键会被静默丢掉)
- **注释写"为什么"**:代码里已有大量实测结论(如 `Export:Scale` 不影响渲染、ZBrush 会截断画布 scale),这些注释比代码本身更值钱
- **测试**:纯逻辑用 `tests/fake_zbrush.py` 的替身跑(不开 ZBrush);新增行为必须加断言,回归 bug 要留一条

## 七、发行(打包)

```bash
.venv/bin/python scripts/package_plugin.py                 # 双平台 + 全部功能(默认)
.venv/bin/python scripts/package_plugin.py --platform macos
.venv/bin/python scripts/package_plugin.py --features true_scale,tex2pp
.venv/bin/python scripts/package_plugin.py --keep-source   # 调试:全部保留 .py
.venv/bin/python scripts/verify_package.py dist/ZBTools-0.2.4-macos   # 单独验收
```

产物:`dist/ZBTools-<版本>-<平台>/` + 同名 `.zip`;版本读 `zbtools/__init__.py` 的 `VERSION`。
每个包都带 `VERSION` / `BUILD-INFO.json` / `MANIFEST.sha256` / `README.md` / `INSTALL.md` /
`THIRD-PARTY-NOTICES.md`,以及 `launch/` 一键启动器。打包脚本最后会自动跑验收。

约定:

* **内部模块只发 `.pyc`**(`optimize=2`),但 **`init.py` 必须是源码** —— ZBrush 的自动加载
  只扫描这个文件名(换成 `init.pyc` 不会被执行);它只做 `sys.path` + `register()`,没有逻辑
* **`tools/**`、`features/*/tools/**`、`manual/` 必须是源码**:它们跑在外部解释器上
  (显示器探测 / 屏幕真尺 / 烘焙 / 深度管线),解释器版本不一定是 3.11;`.pyc` 与版本绑定,
  发字节码等于装完就跑不起来。`bake_pipeline.py` 也一样(不要按"打包时的解释器"假设用户会用同版本)
* 功能目录可缺省:`features/__init__.py` 逐个 `try` 导入 —— `ruler/` 不打包时插件照常启动;
  Windows 包默认剔除 `ruler`(浮层后端是 AppKit)
* **跨功能共用**的 ZBrush 操作放 `zbapi/`:`toolio.py` 是「导出 SubTool / 找模型带出的贴图 /
  按界面项 / 导入贴图 / 挂到 SubTool」——`tex2pp` 与 `depthimg` 都用它(功能之间不互相 import)
* **外部解释器**的默认值 = `ZBTOOLS_PYTHON` / `ZBRUSH_PYTHON` 环境变量 → 项目 `.venv` →
  `PATH` 里的 `python3`/`python`;**永不回落到 `sys.executable`** —— 在 ZBrush 内嵌 VM 里它指向
  ZBrush 自己的可执行文件(官方文档明说),当解释器只会再启动一个 ZBrush。
  外部脚本只吃 argv、只打 stdout、可单独运行(零耦合)
* 未在真机验证过的平台代码,必须在 `INSTALL.md` 里标出来(别把"应该能跑"写成"已验证")
* 验收(`scripts/verify_package.py`)四项:字节码 magic 与 3.11 一致、无源码泄漏、
  `init.py` 与 `manual/Load ZBTools.py` 两个入口能用**包内代码**真正装配面板、清单 sha256 一致

## 八、文档索引

| 文档 | 内容 |
|---|---|
| `README.md`(根) | 项目总览、SDK 说明、快速上手 |
| `docs/DEVELOPMENT.md` | 本文件:结构、依赖方向、命名、加功能步骤、发行 |
| `docs/INSTALL.md` | 面向用户的安装说明(随发行包分发) |
| `docs/API_REFERENCE.md` | 自动生成的官方 API 索引(`scripts/gen_api_reference.py`) |
| `docs/MEASURING.md` | ZBrush 原生量测能力调研(动作线/Gizmo/浮层真尺的取舍与结论) |
| `docs/DESIGN_MESHTOOLS.md` | 网格修复模块详细设计 —— **已归档**:功能代码已清空(只剩分区占位),本文档只作设计参考 |
| `plugin/zbtools/README.md` | 插件本体说明 |
| `features/*/README.md` | 单个功能的原理、用法、限制 |

## 九、常用命令

```bash
.venv/bin/python tests/run_all.py                  # 全部测试(架构 + 逻辑 + 交互)
.venv/bin/python scripts/gen_api_reference.py      # 重新生成 docs/API_REFERENCE.md
./scripts/launch_zbrush_with_plugin.command        # 带 PYTHONPATH 启动 ZBrush
```
ZBrush 内热更新:`ZScript > Python Scripting > Load > scripts/load_plugin.py`

热更新为什么是"复用顶层面板 + 重建子面板"(`ui.ensure_palette`):
ZBrush 界面操作进命令队列,`close` 掉脚本创建的 palette 后**名字不会立刻释放**,
同名 `add_palette` 会返回 False;`delete_interface_item` 官方说明对 palette / sub-palette
无效。所以"关掉顶层再重建"在同一会话里行不通(只能重启),改用复用即可。
副作用:功能**显示名**改了要重启才能清掉旧分区。

在 ZBrush 里跑的探针脚本(同样是 Load 后按屏幕提示操作,结果自动进剪贴板):

| 脚本 | 用途 |
|---|---|
| `scripts/probe_item_paths.py` | 验证界面 item path 写法(路径含冒号会显示空白条) |
| `scripts/probe_measure.py` | 量测可行性:canvas_click 程序化落点、动作线长度单位、吸附开关、鼠标能力 |
| `scripts/verify_ruler_overlay.py` | 截屏核验真尺(差分法):实测角度 + 刻度周期 vs 理论值,需 `--pid` |
| `scripts/probe_goz_polypaint.py` | 贴图→polypaint 通道调研:导出 .goz/.obj 并报颜色块位置(结论:导出模板不含 MRGB 段) |
| `scripts/probe_color_channel.py` | 顶点色通道实测:导入三份对照文件(OBJ+#MRGB / OBJ+顶点色 / PLY),结论是**只有 PLY 认** |
| `scripts/export_active_subtool.py` / `scripts/import_ply_and_check.py` | 贴图转色的**手动版**(导出当前 SubTool / 导入 PLY 并核对尺度与点数)。日常请用面板上的①②,这两个只在排障时用 |
| `scripts/probe_texture_paths.py` | 图片转深度图专用:**ZBrush 的 Python SDK 没有任何贴图 API**。这个探针逐个 `exists()` 报告候选路径、真的导入一张测试图、**读贴图槽的 title/tooltip**(实测能读出贴图名与尺寸!)、并验证"不导出网格直接导贴图"的快路径(与 `Tool:Export` 带出的那张比 sha256)。第一轮结果已落进 `zbapi/toolio.py` 的候选表与注释 |
| `_probe/depthprobe/` | 深度引擎的一次性验证(MPS 耗时/画质、vendored 与 ComfyUI 版逐像素对比、图案距离场对比、DA3 独立 venv 安装脚本)——不在 `scripts/` 里,因为它们是"查资料用的",不是天天跑的探针 |
| `scripts/hello_zbrush.py` | 最小可用脚本(确认 Python VM 与环境变量通了) |

## 十、现有功能(作为写法参考)

| key | 显示名 | 类型 | 参考价值 |
|---|---|---|---|
| `true_scale` | 实际大小 | 改模型画布变换 + 提示 | 复杂业务、读写 transform、诊断、记账状态 |
| `ruler` | 屏幕真尺 | 多把外部浮层窗口(AppKit 子进程,可旋转/翻面),不碰模型 | 换算 + 子进程生命周期 + 纯函数几何 + 差分核验脚本 |
| `meshtools` | 网格修复 | **空存根**(功能已整体清空) | 分区占位的写法:面板上仍显示这一区,`build` 为空 —— 历史实现与设计见 `docs/DESIGN_MESHTOOLS.md` |
| `tex2pp` | 贴图转色 | 外部管线子进程(trimesh 最近点 + 重心坐标插值 UV + 采样贴图)→ PLY 导回 | 两步式面板(①记住低模 / ②烘焙并导回,都是按钮)、功能自带 `tools/` 子进程、外部格式契约(PLY **逐面交错**)、算法与**暴力参考实现**对照测试 |
| `depthimg` | 图片转深度图 | 外部管线子进程(深度估计)→ 导入 ZBrush Texture 面板(可选挂到 SubTool) | 一个按钮 + 两个开关 + **DA V2 四档规模单选组**、**许可判定**(只有 Small 可商用,NC 需显式放开,有测试钉死)、**"面板选规模 ⇒ 规模对不上要报错"**(不静默降级)、**vendored** 上游模型定义(Apache-2.0,附 LICENSE/PATCHES.md)、ZBrush 侧"没有贴图 API → 候选路径 + `exists()` 探测"的写法、外部管线的**发行形态**(带源码) |

`ruler` 只有 ~100 行,新功能可以照它起步。
