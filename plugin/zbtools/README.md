# zbtools —— ZBrush 工具集插件

一个插件里放多个功能,每个功能一个包(见 `../../docs/DEVELOPMENT.md`)。

## 现状

| 功能 key | 显示名 | 说明 |
|---|---|---|
| `true_scale` | 实际大小 | 按屏幕实际物理尺寸(1:1)显示选中的模型;详见 `zbtools/features/true_scale/README.md` |
| `ruler` | 屏幕真尺 | 屏幕上贴一把真实刻度的尺子(可旋转/多把);详见 `zbtools/features/ruler/README.md` |
| `meshtools` | 网格修复 | **空存根**:功能代码已整体清空,只保留分区占位(详见 `zbtools/features/meshtools/README.md`) |
| `tex2pp` | 贴图转色 | 低模(有 UV + 贴图)的贴图 → 高模 polypaint:**不用细分低模、不用给高模做 UV**;详见 `zbtools/features/tex2pp/README.md` |
| `depthimg` | 图片转深度图 | 把**选中模型的贴图**转成灰度深度图并送回 ZBrush(默认替换模型贴图);面板四格 = **DA V2 四档规模**(V2-S/B/L/G,只有 S 可商用)+ 自研 luma·sdf 与 DA3 仍在配置里可切;详见 `zbtools/features/depthimg/README.md` |

## 面板

```
ZBTools(右侧菜单栏)
└── <功能显示名>        ← 每个功能一个子面板
      [显示实际大小]
      [核验显示比例] [诊断信息]
└── 屏幕真尺
      [新建尺子] [删除尺子]
      [尺长 mm] [诊断信息]
└── 网格修复            ← 空存根:只有分区标题,区里没有任何控件
└── 贴图转色
      [① 记住低模    ] [UV 翻转     ]     ← 都是按钮:点中低模按①,点中高模按②
      [② 烘焙并导回   ] [投射阈值 ▬▬▬ ]     ← 只有出错才弹提示
      [诊断信息                        ]
└── 图片转深度图                          ← 和上面的①②无关(独立功能)
      [转深度图        ] [替换模型贴图 (开关) ]   ← 读选中模型的贴图 → 深度图 → 送回 ZBrush
      [UV 翻转 (开关)  ] [诊断信息           ]   ← UV 翻转默认开(ZBrush 里会上下颠倒)
      [V2-S][V2-B][V2-L][V2-G]                  ← DA V2 四档**规模**单选组:一行四个,点一个其它自动关
```

## 加载

- **开发热更新**:ZBrush > ZScript 面板 > Python Scripting > Load > `scripts/load_plugin.py`
- **自动加载**:双击 `scripts/launch_zbrush_with_plugin.command`(从终端启动并带上 PYTHONPATH;
  macOS 上从 Dock 启动的 GUI 应用拿不到 shell 环境变量)

热更新的行为(实测,踩过坑):**顶层面板是复用的,每次 Load 只重建各功能的子面板**。
原因是 ZBrush 的界面操作和 `press` 一样进命令队列 —— `close` 掉脚本创建的 palette 之后
名字不会立刻释放,紧接着同名 `add_palette` 会返回 False(而 `delete_interface_item`
官方说明对 palette / sub-palette 无效),所以"关掉再重建"在同一个 ZBrush 会话里走不通,
只能重启 —— 现象就是"必须关掉 ZBrush 再 Load"。现在改成复用后,改完代码直接 Load 即可。
唯一代价:**某个功能的显示名改了,旧分区会留到重启为止**(拿不到"枚举子面板"的 API)。

## 结构

```
plugin/zbtools/
├── init.py              # 入口:sys.path + register()
└── zbtools/
    ├── app.py           # 装配(建面板、遍历 features)
    ├── ui.py            # 通用 UI + 提示(警告换底色)
    ├── settings.py      # 配置 + 状态版本
    ├── notes.py errors.py
    ├── zbapi/           # 共用:screen(屏幕)/ model(尺寸)/ canvas(画布)/ clipboard
    └── features/        # 功能:registry + 每个功能一个包
```
