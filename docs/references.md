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
