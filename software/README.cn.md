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

---

## 4. FP8→FP32 满速软件路径（`QMMA.SF` 与 CUDA 13.2+）

针对 **FP8（E4M3）→ FP32** 场景，原装消费级 Blackwell 显卡无需牺牲精度即可拿回满速：

- **原理。** 传统非缩放 `QMMA`（`m16n8k32.f32.e4m3.e4m3.f32`）被锁在 `0.50×`，但 Blackwell 新增的微缩放指令 `QMMA.SF.16832.F32.E4M3.E4M3.E8`（`mma.sync.aligned.kind::mxf8f6f4.block_scale.scale_vec::1X`，配合寄存器常量单位 UE8M0 缩放因子 `0x7F7F7F7F`）**未被限速**，且输出与普通 `QMMA` 逐位一致（已在 RTX 5080 [NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732)、RTX 5090 [flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963) 及 RTX 5070 Laptop [steviecurrie/gpu-unlock-gb206#1](https://github.com/steviecurrie/gpu-unlock-gb206/issues/1) 上跨卡印证）。
- **本机 GB205 一手复现（RTX 5070 Ti Laptop，46 SM，驱动 `621.01`，CUDA 13.5）：**
  - 指令级微基准（`sm_120a`，`cuobjdump` 已核对 SASS）：普通 `QMMA` 为 **`1020.0 FLOP/SM/clk`**（`0.498×`，`128.31 TFLOP/s`），单位缩放 `QMMA.SF` 达到 **`1956.3 FLOP/SM/clk`**（`0.955×`，`235.83 TFLOP/s`，时钟归一化提速 `1.918×`）；精度验证 `256² + 0.125² = 65536.015625`（`0x47800002`）精确无损。
  - `N=8192` FP8→FP32 GEMM（CUDA 13.5 `cuBLASLt 131000`）：`FAST_ACCUM=0` 测得 **`241.70 TFLOP/s`**（`4.55 ms/iter`），`FAST_ACCUM=1` 为 **`239.93 TFLOP/s`**（`4.58 ms/iter`）——同卡同驱动下较归档的 PyTorch `2.11.0+cu128`（CUDA 12.8）基线（`111.80 TFLOP/s`，`0.50×`）直接提升 **`2.16×`**。
- **落地建议与适用边界。** 跑 FP8→FP32 矩阵乘时，升级到 **CUDA 13.2+**（新版 `cuBLASLt` 会自动走 `QMMA.SF`）或在自定义 CUTLASS / CuTe / FlashInfer 内核中改发单位缩放 `QMMA.SF`，即可无损拿回满速。但 **`HMMA`（FP16 / BF16 / TF32 → FP32）没有对应的微缩放指令，依然锁在 `0.5×`**，仍需依赖上文的 K 维分块规约（或路线 1 的固件层研究）。
