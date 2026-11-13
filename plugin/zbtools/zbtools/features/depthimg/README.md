# 图片转深度图(depthimg)

把**选中模型的贴图**转成灰度**深度图**,并送回 ZBrush(默认替换模型贴图)。

和「贴图转色」的①②**没关系**:这里只做"图 → 深度图"这一件事;拿到图之后怎么用
(Alpha / 遮罩 / 置换 / 贴图)由你决定。

## 用法(一个按钮)

```
ZBTools
└── ---- 图片转深度图 ----
      [转深度图        ] [替换模型贴图 (开关) ]
      [UV 翻转 (开关)  ] [诊断信息           ]
      [V2-S][V2-B][V2-L][V2-G]      ← DA V2 四档规模的单选组:一行四个(每个 0.25 宽)
```

1. 点中那个**带 UV + 挂了贴图**的模型(低模)
2. 按 **转深度图**:
   * 插件**导出当前 SubTool** 来读它的贴图(ZBrush 会把贴图顺手写在 OBJ 旁边)
   * 外部管线算成灰度深度图:`<工作目录>/depthimg_depth.png`,**尺寸与贴图一致**(UV 对齐)
   * 送进 ZBrush;开关「替换模型贴图」开着(默认)时,用 ``Tool:Texture Map:Import`` 把这张图
     **直接挂到当前 SubTool 的贴图槽**——也就是模型的贴图变成这张深度图(并且会**核对**:
     贴图槽报出的尺寸 == 这张图的尺寸,才算换上了)
3. 之后随你:当 **Alpha**(`Alpha > Import`)、当遮罩、当置换贴图,或者直接 `polypaint` 用

| 控件 | 说明 |
|---|---|
| `替换模型贴图` | 开(默认)= 挂到当前 SubTool(替换它的贴图);关 = **只追加**到 Texture 面板,模型不动 |
| `UV 翻转` | 开(默认)= 输出前**上下翻转**。ZBrush 的图像是自下而上存的,原样导入会**颠倒**(实测);代价是这张 PNG 在预览/PS 里看着是倒的 —— 那是给 ZBrush 的朝向 |
| `V2-S` / `V2-B` / `V2-L` / `V2-G` | **DA V2 四档规模的单选组**(一组互斥开关 = 单选按钮):点一个就把其它三个关掉,写 ``depthimg__variant`` = `vits`/`vitb`/`vitl`/`vitg`(并把引擎切回 `dav2`)。**面板选的是规模** —— 选中的那一档权重不在磁盘上时**报错**,不拿别的规模凑合(见下) |
| `诊断信息` | 引擎 / DA V2 档位 / 权重与许可 / 解释器 / 依赖 / 路径 / 参数,复制到剪贴板 |

**只有出错才弹提示**;唯一例外是"深度图生成好了、但没能自动挂到模型上"——那时会弹一次
并告诉你手工怎么点。

## 面板上那四格 = DA V2 的四个规模

那四格选的是 **Depth Anything V2 的四档规模**:

| 格子 | `depthimg__variant` | 权重文件 | 参数量 | 许可 | 本机实测:2048x3072 贴图,@518 → **@1554** |
|---|---|---|---|---|---|
| `V2-S` | `vits` | `depth_anything_v2_vits_fp32`(95 MB) | 24.8M | **Apache-2.0 可商用** | 1.9s → **2.5s**,默认档 |
| `V2-B` | `vitb` | `depth_anything_v2_vitb_fp32`(372 MB) | 97.5M | CC-BY-NC-4.0 | 2.8s → **4.0s** |
| `V2-L` | `vitl` | `depth_anything_v2_vitl_fp32`(1.25 GB) | 335.3M | CC-BY-NC-4.0 | 5.0s → **8.1s** |
| `V2-G` | `vitg` | `depth_anything_v2_vitg_fp32`(4.7 GB) | 1.3B | CC-BY-NC-4.0 | 14.7s → **28.9s** |

* **档位之间的差距小于分辨率的差距**:同一尺寸下 vits→vitg 主要换来"边缘更稳、噪点更少",
  而 `size` 从 518 抬到 1554 是**肉眼可见的结构差别**(详见下面「决定质量的是推理边长」)。
* **为什么面板上没有 fp16**:上游(Kijai)自己写着 *"fp16 reduces quality by a LOT, not recommended"*;
  插件一律挑 fp32(同规格两张都在时,`find_model` 显式把 fp32 排前面 —— 文件名排序里 fp16 在前面,不排会踩)。
* **为什么面板上没有 metric**:`metric_hypersim`(室内 20m)/ `metric_vkitti`(室外 80m)是按**固定域**
  标定的米制权重,跨域跑会系统性错;这里只要**相对深度**(做浮雕/遮罩够用)。所以 `find_model`
  **一个 metric 都不自动挑**(只找到 metric 时当作"没找到",报错里告诉你下哪张)。要显式用它,
  只能 `depthimg__model` 指全路径。
* **选中的那档不在磁盘上 → 报错,不降级**。旧逻辑是"找不到就换别的规模凑合",于是"点了 V2-G
  却还是 Small"看起来就像"换了档没反应";现在会直接告诉你缺哪张、并给出照抄的下载命令。
  (例外:`depthimg__model` 显式指了全路径 = 你说了算,只在日志里提醒一句。)
* **许可**:四档里只有 V2-S 可商用;其余三档是 CC-BY-NC-4.0,默认**拒绝**,要在 settings.json 里
  把 ``depthimg__allow_nc_weights`` 设成 true(日志每次都会提醒"不可商用")。

## 引擎(不在面板上,但照样能用)

面板只放 DA V2 的四档;**引擎**仍然是四个,另外三个写 `depthimg__engine` 就能用
(这时面板四格**全灭** = 如实表示"现在跑的不是 V2";点任意一格就切回 V2 的那一档)。

许可依据:官方 README 原文 **"Depth-Anything-V2-Small model is under the Apache-2.0 license.
Depth-Anything-V2-Base/Large/Giant models are under the CC-BY-NC-4.0 license."**;
DA3 的 SMALL / BASE / METRIC-LARGE / MONO-LARGE 是 Apache-2.0,LARGE / GIANT / NESTED 是 CC-BY-NC-4.0。

| 引擎 | 许可 | 需要什么 | 本机实测 |
|---|---|---|---|
| `dav2`(面板上那四格) | 权重按规模:Small=**Apache-2.0**;Base/Large/**Giant** = CC-BY-NC-4.0(**默认拒绝**,要开 `allow_nc_weights`) | torch + `depth_anything_v2_*_fp32.safetensors`(vits 95 MB / vitl 1.25 GB / vitg 4.69 GB) | vits ~1.7s;vitl ~5s;vitg 18.0s |
| `da3` | **Apache-2.0**(MONO-LARGE) | torch + `depth_anything_3` 包 + 权重 1.27 GB(独立 venv) | **4.9s** |
| `luma` | 自研,无许可问题 | 只要 numpy + pillow | 0.06s |
| `sdf` | 自研,无许可问题 | 再加 scipy | 0.2s(半径默认按分辨率自动) |

* 面板上那四格是**单选组**(ZBrush 的 SDK 里没有 radio 控件,只有 button / switch / slider,
  所以"互斥"是自己维护的:点一个就 `set` 其它三个为 0;再点当前那个不会把它点灭)。
* 找到 CC-BY-NC 权重(vitb/vitl/vitg、DA3-LARGE…)时**会拒绝**,并告诉你怎么换成可商用的
  V2-S;确实只给自己用的话,把 `depthimg__allow_nc_weights` 设成 `true` 就能放开。


### 想用 ComfyUI 里那套更好的权重(vitb / vitl / vitg)

**同一批 safetensors 文件**,插件的 vendored 代码能严格加载(实测 vitg 631 个键全对上)。
现在面板上直接点 `V2-B` / `V2-L` / `V2-G` 就是它 —— 只需要先放开 NC 权重这一步:

```json
{ "depthimg__allow_nc_weights": true }        // 它们是 CC-BY-NC-4.0,只能自用
```

然后点面板上那一格即可(点它 = 写 `depthimg__variant`,并保证引擎是 `dav2`);
想固定用某个不在这四档里的权重(例如 metric 版)才需要 `depthimg__model` 指全路径。

**实测各自的代价**(同一张 853x1280 的贴图,只换权重、分辨率都固定在 518):

| 权重 | 耗时 | 备注 |
|---|---|---|
| `vits` fp32(默认,Apache-2.0) | **2.1s** | 够用;背景最平 |
| `vitl` fp32(CC-BY-NC) | ~5s | 边缘更稳 |
| `vitg` fp32(CC-BY-NC,4.7 GB) | **18.0s** | 同一尺寸下档位带来的差别有限;画质的大头是 `size`(见下) |

fp16 权重:上游(Kijai)自己写着 *"fp16 reduces quality by a LOT, not recommended"*。
插件一律**优先挑 fp32**(同规格两张都在时),面板上也不放 fp16;只剩 fp16 时说明里会提醒。

### 引擎怎么选(实测)

| 输入 | 选什么 |
|---|---|
| 照片、真实物体(有真实景深) | **先把 `size` 拉满**(本机 1554),再在面板上从 `V2-S` 往上抬 —— 档位是次要旋钮 |
| **平铺图案、贴花**(壁纸花纹这种) | **`sdf`**(要在 settings.json 里把 `depthimg__engine` 写成 `sdf`)—— AI 深度在这种图上只会给一团模糊(模型把相邻图案当成"远近不同的物体"),距离场反而干净 |
| 本来就是高度图的灰阶图 | **`luma`**(`depthimg__engine: "luma"`) |
| 要更强的结构理解 | **`da3`**(`depthimg__engine: "da3"`;几何一致性更好,但**不会更锐**) |

写 `da3` / `luma` / `sdf` 时面板四格全灭 —— 那是如实显示"现在跑的不是 V2";点任意一格就切回 V2。

**方向统一**:DA V2 输出视差式(越大越近),DA3 直接预测 depth(越大越远)—— 管线把 DA3
倒相,所以不管哪个引擎**亮 = 近/高**;要反过来用 `depthimg__invert`
(用"透视地面 + 近大远小柱子"的合成图逐一核对过)。

## 隐藏配置(不在面板上)

改 `plugin/zbtools/zbtools/settings.json`:

| 键 | 默认 | 用途 |
|---|---|---|
| `depthimg__engine` | `dav2` | `dav2` / `da3` / `luma` / `sdf`。**只有 `dav2` 在面板上**(面板那四格是它的档位) |
| `depthimg__variant` | `vits` | **面板上点的是它**:`vits`(V2-S)/ `vitb`(V2-B)/ `vitl`(V2-L)/ `vitg`(V2-G)。选中的档位没权重时**报错**,不降级 |
| `depthimg__model` | 空 | 显式指定权重:`dav2` → safetensors 全路径(例如 metric 版);`da3` → 仓库 id 或本地目录。**显式指定时以它为准**(和面板档位不一致只在日志里提醒) |
| `depthimg__allow_nc_weights` | `false` | 放开 CC-BY-NC 权重(vitb/vitl/vitg、DA3-LARGE 等;只自用!) |
| `depthimg__invert` | `false` | 反过来(亮 = 远) |
| `depthimg__clip` | `0.5` | 归一化时两端各裁掉的百分位(防一个极值像素压平整张图) |
| `depthimg__size` / `depthimg__size_max` | `518` / `1554` | dav2 的推理边长(**这是决定画质的头号参数**,详见下面「决定质量的是推理边长」)。`0` = 自动跟贴图走(上限 `size_max`);手填的值自动归整到 14 的倍数。**本机 settings.json 已设成 1554** |
| `depthimg__bit_depth` | `8` | 输出位深;做**位移/置换**时改 `16`(另写一张 16 位 TIFF;PNG 永远 8 位) |
| `depthimg__da3_res` | `0`(自动) | DA3 的 `process_res`;**0 = 跟输入长边走(夹在 [504,1512])** |
| `depthimg__detail` | `0` | 叠加原图细节的比例;**默认不叠**(只对平涂线稿有用,对带明暗的图会变麻点);想开就写 0.1~0.3 |
| `depthimg__despike` | `true` | 去孤立极值(DA3 的纯黑色块);关掉加 `--no-despike` |
| `depthimg__despike_robust` | `6.0` | 去极值的判定倍数;**越大越温和**(4 会在噪声图上过度替换:dav2 上 0.52% → 阈值 6 时 0.34%) |
| `depthimg__no_guided_upsample` | `false` | 关掉"以原图为引导"的上采样(默认开着) |
| `depthimg__bg` | `white` | 输入带 alpha 时先合成到底色(`white`/`black`/`none`) |
| `depthimg__sdf_radius` | `0`(= 自动) | sdf 引擎的倒角半径(像素);**0 = 按贴图短边的 0.6%(最小 8px)** |
| `depthimg__use_texture_export` | `false` | 读贴图走**快路径**(`Tool:Texture Map:Export`,不导出网格);先用探针确认过再开 |
| `depthimg__flip_v` | `true` | 同面板「UV 翻转」(给 ZBrush 的朝向,默认开) |
| `depthimg__interpreter` | 空 | 跑深度管线的解释器;空 = **按引擎自动配对**(见下) |
| `depthimg__comfy_dir` | 空 | ComfyUI 根目录(空 = 自动找:复用它的 venv 与已下载权重) |
| `depthimg__timeout_s` / `io_wait_s` / `import_wait_s` | 900 / 300 / 2.0 | 管线超时 / 等导出落盘 / 导入后等多久再挂贴图 |
| `depthimg__work_dir` | 自动 | 工作目录(跨重启复用) |

**解释器自动配对**:`dav2` 只要 `torch`(ComfyUI 的 venv 通常就有 → **零安装**),
`da3` 还要 `depth_anything_3` 包(一般在 `~/.venvs/da3`)。插件按引擎挑**装了对应包**的那个,
所以换引擎不用改两个键还改错一个。

## 替换模型贴图:先保证图进面板,再去挂,而且**要核对**

步骤(每一步都写在日志里):

1. **一定先** `Texture:Import` 把图导入 **Texture 面板**,并把面板的"当前贴图"切到刚导入的那张
   (`Texture:Item Info` 推到最大)—— 你要的"图在 ZBrush 里"就是这个
2. 「替换模型贴图」开着时,**按官方文档的做法**挂上去:

   ```
   zbc.press("Tool:Texture Map:TextureMap")   # 按贴图槽 → ZBrush 弹出贴图列表
   zbc.click("PopUp:<贴图名>")                 # 在弹窗里点那张贴图
   ```

   官方文档原文:"Click on the Texture Map thumbnail to select a texture map from the main
   Texture palette **pop-up**" —— 所以**光按方块没用**(用户实测印证),必须在弹窗里点一条。
   配方来自 ZBrushCentral 的现成 ZScript(ZBrush 4R7 时代写的,路径到 2026.2 依然可用):

   ```
   [IPress,Texture:Import]
   [ISet,Texture:Item Info,[IGetMax,Texture:Item Info]]        ; 选中最新的那张
   [VarSet,txtrName,[IGetTitle,Texture:Item Info]]
   [VarSet,txtrName,[StrExtract,txtrName,0,[StrLength,txtrName]-2]]
   [IPress,Tool:Texture Map:TextureMap]
   [IClick,[StrMerge,"PopUp:",#txtrName]]
   ```
3. 核对判据:**贴图槽的名字变没变**(`get_title("Tool:Texture Map:Texture Map")` 原来显示
   那张贴图的名字,比如 "323")

> ⚠⚠ **不能用尺寸核对**(踩过,而且很致命):深度图是照着模型原来那张贴图算出来的,
> **尺寸当然一样**(实测两边都是 2749x3684)—— 用尺寸当判据会**永远判成功**,
> 于是插件以为挂上了、跳过了"导入面板",用户看到的就是"报错 + 没有生成任何贴图"。
> 所以只认名字;名字读不到就老实说"无法核对",绝不假装成功。

核对这一步是整套东西的关键:ZBrush 的界面操作全是**命令队列**,按下去不等于做成了。

## 两个 AI 引擎的短板不一样,默认值按这个定(实测)

| 引擎 | 用户看到的问题 | 实测根因 | 默认设置 |
|---|---|---|---|
| **DA3** | "一片片纯黑色块" | ① 低分辨率下**细发丝/细结构被并进背景**(归一化后掉到 0);② 头发/边缘会蹦出孤立极值 | ① `da3_res` 默认**自动跟输入走**(夹在 [504,1512]):体内黑岛 **16 处 → 1 处 → 0 处**(504/1008/1512),耗时只 +1.6s;② **去孤立极值**(`despike`,默认开、阈值 6) |
| **V2 系列** | "朦胧/只有剪影" | **模型实际看到的像素太少**:`size` 停在 518 时,2048x3072 的贴图被压成 **336x518**(像素不到 1/36),细节在推理前就没了 —— 不是模型"太平滑" | `size` 设 **1554**(本机已设);理由与实测见下面「决定质量的是推理边长」 |


### 关于"叠加原图细节"(`detail`):**默认关**

一度想用它治 V2-S 的朦胧(平涂线稿上确实有效:局部对比度 +28%),但**对带明暗的图/3D 渲染有害** ——
那里的"高频"是光照和贴图噪声、不是高度,叠上去就是一层麻点(用户实测:明显劣化)。
所以默认 `0`;**只有平涂线稿**想要线条更清楚时才开(0.1~0.3)。

### 什么治不了:**背景里的亮块/条纹是模型自己的失真**

比如同一张 3D 渲染上,DA3 会在角色两侧**凭空造出一块近处墙面**;V2 系列会把背景画成 streak 状。
这是单目深度模型在这种图上的固有偏差(它必须给每个像素一个深度,背景没有信息时就"编"),
调参数治不了 —— 要么用 `浮雕`/`luma` 这种**基于图像本身**的引擎(它们不会凭空造几何),
要么接受它(当遮罩/Alpha 用时通常无感)。

补一句实测数字:**V2-L / V2-G 在这些图上的收益很小**(同一尺寸下换档位,人物内起伏只差几个
百分点),**大头在推理边长**(见下)。

## 决定质量的是推理边长(实测)

**先说结论:同一份权重、同一张贴图,画质的主要变量是"模型实际看到多少像素"。**

`size` = "等比缩放到短边多少":贴图先在 CPU 上缩到这个边长,**模型只看到缩完的图**,
算完再插值回原尺寸(UV 对齐靠这一步)。所以 `size` 设小了,**细节在进模型之前就没了** ——
再怎么换档位、叠细节都补不回来。

本机实测(2048x3072 贴图,走插件生产路径,`detail=0`,第二遍取热态):

| 档位 | @518(模型看到 **336x518**) | @1554(模型看到 **1036x1554**) |
|---|---|---|
| `vits` | 1.9s | 2.5s |
| `vitl` | 5.0s | 8.1s |
| `vitg` | 14.7s | 28.9s |

对照图:`_probe/depthprobe/depthimg_size_matrix.png`
(左→右 = vits@518 | vits@1554 | vitl@518 | vitl@1554 | vitg@518 | vitg@1554)
—— 三对里 **@1554 的轮廓(猫耳、手臂、泳池扶梯、身体边缘)都明显收得住**,@518 发虚、还常有一条腿过曝。

### 为什么不能更高:1554 是这份代码的天花板

vendored 的 `dinov2_layers/attention.py` 用朴素实现 `attn = q @ k.transpose(-2, -1)`,
**显存 O(N²)**(N = 高/14 × 宽/14)。2048x3072 按原生跑 → N≈31,974 → 要 **91 GiB**,
实测直接抛 `RuntimeError: Invalid buffer size: 91.41 GiB`。

你 ComfyUI 那个节点(`comfyui-depthanythingv2`)能按原生分辨率跑,是因为它那份 attention
走 ComfyUI 的 `optimized_attention`(O(N))。**同权重、同图:一边被显存钉在低分辨率、
一边能上原生 —— 这就是"在 ComfyUI 里效果好得多"的真正原因**(不是插件的槽位没挂上)。

突破这条路需要的改动很小:那一层换成 `nn.functional.scaled_dot_product_attention(q, k, v,
scale=1.0)` 即可(**实测与朴素实现逐像素一致**,相关 1.000000),显存从 O(N²) 降到 O(N);
细节记在 `tools/vendored/depth_anything_v2/PATCHES.md`。

### 另外两个旋钮(仍然成立)

| 改什么 | 效果 | 耗时 |
|---|---|---|
| 8 位 → 16 位输出 | 对"糊"**没有帮助**;只影响台阶(级数 256 → 65536) | ≈0(只多写一个文件) |
| 双线性 → 引导滤波上采样 | 深度边缘与原图边缘相关性 0.37 → 0.50(边缘更贴图) | ≈0(测不出来) |

真正省时间的杠杆在别处:

- **别导出大网格**:`depthimg__use_texture_export: true` 跳过"导出 SubTool",直接导 Texture 面板那张图
  (省掉几十 MB 的 OBJ 与等待);代价是少了"点数核对"这道保险。
- **图案/贴花别用 AI**:走 `sdf`(配置里写 `depthimg__engine: "sdf"`)比 AI 快一倍而且更锐。

## 进度反馈:整条链路喂给 ZBrush 顶部状态条(2026-09)

`set_notebar_text(text, progress)` 官方就叫"进度条"(*"the equivalent of a progress bar in
other applications"*)。真机探针(`scripts/probe_notebar.py`,ZBrush 2026.2)实测的三条结论:

* 在脚本回调里循环更新它**会自动刷新** → **不必**每帧搭 `zbc.update(1, redraw_ui=True)`;
* **等子进程期间也能刷新** → 外部管线的进度可以映射进来;
* 单次调用约 4ms、文字超过 ~80 字会被截。

所以本功能的等待被切成四段(`ui.Progress`,节流 100ms):

| 阶段 | 进度 | 怎么来的 |
|---|---|---|
| 读贴图(等 ZBrush 导出 SubTool) | 0 → 0.15 | `toolio.export_active(..., on_tick=)` 每 0.2 秒爬一次 |
| 外部管线(读权重 / 推理 / 后处理 / 写盘) | 0.15 → 0.95 | 管线日志映射成阶段跳变;管线报的 `[zbtools-progress] 0.42` 优先 |
| 送回 ZBrush(导入面板 + 挂到 SubTool) | 0.95 → 1.0 | 两三个瞬间跳变 |
| 收尾 | 清空 | `Progress.done()` → `set_notebar_text("", 0)` |

**阶段之间按时间爬升**(最典型:一次 dav2 推理要十几秒、期间一行日志都没有),但**受天花板
限制、永不越过下一个里程碑** —— 不假报进度。管线那边的进度行格式是 `[zbtools-progress] 0.42`
(见 `ui.PROGRESS_PREFIX`);插件认它、**不**把它当日志打给用户。

> 附带好处:现在子进程的输出是**边跑边打**到 ZBrush 控制台的(以前要等它跑完一次性回显)。

## 读贴图前会**验明正身**(不然会拿到上一个模型的图)

实测踩到的 bug:**换到另一个模型后,生成的深度图还是上一个模型的**。根因有三条,都在 ZBrush 侧:

1. **ZBrush 导出 OBJ 时写出来的贴图是 "Texture 面板当前选中" 的那张** —— 而插件上一次会把
   生成的深度图导入面板并选中它(`Texture:Item Info` 推到最大)→ **下一次换模型导出时写出来的
   还是那张深度图**。两张图尺寸相同时,连"尺寸核对"都拦不住(实测就是这么漏过去的)。
   → 所以导出前必须先把面板的"当前贴图"**挪到这个模型自己那张**上(见下)。
2. **没 Clone 过的贴图导不出来** —— 官方文档 Tool > Texture Map 原文:
   *"It is necessary to **clone** a texture like this if you wish to **export** it from ZBrush."*
   面板里没有它时,导出写出来的就是别的贴图。
3. ZBrush 的导出是**命令队列**、而且文件名固定 → 残留文件可能被当成本次结果。

所以按下按钮后会走这 4 道核对(+ 1 个自动修复),任何一条不过就**报错停下、不生成任何图**:

| 何时 | 核对什么 | 不过怎么办 |
|---|---|---|
| 动手前 | `query_mesh3d` 记下当前 SubTool 的**点数 + 包围盒**;读贴图槽的**名字与尺寸** | 贴图槽是空的 → 直接告诉你"没挂贴图" |
| 动手前 | **把面板的"当前贴图"挪到这个模型自己那张上**:扫 `Texture:Item Info` 按名字找 → 找不到就按 `Clone Txtr` 再选最新那张 → 核对面板当前名 == 模型挂的贴图名 | 对不准就报错停下(并告诉你怎么处理);确实老路子对你更好时可以 `depthimg__skip_texture_focus: true` 跳过 |
| 导出后 | 数 OBJ 里的 `v` 行,**必须等于当前 SubTool 的点数**(实测 ZBrush 导出的 `v` 行数 == 点数) | 报错:"导出的模型不是当前选中的那个(3 vs 999)" |
| 导出后 | OBJ 里有没有 `vt` 行(= 有 UV) | 报错:让点中带 UV 的低模 |
| 导出后 | 贴图必须**是刚写出来的**(mtime ≥ 导出开始),而且**尺寸 == 贴图槽说的尺寸** | 报错并点名:"这个模型的贴图没 Clone → 按一下 `Clone Txtr` 再试" |
| 没导出贴图 | —— | **自动补按一次 `Clone Txtr`** 再导一遍(不用你手动) |

输出文件名 = **贴图名 + 引擎(含 DA V2 档位)+ 内容短码**,例如
`depthimg_depth_323_dav2-vitl_ab12cd.png`(零依赖引擎没有档位后缀:`…_323_sdf_ab12cd.png`):

* 换模型 / 换引擎 / **换档位** → 名字不同,一眼能看出是新结果,也能看出是**哪一档**跑的;
* **必须这样**(实测踩到):ZBrush 的 Texture 面板**按名字去重**,同名导入它会用列表里
  **第一张同名的旧图**,于是"切了引擎却没反应、深度图没区别";
  四格共用引擎名 `dav2`,所以名字里**必须带档位**(`dav2-vits`/`dav2-vitb`/`dav2-vitl`/`dav2-vitg`),
  否则 V2-S 换 V2-L 的文件名只差内容短码,排障时看不出是哪一档跑的;
* 内容相同则同名(重复跑不会在面板里堆同图)。

另外:每次按下会把**上一次的深度图先删掉** —— 免得这次其实失败了、而工作目录里还留着一张旧图,
看着就像"生成了上一个模型的图"。

## 读贴图:两条路

ZBrush 的 Python SDK **没有任何贴图 API** —— `query_mesh3d` 只给点数/面数/包围盒/**UV 框**,
`get_subtool_status` 只给眼睛/体积那几种图标标志。不过**实测发现了两条能用的读数**
(ZBrush 2026.2,见 `scripts/probe_texture_paths.py`):

* **贴图槽的 tooltip 就是贴图信息**(不用导出网格!):

  ```
  get_title("Tool:Texture Map:Texture Map") → "323"
  get_info ("Tool:Texture Map:Texture Map") → "Texture Map\n\n323\n\nWidth=2749\nHeight=3684\nDepth=32 (RGB)"
  ```

  → 于是"这个 SubTool 挂没挂贴图、挂的是哪张、多大"直接可读(诊断信息里会写出来)。
* ⚠ **贴图方块的值(`zbc.get`)永远是 0.0**(挂没挂都是),没有信息量 —— 别拿它判断。

读贴图的**像素**还是只有两条路:

| | 做法 | 代价 | 可靠性 |
|---|---|---|---|
| **默认** | 导出当前 SubTool,ZBrush 顺手把贴图写在 OBJ 旁边 | 大模型要等导出(几十秒/几十 MB) | **稳**:不依赖任何假设 |
| **快路径**(可选) | Tool 层的贴图导出(`Tool:Texture Map:Export`,其次 `Texture:Export`)直接把贴图写成文件 | 秒出,不碰网格 | 取决于那条命令导的是不是当前 SubTool 的贴图 —— 要先用探针比过 |

快路径由 `depthimg__use_texture_export`(默认 **false**)打开;打开后失败会**自动退回**默认路径,
不会卡住。要怎么知道你这台 ZBrush 上快路径可不可信?跑一次:

```
ZBrush > ZScript > Python Scripting > Load > scripts/probe_texture_paths.py
```

先点中那个**有 UV + 挂了贴图**的模型再跑 —— 它会:读贴图方块的值 → 用 `Texture:Export` 导一张
→ 再用老办法导一次 → **对比两者尺寸**(一致就说明面板确实跟着 SubTool 走,快路径可信),
顺带把真实存在的 item path 全列出来(导入贴图 / 挂到 SubTool 那几条)。

另外,插件会读 ``Tool > Texture Map`` 那个贴图方块的**值**来判断"这个 SubTool 到底挂没挂贴图":
读得到且是 0 → 直接报错让你先挂贴图(不猜);读不到 → 返回"未知"而**不是**"没有"
(上一版踩过的坑:两个 -1 相等就当成"有")。

## 原理

```
选中模型 → Tool:Export → depthimg_model.obj(+ ZBrush 顺手写出的贴图 .bmp/.mtl)
                              ↓
      tools/depth_pipeline.py(vendored 的 DA V2 模型定义 / DA3 官方包)
        等比缩放到 `--size` 的边长(默认跟配置:本机 1554;上限 `size_max`)→ 推理 → 插值回**原尺寸** → 8 位灰度 PNG
        归一化:按百分位裁剪后拉伸;带 alpha 时先合成到白底
                              ↓
      Texture:Import(导入 Texture 面板)→ Tool > Texture Map 的贴图方块(挂到 SubTool)
```

管线与插件**零耦合**(只吃 argv、只打 stdout),可以单独跑:

```bash
.venv/bin/python plugin/zbtools/zbtools/features/depthimg/tools/depth_pipeline.py \
    --in 贴图.png --out 深度.png --engine sdf --report-json 报告.json
```

## 实测结论(真机验证过的坑)

| # | 结论 | 影响 |
|---|---|---|
| 1 | **ZBrush 的 Python SDK 里没有任何贴图 API**(145 个命令里一个 texture 都没有) | 读贴图只能"导出 SubTool,让 ZBrush 顺手把贴图写出来";导入/挂载只能按 `父面板:子面板:项` 的命名习惯试候选路径 + `zbc.exists()` 探测 |
| 2 | 上游 DA V2 代码 `import cv2`,Kijai 的 ComfyUI 版还 `import comfy.ops` + ComfyUI 的 `optimized_attention` | vendored 一份**只依赖 torch+numpy+pillow** 的(改动见 `PATCHES.md`);**同一输入尺寸下输出与 ComfyUI 那份逐像素一致**(实测相关 1.000000),但注意力**退回了朴素 `q@k`** —— 后果见第 36 条 |
| 3 | DA3 官方包的 `api.py` 会连带拉起**导出用**的重依赖(moviepy/open3d/plyfile/evo,≈1 GB;xformers 在 macOS 上没有轮子) | 用"一调用就报错"的空壳顶掉用不到的子模块 → 轻装 venv 就能跑;它自带的 logger 要 `DA3_LOG_LEVEL=WARN` 才不会往 ZBrush 控制台喷彩色 INFO |
| 4 | 两个引擎深度方向**相反**(DA V2 视差式 / DA3 直接预测 depth) | 管线把 DA3 倒相,统一成"亮 = 近/高" |
| 5 | 导出文件名固定 → 上一次的贴图会**被当成这次的结果** | `read_model_texture()` 先把上次的 obj/mtl/bmp/_TXTR.tif 全删掉再导出(测试钉死) |
| 6 | 没 UV 的网格 ZBrush 只写一个 `_TXTR.tif`(空白) | 导出后先查 OBJ 里有没有 `vt` 行,没有直接报错,别白等一张空白深度图 |
| 7 | `dav2` 的耗时几乎全在**读权重**(vitl 1.25 GiB→3.5s);vits 只有 95 MB | 默认用 vits:1.6s 跑完,而且它是**唯一可商用**的规模 —— 快和合规这次是同一边 |
| 8 | `.pyc` 与解释器**主版本绑定**(打包 3.11,而深度引擎常跑在 ComfyUI 的 3.13 venv 里) | `tools/depth_pipeline.py` 与 `tools/vendored/` **带源码**发行(`package_plugin.py` 白名单) |
| 9 | 平铺图案没有"景深" | 见上面「引擎怎么选」:把 `depthimg__engine` 写成 `sdf` 才对路 |
| 10 | ZBrush 的 Python SDK 没有贴图 API(`query_mesh3d` 只到 UV 框),**但贴图槽的 tooltip 里带着贴图名与尺寸** | 不用导出网格就能判断"挂没挂、是那张、多大";像素仍要导出 SubTool 或走快路径 |
| 11 | **贴图方块的值永远是 0.0**(挂没挂都读 0) | 判断"挂没挂"只能靠 tooltip,别信那个读数(测试里钉死) |
| 15 | **深度图和原贴图的尺寸永远一样**(它就是照原贴图算的)→ 用尺寸核对"贴图槽换没换"会**永远判成功** | 核对只能看**贴图槽的名字**;这条错误判据真实发生过(表现为"报错 + 没有生成任何贴图",因为插件以为挂上了就跳过了面板导入) |
| 16 | `Tool:Texture Map:Import` 是"导进 **SubTool 的贴图槽**",不是"导进 Texture 面板" | 两者必须分开:面板导入只认 `Texture:Import`,否则"追加到面板"会变成"悄悄替换模型" |
| 13 | **贴图槽是个"弹窗选择器"**:按下去只会弹出贴图列表,光按它**不会替换模型贴图**(用户实测);必须在弹窗里点一条(`zbc.click("PopUp:<贴图名>")`) | 替换的配方:`Texture:Import` → `Texture:Item Info = get_max(...)`(选最新)→ 按 `Tool:Texture Map:TextureMap` → `click("PopUp:<名字>")`;来源:官方文档 + ZBrushCentral 的现成 ZScript |
| 27 | **DA3 的"纯黑色块"是低分辨率把细结构并进背景**:504 时体内黑色小岛 16 处/12480px,1008 → 1 处/244px,1512 → **0 处** | DA3 的 `process_res` 默认改成跟输入走。**dav2 同理 —— 推理边长对它一样是头号参数**(见第 35 条;原文"dav2 不需要"是错的) |
| 35 | **决定画质的是推理边长,不是档位**:2048x3072 的贴图在 `size=518` 下被压成 **336x518**(像素不到 1/36);改 1554 → **1036x1554** | 三档(vits/vitl/vitg)在 1554 下轮廓都明显收得住(对照图 `_probe/depthprobe/depthimg_size_matrix.png`);⚠ **标量指标不可靠**:同一次实测里 vitg@518 的"人物内 std"甚至比 @1554 高,而图上明显 @1554 好 —— 判画质只能看图 |
| 36 | **分辨率的上限是注意力的显存**:vendored 的 `attention.py` 是朴素 `q@k`(物化 N×N),2048x3072 → N≈31,974 → 要 **91 GiB**(实测 `RuntimeError: Invalid buffer size: 91.41 GiB`);ComfyUI 那份走 `optimized_attention`(O(N)),所以它能按原生分辨率跑 | 想在插件里上更高分辨率:把那三行换成 `nn.functional.scaled_dot_product_attention(q, k, v, scale=1.0)`(**实测与朴素实现逐像素一致,相关 1.000000**,只是显存降到 O(N)),再放开 `size_max` |
| 31 | **`detail`(叠加原图细节)只对平涂线稿有效**:带明暗的图/3D 渲染上,原图高频是光照不是高度 → 变麻点(用户实测劣化) | 默认关(0);想要线条清晰只对线稿开 0.1~0.3 |
| 32 | 手动 `--size` 不是 14 的倍数会让 DINOv2 的 patch 直接崩 | 统一归整到 14 的倍数(1024 → 1022) |
| 33 | 换过引擎后模型贴图就是**我们生成的深度图**,它的名字又被当成"贴图名"标签 → 文件名一层层套娃(`depthimg_depth_depthimg_depth_…`) | 取标签时先剥掉自己的前后缀,不再套 |
| 28 | **叠原图细节(`detail`)能让局部对比度上来**:平涂线稿上 0.3 → +28%、0.5 → +80% | 叠加的是原图细节(线条变成浅凹槽),不是伪造深度。**默认关(0)**;`-1` 才是"自动 = AI 0.3 / 零依赖 0"(管线 CLI 的默认值是 -1,插件传的是配置值 0) |
| 29 | 去孤立极值用"中值 + 稳健离群判定";大片平坦图上 MAD 会退化成 0 | 加下限(深度跨度的 0.2%,跨度也退化时退回 min/max),否则平坦区一个极值都抓不出来 |
| 26 | **ZBrush 的 Texture 面板按名字去重**:导入与已有贴图同名的文件时,它用**列表里第一张同名的**(旧的)—— 文件名固定 → 换引擎再跑,面板/模型上还是旧图,表现就是"切了引擎没反应、深度图没区别"(实测踩到) | 输出名改成 **`depthimg_depth_<贴图名>_<引擎[-档位]>_<内容短码6位>.png`**:内容一样同名(不堆垃圾)、内容不一样必不同名(不会撞)。16 位 TIFF 与报告一起改名 |
| 38 | 面板四格**共用引擎名 `dav2`** → 只写引擎名的话"V2-S 换 V2-L"文件名只差内容短码,看不出是哪一档 | 文件名里带**档位后缀**:`dav2-vits` / `dav2-vitb` / `dav2-vitl` / `dav2-vitg`(`service.engine_label()`);取贴图标签时那一段正则也要容得下 `-xxx`,否则文件名会套娃 |
| 25 | 位宽提升对"糊"没有帮助,只影响台阶(256 → 65536 级) | `bit_depth` 默认 8;做位移/置换时再打开 16 |
| 23 | sdf 的整数距离场会让斜坡只有"半径那么多级" | 用模糊 mask 补**亚像素** + 最后 0.7px 平滑,斜坡变连续 |
| 24 | 引导滤波上采样让深度边缘更贴原图(相关性 0.37 → 0.50) | 默认开;`depthimg__no_guided_upsample` 可关 |
| 21 | **sdf 的倒角半径不能写死像素**:同一张 2749x3684 的插画,8px 只剩轮廓线、24px 才是能雕的圆浮雕 | 默认改成**按贴图短边的 0.6%(最小 8px)**(2749 → 16.5px);图案笔画宽度本来就随分辨率一起变大,所以按比例才对 |
| 20 | **ZBrush 导出 OBJ 时写的是"Texture 面板当前选中"的那张贴图**(插件自己上一次导入的深度图就会被选中)→ 换模型后导出的还是那张 | 导出前必须把面板"当前贴图"挪到模型自己那张(`Texture:Item Info` + 按名字扫面板,找不到就 `Clone Txtr`);输出文件名带上贴图名,免得看着像同一张 |
| 18 | **换了模型却拿到上一个模型的图**(实测):① 没 Clone 的贴图 ZBrush 导不出来,导出的是面板里那张别的贴图;② 固定文件名 + 命令队列可能拿到残留文件 | 动手前记点数/包围盒与贴图尺寸,导出后逐条核对(点数、UV、贴图新鲜度与尺寸),不过就报错停下;没导出贴图时自动补按 `Clone Txtr` |
| 19 | ZBrush 导出的 OBJ 里 `v` 行数 == 这个 SubTool 的点数(UV 接缝只多写 `vt`,不拆 `v`) | 有了一个便宜可靠的"这是不是同一个模型"指纹(数行就行) |
| 17 | 贴图名从 `get_title("Texture:Item Info")` 拿,末尾常带一个句点 | 弹窗里那条目的名字要对上(`PopUp:<名字>`),所以句点要去掉;名字格式各版本不一样 → 代码里会一次试几个候选,每个都核对贴图槽 |
| 14 | ZBrush 里贴图**上下颠倒**(ZBrush 的图像自下而上存) | 默认 `--flip-v` 输出前翻一次(`UV 翻转` 开关,默认开);代价是普通看图软件里这张 PNG 是倒的 |
| 12 | 实测存在的 item path:`Texture:Import/Export/New/Remove`、`Tool:Texture Map`、`Tool:Texture Map:Texture Map`、`Tool:Texture Map:Texture On`、`Tool:Texture Map:Import/Export`、`Tool:UV Map:Texture Map` | 候选表按这个钉死(仍保留 `exists()` 探测以兼容别的版本) |
| 34 | 面板那四格从"四个引擎"改成"**DA V2 四档规模**"之后,旧的"找不到就换别的规模"变成**危险的静默降级**(点了 V2-G 其实跑 Small,看起来就是"换了档没反应") | `run_depth` 用 `variant_note` 把规模对不上**顶回去并给出下载命令**;metric 权重一个都不自动挑(`is_metric_weight`) |

## 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| 报"**没能把 Texture 面板的当前贴图挪到这个模型挂的那张上**" | 面板里没有这张贴图(没 Clone 过) | 选中 SubTool → `Tool > Texture Map` → 按 **Clone Txtr** 再试;或 `depthimg__skip_texture_focus: true` 跳过这一步(只打警告) |
| 报"读出来的贴图**不是这个模型挂的那张**"(尺寸对不上) | 官方文档:没 Clone 的贴图 ZBrush 导不出来,导出的是面板里那张别的贴图 | 选中这个 SubTool → `Tool > Texture Map` → 按 **Clone Txtr** → 再按一次「转深度图」(插件在"一张都没导出来"时会自己补按一次) |
| 报"导出的模型**不是当前选中的那个**" | ① 上一次导出还没跑完 ② 中途切了模型/SubTool | 重新点一下模型再按一次;这次**不会**生成任何图 |
| 报"这个模型**没有 UV**" | 点中的是 DynaMesh 高模(没有 UV) | 点中**带 UV + 挂了贴图**的低模再按 |
| 报"读不到这个模型的贴图" | 那个 SubTool 没挂贴图(或导出没写出贴图) | 确认低模真的挂了贴图;或用 `depthimg__bg` 之外的方式先给它贴上贴图 |
| 报"需要 torch" | 当前解释器没有 torch | `depthimg__interpreter` 留空(自动找 ComfyUI 的 venv),或给插件解释器装 `pip install torch` |
| 报"没找到 V2-x 的权重" | 那一档**真的不在磁盘上**(报错里会点名缺哪个文件) | 按错误里给的 `hf_hub_download` 命令下那一张,放到 `~/.zbtools/models/`(或 ComfyUI 的 `models/depthanything/`);不想下就点面板上已经有的那一档 |
| 报"面板选的是 V2-x,磁盘上也有 … 但它属于 CC-BY-NC-4.0" | 选的是 V2-B / V2-L / V2-G,权重在磁盘上,但许可闸默认不放行 | 点回 `V2-S`,或把 `~/.zbtools/settings.json` 的 `depthimg__allow_nc_weights` 设成 true 后重启 ZBrush(只自用) |
| 报"当前档位是 V2-x,但磁盘上找到/要用的是 …" | 只有显式 `depthimg__model` 指的全路径与面板档位不一致时才会提醒(日志,不拦) | 想用面板那一档就把 `depthimg__model` 清空 |
| 报"没能自动把贴图挂到模型上" | 两条路都没核对上(界面项不存在,或按了但贴图槽**名字**没变);日志里会打印每次尝试前后的 `贴图槽:名字(尺寸)` | 跑 `scripts/probe_texture_paths.py` 第 6 节看哪条路能换上;手工做法:选中 SubTool → 在 Texture 面板点中那张深度图 → `Tool > Texture Map` 点贴图方块 |
| 图进了 Texture 面板但模型贴图没变 | 就是"光按方块不管用"那个实测 | 升级后插件会自动走 `Tool:Texture Map:Import` 并核对;若仍失败,把探针第 6 节的输出发我 |
| 贴图上下颠倒了 | ZBrush 的图像自下而上存 | 「UV 翻转」默认已经开着;万一反了,关掉它再按一次 |
| 深度图一团糊、只有剪影 | ① **先查 `depthimg__size`**(默认曾长期是 518 → 2048x3072 的贴图被压成 336x518,细节在推理前就没了);② 才是"平铺图案没有真实景深" | ① 设成 1554(本机已设),重载插件再看;② 图案类把 `depthimg__engine` 写成 `sdf`(距离场浮雕) |
| 浮雕的倒角太细/太平 | 倒角半径(= sdf 半径)相对贴图太小或太大 | 默认按短边 0.6% 自动;想手动写 `depthimg__sdf_radius`(比如 24) |
| 深度方向反了 | DA 系列默认"近处亮" | `depthimg__invert: true` 再按一次 |
| 层次被压平 | 极值像素 / 主体动态范围窄 | 调 `depthimg__clip`(默认 0.5%) |
| DA3 装不上 | 它的 requirements 有 `numpy<2`/open3d/pycolmap/xformers | **别装进 ComfyUI 的 venv**;按 `docs/INSTALL.md` 6.1 的"轻装"装法单开 venv |

## 限制

- 深度图是**相对**深度(近亮远暗),不是真实距离:做浮雕/遮罩够用,量尺寸不行
- **推理边长有硬上限(本机安全值 1554)**:vendored 的 attention 是 O(N²) 显存,再往上会抛
  `Invalid buffer size`(见实测结论第 36 条)。要更高分辨率得先换 SDPA,再放开 `size_max`
- 读贴图靠"导出当前 SubTool":模型很大时导出要等一会儿(可用 `depthimg__io_wait_s` 放宽)
- **替换**是改 ZBrush 里的贴图(Texture 面板 + SubTool 的贴图槽),**不动磁盘上的文件**;
  想拿回原来的贴图:在 Texture 面板里点回原来那张,或重新导入
- 深度引擎要 `torch`(通常借 ComfyUI 的 venv,零安装);发行包里
  `tools/depth_pipeline.py` 与 `tools/vendored/` **是源码**(要跑在别的 Python 版本上)

## 文件

| 文件 | 作用 |
|---|---|
| `__init__.py` | 面板:2 按钮 + 2 开关 + **DA V2 四档规模单选组**(V2-S / V2-B / V2-L / V2-G) |
| `service.py` | 引擎/许可判定、解释器自动配对、外部管线、读贴图、送回 ZBrush、诊断 |
| `tools/depth_pipeline.py` | 深度管线(零耦合,可单独跑;引擎 dav2/da3/luma/sdf;会打 `[zbtools-progress]` 真进度) |
| `tools/vendored/depth_anything_v2/` | DA V2 模型定义(上游 Apache-2.0,改动与许可证见 `PATCHES.md` / `LICENSE`) |
| `tests/test_depthimg.py` | 许可(只留可商用)+ 面板契约 + 隐患防护 + 真子进程 CLI 契约 |
| `scripts/probe_texture_paths.py` | 真机探测 ZBrush 的贴图导入/挂载 item path |
| `_probe/depthprobe/` | 一次性验证:各引擎耗时/画质、vendored 与 ComfyUI 版逐像素对比、图案距离场对比 |
