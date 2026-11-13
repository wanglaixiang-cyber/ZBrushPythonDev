# 网格修复模块(meshtools)详细设计

> ⚠️ **状态:已归档(功能代码已清空)**。`features/meshtools/` 现在只是一个空存根
> (面板上保留「---- 网格修复 ----」分区,里面没有控件);`service.py`、
> `tools/mesh_pipeline.py`、`tests/test_meshtools.py` 与两个真机探针都已删除,
> 完整源码在 `_backup/ZBrushPythonDev-before-meshtools-cleanup-*.tar.gz`。
> 本文档**保留下来当设计参考**(CLI 契约、实测结论、布尔失败条件),
> 重新实现时按 `plugin/zbtools/zbtools/features/meshtools/__init__.py` 的 docstring 起步。

> 状态:**已实现并已按需求收敛**(测试全绿,管线命令已用合成网格实测)。
> 路线:B —— 外部 Python 管线(trimesh / manifold3d / fast-simplification / pymeshlab)。
> **范围(2026 调整)**:体检 / 封洞 / 减面 / **文件夹布尔预检**。
> **布尔运算本身已从插件移除** —— 布尔交给 ZBrush 原生的 Folder Actions
> (Boolean Folder / Boolean DSDiv),插件负责在布尔**之前**按官方失败条件做预检。

## 1. 目标与边界

- ZBrush 侧**零第三方依赖**:插件逻辑只调官方 `zbrush.commands` / `zbrush.utils.run_script`。
- 所有网格计算都在**子进程** `tools/mesh_pipeline.py` 里完成(沿用 `ruler_window.py` / `display_info.py` 的既有模式,用 `cfg["interpreter"]` 指向的 venv Python)。
- 第三方库缺失时**优雅降级**:功能照常注册,点按钮提示"依赖未安装 + 安装命令",不影响插件其他功能。
- 单位口径保持项目既有约定:1 ZBrush unit = 1 mm;OBJ 坐标**原样往返**,管线内不做任何缩放。

## 2. 依赖(装进项目 `.venv`)

```bash
.venv/bin/pip install numpy trimesh fast-simplification manifold3d
# 可选,装了就启用更高质量的封洞/修复(MeshLab 过滤器)
.venv/bin/pip install pymeshlab
```

| 库 | 分工 |
|---|---|
| `numpy` / `trimesh` | OBJ 读写的底座;体检统计(水密性/边界环/非流形边/退化面/自相交/法向一致性) |
| `trimesh.repair` | 快路径:去重复顶点/去退化面/`fill_holes` 封小洞 |
| `pymeshlab`(可选) | `meshing_close_holes`(可设阈值、曲面拟合)、`meshing_repair_non_manifold_edges`、`meshing_remove_duplicate_*` —— 大洞/复杂破面 |
| `fast-simplification` | 减面(Garland–Heckbert,秒级);trimesh `simplify_quadric_decimation` 的标准后端 |
| `manifold3d` | **布尔核心 + 自愈**:把"看起来像流形"的坏网格按卷绕数重建成水密,再做 union / difference / intersection |
| `networkx` | trimesh 的**运行时依赖**(`repair.broken_faces` 建邻接图用;漏装 = NotModuleFound,已实测) |

## 3. 数据流(ZBrush ↔ 外部管线)

```
service.py(插件内)                                    mesh_pipeline.py(子进程)
─────────────────────────────────                    ─────────────────────────
1. 记录当前 Tool / Subtool
2. run_script('[IOBuffer,ExportMesh,"<tmp>.obj"]')  ⚠见探针
3. subprocess: interpreter tools/mesh_pipeline.py
   --cmd ... --in a.obj --out out.obj ──────────────▶  4. 读 OBJ → numpy/trimesh 处理
   ◀────────────────────────────── JSON 结果          5. stdout 输出单块 JSON
6. run_script('[IOBuffer,ImportMesh,"<tmp>_out.obj"]') ⚠见探针
7. 校验导入(读 Tool:Geometry:Face Count / query_mesh3d)
8. 清理临时目录;报告
```

- 临时目录:`tempfile.mkdtemp(prefix="zbtools_meshtools_")`;失败时保留目录便于排障。
- 布尔多一步:导出 Subtool A 与 B(先 `select_subtool` 切到 B 再导出),外部先各自自愈再布尔,结果单 OBJ 导入。
- ⚠ **两个待真机验证点**(SDK 无导出/导入 API,只能走 ZScript):
  1. `[IOBuffer,ExportMesh,"..."]` 导出的是当前 Subtool 还是整个 Tool?受不受 `Tool:Export:Scale` 影响?
  2. `[IOBuffer,ImportMesh,"..."]` 导入行为:新 Tool?覆盖当前?命名?能否变 Subtool?
  → 用 `scripts/probe_mesh_io.py`(照 `scripts/probe_measure.py` 写法)在真机上确认后再定回填逻辑;未确认前 README 标注"未在真机验证"。

## 4. 目录与文件

```
plugin/zbtools/zbtools/features/meshtools/
├── __init__.py          # FEATURE(key="meshtools", title="网格修复", build) + LABELS + paths()
├── service.py           # 编排:导出→调管线→导入→报告;配置读写
├── tools/
│   └── mesh_pipeline.py # 外部子进程 CLI(不 import 任何插件代码,只吃 argv + 输出 JSON)
└── README.md            # 用法/限制/依赖安装
scripts/probe_mesh_io.py # 真机探针:验证 IOBuffer 导出/导入的确切行为
tests/test_meshtools.py  # 逻辑测试(fake_zbrush,不开 ZBrush)
docs/DESIGN_MESHTOOLS.md # 本文档
```

依赖方向合规性(由 `tests/test_architecture.py` 兜底):
- `meshtools` 只依赖 `ui` / `settings` / `errors` /(可选 `zbapi.clipboard`),不跨功能 import。
- `mesh_pipeline.py` 是独立进程脚本,与插件代码零耦合(与 `ruler_window.py` 同一原则)。

## 5. `mesh_pipeline.py` CLI 契约

```
--cmd inspect | fill_holes | decimate | boolean | self_check
--in a.obj [--in2 b.obj] --out out.obj
[--target-ratio 0.5] [--hole-max-edges 50] [--boolean-op union|intersection|difference]
stdout: 单块 JSON {ok:true, stats:{...}} 或 {ok:false, error:"..."}
```

- `inspect`:只读统计,不写 `--out`。stats = watertight / boundary_loops / non_manifold_edges / degenerate_faces / self_intersecting / vertices / faces。
- `fill_holes`:去重复/退化 → pymeshlab `close_holes`(有则用,`--hole-max-edges`)→ trimesh `fill_holes` 兜底 → 报告封堵后边界余量。
- `decimate`:fast-simplification 按 `--target-ratio` 减面;pymeshlab 可用时改走 `meshing_decimation_quadric_edge_collapse`(目标面数,质量优先);报告前后顶点/面数。
- `boolean`:载入双网格 → 各自自愈(`manifold3d.Manifold(mesh)`,自相交/非流形归正)→ manifold 布尔 → 校验结果 valid 且体积 > 0 → 导出。
- `self_check`:列各库版本,缺失报 false(供「诊断信息」按钮)。
- 超时:插件侧 `subprocess.run(timeout=cfg["meshtools__pipeline_timeout_s"])`,默认 600s;超大网格先弹"处理中…"提示。

## 6. 面板 UI

LABELS(每段不含 ASCII 冒号):

| key | 控件 | 说明 |
|---|---|---|
| `inspect` | 按钮 | 体检当前模型:统计报告水密/洞数/非流形边/退化面 |
| `fill` | 按钮 + slider `hole_max_edges`(5–200,默认 50) | 封洞;提示封堵后边界余量 |
| `decimate` | 按钮 + slider `target_ratio`(1–100%,默认 50) | 减面;提示前后顶点/面数 |
| `boolean` | switch `heal_first`(默认开)+ 并集/差集/交集 3 按钮 | 取"下一个 Subtool"为操作数 B;操作数先自愈 |
| `diag` | 按钮 | 依赖版本(self_check)+ 最近一次管线 JSON + 临时目录,复制到剪贴板 |

所有按钮包 `ui.guard`;耗时操作先 `ui.note("处理中…")`。

## 7. 配置键(`settings`,前缀 `meshtools__`,默认值放 service,仿 ruler)

```
meshtools__hole_max_edges   = 50      # 封洞阈值(边界边数)
meshtools__target_ratio     = 0.5     # 减面保留比例
meshtools__heal_first       = True    # 布尔前先自愈
meshtools__pipeline_timeout_s = 600
meshtools__keep_temp        = False   # 失败时保留临时目录
```
解释器复用现有 `cfg["interpreter"]`(不新增)。

## 8. 异常与提示

- `errors.py` 增加 `PluginError(RuntimeError)`,`PluginError` 改为继承它;`ui.guard` 捕获 `PluginError`(小重构,同步更新测试)。
- 依赖缺失:子进程 `self_check` 返回 false → 提示"依赖未安装:\n.venv/bin/pip install numpy trimesh fast-simplification manifold3d"。
- 导出/导入失败:提示 + 保留 OBJ 路径,用户可手动导入兜底。

## 9. 测试计划(不开 ZBrush)

- `tests/test_architecture.py` 自动覆盖新功能(无需改动)。
- `tests/test_meshtools.py`:
  - `command()` 构造(cfg → argv,纯函数,仿 `ruler.command`);
  - 管线 JSON → 报告文本解析;
  - service 编排调用序列:fake_zbrush 扩展(`run_script` 记录、`select_subtool`、`get_active_tool_index`),断言 导出→处理→导入 顺序与文件名;
  - 依赖缺失分支:报错文案断言。
- 真机验证项(需 ZBrush):`scripts/probe_mesh_io.py`;确认前功能标注"未在真机验证"。

## 10. 发行影响

- `scripts/package_plugin.py`:`FEATURE_DIRS` 增加 `"meshtools"`;管线脚本随包 `.pyc` 化(子进程跑 `.pyc` 与 `ruler_window.py` 同机制,已由打包器统一处理)。
- 无第三方库的环境(如 Windows 发行版跑 ZBrush 自带解释器):`self_check` 失败 → 功能显示"依赖缺失"提示,不崩溃。
- `docs/INSTALL.md`:补依赖安装命令 + "IOBuffer 流程未在真机验证"标注。

## 11. 实施清单(按序)

1. `errors.py` 加 `PluginError` + `ui.guard` 更新 + 对应测试更新
2. `features/meshtools/tools/mesh_pipeline.py`(self_check / inspect / fill_holes / decimate 先落地)
3. pip 安装依赖,用手工 OBJ 跑通全部 CLI 命令
4. `features/meshtools/{__init__.py, service.py}`(先体检 + 封洞 + 减面)
5. `features/__init__.py` 注册(try/except,同 ruler)
6. `scripts/probe_mesh_io.py`;真机验证 IOBuffer 后补布尔 + 回填逻辑
7. `tests/test_meshtools.py` + `fake_zbrush.py` 扩展;`tests/run_all.py` 全绿
8. README / INSTALL 更新 + `package_plugin.py` FEATURE_DIRS

## 12. 风险与待验证点

- IOBuffer ExportMesh / ImportMesh 的确切语法与行为(探针验证)
- OBJ 导出是否受 `Tool:Export:Scale` 影响(探针读坐标 offset)
- pymeshlab 在 Python 3.11 的可用性(装不上则纯 trimesh + manifold3d 路径)
- 超大网格(>500 万面)布尔耗时/内存:超时与提示策略先行

## 13. 实现偏差与实测结论(实现后回填)

- **`networkx` 是 trimesh 的硬性运行时依赖**:`repair.broken_faces` 需要它,
  设计稿没列;已实测确认并写进安装命令。
- **manifold3d 3.5 API 与旧文档不同**(实测):布尔不再有 `union/difference/
  intersection` 方法,改成 `manifold3d.Manifold.batch_boolean([a, b], op)`,
  `op` 是 `OpType.Add|Subtract|Intersect`(差集 = 首元素 − 尾部);`Mesh`
  构造器要**位置传参** `Mesh(vert_properties_float32, tri_verts_uint32)`,
  不接受 dict。
- **trimesh 5.x 的 `broken_faces` = 边界邻接面(破面),不是退化面**:
  用它删面会把伤口撕得更大,所以清理逻辑改为只删「含 NaN 顶点 / 零面积」的
  真退化面;统计拆成 `broken_faces`(破面数)与 `degenerate_faces`(真退化数)。
- 减面比例在面板上用**百分数滑块**(1–100),配置仍存 0~1 小数
  (`meshtools__target_ratio`);封洞/减面输出带 `before` 统计,报告显示前后对比。
- 管线输出追加 `boolean_ready`(manifold3d 能否把输入自愈成合法流形 ≈ 布尔前提)。
- 实施清单状态:1–5、7、8 已完成;6 的探针脚本已写,真机执行待用户 ZBrush 环境。
- **真机探针第一轮结论(2026,已实测)——路线调整**:
  ZScript `[IOBuffer,ExportMesh,"..."]` 经 `zbu.run_script` **返回 False 且不产生文件**,
  该路线**废弃**;改用 Maxon 官方示例
  (`sdk/examples/modeling/ex_mod_subtool_export.py`,Javier Edo / Maxon, 2025-08)
  验证过的路线:
  ```python
  zbc.set_next_filename(全路径)   # 预设文件名
  zbc.press("Tool:Export")        # 按 Tool 面板的 Export 按钮(导出当前 SubTool)
  ```
  据此 `service.py` 已重写导出/导入(`export_steps` / `import_steps` 纯函数便于对照),
  并加**写盘轮询**(`meshtools__io_wait_s`,默认 30s):ZBrush 把 press 排进命令队列、
  不是同步调用;轮询同时兼容"直接落盘"与"弹框后用户确认"两种情况。
- 第一轮探针的另一发现:该机 `Tool:Export:Scale = 0.0`(而 `true_scale` 的读数口径按
  `>0 才用,否则回落到 1.0` 处理)—— 所以第二轮探针专门比对
  「OBJ 包围盒 vs Tool:Geometry 尺寸 vs Export:Scale」,确认导出有没有被缩放。
- **真机探针第二轮结论(已实测)——导入语义与收尾策略**:
  - 官方导出路线**可用**:`set_next_filename(全路径)` + `press("Tool:Export")`
    → 0.01s 落盘(638KB,v=10968 f=11066),**不弹对话框**(若有对话框等确认,
    0.01s 内不可能落盘)。
  - 坐标口径:OBJ 包围盒 ÷ `Tool:Geometry:X Size` = **0.999990** → 写的是原始几何
    单位,**未乘 `Export:Scale`**(该机 `Export:Scale = 0.0`);差值 ~1e-5 来自 OBJ
    文本精度/多面体逼近,无实际影响。
  - `Tool:Import` / `Tool:Export` 两个按钮都存在;导入**不是追加 Subtool**,
    而是让结果**占用当前 Tool 槽**,Tool 名变成 `!:<完整路径>`(无扩展名),
    Tool 索引不变(实测仍是 48),SubTool 数不变(仍 1)。
  - 据此 `service.py` 增加:**导入前 `_tool_snapshot()` 记录 Tool 列表与选择** →
    导入后 `finish_import()` 认出结果(`!:` 前缀/名字变化)→ 用 `set_tool_path`
    **改名成 `原模型名_动作`** → 在列表里找原模型:还在就 `select_tool` 切回 +
    提示 Append 合并;不在就**明确警告原模型被顶替**(提示 Cmd+Z / 恢复工程)。
    `set_tool_path` 返回 **0 = 成功**(不是布尔)。
  - 新增配置 `meshtools__auto_import`(默认 true):关掉就只留结果文件、不碰场景。
  - 实现中踩到的坑(已修):`select_tool` 成功返回 `0`,写成
    `int(... or -1) == 0` 会把成功判成失败 —— 0 是假值,必须显式判 `None`。
  - 仍待第三轮确认:`set_tool_path` 对 `!:路径` 这种名字改名是否生效、
    导入后原模型是否仍在 Tool 列表里(插件对两种结果都已处理好)。

## 14. 范围调整(2026,按使用者要求)

- **移除**:插件内自己做并/差/交布尔(pipeline 的 `boolean` 命令、service 的 `boolean()`、
  面板的三个布尔按钮、`heal_first` 配置、相关测试)。原因:布尔用 ZBrush 原生
  Folder Actions 更顺手。
  * 保留 `manifold3d` 的用途:体检里的「可自愈性 / 布尔可用性」判据
    (输入能否重建为合法流形 ≈ 布尔引擎能不能吃下它)。
- **新增「文件夹布尔预检」**(面板按钮,只在当前 SubTool 处于 SubTool 文件夹内时可用):
  - 作用域 = 当前 SubTool 所在文件夹(**官方**:Boolean Folder 忽略文件夹外的 SubTool,
    文件夹第一个 SubTool 恒为正/Start Group);
  - 逐个成员导出 → 管线 `folder_check` → 按官方失败条件出结论;
  - 官方失败条件(逐条实现,来源见下):
    1. **避免共面面**(头号原因)→ 跨模型共面重叠检测(量化平面分桶 + 2D 三角形相交,
       同平面并排不误报);
    2. **水密/体**(不要单面片)→ 水密 + 边界环(洞);
    3. **非流形边必须移除** → 边引用数 >2;
    4. **退化拓扑** → 零面积/NaN 面 + 同一网格重复面;
    5. **密度一致性** → 面数最大/最小 ≥100 倍提醒;
    6. **降低 DSDiv 级别** → 报告内提醒(DSDiv 布尔前转真实几何);
    7. **必须 PolyMesh 3D** → 导不出来的成员列出并提示 Make PolyMesh3D;
    8. 文件夹结构:成员 <2 拒绝、>100 提醒、名字非字母数字提醒、隐藏项列出;
    9. 补充:与基准模型包围盒不相交(布尔中无贡献)。
  - 面板新增「共面高亮」:开关 ZBrush 原生 `Render >> Render Booleans >> Show Coplanar`,
    让用户复核预检报出的共面处。
  - 按钮启用/禁用:注册时 + 每次点本功能按钮时刷新(ZBrush 无选择变化回调;
    为此给共享层 `ui.Panel` 加了 `set_enabled()`)。

### 官方依据(本轮查证)

- *Important Information about Boolean Operations*(Maxon 在线帮助, Live Boolean 章节):
  避免共面面 / 使用体(建议水密)/ 网格密度一致性 / 降低 Dynamic Subdivision 级别 /
  非流形边必须移除。
- *Boolean Resulting Topology Issues and Errors*:错误多来自**共面面**或**完全退化的拓扑**;
  工具 `Render >> Render Booleans >> Show Coplanar` 与 `Show Issues`。
- *Geometry and Topology Analysis Functions*:Show Coplanar / Show Issues 的 Inside、Solo、
  Previous/Next。
- *SubTool Folders*:Boolean Folder 只处理文件夹内 SubTool、**第一个 SubTool 恒为正且为
  Start Group**、文件夹不能嵌套、上限 100 个/文件夹、名字建议纯字母数字;
  Boolean DSubDiv = 同上 + 处理期间对文件夹内 SubTool 临时开 Dynamic Subdivision。
- *Live Boolean*:布尔要求模型是某种 PolyMesh 3D;Primitive/ZSphere/ZSketch/MicroMesh
  必须先转换。

### 第四轮真机实测结论(文件夹 API + 原生诊断项)

- **文件夹索引从 0 开始**,同文件夹的 SubTool 读到同一索引,默认名 `Untitled Folder`;
  既然 0 是有效文件夹,"不在任何文件夹里"必然是负数(惯例 -1)
  → 判定简化为 `folder_index >= 0`(不再要求名字非空;名字只用于展示与命名建议)。
- **`Render:Render Booleans:Show Coplanar` 存在且可读写**(set 1.0 → 读回 1.0 → 还原成功)
  → 面板「共面高亮」按钮照原计划实现,无需改路径。
- 同时存在:`...:ShowCoplanar`(无空格写法)、`...:Show Issues`(布尔后定位错误)、
  `...:Live Boolean`、`Render:Show Coplanar`(简写);
  `...:Show Coplanar:Inside` / `:Solo` **不是**独立 item(是联动子模式)。
- 命名建议放宽:`Untitled Folder` 带空格是 ZBrush 默认名,不该被误报;
  只有字母/数字/空格/下划线/连字符之外的字符(含中文)才提醒。

## 15. 回归事故记录(2026,真机反馈)

**现象**:真机点「封洞 / 减面 / 体检」全部失败,报
``load_new_mesh(): incompatible function arguments ... file_name: str``。

**根因**:为让 ``folder_check`` 能一次传多个文件,把 ``--in`` 改成了可重复参数
(``action="append"``)—— 于是 ``args.input`` 永远是**列表**,而 ``inspect`` /
``fill_holes`` / ``decimate`` 三条命令还在把它当单个路径用:trimesh 报
``file_type 'None' not supported``,pymeshlab 报参数类型不符。

**修复**:新增 ``_single_input(args)``(取唯一元素,数量不对就报"需要恰好一个 --in"),
三条单网格命令全部改用它;``folder_check`` 继续用列表。

**为什么测试没抓到(比 bug 本身更重要)**:编排测试把
``service.subprocess.run`` 换成了假替身 —— 而 ``service.subprocess`` 就是**真的
subprocess 模块**,这一下把**整个测试进程**的 ``subprocess.run`` 都换掉了;
于是新写的"真跑管线 CLI"冒烟测试静默拿到假载荷,根本没启动子进程。
(另一个更早的 gap:改 ``--in`` 时只手工验证了 ``folder_check``,
没把三条老命令一起回归。)

**两条措施**:
1. CLI 契约用**真子进程 + 真文件**钉住:``tests/test_meshtools.py:pipeline_cli_tests``
   跑 ``self_check / inspect / fill_holes / decimate / folder_check`` 与参数数量校验;
2. 测试替身只替换 ``service`` 看到的那一层(``FakeSubprocess`` 赋给 ``service.subprocess``),
   **绝不改真模块的属性** —— 否则会污染同进程里其它测试(已在 docstring 里写明原因)。
