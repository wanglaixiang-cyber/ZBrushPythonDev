# 这里是什么

**Depth Anything V2 的模型定义源码**,取自上游仓库(Apache-2.0,许可证见同目录 `LICENSE`):

* <https://github.com/DepthAnything/Depth-Anything-V2>
* 取的文件:`depth_anything_v2/{dpt.py, dinov2.py, dinov2_layers/*.py, util/blocks.py}`

为什么要放一份在这里:本功能的深度图是**外部子进程**算的(零耦合:只吃 argv、只打 stdout),
而 ZBrush 自带 Python 里没有 torch。把模型定义随插件带一份,外部解释器只要有 `torch` 就能算,
不必依赖 ComfyUI / Depth-Anything 仓库装在哪、有没有被升级。

# 改了什么(只有一处,其余逐字未动)

1. `dpt.py`:删掉 `import cv2`、`from torchvision.transforms import Compose`、
   `from .util.transform import ...`,并删掉依赖它们的 `infer_image()` / `image2tensor()`。
   理由:外部管线只允许依赖 `torch + numpy + pillow`,不该为了 resize 去装 opencv;
   预处理(等比缩放到 14 的倍数 → ImageNet 归一化)改在 `depth_pipeline.py` 里做,
   与上游 `Resize(keep_aspect_ratio, lower_bound, ensure_multiple_of=14)` + `NormalizeImage` 等价。
2. 删掉 `util/transform.py`(只被上面那两个方法用)。
3. `dinov2_layers/attention.py`:上游用 `if XFORMERS_AVAILABLE` 的 `memory_efficient_attention`
   (**macOS 上没有 xformers 轮子 → 实测走不到**),Kijai 的 ComfyUI 版则改成 ComfyUI 的
   `optimized_attention`(O(N))。本目录保留的是**朴素实现** `attn = q @ k.transpose(-2,-1)`,
   即 **O(N²) 显存**:N = 高/14 × 宽/14,2048x3072 时 N≈31,974 → 单次注意力要 ~91 GiB
   (实测直接抛 `Invalid buffer size: 91.41 GiB`)。
   → **这是本功能推理边长的天花板**(默认/推荐 1554)。
   若哪天要上原生分辨率,改动面就是这一层:换成
   `nn.functional.scaled_dot_product_attention(q, k, v, scale=1.0)`(实测与朴素实现
   **逐像素一致**,相关 1.000000,显存 O(N²) → O(N)),再放开 `size_max`。

**模型结构、层名、权重键名一个字没改** —— 所以官方权重和 Kijai 转换的 safetensors
都能**严格**加载(`load_state_dict(..., strict=True)`,实测 vitl 407 个键、vitg 631 个键全对上)。

# 怎么验证没被改坏

```bash
# 用任意有 torch 的解释器。注意判据:
#   * 同一输入尺寸下,输出应与「直接用 ComfyUI 里那份代码」逐像素一致(实测相关 1.000000)
#   * 但**别指望不同分辨率下也一致** —— ComfyUI 那份能按原生分辨率跑,本目录这份会被 O(N²) 显存卡住
.venv/bin/python _probe/depthprobe/probe_vendored.py vitl <贴图> out.png
```
