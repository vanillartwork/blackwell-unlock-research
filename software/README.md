[English](README.md) | [中文](README.cn.md)

# Route 2. Tensor throughput and accumulation accuracy

Measure steady-state throughput and relative L2 error on a Blackwell GPU, then choose a K chunk size for inference. The tools run in userspace on Linux or Windows with a CUDA driver and CUDA-enabled PyTorch.

---

## 1. Tools

| Tool | Purpose |
|---|---|
| [clamp_eval.py](clamp_eval.py) | Compare FP16, BF16, TF32 and FP8 with FP32 accumulation against FP16-accumulate, INT8 and FP32 controls. The benchmark uses a 16 × 16 CPU FP64 reference sample and an order-balanced FP16 A/B comparison. |
| [chunked_fp16_matmul.py](chunked_fp16_matmul.py) | Import `chunked_fp16_mm` for two matrices or `chunked_fp16_matmul` for batched inputs. The CLI sweeps K chunk sizes and measures throughput and error. |
| [dual_qmma_tma_ws.cu](dual_qmma_tma_ws.cu) | SM 12.0 `TMA-WS 128×256×64` single-sided Dual `QMMA.SF` (`W8A16` / `W4A16`: pre-split, 2-kernel, online-split) plus fused `RMSNorm`/`SwiGLU` dual-split epilogue kernels and steady-state NVML benchmark. |
| [verify_dual_fp8.py](verify_dual_fp8.py) | Pure-stdlib (no GPU) verifier checking `reports/gb205-2f18/route2-dual-qmma-621.01.json` (TMA-WS throughput, prefill frontier vs. `Marlin-HMMA`/`W4A4`, cold-L2 decode sweep, fused MLP, 517M-block scan, and 32k Deploy+High-Res LLM KL CIs). |
| [verify_wt2_bootstrap.py](verify_wt2_bootstrap.py) | Pure-stdlib (no GPU / no third-party dependencies) verifier that recomputes means, ΔPPL, excess KL, compression ratios and 95% paired bootstrap CIs from `reports/gb205-2f18/route2-wt2-288k-fp64.json`. |

---

## 2. Choosing a chunk size

On the measured GB205, FP32-accumulate tensor paths ran near half the architectural ceiling. FP16 accumulation reached 0.80–0.83×, but its sample relative L2 error at `N=8192` was 16.6× the FP32-accumulate baseline.

Split the inner dimension `K` into blocks of `chunk_k`. Compute each partial product with `allow_fp16_accumulation=True`, then add it to an FP32 accumulator with `acc.add_(partial)`.

### GB205 measurements (RTX 5070 Ti Laptop, driver `621.01`, `N = K = 8192`, about 100 W sustained)

The [archived sweep](../reports/gb205-2f18/chunked-fp16-621.01.json) contains the full measurements.

| Variant | Chunks | Throughput | Speed vs `fp32acc` | SM clock | Power | `FLOP/clk/SM` | Relative L2 error | Error vs `fp32acc` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_fp32acc` (baseline) | — | 44.29 TFLOP/s | 1.00× | 1852 MHz | 97.5 W | 519.9 | `0.000196` | 1.00× |
| `chunk_k=512` | 16 | 28.25 TFLOP/s | 0.64× | 1717 MHz | 99.7 W | 357.7 | `0.000870` | 4.44× |
| `chunk_k=1024` | 8 | 38.54 TFLOP/s | 0.87× | 1785 MHz | 100.3 W | 469.3 | `0.001142` | 5.83× |
| **`chunk_k=2048`** | **4** | **48.90 TFLOP/s** | **1.10×** | 1800 MHz | 100.0 W | **590.6** | **`0.001644`** | **8.40×** |
| **`chunk_k=4096`** | **2** | **56.27 TFLOP/s** | **1.27×** | 1848 MHz | 99.8 W | **661.7** | **`0.002140`** | **10.93×** |
| `full_fp16acc (k=8192)` | 1 | 65.74 TFLOP/s | 1.48× | 1672 MHz | 99.8 W | 854.7 | `0.003244` | 16.57× |

### Interpreting the sweep

1. At `K=8192`, chunks of 2048 and 4096 improved throughput by **10% and 27%** over FP32 accumulation. Their sample errors were **49% and 34% lower**, respectively, than full-length FP16 accumulation.
2. At `chunk_k ≤ 1024`, writing an intermediate `(M, N)` matrix and updating the FP32 accumulator for every chunk outweighed the GEMM speedup. A fused Triton or CUTLASS kernel might reduce those transfers. This sweep measured PyTorch only.
3. Use `torch.no_grad()` or `torch.inference_mode()` for inference. Buffer reuse prevents autograd. Scale inputs if partial products risk FP16 overflow (largest finite value 65504). The CUDA accumulation flag is process-wide, so avoid concurrent CUDA matrix multiplies during the call.
4. Check accuracy on your workload. The measured errors were on the order of `1e-3`; precision-sensitive training can require FP32 accumulation throughout.

---

## 3. Run the benchmark or import the operator

Install CUDA-enabled PyTorch ≥ 2.7 and optionally `nvidia-ml-py` as described in the [setup guide](../README.md#31-dependencies).

```python
import torch
from software.chunked_fp16_matmul import chunked_fp16_matmul

x = torch.randn(4, 2048, 8192, device="cuda", dtype=torch.float16)
w = torch.randn(8192, 4096, device="cuda", dtype=torch.float16)
with torch.inference_mode():
    y = chunked_fp16_matmul(x, w, chunk_k=2048)
```

```bash
# Sweep chunk sizes.
python3 software/chunked_fp16_matmul.py --n 8192 --chunks 512,1024,2048,4096,8192 --json chunked-local.json

# Run the steady-state evaluation.
python3 software/clamp_eval.py --n 8192 --min-seconds 2.0 --json clamp-local.json

# Compare existing results. Exit 3 indicates drift or no comparable results.
python3 scripts/verify-baseline.py --clamp-json clamp-local.json --compare reports/gb205-2f18/clamp-eval-621.01-n8192.json
```

`--device` is a CUDA ordinal. Telemetry matches the physical GPU by PCI bus ID, then UUID, then a unique model name. Ambiguous matches leave the telemetry fields empty. If the sweep has no single-chunk case (`chunk_k >= n`), `error_reduction_vs_full_fp16acc` is `null`.

---

## 4. Full-rate FP8→FP32 via `QMMA.SF` and CUDA 13.2+

For **FP8 (E4M3 / E5M2) → FP32** and mixed FP8×FP6 / FP8×FP4 workloads, stock consumer Blackwell cards do not require a precision trade-off:

- **Mechanism & exact operand rule.** While legacy `QMMA` (`kind::f8f6f4`) is clamped at `1024 FLOP/SM/clk` (`0.50×`), Blackwell's block-scaled `QMMA.SF.16832.F32.<A>.<B>.E8` (`kind::mxf8f6f4.block_scale.scale_vec::1X` with constant unit UE8M0 scale `0x7F7F7F7F`) is **unclamped whenever at least one operand is FP8 (`e4m3` or `e5m2`)**—and produces bit-identical FP32 accumulators (corroborated on RTX 5080 in [NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732), RTX 5090 in [flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963), and RTX 5070 Laptop in [steviecurrie/gpu-unlock-gb206#1](https://github.com/steviecurrie/gpu-unlock-gb206/issues/1)). If **neither** operand is FP8 (`FP6×FP6`, `FP6×FP4`, or `FP4×FP4` via `QMMA.SF.16832`), throughput remains at `1024 FLOP/SM/clk`; full-rate FP4 (`4096 FLOP/SM/clk`) requires `OMMA.SF.16864` (`kind::mxf4` or `kind::mxf4nvf4`).
- **Local GB205 SASS-verified matrix (RTX 5070 Ti Laptop, 46 SMs, driver `621.01`, CUDA 13.5, `sm_120a`, random non-zero finite inputs, `clock64()` cycle-normalized):**

| Format / Mode | SASS Opcode (`cuobjdump`) | Measured `FLOP/SM/clk` | Wall `TFLOP/s` | Notes |
|---|---|---:|---:|---|
| `HMMA` FP16→FP32 (`k16`) | `HMMA.16816.F32` | `511.8` | 57.56 | Clamped `0.50×` |
| `HMMA` BF16→FP32 (`k16`) | `HMMA.16816.F32.BF16` | `511.4` | 56.80 | Clamped `0.50×` |
| `HMMA` FP16→FP16 (`k16`) | `HMMA.16816.F16` | `1023.2` | 114.20 | Full `1.00×` |
| FP8 `e4m3×e4m3` plain vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 115.67 → **226.34** | **`2.00×`**, `0 / 33,554,432` bit diffs |
| FP8 `e5m2×e5m2` plain vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 113.67 → **225.21** | **`2.00×`**, `0 / 33,554,432` bit diffs |
| FP8 `e4m3×e5m2` plain vs **`QMMA.SF`** | `QMMA` → **`QMMA.SF.16832...E8`** | `1022.4` → **`2045.3`** | 115.42 → **222.27** | **`2.00×`**, `0 / 33,554,432` bit diffs |
| Undoc. **`E3M4FN×E3M4FN`** (`code 2×2`) | Patched `QMMA.SF.16832` (`hi64` split) | **`2045.2`** | **227.05** | **`2.00×`**, 4-bit mantissa verified (`0x31 → 36.125`) |
| Mixed **`E4M3×E3M4FN`** (`code 0×2`) | Patched `QMMA.SF.16832` (`hi64` split) | **`2045.2`** | **225.92** | **`2.00×`**, exact (`0x31 → 19.125`) |
| Mixed `e4m3×e3m2` / `e3m2×e4m3` **`QMMA.SF`** | **`QMMA.SF.16832...E8`** | **`2045.5`** | **225.68 / 225.03** | **`2.00×`** (FP8 operand present) |
| Mixed `e4m3×e2m1` / `e2m1×e4m3` / `e5m2×e2m1` **`QMMA.SF`** | **`QMMA.SF.16832...E8`** | **`2044.3–2044.9`** | **225.22–227.77** | **`2.00×`**, `0 / 33,554,432` bit diffs (`e4m3×e2m1`) |
| FP6 `e3m2×e3m2` / `e2m3×e2m3` plain vs `QMMA.SF` | `QMMA` / `QMMA.SF.16832...E8` | `1022.5` vs **`1023.1`** | 115.92 vs 115.79 | **Remains half-rate** (no FP8 operand) |
| Mixed `e3m2×e2m1` `QMMA.SF` | `QMMA.SF.16832...E8` | **`1023.2`** | 115.61 | **Remains half-rate** (no FP8 operand) |
| Sparse 2:4 FP8 `e4m3` plain vs **`QMMA.SF.SP`** (`k64`) | `QMMA.SP` → **`QMMA.SF.SP.16864...E8`** | `2045.9` → **`4026.4`** | 230.94 → **440.18** | **`1.97×`** sparse full rate |
| FP4 `e2m1×e2m1` `k32` plain / `mxf8f6f4` | `QMMA` / `QMMA.SF.16832` | `1022.0` / `1022.9` | 115.42 / 115.99 | Half-rate in `k32` form |
| FP4 `e2m1×e2m1` **`mxf4`** (`UE8M0`) / **`mxf4nvf4`** (`UE4M3`) | **`OMMA.SF.16864...`** | **`4087.7`** | **459.01 / 457.76** | **Full `4096` rate** |
| INT8 `s8×s8→s32` (`k32`) | `IMMA.16832.S8.S8` | **`2031.8`** | **227.75** | **Full `2048` OP/SM/clk** (usable for Ozaki slicing) |
| INT4 `s4×s4→s32` (`k64`) | Emulated via `IMMA.16832.S8.S8` | `213.7` | 24.27 | `~0.05×` of 4096; no native INT4 MMA on SM 12.0 |

  - **Precision & `cuBLASLt` check (same GB205, driver `621.01`).** `QMMA.SF` passed exact FP32 accumulation on `256² + 0.125² = 65536.015625` (`0x47800002`), and negative controls with UE8M0 scale `2.0` (`0x80`) differed in `~33.55M / 33.55M` registers. At `N=8192` FP8→FP32 GEMM, CUDA 13.5 `cuBLASLt 131000` reached **`241.70 TFLOP/s`** (`FAST_ACCUM=0`, `4.55 ms/iter`) and **`239.93 TFLOP/s`** (`FAST_ACCUM=1`, `4.58 ms/iter`), up **`2.16×`** over the archived PyTorch `2.11.0+cu128` (CUDA 12.8) baseline (`111.80 TFLOP/s`, `0.50×`).
  - **Undocumented `E3M4FN` (SASS type code `2`, hardware-verified on GB205).** Cross-diffing all five documented `QMMA.SF.16832` types (`e4m3`/`e5m2`/`e3m2`/`e2m3`/`e2m1`) in `nvdisasm -hex` shows the 3-bit operand type fields in `hi64` are **non-contiguous**: `typeA = (bit83 << 2) | (bit82 << 1) | bit78`, `typeB = (bit85 << 2) | (bit84 << 1) | bit79` (`bit 80` is the sparse flag, so a contiguous 3-bit mask at bit 78 corrupts bit 79/80). Codes map to `0=E4M3`, `1=E5M2`, `2=E3M4FN` (silently skipped by `nvdisasm`, executed natively by hardware), `3=E3M2`, `4=E2M3`, `5=E2M1`, while `6`/`7` decode as `INVALID6`/`INVALID7` and raise `CUDA_ERROR_ILLEGAL_INSTRUCTION`. Probing `a × 0x30` with unit UE8M0 scale confirms exact **E3M4FN** semantics (`1s + 3e bias=3 + 4m`, no Inf): subnormals `0x01 = 2⁻⁶ (0.015625)`..`0x0F = 15/64`, normals `0x10 = 0.25`, `0x30 = 1.0`, `0x31 = 1.0625`, max finite `0x7E = 30.0`, NaN only at `0x7F`/`0xFF`, negative `0xB0 = -1.0`. Both `E3M4×E3M4` and `E4M3×E3M4` sustain full **`2045.2 FLOP/SM/clk`**.

### Route 2 precision toolbox (stock drivers)

| Workload / Precision | Recommended Path | Ideal / Measured Rate (`OP/SM/clk`) | Accuracy & Engineering Notes |
|---|---|---:|---|
| FP8 (`e4m3`/`e5m2`, per-tensor/channel) | CUDA 13.2+ `cuBLASLt`, or unit-scale `QMMA.SF` (`0x7F`) | `2048` (sparse `4096`) | Bit-identical to plain FP8→FP32; keep tensor/channel scale in epilogue |
| Pure FP6 (`e3m2`/`e2m3`) or `FP6×FP4` | Lossless `FP6 → E4M3` upcast + `QMMA.SF` (`0x7F`) | **`2048`** (`2.00×`) | `64/64` exact embedding, unchanged `UE8M0`, **`0 / 33,554,432` bit diffs** across all 5 combos |
| MXFP8 `E3M4FN` (SASS-patched cubin) | Patched `QMMA.SF.16832` (`code = 2`) + UE8M0 | `2048` | +1-bit mantissa over E4M3 (max finite `30.0`, no PTX syntax; requires cubin patching) |
| Mixed FP8×FP6 / FP8×FP4 (`W6A8` / `W4A8`) | Unit-scale / block-scaled `QMMA.SF` | `2048` | Bit-identical to plain `kind::f8f6f4` |
| MXFP4 / NVFP4 | `OMMA.SF.16864` (`mxf4` / `mxf4nvf4`) | `4096` | FP4 quantization error only |
| **`W8A16` / `W4A16` / K-smoothed FP8-KV `QKᵀ`** | **Single-sided Dual `QMMA.SF` (`X = s(H+L/16)`, shared FP32 acc; see [dual_qmma_tma_ws.cu](dual_qmma_tma_ws.cu))** | **`951–981`** (**`1.97–2.01×`** per-GHz vs. `Marlin-HMMA`; fused MLP `1.56–1.67×` wall; cold-L2 decode `M=32–64` `1.39–1.64×` vs. Marlin) | Zero FTZ (`[2⁻⁶, 256)` bit-exact); same-weight FP64 `rel_L2` matches `cuBLAS BF16` (`W8`: `1.38e-6`, `W4`: `1.67e-7`); removes Single-FP8's `+71%–107%` extra KL on `W8`/K-smoothed `FP8-KV` (`20.8×–24.5×` High-Res local KL cut) |
| FP16 / BF16 near-Gaussian inference | **TMA-WS Pure `B1` (`N=3` `HMMA.F16` + `HADD2.F32`/`FADD` flush)** | **`782–841`** (`K=4096`, `1.61–1.66×` per-GHz vs. `cuBLAS BF16`) | Single-GEMM `rel_L2 ≈ 2.94e-4`; indistinguishable from `cuBLAS BF16→FP32` in BF16 deploy; `+1.35e-6` excess KL (`+10%`) in FP16 deploy |
| FP16 / BF16 outlier layers (e.g. `down_proj`) | **TMA-WS `C1+B1` (`N=2`, `1:2` F32:F16) + `K`-perm (`rms(X)`)** | **`736–791`** (`K=4096`, `1.52–1.56×` per-GHz, `+6.4%` vs. Pure `B1`) | Cuts single-GEMM FP32 `rel_L2` **~6×** (`4.9e-5`), peak FP16 partial sums **20×–137×**, and FP16 LLM excess KL **2.7×–2.9×** |
| Large-`K` non-zero-mean GEMMs (e.g. `dW`) | **`HMMA.F32` + RN flush every 16 `k16` steps (`RN16`)** | `512` (matches native) | **19×–84×** lower error than `cublasGemmEx` (`19×` with split-K at `K=262k`, `84×` without split-K at `K=65k`) |
| `lm_head`, MoE routers (FP32-sensitive) | Keep native `HMMA.F32` (or `RN16`) | `512` | `< 1.0%` wall time in last-token inference prefill (`0.73%–1.02%` on 1B–1.5B); in full-seq training/eval (`6.7%–9.5%` wall / `15%–21%` MACs) weigh cost vs. `+2.9e-7` to `+9.7e-7` KL penalty |
| FP16 / BF16 quick PyTorch userspace drop-in | K-chunked `HMMA.F16` + FP32 reduction (§2 above) | `591–662` (`chunk_k=2048–4096`) | No custom CUDA kernel needed; `~1.6–2.1e-3` error at `K=8192` |

---

## 5. SM 12.0 Throttle-Gate Model, TMA-WS GEMM & End-to-End LLM Study

A complete empirical study on GB205 covering the 4,096-opcode `HMMA` closure, per-SMSP 32-cycle hard cooldown gate (no burst credit, `1:2` zero-penalty mix default), lossless `FP6 → E4M3` 2× upcast, steady-state NVML clock/power separation of `128×256×48` TMA + `mbarrier` + `setmaxnreg 40/232` kernels (`0 / 2,097,152` bit-exact with the LLM evaluation library), full 288,768-token WikiText-2-raw dual-regime (`FP16`/`FP32` `lm_head`) true-FP64 bootstrap with 95% CIs, BF16 deployment control, and hardware FP16 partial-sum overflow headroom is archived in **[Route 2 Deep Dive](../docs/route2-mma-bypass.md)**.
