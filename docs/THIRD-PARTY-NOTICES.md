# 第三方组件与许可说明

本发行包 = 插件自己的代码 + 下面列出的第三方材料。**运行期第三方库不随包发布**
(numpy / trimesh / torch / pyobjc 等由用户在外面装,见 `INSTALL.md` 第四节)。

## 随包发布

### Depth Anything V2(vendored 源码)

- 位置:`zbtools/features/depthimg/tools/vendored/depth_anything_v2/`
- 上游:https://github.com/DepthAnything/Depth-Anything-V2
- 许可:**Apache License 2.0**(许可证原文随包:同目录 `LICENSE`)
- 说明:这是上游推理代码的精简 vendored 副本(改动见同目录 `PATCHES.md`);
  它只被"图片转深度图"的外部管线 import,插件本身不依赖
- **权重不随包发布**:`depth_anything_v2_vits_fp32.safetensors`(V2-S)为 Apache-2.0;
  V2-B/L/G 与部分 DA3 变体是 CC-BY-NC-4.0(非商用),插件默认拒绝,需显式开启

### 插件自身

- `zbtools/**`(编译为 `.pyc`)、`init.py` 与 `tools/**`(源码)是本项目代码
- 本包**不附带**任何 Maxon 的 SDK 文件、官方示例或 ZBrush 可执行文件

## 运行期依赖(不随包,用户自行安装)

| 组件 | 用途 | 许可(上游) |
|---|---|---|
| pyobjc(Quartz / AppKit) | macOS 显示器探测、屏幕真尺浮层 | MIT |
| numpy | 贴图转色、深度图(luma/sdf) | BSD-3-Clause |
| trimesh / rtree / scipy / pillow | 贴图转色的最近点查询与采样 | MIT / MIT / BSD-3-Clause / HPND |
| torch | 深度图 DA V2 / DA3 推理 | BSD-3-Clause |
| depth-anything-3(可选) | DA3 引擎 | Apache-2.0(以实际仓库为准) |

## 商标

ZBrush、Maxon 是 Maxon Computer GmbH 的商标。本包是第三方插件,与 Maxon 无隶属关系,
未使用其 SDK 源码或商标素材。
