[English](route2-mma-bypass.md) | [中文](route2-mma-bypass.cn.md)

# Route 2 Summary & Standalone Repository Relocation Note

> **Relocation notice:** All Route 2 CUDA kernels, hardware micro-probes,
> unmodified upstream `vLLM Marlin` harnesses, 288k/32k WikiText-2-raw LLM
> evaluations, raw JSON archives, and paper reproduction scripts have been split
> into a standalone mixed-precision scheduling repository (**repository link to
> be added upon publication**).
>
> To preserve every previously cited commit and external link permanently, the
> complete pre-split report, figures, source code, and raw JSON datasets remain
> archived in this repository under tag **`v1-pre-split`** (commit
> `5761a1953358fdef4dcad05bfecfe6165e44a1a9`, keeping earlier cited commits such
> as `1f1c489` intact).

---

## Summary of Key Findings (GB205, RTX 5070 Ti Laptop, driver `621.01`, CUDA `13.5`)

> **Scope:** All measurements summarized below were collected on a single
> **GB205 laptop GPU (RTX 5070 Ti Laptop, 46 SMs, 140 W; pure-PyTorch chunked
> sweep from archived PyTorch `2.11.0+cu128` at ~100 W)**. Cross-GPU validation
> will be added in the standalone repository.

1. **Issue-gate mechanism:** Exhaustive 4,096-opcode SM 12.0 scanning confirms
   no `HMMA.SF` exists; `HMMA.F32`, plain `QMMA`, and non-FP8 `QMMA.SF` share a
   hard **32-cycle per-SMSP cooldown gate (zero burst credit)** over a full-rate
   16-cycle datapath. Interleaving at **`1:2`** (`HMMA.F32 : HMMA.F16`) fills
   cooldown gaps with zero gate penalty.
2. **Full-rate FP8 / FP6 / FP4 / INT8 rules:**
   - Upgrading to CUDA 13.5 `cuBLASLt` raises `N=8192` FP8→FP32 from `111.80` to
     **`241.70 TF/s`** with zero custom code;
   - Unit-scale `QMMA.SF` (`UE8M0 = 0x7F`) runs at **`2045.3 FLOP/SM/clk`**
     (`2.00×` vs. plain FP8, `0 / 33,554,432` bit diffs; 2:4 sparse `4026.4`,
     `1.97×`) whenever at least one operand is an 8-bit float;
   - Lossless load-time `FP6 → E4M3` upcast + `QMMA.SF` hits **`2048.1`**
     (`2.00×`, `0` bit diffs across all 5 mixes);
   - Full-rate FP4 (**`4087.7 FLOP/SM/clk`**) requires `OMMA.SF.16864`
     (`kind::mxf4` / `mxf4nvf4`), not `kind::mxf8f6f4` (`~1023`); INT8 `IMMA`
     runs at **`2031.8 OP/SM/clk`**.
3. **Native BF16 / FP16 → FP32 (TMA-WS `Pure B1` / `C1+B1`):**
   - vs. `cuBLAS BF16→FP32`: **`1.52–1.69×`** per-GHz (`1.53–1.69×` on 48-aligned
     `K=4080`: `C1+B1` `1.53–1.58×`, `Pure B1` `1.63–1.69×`) and **`1.25–1.47×`**
     wall-clock under the 140 W laptop cap;
   - On 288,768-token WikiText-2-raw, statistically indistinguishable in BF16
     deployment and `+6.46e-7` (`C1+B1 + K-perm`) to `+1.35e-6` (`Pure B1`
     keeping `lm_head`) excess KL in FP16 deployment.
4. **Quantized `W8A16` / `W4A16` & deployment-frozen K-smoothed `FP8-KV` (Single-sided `Dual-FP8 QMMA.SF`):**
   - `QMMA.SF.16832` executes E4M3 subnormals with zero FTZ, making
     `X = s(H + L/16)` bit-exact over `[2⁻⁶, 256)` (`3,584/3,584`, covering
     `99.94%` of real LLM activations); scanning `517,031,936` real BF16 blocks
     yielded **`0 / 517,031,936`** exact lossless INT8-affine pass rate;
   - Prefill wall-clock beats unmodified upstream `vLLM Marlin` (commit `3aad861`,
     SASS-verified `HMMA.16816.F32.BF16` only) by **`1.53–1.86×`** (**`1.41–1.77×`**
     on same-weight full Llama-3-8B MLP), and beats our own re-implemented
     Marlin-style `Dequant-HMMA.F32` upper bound by **`1.56–1.63×`** wall
     (**`1.97–2.01×`** per-GHz);
   - Cold-L2 decode (`M=1–16`) runs **`1.71–1.77×`** (W8) / **`2.66–2.99×`** (W4)
     faster than `cuBLAS BF16` with symmetric steady-state throughput ratio
     `Dual/Single = 0.89–1.00×`; pure `LDG.E.128` ablation proves on-chip W4
     unpacking costs `< 0.1%` (`49.1 vs 49.2 µs`);
   - On 32k WikiText-2-raw, eliminates Single-FP8's `+65%–107%` extra KL on
     `W8_MXFP8` and deployment-frozen K-smoothed (`Prefill-256` incl. unseen
     decode suffix `[256:]` plus offline static $\mu_{\text{calib}}$) `FP8-KV`
     (while `W4A4` jumps `+122%–184%`). **Decision boundary:** never quantize a
     native BF16 model solely to use `Dual-FP8`; on already-quantized weights,
     `Dual-FP8` dominates decode (`M ≤ 16`) with no trade-off, while prefill
     trades speed vs. KL between `Dual-FP8` and `Single-FP8`.

---

## Where to Find Archived Files

- Stock userspace baseline tools (`clamp_eval.py`, `chunked_fp16_matmul.py`)
  remain in [software/README.md](../software/README.md).
- To inspect the full pre-split report, SVG figures, and raw JSON archives in
  this repository, check out tag **`v1-pre-split`** (`git checkout v1-pre-split`).
