# ZBrush 原生量测能力调研(TransPose 动作线 / Gizmo 3D)

> 目的:搞清 ZBrush 自己有哪些"尺子"、参数在哪、哪些能用 Python API 读写。
> 结论先行:**Y 键切换的两样东西里,能当尺子的是 TransPose 动作线,不是 Gizmo 3D**;
> 而 API 提供了 `canvas_click()`,插件可以**自己**在画布上落点并读回长度 —— 这是做屏幕量测的底座。

来源:Maxon 官方帮助([Gizmo 3D](https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/modeling-basics/gizmo-3d/gizmo-3d.html)、
[Basic Operations](https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/modeling-basics/gizmo-3d/basic-operations/basic-operations.html)、
[Transpose Units](https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/modeling-basics/transpose/transpose-units/transpose-units.html)、
[Preferences > Transpose](https://help.maxon.net/zbr/en-us/Content/html/reference-guide/preferences/transpose/transpose.html)、
[Transform > Info](https://help.maxon.net/zbr/en-us/Content/html/reference-guide/transform/info/info.html))
+ 官方 Python SDK(`sdk/api/zbrush/commands.py` 与 `sdk/examples/fragments/`)。

---

## 一、Y 键到底切了什么

官方原文:

> To switch between the Gizmo 3D and the TransPose action line, you simply need to toggle the Gizmo 3D icon or
> press the corresponding hotkey: **Y**. By switching between Gizmo 3D and TransPose, you will notice that both
> Gizmo 3D and TransPose action line have the same position and orientation.

| | Gizmo 3D | TransPose 动作线 |
|---|---|---|
| 用途 | 移动 / 缩放 / 旋转 + Deformer | 移动 / 缩放 / 旋转 |
| **刻度尺** | 无 | **有(默认开启)** |
| 长度读数 | 无 | 拖长时**状态栏显示总长** |
| Python 可读写 | ❌(只能 `get/set("Transform:Gizmo 3D")` 开关) | ✅ `get_transpose()` / `set_transpose()` |
| 存在条件 | `Transform:Gizmo 3D` = 开 | `Transform:Gizmo 3D` = **关** |

所以"用 Gizmo 量长度"是走不通的;**关掉 Gizmo 3D、让动作线出来**(= 按 Y)才是量测姿势。

---

## 二、尺子的参数和设置

### 1) Preferences > TransPose Units(刻度与单位)

官方原文:

> TransPose can display graduations on its line so that it can be used for measurements.
> The graduations are enabled by default and can be changed in the Preferences > TransPose Units sub-palette.

| 项 | 作用 |
|---|---|
| `Units Scale` / `Distance Units Scale Factor` | ZBrush 单位 → 真实单位的比例 |
| `Set Units` / `Set Distance Units` | 给单位起名(如 "mm") |
| `Minor Ticks Per Unit`(0=off) | 每个单位画几根次刻度 |
| `Major Ticks Per Unit`(0=off) | 每个单位画几根主刻度 |
| `Calibration Distance` | **校准**:输入已知长度,把当前动作线长度定为该值 |

官方给的标准校准流程:

1. 在 Edit 模式下点 Move/Scale/Rotate 切到 TransPose
2. 拖动动作线两端,定义"一个单位长度"的起止
3. Preferences > TransPose Units → `Units Scale` 设为 1
4. 点 `Set Units`,输入你想要的单位名
5. `Major Ticks Per Unit` 设为 1
6. `Minor Ticks Per Unit` 设成你要的值(如 2)
7. `Calibration` 设为 1 —— 这一步把当前线长设为 1 个单位
8. 再拖长动作线,新刻度会出现,**总长显示在状态栏**

### 2) Preferences > Transpose(交互与吸附)

`Circles Radius`、`3D`(圆圈是否随透视缩放)、`Axis length`、`Show Alignment Dial`、
`Rotation Steps`、`Mask Blur Strength`、`Mask Sharpen Strength`、`Align ActionLine To Axis`、
`Align MaskLine To Axis`、`Enable Transpose Inflate`、`Enable Transpose Trim`,
以及关键的一条 —— **`Snap Max distance`**,官方原文:

> When drawing the Transpose line the ends of the line snap to the surface under or near to the cursor.
> When the cursor is over the canvas background, the `Snap Max distance` determines how far it can be from
> the model and still snap a Transpose line endpoint to the model's surface.

**所以"吸附"没有开关,只有一个距离阈值**:

* 光标压在模型上 → 端点**永远**贴到表面(这是放置规则,关不掉)
* 光标在空白画布上 → `Snap Max distance` 决定"多远之内还是会被拉去贴模型";调到 0/最小,端点就留在画布空间

### 3) Transform > Info(另一条读数通道)

官方原文:

> The Info sliders display numeric data about the position, size and orientation of a 3D object when it's in a
> Transform or Edit mode. If the Move button is pressed, these sliders display the X, Y and Z components of the
> object's position. If Scale is pressed, they display the object's size, and if Rotate is pressed, they display
> the object's orientation. […] If the Draw Pointer is selected, these sliders display the cursor's horizontal
> and vertical position and its depth.

即 `Transform:Info:X/Y/Z` 会在三种含义间切换(物体位置 / 物体尺寸 / 光标位置+深度),取决于当前 Transform 模式。

### 4) "锁定视图"

ZBrush **没有视图锁**。Gizmo 3D 里那个 Lock/Unlock 是另一回事:锁定 = gizmo 与所选几何绑定(拖 gizmo 就动模型);
解锁 = gizmo 自由移动、**不碰模型**(测量时正是要这个姿势)。
"视图不跑"只能靠不去转视角;插件侧我们有 `remember_view()/restore_view()` 兜底。

---

## 三、"能不能测投影"

关键区分 —— **动作线的长度是空间长度,不是屏幕投影长度**:

| 端点怎么放 | 量到的是什么 | 什么时候对 |
|---|---|---|
| 吸在模型表面上(默认) | 两端点的**真实 3D 距离** | 想量零件真实尺寸时——**这个更准**,倾斜的零件也是真长度 |
| 点在空白画布上(需 `Snap Max distance`≈0) | 两端点同深度时的**画布平面距离** | 想复刻"在纸上量图纸"时 |

因为动作线两端点若落在同一画布深度平面上,它们的画布 Δpixol 就正比于屏幕距离;
再乘我们已有的 `mm_per_pixol`(且显示为 1:1、正交相机)就得到真实长度。
**如果两端点深度不同(透视相机),比例不成立** —— 这也是"只看屏幕投影"本身的限制,不是方法能绕过的。

---

## 四、Python API 能碰的地方

| API | 说明 |
|---|---|
| `canvas_click(X1,Y1[,X2,Y2,...X8,Y8])` | **模拟画布点击/拖拽**,坐标为画布空间 → 插件可以自己画动作线 |
| `get_transpose()` | 16 个值:原点 xyz、法线 xyz、**长度**、红/绿/蓝三轴 |
| `set_transpose(origin, normal, length=…, axes…)` | 直接摆放动作线(示例明确:`x_end/y_end/z_end` 是**法线**不是终点,长度由 `length` 决定) |
| `is_transpose_shown()` | TransPose 是否激活(与用线还是用 Gizmo 无关) |
| `set("Transform:Gizmo 3D", 0/1)` | 切 Gizmo 3D 开/关(= 让动作线出来/收起来) |
| `press("Transform:Move")` / `click(item_path, x, y)` | 切到 Move 模式;点击任意界面项 |
| `get_transform()` | 工具在画布空间的位置/缩放/旋转。官方示例注释:位置原点在画布**左上角**;"想同时看到 (0,0,0) 和 (canvas_width, canvas_height, 0),缩放必须是 (1,1,1),常见工具缩放约 (100,100,100)" |
| `get_canvas_zoom()` / `get_canvas_pan()` | 官方画布缩放/位移(我们之前是自己实现的,可换成官方) |
| `get_mouse_pos(global_coordinates=False)` | 画布空间鼠标位置(官方示例明确是 canvas space) |
| `get("Transform:Info:X")` 等 | 上面说的 Info 读数 |

### 由此得到的量测方案

```
用户点两个点(或插件直接算好两点)
        ↓
set("Transform:Gizmo 3D", 0) + press("Transform:Move")     # 让动作线可用
        ↓
canvas_click(x1, y1, x2, y2)                               # 插件自己画尺子(带原生刻度)
        ↓
length = get_transpose()[6]                                # 读回长度
        ↓
length × (单位换算) → mm / cm / in(跟随 Scale Master 显示单位)
```

好处:不碰网格、看得见刻度、不需要用户手动关吸附或锁视图。

### 还没确定的(要实测)

1. `get_transpose()[6]` 的长度是**画布 pixol** 还是**网格单位**
   → 探针用 `canvas_click` 画一条已知跨度的线,再用 `get_transform()[3]`(= 每网格单位多少 pixol)反推
2. `canvas_click` 在 ZBrush 的 Python VM 里是否真的生效(以及是否会被吸附拉走)
3. 那些 Preferences 的 item path 到底怎么写(`Preferences:Transpose:Snap Max distance` 等)
4. 画布空白的落点深度是否稳定(决定"纸面投影"是否可靠)

以上由 `scripts/probe_measure.py` 一次跑完并自动判定。

---

## 五、最终采用:屏幕真尺浮层(不用动作线,也不用点两个点)

调研到这里出现了更省事的一条路,已实现为 `ruler` 功能(显示名「屏幕真尺」)。

### 为什么不是"插件直接在画布边缘画刻度"

ZBrush 的 Python API **没有任何画布绘图原语**(145 个函数里没有画线/矩形/文字),
插件的 UI 控件也只能待在左右面板或作为居中模态 Note(Note 的定位参数官方标注
"Has no effect")。所以"贴在屏幕上的真尺"只能由 ZBrush 之外的子进程画一个置顶窗口
(`plugin/zbtools/tools/ruler_window.py`,AppKit:支持旋转与点击穿透)。

### 刻度依据:ppi 口径,不用 EDID 宽高

```
1 mm = 逻辑ppi ÷ 25.4 个逻辑点 = 3.1992 逻辑点 = 6.3984 原生像素(P270)
```

- 必须与 `mm_per_pixol` 用**同一套 ppi**,否则尺子和「显示实际大小」会各说各话
- 一开始想用 EDID 的物理宽高逐轴算,结果横竖差 **1.2%** —— 屏幕像素是方的,
  这个差说明 EDID 的宽高只能用来**发现问题**,不能当基准(它与 ppi 差约 0.3%)
- 尺子因此**只依赖显示器物理参数**,与 ZBrush 画布缩放、模型、透视完全无关
- 反过来,读数是否有意义取决于模型是否 1:1 显示;**平移视窗不改变比例**,所以
  "平移模型对着刻度读"这个流程是安全的

### 验证

| 验证项 | 结果 |
|---|---|
| 渲染路径(截屏量主刻度间距 vs 理论值) | 误差 **0.017%**(EDID 口径)/ **0.062%**(ppi 口径) |
| 横竖方向一致性 | 完全相同(同一 ppi 口径) |
| 父进程守卫(ZBrush 退出后不留孤儿窗口) | 已验证:父进程消失后窗口自行关闭 |
| 任意角度旋转(0°/45°/137°) | 实测偏差 ≤**0.02°**(差分法量出来的角度);轴心按抓取的半边自动选 |
| 点击穿透 | 已验证:光标离开尺身即 `setIgnoresMouseEvents_`,日志可查 |
| 自动核验脚本 | `.venv/bin/python scripts/verify_ruler_overlay.py 3.1992` |
| 实体尺子 | 待用户比对(最终裁决) |

### 与其它方案的关系

- **不用** TransPose 动作线当尺:它的屏幕长度随画布缩放变化,刻度还依赖 ZBrush 的单位配置
- **不用**两点卡尺(`canvas_click` + `get_transpose`):可行,但每次要取点、读数不如"看一眼"直接;
  这些 API 的结论保留在上面第四节,将来若要做"自动量两点距离"可以直接用
