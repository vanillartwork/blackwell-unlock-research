[English](README.md) | [中文](README.cn.md)

# 路线 2：张量吞吐与累加精度

在 Linux 或 Windows 上用 CUDA 版 PyTorch 测量稳态吞吐和相对 L2 误差，再为推理选择 K 维分块大小。工具运行在用户态，使用已安装的 CUDA 驱动。

---

## 1. 模块清单

| 文件 | 用途 |
|---|---|
| [clamp_eval.py](clamp_eval.py) | 跨平台稳态张量钳制评估器（`fp16_fp16acc` / `fp16_fp32acc` / `bf16_fp32acc` / `tf32_fp32acc` / `fp8_fp32acc` / `int8` / `fp32_control` + `16×16` CPU FP64 相对 L2 误差 + 顺序平衡配对 A/B） |
| [chunked_fp16_matmul.py](chunked_fp16_matmul.py) | K 维分块 FP16 累加 + FP32 块间归约（提供可导入的 `chunked_fp16_mm` / `chunked_fp16_matmul` 函数及 CLI 帕累托扫描） |

---

## 2. 如何选择分块大小

在 GB205 样本上，FP16、BF16、TF32 或 FP8 输入配合 FP32 累加时，张量核的时钟归一化吞吐约为参考满速的 0.5 倍。FP16 输入配合 FP16 累加可达 0.80–0.83 倍，但长内积会放大舍入误差。在 `N = K = 8192` 时，全程 FP16 累加的样本相对 L2 误差是 FP32 累加的 16.6 倍。

**做法**：沿内积维 `K` 按 `chunk_k` 分块，块内走未钳制的 `allow_fp16_accumulation=True`，块间在 FP32 累加器里规约（`acc.add_(partial)`）。

### GB205（RTX 5070 Ti Laptop，驱动 `621.01`，`N = K = 8192`，~100 W 稳态）实测

原始 JSON 见 [reports/gb205-2f18/chunked-fp16-621.01.json](../reports/gb205-2f18/chunked-fp16-621.01.json)：

| 方案 | 块数 | 吞吐 | 相对 `fp32acc` | SM 时钟 | 功耗 | `FLOP/clk/SM` | 相对 L2 误差 | 误差倍率（vs `fp32acc`） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_fp32acc`（钳制基线） | — | 44.29 TFLOP/s | 1.00× | 1852 MHz | 97.5 W | 519.9 | `0.000196` | 1.00× |
| `chunk_k=512` | 16 | 28.25 TFLOP/s | 0.64× | 1717 MHz | 99.7 W | 357.7 | `0.000870` | 4.44× |
| `chunk_k=1024` | 8 | 38.54 TFLOP/s | 0.87× | 1785 MHz | 100.3 W | 469.3 | `0.001142` | 5.83× |
| **`chunk_k=2048`** | **4** | **48.90 TFLOP/s** | **1.10×** | 1800 MHz | 100.0 W | **590.6** | **`0.001644`** | **8.40×** |
| **`chunk_k=4096`** | **2** | **56.27 TFLOP/s** | **1.27×** | 1848 MHz | 99.8 W | **661.7** | **`0.002140`** | **10.93×** |
| `full_fp16acc (k=8192)` | 1 | 65.74 TFLOP/s | 1.48× | 1672 MHz | 99.8 W | 854.7 | `0.003244` | 16.57× |

### 工程结论

1. 在这块 GB205 上，`K=8192` 时，分块 2048、4096 的吞吐分别比 FP32 累加高 **10%、27%**；相对全程 FP16 累加，样本误差分别低 **49%、34%**。
2. `chunk_k ≤ 1024` 时，每块都要把 `(M, N)` 中间矩阵写入显存，再更新 FP32 累加器。额外访存抵消了矩阵乘法的提速。本轮只测了 PyTorch；Triton 或 CUTLASS 融合实现能否改善吞吐，还需实测。
3. **使用限制**：仅用于推理。分块循环写入复用缓冲区，不可求导（输入需要梯度时会报错，请包在 `torch.no_grad()` 或 `torch.inference_mode()` 中）；每块中间结果以 FP16 保存，绝对值超过 65504 会溢出为 `inf`，必要时先缩放输入；`allow_fp16_accumulation` 是进程级开关，调用期间不要并发执行其他 CUDA 矩阵乘。
4. **适用边界**：适合可接受 `~1e-3` 级误差的推理、特征投影或自定义长 K 矩阵乘；不能无条件替代对数值稳定性敏感的 BF16/FP8 训练主链。

---

## 3. 使用示例

依赖（CUDA 版 PyTorch ≥ 2.7，`nvidia-ml-py` 可选）的安装命令见根目录 [README 第 3.1 节](../README.cn.md#31-依赖)。

```python
import torch
from software.chunked_fp16_matmul import chunked_fp16_matmul

x = torch.randn(4, 2048, 8192, device="cuda", dtype=torch.float16)
w = torch.randn(8192, 4096, device="cuda", dtype=torch.float16)
with torch.inference_mode():
    y = chunked_fp16_matmul(x, w, chunk_k=2048)
```

```bash
# 复现帕累托扫描
python3 software/chunked_fp16_matmul.py --n 8192 --chunks 512,1024,2048,4096,8192 --json chunked-local.json

# 复现全精度稳态钳制评估
python3 software/clamp_eval.py --n 8192 --min-seconds 2.0 --json clamp-local.json

# 与归档基线比对（不重新跑基准；漂移超过容差时退出码为 3）
python3 scripts/verify-baseline.py --clamp-json clamp-local.json --compare reports/gb205-2f18/clamp-eval-621.01-n8192.json
```

多卡主机上 `--device` 指 CUDA 序号；遥测依次按 PCI 总线号、UUID、唯一型号名匹配到同一张物理卡，不受 `CUDA_VISIBLE_DEVICES` 或 CUDA 默认的“最快优先”排序影响（PyTorch 2.7 没有 PCI 属性，走 UUID）。匹配方式记录在 JSON 的 `telemetry_match`；无法唯一匹配时时钟与功耗留空，不会借用另一张卡的读数。扫描中若没有单块（`chunk_k >= n`）的一档，`error_reduction_vs_full_fp16acc` 记为 `null`。
