[English](README.md) | [中文](README.cn.md)

# Route 2. Tensor throughput and accumulation accuracy

Measure steady-state throughput and relative L2 error on a Blackwell GPU, then choose a K chunk size for inference. The tools run in userspace on Linux or Windows with a CUDA driver and CUDA-enabled PyTorch.

---

## 1. Tools

| Tool | Purpose |
|---|---|
| [clamp_eval.py](clamp_eval.py) | Compare FP16, BF16, TF32 and FP8 with FP32 accumulation against FP16-accumulate, INT8 and FP32 controls. The benchmark uses a 16 × 16 CPU FP64 reference sample and an order-balanced FP16 A/B comparison. |
| [chunked_fp16_matmul.py](chunked_fp16_matmul.py) | Import `chunked_fp16_mm` for two matrices or `chunked_fp16_matmul` for batched inputs. The CLI sweeps K chunk sizes and measures throughput and error. |

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
