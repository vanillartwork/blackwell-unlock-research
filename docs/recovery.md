[English](recovery.md) | [中文](recovery.cn.md)

# Boot failures, recovery and sample classification

After an FMC timeout, a hung `ioctl` or an unexpected return code, save the logs. Power the machine off fully before the next Route 1 observation. See the [boot ABI checklist](abi-invariants.md).

The project scripts inspect recovery options without issuing a reset.

---

## 1. Interpreting logs

The strings below come from `open-gpu-kernel-modules` 620.73.04. The mailbox interpretation also matches `nova-core`.

| Log or symptom | Source | Interpretation | Record |
|---|---|---|---|
| `unexpected WPR2 already up, cannot proceed with booting GSP` | `gsp/kernel_gsp.c` | The driver found WPR2 active before GSP boot and returned `NV_ERR_INVALID_STATE`. An earlier session may have left it active. | Both WPR2 address pairs and the previous unload log |
| `FSP response reported error. Task ID … Command type … Error code …` | `fsp/arch/hopper/kern_fsp_gh100.c` | FSP rejected the message. Command `0x14` is COT and `0x13` is PRC. Only some error codes have public definitions. | All three numeric values |
| `FSP boot cmds failed. RM cannot boot.` | Same FSP file | COT delivery or the reply failed. Earlier lines describe the COT. | The sanitized sequence |
| `Fatal GSP-FMC Error: version=… partition=… error code=… additional info=…` | `gpu/arch/hopper/gpu_gsp_fmc_error_reporting_gh100.c` | FMC ran and reported a fatal error. | All four values |
| `Fatal GSP-FMC Error: SK error code =… phase …` | Same error-reporting file | Alternate FMC error format | Both values |
| Nonzero GSP `MAILBOX0` | `_kgspLockdownReleasedOrFmcError`, `nova-core` `gsp/hal/gh100.rs` | If `MAILBOX1:MAILBOX0` still equals the boot-argument address, FMC has yet to consume it. Otherwise a nonzero status indicates an error. Upstream describes an 8-bit error code with zero for success. | Both mailbox readings, redacted before publication |
| `Possible bad register read … regvalue: 0xbadf41xx` | `gpu/arch/hopper/kern_gpu_gh100.c` | This readback can appear while the PRIV target mask restricts GSP access to FSP (`nova-core`, `falcon/gsp.rs`). | Address and returned word |
| `… heartbeat timed out`, Xid 109 / 154 | `gsp/kernel_gsp.c` | Investigate the runtime workload and surrounding log before attributing a failure to the boot ABI. | Workload and complete Xid messages. [Upstream report #1080](https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1080) |

### 1.1 Observed FSP/FMC error codes and failure tiers (third-party GB202, `595.91.07`)

The table below summarizes third-party hardware measurements on 2× RTX 5090 (GB202 `10de:2b85`, Linux driver `595.91.07`) reported in [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049) (CC BY 4.0; not independently reproduced here; behavior on `615+` / `620.73.04` may differ).

| Failure tier | Observed error codes / symptoms | Recovery cost on `595.91.07` | Observed mechanism |
|---|---|---|---|
| **1. Pre-auth refusal** | **FSP COT (`cmd 0x14`)**: `0xd9` (`frtsVidmemSize != 0x100000`, must be exact 1 MiB), `0xd6` (`frtsVidmemOffset` not 128 KiB-aligned).<br>**FMC `WprMeta`**: `0x827` (`magic != 0xdc3aae21371a60b3`), `0x828` (`revision != 1`), `0x82e` (`sizeOfBootloader` not 4 KiB-aligned), `0x836` (`sizeOfBootloader` hits 256 MiB limit), `0x83e` (`sysmemAddrOfRadix3Elf` moved / pinned), `0x841` (`bootloaderManifestOffset != 0` in range), `0x885` (out-of-range / alignment refusal), `0x83d` (FRTS withheld via `RmDisableCotCmd=0x4`).<br>**`.fwsignature_gb20x`**: `0xa97` (aligned pointer, wrong first-word marker), `0xaaa` (`+0x10` record size `> 0x200`; valid window `[0x180, 0x200]`, fixed `0x200` stride, no unsigned wrap), `0xa0f` (`0x87c` table mode outside `{1, 2}`).<br>**Image/sig auth (`0xaff`)**: `info=0x1` (slot 0/1 mismatch; slots 2/3 unverified), `info=0x2` (plaintext table `0x814–0x81c` mismatch), `info=0x7` (record size `< 0x180`), `info=0xb` (GSP-RM or `GspRmBoot` auth failure). | **Driver reload** (WPR2 not left up; cold-boot classification still requires a full power cycle) | Rejected before WPR2 is armed or finalized |
| **2. Post-WPR2 auth failure** | **FSP COT (`cmd 0x14`)**: `0x377` (COT hash auth failure), `0x1db` (self-signed or unanchored public key, rejected before hash/sig check), `0x33` (FMC container header or ciphertext byte tampered). | **Reboot required** (subsequent retries fail with `unexpected WPR2 already up`) | FSP arms WPR2 *before* authenticating the COT payload |
| **3. Parameter-induced hard wedge** | `gspFwHeapSize` below GSP-RM minimum (e.g. `16 MiB` → `NV_ERR_TIMEOUT 0x65`); `nonWprHeapSize = 4 GiB`. | **Reboot / full power cycle** | WPR2 left armed |
| **4. Silent hang (no local log)** | Corrupting `SRMeta.magic` or `SRMeta.revision != 2` during `SR_RESUME` (`s2idle`). | **Reboot / full power cycle**; requires netconsole/serial + phase markers | Local `journald` cannot flush before hang |

**Host-driver pitfalls recorded on `595.91.07` (unrelated to firmware security or the tensor clamp; verify before running Stage 2 probes on other tags):**
- *Host-driver error-recovery lifetime bug:* on `595.91.07`, repeated COT failure triggers a kernel oops in `hrtimer_active` (NULL `hrtimer.base`) because `kfspCleanupBootState_IMPL` frees the engine-lifetime `pPollEvent` prematurely, leaving the retry path to schedule and tear down a freed event (use-after-free in error cleanup; moving the NULL-guarded destroy to `kfspStateDestroy_IMPL` avoids it).
- *Early-boot hook ordering constraint (not a stock driver bug):* calling BAR1 mapping helpers from a custom hook before `gpuStateLoad` (`kbusInitBar1` runs in `kbusStatePostLoad`, *after* `kgspInitRm`) dereferences an uninitialized NULL PTE array. During or after boot, host framebuffer reads go through the BAR0 PRAMIN window (`0x700000`), where protected WPR reads return the repeating sentinel `0xbad0ac00`.

---

## 2. Recovery scope

| Method | Scope | Consequence | Cold-boot evidence? |
|---|---|---|---|
| Full power removal | Whole system | Shut down and follow the hardware vendor's procedure to remove power, especially on laptops with an internal battery. | Yes, if GPU power was removed |
| Warm reboot | Whole system | The GPU may remain powered and retain FSP or WPR2 state. | Record as warm reboot |
| Kernel `reset` (`flr`, `bus`, etc. in `reset_method`) | Function or bus | Other functions, including GPU audio, may prevent a bus reset. | No |
| Kernel `reset_subordinate` on a bridge (available since 2024) | All devices below the bridge | Also affects unrelated downstream devices. | No |
| Third-party secondary bus reset (SBR) script | All devices below the bridge | Direct bridge writes and additional side effects, described below. | No |

`scripts/check-env.sh` lists each NVIDIA card's reset methods, upstream bridge and downstream neighbors. It warns when a bridge also serves another device.

---

## 3. Community SBR script

The [community recovery script](https://github.com/gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery) at `d744382a58e9` (July 2026) uses driver removal, bridge and function resets, and a bus rescan. Its author provides a successful recovery log. This project has not reproduced that result.

The source review identified the following limits.

1. The repository had no license at the reviewed commit.
2. It stops the desktop, Docker and containerd, and force-terminates processes using NVIDIA devices. Unsaved work can be lost.
3. It processes all NVIDIA GPUs and does not check which other devices share the bridge.
4. Direct bridge writes bypass kernel-managed PCI configuration save/restore. Several errors are ignored.
5. It depends on `/root/native_resize_bar.sh` and changes BAR sizing.
6. Repeating a boot observation in the same powered session would invalidate its cold-boot classification.

For recovery after evidence collection, inspect the bridge scope first. Prefer a kernel-managed recovery method where appropriate.

---

## 4. Classifying the next capture

- Label a capture after SBR, FLR or warm reboot `POST_RESET_SAMPLE`. Keep it out of cold-boot baselines and WPR2 cold-boot comparisons.
- A cleared `WPR2_HI` establishes that the register reads zero. FSP, FMC and fuse-shadow state require separate evidence.
- Resume Route 1 observations only after a full power cycle.
- Record the original failure, recovery method and whether power was removed afterward.
