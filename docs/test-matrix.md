[English](test-matrix.md) | [中文](test-matrix.cn.md)

# Blackwell measurements by GPU and driver

The tables separate register readbacks, ROM metadata and steady-state measurements. A dash marks a missing observation. Compare only values measured on the named card.

---

## 1. Register and privilege-mask baselines

| Register / metric | BAR0 offset | GB202<br> (RTX 5090) | GB203M | GB203<br> (5080 / 5070 Ti) | **GB205**<br> (5070 Ti Laptop) | GB206<br> (5060 Ti 16G) | GB207<br> (5050) | RTX PRO 6000<br> (GB202 workstation) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PCI Device ID | — | — | — | — | **`10de:2f18`** | `10de:2d04` | — | `10de:2bb1` |
| `PMC_BOOT_0` | `0x000000` | `0x21120000` (unconfirmed ①) | `0x1b3000a1` | — | **`0x1b5000a1`** | `0x1b6000a1` | — | — |
| `FEAT_OVR_PLM` | `0x823804` | **`0xfffffb8f`** | **`0xfffffb8f`** | — | **`0xfffffb8f`** | **`0xfffffb8f`** | — | **`0xfffffb8f`** |
| `FEAT_READOUT_1` | `0x823818` | **`0x00c36180`** | **`0x00c36180`** | — | **`0x00c36180`** | **`0x00c36180`** | — | **`0x00000000`** |
| `FEAT_READOUT_0` | `0x823814` | `0x00000233` | — | — | `0x00000233` | `0x00000233` | — | — |
| `FEAT_OVR_ECC` / `ECC_1` | `0x82380c/10` | `0x00888888` / `0x002a2aaa` | — | — | `0x00888888` / `0xaa2a2aaa` | `0x00888888` / `0xaa2a2aaa` | — | — |
| `FUSE_SS_FMLA32` | `0x8207d8` | **3** | **3** | — | **3** | **3** | — | **0** |
| `FUSE_SS_IMLA1 / 2 / 4` | `0x8207e0/e4/ec` | 3 / 3 / 3 | 3 / 3 / 3 | — | 3 / 3 / 3 | 3 / 3 / 3 | — | 0 / 0 / 0 |
| `FUSE_SS_DP/FFMA/FMLA16/IMLA0/3` | — | all 0 | all 0 | — | all 0 | all 0 | — | all 0 |
| `FUSE_FEAT_OVR_DIS` | `0x8203f0` | 0 | 0 | — | 0 | 0 | — | — |
| `FUSE_EN_SW_OVERRIDE` | `0x820040` | 0 | 0 | — | 0 | 0 | — | — |
| `FUSE_SS_PLM` | `0x8200fc` | — | `0xfffffbff` | — | `0xfffffbff` | `0xfffffbff` | — | — |
| `SECURE_FEAT_OVR_QUADRO_WR` | `0x82038c` | 2 | — | — | 2 | 2 | — | — |
| `SM_ISSUE_RATE_MODIFIER` | `0x504204` | 5 | — | — | **5** | 5 | — | — |
| `SEC2_PLM` | `0x8403c4` | — | — | — | **`0x00000b0f`** | `0x00000b0f` | — | — |
| `GSP_CPUCTL` | `0x110100` | `0xbadf4100` | — | — | `0xbadf5720` | `0xbadf5720` | — | — |
| `WPR2_LO / HI` ② | `0x1fa824/28` | — | — | — | `0x02fb8800` / `0x02fc1000` | — | — | — |
| Source | — | Community capture | Community capture | — | [reports/gb205-2f18/](../reports/gb205-2f18/README.md) | Community capture (`nv-uefi-unlocker` #168) | — | Community capture |

- ① The published GB202 value needs a second capture. `PMC_BOOT_0[31:20]` is `0x1b3`, `0x1b5` and `0x1b6` on GB203, GB205 and GB206. The same pattern predicts `0x1b2` for GB202. Treat the published word as unconfirmed.
- ② WPR2 depends on the boot and driver. For the address pairs and conversion, see [WPR2 encoding](abi-fields.md#7-wpr2-addresses-and-encoding).
- The GB202 `GSP_CPUCTL` value `0xbadf4100` belongs to the `0xbadf41xx` pattern seen while FSP owns access (`nova-core`, `falcon/gsp.rs`). The meaning of `0xbadf5720` on GB205 and GB206 is unknown.
- Regenerate the GB205 column with `python3 scripts/parse-ssdiag.py reports/gb205-2f18/bar0-prom-metadata-621.01.txt --markdown`.

---

## 2. PROM BIT and V6 metadata by die

| Die and model | VBIOS version | BIT offset / `adjust` | Ucode slots (total / used / empty) | Active APP IDs | Descriptor format | Evidence |
|---|---|---|---|---|---|---|
| **GB205 (RTX 5070 Ti Laptop `2f18`)** | `98.05.31.00.44` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | `0x07, 0x15, 0x18, 0x19, 0x23, 0x24` (`0x18/0x24` lie outside the 1 MiB BAR0 window) | All 80 B **V6** (`vDesc` bit 2 set throughout; `0x19/0x15` have 20-byte Class A rows) | [vbios-v6-descriptor.md](vbios-v6-descriptor.md) |
| GB202 (RTX 5090 `2b85`) | `98.02.2E.80.C8` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB202 (RTX 5090 D `2b87`) | `98.02.31.40.4C` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB202 (RTX PRO 6000 `2bb1`) | `98.02.52.00.02` | `0x365F0` / `0x4D800` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB203 (RTX 5080 `2c02`) | `98.03.3B.C0.3E` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB203 (RTX 5070 Ti `2c05`) | `98.03.3B.80.2F` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB203 (RTX 5070 Ti `2c05`) | `98.03.58.40.B6` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB205 (RTX 5070 ASUS `2f04`) | `98.05.28.00.64` | `0x391F0` / `0x50400` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB205 (RTX 5070 Palit `2f04`) | `98.05.28.00.1D` | `0x391F0` / `0x50400` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB206 (RTX 5060 Ti `2d04`) | `98.06.1F.00.CD` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB206 (RTX 5060 `2d05`) | `98.06.21.40.42` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |
| GB207 (RTX 5050 `2d83`) | `98.07.1D.00.55` | `0x365F0` / `0x4D800` | 35 / 6 / 29 | Same as GB205 laptop | All 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.md) |

Run `python3 scripts/v6-closure.py <file>` on decoded scalars to check which GB205 relationships hold on another card. The GB205 archive includes [V6 scalars](../reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json) and [IDs, offsets and sizes](../reports/gb205-2f18/rom-directory-98.05.31.00.44.json) for 155 verified objects in seven directories.

---

## 3. Steady-state tensor throughput

Each ratio is measured FLOP per clock per SM divided by the architectural full-rate ceiling for that case. Parentheses give measured throughput.

| Die / model | Driver | Size | `fp16_fp16acc` | `fp16_fp32acc` | `bf16_fp32acc` | `tf32_fp32acc` | `fp8_fp32acc` | Record |
|---|---|---|---:|---:|---:|---:|---:|---|
| **GB205 (5070 Ti Laptop, 46 SM)** | `621.01` | `N=8192` | **0.83×** (82.41 TF/s) | **0.49×** (53.72 TF/s) | **0.48×** (60.41 TF/s) | **0.48×** (30.15 TF/s) | **0.50×** (111.80 TF/s) | [clamp-eval-621.01-n8192.json](../reports/gb205-2f18/clamp-eval-621.01-n8192.json) |
| **GB205 (5070 Ti Laptop, 46 SM)** | `621.01` | `N=4096` (400 iters) | **0.80×** (83.21 TF/s) | **0.49×** (54.07 TF/s) | **0.47×** (59.98 TF/s) | **0.49×** (29.60 TF/s) | **0.54×** (117.68 TF/s) | [clamp-eval-621.01-n4096.json](../reports/gb205-2f18/clamp-eval-621.01-n4096.json) |
| GB202 / GB203 / GB206 / GB207 | — | — | — | — | — | — | — | [Report template](../reports/template.md) |

Compare a new run with the archived baseline after a driver change. A ratio outside tolerance produces exit code 3.

```bash
python3 scripts/verify-baseline.py --bench --compare reports/gb205-2f18/clamp-eval-621.01-n8192.json
```

Shorter bursts at `N=4096` yielded only nine active samples for `fp16_fp16acc` and six for `fp8_fp32acc`. Their clock-normalized ratios have greater uncertainty than at `N=8192`.

---

## 4. Measurements still needed

1. Full register and `--prom-bit` captures from desktop GB203 (5080 or 5070 Ti) and GB207 (5050).
2. Native Linux cold-boot and S3/s2idle resume logs for WPR2 and GSP on any GB20x using the open driver (`INV-S02`, `INV-X01`).
3. Readbacks from both WPR2 address pairs, `0x1fa824/28` and `0x88a824/28`, on the same GB20x card and boot.
4. An independent GB202 `PMC_BOOT_0` readback to check note ①.
5. A GB20x FSP-ready register readback at `0x00ad00bc`. The expected value is `0xff`. See the [ABI field reference](abi-fields.md).
6. Metadata from `scripts/rom-survey.py --export`. Follow the [source guidance](references.md). Priority samples are another laptop (to distinguish product, mobile and firmware effects on six objects), any ROM testing the relationship between objects `0x52`, `0x5c`, `0x74`, `0x76` and outer `+00C`, a consumer GB202 on `98.02.5x` or a version-matched GB203/GB206 RTX PRO, and another version family of a sampled model (different third version byte).
7. RTX PRO Blackwell register captures. ROM metadata alone cannot establish fuse and PLM values.
