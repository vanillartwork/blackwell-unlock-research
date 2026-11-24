[English](README.md) | [中文](README.cn.md)

# Boot ABI trace patches

This directory holds minimal logging patches for pinned revisions of `NVIDIA/open-gpu-kernel-modules`.

1. Keep upstream source files, firmware and decrypted data out of the repository.
2. Log host-supplied boot structures and status with `NV_PRINTF` or `printk`. Use the [native Linux preflight](../scripts/check-env.sh) before collecting a trace.
3. State the target tag, full commit and verification performed. Recheck the layout when changing tags.

---

## Trace locations

The [GB20x HAL bindings](../docs/abi-fields.md) select the following implementations. Source paths are relative to `src/nvidia/src/kernel/gpu/`.

| Observation | Function | Upstream file | Coverage |
|---|---|---|---|
| FMC arguments and COT | `kgspSetupGspFmcArgs_GH100`, `kfspSendBootCommands_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c`, `fsp/arch/hopper/kern_fsp_gh100.c` | [620.73.04 trace patch, RFC](620.73.04-01-boot-abi-trace.patch) |
| WPR metadata | `kgspPopulateWprMeta_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c` | Same patch |
| Suspend/resume | `KGSP_BOOT_MODE_SR_RESUME` branch of `kgspSetupGspFmcArgs_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c`, `gsp/kernel_gsp.c` | Logs `bootMode` and descriptor length. SR metadata contents are outside current coverage. |

### Verification of the 620.73.04 patch

| Check | Result |
|---|---|
| `git apply --check`, apply and `-R` at `a4b95f6a2db0` | Passed. Reversing the patch restored a clean tree. |
| `make -C src/nvidia`, GCC 15.2.0 on Ubuntu 26.04 | Passed without warnings. `nv-kernel.o` contains every `BWLAB` string. |
| Userspace stub → `collect-report.sh --sanitize` → `abi-layout.py --from-log` | Round trip passed. |
| Link `.ko`, load module, boot on hardware | Untested |

The patch adds log lines and accesses no registers. Release builds send `LEVEL_ERROR` and higher to `dmesg`, so traces use that level. A row offset keeps repeated data rows distinct.

> The instrumented functions are in the Hopper files. `kernel_gsp_gb202.c` handles other tasks, Blackwell does not use the Booter path in `kernel_gsp_booter.c`, and `kernel_gsp_fwsec.c` contains only a license header at this tag.

### Recorded upstream revisions

| Tag | Commit | `GSP_FMC_BOOT_PARAMS` | Notes |
|---|---|---|---|
| `620.73.04` | `a4b95f6a2db0bba10ccaccef79190a3c18dcf14a` | 256 B, with `magic` | Trace patch target reviewed on 2026-11-23 |
| `615.71.10` | `6f3be8c9c3fb4ed6ffc607a0d32d31ce4db7fba5` | 256 B, with `magic` | Driver in the community GB206 sample |
| `610.57.04` | `e4a5faa2567f28c8eabe0ebb6422b6d0abcf37eb` | 88 B, no `magic` | Last recorded pre-magic layout |
| `570.124.04` | `129479b1b7345f0055016ab3aa42ae78a4ca2048` | 80 B, no `magic` | `kgspCalculateFbLayout_GH100` populates `GspFwWprMeta` |

The layout changed between 610 and 615. Log fields by name or decode the dump using its exact tag with `scripts/abi-layout.py --decode`.

### Log format

- Record lengths, offsets, flags and status. For `hash384`, `publicKey` and `signature`, record lengths only.
- Mask physical addresses to their low 12 bits in a private copy. Leave the structure submitted to firmware intact.
- Each trace message contains `BWLAB:`. A complete dump has `BWLAB: begin <struct> tag=<tag> size=<bytes>`, offset-labelled 16-byte rows and `BWLAB: end <struct>`. The decoder flags missing or out-of-order rows.

---

## Apply or reverse the patch

Follow the [Linux trace procedure](../README.md#4-route-1-on-native-linux) to build, reload, collect and restore the installed module. To reverse only the source patch, run:

```bash
cd "$HOME/ogkm-620.73.04"
git apply -R /path/to/blackwell-abi-lab/patches/620.73.04-01-boot-abi-trace.patch
make modules -j"$(nproc)"
```

Sanitize captures with `scripts/collect-report.sh` and identify the applicable [invariant](../docs/abi-invariants.md) in the report.
