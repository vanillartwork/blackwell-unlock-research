[English](route2-mma-bypass.md) | [中文](route2-mma-bypass.cn.md)

# Route 2 Deep Dive · SM 12.0 MMA Throttle-Gate Model, TMA-WS GEMM & End-to-End LLM Verification

All hardware measurements in this note were collected on a single consumer Blackwell GPU (**RTX 5070 Ti Laptop GPU**, `GB205`, `10de:2f18`, 46 SMs, 140 W board TDP, driver `621.01`, CUDA `13.5`, `sm_120a`), with SASS verified via `cuobjdump --dump-sass` / `nvdisasm -b SM120`. Cross-card confirmation on desktop RTX 5080 and an unthrottled RTX PRO 6000 control remains open (§5).

---

## 1. Consumer Blackwell MMA Throttle-Gate Model & Opcode Closure

### 1.1 Exhaustive SM 12.0 MMA opcode & `HMMA` bit-field closure

Offline disassembly scanning of all 4,096 12-bit SM 12.0 opcodes (`lo[11:0] = 0x000..0xFFF`) finds 255 valid opcodes and **exactly six `*MMA*` instructions**: `0x237 IMMA`, `0x23c HMMA`, `0x23f DMMA`, `0x27a QMMA`, `0x47a QMMA.SF`, and `0x47f OMMA.SF`. There is no block-scaled `HMMA.SF` opcode.

Probing `HMMA` (`0x23c`) control bits in `hi64` (`bit 76` = 1-bit accumulator `0=F16, 1=F32`; `bits 83:82` = input `0=F16, 1=BF16, 2=TF32, 3=INVALID3`) confirms on live GB205 silicon that `HMMA.16816.F16.BF16`, `HMMA.1688.F16.TF32`, `HMMA.16816.F32.TF32`, `INVALID3`, and `bit 81` all trap `CUDA_ERROR_ILLEGAL_INSTRUCTION (715)`, while silent modifier bits (`74, 77, 79, 80, 84, 85, 86`) execute cleanly but remain clamped at `508.5 FLOP/SM/clk`.

### 1.2 Per-SMSP cooldown gate (no burst credit)

Interleaving throttled and unthrottled MMA instructions on the same SMSP isolates the consumer throttle mechanism:

- **Shared datapath:** Every MMA occupies **16 SMSP cycles** regardless of whether it is throttled or full-rate.
- **Per-SMSP throttle gate:** Any two throttled MMAs (`HMMA.F32.F16`, `HMMA.F32.BF16`, `HMMA.F32.TF32`, unscaled `QMMA`, and non-FP8 `QMMA.SF`) issued on the same SMSP must be separated by at least **32 SMSP cycles**, with **no burst credit**.
  - *Decisive 1-warp/SMSP test:* `F16×4 → F32 → F32` (back-to-back throttled) takes `124.51 clk` vs. `113.63 clk` when the two `F32` MMAs are separated; at `4 warps/SMSP`, cross-warp staggering hides the back-to-back gap down to `96.00 clk` (`16.00 clk/MMA`).
- **Per-warp issue ceiling:** A single warp per SMSP caps at `21.00 clk/MMA` on `QMMA.SF`; **`2 warps/SMSP` (`8 warps/SM`, up to 255 regs/thread)** already saturates the `16.00 clk/MMA` datapath on pure `QMMA.SF`, `1:2` free-running (`16.00 clk/MMA`), and `1:2` barrier-lockstep (`16.08 clk/MMA`).
- **Mix-ratio sweep (`4 warps/SMSP`, SASS-verified):**
  - **`1:2` (`33.3%` throttled):** **`16.00 clk/MMA` (`0.0%` penalty — engineering default)**
  - `2:3` (`40.0%`): `16.16–16.30 clk/MMA` (`+1.0–1.9%`)
  - `3:4` (`42.9%`): `16.63–16.79 clk/MMA` (`+3.9–4.9%`)
  - `1:1` (`50.0%`): `17.91 clk/MMA` (`+11.9%`)

### 1.3 Narrow-format consequences: lossless `FP6 → E4M3` 2× & undocumented `E3M4FN`

- **Non-FP8 `QMMA.SF` shares the exact same cooldown gate:** Interleaving `1 HMMA.F32 + 1 QMMA.SF(e3m2×e3m2)` takes `64.00 clk` (same for `e2m1×e2m1`), whereas `1 QMMA.SF(e3m2×e3m2) + 1 QMMA.SF(FP8)` overlaps to `35.77 clk`—proving non-FP8 `QMMA.SF` is slowed by the consumer cooldown gate, not a 32-cycle datapath.
- **Lossless `FP6 → E4M3` upcast (`2.00×`, `0` bit diffs):** Both `E3M2` and `E2M3` embed losslessly into `E4M3` (`64/64` exact values each, unchanged `UE8M0` block scales). Upcasting `FP6` operands to `E4M3` at the SMEM/load stage and running `QMMA.SF.16832` produces **`0 / 33,554,432` bit differences** across all five combinations (`E3M2×E3M2`, `E2M3×E2M3`, `E3M2×E2M3`, `E3M2×E2M1`, `E2M3×E2M1`; 167.8M FP32 accumulators) while jumping from `1023.5–1024.0` to **`2048.1 FLOP/SM/clk`** (`2.00×`, `233.2–258.5 TFLOP/s` on GB205; `1466.5 FLOP/SM/clk` with 16× CTA-amortized register SWAR; avoid `__constant__` LUTs, which serialize to `40.6 FLOP/SM/clk`).
- **Undocumented `E3M4FN` (`QMMA.SF` code `2`):** Full hardware decoding and `2045.2 FLOP/SM/clk` verification are documented in [software/README.md](../software/README.md#4-full-rate-fp8fp32-via-qmmasf-and-cuda-132).

### 1.4 Hardware rounding modes & large-`K` `HMMA.F32` drift fix

Direct single-tile probes establish that `HMMA.16816.F16` uses **IEEE round-to-nearest-even (RN)** (`5/5` exact), whereas `HMMA.16816.F32` (both FP16 and BF16 inputs) uses **round-toward-zero (RZ) truncation** (`4/4` exact). On long inner dimensions with non-zero mean activations (e.g. weight-gradient GEMMs), sequential `HMMA.F32` accumulates systematic RZ drift. Flushing a local `HMMA.F32` accumulator into a master FP32 register via CUDA-core IEEE RN `FADD` every 16 `k16` steps (`HMMA.F32 + RN16`) runs at identical speed to native `HMMA.F32` while cutting error by **19×–84×** vs. real `cublasGemmEx` (`BF16→FP32`) and **562×** vs. a single unsplit chain:

| Workload (biased `N(0.25, 1)` inputs, vs. true FP64) | Single-chain `HMMA.F32` | Real `cublasGemmEx` (`BF16→FP32`) | **`HMMA.F32 + RN16`** | Improvement vs. `cuBLAS` |
|---|---:|---:|---:|---:|
| `1024×1024×65,536` (no cuBLAS split-K) | — | `2.31e-05` | **`2.74e-07`** | **`84×`** |
| `128×128×262,144` (cuBLAS uses split-K) | `3.24e-04` | `1.08e-05` | **`5.76e-07`** | **`19×`** (`562×` vs. single chain) |

---

## 2. End-to-End SM 12.0 TMA + `mbarrier` + Warp-Specialized GEMM

Early `cp.async` prototypes exposed two bottlenecks: (1) consumer warps running address math, `cp.async`, `ldmatrix`, spills, and FP16→FP32 flush (`HADD2.F32` + `FADD` in SM 12.0 SASS—`ptxas 13.5` lowers `__half22float2` to fast FMA-pipe `HADD2.F32`, not slow `F2F`) contended for issue slots, making `cp.async` `Pure B1 (N=3)` slower than `Pure F32` (`51.6` vs. `54.7 TFLOP/s`); (2) `ptxas 13.5` hoists all `HMMA.F32` instructions ahead of all `HMMA.F16` instructions within a single warp even inside one `asm volatile` block.

Moving to a **`128×256×48` 2-stage TMA (`SWIZZLE_32B`) + `mbarrier` + Warp-Specialized (`setmaxnreg 40 / 232`)** kernel (1 producer warpgroup + 2 consumer warpgroups = 8 consumer warps, `wm`-phase staggered across the 2 warps per SMSP) eliminated both bottlenecks. Two SM 12.0 compiler requirements are essential: compile with `-gencode arch=compute_120a,code=sm_120a` (plain `-arch=sm_120a` rejects `setmaxnreg`), and declare `__launch_bounds__(384, 1)` (omitting `, 1` causes `ptxas` `C7508: 'setmaxnreg' ignored`, collapsing `C1+B1` to 40 registers / 1,384 B spill / 6.3 TFLOP/s; with `, 1`, `Pure F32`/`F16` have `0 B` spill and `Pure B1`/`C1+B1 (N=2)` have `8 B` stack).

### Steady-state NVML benchmark (`1.0 s` continuous pre-burn + `1.0 s` NVML sampling, GB205, 140 W TDP)

Because this 140 W laptop GPU (`~3.0 W/SM`) boosts clamped `cuBLAS BF16→FP32` to `2.40–2.62 GHz` while full-rate kernels hit the 140 W wall at `2.07–2.17 GHz` (`18–21%` lower clock), both wall-clock `TFLOP/s` and clock-normalized `TF/s/GHz` (`FLOP/SM/clk`) are reported alongside true FP64 `rel_L2` (and `rel_L2` vs. `cuBLAS FP16→FP32`):

#### Aligned `K = 4080` (`85 × 48`): `4096 × 4096 × 4080` and `4096 × 14336 × 4080`

The first two tables align `K` to the `BK = 48` tile (`4080 = 85 × 48`). The exact unaligned Llama-3-8B `K = 4096` (`85 × 48 + 16` tail) benchmark follows immediately below.

**48-aligned Q/O (`4096 × 4096 × 4080`)**

| Kernel | Wall `TF/s` | `TF/s/GHz` (`FLOP/SM/clk`) | SM Clock / Power | `rel_L2` vs. True FP64 (vs. `cuBLAS F16→F32`) | vs. `cuBLAS BF16` (wall / per-GHz) |
|---|---:|---:|---:|---:|---:|
| `cuBLAS BF16→FP32` (target) | `53.52` | `22.34` (`485.7`) | `2.40 GHz` / `135.1 W` | — | `1.00× / 1.00×` |
| `cuBLAS FP16→FP32` (ref) | `52.51` | `21.99` (`478.1`) | `2.39 GHz` / `138.9 W` | `4.821e-06` (`0.000e+00`) | `0.98× / 0.98×` |
| `cuBLAS FP16→FP16` (ceiling) | `91.08` | `43.61` (`948.1`) | `2.09 GHz` / `139.1 W` | — | `1.70× / 1.95×` |
| `TMA-WS 128×256 Pure F32` | `55.52` | `21.79` (`473.8`) | `2.55 GHz` / `139.4 W` | `4.821e-06` (`0.000e+00`) | `1.04× / 0.98×` |
| `TMA-WS 128×256 Pure F16` | `86.71` | `40.52` (`880.8`) | `2.14 GHz` / `139.4 W` | `2.350e-03` (`2.350e-03`) | `1.62× / 1.81×` |
| **`TMA-WS 128×256 Pure B1 (N=3)`** | **`78.79`** | **`36.34` (`789.9`)** | `2.17 GHz` / `139.3 W` | `2.940e-04` (`2.940e-04`) | **`1.47× / 1.63×`** |
| **`TMA-WS 128×256 C1+B1 (N=2)`** | **`73.88`** | **`34.15` (`742.5`)** | `2.16 GHz` / `139.3 W` | **`2.069e-04`** (`2.069e-04`) | **`1.38× / 1.53×`** |
| `TMA-WS 128×256 C1+B1 (N=4)` | `72.54` | `32.77` (`712.3`) | `2.21 GHz` / `139.3 W` | `2.655e-04` (`2.655e-04`) | `1.36× / 1.47×` |

**48-aligned Up (`4096 × 14336 × 4080`)**

| Kernel | Wall `TF/s` | `TF/s/GHz` (`FLOP/SM/clk`) | SM Clock / Power | `rel_L2` vs. True FP64 (vs. `cuBLAS F16→F32`) | vs. `cuBLAS BF16` (wall / per-GHz) |
|---|---:|---:|---:|---:|---:|
| `cuBLAS BF16→FP32` (target) | `60.66` | `23.14` (`503.1`) | `2.62 GHz` / `137.0 W` | — | `1.00× / 1.00×` |
| `cuBLAS FP16→FP32` (ref) | `58.45` | `22.80` (`495.6`) | `2.56 GHz` / `140.1 W` | `4.825e-06` (`0.000e+00`) | `0.96× / 0.99×` |
| `cuBLAS FP16→FP16` (ceiling) | `98.06` | `45.59` (`991.0`) | `2.15 GHz` / `139.8 W` | — | `1.62× / 1.97×` |
| `TMA-WS 128×256 Pure F32` | `58.15` | `22.93` (`498.5`) | `2.54 GHz` / `139.8 W` | `4.825e-06` (`0.000e+00`) | `0.96× / 0.99×` |
| `TMA-WS 128×256 Pure F16` | `85.84` | `41.31` (`898.0`) | `2.08 GHz` / `139.8 W` | `2.343e-03` (`2.343e-03`) | `1.42× / 1.79×` |
| **`TMA-WS 128×256 Pure B1 (N=3)`** | **`80.71`** | **`39.05` (`848.9`)** | `2.07 GHz` / `139.8 W` | `2.935e-04` (`2.935e-04`) | **`1.33× / 1.69×`** |
| **`TMA-WS 128×256 C1+B1 (N=2)`** | **`76.62`** | **`36.67` (`797.2`)** | `2.09 GHz` / `139.9 W` | **`2.073e-04`** (`2.073e-04`) | **`1.26× / 1.58×`** |
| `TMA-WS 128×256 C1+B1 (N=4)` | `74.21` | `34.84` (`757.5`) | `2.13 GHz` / `139.5 W` | `2.672e-04` (`2.673e-04`) | `1.22× / 1.51×` |

#### Exact unaligned Llama-3-8B `K = 4096` (`85 × 48 + 16` tail via TMA OOB zero-fill)

Setting `num_tiles = ceil(K / 48) = 86` lets SM 12.0 TMA hardware zero-fill the 32 out-of-bounds columns of the final tile (`CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE`) with no inner-loop branch. `Pure F32` matches `cuBLAS FP16→FP32` to `0.000e+00` (`4.849e-06` / `4.839e-06` vs. true FP64), and per-clock efficiency drops by only `~0.9%–1.0%` from the 32 padded columns:

| Shape (`M × N × K`) | Kernel | Wall `TF/s` | `TF/s/GHz` (`FLOP/SM/clk`) | SM Clock / Power | `rel_L2` vs. True FP64 | vs. `cuBLAS BF16` (wall / per-GHz) |
|---|---|---:|---:|---:|---:|---:|
| Llama-3-8B Q/O (`4096×4096×4096`) | `cuBLAS BF16→FP32` (target) | `54.54` | `22.31` (`484.9`) | `2.44 GHz` / `138.6 W` | — | `1.00× / 1.00×` |
| Llama-3-8B Q/O (`4096×4096×4096`) | `cuBLAS FP16→FP16` (ceiling) | `92.30` | `43.80` (`952.2`) | `2.11 GHz` / `138.4 W` | — | `1.69× / 1.96×` |
| Llama-3-8B Q/O (`4096×4096×4096`) | `TMA-WS Pure F32` | `55.19` | `21.29` (`462.7`) | `2.59 GHz` / `140.0 W` | `4.849e-06` (`0.000` vs. cuBLAS) | `1.01× / 0.95×` |
| Llama-3-8B Q/O (`4096×4096×4096`) | **`TMA-WS Pure B1 (N=3)`** | **`75.41`** | **`35.96` (`781.7`)** | `2.10 GHz` / `139.5 W` | `2.951e-04` | **`1.38× / 1.61×`** |
| Llama-3-8B Q/O (`4096×4096×4096`) | **`TMA-WS C1+B1 (N=2)`** | **`71.78`** | **`33.84` (`735.7`)** | `2.12 GHz` / `139.2 W` | **`2.082e-04`** | **`1.32× / 1.52×`** |
| Llama-3-8B Up (`4096×14336×4096`) | `cuBLAS BF16→FP32` (target) | `61.81` | `23.25` (`505.5`) | `2.66 GHz` / `134.7 W` | — | `1.00× / 1.00×` |
| Llama-3-8B Up (`4096×14336×4096`) | `cuBLAS FP16→FP16` (ceiling) | `99.57` | `45.40` (`987.1`) | `2.19 GHz` / `139.5 W` | — | `1.61× / 1.95×` |
| Llama-3-8B Up (`4096×14336×4096`) | `TMA-WS Pure F32` | `58.39` | `23.05` (`501.0`) | `2.53 GHz` / `139.5 W` | `4.839e-06` (`0.000` vs. cuBLAS) | `0.94× / 0.99×` |
| Llama-3-8B Up (`4096×14336×4096`) | **`TMA-WS Pure B1 (N=3)`** | **`81.13`** | **`38.68` (`840.8`)** | `2.10 GHz` / `139.7 W` | `2.941e-04` | **`1.31× / 1.66×`** |
| Llama-3-8B Up (`4096×14336×4096`) | **`TMA-WS C1+B1 (N=2)`** | **`77.08`** | **`36.36` (`790.5`)** | `2.12 GHz` / `139.5 W` | **`2.076e-04`** | **`1.25× / 1.56×`** |

Key observations:
1. Offloading memory transfers to the TMA producer warpgroup lifted `Pure B1 (N=3)` from `51–64 TF/s` (`cp.async`) to **`75.4–81.1 TF/s`** on exact `K=4096` (`78.8–80.7 TF/s` on 48-aligned `K=4080`), sustaining **`1.61×–1.66×`** per GHz (`1.31×–1.38×` wall) over `cuBLAS BF16→FP32` on exact `K=4096` (`1.63×–1.69×` per GHz on `K=4080`).
2. `C1+B1 (N=2)` sustains **`1.52×–1.56×`** per GHz (`1.25×–1.32×` wall) on exact `K=4096` (`1.53×–1.58×` per GHz on `K=4080`), costing **`+6.3%–6.5%`** more time per clock than `Pure B1 (N=3)`.
3. **Single-GEMM outlier accuracy (`5%` channels at `20×`, `K=4096`):** Routing the top-`1/3` `K` channels into the `HMMA.F32` slot (`C1+B1 + K-perm`) cuts single-GEMM FP32-output `rel_L2` by **~6×** (`~2.9e-4 → 4.92e-5`).
4. **Bit-exact speed/quality equivalence:** Comparing the fast `TMA-WS 128×256` kernel against the arbitrary-`(M, N, K)` SM 12.0 `HMMA` library used for LLM evaluation on `1024×2048×2064` (with `50×` outlier channels) yields **`0 / 2,097,152` bit differences (`max_abs = 0.000e+00`, `rel_L2 = 0.000e+00`)** across all five modes (`Pure F32`, `Pure F16`, `Pure B1 (N=3)`, `C1+B1 (N=2)`, `C1+B1 (N=4)`).

---

## 3. End-to-End LLM Quality: Dual-Regime KL, Fixed-Ref Causal Ablation & Headroom

Every `nn.Linear` layer in **`Llama-3.2-1B`** (`unsloth/Llama-3.2-1B`, revision `9535bd9b1d1d`, 113 layers; `transformers 5.24.1`, `tokenizers 0.23.3`) and **`Qwen2.5-1.5B`** (`Qwen/Qwen2.5-1.5B`, 197 layers) was replaced with real SM 12.0 hardware `HMMA` kernels and evaluated on **WikiText-2-raw** prefill (`seq_len = 1024`), using an **unbiased true FP64-accumulation reference** (`F.linear(x.double(), w.double(), b.double())` with identical FP16 weights and inter-layer FP16 rounding; `KL = 0.0` across repeated runs). Channel permutations for `C1+B1 + K-perm` were calibrated on a disjoint 2-chunk validation split (`rms(X)` per channel) and applied via runtime `index_select` gather in the evaluation harness.

### 3.1 Dual-regime KL gap, fixed-reference 2×2 causal ablation & rounding saturation

Comparing the **Deploy regime** ($\mathrm{KL}(\mathrm{softmax}(Q(z_{\text{ref}})) \,\|\, \mathrm{softmax}(Q(z_{\text{cur}})))$, where both reference and candidate `lm_head` outputs are rounded to `FP16`/`BF16`) against the **High-Res regime** ($\mathrm{KL}(\mathrm{softmax}(z_{\text{ref}}) \,\|\, \mathrm{softmax}(z_{\text{cur}}))$, raw `FP32` `lm_head` logits on both sides) separates the backbone saturation floor (`~35%`) from the dual-regime output gap (`Deploy − High-Res`, `~65%`). Because the two-regime difference quantizes *both* reference and candidate logits simultaneously, we also run a **fixed-FP32-reference 2×2 paired causal ablation** (`Llama-3.2-1B`, 32 chunks = `32,768` tokens, holding $P = \mathrm{softmax}(z_{\text{ref}}^{\text{FP32}})$ strictly fixed across all cells):

![Dual-Regime KL Breakdown](route2-kl-floor.svg)

![Discrete Rounding Saturation vs. Depth](route2-depth-saturation.svg)

| Fixed-FP32-Ref 2×2 Causal Ablation (`Llama-3.2-1B`, 32k tokens) | Backbone only $\mathrm{KL}(P_{\text{ref}}^{32} \| Q_{\text{cur}}^{32})$ | Single-side `lm_head` only $\mathrm{KL}(P_{\text{ref}}^{32} \| Q_{\text{ref}}^{\text{low}})$ | Joint (fixed ref) $\mathrm{KL}(P_{\text{ref}}^{32} \| Q_{\text{cur}}^{\text{low}})$ | Interaction (`joint − bb − lm`) `[95% CI]` | Dual-quantized Deploy $\mathrm{KL}(Q_{\text{ref}}^{\text{low}} \| Q_{\text{cur}}^{\text{low}})$ |
|---|---:|---:|---:|---:|---:|
| **FP16** `Mode0` (vs. fixed true FP64) | `4.64e-6` (`47.8%`) | **`5.11e-6` (`52.6%`)** | `9.70e-6` | `-4.25e-8 [-1.12e-7, +2.41e-8]` (`n.s.`) | `1.32e-5` (gap `+8.52e-6`) |
| **FP16** `Pure B1 [keep head]` | `5.68e-6` | `5.11e-6` | `1.08e-5` | `-6.84e-9 [-5.69e-8, +4.40e-8]` (`n.s.`) | `1.46e-5` |
| **FP16** `C1+B1+K-perm [ALL]` | `5.09e-6` | `5.11e-6` | `1.02e-5` | `-3.26e-8 [-9.10e-8, +2.22e-8]` (`n.s.`) | `1.38e-5` |
| **BF16** `Mode0` (vs. fixed `FP32-RN`) | `2.94e-4` (`47.6%`) | **`3.27e-4` (`52.9%`)** | `6.17e-4` | `-3.17e-6 [-6.11e-6, -1.12e-7]` (`-0.5%`) | `8.42e-4` (gap `+5.48e-4`) |
| **BF16** `Pure B1 [keep head]` | `3.02e-4` | `3.27e-4` | `6.29e-4` | `-2.23e-7 [-3.72e-6, +3.37e-6]` (`n.s.`) | `8.57e-4` |
| **BF16** `C1+B1+K-perm [ALL]` | `2.98e-4` | `3.27e-4` | `6.27e-4` | `+2.79e-6 [-8.48e-7, +6.43e-6]` (`n.s.`) | `8.51e-4` |

- **Fixed-reference causal additivity:** Holding $P_{\text{ref}}^{\text{FP32}}$ fixed confirms that single-side `lm_head` output quantization independently contributes **`5.11e-6` (`52.6%`)** on FP16 and **`3.27e-4` (`52.9%`)** on BF16 (an exact **`64.0×`** = $(2^3)^2$ ratio), backbone saturation independently contributes **`4.64e-6` (`47.8%`)** / **`2.94e-4` (`47.6%`)** (`63.4×` ratio), and their non-linear interaction is **`< 0.5%`** (statistically indistinguishable from zero across FP16 and BF16 `Pure B1` / `C1+B1`). In the dual-quantized Deploy regime (`1.32e-5` / `8.42e-4`), quantizing *both* reference and candidate logits onto partially decorrelated grids widens the regime gap to `~1.67×` the single-side `lm_head` term (`~65%` of Deploy KL).
- **Backbone discrete-rounding saturation:** Even IEEE-754 RN `FP32` (`allow_tf32=False`, single-GEMM `rel_L2 = 1.43e-7`) flips `31%–34%` of residual-block elements at `L00` and converges by `L05–L09` to the exact same **`~85%–87%` discrete grid-saturation plateau** as `Mode0` and `Pure B1`. Final-layer hidden-state `rel_L2` on `Llama-3.2-1B` is `1.69e-3` (FP16) vs. `1.31e-2` (BF16) (`7.75× ≈ 8 = 2³`).
- **Softmax KL scaling note:** Because softmax is shift-invariant, small logit perturbations $\Delta z$ expand to $\mathrm{KL}(P \| Q) \approx \frac{1}{2}\mathrm{Var}_{i \sim P}(\Delta z_i)$ (vanishing if a constant offset shifts all logits or if $P$ is near one-hot). Under approximately independent per-token rounding noise on multi-token competitive distributions, this produces the observed `~64×` $(\text{ULP}_{\text{BF16}}/\text{ULP}_{\text{FP16}})^2$ scaling (`~ULP²/12` order-of-magnitude heuristic, not a general exact formula).
- **Primary metric:** Because relative percentages depend on which regime floor is included, **absolute excess KL vs. `Mode0` (with 95% CI)** is the primary kernel-quality metric; percentages are auxiliary.

### 3.2 Full WikiText-2-raw (`288,768` tokens, 282 chunks) · True FP64 Reference (`Llama-3.2-1B`, FP16)

All 282 per-chunk records (`kl_deploy`, `kl_high_res`, `top1_high_res`, `nll_high_res`, `ref_nll_*`, exact model revision `9535bd9b1d1d`, `transformers 5.24.1`, `tokenizers 0.23.3`) are archived in [reports/gb205-2f18/route2-wt2-288k-fp64.json](../reports/gb205-2f18/route2-wt2-288k-fp64.json) and can be independently recomputed without a GPU via `python3 software/verify_wt2_bootstrap.py` (10,000-resample chunk-paired bootstrap, `seed = 42`; `PPL_hi = 10.78688`, `PPL_dep = 10.78695`; across all B1/C1 variants, `|ΔPPL| ≤ 0.00014`, `< 0.0013%`, within measurement noise):

| Configuration (vs. True FP64, 288,768 tokens) | Deploy (`FP16 lm_head`) KL · **Excess vs. `Mode0` `[95% CI]`** | High-Res (`FP32 lm_head`) KL · **Excess vs. `Mode0` `[95% CI]`** | High-Res Top-1 |
|---|---:|---:|---:|
| `FP32-RN (allow_tf32=False)` | `1.30e-5` · `-9.92e-8 [-1.51e-7, -4.69e-8]` | `4.48e-6` · `-1.06e-7 [-1.36e-7, -7.66e-8]` | `99.88%` |
| `HW Mode5: HMMA.F32 + RN16` | `1.30e-5` · `-9.48e-8 [-1.46e-7, -4.28e-8]` | `4.53e-6` · `-5.93e-8 [-8.75e-8, -2.96e-8]` | `99.88%` |
| `HW Mode0: Native HMMA.F32` (ref) | `1.31e-5` · `0` (`High-Res 4.59e-6 + gap 8.50e-6`) | `4.59e-6` · `0` (`High-Res floor = 35.0%`) | `99.87%` |
| `HW Mode1: Pure F16 (no flush)` | `3.03e-4` · **`+2.90e-4 [+2.87e-4, +2.92e-4]`** (`23.1×`) | `2.98e-4` · **`+2.93e-4 [+2.91e-4, +2.95e-4]`** (`65.0×`) | `98.72%` |
| `HW Mode2: Pure B1 (N=3)` `[keep head]` | `1.44e-5` · **`+1.35e-6 [+1.29e-6, +1.41e-6]`** (`+10.3%`) | `5.61e-6` · **`+1.03e-6 [+9.88e-7, +1.07e-6]`** (`+22.4%`) | `99.86%` |
| `HW Mode2: Pure B1 (N=3)` `[ALL]` | `1.48e-5` · **`+1.75e-6 [+1.69e-6, +1.81e-6]`** (`+13.4%`) | `5.91e-6` · **`+1.32e-6 [+1.28e-6, +1.36e-6]`** (`+28.8%`) | `99.85%` |
| `HW Mode3: C1+B1 (N=2)` no-perm `[keep]` | `1.39e-5` · **`+8.10e-7 [+7.54e-7, +8.66e-7]`** (`+6.2%`) | `5.21e-6` · **`+6.24e-7 [+5.87e-7, +6.62e-7]`** (`+13.6%`) | `99.87%` |
| **`HW Mode3: C1+B1+K-perm(act)` `[ALL]`** | `1.37e-5` · **`+6.46e-7 [+5.92e-7, +7.02e-7]`** (`+4.9%`) | `5.04e-6` · **`+4.56e-7 [+4.24e-7, +4.89e-7]`** (`+9.9%`) | **`99.87%`** |

Key paired bootstrap findings across all 288,768 tokens:
1. **Unbiased ordering confirmed at `10⁻⁸` KL:** Against true FP64, `FP32-RN` (`4.48e-6`) < `Mode5 (RN16)` (`4.53e-6`, `-5.93e-8` vs. `Mode0`, `p < 0.05`) < `Mode0` (`4.59e-6`), directly confirming `Mode5` removes `HMMA.F32` RZ bias (statistically significant, though `< 1.3%` in magnitude).
2. **Consistent compression across both regimes:** `C1+B1+K-perm(act) [ALL]` cuts excess KL vs. `Pure B1 [ALL]` by **`2.90×` `[2.72×, 3.11×]`** in High-Res (`+4.56e-7` vs. `+1.32e-6`) and **`2.71×` `[2.51×, 2.93×]`** in Deploy (`+6.46e-7` vs. `+1.75e-6`). A small second-order effect (Deploy excess `~1.2–1.4×` High-Res excess for small-excess methods, equal for `Pure F16`) reflects near-saturated dual-grid rounding.
3. **`lm_head` ablation and scenario-dependent cost:** Replacing `lm_head` with `Pure B1` adds **`+2.93e-7` `[+2.89e-7, +2.98e-7]`** (High-Res) / **`+4.02e-7` `[+3.78e-7, +4.24e-7]`** (Deploy) on `Llama-3.2-1B`; on `Qwen2.5-1.5B` (6k–12k true FP64), replacing `lm_head` raises High-Res `Pure B1` excess from `+1.56e-6` (`+29.1%`) to `+2.53e-6` (`+47.2%`), while `C1+B1+K-perm` stays at `+4.9e-7` (`+9.1%`). The runtime cost of keeping `lm_head` native depends strongly on whether logits are computed for every token or only the last token (`seq_len = 1024, batch = 1`, native `cuBLAS` on GB205):
   - **Inference prefill (last-token logits only, `M = 1`):** `lm_head` is only **`0.026%`** (`Llama-3.2-1B`) / **`0.017%`** (`Qwen2.5-1.5B`) of linear MACs and **`0.90%–1.02%`** (`0.85–1.02 ms` vs. `93.9–99.0 ms`) / **`0.73%–0.82%`** (`0.84–0.95 ms` vs. `114.5–114.6 ms`) of measured forward wall time — keeping `lm_head` native is practically free.
   - **Full-sequence logits (training / PPL evaluation, `M = 1024`):** Because `1B–1.5B` models carry `128k–152k` vocabularies, `lm_head` accounts for **`21.26%`** (`Llama-3.2-1B`) / **`15.12%`** (`Qwen2.5-1.5B`) of total linear MACs (`7.00%` on `Llama-3-8B`), and **`8.9%–9.5%`** (`9.18–10.40 ms`) / **`6.7%–7.5%`** (`8.27–9.24 ms`) of real forward wall time on GB205. In full-sequence workloads, keeping `lm_head` native is therefore an explicit ~`7%–10%` time-vs-accuracy trade-off rather than free.
4. **Activation-only vs. `act×W` calibration:** Across all 288,768 tokens (verified on the `FP32-RN` full sweep and 12k–16k true-FP64 runs on both models), `C1+B1+K-perm(act×W) − C1+B1+K-perm(act) = -7.56e-9 [-6.54e-8, +5.15e-8]` (`n.s.`). Simpler activation-RMS calibration (`rms(X)`) is sufficient.
5. **K-perm runtime-cost scoping:** Offline folding is strictly zero-overhead for `down_proj` (folds into `gate_proj`/`up_proj` output rows) and per-head for `o_proj` (folds into `v_proj` output rows). For `q/k/v_proj` and `gate/up_proj`, per-layer permutations differ and require a fused permuted store in the preceding `RMSNorm` kernel (~0 overhead, requires kernel support).

### 3.3 BF16 deployment control & `BF16 → FP16 → BF16` round-trip census (`Llama-3.2-1B` & `Qwen2.5-1.5B`)

Because `BF16` has an 8-bit exponent vs. `FP16`'s 5-bit exponent, checking `max|x| < 65504` only rules out **overflow**; values with `|x| < 2⁻¹⁴` enter `FP16`'s subnormal range (losing BF16 mantissa bits below `2⁻¹⁷`), and values `≤ 2⁻²⁵` underflow to `0` under round-to-nearest-even `FP16`. Measuring exact `BF16 → FP16 → BF16` round-trips across all linear weights and 16 chunks (`16,384` tokens) of real WikiText-2-raw activations shows that internal `BF16 → FP16` casting is **not bit-lossless**, but the affected subnormal fraction is tiny and perturbs tensor energy by only `10⁻¹⁰–10⁻⁸` `rel_L2`:

| Model & Tensor (all `nn.Linear`, 16k tokens) | Elements | `max\|x\|` (ovf `>65504`) | `< 2⁻¹⁷` (subnormal loss) | Changed on `BF16→FP16→BF16` | Non-zero underflow `→ 0` | Round-trip `rel_L2` |
|---|---:|---:|---:|---:|---:|---:|
| `Llama-3.2-1B` weights | `1.236B` | `1.23` (`0`) | `425,200` | `283,704` (`0.0230%`) | `1,736` (`0.00014%`) | `1.80e-08` |
| `Llama-3.2-1B` activations | `5.402B` | `1,344.0` (`0`) | `2,819,459` | `1,948,418` (`0.0361%`) | `19,050` (`0.00035%`) | `1.19e-09` |
| `Qwen2.5-1.5B` weights | `1.544B` | `3.06` (`0`) | `361,775` | `62,666` (`0.0041%`) | `479` (`0.00003%`) | `6.06e-09` |
| `Qwen2.5-1.5B` activations | `8.363B` | `5,184.0` (`0`) | `1,093,172` | `748,051` (`0.0089%`) | `31,957` (`0.00038%`) | `2.71e-10` |

Keeping weights and inter-layer activations in `BF16` and comparing against unbiased `BF16-input FP32-RN` (24k–32k tokens):
- **Deploy (`BF16 lm_head`):** `cuBLAS BF16→FP32` (`8.57e-4` / `9.91e-4`), `Pure B1 (N=3)` (`8.50e-4` / `9.86e-4`), `C1+B1 (N=2)` (`8.46e-4` / `9.82e-4`), and `C1+B1+K-perm` (`8.39e-4` / `9.87e-4`) are **statistically indistinguishable** on both models; unflushed `Pure F16` degrades by **`+33%–46%`** (`1.25e-3` / `1.32e-3`).
- **High-Res (`FP32 lm_head`, Llama BF16):** Even after removing the `BF16 lm_head` cast (`8.26e-4 → 2.86e-4` on 24k; `8.42e-4 → 2.94e-4` on 32k), `Pure B1` (`2.89e-4`) and `C1+B1+K-perm` (`2.82e-4`) remain indistinguishable from `Mode0` (`2.86e-4`), while `Pure F16` rises **`2.16×`** (`6.18e-4`).

### 3.4 Hardware-measured FP16 partial-sum overflow headroom (`65504 / max|tmp_f16|`)

Measured via in-kernel `atomicMax` on every `HMMA.F16` partial sum before flush:

| Model (scope) | Worst Layer | `Pure F16` (`headroom`) | `Pure B1 (N=3)` (`headroom`) | `C1+B1` no-perm (`headroom`) | `C1+B1 + K-perm` (`headroom`) |
|---|---|---:|---:|---:|---:|
| **`Llama-3.2-1B`** (all 113 layers) | `L1.mlp.down_proj` | `470.8` (`139×`) | `457.5` (`143×`) | `457.5` (`143×`) | Global max **`3.7`** (`17,736×`, **`124×` lower**) |
| **`Qwen2.5-1.5B`** (all 197 layers) | `L26.mlp.down_proj` | **`7,240.0` (`9.0×`)** | **`2,930.0` (`22.4×`)** | `2,930.0` (`22.4×`) | `L26`: **`21.4` (`3,062×`, `137×` lower)**; global max `150.5` @ `L27` (`435×`) |
| **`Qwen2.5-7B`** (**`L0–L5` only**, shard 1) | `L3` / `L4.mlp.down_proj` | **`9,048.0` (`7.2×` @ L3)** | `205.2` @ L3 / `317.8` @ L4 (`206×`) | `165.8` / `269.8` | **`12.9` @ L3 (`5,085×`)** / **`3.3` @ L4 (`19,636×`)** |

---

## 4. Single-Sided Dual `QMMA.SF` (`W8A16` / `W4A16` / `FP8-KV`) & Exact INT8-Block Negative Verdict

When weights are already quantized to FP8 (`MXFP8`) or FP4 (`MXFP4`)—or attention keys sit in an FP8 KV cache—while activations / queries stay in BF16, each 32-element `K`-block of BF16 activations can be split as $X = s(H + L/16)$ (power-of-2 `UE8M0` scale $s$ mapping block peak into $[128, 256)$, nearest `E4M3FN` $H$, and `16×`-amplified `E4M3FN` rounding residual $L$). Two full-rate `QMMA.SF.16832` instructions accumulate $H \cdot W$ (scale $s$) and $L \cdot W$ (scale $s/16 \Rightarrow \text{scale}_L = \text{scale}_H - 4$) directly into the **same FP32 accumulator**, reusing the weight registers with zero second accumulator and zero epilogue merge. Full raw records are archived in [reports/gb205-2f18/route2-dual-qmma-621.01.json](../reports/gb205-2f18/route2-dual-qmma-621.01.json), the SM 12.0 CUDA kernel/benchmark is in [software/dual_qmma_tma_ws.cu](../software/dual_qmma_tma_ws.cu), and CPU-only verification runs via `python3 software/verify_dual_fp8.py`.

### 4.1 `QMMA.SF.16832` hardware probes: zero FTZ, 23-bit RZ accumulator & shared-acc throughput

- **Zero FTZ on E4M3 subnormals (`16,384×` exact dynamic range):** Probing all 14 non-zero `E4M3` subnormals across two `UE8M0` scales (`0x7F`/`0x83`) yields **`28 / 28` bit-exact (`FTZ = NO`)** on both `E4M3 × E4M3` and `E4M3 × E2M1`. Because hardware preserves subnormals, the provably lossless BF16 range widens `8×` beyond normal-only $[1/8, 256)$ (`2,048×`) down to **`[2⁻⁶, 256)` (`16,384×`, 14 consecutive binary exponents)**.
- **`E2M1` register packing & `scale_vec::1X` lane mapping:** In `kind::mxf8f6f4` (`m16n8k32.e4m3.e2m1`), each 4-bit `E2M1` weight occupies **bits `[5:2]`** of an 8-bit lane (`nib << 2`, verified **`64 / 64` bit-exact** across all 16 codes × 4 byte lanes). Offline interleaved `(k, k+16)` 4-bit packing unpacks directly to `b0, b1` with two shift-mask ops (halving weight GMEM/SMEM traffic and `ldmatrix` calls vs. FP8). For `scale_vec::1X {0,0}`, `A` scales come from `t ∈ {0,1}` byte 0 (row `group + 8·t`) and `B` scales from `t = 0` byte 0 (col `group`).
- **Full 23-bit mantissa, RZ rounding & `RN16` synergy (`128×128` matrix statistics):** `QMMA.SF.16832` retains a **full 23-bit IEEE FP32 mantissa** (`1.0 + 2⁻²³ → 0x3f800001`, unlike Hopper's FP22 WGMMA) with **RZ (round-toward-zero)** truncation (`1.0 + 1.5·2⁻²⁴ → +0 ulp`, `1.0 + 1.75·2⁻²⁴ → +0 ulp`), identical to `HMMA.F32`. Exhaustively testing all `3,584` BF16 values in `[2⁻⁶, 256)` confirms **`3,584 / 3,584` bit-exactness** (`max_rel = 0.000e+00`) for both `W8A16` and `W4A16`. To eliminate single-scalar cancellation artifacts, we measure matrix-level `rel_L2` across **`128×128` outputs (`16,384` dot products)** against exact GPU FP64 (`cublasDgemm` on identical dequantized weights):

| Matrix Statistics (`128×128` outputs vs. exact FP64) | Weight | `cuBLAS BF16` (split-K) | `Single-FP8` | `Dual-FP8` (unflushed) | `Dual-FP8 + RN16` | `RN16` gain vs. `Dual` |
|---|---|---:|---:|---:|---:|---:|
| `K = 4,096` zero-mean `N(0,1)` | `W8A16` | `8.713e-08` | `2.680e-02` | `1.401e-06` | **`1.503e-07`** | **`9×`** |
| `K = 4,096` zero-mean `N(0,1)` | `W4A16` | `5.383e-08` | `2.676e-02` | `1.671e-07` | **`5.546e-08`** | **`3×`** |
| `K = 65,536` biased `N(0.25,1)` | `W8A16` | `1.452e-06` | `1.357e-03` | `5.597e-05` | **`2.080e-07`** | **`269×`** |
| `K = 65,536` biased `N(0.25,1)` | `W4A16` | `1.914e-07` | `1.346e-03` | `2.902e-05` | **`1.530e-07`** | **`190×`** |

- **Shared-accumulator instruction throughput:** At `16 warps/SM`, back-to-back `H` then `L` into the same `c[k]` (`4T`) sustains `1941.7` raw = `970.8` eff FLOP/SM/clk—identical to staggered `H[0..3] → L[0..3]` (**zero RAW stall**). Scaling to `8T` reaches **`1993.5` raw → `996.7` eff FLOP/SM/clk (`1.947×` of native 512)** on both `W8A16` and `W4A16`.

### 4.2 Prefill speed–accuracy frontier: `W4A4`, `Single-FP8`, `Dual-FP8` & `Marlin-HMMA` (GB205, 140 W)

[software/dual_qmma_tma_ws.cu](../software/dual_qmma_tma_ws.cu) ships three deployment modes: **Pre-split** (`(H, L, sH)` emitted by a fused `RMSNorm`/`SwiGLU` prologue or reused across `q/k/v` and `gate/up`), **2-kernel** (standalone split kernel + pre-split GEMM, total wall time including split), and **Online** (single kernel reading raw BF16 via TMA and splitting asynchronously in producer warps). To compare against real deployment competitors on identical shapes, we benchmark—under the same 140 W steady-state NVML harness—a full `TMA-WS 128×256×64` **Marlin-style `Dequant-HMMA`** baseline (loading compressed `W8`/`W4`, dequantizing once per `N`-tile in registers hoisted outside all 4 `M`-tiles, then issuing `HMMA.16816.F32.BF16`; bit-exact `rel_L2 = 0.000e+00` vs. same-weight `cuBLAS BF16`) alongside **`Single-FP8` (`W8A8`/`W4A8`)** and full-rate **`W4A4` (`OMMA.SF.16864 kind::mxf4`**, verified `rel_L2 = 0.000e+00` vs. `dequant(A4)×dequant(W4)`):

#### (1) Complete speed–accuracy frontier on `4096×14336×4096` (Llama-3-8B Up, steady-state 140 W NVML)

| Scheme | Ceiling (`FL/SM/clk`) | Activation Precision | Wall `TF/s` | `FLOP/SM/clk` | SM Clock / Power | `rel_L2` vs. Same-Weight `BF16` Act | vs. `Marlin-HMMA` (wall / per-GHz) |
|---|---:|---|---:|---:|---:|---:|---:|
| `W4A4` (`OMMA.SF.16864`) | `4096` | `FP4` | **`309.04`** | **`3128.6`** | `2.15 GHz` / `139.2 W` | `1.155e-01` (`11.5%`) | `5.01× / 6.42×` |
| `Single-FP8 W4A8` (`QMMA.SF`) | `2048` | `FP8` | **`177.59`** | **`1808.6`** | `2.13 GHz` / `139.7 W` | `2.665e-02` (`2.66%`) | `2.88× / 3.71×` |
| `Single-FP8 W8A8` (`QMMA.SF`) | `2048` | `FP8` | **`158.45`** | **`1705.6`** | `2.02 GHz` / `140.1 W` | `2.665e-02` (`2.66%`) | `2.63× / 3.50×` |
| `cuBLAS FP16→FP16` (control) | `1024` | `FP16` (FP16 acc) | `105.05` | `977.2` | `2.34 GHz` / `140.1 W` | — | `1.70× / 2.00×` |
| **`Dual-FP8 W4A16` (pre-split)** | `1024` | **BF16 exact** | **`99.36`** | **`969.2`** | `2.23 GHz` / `139.2 W` | **`3.992e-08`** | **`1.61× / 1.99×`** |
| **`Dual-FP8 W4A16` (2-kernel)** | `1024` | **BF16 exact** | **`96.57`** | **`942.4`** | `2.23 GHz` / `140.1 W` | **`3.992e-08`** | **`1.56× / 1.93×`** |
| **`Dual-FP8 W8A16` (pre-split)** | `1024` | **BF16 exact** | **`95.35`** | **`979.2`** | `2.12 GHz` / `139.8 W` | **`2.214e-07`** | **`1.58× / 2.01×`** |
| **`Dual-FP8 W8A16` (2-kernel)** | `1024` | **BF16 exact** | **`93.97`** | **`946.8`** | `2.16 GHz` / `139.9 W` | **`2.214e-07`** | **`1.56× / 1.94×`** |
| `cuBLAS BF16→FP32` (control) | `512` | `BF16` | `62.22` | `500.3` | `2.70 GHz` / `139.7 W` | `0` (ref) | `1.01× / 1.03×` |
| `Marlin-HMMA W4A16` (dequant) | `512` | `BF16` | `61.71` | `487.5` | `2.75 GHz` / `137.1 W` | `0.000e+00` | `1.00× / 1.00×` |
| `Marlin-HMMA W8A16` (dequant) | `512` | `BF16` | `60.22` | `487.7` | `2.68 GHz` / `140.0 W` | `0.000e+00` | `1.00× / 1.00×` |

> Additional shapes archived in `route2-dual-qmma-621.01.json`: (1) on `4096×4096×4096`, `Marlin-HMMA W8/W4` hits `56.81 / 57.26 TF/s` (`453.9 / 453.2 FL/SM/clk`, beating `cuBLAS BF16` at `55.85 TF/s`), while `Dual-FP8 W8/W4 (pre-split)` reaches **`90.30 / 93.61 TF/s`** (**`1.59× / 1.63×` wall, `2.00× / 1.98×` per-GHz vs. Marlin**), `Single-FP8` hits `156.27 / 171.51 TF/s`, and `W4A4` hits `304.35 TF/s`; (2) on `512×14336×4096`, `Marlin-HMMA W8/W4` hits `58.25 / 59.54 TF/s` (`471.5 / 471.6 FL/SM/clk`, beating `cuBLAS` at `53.01 TF/s`), while `Dual-FP8 (pre-split)` reaches **`90.60 / 94.89 TF/s`** (**`1.56× / 1.59×` wall, `1.99× / 1.97×` per-GHz vs. Marlin**), `Single-FP8` hits `151.08 / 167.08 TF/s`, and `W4A4` hits `282.27 TF/s`.

#### (2) Deployment split modes & same-weight true-FP64 accumulation error ([software/dual_qmma_tma_ws.cu](../software/dual_qmma_tma_ws.cu))

Comparing Pre-split, 2-kernel, and Online single-kernel variants against **CPU true FP64 using the identical dequantized weights** (measuring accumulation/split fidelity only, not weight-quantization error):

| Shape (`M×N×K`) | Kernel | Wall `TF/s` | `FLOP/SM/clk` | SM Clock / Power | `rel_L2` vs. Same-Weight FP64 | vs. `cuBLAS BF16` (wall / per-GHz) |
|---|---|---:|---:|---:|---:|---:|
| `4096×14336×4096` (Up) | `cuBLAS BF16→FP32` (same W8 dequant) | `62.60` | `503.7` | `2.70 GHz` / `132.4 W` | `1.47e-06` | `1.00× / 1.00×` |
| `4096×14336×4096` (Up) | **`Dual-FP8 W8A16` (pre / 2k / online)** | **`96.06` / `92.74` / `85.76`** | **`980.8` / `951.1` / `893.4`** | `2.09–2.13 GHz` | `1.38e-06` | **`1.53×–1.37×` / `1.95×–1.77×`** |
| `4096×14336×4096` (Up) | **`Dual-FP8 W4A16` (pre / 2k / online)** | **`98.86` / `96.86` / `88.67`** | **`968.6` / `941.3` / `892.1`** | `2.16–2.24 GHz` | `1.67e-07` | **`1.58×–1.42×` / `1.92×–1.77×`** |
| `4096×4096×4096` (Q/O) | `cuBLAS BF16→FP32` | `55.35` | `484.7` | `2.48 GHz` / `139.9 W` | `1.53e-06` | `1.00× / 1.00×` |
| `4096×4096×4096` (Q/O) | **`Dual-FP8 W8A16` (pre / 2k / online)** | **`90.68` / `85.13` / `82.20`** | **`907.5` / `843.9` / `836.8`** | `2.14–2.19 GHz` | `1.39e-06` | **`1.64×–1.48×` / `1.87×–1.73×`** |
| `4096×4096×4096` (Q/O) | **`Dual-FP8 W4A16` (pre / 2k / online)** | **`94.97` / `87.82` / `84.70`** | **`904.3` / `828.3` / `829.9`** | `2.22–2.31 GHz` | `1.55e-07` | **`1.72×–1.53×` / `1.87×–1.71×`** |
| `512×14336×4096` (B512) | `cuBLAS BF16→FP32` | `52.66` | `509.0` | `2.25 GHz` / `139.4 W` | `1.63e-06` | `1.00× / 1.00×` |
| `512×14336×4096` (B512) | **`Dual-FP8 W8A16` (pre / 2k / online)** | **`91.45` / `88.93` / `82.26`** | **`957.1` / `914.6` / `860.8`** | `2.08–2.11 GHz` | `1.53e-06` | **`1.74×–1.56×` / `1.88×–1.69×`** |
| `512×14336×4096` (B512) | **`Dual-FP8 W4A16` (pre / 2k / online)** | **`94.53` / `91.71` / `85.87`** | **`923.4` / `885.8` / `844.1`** | `2.21–2.25 GHz` | `1.78e-07` | **`1.79×–1.63×` / `1.81×–1.66×`** |

Key takeaways: (1) **Why `Dual-FP8` beats `Marlin-HMMA` by `~2×` per clock:** our `Marlin-HMMA` baseline sustains `487.5–487.7 FLOP/SM/clk` (`95.3%` of the 512 ceiling, faster than `cuBLAS BF16` on `4096×4096` and `512×14336`), proving it is a strong baseline—yet because it routes through the throttled `HMMA.16816.F32.BF16` pipe, `Dual-FP8` beats it by **`1.56×–1.63×` wall-clock (`1.97×–2.01×` per-GHz)** across all prefill shapes; (2) **Proper scoping of accumulation error:** the true-FP64 `rel_L2` column compares accumulation/split fidelity on the **same quantized weights** (not model-level accuracy vs. unquantized BF16): `W8A16` (`1.38e-6–1.53e-6`) matches `cuBLAS BF16` (`1.47e-6–1.63e-6`, both RZ-truncated 23-bit FP32), while `W4A16` (`1.55e-7–1.78e-7`) is smaller simply because 4-bit `E2M1` products have fewer mantissa bits and trigger fewer RZ truncations; (3) **`2-kernel` vs. `online`:** `2-kernel` splits `A` once (`0.016–0.172 ms`) and beats `online` by `6%–9%`, while `online` delivers `1.66×–1.77×` per-GHz with zero workspace allocation.

### 4.3 Cold-L2 decode sweep (`M = 1, 8, 16, 32, 64`): zero 2nd-MMA cost & `M = 32–64` compute crossover

Because a single `14336×4096` `W4` matrix (`31.2 MB`) fits inside GB205's **`36.0 MB` L2 cache**, looping on one buffer would produce artificial L2-hit bandwidth (`>1 TB/s`). To reflect true multi-layer LLM decode streaming from GDDR7, we allocate **rotating cold-L2 weight pools of `281–352 MB`** (`BF16` ×3 = `352 MB`, `W8` ×5 = `303 MB`, `W4` ×9 = `281 MB`, all `> 7.8× L2`) and compare `cuBLAS BF16`, `Marlin-HMMA`, `Single-FP8`, and `Dual-FP8` using the same double-buffered SMEM-`A` + `ldmatrix.x4` Split-K decode kernel:

| `M` (`split_k`) | `cuBLAS BF16` (`µs`) | `Marlin-HMMA` `W8 / W4` (`µs`) | `Single-FP8` `W8 / W4` (`µs`) | **`Dual-FP8` pre** `W8 / W4` (`µs`) | **`Dual-FP8` incl. split** `W8 / W4` (`µs`) | `Dual` vs. `cuBLAS` (`W8 / W4`) | `Dual` vs. `Marlin` (`W8 / W4`) | `Dual / Single` (`W8 / W4`) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **`1`** (`4`) | `184.5` | `107.1` / `86.5` | `113.7` / `80.7` | **`106.1` / `69.5`** | `117.7` / `70.9` | **`1.74× / 2.66×`** | **`1.01× / 1.25×`** | **`0.93× / 0.86×`** |
| **`8`** (`4`) | `195.1` | `110.2` / `68.5` | `116.8` / `66.7` | **`110.5` / `65.2`** | `113.4` / `70.2` | **`1.77× / 2.99×`** | **`1.00× / 1.05×`** | **`0.95× / 0.98×`** |
| **`16`** (`4`) | `196.6` | `122.0` / `71.4` | `125.7` / `70.2` | **`115.1` / `68.7`** | `115.0` / `74.3` | **`1.71× / 2.86×`** | **`1.06× / 1.04×`** | **`0.92× / 0.98×`** |
| **`32`** (`2`) | `233.0` | `119.4` / **`104.5`** | `123.4` / `76.2` | **`125.2` / `75.1`** | `125.9` / `86.0` | **`1.86× / 3.10×`** | `0.95×` / **`1.39×`** | **`1.02× / 0.99×`** |
| **`64`** (`2`) | `224.6` | **`231.3` / `196.9`** | `136.3` / `87.4` | **`154.2` / `120.1`** | `155.3` / `130.2` | **`1.46× / 1.87×`** | **`1.50× / 1.64×`** | `1.13× / 1.37×` |

Two clear roofline regimes emerge: (1) **Memory-bound low-batch decode (`M = 1–16`):** the second `QMMA.SF` pass is completely hidden behind GDDR7 weight streaming (`Dual / Single = 0.86×–0.98×`), yielding **`1.71×–1.77×` (W8)** and **`2.66×–2.99×` (W4)** cold-VRAM speedup over `cuBLAS BF16` with exact BF16 activations (`L2 = 9.4e-8–1.3e-6` vs. Single-FP8 `2.65e-2`) and only `1–6 µs` overhead when including a standalone split kernel; (2) **Batched / speculative decode compute crossover (`M = 32–64`):** `Marlin-HMMA` slams into the 512 FLOP/SM/clk HMMA ceiling at `M = 32` (`W4: 104.5 µs`, `36.0 TF/s`) and `M = 64` (`W8/W4: 231.3 / 196.9 µs`, `32.5 / 38.2 TF/s`), whereas `Dual-FP8` scales past it to **`50.1 / 62.6 TF/s`**, beating `Marlin-HMMA` by **`1.39×` (`M = 32 W4`)** and **`1.50× / 1.64×` (`M = 64 W8/W4`)**.

### 4.4 Landing pre-split in practice: fused `RMSNorm` + `SwiGLU` full Llama-3-8B MLP end-to-end

Making the `95–99 TF/s` pre-split path real in production requires two things: (1) emitting **`sH[K/32, M]` (K-block-outer)** alongside row-major `(H, L)` directly from the preceding elementwise epilogue (`sH` adds only `1/32` of tensor bytes); (2) reusing a single `RMSNorm` split across **`gate_proj + up_proj`** (or `q/k/v_proj`), and emitting `down_proj`'s split directly from **`SwiGLU`**.

We added single-pass register-resident `rmsnorm_fused_dual_split_kernel` and vectorized `swiglu_fused_dual_split_kernel` directly to [software/dual_qmma_tma_ws.cu](../software/dual_qmma_tma_ws.cu) and timed a complete Llama-3-8B MLP (`RMSNorm → gate_proj + up_proj → SwiGLU → down_proj`, `H = 4096, I = 14336`, steady-state 140 W NVML):

| Seq `M` | Elementwise Microbenchmark (`µs`) | Full End-to-End MLP Pipeline | Total Time (`ms`) | Eff `TF/s` | SM Clock / Power | vs. BF16 MLP (wall / per-GHz) | Final `rel_L2` |
|---:|---|---|---:|---:|---:|---:|---:|
| **`4096`** | `RMSNorm`: BF16 `170.1` vs. Fused **`171.2`** (`+1.1 µs`, saves `170.7 µs`)<br>`SwiGLU`: BF16 `1209.1` vs. Fused **`1178.2`** (`-30.9 µs`, saves `531.8 µs`) | `BF16 Baseline` (`cuBLAS + elem`) | `25.68` | `56.20` | `2.58 GHz` / `139.6 W` | `1.00× / 1.00×` | `0` |
| `4096` | | `Dual-FP8 W8A16` (unfused 2-kernel) | `17.05` | `84.64` | `2.18 GHz` / `139.6 W` | `1.51× / 1.78×` | `3.33e-05` |
| `4096` | | **`Dual-FP8 W8A16` (Fused Epilogue)** | **`16.45`** | **`87.71`** | `2.19 GHz` / `139.9 W` | **`1.56× / 1.84×`** | `3.33e-05` |
| `4096` | | **`Dual-FP8 W4A16` (Fused Epilogue)** | **`15.95`** | **`90.50`** | `2.26 GHz` / `139.3 W` | **`1.61× / 1.84×`** | `1.41e-05` |
| **`512`** | `RMSNorm`: BF16 `11.6` vs. Fused **`13.5`** (`+1.9 µs`)<br>`SwiGLU`: BF16 `130.2` vs. Fused **`132.3`** (`+2.1 µs`) | `BF16 Baseline` (`cuBLAS + elem`) | `3.60` | `50.12` | `2.31 GHz` / `139.1 W` | `1.00× / 1.00×` | `0` |
| `512` | | `Dual-FP8 W8A16` (unfused 2-kernel) | `2.26` | `79.89` | `2.27 GHz` / `138.8 W` | `1.59× / 1.63×` | `2.93e-05` |
| `512` | | **`Dual-FP8 W8A16` (Fused Epilogue)** | **`2.23`** | **`81.06`** | `2.26 GHz` / `138.9 W` | **`1.62× / 1.65×`** | `2.93e-05` |
| `512` | | **`Dual-FP8 W4A16` (Fused Epilogue)** | **`2.15`** | **`83.71`** | `2.37 GHz` / `139.0 W` | **`1.67× / 1.63×`** | `1.36e-05` |

Fusing the split + `sH[K/32, M]` write into `RMSNorm` and `SwiGLU` adds essentially zero latency (`+1.1 µs` / `-30.9 µs` at `M = 4096`; `+1.9 / +2.1 µs` at `M = 512`), delivering **`1.56×–1.67×` end-to-end MLP wall-clock speedup (`1.84×` per-GHz at `M = 4096`)**. Where fusion is unavailable (e.g., direct residual consumers or generic framework boundaries), unfused 2-kernel falls back gracefully to `1.51×–1.59×` wall-clock (only `~1.5%–3.5%` slower).

### 4.5 Real-model 517M-block distribution census & exact INT8-affine negative verdict

Scanning all linear weights and 16,384 WikiText-2-raw activation tokens across `Llama-3.2-1B` and `Qwen2.5-1.5B` (**`517,031,936` 32-element BF16 blocks / `16.55 billion` elements**), with exact Python `Fraction` dyadic GCD verification for 256-level uniform grids:

| Model & Tensor | 32-elem Blocks | `nz ∈ [1/8,256)` | `nz ∈ [2⁻⁶,256)` | **Dual-FP8 Exact Bit-Match** | `rel_L2` (Single → Dual FP8) | `B2` (`≤16×` DR) | **Exact Lossless INT8 Affine** |
|---|---:|---:|---:|---:|---|---:|---:|
| `Llama-3.2-1B` weights | `38,617,088` | `99.861%` | **`99.983%`** | **`99.9885%`** | `2.659e-2` → **`8.185e-9`** (`3.25M×`) | `2.0658%` | **`0` (`0.000000%`)** |
| `Llama-3.2-1B` activations | `168,820,736` | `99.497%` | **`99.917%`** | **`99.9409%`** | `2.677e-2` → **`2.274e-7`** (`118k×`) | `0.6669%` | **`0` (`0.000000%`)** |
| `Qwen2.5-1.5B` weights | `48,236,544` | `99.857%` | **`99.983%`** | **`99.9974%`** | `2.664e-2` → **`4.984e-9`** (`5.34M×`) | `1.9818%` | **`0` (`0.000000%`)** |
| `Qwen2.5-1.5B` activations | `261,357,568` | `99.432%` | **`99.916%`** | **`99.9427%`** | `2.668e-2` → **`6.017e-7`** (`44.3k×`) | `0.4824%` | **`0` (`0.000000%`)** |

Two sharp empirical verdicts: (1) exploiting E4M3 subnormals lifts real-LLM Dual-FP8 exact bit-identity to **`99.94%`** on activations (`rel_L2 = 2.3e-7–6.0e-7`) and **`99.99%+`** on weights (`5e-9–8e-9`); (2) **exact lossless INT8 affine block detection passes `0 / 517,031,936` (`0.000000%`)** on continuous BF16 LLM tensors (`100.00%` of blocks are mixed-sign; while `3.91M` blocks clear the necessary `span ≤ 255·min_diff` screen, zero survive exact dyadic GCD). That closes online lossless BF16→INT8 block dispatch for generic BF16 LLMs (it applies only to tensors already pre-quantized onto a `≤8-bit` uniform grid, where no online check is needed).

### 4.6 End-to-end 32k WikiText-2-raw LLM KL evaluation (K-smoothed `FP8-KV`, AWQ+MSE `W4`, dual `lm_head` lenses)

Across 32 chunks (`32,768` tokens) on both models—evaluating `W8_MXFP8`, uncalibrated RTN `W4_MXFP4`, AWQ+MSE calibrated `W4_MXFP4_AWQ_MSE` (per-channel activation-RMS scaling `α=0.5` calibrated on 8 held-out WikiText-2 validation chunks + per-block-32 weighted-MSE UE8M0 selection), and **SageAttention-style per-channel K-smoothed `FP8-KV QKᵀ` (`K_smooth = K − mean_T(K)`, mathematically exact under softmax shift-invariance)**—we record exact FP64 `log_softmax` KL under **both Deploy (`BF16 lm_head`) and High-Res (`FP32 lm_head`) lenses** (4,000-resample paired bootstrap, `seed=2026`, verifiable via `python3 software/verify_dual_fp8.py`):

| Model & Regime (32,768 tokens) | Activation / `Q` | `PPL` | Local KL vs. Same-Weight `BF16` (Deploy / HiRes) | KL vs. Orig Unquantized BF16 | Single Extra KL vs. Orig | Single / Dual Local KL Ratio (Deploy `[95% CI]` / HiRes) |
|---|---|---:|---:|---:|---:|---:|
| `Llama-3.2-1B` **`W8_MXFP8`** (orig `11.1352`) | `BF16` / `Single` / **`Dual`** | `11.2261` / `11.3068` / **`11.2246`** | `0` / `7.403e-3` / **`8.298e-4`** (`2.784e-4` HR) | `7.280e-3` / `1.390e-2` / **`7.281e-3`** | **`+90.9%`** (Dual `+0.02%`) | **`8.9×`** `[8.6×, 9.3×]` / **`24.2×`** |
| `Llama-3.2-1B` **`FP8-KV-Smooth`** (`K−μ`) | `BF16` / `Single` / **`Dual`** | `11.2108` / `11.2955` / **`11.2129`** | `0` / `1.136e-2` / **`2.613e-3`** (`1.959e-3` HR) | `7.505e-3` / `1.399e-2` / **`7.440e-3`** | **`+86.4%`** (Dual `-0.87%`) | **`4.3×`** `[4.1×, 4.6×]` / **`5.5×`** |
| `Llama-3.2-1B` **`W4_MXFP4`** (uncal. RTN) | `BF16` / `Single` / **`Dual`** | `13.5875` / `13.7167` / **`13.5892`** | `0` / `9.932e-3` / **`9.504e-4`** (`3.780e-4` HR) | `2.027e-1` / `2.113e-1` / **`2.028e-1`** | `+4.2%` (Dual `+0.04%`) | **`10.5×`** `[10.0×, 10.9×]` / **`24.5×`** |
| `Llama-3.2-1B` **`W4_MXFP4_AWQ_MSE`** (cal.) | `BF16` / `Single` / **`Dual`** | **`12.7064`** / `12.7942` / **`12.7118`** | `0` / `8.845e-3` / **`9.339e-4`** (`3.565e-4` HR) | `1.330e-1` / `1.401e-1` / **`1.330e-1`** | **`+5.3%`** (Dual `+0.01%`) | **`9.5×`** `[9.1×, 9.9×]` / **`23.0×`** |
| `Qwen2.5-1.5B` **`W8_MXFP8`** (orig `9.9583`) | `BF16` / `Single` / **`Dual`** | `9.9902` / `10.0435` / **`9.9889`** | `0` / `8.996e-3` / **`9.598e-4`** (`3.409e-4` HR) | `7.444e-3` / `1.538e-2` / **`7.481e-3`** | **`+106.6%`** (Dual `+0.50%`) | **`9.4×`** `[8.9×, 9.9×]` / **`24.5×`** |
| `Qwen2.5-1.5B` **`FP8-KV-Smooth`** (`K−μ`) | `BF16` / `Single` / **`Dual`** | **`9.9896`** / `10.0235` / **`9.9843`** | `0` / `6.128e-3` / **`1.895e-3`** (`1.237e-3` HR) | `4.707e-3` / `8.035e-3` / **`4.724e-3`** | **`+70.7%`** (Dual `+0.36%`) | **`3.2×`** `[3.1×, 3.4×]` / **`4.4×`** |
| `Qwen2.5-1.5B` **`W4_MXFP4`** (uncal. RTN) | `BF16` / `Single` / **`Dual`** | `11.1879` / `11.2609` / **`11.1893`** | `0` / `9.112e-3` / **`1.011e-3`** (`3.847e-4` HR) | `1.446e-1` / `1.514e-1` / **`1.447e-1`** | `+4.7%` (Dual `+0.02%`) | **`9.0×`** `[8.7×, 9.3×]` / **`22.0×`** |
| `Qwen2.5-1.5B` **`W4_MXFP4_AWQ_MSE`** (cal.) | `BF16` / `Single` / **`Dual`** | **`10.9981`** / `11.0671` / **`11.0003`** | `0` / `8.964e-3` / **`1.035e-3`** (`3.990e-4` HR) | `1.154e-1` / `1.234e-1` / **`1.155e-1`** | **`+6.9%`** (Dual `+0.08%`) | **`8.7×`** `[8.3×, 9.0×]` / **`20.8×`** |

Key observations:
1. **Accurate scoping of end-to-end quality value:** `Dual-FP8` delivers its largest quality gain on **`W8_MXFP8`** (where `Single-FP8` adds **`+90.9%` / `+106.6%`** extra KL vs. the unquantized BF16 model) and **K-smoothed `FP8-KV QKᵀ`** (`Single-FP8` adds **`+86.4%` / `+70.7%`**), because activation and weight/KV quantization errors are comparable there; `Dual-FP8` restores exact `BF16`-activation quality (`+0.02%–0.50%`). On uncalibrated RTN `W4_MXFP4`, 4-bit weight error dominates (`KL = 0.145–0.203`), so `Single-FP8` adds only `+4.2% / +4.7%`. Under AWQ+MSE calibration, 4-bit weight KL drops `20%–34%` (`Llama PPL 13.59 → 12.71`, `Qwen 11.19 → 11.00`) and `Single-FP8`'s activation-error share rises to **`+5.3% / +6.9%`** (expected to rise further on 8B–70B models with smaller 4-bit weight error)—and even on `W4A16`, `Dual-FP8` already beats `Marlin-HMMA` by `1.56×–1.63×` in prefill and `1.39×–1.64×` in `M = 32–64` decode.
2. **Per-channel K smoothing rescues `Qwen2.5-1.5B` FP8-KV:** Without smoothing, `Qwen2.5-1.5B`'s static `k_proj` bias outliers (`k_max = 322` in layer 0) collapse naive block-32 FP8-KV to `PPL = 29.71` (`KL = 1.144`). Subtracting the per-channel sequence mean `K − mean_T(K)` restores a healthy **`9.9896`** baseline (`KL_orig = 4.707e-3`, `243×` lower), on which `Dual-FP8 Q` cleanly eliminates `Single-FP8`'s `+70.7%` penalty.
3. **High-Res `FP32 lm_head` confirms floor attribution:** Removing `BF16 lm_head` rounding drops `Dual-FP8` linear-layer local KL from `8.30e-4` / `9.60e-4` down to **`2.78e-4` / `3.41e-4`** (matching §3.3's `2.86e-4` BF16 high-res backbone floor), revealing a **`20.8×–24.5×`** local KL compression over `Single-FP8`.

---

## 5. Route 2 Two-Flagship Synthesis, Engineering Selection Table & External Context

On consumer Blackwell (SM 12.0), dense `BF16 / FP16`-activation GEMMs breaking past the `512 FLOP/SM/clk` clamp converge onto **two `1024 FLOP/SM/clk` (`2×`-ceiling) flagship paths partitioned cleanly by weight format**:

- **BF16 / FP16 weights → `Pure B1` / `C1+B1 (+ K-perm)`:** Bypasses the 32-cycle `HMMA.F32` cooldown gate for **`1.53×–1.69×`** per-GHz speedup via short-chunk `HMMA.F16` + FP32 flush (indistinguishable in BF16 deploy; `+6.5e-7` to `+1.35e-6` excess KL in FP16 deploy).
- **FP8 / MXFP4 weights (or FP8 KV cache) → Single-sided `Dual-FP8 QMMA.SF`:** Keeps exact BF16 activations and a full 23-bit FP32 accumulator at **`1.87×–1.95×`** per-GHz (**`1.56×–1.63×` wall / `1.97×–2.01×` per-GHz vs. `Marlin-HMMA`** in prefill; **`1.56×–1.67×` wall** end-to-end across a fused `RMSNorm`+`SwiGLU` Llama-3-8B MLP; ~0% 2nd-MMA overhead at `M = 1–16` cold-L2 decode and **`1.39×–1.64×` vs. `Marlin-HMMA`** at `M = 32–64`). **Whenever weights or keys are quantized, `Dual-FP8` is strictly preferred.**

| Workload / Scenario | Recommended Path | Empirical Basis |
|---|---|---|
| **FP8 / MXFP4 weights + BF16 activations (`W8A16` / `W4A16`) or K-smoothed FP8-KV `QKᵀ`** | **Single-sided Dual `QMMA.SF` (`X = s(H+L/16)`, shared FP32 acc)** | Prefill **`95.4–99.4 TF/s`** (**`1.56×–1.63×` wall / `1.97×–2.01×` per-GHz vs. `Marlin-HMMA`**); fused `RMSNorm`+`SwiGLU` Llama-3-8B MLP **`1.56×–1.67×`** wall end-to-end; cold-L2 decode `M=1–16` **`1.71×–2.99×`** vs. `cuBLAS` (`Dual/Single ≈ 1.0×`), `M=32–64` **`1.39×–1.64×`** vs. `Marlin-HMMA`; eliminates Single-FP8's **`+71%–107%`** extra KL on `W8` & K-smoothed `FP8-KV` (`20.8×–24.5×` High-Res local KL cut) |
| Max throughput when `2.7%–11.5%` activation error is acceptable | **`Single-FP8` (`W8A8`/`W4A8`) or `W4A4` (`OMMA.SF.16864`)** | `4096×14336×4096` steady-state: `158.5 / 177.6 TF/s` (`2.66%` `rel_L2`) and **`309.0 TF/s`** (`11.5%` `rel_L2`) |
| Pure FP16 / BF16 weights & activations, near-Gaussian inference | **Pure `B1` (`N=3`, TMA-WS)** | `782–841 FLOP/SM/clk` on exact `K=4096` (`1.61–1.66×` per-GHz vs. `cuBLAS BF16`; `790–849` / `1.63–1.69×` on `K=4080`); single-GEMM `rel_L2 ≈ 2.94e-4`; indistinguishable from `cuBLAS BF16→FP32` in BF16 deploy and `+1.35e-6` excess KL in FP16 deploy |
| Pure FP16 / BF16 heavy-tailed / outlier layers (e.g., `down_proj` with `max\|X\| > 10³`) | **`C1 + B1 (N=2)` + `K`-dim perm (`rms(X)` calib)** | `+6.4%–6.5%` time per clock vs. Pure `B1`; cuts single-GEMM FP32-output `rel_L2` **~6×** (`4.9e-5`), peak FP16 partial sums **20×–137×**, and FP16 LLM excess KL **2.7×–2.9×** (`+6.5e-7` Deploy / `+4.6e-7` High-Res) |
| Large `K` with non-zero activation mean (e.g., weight-gradient GEMMs) | **`HMMA.F32` / Dual `QMMA.SF` + `RN16` flush** | Same throughput as native; **19×–84×** lower error than `cublasGemmEx`; cuts biased `128×128×65,536` Dual `QMMA.SF` RZ drift from `5.60e-5 / 2.90e-5` to **`2.08e-7 / 1.53e-7`** (**`269× / 190×`**) |
| `lm_head`, MoE routers, and other FP32-sensitive projections | **Keep native `HMMA.F32` (or `RN16`)** | `< 1.0%` wall time in last-token inference prefill (`0.73%–1.02%` on 1B–1.5B); in full-sequence training/eval (`6.7%–9.5%` wall / `15%–21%` MACs), weigh against the `+2.9e-7` to `+9.7e-7` KL penalty of replacing `lm_head` |
| FP8 / mixed FP8×FP6 / FP8×FP4 / pure FP6 | **`QMMA.SF` unit scale (`0x7F`)** (upcast pure `FP6 → E4M3`) | Full **`2048 FLOP/SM/clk`** (`2.00×`), **`0` bit differences** |

### External Blackwell software workarounds (third-party RTX 5090 reports, distinct from our GB205 measurements)

- **FP4 microscaling attention ([SageAttention3](https://arxiv.org/abs/2505.11594)):** Targets unthrottled `OMMA.SF` NVFP4 on Blackwell, reporting up to `~1038 TOPS` (`~5×` over FlashAttention) on RTX 5090 for long-sequence video/image diffusion attention; our K-smoothed Dual-FP8 `QKᵀ` complements it for FP8 KV-cache LLM attention requiring BF16-grade `Q` fidelity.
- **NVFP4 + fused low-rank outlier branch ([SVDQuant / Nunchaku](https://arxiv.org/abs/2411.05007)):** Absorbs activation/weight outliers via SVD into a fused low-rank branch around a 4-bit backbone, reporting `~3.1×` over W4A16 on RTX 5090; complements our standard-MXFP4 `Dual-FP8 W4A16` (no low-rank branch, `1.56×–1.63×` wall / `1.97×–2.01×` per-GHz vs. `Marlin-HMMA` in prefill).
- **INT8 Tensor Core emulation ([GEMMul8 / Ozaki-II](https://github.com/RIKEN-RCCS/GEMMul8)):** Uses unthrottled `IMMA.16832.S8.S8` (`2031.8 OP/SM/clk` on our GB205) with modular/CRT slicing to emulate FP64/FP32 scientific GEMMs; conversely, direct exact lossless BF16→INT8 block dispatch fails on `0 / 517M` continuous LLM blocks (§4.5).

### Pending / Out-of-Scope Verification Items
- **Cross-GPU confirmation:** Re-running the TMA-WS NVML clock/power split (`Pure B1`/`C1+B1` and Dual `QMMA.SF`) on a higher-TDP desktop **RTX 5080** (`~4.3 W/SM`) and on an unthrottled **RTX PRO 6000** (negative control where `HMMA.F32` has no 32-cycle gate).
- **Larger-model (8B–70B) calibrated W4 & deep 7B layers:** Larger models exhibit much smaller 4-bit weight quantization error, where `Dual-FP8`'s activation-accuracy advantage over `Single-FP8` on calibrated W4 should grow proportionally larger; `Qwen2.5-7B` partial-sum headroom above also covers early layers `L0–L5` (shard 1) only, leaving `L6–L27` unmeasured.
