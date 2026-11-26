[English](README.md) | [中文](README.cn.md)

# 路线 2：张量吞吐与累加精度

在 Linux 或 Windows 上用 CUDA 版 PyTorch 测量稳态吞吐和相对 L2 误差，再为推理选择 K 维分块大小。工具运行在用户态，使用已安装的 CUDA 驱动。

---

## 1. 模块清单

| 文件 | 用途 |
|---|---|
| [clamp_eval.py](clamp_eval.py) | 跨平台稳态张量钳制评估器（`fp16_fp16acc` / `fp16_fp32acc` / `bf16_fp32acc` / `tf32_fp32acc` / `fp8_fp32acc` / `int8` / `fp32_control` + `16×16` CPU FP64 相对 L2 误差 + 顺序平衡配对 A/B） |
| [chunked_fp16_matmul.py](chunked_fp16_matmul.py) | K 维分块 FP16 累加 + FP32 块间归约（提供可导入的 `chunked_fp16_mm` / `chunked_fp16_matmul` 函数及 CLI 帕累托扫描） |
| [dual_qmma_tma_ws.cu](dual_qmma_tma_ws.cu) | SM 12.0 `TMA-WS 128×256×64` 单侧双发 `QMMA.SF`（`W8A16` / `W4A16`，含预拆分 / 独立两核 / 单核在线拆分与稳态 NVML 基准） |
| [verify_dual_fp8.py](verify_dual_fp8.py) | 纯标准库（无 GPU）复算脚本，校验 `reports/gb205-2f18/route2-dual-qmma-621.01.json` 全部 TMA-WS 吞吐、5.17 亿块分布扫描与 32k LLM KL 置信区间 |
| [verify_wt2_bootstrap.py](verify_wt2_bootstrap.py) | 纯标准库（无 GPU / 无第三方依赖）复算脚本，可直接从 `reports/gb205-2f18/route2-wt2-288k-fp64.json` 重算均值、ΔPPL、超额 KL、压缩比与 95% 配对 bootstrap 置信区间 |

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

针对 **FP8（E4M3 / E5M2）→ FP32** 以及混合 **FP8×FP6 / FP8×FP4** 场景，原装消费级 Blackwell 显卡无需牺牲精度即可拿回满速：

- **原理与精确硬件门控规律。** 传统非缩放 `QMMA`（`kind::f8f6f4`）被锁在 `1024 FLOP/SM/clk`（`0.50×`），而 Blackwell 新增的微缩放指令 `QMMA.SF.16832.F32.<A>.<B>.E8`（`kind::mxf8f6f4.block_scale.scale_vec::1X`，配合寄存器常量单位 UE8M0 缩放 `0x7F7F7F7F`）**只要两个操作数中至少有一个是 FP8（`e4m3` 或 `e5m2`）即可跑满 `2048 FLOP/SM/clk`**，且输出与普通 `QMMA` 逐位一致（已在 RTX 5080 [NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732)、RTX 5090 [flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963) 及 RTX 5070 Laptop [steviecurrie/gpu-unlock-gb206#1](https://github.com/steviecurrie/gpu-unlock-gb206/issues/1) 上跨卡印证）。如果**两个操作数都不是 FP8**（如纯 `FP6×FP6`、`FP6×FP4` 或 `k32` 版 `FP4×FP4`），`QMMA.SF.16832` 仍停在 `1024 FLOP/SM/clk`；满速 FP4（`4096 FLOP/SM/clk`）必须改走 `OMMA.SF.16864`（`kind::mxf4` 或 `kind::mxf4nvf4`）。
- **本机 GB205 全矩阵一手实测（RTX 5070 Ti Laptop，46 SM，驱动 `621.01`，CUDA 13.5，`sm_120a`，非零随机有限输入，`clock64()` 时钟归一化）：**

| 精度 / 模式 | 实测 SASS 指令（`cuobjdump`） | 实测 `FLOP/SM/clk` | 稳态 `TFLOP/s` | 结论与精度核验 |
|---|---|---:|---:|---|
| `HMMA` FP16→FP32 (`k16`) | `HMMA.16816.F32` | `511.8` | 57.56 | 锁死 `0.50×` |
| `HMMA` BF16→FP32 (`k16`) | `HMMA.16816.F32.BF16` | `511.4` | 56.80 | 锁死 `0.50×` |
| `HMMA` FP16→FP16 (`k16`) | `HMMA.16816.F16` | `1023.2` | 114.20 | 满速 `1.00×` |
| FP8 `e4m3×e4m3` 普通 vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 115.67 → **226.34** | **`2.00×`**，`0 / 33,554,432` 位差异 |
| FP8 `e5m2×e5m2` 普通 vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 113.67 → **225.21** | **`2.00×`**，`0 / 33,554,432` 位差异 |
| FP8 `e4m3×e5m2` 普通 vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 115.42 → **222.27** | **`2.00×`**，`0 / 33,554,432` 位差异 |
| 未公开 **`E3M4FN×E3M4FN`** (`code 2×2`) | 修补 `QMMA.SF.16832`（`hi64` 拆分位） | **`2045.2`** | **227.05** | **`2.00×`**，4-bit 尾数实证（`0x31 → 36.125`） |
| 混合 **`E4M3×E3M4FN`** (`code 0×2`) | 修补 `QMMA.SF.16832`（`hi64` 拆分位） | **`2045.2`** | **225.92** | **`2.00×`**，精确（`0x31 → 19.125`） |
| 混合 `e4m3×e3m2` / `e3m2×e4m3` **`QMMA.SF`** | **`QMMA.SF.16832...E8`** | **`2045.5`** | **225.68 / 225.03** | **`2.00×`**（含 FP8 操作数） |
| 混合 `e4m3×e2m1` / `e2m1×e4m3` / `e5m2×e2m1` **`QMMA.SF`** | **`QMMA.SF.16832...E8`** | **`2044.3–2044.9`** | **225.22–227.77** | **`2.00×`**，`0 / 33,554,432` 位差异（`e4m3×e2m1`） |
| FP6 `e3m2×e3m2` / `e2m3×e2m3` 普通 vs `QMMA.SF` | `QMMA` / `QMMA.SF.16832...E8` | `1022.5` vs **`1023.1`** | 115.92 vs 115.79 | **仍为半速**（无 FP8 操作数） |
| 混合 `e3m2×e2m1` `QMMA.SF` | `QMMA.SF.16832...E8` | **`1023.2`** | 115.61 | **仍为半速**（无 FP8 操作数） |
| 2:4 稀疏 FP8 `e4m3` 普通 vs **`QMMA.SF.SP`** (`k64`) | `QMMA.SP` → **`QMMA.SF.SP.16864...E8`** | `2045.9` → **`4026.4`** | 230.94 → **440.18** | **`1.97×`** 跑满稀疏满速 |
| FP4 `e2m1×e2m1` `k32` 普通 / `mxf8f6f4` | `QMMA` / `QMMA.SF.16832` | `1022.0` / `1022.9` | 115.42 / 115.99 | `k32` 形态仍为半速 |
| FP4 `e2m1×e2m1` **`mxf4`** (`UE8M0`) / **`mxf4nvf4`** (`UE4M3`) | **`OMMA.SF.16864...`** | **`4087.7`** | **459.01 / 457.76** | **跑满 `4096`** |
| INT8 `s8×s8→s32` (`k32`) | `IMMA.16832.S8.S8` | **`2031.8`** | **227.75** | **跑满 `2048` OP/SM/clk**（可用于 Ozaki 切片） |
| INT4 `s4×s4→s32` (`k64`) | 由 `IMMA.16832.S8.S8` 软模拟 | `213.7` | 24.27 | 仅 `~0.05×`；SM 12.0 无原生 INT4 硬件 MMA |

  - **精度与 `cuBLASLt` 对照（同卡 GB205，驱动 `621.01`）。** `QMMA.SF` 在 `256² + 0.125² = 65536.015625`（`0x47800002`）上精确无损；对照组把 UE8M0 缩放改为 `2.0`（`0x80`）时约 `33.55M / 33.55M` 个寄存器产生差异。`N=8192` FP8→FP32 GEMM 下，CUDA 13.5 `cuBLASLt 131000` 在 `FAST_ACCUM=0` 测得 **`241.70 TFLOP/s`**（`4.55 ms/iter`），`FAST_ACCUM=1` 为 **`239.93 TFLOP/s`**（`4.58 ms/iter`），较同卡归档的 PyTorch `2.11.0+cu128`（CUDA 12.8）基线（`111.80 TFLOP/s`，`0.50×`）直接提升 **`2.16×`**。
  - **未公开 `E3M4FN`（SASS 类型码 `2`，本机 GB205 真机实证）。** 对 5 种公开 `QMMA.SF.16832` 类型（`e4m3`/`e5m2`/`e3m2`/`e2m3`/`e2m1`）做 `nvdisasm -hex` 差分证实，其 `hi64` 中的 3-bit 类型码为**非连续拆分位**：`typeA = (bit83 << 2) | (bit82 << 1) | bit78`，`typeB = (bit85 << 2) | (bit84 << 1) | bit79`（`bit 80` 为稀疏位，不能按连续 3-bit 掩码写入）。类型码对应 `0=E4M3`、`1=E5M2`、`2=E3M4FN`（`nvdisasm` 静默跳过，但真机硬件原生执行）、`3=E3M2`、`4=E2M3`、`5=E2M1`，而 `6`/`7` 反汇编为 `INVALID6`/`INVALID7` 并触发 `CUDA_ERROR_ILLEGAL_INSTRUCTION`。在单位 UE8M0 缩放下逐点探测 `a × 0x30` 证实了完整的 **E3M4FN** 语义（`1s + 3e bias=3 + 4m`，无 Inf）：次正规数 `0x01 = 2⁻⁶ (0.015625)`..`0x0F = 15/64`，正规数 `0x10 = 0.25`、`0x30 = 1.0`、`0x31 = 1.0625`，最大有限值 `0x7E = 30.0`，仅 `0x7F`/`0xFF` 为 `NaN`，负数 `0xB0 = -1.0`；`E3M4×E3M4` 与 `E4M3×E3M4` 均跑满 **`2045.2 FLOP/SM/clk`**。

### 路线 2 现行软件工具箱与选型建议（原装驱动）

| 场景 / 精度 | 推荐路径 | 理论 / 实测速率（`OP/SM/clk`） | 精度与工程说明 |
|---|---|---:|---|
| FP8（`e4m3`/`e5m2`，逐张量/逐通道） | CUDA 13.2+ `cuBLASLt`，或单位缩放 `QMMA.SF`（`0x7F`） | `2048`（稀疏 `4096`） | 与普通 FP8→FP32 逐位一致；逐张量/逐通道缩放留到 epilogue 乘即可 |
| 纯 FP6（`e3m2`/`e2m3`）或 `FP6×FP4` | 无损 `FP6 → E4M3` 提升 + 单位缩放 `QMMA.SF`（`0x7F`） | **`2048`**（`2.00×`） | `64/64` 精确嵌入、`UE8M0` 不变，5 种组合全部 **`0 / 33,554,432` 位差异** |
| MXFP8 `E3M4FN`（cubin 修补 SASS） | 修补 `QMMA.SF.16832`（`code = 2`）+ UE8M0 | `2048` | 比 E4M3 多 1-bit 尾数（最大有限值 `30.0`，无 PTX 语法，需原位修补 cubin） |
| 混合 FP8×FP6 / FP8×FP4（`W6A8` / `W4A8`） | 单位缩放 / 块缩放 `QMMA.SF` | `2048` | 与普通 `kind::f8f6f4` 逐位一致 |
| MXFP4 / NVFP4 | `OMMA.SF.16864`（`mxf4` / `mxf4nvf4`） | `4096` | 仅有 FP4 量化误差 |
| **`W8A16` / `W4A16` / FP8-KV `QKᵀ`** | **单侧双发 `QMMA.SF`（`X = s(H+L/16)`，共享 FP32 累加器，见 [dual_qmma_tma_ws.cu](dual_qmma_tma_ws.cu)）** | **`951–981`**（同频 **`1.87–1.95×`**，单核在线 `892–893` / `1.77×`） | 硬件无 FTZ（`[2⁻⁶, 256)` 逐位无损）；真 FP64 `rel_L2 = 1.67e-7–1.38e-6`（优于 `cuBLAS BF16`）；32k LLM 较单次 FP8 降 KL **`8.9×–10.5×`** |
| FP16 / BF16 近似高斯推理常规层 | **TMA-WS 纯 `B1`（`N=3` `HMMA.F16` + `HADD2.F32`/`FADD` 刷新）** | **`782–841`**（`K=4096`，同频较 `cuBLAS BF16` 快 `1.61–1.66×`） | 单 GEMM `rel_L2 ≈ 2.94e-4`；BF16 部署与 `cuBLAS` 统计不可区分；FP16 部署仅 `+1.35e-6` 超额 KL（`+10%`） |
| FP16 / BF16 强离群通道层（如 `down_proj`） | **TMA-WS `C1+B1`（`N=2`，`1:2` F32:F16）+ `K` 维重排（`rms(X)`）** | **`736–791`**（`K=4096`，同频 `1.52–1.56×`，较纯 `B1` 多 `+6.4%`） | 单层 FP32 `rel_L2` 降 **~6×**（`4.9e-5`）、FP16 部分和峰值降 **20×–137×**、FP16 LLM 超额 KL 降 **2.7×–2.9×** |
| 大 `K` 且激活均值非零（如权重梯度 GEMM） | **`HMMA.F32` + 每 16 步 `k16` 做 RN 刷新（`RN16`）** | `512`（与原生同速） | 较 `cublasGemmEx` 准 **19×–84×**（`K=262k` 触发 split-K 时 `19×`；`K=65k` 未触发 split-K 时 `84×`） |
| `lm_head`、MoE router 等 FP32 敏感层 | 保留原生 `HMMA.F32`（或 `RN16`） | `512` | 推理末位 prefill 耗时仅 `0.73%–1.02%`；全序列训练/评测耗时 `6.7%–9.5%`（MAC 占 `15%–21%`），需权衡耗时与 `+2.9e-7` 至 `+9.7e-7` 额外 KL |
| FP16 / BF16 轻量 PyTorch 现成方案 | K 维分块 `HMMA.F16` + FP32 归约（见第 2 节） | `591–662`（`chunk_k=2048–4096`） | 无需手写 CUDA 内核；`K=8192` 时 `~1.6–2.1e-3` 误差 |

---

## 5. SM 12.0 限速门模型、TMA-WS GEMM 与端到端 LLM 深度报告

关于 SM 12.0 全 4,096 opcode 封口、每 SMSP 32 周期硬冷却门（无突发信用、`1:2` 零惩罚默认值）、`FP6 → E4M3` 无损 2× 提升、`128×256×48` TMA + `mbarrier` + `setmaxnreg 40/232` 稳态 NVML 同频/功耗拆分（与质量动态库 `0 / 2,097,152` 逐位一致）、完整 288,768 tokens WikiText-2-raw 双口径（`FP16`/`FP32` `lm_head`）真 FP64 参考配对 bootstrap 95% CI、BF16 部署对照及硬件部分和溢出余量的完整实证记录，见 **[路线 2 深度报告](../docs/route2-mma-bypass.cn.md)**。
