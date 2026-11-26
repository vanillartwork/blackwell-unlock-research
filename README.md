[English](README.md) | [中文](README.cn.md)

# blackwell-unlock-research

**Attack-surface map, vulnerability research roadmap, and low-friction native Linux test kit built toward unlocking the 0.5× FP32-accumulation clamp on NVIDIA Blackwell RTX 50-series GPUs (GB20x).**

**Half of your NVIDIA GPU's tensor compute is locked out of the box.** On the same RTX 5090, dense FP16 inputs peak at about 419 TFLOP/s with FP16 accumulation and 209.5 TFLOP/s with FP32 accumulation. NVIDIA lists both figures in its architecture whitepaper. The sales page emphasizes AI TOPS, leaving buyers little basis for judging the cost in their own training or inference workloads. I found no adequate technical justification for the limit in the materials reviewed. In my view, its primary purpose is to create an artificial performance gap between consumer and professional products. Imposing that cost without clearly disclosing it at the point of sale is indefensible and shameful. [RTX 5090 specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/) · [Architecture whitepaper, Appendix A](https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf)

[![Blackwell dense tensor throughput ceiling (BF16 / FP16)](docs/throughput-ceiling.svg)](https://vanillartwork.github.io/blackwell-unlock-research/#en)

*Blackwell dense tensor throughput preview (BF16 / FP16, FP32 accumulation, desktop models) — **[open the interactive chart](https://vanillartwork.github.io/blackwell-unlock-research/#en)** to switch between BF16 / FP16 / FP8 / TF32 / FP32, toggle log scale, and compare desktop, laptop, RTX PRO, B200 and DGX Spark.*

| GPU | SMs | Reference boost | Factory peak | Hypothetical full rate |
|---|---:|---:|---:|---:|
| RTX 5090 | 170 | 2407 MHz | 209.5 | 419.0 |
| RTX 5080 | 84 | 2617 MHz | 112.6 | 225.1 |
| RTX 5070 | 48 | 2512 MHz | 61.7 | 123.5 |

All figures are dense TFLOP/s for FP16 or BF16 inputs with FP32 accumulation. The factory column follows NVIDIA’s whitepaper convention. The last column assumes twice the per-SM throughput at the same clock. Both exclude structured sparsity. Under this model, a full-rate 5080 would exceed a factory 5090, and a full-rate 5070 would exceed a factory 5080. The corresponding FP8 ceiling for the 5090 would be 838.0 TFLOP/s. Route 1 has not demonstrated these ceilings.

### Why test the 50 series?

- **Training and fine-tuning.** LoRA, QLoRA and full training can benefit if matrix multiplies with FP32 accumulation dominate step time. Even if those kernels become twice as fast, they must account for two-thirds of the original runtime to yield a 1.5× end-to-end speedup.
- **Long prompts and diffusion models.** Long-context prefill, large batches and the dense matrix multiplies in models such as Flux and Wan are good workloads to profile. Single-request, token-by-token decoding is often memory-bandwidth limited and may see little change.
- **Scientific computing and rendering.** For PINNs, NeRF or 3D Gaussian Splatting, profile the time spent in TF32-enabled matrix multiplies or convolutions. Rasterization, memory access and reductions have their own bottlenecks.
- **Speed and numerical error.** At `N = K = 8192` on GB205, full-length FP16 accumulation produced 16.6× the sample relative L2 error of FP32 accumulation. Route 2 already cuts that error in software on stock drivers; Route 1 aims to test whether the firmware clamp can be lifted to recover full throughput without giving up FP32 accumulation. [Measured comparison](reports/gb205-2f18/README.md)

Laptop power limits, sustained clocks, memory bandwidth and cuBLAS kernel selection all affect the outcome. The following measurements are the starting point.

On the RTX 5070 Ti Laptop (GB205), FP16, BF16, TF32 and FP8 inputs with FP32 accumulation each sustained about **half their reference throughput** after normalization for SM clock. Register captures from consumer GB202, GB203M, GB205 and GB206 cards also report `FEAT_OVR_PLM = 0xfffffb8f`, which grants write access to PL3 only. The throughput result comes from a benchmark on one card. Comparing performance across models requires equivalent measurements. [GB205 measurements](reports/gb205-2f18/README.md) · [Cross-card baselines](docs/test-matrix.md)

### Why RTX 20–40 series unlock methods fail on the 50 series

- **The 20–40 series SEC2 booter path is gone.** On Ampere and Ada, community unlockers (such as `nv-uefi-unlocker` and `gpu-unlock-ada`) relied on a leaked test key to decrypt the public debug `SEC2/Falcon booter_load` firmware and exploit a missing stack-buffer bound check in its signature loader during UEFI / pre-driver init, gaining privileged execution to clear `FEAT_OVR_PLM` and override `SS0/SS1` ([offline disassembly note](docs/references.md#5-additional-verification-notes-2026-11-25)). Blackwell removes the `SEC2 booter_load` stage altogether (`SEC2_PLM = 0x00000b0f`, open driver HAL falls back to a null stub), so that entire attack surface no longer exists.
- **A PL3 hardware privilege wall.** Across consumer Blackwell samples (GB202, GB203M, GB205 and GB206), `FEAT_OVR_PLM` consistently reads `0xfffffb8f`—**granting write access exclusively to PL3 (on-die secure firmware)**. Host CPU code, UEFI and OS drivers run at PL0, and GSP-RM runs at PL2, so direct BAR0 writes or PL2 RM writes to `SS0/SS1` are blocked in hardware.
- **No pre-lock race window.** Blackwell's immutable on-die ROM and FSP → FMC secure boot chain initialize and lock permissions before the host driver takes over the GPU, leaving no window to overwrite registers before the lock engages.

Reusing the 20–40 series UEFI/SEC2-booter approach is therefore a dead end on the 50 series. Any software-level bypass has to target an interface that reaches **PL3 execution** from the host—which is why **Route 1** focuses on the **host-to-FMC boot ABI**. [Boot chain and privileges](docs/architecture-map.md)

## 1. Two research routes

**Current status and division of work.** There is no known RTX 50-series unlock exploit yet, and I do not have a bare-metal native Linux test box with an independent recovery path. I have therefore done the groundwork up front: ruling out dead ends, auditing public driver source, mapping the attack surface, and shipping bare-metal preflight/recovery scripts along with the first-stage baseline logging patch. The goal is to lower the barrier to hardware testing so security researchers and developers with suitable RTX 50-series machines can jump straight into probing firmware boundaries (Route 1). If you need a practical speedup on stock drivers today before any hardware bypass exists, start with Route 2 on Windows or Linux.

### Route 1 · Probe the host-to-FMC (PL3) boot interface

Because `FEAT_OVR_PLM` restricts writes to PL3, neither PL2 GSP-RM nor normal host driver paths can modify the clamp directly. That makes the **host-to-FMC (First Mutable Code, PL3) boot interface** the lowest-level dynamic attack surface directly reachable from the host. Any software-level break requires first understanding how PL3 firmware parses and validates the host-supplied `GSP_FMC_BOOT_PARAMS`, cold-boot `GspFwWprMeta`, and resume `GspFwSRMeta` structures before GSP starts.

Route 1 proceeds in two stages, with this repository covering the engineering setup for Stage 1:

1. **Stage 1: Capture the hardware parameter baseline (included here).** The repository ships a logging-only reference patch pinned to official `open-gpu-kernel-modules 620.73.04` as a concrete example (other driver versions can adapt the target tag and patch as needed) to collect real-machine snapshots across cold boot, driver reload, and suspend/resume—verifying what the driver actually writes, what firmware fills in, and how firmware responds. The logging patch compiles at the driver object level; full module linking, loading, and physical boot still need hardware verification. Bare-metal preflight, log sanitization, decoding, and recovery tooling are ready to run. [RFC patch](patches/620.73.04-01-boot-abi-trace.patch) · [Verification record](patches/README.md) · [Hardware procedure](#4-route-1-on-native-linux)
2. **Stage 2: Controlled mutation and boundary probing (next research phase).** Once a hardware baseline is in hand, use the field map and [invariant checklist](docs/abi-invariants.md) (state flags, memory bounds and alignment, cross-struct consistency) to run controlled parameter mutations and differential testing (fuzzing / edge-case probing) against FMC's parsing and validation logic. See [ABI fields](docs/abi-fields.md) for the host-written vs. firmware-written split.
3. **Third-party `595.91.07` GB202 triage (integrated).** 87 hardware experiments on 2× RTX 5090 (`595.91.07`, [shuttie/bf16-unlock-blackwell-research](docs/references.md#6-third-party-gb202-hardware-experiments-shuttiebf16-unlock-blackwell-research-d85201e2465d), CC BY 4.0) have already mapped 17 FSP/FMC error codes, four failure/recovery tiers, live vs. inert cold-boot fields, and post-FLR autonomous devinit re-lock timing. Those closed paths and the prioritized remaining open leads (including `SR_RESUME` `radix3` PTE mutations and `570`/`595`/`620` cross-version ABI diffs) are consolidated in [recovery.md](docs/recovery.md#11-observed-fspfmc-error-codes-and-failure-tiers-third-party-gb202-5959107) and [abi-invariants.md](docs/abi-invariants.md#6-third-party-gb202-triage-and-remaining-route-1-open-leads).

> Given how hardened Blackwell's secure boot chain is, the odds of finding an exploitable flaw via Route 1 are low, and this repository makes no promise of an unlock. Doing the triage and tooling up front lets developers with suitable hardware test that boundary at minimal cost—**even if the answer turns out to be an airtight defense, hard hardware evidence is worth far more than endless speculation.**

### Route 2 · Software workarounds available today

**Core mechanism:** Consumer Blackwell's 0.5× clamp is enforced per instruction class at issue time—throttled MMAs on the same SMSP must be separated by at least **32 cycles**, while the **16-cycle** tensor datapath itself runs at full rate. That yields three software workarounds that work today on stock drivers:

1. **Switch to an unthrottled instruction:** Route work through full-rate opcodes outside the throttle list (unit-scale `QMMA.SF`, lossless `FP6 → E4M3` upcast, `OMMA.SF`).
2. **Interleave to hide cooldown:** Mix throttled and unthrottled MMAs on the same SMSP at a ratio of at most **`1:2`** to fill cooldown gaps at zero gate penalty (`C1+B1`).
3. **Short-chunk FP16 accumulation with FP32 flush:** Accumulate short tiles in full-rate `HMMA.F16` and flush periodically into FP32 to bound rounding error (`Pure B1` / chunked PyTorch matmul).

All measurements below were collected on the same **GB205 (RTX 5070 Ti Laptop, driver `621.01`)** — CUDA `13.5` at the 140 W board TDP for the custom `QMMA.SF` / `OMMA.SF` / TMA-WS rows, and archived PyTorch `2.11.0+cu128` (CUDA `12.8`, ~100 W sustained power cap) for the final pure-PyTorch row:

| Precision & Workload | Recommended Path | Measured Throughput / Speedup | Accuracy & Error |
| :--- | :--- | :--- | :--- |
| **FP8 / FP6 / Mixed FP8** | Unit-scale `QMMA.SF` / lossless `FP6 → E4M3` | **~2045–2048 FLOP/SM/clk** (**2.00×**, full rate) | **Bit-exact** (`0` bit diffs) |
| **FP4 / 2:4 Sparse FP8** | `OMMA.SF` (`mxf4`/`mxf4nvf4`) / `QMMA.SF.SP` | **4087.7** / **4026.4 FLOP/SM/clk** (full rate) | No extra format loss (bit-exact on sparse FP8) |
| **`W8A16` / `W4A16` / FP8-KV** | **Single-sided Dual `QMMA.SF` (`X = s(H+L/16)`, shared acc)** | **1.92×–1.95× per-GHz** (`96.1–98.9 TF/s` pre-split; `1.87×–1.89×` 2-kernel; `1.77×` online) | **True-FP64 `rel_L2` `1.67e-7–1.38e-6` (beats `cuBLAS BF16`)**; matches exact BF16 LLM quality |
| **Pure FP16 / BF16 → FP32** | SM 12.0 TMA-WS `Pure B1` / `C1+B1` | **1.66× / 1.56× per-GHz** (`K=4096`, 1.31× / 1.25× wall at 140 W) | **Indistinguishable** in BF16 deploy; `+1.35e-6` / `+6.46e-7` excess KL in FP16 |
| **Large-`K` Non-Zero-Mean GEMM** | `HMMA.F32 + RN16` (periodic RN flush) | **1.00×** (matches native rate) | **19×–84× more accurate** than `cuBLAS` |
| **Zero-Compile Drop-In** (`cu128`, ~100 W) | Pure-PyTorch chunked matmul (`chunk_k=2048`) | **48.90 TFLOP/s** (**+10%** at `N=K=8192`) | Sample relative L2 error `0.001644` |

#### 1. FP8 / FP6 / FP4: Lossless Full Rate (`QMMA.SF` / `OMMA.SF`)

- **Unit-scale FP8 (`2.00×`):** Legacy unscaled `QMMA` is clamped at `1024 FLOP/SM/clk` (`0.50×`). Switching to block-scaled `QMMA.SF.16832` with constant unit scale (`UE8M0 = 0x7F7F7F7F`) unlocks full **`~2045–2048 FLOP/SM/clk`** (`0 / 33,554,432` bit diffs) whenever at least one operand is an 8-bit float.
  - Supported formats: `e4m3`, `e5m2`, SASS-patched undocumented **`E3M4FN`** (type code `2`), mixed `FP8×FP6` / `FP8×FP4`, and 2:4 sparse `QMMA.SF.SP` (`4026.4 FLOP/SM/clk`, `1.97×`).
  - End-to-end GEMM: At `N = 8192` FP8→FP32 (`FAST_ACCUM=0`), throughput jumped from `111.80 TFLOP/s` (CUDA 12.8) to **`241.70 TFLOP/s`** (CUDA 13.5 `cuBLASLt`).
- **Lossless FP6 upcast (`2.00×`):** Upcasting at least one FP6 operand (`e3m2` / `e2m3`) to `E4M3` at load time and issuing `QMMA.SF` gives **`0` bit differences** across all five FP6/FP4 mixes while doubling throughput from `1023.5` to **`2048.1 FLOP/SM/clk`**.
- **Other full-rate paths:** Full-rate FP4 (`4087.7 FLOP/SM/clk`) requires `OMMA.SF.16864` (`mxf4` / `mxf4nvf4`); INT8 `IMMA` runs unclamped at `2031.8 OP/SM/clk`.

> Further reading: [Full GB205 MMA matrix & toolbox](software/README.md#4-full-rate-fp8fp32-via-qmmasf-and-cuda-132) · [Route 2 deep dive](docs/route2-mma-bypass.md)

#### 2. FP16 / BF16 → FP32: TMA-WS Chunked & Interleaved Kernels

- **Throttle-gate model:** Exhaustive SM 12.0 scanning of all 4,096 12-bit opcodes confirms **no `HMMA.SF` exists**. All throttled MMAs (`HMMA.F32`, unscaled `QMMA`, non-FP8 `QMMA.SF`) share a 16-cycle datapath gated by a hard **32-cycle per-SMSP cooldown gate (no burst credit)**; interleaving at **`1:2`** (`HMMA.F32 : HMMA.F16`) incurs zero gate penalty.
- **Real GEMM throughput** (`4096×14336×4096` exact Llama-3-8B `K=4096` with TMA zero-fill tail, `128×256×48` TMA + `mbarrier` + `setmaxnreg 40/232`, steady-state NVML):
  - **`Pure B1 (N=3)`:** **`81.13 TF/s`** (`840.8 FLOP/SM/clk`), **`1.66×`** faster per GHz than `cuBLAS BF16→FP32` (`1.31×` wall-clock under the 140 W laptop TDP cap; `1.69×` per-GHz / `1.33×` wall on 48-aligned `K=4080`).
  - **`C1+B1 (N=2)`:** **`77.08 TF/s`** (`790.5 FLOP/SM/clk`), **`1.56×`** faster per GHz (`1.25×` wall-clock; `1.58×` per-GHz / `1.26×` wall on `K=4080`).
- **End-to-end LLM quality** (`Llama-3.2-1B` full 288,768-token WikiText-2-raw & `Qwen2.5-1.5B`, every linear layer replaced, true FP64-accum reference):
  - **BF16 deployment:** Both `Pure B1` and `C1+B1` are **statistically indistinguishable** from `cuBLAS BF16→FP32`.
  - **FP16 deployment:** Absolute excess KL vs. native `HMMA.F32` is only **`+1.35e-6`** (`Pure B1`, keeping `lm_head`) and **`+6.46e-7`** (`C1+B1 + K-perm`, all layers), or `+1.03e-6` and `+4.56e-7` under the FP32-`lm_head` high-resolution lens; unflushed `Pure F16` reaches `+2.90e-4` (`23×–65×` worse).
- **Single-sided Dual `QMMA.SF` (`W8A16` / `W4A16` / `FP8-KV`, recommended quantized-weight path):** Hardware probes confirm `QMMA.SF.16832` has **zero FTZ** on E4M3 subnormals (`28/28`) and a full 23-bit mantissa, letting each 32-element BF16 activation block split losslessly as $X = s(H + L/16)$ (`3,584/3,584` bit-exact over `[2⁻⁶, 256)`, covering `99.94%` of real LLM activations) into the same FP32 accumulator (`scale_L = scale_H - 4`). On `4096×14336×4096`, `W8A16` / `W4A16` reaches **`96.06 / 98.86 TF/s`** pre-split (**`1.95× / 1.92×`** per-GHz, `1.53× / 1.58×` wall), **`92.74 / 96.86 TF/s`** 2-kernel (`1.89× / 1.87×` per-GHz), and **`85.76 / 88.67 TF/s`** single-kernel online (`1.77×`), with true-FP64 `rel_L2` of `1.38e-6` / `1.67e-7` (better than `cuBLAS BF16`) and **`8.9×–10.5×`** lower 32k LLM activation KL than Single-FP8 (matching exact BF16 quality). Separately, scanning `517,031,936` real 32-element BF16 blocks across both models gives a **`0 / 517,031,936`** exact lossless INT8-affine pass rate, closing online lossless BF16→INT8 dispatch for continuous LLMs. Source: [dual_qmma_tma_ws.cu](software/dual_qmma_tma_ws.cu); CPU verifier: [verify_dual_fp8.py](software/verify_dual_fp8.py).
- **Additional paths:** For large-`K` non-zero-mean GEMMs, `HMMA.F32 + RN16` matches native speed while cutting error **`19×–84×`** vs. `cuBLAS` (and likewise collapses biased `K=65,536` Dual `QMMA.SF` RZ drift from `4.34e-5` to `3.16e-8`); a zero-compile PyTorch chunked drop-in is also included (`48.90 TFLOP/s`, `+10%`, relative L2 error `0.001644` at `N=K=8192`, `chunk_k=2048`).

> Further reading: [Route 2 deep dive & selection table](docs/route2-mma-bypass.md) · [Operator usage and PyTorch sweep](software/README.md)

## 2. Prior triage & background work

- **Static VBIOS analysis.** The survey covers twelve ROMs across five dies, including the RTX PRO 6000 and RTX 5090 D. All 72 V6 descriptors have 80-byte headers. Each object’s size equals its header, row area and declared stored size combined, with an independent boundary check from the SHA-384 object directory. The plaintext structures examined have no distinguishing professional-card marker. The 28-byte tails and payloads group one-to-one in these samples. The survey provides no evidence that editing a header or swapping table rows removes the throughput limit. The tail’s function and runtime authentication order remain unknown. [V6 study](docs/vbios-v6-descriptor.md) · [Specimens and provenance](reports/rom-survey-20261123/README.md)
- **Debug FMC, GSP-RM internals & bundled LS falcons.** Direct checks across five upstream driver tags show `GspRmFmcGfwDebugSigned_GB202` and `ProdSigned_GB202` carry distinct encrypted ciphertexts in `595.71.05`–`610.57.04` and byte-identical encrypted `UCODE_IMAGE` (287,304 B, entropy `7.9992 bits/byte`) plus `UCODE_HASH` in `615.71.10`–`620.73.04` (differing in `UCODE_SIG` and `UCODE_PKEY`)—leaving no plaintext gadgets or signature-table downgrade surface. Third-party offline static/Unicorn analysis ([shuttie/bf16-unlock-blackwell-research#2](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/2), [#3](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/3)) also closes GSP-RM internal write paths (PL2-blocked), 1,363/1,516 functionized RM control handlers (no `0x823804` writes), `RMDisableFeatureDisablement` (synthetic inputs), LPWR slot-1 capture (disabled on GB206), and `PMU_RISCV_EB` (encrypted LS/PL2). [Verification notes](docs/references.md#5-additional-verification-notes-2026-11-25)
- **GSP-RM runtime RPC.** GSP-RM runs at PL2 after FMC initialization. Its messages can help interpret runtime clocks, power and suspend/resume behavior. The observed mask grants writes to the relevant registers only at PL3. [Boot stages and privileges](docs/architecture-map.md)
- **Read-only register baselines.** The archive covers several consumer models and RTX PRO. To assess a performance change, compare throughput at matched workloads on the same GPU, record SM clocks and accumulation mode, and use register readbacks to interpret the result. [Measurement matrix](docs/test-matrix.md)

Physical fault injection (EMFI and LFI) and side-channel research require specialist equipment and are outside the scope of this toolkit.

## 3. Quick start

### 3.1 Dependencies

| Task | Requirements |
|---|---|
| Offline analysis and unit tests | Python 3.9 or later. No GPU required. Tests with optional software or platform dependencies skip automatically. |
| Hardware baseline | Installed driver and its `nvidia-smi` utility. |
| Route 2 operators and throughput benchmarks | CUDA-enabled PyTorch ≥ 2.7. Telemetry uses `nvidia-ml-py` when available and falls back to `nvidia-smi`. |
| Route 1 capture and build | Native Linux, Git, GCC, make, matching kernel headers, Bash, GNU coreutils, grep, sed, awk and `dmesg`. The collector reads PCI data from sysfs without `lspci`. |

The examples use Bash. On Windows PowerShell, create the environment with `py -m venv .venv`, activate it with `.\.venv\Scripts\Activate.ps1`, then use `python` for the Python commands below.

```bash
# CUDA-enabled PyTorch is required only for operators and throughput benchmarks.
python3 -m venv .venv && . .venv/bin/activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt

# Route 1 build tools. Choose the command for your distribution.
sudo apt install git build-essential linux-headers-$(uname -r)  # Debian / Ubuntu
sudo dnf install git gcc make kernel-devel-$(uname -r)          # Fedora
sudo pacman -S --needed git base-devel linux-headers            # Arch: match the running kernel
```

### 3.2 Measurements and offline checks

```bash
# Collect the hardware and throughput baseline, then sweep K chunk sizes.
python3 scripts/verify-baseline.py --bench --json baseline.json
python3 software/chunked_fp16_matmul.py --n 8192 --json chunked-sweep.json

# Compare ABI layouts, check V6 sizes and compare local ROMs offline.
python3 scripts/abi-layout.py --struct GSP_FMC_BOOT_PARAMS --diff 610.57.04 620.73.04
python3 scripts/v6-closure.py reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json
python3 scripts/rom-survey.py /path/to/roms

# Run the test suite.
python3 -B -m unittest discover -s tests -v
```

## 4. Route 1 on native Linux

1. Route 1 requires native Linux on a physical host with matching kernel headers and toolchain. WSL2 and virtual machines, including guests with GPU passthrough, are outside this hardware boot procedure.
2. Open a text console driven by an iGPU or a second, non-NVIDIA GPU. Two NVIDIA cards sharing the driver both lose their output when it unloads. Test SSH from another machine and configure netconsole or a serial console to retain logs if the host hangs.
3. Check Secure Boot and kernel lockdown. If the kernel enforces module signatures, sign the test module with a key it trusts, such as an enrolled MOK.
4. Run the following blocks from the repository root. Read the preflight summary and warnings, confirm `FAIL=0` and `VIRT=bare_metal` (if `VIRT=unknown`, manually confirm a physical bare-metal Linux host) before building.

> The steps below execute Route 1, Stage 1 (read-only baseline capture). **The pinned `620.73.04` driver version below is provided as a concrete reference example** matching the bundled patch; Route 1 itself is not locked to one driver release. If your machine runs another version, swap in the upstream tag that matches your installed userspace driver and `/lib/firmware/nvidia/<version>/`, and adapt the trace patch per [patches/README.md](patches/README.md) (check layout changes across `610 → 615` with `scripts/abi-layout.py --diff`). The patch logs host-prepared arguments and leaves SPI flash untouched. Load the test module directly from its build directory. A reboot with the normal boot configuration loads the installed driver again. If initialization hangs or FMC reports an error, save the logs and power-cycle the machine before another attempt. [Recovery guide](docs/recovery.md)

**Steps 0–2. Check versions, fetch the source and build.**

```bash
# Require FAIL=0, VIRT=bare_metal (or manually verified physical host), and matching driver/userspace/GSP versions.
bash scripts/check-env.sh
modinfo -F version nvidia                 # Expect 620.73.04
ls /lib/firmware/nvidia/620.73.04/        # Expect gsp_*.bin

# Clone outside this repository and verify the pinned commit.
git clone --depth 1 --branch 620.73.04 https://github.com/NVIDIA/open-gpu-kernel-modules.git "$HOME/ogkm-620.73.04"
git -C "$HOME/ogkm-620.73.04" rev-parse HEAD   # Expect a4b95f6a2db0bba10ccaccef79190a3c18dcf14a

# Stop on the first failed apply or build.
git -C "$HOME/ogkm-620.73.04" apply --check "$PWD/patches/620.73.04-01-boot-abi-trace.patch" &&
git -C "$HOME/ogkm-620.73.04" apply "$PWD/patches/620.73.04-01-boot-abi-trace.patch" &&
make -C "$HOME/ogkm-620.73.04" modules -j"$(nproc)"
```

**Step 3. Unload the installed driver from SSH or the independent console.** Stop if a module is in use.

```bash
sudo systemctl stop nvidia-persistenced nvidia-powerd 2>/dev/null
sudo modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia
lsmod | grep '^nvidia'                    # Continue only if this prints nothing.
```

**Step 4. Load the test module and initialize the GPU once.**

```bash
KO="$HOME/ogkm-620.73.04/kernel-open/nvidia.ko"
for dep in $(modinfo -F depends "$KO" | tr ',' ' '); do sudo modprobe "$dep"; done
sudo insmod "$KO" && sudo nvidia-smi
```

**Step 5. Collect and decode the trace.** Run as a normal user so only `dmesg` reading is elevated; omit `--out` (or pass a fresh path per run) so cold-boot and reload samples stay separate.

```bash
bash scripts/collect-report.sh --sudo-dmesg --tar
python3 scripts/abi-layout.py --from-log ./blackwell-report-<UTC-timestamp>/dmesg-gsp-fmc.txt
```

**Step 6. Reload the installed driver or reboot.**

```bash
sudo rmmod nvidia && sudo modprobe nvidia_drm
```

- If unloading reports `in use`, inspect `sudo fuser -v /dev/nvidia*` and `lsmod | grep nvidia` for clients and dependent modules such as `nvidia_peermem`. If the desktop holds the device, confirm SSH access before switching to `sudo systemctl isolate multi-user.target`, which stops the desktop.
- For a cold-boot sample, use the temporary boot parameter `modprobe.blacklist=nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm,nouveau,nova_core`. Power-cycle, confirm with `lsmod` that none of those GPU drivers loaded, then continue at step 4.
- `Key was rejected` indicates a signing or trust problem. For `API mismatch`, check that the installed userspace and test driver match the patch tag.
- Decoder exit codes: `0` matches the static baseline, `3` indicates a difference or an incomplete trace, and `2` means the decoder found no `BWLAB` lines or could not read the input.

## 5. Share a measurement (optional)

You can use the tools locally. If you share a result, a normal boot, build error, load failure, `DIFF` or hang can all help. Include the GPU, driver, VBIOS, boot state, exact commands and the step that produced the result.

1. Review the four sanitized files from `collect-report.sh`: `summary.txt`, `nvidia-smi.txt`, `pci-topology.txt` and `dmesg-gsp-fmc.txt`. Remove any remaining personal or machine identifiers.
2. Attach the files or the archive produced by `--tar` to a GitHub issue. For a pull request, place the report and sanitized files in `reports/<die>-<device-id>/`. Use archives only as issue attachments. [Report template](reports/template.md) · [Contribution guide](CONTRIBUTING.md)

The repository accepts patches, scripts, documentation and sanitized metadata. Keep complete upstream source files, NVIDIA firmware, keys and raw VBIOS/PROM dumps local. Label hardware measurements, public-source findings and hypotheses separately. Project license: [GPL-2.0-only](LICENSE).

## 6. Repository layout

```text
blackwell-unlock-research/
├── README.md                        # Project overview and prerequisites
├── CONTRIBUTING.md                  # Submission and evidence requirements
├── requirements.txt                 # Optional benchmark dependencies
├── .github/workflows/ci.yml         # Tests, ShellCheck and consistency checks
├── docs/
│   ├── .nojekyll                    # Disable Jekyll processing for GitHub Pages
│   ├── index.html                   # Interactive GPU throughput chart (GitHub Pages)
│   ├── throughput-ceiling.svg       # README clickable throughput chart preview (EN)
│   ├── throughput-ceiling-zh.svg    # README clickable throughput chart preview (ZH)
│   ├── route2-kl-floor.svg          # Route 2 additive KL floor decomposition chart (EN)
│   ├── route2-kl-floor-zh.svg       # Route 2 additive KL floor decomposition chart (ZH)
│   ├── route2-depth-saturation.svg  # Route 2 per-layer discrete rounding saturation chart (EN)
│   ├── route2-depth-saturation-zh.svg # Route 2 per-layer discrete rounding saturation chart (ZH)
│   ├── route2-mma-bypass.md         # SM 12.0 throttle-gate model, TMA-WS GEMM & LLM quality report
│   ├── architecture-map.md          # Boot stages, privileges and research routes
│   ├── abi-fields.md                # Host fields, HAL bindings and version differences
│   ├── abi-invariants.md            # Host-to-FMC baseline checks
│   ├── recovery.md                  # Boot failure logs and recovery evidence
│   ├── references.md                # External sources and verification notes
│   ├── test-matrix.md               # Measurements by GPU and driver
│   └── vbios-v6-descriptor.md       # V6 descriptors and Class A manifests
├── patches/
│   ├── README.md                    # Pinned versions and patch verification
│   └── 620.73.04-01-boot-abi-trace.patch  # Logging-only RFC; object built, hardware test pending
├── scripts/
│   ├── check-env.sh                 # Native Linux preflight and recovery console checks
│   ├── collect-report.sh            # Sanitized boot, PCI and driver report
│   ├── verify-baseline.py           # Hardware baseline and optional clamp comparison
│   ├── abi-layout.py                # Offline structure layouts, diffs and trace decoder
│   ├── parse-ssdiag.py              # Parse register captures and generate matrix rows
│   ├── v6-closure.py                # Check descriptor and manifest arithmetic
│   ├── rom-directory.py             # Read local ROM object directories
│   └── rom-survey.py                # Compare metadata across local ROMs
├── software/                        # Route 2 userspace benchmarks and operators
│   ├── README.md                    # Throughput and accuracy trade-offs
│   ├── clamp_eval.py                # Steady-state tensor benchmark
│   ├── chunked_fp16_matmul.py       # K-chunked FP16 GEMM with FP32 reduction
│   ├── dual_qmma_tma_ws.cu          # SM 12.0 TMA-WS Dual QMMA.SF (W8A16/W4A16 pre-split/2-kernel/online) kernels & benchmark
│   ├── verify_dual_fp8.py           # CPU-only verifier for Dual QMMA.SF throughput, 517M-block scan & 32k LLM KL CIs
│   └── verify_wt2_bootstrap.py      # CPU-only verifier for 288k WikiText-2 bootstrap CIs
├── reports/
│   ├── template.md                  # Hardware report template
│   ├── rom-survey-20261123/         # Metadata from twelve ROMs across five dies
│   └── gb205-2f18/                  # RTX 5070 Ti Laptop baseline, driver 621.01
└── tests/                           # Unit tests and documentation checks
```

## 7. Disclaimer

1. **Research purpose and no warranty ("AS IS").** All patches, scripts, benchmarks, analysis notes and documentation in this repository are provided strictly for technical research, security analysis and educational purposes on an **"AS IS"** and **"AS AVAILABLE"** basis. To the maximum extent permitted by applicable law and the [GPL-2.0-only](LICENSE) license, this project disclaims all warranties, express or implied, including without limitation implied warranties of merchantability, fitness for a particular purpose, accuracy, reliability, compatibility and non-infringement. Nothing in this repository guarantees that any route or tool will unlock throughput limits, improve a given workload, or uncover an exploitable flaw.
2. **Kernel, firmware and hardware risks (especially Route 1).** Building and loading custom kernel modules, unloading/reloading GPU drivers, altering kernel boot parameters, reading low-level hardware registers, and tracing or probing the host-to-FMC (PL3) boot interface carry inherent low-level risks. These operations can cause loss of display output, GPU initialization failures, kernel panics, hard system hangs, filesystem corruption, data loss, and—in extreme or unforeseen circumstances—persistent lockouts or **permanent hardware damage**. In addition, the read-only logging patch in this repository has not yet been verified end-to-end on physical hardware; any Stage 2 parameter mutation or fuzzing conducted independently based on this documentation carries substantially higher risk and is undertaken solely at the operator's discretion.
3. **Numerical and production risks (Route 2).** The chunked FP16-accumulation / FP32-reduction operator in Route 2 is a throughput-versus-accuracy trade-off whose rounding error, overflow behavior and convergence impact depend on matrix dimensions, input distributions and model architecture. Validate numerical accuracy thoroughly before using it in any critical or production workload.
4. **Warranty, authorization and legal compliance.** Loading modified kernel modules, changing boot configurations, or probing hardware and firmware interfaces may void official warranties or result in refusal of RMA or vendor support from GPU, board or system manufacturers. Run hardware experiments only on physical machines that **you own or have explicit written authorization to test**, and ensure compliance with all applicable local laws, licenses and policies. Do not redistribute proprietary firmware, signing keys, or raw VBIOS/PROM dumps.
5. **Prerequisites and limitation of liability.** Before running any hardware procedure, evaluate the risks for your specific machine, back up all important data, and verify an independent recovery path (separate display output, SSH, serial console or netconsole, and physical power-cycle access). You assume full responsibility for all direct, indirect, incidental, special or consequential outcomes resulting from the use, modification, reference to, or inability to use any material in this repository (including derivative experiments)—including but not limited to **hardware damage, bricked devices, voided warranties, data loss, system corruption, downtime or financial loss**. **Under no circumstances shall the project authors or contributors be held liable for any claim, damage or other liability.**
6. **No affiliation and trademarks.** This is an independent community research project and is not affiliated with, endorsed by, or sponsored by NVIDIA Corporation or any GPU, board or system vendor. NVIDIA, GeForce, RTX, Blackwell, CUDA, PyTorch, Linux and other product names mentioned herein are trademarks or registered trademarks of their respective owners and are used solely for technical identification.
