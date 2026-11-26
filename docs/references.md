[English](references.md) | [中文](references.cn.md)

# Sources and verification notes

The comparisons below were recorded on 23 November 2026. Each finding applies to the specified revision and hardware family.

| Source | License | Used for | Scope |
|---|---|---|---|
| [NVIDIA open kernel modules 620.73.04](https://github.com/NVIDIA/open-gpu-kernel-modules/tree/a4b95f6a2db0bba10ccaccef79190a3c18dcf14a) | Per-file licenses | Host-to-FMC structures, HAL bindings, version diffs and trace patch | The RM engine types in `cl2080_notification.h` (0x00–0x53) cannot explain all V6 entry IDs |
| [bird/vgpu-unlock-blackwell](https://github.com/bird/vgpu-unlock-blackwell/tree/22d2b112e20981565d543b74745c8c02bf4e8960) | MIT | Third-party RTX 5090 vGPU observations | The author reports a GSP-stage failure in the vGPU experiment. Its scope does not establish the cause of the tensor throughput limit. |
| [Linux `nova-core` and FSP](https://docs.kernel.org/gpu/nova/core/fsp.html) | GPL-2.0 | Route 1 fields, encoding and timing | Independent implementation, details in §1 |
| [gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery](https://github.com/gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery) | None listed | Recovery behavior | [Source review](recovery.md). No integration. |
| [Panchovix/nvidia-vbios-vix-editor](https://github.com/Panchovix/nvidia-vbios-vix-editor) | GPL-3.0 | VBIOS manifest entries | Format comparison in §2 |
| [NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732) | BSD-3-Clause | SM120 FP8 `QMMA` vs unit-scale `QMMA.SF` (`kind::mxf8f6f4`) | Bit-identity and GeForce vs RTX PRO 6000 confirmation; locally reproduced on GB205 in §5 |
| [flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963) | Apache-2.0 | RTX 5090 FP8 attention `QMMA` vs unit-scale `QMMA.SF` | Cross-card corroboration on GB202 |
| [steviecurrie/gpu-unlock-gb206](https://github.com/steviecurrie/gpu-unlock-gb206) | GPL-2.0 | GB206 baselines, `FEAT_READOUT_1` decode note, Debug vs Prod FMC note | Debug vs Prod FMC independently re-verified on `615.71.10` and `620.73.04` in §5 |
| [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049) (issues [#2](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/2) · [#3](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/3)) | CC BY 4.0 (text); MIT / GPL-2.0-only (tools) | 87 GB202 hardware experiments on `595.91.07` (plus offline analysis of `gsp_ga10x.bin` and Ada `booter_load`) | Summaries cited with attribution in §5, §6, [recovery.md](recovery.md#11-observed-fspfmc-error-codes-and-failure-tiers-third-party-gb202-5959107) and [abi-invariants.md](abi-invariants.md#6-third-party-gb202-triage-and-remaining-route-1-open-leads); no code or firmware copied |
| [SageAttention3 (`arXiv:2505.11594`)](https://arxiv.org/abs/2505.11594) · [SVDQuant / Nunchaku (`arXiv:2411.05007`)](https://arxiv.org/abs/2411.05007) · [RIKEN-RCCS/GEMMul8](https://github.com/RIKEN-RCCS/GEMMul8) | Apache-2.0 / MIT | Third-party Blackwell (RTX 5090) microscaling FP4 attention, low-rank outlier branch, and INT8 Ozaki-II emulation context | Cited for Route 2 positioning in [route2-mma-bypass.md](route2-mma-bypass.md) §5; kept strictly separate from local GB205 measurements |

---

## 1. Linux `nova-core` (torvalds/linux `b6a9fa34ce41`, 7.4-rc4)

This second implementation provides an independent check on the [ABI layout](abi-fields.md). Its bindings target **r570.144**. The cold-boot path is implemented. The `resume` argument is always false at this revision.

| Item | `nova-core` | Open driver / local reference | Finding |
|---|---|---|---|
| Host-written `GspFwWprMeta` fields on the FSP path | `from_sizes` writes addresses, sizes and PMU/VF counts. Its comments assign layout offsets to ACR ucode. | Host leaves `gspFwWprStart/End`, `fbSize`, `bootCount`, `verified` and layout offsets untouched. | Agrees |
| `GSP_FMC_BOOT_PARAMS` (570) | `r570_144/bindings.rs`, 80 B, no `magic` | `570.124.04`, 80 B, no `magic` | Same fields, order and padding |
| `GspFwWprMeta` (570) | Includes `padding[2]` | Same fields | Agrees |
| `NVDM_PAYLOAD_COT` | Packed, from `version` to `gsp_boot_args_sysmem_offset` | 860 B | Same order |
| COT version | GH100 uses 1, GB10x/GB20x use 2 | Same HAL values in 620.73.04 | Agrees |
| WPR2 bounds | Bits 31:4 shifted left 12. LO inclusive, HI exclusive, zero HI means unset. | Same encoding | Clarifies the upper bound |
| WPR2 address | `0x1fa824/28` on Turing–Ada only | GB20x reads `0x88a824/28` | Aliasing on GB20x remains unverified |
| Mailbox status | Wait while the pair equals the boot-argument address. Other nonzero status indicates an FMC error. | Same rule | Agrees |

### Version-specific details

- GH100 uses COT v1 with a 384 B public key and a 384 B signature. GB100/GB10x/GB20x use COT v2 with a **97 B public key** and **96 B signature** in the 620.73.04 HAL. These sizes match an uncompressed P-384 point (`1 + 48 × 2`) and an ECDSA signature (`48 × 2`). `nova-core` reserves up to 384 B. The RSA-3K description applies to GH100. [Kernel FSP documentation](https://docs.kernel.org/gpu/nova/core/fsp.html)
- In August 2026 mainline switched the FMC firmware to a TLV container (`VERS`, `BLOB`, `HASH`, `PKEY`, `SIGN`). The documentation reviewed on 23 November still described the ELF layout.
- PRC can change device configuration. The tools in this project only observe state.

---

## 2. `nvidia-vbios-vix-editor` (`638ea500f909`)

The independent offline parser lists support for GB202, GB203, GB205, GB206 and GB207. The project's `scripts/rom-directory.py` and `v6-closure.py` implement the format independently. The editor's GPL-3.0 code stays outside this GPL-2.0-only repository.

The editor calls this format object-directory v3. Each directory has a 16 B header and 60 B entries containing an ID, offset, size and SHA-384 digest. Two of its table definitions can be compared directly with GB205 readbacks.

| Entry ID | Editor's expected header | GB205 header | Extent |
|---|---|---|---|
| `0x14` | VFE, `30 / 14 / 29 / 255 / 25` | `30 / 14 / 29 / 255 / 25` | `14 + 255 × 29 = 7409`, exactly `field_C` (`0x1cf1`) |
| `0x12` | Power device table, `30 / 24 / 25 / 11` | `30 / 24 / 25 / 11` | `24 + 11 × 25 = 299` of 1320 B. The remainder is undecoded. |

The values are version (hex), header size, record size, count and, where recorded, the next table's record size (decimal).

On the local GB205 ROM, all seven directory digests and 155 object hashes verified. All ten distinct type 1 IDs have the same starting offset as the corresponding object. Nine also match its size. Entry `0x14` uses the first 7409 B. All six V6 descriptors and their associated data fill their objects. [Measurements and file identity](vbios-v6-descriptor.md)

Only format facts and matching header values were used. Table names are third-party interpretations. The editor and companion loader are outside this project's read-only workflow.

---

## 3. ROM provenance and collection

Keep ROMs local. Publish only the metadata produced by `scripts/rom-survey.py --export`. Record the die, board model, VBIOS version and source category for each sample.

| Source | Provenance | Collection notes |
|---|---|---|
| Your own card | Direct measurement | Save a read-only copy with GPU-Z or the save function of `nvflash`. |
| Board or laptop vendor | Vendor-supplied | Extract the update package offline. Leave the flasher unexecuted. |
| [TechPowerUp VGA BIOS Collection](https://www.techpowerup.com/vgabios/) | Depends on entry review | Use the browser. On the review date, its robots.txt excluded `/vgabios/*/*.rom` for automated access and `/vgabios/` for several AI crawlers, and required prior written permission for scraping. Automated requests returned 403. |
| Forum dump | Often unknown | Check object hashes and directory digests. `MISMATCH` or `absent` yields `DIFF`. Matching digests establish internal consistency. Authenticity requires separate evidence. |

The owner supplied eleven of the twelve files in the [first cross-card survey](../reports/rom-survey-20261123/README.md).

## 4. Interactive chart data and conventions

- **Dataset and attribution.** The [interactive chart](index.html#en) adapts desktop model names, dies and SM counts from the 269-row array in **physicsdolphin/nv-uefi-unlocker**, commit `e912777c9a89`, licensed under GPL-2.0. It includes 21 selected models. Both RTX 5060 Ti memory variants share one compute profile. Five selected GeForce laptops and the workstation PRO 4500 were missing from the source and are supplemented below. The same revision provides community register observations and the older V2/V3 layout reference. [Original dataset](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/docs/tools/tflops-ceiling.html) · [License](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/LICENSE) · [GB20x status](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/docs/blackwell-gb20x-status.md) · [V2/V3 field source](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/loader/src/tx_fwsec.rs)
- **GeForce and RTX PRO.** Desktop GeForce rows use reference clocks. Laptop rows use the upper end of each published boost range. RTX PRO rows use desktop workstation configurations. The 5090 D has a separate advertised AI rating; its row models only SM-level throughput. [Blackwell whitepaper](https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf) · [Desktop specifications](https://www.nvidia.cn/geforce/graphics-cards/compare/) · [Laptop specifications](https://www.nvidia.com/en-us/geforce/laptops/50-series/) · [PRO 4500 workstation specifications](https://marketplace.nvidia.com/en-us/enterprise/laptops-workstations/pny-rtx-pro-4500-blackwell-workstation-edition/) · [5090 D announcement](https://blogs.nvidia.cn/blog/nvidia-blackwell-geforce-rtx-50-series-opens-new-world-of-ai-computer-graphics/)
- **System references.** B200 divides HGX B200 system figures by eight GPUs and sparse tensor peaks by a further two. For example, dense BF16 is 36,000 / 8 / 2 = 2,250 TFLOP/s. GB10 refers to the 6,144-core desktop DGX Spark. NVIDIA highlights FP4 AI performance; the other precision figures are upstream estimates. [HGX B200 specifications and footnotes](https://www.nvidia.com/en-au/data-center/hgx/) · [DGX Spark specifications](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
- **Route 2 marker.** The 0.8× guide approximates normalized full-length FP16 accumulation on GB205. The chunked result is a separate measurement: at chunk size 4096 it was 661.7 / 1024 ≈ 0.65×. [GB205 measurements](../reports/gb205-2f18/README.md)

---

## 5. Additional verification notes (2026-11-25)

- **Debug vs Prod FMC bindata across five `open-gpu-kernel-modules` tags.** Directly comparing `g_bindata_kgspGetBinArchiveGspRmFmcGfwDebugSigned_GB202.c` against `ProdSigned_GB202.c` shows a packaging shift between `610` and `615`:

  | Driver tag | `UCODE_IMAGE` (287,304 B) | `UCODE_HASH` (48 B) | `UCODE_SIG` (96 B) | `UCODE_PKEY` (97 B) |
  |---|---|---|---|---|
  | `595.71.05` | Differs (`42e8e595` vs `960ccfe7`) | Differs | Differs | Differs |
  | `595.91.07` | Differs (`42e8e595` vs `cb93e210`) | Differs | Differs | Differs |
  | `610.57.04` | Differs (`e5dc9028` vs `17a15ce8`) | Differs | Differs | Differs |
  | `615.71.10` | Byte-identical (`5b74fefd`, entropy `7.9992`) | Byte-identical | Differs | Differs |
  | `620.73.04` | Byte-identical (`55042efa`, entropy `7.9992`) | Byte-identical | Differs | Differs |

  In both regimes the image is fully encrypted with no cleartext preamble and ships a single signature without a slot table, leaving neither plaintext gadgets nor a signature-downgrade surface.
- **FP8/FP6/FP4/INT MMA matrix and CUDA 13.5 `cuBLASLt` (local GB205 hardware verification).** Tested on the archived RTX 5070 Ti Laptop (`10de:2f18`, 46 SMs, driver `621.01`, `nvcc 13.5`, `-gencode arch=compute_120a,code=sm_120a`, random non-zero finite inputs, SASS confirmed via `cuobjdump --dump-sass`; full table in [software/README.md §4](../software/README.md#4-full-rate-fp8fp32-via-qmmasf-and-cuda-132)):
  - `QMMA.SF.16832` (constant unit UE8M0 `0x7F7F7F7F`) doubles from `1022.4` to **`~2045 FLOP/SM/clk`** (`2.00×`, `0 / 33,554,432` bit diffs vs plain `kind::f8f6f4`, scale-`2.0` control `~33.55M` diffs) whenever at least one operand is FP8 (`e4m3×e4m3`, `e5m2×e5m2`, `e4m3×e5m2`, `e4m3×e3m2`, `e3m2×e4m3`, `e4m3×e2m1`, `e2m1×e4m3`, `e5m2×e2m1`), and exact FP32 accumulation holds on `256² + 0.125² = 65536.015625` (`0x47800002`). Sparse 2:4 `QMMA.SF.SP.16864` similarly jumps from `2045.9` to **`4026.4 FLOP/SM/clk`** (`1.97×`).
  - Without an FP8 operand, `QMMA.SF.16832` stays half-rate (`e3m2×e3m2` / `e2m3×e2m3`: `1023.1`, `e3m2×e2m1`: `1023.2`, `e2m1×e2m1`: `1022.9 FLOP/SM/clk`). Full-rate FP4 requires `OMMA.SF.16864` (`mxf4`/`mxf4nvf4`: **`4087.7 FLOP/SM/clk`**); INT8 `IMMA.16832.S8.S8` runs unclamped at **`2031.8 OP/SM/clk`**; INT4 `s4` is emulated via `IMMA.16832.S8.S8` (`213.7 OP/SM/clk`).
  - **Undocumented `E3M4FN` (SASS type code `2`, hardware-verified on GB205).** Diffing all five documented `QMMA.SF.16832` types in `nvdisasm -hex` locates non-contiguous 3-bit type fields in `hi64`: `typeA = (bit83 << 2) | (bit82 << 1) | bit78`, `typeB = (bit85 << 2) | (bit84 << 1) | bit79` (`bit 80` is the sparse flag). Patching `code = 2` (`0b010`, silently skipped by `nvdisasm`; codes `6`/`7` decode as `INVALID6`/`INVALID7` and raise `CUDA_ERROR_ILLEGAL_INSTRUCTION`) executes natively on GB205 as **E3M4FN** (`1s + 3e bias=3 + 4m`, subnormals `0x01 = 2⁻⁶`..`0x0F = 15/64`, `0x30 = 1.0`, `0x31 = 1.0625`, max finite `0x7E = 30.0`, NaN only at `0x7F`/`0xFF`), sustaining full **`2045.2 FLOP/SM/clk`** for both `E3M4×E3M4` (`227.05 TFLOP/s`) and `E4M3×E3M4` (`225.92 TFLOP/s`).
  - `N=8192` FP8 E4M3 → FP32 GEMM via CUDA 13.5 `cuBLASLt 131000` (BF16 output): `241.70 TFLOP/s` (`FAST_ACCUM=0`, `4.55 ms/iter`) and `239.93 TFLOP/s` (`FAST_ACCUM=1`, `4.58 ms/iter`), up `2.16×` from the archived PyTorch `2.11.0+cu128` (CUDA 12.8) baseline (`111.80 TFLOP/s`, `0.50×`) on the same GPU and driver. This corroborates community reports on RTX 5080 ([NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732)), RTX 5090 ([flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963)) and RTX 5070 Laptop ([steviecurrie/gpu-unlock-gb206#1](https://github.com/steviecurrie/gpu-unlock-gb206/issues/1)). Legacy `HMMA` (FP16/BF16/TF32→FP32) remains clamped at `0.5×` (`511.4–511.8 FLOP/SM/clk`).
- **GSP-RM internal paths, LPWR, `PMU_RISCV_EB`, and Ada `booter_load` (third-party offline analysis).** Treat all items below from [shuttie/bf16-unlock-blackwell-research#2](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/2) and [#3](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/3) as offline static/Unicorn analysis rather than live hardware tests:
  - **GSP-RM FEAT writer and control census.** Static tracing on `595.91.07` (GB202, #3) and `596.49` / `595.71.05` (GB206, #2) locates the call chain `engine-start → Graphics+0xa58 → Graphics+0x938` writing `0x381c`/`0x3820`, showing GSP-RM attempts the write at engine init but runs at PL2 and is blocked by `FEAT_OVR_PLM` (PL3-only). Across 1,516 non-null RM control handlers in `595.91.07`, 1,363 were functionized (153 could not be disassembled); none of the 1,363 invokes the BAR0 port helper, public getters (`0x20801230` / `0x2080123c`) only read memory caches, and image-wide scans find no RM writer to `0x823804`.
  - **Registry key, LPWR slot-1, and `PMU_RISCV_EB`.** In bounded Unicorn runs on `596.49` GB206 (#2), `RMDisableFeatureDisablement` (`GPU+0x545a`) altered only zero-value fallback handling while leaving tested nonzero values `1/2/3` unchanged under synthetic inputs; `OBJPGCTRL` LPWR slot-1 capture was enabled only for Turing ordinals `37..41` and disabled on GB206 (`70`); `PMU_RISCV_EB` (`falconId=26`) carried a 4,944 B `LSF_UCODE_DESC_WRAPPER` (`bLsEncrypted=1`, entropy `7.9999`), placing it in Light Secure (LS/PL2) with no PL3 write authority.
  - **Ada `booter_load` overflow mechanism.** Static Falcon disassembly of the public `595.91.07` AD102/GA102 debug `BooterLoadUcode` (#3, comment `6863650651`) traces the prior-generation unlock to a missing `0x40`-byte stack-buffer bound check in the signature loader (`0x6456`/`0x6556`), where `host_length ∈ (0x40, 0x60]` overwrites post-buffer stack state at `sp+0x88` before the stack canary at `sp+0xa8` is checked. Blackwell removes the SEC2 `booter_load` stage entirely.

---

## 6. Third-party GB202 hardware experiments (`shuttie/bf16-unlock-blackwell-research`, `d85201e2465d`)

Reviewed on 2026-11-26 from [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049) (commit `d85201e2465d`, 2026-11-05; text licensed under **CC BY 4.0**; no tool code or firmware is copied into this repository). Test bench: **2× RTX 5090 (GB202 `10de:2b85`)** on Linux driver/GSP-RM **`595.91.07`**, plus one **RTX PRO 6000 (`10de:2bb1`)** reference on `615.71.09` (`FEAT_READOUT_1 = 0`, 518 TFLOP/s BF16→FP32).

All hardware results below remain third-party measurements on `595.91.07` and are kept distinct from our local GB205 captures and `620.73.04` static baseline:

- **FSP/FMC error taxonomy and host-driver pitfalls.** 17 measured FSP COT and FMC parser/auth error codes grouped into clean pre-auth refusals, post-WPR2 auth failures (`0x377`, `0x1db`, `0x33`), parameter wedges, and silent `SR_RESUME` hangs, together with two non-firmware host-side pitfalls on `595.91.07` (a driver error-recovery lifetime bug in `kfspCleanupBootState_IMPL` on COT retries, and an early-boot BAR1 hook-ordering constraint before `gpuStateLoad`). Integrated in [recovery.md §1.1](recovery.md#11-observed-fspfmc-error-codes-and-failure-tiers-third-party-gb202-5959107).
- **Cold-boot field liveness, `FEAT`/FLR timing, and `SR_RESUME` layout.** Live vs. inert `WprMeta`/COT fields (`COT.frtsVidmemOffset` 1:1 WPR translation over ≥ 1 GiB; `WprMeta.frtsSize`, `verified`, `sizeOfSignature` and `bootloader{Code,Data}Offset` inert; `sysmemAddrOfRadix3Elf` pinned; `sysmemAddrOfSignature` live 4-byte aligned), pre-FMC `FEAT` finality, cold-boot-only `SS0`/`SS1` variance (surviving warm reboot and FLR), GPU-side autonomous devinit re-lock of `FEAT_OVR_PLM` (`0x80842bcf → 0xfffffb8f`) within ~1.16–1.35 s post-FLR, and `SRMeta` + 3-level `radix3` / `205.875 MiB` ciphertext observations. Integrated in [abi-invariants.md](abi-invariants.md) (§1–§3 and [§6](abi-invariants.md#6-third-party-gb202-triage-and-remaining-route-1-open-leads)).
- **Complementarity with this repository.** The upstream bench stays on `595.91.07` because `615+` removes secure-boot debug strings from GSP-RM and restructures engine ucode into `NVUCODES`. Our [620.73.04 baseline](../patches/README.md) covers the post-615 ABI (`GSP_FMC_BOOT_PARAMS` 256 B) and provides the reference point for cross-version (`570` / `595` / `620`) differential probing.
