# ZBTools 0.2.4 发行说明

- 构建时间:2026-11-04
- 目标:ZBrush 2026(Python SDK 2026.1,内嵌 CPython 3.11.9)
- **本轮只出 macOS 包**:`dist/ZBTools-0.2.4-macos.zip`(Windows 不动)

## 修复:V2-B / V2-L / V2-G 报错的信息误导

**现象**:点面板后面三档,报

```
当前档位是 V2-L(Large,CC-BY-NC-4.0),但磁盘上找到/要用的是
depth_anything_v2_vits_fp32.safetensors(V2-S(Small,Apache-2.0))。
```

看起来像"V2-L 的权重没下",实际不是:磁盘上三档权重都在,只是它们是
CC-BY-NC-4.0,**默认被许可闸挡住**,旧逻辑于是退回到唯一可商用的 vits,
再把"档位对不上"当成错误抛出来。真正原因(许可)反而没在错误里说。

**修法**:

1. `find_model` 改成**严格按档位**:选中的那一档没有/不可用就返回空,
   **不再退回别的规模**(旧逻辑的"先退回、再由 variant_note 顶回去"已删)。
2. 新增 `variant_weight_on_disk`:找不到可用权重时,再查"这一档的权重是不是
   其实在磁盘上、只是不可商用"。如果是,直接报许可原因:
   - 自己用 → 把 `depthimg__allow_nc_weights` 设成 true,重启 ZBrush;
   - 要商用 → 点 V2-S(Apache-2.0)。
3. 只有当那一档**真的不在磁盘上**时,才给"没找到 V2-x 权重 + 下载命令"。

**未改**:深度推理本身、四档权重选择逻辑之外的行为、配置位置、面板。
**Windows**:未构建、未修改。

## 本次同时处理的现场

- 用户配置 `~/.zbtools/settings.json` 已设 `"depthimg__allow_nc_weights": true`
  (仅自用、非商用;每次跑都会在控制台提醒)
- 本机权重(实测都存在,开闸后应能直接跑):
  - `vitb` 390 MB → `~/.zbtools/models`
  - `vitl` 1.3 GB → `~/ComfyUI-Installs/ComfyUI/ComfyUI/models/depthanything`
  - `vitg` 5.0 GB → 同上
  - `vits` 99 MB → `~/.zbtools/models`
- 面板截图里 V2-S 亮着,是默认档;后面三档点选后会在 `run_depth` 里被许可闸拦下。

## 验证

- `tests/test_depthimg.py`:新增两条回归断言并全部通过
  - 选中的档位不在磁盘上 → `find_model` 返回空(不拿别的规模凑合)
  - 选 V2-L 但未放开许可 → 报错里明说 `allow_nc_weights`,不再误报"找到 Small"
- `tests/run_all.py`:全部通过
- 打包 + `verify_package.py`、解压后再验收:通过
