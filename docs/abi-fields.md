[English](abi-fields.md) | [中文](abi-fields.cn.md)

# Host boot ABI field reference

These structures describe what the GB20x host driver submits to FSP and GSP-FMC. Use the host-write column to interpret a snapshot before checking the [ABI invariants](abi-invariants.md).

> **Evidence level: static source analysis.** The layouts come from `NVIDIA/open-gpu-kernel-modules`, reviewed on 23 November 2026. Validation against a native Linux hardware trace is pending. Record the tag and snapshot if a measurement disagrees.

The [layout tool](../scripts/abi-layout.py) computes offsets and sizes. GCC 15.2.0 matched all 85 fields of four 620.73.04 structures against the upstream headers using `offsetof` and `sizeof`. The r570.144 `nova-core` bindings independently agree with the 570 layout. For the other tags, only total sizes were checked, including the upstream 256 B assertions for the 615 FMC parameters and WPR metadata. [Independent checks](references.md)

```bash
python3 scripts/abi-layout.py --list
python3 scripts/abi-layout.py --struct GSP_FMC_BOOT_PARAMS --diff 610.57.04 620.73.04
python3 scripts/abi-layout.py --struct GspFwWprMeta --tag 620.73.04 --decode dump.hex
python3 scripts/abi-layout.py --from-log my-gb20x-report/dmesg-gsp-fmc.txt
```

---

## 1. Call sequence and HAL bindings (620.73.04)

The GB202, GB203, GB205, GB206 and GB207 bindings come from `src/nvidia/generated/g_kernel_gsp_nvoc.c` and `g_kern_fsp_nvoc.c`.

| Step | Implementation | Upstream file | Structure |
|---|---|---|---|
| Wait for FSP secure boot | `kfspWaitForSecureBoot_GB202` | `fsp/arch/blackwell/kern_fsp_gb202.c` | — |
| Populate WPR metadata | `kgspPopulateWprMeta_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c` | `GspFwWprMeta` |
| Populate FMC parameters | `kgspSetupGspFmcArgs_GH100` | As above | `GSP_FMC_BOOT_PARAMS` |
| Build and send COT | `kfspPrepareBootCommands_GH100` → `kfspSendBootCommands_GH100` | `fsp/arch/hopper/kern_fsp_gh100.c` | `NVDM_PAYLOAD_COT` (MCTP/NVDM message, `NVDM_TYPE_COT`) |
| Check WPR2 status | `kgspIsWpr2Up_GB100` | `gsp/arch/blackwell/kernel_gsp_gb100.c` | Read only |

Source paths in the table are relative to `src/nvidia/src/kernel/gpu/`.

The boot-parameter implementations live in the Hopper source files. `kernel_gsp_gb202.c` handles vGPU heap sizing, partition counts and the ucode fuse version. At this tag `kernel_gsp_fwsec.c` contains only a license header. GB20x uses the FSP path in place of the older Booter.

In 570.124.04, `kgspCalculateFbLayout_GH100` populates `GspFwWprMeta`.

On COT failure, `kfspSendBootCommands_GH100` already logs `version`, `size` and the FMC, FRTS and boot-argument offsets at `LEVEL_ERROR`. The [trace patch](../patches/README.md) adds a success summary and snapshots of two host structures.

---

## 2. Layout changes by version

| tag | commit | `GSP_FMC_BOOT_PARAMS` | `GspFwWprMeta` | `NVDM_PAYLOAD_COT` |
|---|---|---:|---:|---:|
| `570.124.04` | `129479b1b734` | 80 B, no `magic` | 256 B | 860 B |
| `610.57.04` | `e4a5faa2567f` | 88 B, no `magic` | 256 B | 860 B |
| `615.71.10` | `6f3be8c9c3fb` | **256 B, with `magic` / `size`** | 256 B (`padding` → `pagingConfig`) | 860 B |
| `620.73.04` | `a4b95f6a2db0` | 256 B (adds `flags` and TDISP) | 256 B | 860 B |

- Between 610 and 615, the existing FMC parameters moved and the SPDM fields `payloadBufferSize` and `payloadBufferOffset` exchanged order. Decode offsets using the capture's tag.
- From 615 to 620, `bootGspRmParams` gained `flags` and `reserved`, and `gspTdispParams` used the final reserved space. Existing non-reserved fields retained their offsets.
- From 615, `gsp_fw_wpr_meta.h` omits `GspFwHeapFreeList` and two `RECOVERY_MARGIN` flags. In 620, `gsp_fw_wpr_meta_v1.h` defines `GspFwWprMetaV1` for the Turing–Ada Booter.
- The `NVDM_PAYLOAD_COT` header is byte-identical across 570.124.04, 595.112.07, 610.57.04, 615.71.10 and 620.73.04.

---

## 3. `GSP_FMC_BOOT_PARAMS` (620.73.04, 256 B)

The host clears the buffer before setting individual fields. Reserved fields should read zero. The in-memory magic is `"FMC "` (`0x20434d46`).

| Offset | Size (B) | Field | Host value |
|---:|---:|---|---|
| `0x000` | 4 | `magic` | `0x20434d46` |
| `0x004` | 2 | `size` | `256` |
| `0x020` | 4 | `initParams.regkeys` | CC / NVLink / TDISP proxy regkeys, bitwise OR |
| `0x040` | 4 | `bootGspRmParams.target` | Descriptor address space |
| `0x044` | 4 | `bootGspRmParams.gspRmDescSize` | `sizeof(GspFwWprMeta)` at cold boot or `sizeof(GspFwSRMeta)` on resume (both 256) |
| `0x048` | 8 | `bootGspRmParams.gspRmDescOffset` | Points to `GspFwWprMeta` at cold boot or `GspFwSRMeta` on resume |
| `0x050` | 8 | `bootGspRmParams.wprCarveoutOffset` | Untouched (0) |
| `0x058` | 4 | `bootGspRmParams.wprCarveoutSize` | Untouched (0) |
| `0x05c` | 1 | `bootGspRmParams.bIsGspRmBoot` | `1` |
| `0x05d` | 1 | `bootGspRmParams.bInstInSysMode` | Untouched (0) |
| `0x05e` | 1 | `bootGspRmParams.bIcuEnabled` | Chip property |
| `0x05f` | 1 | `bootGspRmParams.bScrubCbcSr` | Chip HAL |
| `0x060` | 4 | `bootGspRmParams.flags` | bit 0 = `SEC2_UCODE_SIDE_LOAD` |
| `0x080` | 4 | `gspRmParams.target` | LibOS argument address space |
| `0x088` | 8 | `gspRmParams.bootArgsOffset` | Physical address of LibOS initialization arguments |
| `0x0a0` | 4 | `gspSpdmParams.target` | When SPDM is enabled |
| `0x0a4` | 4 | `gspSpdmParams.payloadBufferSize` | When SPDM is enabled |
| `0x0a8` | 8 | `gspSpdmParams.payloadBufferOffset` | When SPDM is enabled |
| `0x0c0` | 8 | `gspRmMemParams.flushSysmemAddrValLo/Hi` | When CC workaround 5107790 is active |
| `0x0f8` | 1 | `gspTdispParams.bTdispEnabled` | Zero when TDISP is disabled |

Other offsets are reserved. `target` values are 0 for local video memory, 1 for coherent system memory and 2 for noncoherent system memory.

---

## 4. `GspFwWprMeta` (620.73.04, 256 B)

Host writes below come from assignments in `kgspPopulateWprMeta_GH100`. Tags 595.112.07, 610.57.04, 615.71.10 and 620.73.04 have the same assignment set except for `pagingConfig`, introduced in 615.

| Offset | Size (B) | Field | Host writes |
|---:|---:|---|:---:|
| `0x000` | 8 | `magic` (`0xdc3aae21371a60b3`) | Yes |
| `0x008` | 8 | `revision` (`1`) | Yes |
| `0x010` / `0x018` | 8 / 8 | `sysmemAddrOfRadix3Elf` / `sizeOfRadix3Elf` | Yes |
| `0x020` / `0x028` | 8 / 8 | `sysmemAddrOfBootloader` / `sizeOfBootloader` | Yes |
| `0x030` / `0x038` / `0x040` | 8 ×3 | `bootloaderCode` / `Data` / `ManifestOffset` | Yes |
| `0x048` / `0x050` | 8 / 8 | `sysmemAddrOfSignature` / `sizeOfSignature` | When a signature is present |
| `0x058` | 8 | `gspFwRsvdStart` | No |
| `0x060` | 8 | `nonWprHeapOffset` | No |
| `0x068` | 8 | `nonWprHeapSize` | Yes |
| `0x070` | 8 | `gspFwWprStart` | No |
| `0x078` | 8 | `gspFwHeapOffset` | No |
| `0x080` | 8 | `gspFwHeapSize` | Yes (minimum request) |
| `0x088` / `0x090` | 8 / 8 | `gspFwOffset` / `bootBinOffset` | No |
| `0x098` | 8 | `frtsOffset` | No |
| `0x0a0` | 8 | `frtsSize` | Yes |
| `0x0a8` | 8 | `gspFwWprEnd` | No |
| `0x0b0` | 8 | `fbSize` | No |
| `0x0b8` | 8 | `vgaWorkspaceOffset` | No |
| `0x0c0` | 8 | `vgaWorkspaceSize` (128 KiB) | Yes |
| `0x0c8` | 8 | `bootCount` | No |
| `0x0d0` | 16 | Union: partition RPC view or padding | No |
| `0x0e0` / `0x0e8` | 8 / 4 | `sysmemAddrOfCrashReportQueue` / `sizeOfCrashReportQueue` | When the queue is in system memory |
| `0x0ec` | 4 | `lsUcodeVersion` union padding | No |
| `0x0f0` | 1 | `gspFwHeapVfPartitionCount` | Yes (0 for non-vGPU) |
| `0x0f1` | 1 | `flags` | `PPCIE_ENABLED` bit only |
| `0x0f2` | 2 | `pagingConfig` | From 615 |
| `0x0f4` | 4 | `pmuReservedSize` | Yes |
| `0x0f8` | 8 | `verified` | No |

The host requests sizes. ACR ucode fills layout offsets when it establishes WPR2. Through 610, `kernel_gsp.c` later copies `nonWprHeapOffset` and `frtsOffset` from static configuration into the host copy after GSP-RM starts.

A pre-submit GB20x snapshot should therefore show zero for `gspFwWprStart`, `gspFwWprEnd`, `fbSize`, `bootCount` and `verified`. On Turing–Ada, `kernel_gsp_tu102.c` computes the layout on the host.

---

## 5. Suspend and resume with `GspFwSRMeta` (256 B)

`kgspSetupGspFmcArgs_GH100` selects the descriptor by `bootMode`.

| `bootMode` | Name | `gspRmDescOffset` points to |
|---:|---|---|
| 0 | `KGSP_BOOT_MODE_NORMAL` | `GspFwWprMeta` |
| 1 | `KGSP_BOOT_MODE_SR_RESUME` | **`GspFwSRMeta`** |
| 2 | `KGSP_BOOT_MODE_GC6_EXIT` | Selects `GspFwWprMeta` if reached. Call path unverified. |
| 3 | `KGSP_BOOT_MODE_SR_WITH_WPR_IN_SYSMEM` | As above |

Only `SR_RESUME` selects `GspFwSRMeta`. Use the boot mode and descriptor type to identify this path.

| Offset | Size (B) | Field |
|---:|---:|---|
| `0x000` / `0x008` | 8 / 8 | `magic` / `revision` (`2`) |
| `0x010` / `0x018` | 8 / 8 | `sysmemAddrOfSuspendResumeData` / `sizeOfSuspendResumeData` |
| `0x020` | 128 | `internal[32]` (internal to secure ucode) |
| `0x0a0` / `0x0a4` | 4 / 4 | `flags` / `subrevision` |
| `0x0a8` | 88 | `padding[22]` |

---

## 6. FSP payload `NVDM_PAYLOAD_COT` (860 B, packed)

| Offset | Size (B) | Field |
|---:|---:|---|
| `0x000` / `0x002` | 2 / 2 | `version` / `size` (860) |
| `0x004` | 8 | `gspFmcSysmemOffset` |
| `0x00c` / `0x014` | 8 / 4 | `frtsSysmemOffset` / `frtsSysmemSize` |
| `0x018` / `0x020` | 8 / 4 | `frtsVidmemOffset` (**relative to the end of video memory**)/ `frtsVidmemSize` |
| `0x024` | 48 | `hash384[12]` |
| `0x054` | 384 | `publicKey[96]` |
| `0x1d4` | 384 | `signature[96]` |
| `0x354` | 8 | `gspBootArgsSysmemOffset` → `GSP_FMC_BOOT_PARAMS` |

The three arrays have fixed storage. The HAL specifies the bytes used on each chip. Unused bytes are zero-filled.

| Chip | COT `version` | Public key | Signature | Algorithm |
|---|---:|---:|---:|---|
| GH100 | 1 | 384 B | 384 B | RSA-3072 |
| GB100 / GB10x / **GB20x** | **2** | **97 B** | **96 B** | ECDSA P-384 (inferred from sizes) |

Sizes come from the 620.73.04 `cotPayload*` HAL fields. `nova-core` agrees on the versions. The kernel documentation's RSA-3K example applies to GH100.

### Message headers and replies

Two 32-bit headers precede COT in both `nova-core` (`mctp.rs`) and the kernel documentation.

| Header | Bits | Meaning |
|---|---|---|
| MCTP | `31` / `30` / `29:28` / `23:16` / `15:8` / `3:0` | SOM / EOM / sequence / source endpoint / destination endpoint / version |
| NVDM | `31:24` / `23:8` / `6:0` | Type (`0x14` COT, `0x13` PRC, `0x15` reply) / vendor `0x10de` / message type `0x7e` |

After both headers, a reply contains three `u32` values: `task_id`, `command_nvdm_type` and `error_code`. A nonzero error code indicates failure. The log prints these as `Task ID`, `Command type` and `Error code`. [Log interpretation](recovery.md)

Before transmission the host waits for FSP readiness. GB20x reads `0x00ad00bc` and expects `0xff` after secure boot. GH100/GB10x use `0x000200bc`. The `nova-core` timeout is five seconds.

In the static interface, `RmDisableCotCmd` defaults to `0`. Fields `1:0`, `3:2` and `5:4` refer to FRTS system memory, FRTS video memory and GSP-FMC respectively.

---

## 7. WPR2 addresses and encoding

- Extract bits `31:4` and shift left 12: `byte_address = (raw >> 4) << 12`. For a zero low nibble this equals `raw << 8`. GB100 `NV_HUBMMU_PRI_MMU_WPR2_ADDR_*` and TU102 `NV_PFB_PRI_MMU_WPR2_ADDR_*` use the same encoding.
- On GB205, driver `621.01` returned `0x02fb8800..0x02fc1000`, or 12216.5–12225 MiB (**8.5 MiB**) within `FB_SIZE_MIB = 12227`. Driver `616.64` returned `0x02fa7e00..0x02fb0400` (**8.375 MiB**).
- `kgspIsWpr2Up_GB100` uses `NV_HUBMMU0_PRI_BASE (0x880000) + 0xa828`, or `0x88a828` (LO `0x88a824`). Community captures use the older `0x1fa824/28`. The GB205 results there are self-consistent, but aliasing remains unverified. Record both pairs. `nova-core` uses the older pair only on Turing–Ada.
- `nova-core` (`fb/regs.rs`) describes LO as inclusive and HI as exclusive. Zero HI means unset. Span is `HI − LO`.
- The archived GB205 captures used Windows `LEGACY` mode. Compare WPR2 sizes within the same driver mode.
