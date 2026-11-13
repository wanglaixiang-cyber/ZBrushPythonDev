# 实际大小(true_scale)—— ZBrush 按屏幕物理尺寸显示模型

让选中的 SubTool 在屏幕上以**真实物理尺寸**显示(1:1):
Scale Master 报 78.76mm 的模型,屏幕上量出来就是 78.76mm。

不改视角、不动透视、**只读不改你的模型**。这是 `ZBTools` 插件里的一个功能,
面板上的显示名是「实际大小」,功能 key(配置前缀/诊断用)是 `true_scale`。

## 面板(3 个按钮)

```
ZBTools > 实际大小
  [显示实际大小]
  [核验显示比例] [诊断信息]
```

- **显示实际大小**:按当前视图显示真实物理尺寸
  (不改旋转、不动透视、**不动模型位置**,只改尺寸;要居中把
  `~/.zbtools/settings.json` 的 `center_model` 设为 `true`)
- **核验显示比例**:输入框**预填探测值(单位 mm)**,量画布边框宽度对照,不同就改成实测值
- **诊断信息**:三段数据链(模型 / 屏幕 / 映射)打印到 Python 控制台,**并自动复制到系统剪贴板**
  (ZBrush 控制台不能选中复制;点完直接 Cmd+V 粘贴到聊天/编辑器即可)

屏幕参数**全自动探测**(EDID),没有检测类按钮;每次「显示实际大小」前都会重探,换屏能跟上;
探测不可用时才回落到配置里的手动参数。
提示里**只有模型相关的数值**跟随 Scale Master 选中的显示单位(mm / cm / in);
**屏幕与画布尺寸始终用 mm**;面板上不放单位开关。

## 尺寸口径(经实测确认)

### 尺寸来源

探测脚本(`scripts/probe_item_paths.py`)在真机上读出:

```
Tool:Geometry:X Size   = 2.0000193     ← 网格本身的尺寸(= query_mesh3d 的包围盒)
Tool:Export:Scale      = 39.3791770    ← ZBrush Scale(用户的缩放存在这里)
Zplugin:Scale Master:X = 78.7590027    ← 2.0000193 × 39.3791770 = 78.759 ✓ 完全吻合
```

所以:

```
真实尺寸     = 网格尺寸 × Tool:Export:Scale × 单位系数     (实时读取,无副作用)
屏幕缩放 scale = (真实尺寸 ÷ 每 pixol 毫米数) ÷ 网格尺寸
```

**三个关键点**:

0. **尺寸是逐轴的**(X/Y/Z 分别取),与 Scale Master 的 X/Y/Z 数值一一对应 ——
   不是把某个标量重复三遍(早期版本有这个 bug,任何物体都报成正方体)。

1. **`Export:Scale` 不影响屏幕渲染** —— 实测证据:画布 scale 3.202、网格 2.0 时,
   模型在屏幕上只是个小点(6.4 像素)。所以屏幕缩放的分母必须是**网格尺寸**,
   而不是"有效尺寸"(网格 × Export:Scale)。
2. **单位开关只是换算显示,不改变真实尺寸** —— 实测语义:同一个模型
   Scale Master 选 mm 显示 `2`,选 cm 显示 `0.2`,选 in 显示 `0.079`(= 同一个 2mm)。
   所以**尺寸口径恒为 1 ZBrush unit = 1 mm**;Scale Master 选哪个单位只影响提示里
   数值怎么显示(2.00 mm / 0.20 cm / 0.08 in),底层尺寸与画布 scale 完全不变。

### Scale Unify 烘焙的识别

`ZBrush Scale Unify` 可能把画布 scale 烘焙进几何(几何 ×K,屏幕可见大小不变)。
插件用 `intrinsic_g` / `eff_at_apply` / `last_applied_scale` 三个记账量区分
"我们的 K 被烘焙" 与 "模型真被改动",避免尺寸被放大 K 倍。

判定规则:

```
有效尺寸 ≈ eff_at_apply                       -> 尺寸未变
有效尺寸 ≈ eff_at_apply × last_applied_scale  -> 我们的 K 被烘焙,尺寸保持不变
其它                                          -> 模型已改动,尺寸跟随
```

实测(网格 2 unit + ZBrush Scale 39.379,Scale Master 选 mm):

| 操作 | 报告真实尺寸 | 屏幕物理尺寸 | 画布 scale |
|---|---|---|---|
| 点「显示实际大小」 | **78.76 mm** | **78.76 mm** ✅ | 1.000 → **125.97** |
| 点 `ZBrush Scale Unify` 后再点 | 78.76 mm ✅ | 78.76 mm ✅ | 1.000(识别出烘焙) |
| 再点一次(幂等) | 78.76 mm | 78.76 mm | 不变 |
| 在 Scale Master 里改尺寸后再点 | 跟随新尺寸 | 跟随 | 按新尺寸算 |
| 切换 Scale Master 的显示单位 | **不变**(2.00mm ↔ 0.20cm ↔ 0.08in 同一个尺寸) | 不变 | 不变 |

## 提示(布局可在 `~/.zbtools/settings.json` 的 `note_template` 里改)

```
真实尺寸:78.43 x 124.47 x 91.53 cm     ← 逐轴尺寸,与 Scale Master 的 X/Y/Z 对应
屏幕 P270 (1) 27.1" 163ppi              ← 只在模型数值上跟随 Scale Master 的单位
画布边框 401.4mm                         ← 屏幕/画布尺寸始终用 mm
正交视图                                 ← 或 透视视图
模型超出画布                             ← 仅 1:1 下超出画布时出现
```

**只有"给不出正确测量比例"时才独占提示窗口**:此时提示窗口只显示那一行警告,
不再堆尺寸/屏幕等信息。目前只有一种情况:

```
警告:所需缩放 31990 超出 ZBrush 可用范围(约 4000),显示会偏小;请先按 ZBrush Scale Unify 把缩放烘焙进网格
```

(网格很小 + ZBrush Scale 很大时会出现;此时 ZBrush 会截断画布 scale,实际显示比报告值小若干倍。)

**「模型超出画布」不是警告** —— 比例仍然正确,只是看不到全部,所以它是提示里的一行普通信息:

```
真实尺寸:200.00 x 200.00 x 200.00 cm
屏幕 P270 (1) 27.1" 163ppi
画布边框 401.4mm
正交视图
模型超出画布
```

脚本自身的错误(例如没选中网格)只弹一行错误说明。

正常情况下的规则:

- 第 1 行:模型三维尺寸(逐轴),单位跟随 Scale Master(mm / cm / in)
- 第 2 行:当前使用的显示器(自动探测;参数不可用时用配置里的兜底值)
- 第 3 行:画布边框宽度,**始终 mm**
- 之后:视图状态(正交/透视);超出画布时才多一行
- **不显示**"尺寸未变 / 首次"这类噪音;只有识别到 Scale Unify 烘焙、或模型被改动时才提示一行

可用变量见「诊断信息」里打印的对照表(`{x} {y} {z} {unit} {screen} {inch} {ppi} {canvas_mm} …`)。

## 屏幕映射与核验

- 探测来源:**CoreGraphics + EDID**(物理尺寸、原生像素、逻辑点),见
  `plugin/zbtools/tools/display_info.py`(共用,不在功能包内)
- `ppi` 与 `√(原生宽²+原生高²) ÷ 对角线英寸` 互相核验,偏差 >3% 会提示
- **核验显示比例**:输入框预填探测值(mm),量画布边框宽度对照;
  文档比屏幕逻辑分辨率大时 ZBrush 可能缩放显示画布(`get_canvas_zoom()` 不反映),这时必须靠这一步定标

## 不做副作用

- **只读** Scale Master:不按它的任何按钮(`press_scalemaster_refresh` 默认 `false`)
- 读包围盒时临时把画布 scale 归一到 1、读完立即还原
- 不改旋转、不动透视开关

## 常见偏差对照

| 症状 | 原因 | 处理 |
|---|---|---|
| 尺寸与 Scale Master 差 K 倍 | Scale Unify 烘焙(记账失效) | 已自动处理;必要时删掉配置里的 `intrinsic_g`/`eff_at_apply` 重记一次 |
| 数字对但屏幕不对 | 画布被缩放显示 / 屏幕映射 | 量画布边框,用「核验显示比例」填实测值 |
| 尺寸随深浅变化 | 透视视图 | 提示里会写明;要精确就切非透视 |
| 模型没落在画布中心 | canvas Y 方向约定 | `~/.zbtools/settings.json` 里 `flip_y` 改 `true` |
| 提示「模型超出画布」 | 1:1 下屏幕放不下 | 只能看局部(例如单位切成 cm 后模型变大 10 倍) |
| 提示「所需缩放 … 超出 ZBrush 可用范围」 | 网格很小而 ZBrush Scale 很大(如 0.0359 × 10000),1:1 需要 scale 31990,而 ZBrush 实测会在 4000 处截断 → 显示偏小 | 按 `ZBrush Scale Unify` 把缩放烘焙进网格后重试(此时网格 359.28、Export Scale 1,所需 scale 只有 3.2) |
| 模型应用后"飘走"/窗口反向偏 | 老版本会用「网格包围盒中心 × 画布 scale」重算位置,mesh 很小时偏移被 scale 放大 | 默认已改为**不动位置**;确实要居中再开 `center_model` |

## 加载

开发态(改完再 Load 即热更新):

```
ZBrush > ZScript 面板 > Python Scripting 子面板 > Load
  → scripts/load_plugin.py
```

自动加载:双击 `scripts/launch_zbrush_with_plugin.command`
(从终端启动 ZBrush 并带上 `PYTHONPATH`)。发行包里的加载方式见包内 `INSTALL.md`。

## 相关 API 索引

插件用到的接口都在 `docs/API_REFERENCE.md`(项目根,自动生成)。
插件最相关的是:`show_note`(提示框,支持 bg_color/时长)、`ask_string`(输入框)、
`add_palette/add_subpalette/add_button`(建面板)、`query_mesh3d`(包围盒)、
`get_transform/set_transform`(画布空间变换)、`get/set/exists/press`(读写任意界面项)。

## 依赖

- `pyobjc-framework-Quartz`(项目 venv 内),用于读显示器参数;
  换机器/换 venv 后改 `~/.zbtools/settings.json` 的 `interpreter`
  (或用环境变量 `ZBTOOLS_PYTHON`)
- 单独看显示器信息:`.venv/bin/python plugin/zbtools/tools/display_info.py`
- 探测 ZBrush item path:Load `scripts/probe_item_paths.py`

## 文件结构

```
plugin/zbtools/
├── init.py                       # 插件入口(PYTHONPATH 里的 init.py,自动执行)
├── tools/display_info.py         # CoreGraphics + EDID 显示器探测(子进程调用)
└── zbtools/
    ├── settings.py               # 配置(默认 ~/.zbtools/settings.json)+ 状态版本
    ├── zbapi/                    # screen / model / canvas / clipboard
    └── features/true_scale/
        ├── __init__.py           # FEATURE + 面板控件
        ├── service.py            # 尺寸口径、映射、核验、诊断
        └── README.md             # 本文件
```

## 测试

```bash
.venv/bin/python tests/test_true_scale.py   # 换算、有效尺寸、Unify 烘焙、幂等、错屏…
.venv/bin/python tests/test_ui.py           # 按钮面板、单位跟随 Scale Master、预填核验、item path 冒号回归
```
