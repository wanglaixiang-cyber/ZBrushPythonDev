# ZBTools —— ZBrush 工具集插件

一个 ZBrush **Python 插件**(不是 `.zsc`):装上之后右侧菜单栏多出一个 `ZBTools` 面板,
里面按功能分区。本目录就是插件本体,安装见 `INSTALL.md`。

## 功能

| 分区 | 显示名 | 做什么 |
|---|---|---|
| `true_scale` | 实际大小 | 按屏幕物理尺寸 1:1 显示选中的模型(可核验校准) |
| `ruler` | 屏幕真尺 | 屏幕上贴一把真实刻度的尺子(可旋转、多把;**仅 macOS**) |
| `meshtools` | 网格修复 | 空占位:只有分区标题,暂无控件 |
| `tex2pp` | 贴图转色 | 低模(有 UV + 贴图)的贴图 → 高模 polypaint,不用细分低模、不用给高模做 UV |
| `depthimg` | 图片转深度图 | 选中模型的贴图 → 灰度深度图 → 送回 ZBrush(默认替换模型贴图) |

Windows 包不含 `ruler`(透明浮层的后端只有 AppKit 实现)。`meshtools` 是刻意的空占位。

## 安装(最短路径)

把本目录放到一个稳定位置,然后二选一:

- **推荐**:双击 `launch/Enable Autoload.command`(macOS,一次),之后正常启动 ZBrush 都会加载;
- 临时:双击 `launch/Launch ZBrush with ZBTools.command`(只对本次生效);
- 手动:每次 `ZScript → Python Scripting → Load` 选 `manual/Load ZBTools.py`。

细节、依赖和排查见 **`INSTALL.md`**。

## 版本与包信息

- 版本:见 `VERSION`;精确构建信息(平台 / 功能 / 解释器 / 时间):`BUILD-INFO.json`
- 逐文件 sha256:`MANIFEST.sha256`
- 第三方组件(numpy 等运行期依赖不随包发布;随包的只有 Depth Anything V2 的 vendored 源码):
  `THIRD-PARTY-NOTICES.md`

## 配置

首次运行后会自动生成 **`~/.zbtools/settings.json`**(在用户目录,不在插件目录;插件目录
可能只读,且升级要整目录替换),可以手改:

- `interpreter`:跑外部脚本的 Python(显示器探测 / 屏幕真尺 / 烘焙 / 深度管线),
  也可用环境变量 `ZBTOOLS_PYTHON` 覆盖
- `display_id` / `ppi` / `diagonal_inch` / `native_px`:屏幕与校准参数
- `mm_per_unit` / `display_unit` / `center_model`:尺寸口径与显示行为
- `depthimg__*` / `tex2pp__*` / `ruler__*`:各功能自己的键(前缀约定)

删掉这个文件就会回到默认值;`ZBTOOLS_CONFIG` 可以指定别的路径。
从 0.2.0 升级时,旧包的 `zbtools/settings.json` 里的参数要先复制到
`~/.zbtools/settings.json`。**自动迁移只在旧包文件还在时有效**;按 INSTALL 的
“先删旧目录”流程安装时不会迁移,必须先手动复制。
