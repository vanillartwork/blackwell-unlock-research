[English](template.md) | [中文](template.cn.md)

# Blackwell hardware observation report

> Remove serial numbers, disk and partition inventories, unrelated PCI devices, PnP instance IDs, raw firmware bytes and VBIOS images before submission.

## 1. Hardware and software

| Field | Observation (`unknown` if unknown) |
|---|---|
| Capture date (UTC) | `YYYY-MM-DD` |
| GPU and PCI ID | e.g. `RTX 5070 Ti Laptop (10de:2f18, SUBSYS 1558:6586)` |
| `PMC_BOOT_0` and die | e.g. `0x1b5000a1` (`GB205`) |
| VBIOS | e.g. `98.05.31.00.44` |
| OS and kernel | e.g. `Ubuntu 26.04 / Linux 6.12.x`. Indicate bare metal or virtualization. |
| Driver version and type | e.g. `open-gpu-kernel-modules 615.71.10` or `Windows 621.01` |
| Display topology | iGPU with headless dGPU, two discrete GPUs or one directly connected GPU. Name the adapter and driver serving the console. |
| Secure Boot / Lockdown | `OFF` / `ON` |
| Boot state | Power-off cold boot, warm reboot, driver reload, suspend/resume or post-reset (SBR/FLR). Label post-reset captures `POST_RESET_SAMPLE`. |

## 2. Observation and research route

- Research route: Route 1 (host-to-FMC boot ABI) or Route 2 (software operators and accuracy). For supporting evidence, specify runtime RPC, VBIOS metadata or a cross-card baseline.
- Applicable [invariant ID](../docs/abi-invariants.md): `INV-xxx`.

## 3. Register and topology readbacks (if collected)

| Register or metric | Value |
|---|---|
| `FEAT_OVR_PLM (0x823804)` | |
| `FEAT_READOUT_1 (0x823818)` | |
| `FUSE_SS_FMLA32 (0x8207d8)` | |
| `FUSE_FEAT_OVR_DIS (0x8203f0)` | |
| `SM_ISSUE_RATE_MODIFIER (0x504204)` | |
| `SEC2_PLM (0x8403c4)` | |
| `WPR2_LO / WPR2_HI (0x1fa824 / 0x1fa828)` (raw words) | |
| `WPR2_LO / WPR2_HI (0x88a824 / 0x88a828)` (addresses used by the Blackwell driver) | |
| `FB_SIZE_MIB (0x1183a4)` | |
| BAR1 size from `verify-baseline.py` | |
| `GR_TOPO_409604 (0x409604)` / active SM count | |

## 4. Sanitized evidence

- Include relevant GSP/FMC lines from `scripts/collect-report.sh`, mailbox return codes or the `software/clamp_eval.py` summary.
- For Route 1, record boot state, input differences, where the driver stopped or which status FMC returned, and any hang or timeout.

- For serial or netconsole captures, run `bash scripts/collect-report.sh --sanitize < raw.log > clean.log` and review the result before sharing.

## 5. Conclusion and reproduction

- State the measured finding in one sentence. Label any interpretation that still needs verification.
- Include the exact commands and patch version.
