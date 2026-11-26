[English](README.md) | [中文](README.cn.md)

# GB205 measurements on the RTX 5070 Ti Laptop

The local baseline is an NVIDIA GeForce RTX 5070 Ti Laptop GPU, PCI `10de:2f18`, subsystem `1558:6586`, VBIOS `98.05.31.00.44`. It has 46 SMs in 23 TPCs across five active GPCs, 12 GiB GDDR7 and a 140 W maximum power limit.

## Hardware & Register Baseline

Source: [`bar0-prom-metadata-621.01.txt`](bar0-prom-metadata-621.01.txt) (captured read-only on Windows 11 26H2 with driver `621.01` / `32.0.16.2101`).

- **Silicon identity**: `PMC_BOOT_0 = 0x1b5000a1` (GB205 A1), `GR_TOPO_409604 = 0x06030005` (6 GPC slots / 5 active GPCs), `FB_SIZE_MIB = 0x00002fc3`.
- **Privilege walls**:
  - `FEAT_OVR_PLM (0x00823804) = 0xfffffb8f` (closed; PL3-only writes).
  - `FUSE_SS_PLM = 0xfffffbff`, `GFW_PLM (0x00118128) = 0x00008b8f`, `MMU_PLM (0x001fa7c8) = 0x0884cb8f`.
  - `SECURE_FEAT_OVR_QUADRO_WR = 0x00000002`, `FUSE_EN_SW_OVERRIDE = 0x00000000`, `SM_ISSUE_RATE_MODIFIER = 0x00000005`.
- **Rate selectors & fuse mirrors**:
  - `SS0 (0x0082381c) = 0x76027063`, `SS1 (0x00823820) = 0x00030004`, `READOUT_1 (0x00823818) = 0x00c36180`.
  - Fuse mirrors: `FUSE_SS_FMLA32 = 3`, `FUSE_SS_IMLA1/2/4 = 3`, `FUSE_SS_DP/FFMA/FMLA16/IMLA0/IMLA3 = 0`.
- **WPR2 & driver delta (`616.64 → 621.01`)** — raw words from [`bar0-core-616.64.txt`](bar0-core-616.64.txt) and [`bar0-prom-metadata-621.01.txt`](bar0-prom-metadata-621.01.txt); bytes are `(raw >> 4) << 12`, see [`abi-fields.md` §7](../../docs/abi-fields.md):

  | Driver | `WPR2_LO` | `WPR2_HI` | Byte range (MiB) | Span |
  | --- | --- | --- | --- | ---: |
  | `616.64` | `0x02fa7e00` | `0x02fb0400` | 12199.875 – 12208.25 | 8.375 MiB |
  | `621.01` | `0x02fb8800` | `0x02fc1000` | 12216.5 – 12225 | 8.5 MiB |

  Both ranges lie below `FB_SIZE_MIB` (12227 MiB). `SEC2_MBOX0` also moved (`0xbf7b2000 → 0xbf7d4000`), as did the `SS0`/`SS1` shadows (`0x56027063`/`0x00032004 → 0x76027063`/`0x00030004`); every other register tracked by [`parse-ssdiag.py`](../../scripts/parse-ssdiag.py) and present in both captures is identical, and `FEAT_OVR_PLM` stays at `0xfffffb8f`. The two captures differ in both driver and cold boot, so the shift cannot be attributed to the driver alone. Both were taken in Windows `LEGACY` mode and read the Turing-era `0x1fa824/28` addresses; the Blackwell driver itself reads `0x88a824/28`, which has not been sampled.
- **VBIOS / PROM metadata**:
  - 5 verified `PCIR` images; BIT at `PROM+0x361f0` (20 tokens, `adjust = 0x4d400`); ucode table at `PROM+0xa25a1` (35 slots: 6 populated, 4 within 1 MiB BAR0 PROM aperture, all `V6` 80-byte descriptors with `ENC(bit2) = YES`).
  - Full-ROM object directory: 10/10 Class A manifest ids start at the same-numbered object and 6/6 V6 descriptors fill their object exactly ([`vbios-v6-descriptor.md` §6.10](../../docs/vbios-v6-descriptor.md)).
  - Full structural analysis (zero raw firmware bytes): [`../../docs/vbios-v6-descriptor.md`](../../docs/vbios-v6-descriptor.md).

## Steady-State Tensor Clamp Baseline

Captured with [`../../software/clamp_eval.py`](../../software/clamp_eval.py) on PyTorch `2.11.0+cu128` / CUDA `12.8` (driver `621.01`, ~140 W power cap):

| Case | `N=8192` TFLOP/s | `N=8192` FLOP/c/SM | `N=8192` Ratio of Full | `N=8192` relL2 | `N=4096` Ratio of Full | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `fp16_fp16acc` | 82.41 | 853.1 | 0.833× (of 1024) | `0.003244` | 0.796× | Unclamped control |
| `fp16_fp32acc` | 53.72 | 505.5 | **0.494×** (of 1024) | `0.000196` | **0.486×** | **CLAMPED (~0.5×)** |
| `bf16_fp32acc` | 60.41 | 487.8 | **0.476×** (of 1024) | `0.001748` | **0.470×** | **CLAMPED (~0.5×)** |
| `tf32_fp32acc` | 30.15 | 247.6 | **0.484×** (of 512) | `0.000296` | **0.492×** | **CLAMPED (~0.5×)** |
| `fp8_fp32acc` | 111.80 | 1016.1 | **0.496×** (of 2048) | `0.001665` | **0.537×** | **CLAMPED (~0.5×)** |
| `int8` | 65.04 | 529.6 | 0.259× (of 2048) | n/a | 0.255× | Informational |
| `fp32_control` | 17.98 | n/a | n/a | `0.0000016` | n/a | SIMT control |

- Paired ABBA (`off → on → on → off`) `allow_fp16_accumulation` ratio at `N=8192`: **1.55×–1.58×** raw TFLOP/s (**1.68×–1.71×** clock-normalized).
- `N=4096` bursts are short: `fp16_fp16acc` and `fp8_fp32acc` have only 9 and 6 active telemetry samples, so their clock-normalized ratios are less certain than the `N=8192` ones.
- Full JSON records: [`clamp-eval-621.01-n8192.json`](clamp-eval-621.01-n8192.json), [`clamp-eval-621.01-n4096.json`](clamp-eval-621.01-n4096.json).

## Route 2 Software Trade-Off (`chunked_fp16_matmul.py`)

Captured with [`../../software/chunked_fp16_matmul.py`](../../software/chunked_fp16_matmul.py) at `N=K=8192` (~100 W sustained laptop power envelope; full JSON: [`chunked-fp16-621.01.json`](chunked-fp16-621.01.json)):

| Variant | Chunks | TFLOP/s | vs `fp32acc` | SM Clock (MHz) | FLOP/c/SM | relL2 | vs full `fp16acc` err |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_fp32acc` | — | 44.29 | 1.00× | 1852 | 519.9 | `0.000196` | 16.57× lower |
| `chunk_k=512` | 16 | 28.25 | 0.64× | 1717 | 357.7 | `0.000870` | 3.73× lower |
| `chunk_k=1024` | 8 | 38.54 | 0.87× | 1785 | 469.3 | `0.001142` | 2.84× lower |
| `chunk_k=2048` | 4 | **48.90** | **1.10×** | 1800 | 590.6 | `0.001644` | **1.97× lower (~49% cut)** |
| `chunk_k=4096` | 2 | **56.27** | **1.27×** | 1848 | 661.7 | `0.002140` | **1.52× lower (~34% cut)** |
| `full_fp16acc(k=8192)` | 1 | 65.74 | 1.48× | 1672 | 854.7 | `0.003244` | 1.00× |

At `N=8192`, chunks of 2048 and 4096 improved throughput by 10% and 27% over the FP32-accumulate baseline. Relative L2 error fell by 49% and 34%, respectively, compared with full-length FP16 accumulation. Smaller chunks spent more time moving intermediate matrices. A fused kernel remains an unmeasured alternative.

## Files in This Folder

- [`bar0-prom-metadata-621.01.txt`](bar0-prom-metadata-621.01.txt) — Sanitized BAR0 + PROM BIT/V6 metadata dump (driver `621.01`)
- [`bar0-core-616.64.txt`](bar0-core-616.64.txt) — Register rows of the earlier driver `616.64` capture
- [`rom-directory-98.05.31.00.44.json`](rom-directory-98.05.31.00.44.json) — Object directory ids, offsets and sizes of the full ROM (155 objects, all hashes verified locally)
- [`v6-descriptors-98.05.31.00.44.json`](v6-descriptors-98.05.31.00.44.json) — Decoded V6 descriptor and Class A manifest scalars
- [`clamp-eval-621.01-n8192.json`](clamp-eval-621.01-n8192.json) — Steady-state clamp evaluation (`N=8192`)
- [`clamp-eval-621.01-n4096.json`](clamp-eval-621.01-n4096.json) — Steady-state clamp evaluation (`N=4096`)
- [`chunked-fp16-621.01.json`](chunked-fp16-621.01.json) — Route 2 K-chunked FP16-acc Pareto sweep (`N=8192`)
- [`route2-wt2-288k-fp64.json`](route2-wt2-288k-fp64.json) — Per-chunk (282 × 1,024 = 288,768 tokens) WikiText-2-raw KL, Top-1 and NLL records vs. true FP64 on `unsloth/Llama-3.2-1B` (reproducible on CPU via [`../../software/verify_wt2_bootstrap.py`](../../software/verify_wt2_bootstrap.py))
