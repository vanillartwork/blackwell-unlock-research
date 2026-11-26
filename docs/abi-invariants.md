[English](abi-invariants.md) | [中文](abi-invariants.cn.md)

# Host-to-FMC boot ABI observations

Use these IDs to label read-only snapshots of GB20x boot parameters, driver logs and register readbacks. Each row identifies the evidence needed to interpret a result.

> **Capture conditions**
> Use native Linux with an independently driven console, tested SSH and off-host logging (netconsole or serial). After an FMC timeout, hung `ioctl` or unexpected status, save the log and power-cycle before another observation. Collect cold boot, reload and resume as separate samples. [Recovery and classification](recovery.md)

> The 595–620 host code leaves `gspFwWprStart`, `gspFwWprEnd`, `fbSize`, `bootCount`, `verified` and layout offsets untouched at submission. Firmware populates the layout as it establishes WPR2. Decode the [620.73.04 trace](../patches/README.md) with `scripts/abi-layout.py --from-log` and consult the [host-write table](abi-fields.md).

---

## 1. State and flags

Record the boot mode and the stage at which each value was sampled.

| ID | Field | Boot context | Static baseline or observation | Record |
|---|---|---|---|---|
| `INV-S01` | `verified` | Cold boot | The host clears the buffer and leaves this field untouched. Expect `0` before submission. Upstream defines `0` and `0xa0a0a0a0a0a0a0a0`. Third-party `595.91.07` GB202 probing found `verified` inert on cold boot. | Snapshot stage, mailbox status, WPR2 bounds and ordered log |
| `INV-S02` | `bootCount` | Cold boot, reload, S3/s2idle resume | The host leaves the count untouched. `SR_RESUME` selects `GspFwSRMeta` revision `2`. Third-party `595.91.07` GB202 captures observed `SRMeta` (`magic=0x8a3bb9e6c6c39d93`, `rev=2`, `size=0xcde0000`, `bootMode=1`) + a plaintext 3-level `radix3` table (52,704 4 KiB PTEs) in sysmem, with `205.875 MiB` of per-boot, per-card high-entropy ciphertext (`7.9998 bits/byte`); corrupting `magic` or `rev` hangs silently. | `bootMode`, descriptor type, `sizeOfSuspendResumeData`, WPR2 and GSP mailbox status |
| `INV-S03` | Boot mode and COT flags | Cold boot | Record the driver configuration and any host-reset timeout. The documented COT fields refer to FRTS system/video memory and GSP-FMC. The default is `0`. | Module parameters, error text and any uninterruptible wait |
| `INV-S04` | `FEAT_SS0/SS1` | Across cold boots and within a boot | Compare selector nibbles, enable bit 3 and `FEAT_OVR_PLM`. GB205 shadows changed from `0x56027063/0x00032004` to `0x76027063/0x00030004`; the privilege mask stayed `0xfffffb8f`. Third-party `595.91.07` GB202 tests confirmed the bank is already final before FMC runs, `SS0`/`SS1` change only across cold power cycles (surviving warm reboot and FLR), and post-FLR `FEAT_OVR_PLM = 0x80842bcf` (PL2+PL3 write) is autonomously re-locked to `0xfffffb8f` by GPU-side devinit within ~1.16–1.35 s (before first driver open, ~1.85 s before GSP-RM). | Readbacks at `0x82381c/0x823820`, boot state and timestamps |

---

## 2. Bounds and alignment

Compare host size requests with the bounds firmware actually established.

| ID | Fields | Measurement | Expected relationship | Record |
|---|---|---|---|---|
| `INV-B01` | `gspFwWprStart`, `gspFwWprEnd`, `fbSize` | WPR2 address alignment | These are firmware outputs on GB20x. Use LO/HI readbacks to assess alignment (4 KiB, 64 KiB, 1 MiB). | Host snapshot, WPR2 words and status |
| `INV-B02` | Image, FRTS and heap sizes | Sizes in normal captures | The host supplies `sizeOfRadix3Elf`, `sizeOfBootloader`, `frtsSize`, `nonWprHeapSize` and `gspFwHeapSize`. Compare by card and driver. Third-party `595.91.07` GB202 tests showed `COT.frtsVidmemOffset` (128 KiB-aligned) shifts the full WPR layout 1:1 across at least 1 GiB, while `WprMeta.frtsSize` is decorative (COT is authoritative). | Sizes, status, WPR2 and MMU lock state |
| `INV-B03` | Start, end and size | WPR2 range | An active region has `LO < HI` and fits in framebuffer memory. `HI = 0` indicates unset. Firmware computes these bounds. | Snapshot, framebuffer size and any driver/FMC error |
| `INV-B04` | `gspFwHeapSize` | Supported heap configurations | The requested size is a minimum. Alignment padding can increase the final allocation. Third-party `595.91.07` GB202 tests confirmed `gspFwHeapSize` and `nonWprHeapSize` both scale 1:1 downward without exposing WPR (host WPR reads return `0xbad0ac00`; the non-WPR heap holds no RM objects). | Requested size, observed WPR2 span, `FB_SIZE_MIB` and layout log |

---

## 3. Cross-structure checks

Compare fields with the same units and identify their producer before interpreting a difference.

| ID | Fields | Baseline | Record |
|---|---|---|---|
| `INV-C01` | Outer and inner lengths | From 615, outer `size` is `256` and `gspRmDescSize` is `256`. `sizeOfRadix3Elf` and `sizeOfBootloader` describe images. `gspFwRsvdStart` is a firmware-produced offset. | Tag, snapshot and status |
| `INV-C02` | Signature metadata and COT | `sysmemAddrOfSignature` and `sizeOfSignature` describe GSP-RM metadata. The 860 B COT v2 payload for GB20x uses 97 B of public key and 96 B of signature, with zero padding. Third-party `595.91.07` GB202 probing confirmed `sysmemAddrOfSignature` is a live 4-byte-aligned pointer, whereas `sizeOfSignature`, `bootloaderCodeOffset`, `bootloaderDataOffset` and `fbSize` are inert on cold boot; neither FMC nor `GspRmBoot` is decrypted in place in sysmem, and both are bound to authentication including headers. | Lengths and return code only |
| `INV-C03` | Magic and revision | `GspFwWprMeta` magic `0xdc3aae21371a60b3`, revision `1`; `GSP_FMC_BOOT_PARAMS` magic `"FMC "` from 615; `GspFwSRMeta` revision `2`. | Driver tag, structure and status |
| `INV-C04` | Radix3 and bootloader addresses | Check 4 KiB alignment in `sysmemAddrOfRadix3Elf` and `sysmemAddrOfBootloader`. `target` values range from `0` to `2`. Third-party `595.91.07` GB202 probing found `sysmemAddrOfRadix3Elf` effectively pinned (`0x83e` on shift) and `sizeOfBootloader` 4 KiB-aligned with a 256 MiB refusal (`0x836`). | Alignment, address-space type and any kernel/IOMMU or FMC error |

---

## 4. Cross-driver and cross-die comparisons

Use read-only captures and pinned source tags for these comparisons.

| ID | Comparison | What to check | Available evidence |
|---|---|---|---|
| `INV-X01` | Same GPU, different drivers | WPR2 bounds, SEC2 mailboxes, GSP-RM boot metadata | GB205 Windows `LEGACY`: `616.64` returned `0x02fa7e00..0x02fb0400` (8.375 MiB); `621.01` returned `0x02fb8800..0x02fc1000` (8.5 MiB). `SEC2_MBOX0` moved from `0xbf7b2000` to `0xbf7d4000`. The SS shadows also changed. Shared fuse/PLM observations agree. Driver and cold boot both changed. |
| `INV-X02` | Consumer dies | Compare overlapping FEAT, fuse, SM issue and SEC2 privilege readings | GB202, GB203M, GB205 and GB206 share the sampled rate selectors. Desktop GB203 and GB207 register captures are pending. [Test matrix](test-matrix.md) |
| `INV-X03` | ROMs across dies and OEMs | Used slots, targets, `+28`, printable tags and size residuals | All twelve ROMs across five dies share six active APPs in 35 slots, `0x01012801`, `OOBHUB`/`FSP_RUN` and the `0xA00` residual pattern. [V6 study](vbios-v6-descriptor.md) |
| `INV-X04` | RTX PRO vs consumer | Fuses, `FEAT_READOUT_1`, V6 flags and WPR2 | GB202 PRO 6000 reports zero arithmetic-rate selectors and `FEAT_OVR_PLM = 0xfffffb8f`. The `98.02.52` V6 sample has the same slots, flags and constants as the consumer samples. Payload and version both differ. WPR2 comparison is pending. [Survey](vbios-v6-descriptor.md) |
| `INV-X05` | Upstream tags | Compare FMC, WPR and COT layouts | Recorded tags: `570.124.04`, `610.57.04`, `615.71.10`, `620.73.04`. FMC parameters grew from 88 B to 256 B between 610 and 615. [Layout changes](abi-fields.md#2-layout-changes-by-version) |

---

## 5. Reporting

Include the invariant ID and an outcome label in the [report template](../reports/template.md).

- `PASS_FAIL_CLOSED`: the driver or firmware rejected the operation as expected, with no observed side effect.
- `OBSERVED_BASELINE`: collected a read-only baseline or a comparison across boots or drivers.
- `TIMEOUT_OR_HANG`: include the sanitized log tail and the subsequent cold-boot outcome.
- `POST_RESET_SAMPLE`: captured after SBR, FLR or warm reboot. Classify separately from cold boot. [Recovery rules](recovery.md)
- `ANOMALY_NEEDS_REPRO`: unexpected behavior or status awaiting confirmation on an independent cold boot.

---

## 6. Third-party GB202 triage and remaining Route 1 open leads

Third-party hardware experiments on 2× RTX 5090 (GB202 `10de:2b85`, Linux `595.91.07`) in [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049) (CC BY 4.0; summarized in [references.md](references.md#6-third-party-gb202-hardware-experiments-shuttiebf16-unlock-blackwell-research-d85201e2465d) and [recovery.md](recovery.md#11-observed-fspfmc-error-codes-and-failure-tiers-third-party-gb202-5959107)) have already closed most straightforward cold-boot and runtime dead ends on `595.91.07`:

- **Closed on `595.91.07` GB202:** direct PL0/PL2 register writes (including the Ada `SS0`/`SS1` payload), `SM_ISSUE_RATE_MODIFIER` (reasserted per CUDA context and inert), FECS doorbell, `RMOverrideSmSpeedSelect*`, `GT200_DEBUGGER`, plaintext VBIOS devinit tables (no FEAT literals; `0xfffffb8f` sits inside signed `app 0x23 / tgt 0x0e` data and DEVINIT tables are HS-authenticated with AES-ECB bound to device ID), COT key substitution (`0x1db`), in-place decryption of FMC or `GspRmBoot`, non-WPR heap inspection, and P2P peer reads of WPR (`0xbad0ac00`).

For researchers with bare-metal Linux GB20x test hardware, the genuinely open Stage 2 leads that remain untested or incomplete are:

1. **`SRMeta` + `radix3` PTE mutations (`SR_RESUME`):** mutate `radix3` PTE `flags`, `size`, and malformed/permuted entries, and flip a single bit in a zero-page ciphertext block to test whether the `205.875 MiB` resume blob enforces integrity or confidentiality only (requires netconsole/serial logging).
2. **Remaining `.fwsignature_gb20x` and `WprMeta` parser edges:** map `0x87c` mode-2 record layout, probe the second host buffer behind `sysmemAddrOfSignature`, and bisect the exact `sizeOfBootloader` `0x836` refusal boundary between `16 MiB` and `256 MiB`.
3. **Unvaried boot fields and sysmem buffer census:** probe `ACR`, `wprCarveout*`, and `boot-args` sizes, flags and per-engine offsets, and audit every host-built sysmem buffer parsed by secure firmware.
4. **FSP MCTP / NVDM framing parser (immutable ROM):** test tail-length ±N perturbations, non-EOM fragments, and mismatched task/tag/SEID fields.
5. **Boot-input TOCTOU race:** determine whether the host can modify plaintext FMC header fields or swap GSP-RM pages between hash verification and consumption (requires timing the firmware reads first).
6. **Cross-version ABI and FMC differential (`570.144` vs `595.91.07` vs `620.73.04`):** `570.144` ships a `0x25800` B FMC vs `595.91.07`'s `0x37000` B FMC, while `615+` / `620.73.04` expands `GSP_FMC_BOOT_PARAMS` to 256 B and restructures engine ucode into `NVUCODES`. Re-checking `595.91.07` parser bounds against the [620.73.04 baseline](../patches/README.md) (and `570.144`) remains open.
7. **Adjacent hardware leads outside cold-boot ABI:** dual-card P2P peer **writes** into peer BAR0 during the ~1.3 s post-FLR open-PLM window (peer reads are closed; peer writes remain untested), and GC6 runtime-PM re-initialization / register-bank persistence.
