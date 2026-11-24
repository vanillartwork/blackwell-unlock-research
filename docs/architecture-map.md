[English](architecture-map.md) | [中文](architecture-map.cn.md)

# Blackwell boot stages and observable interfaces

This map covers consumer Blackwell (SM 12.0, GB202 through GB207 as listed below). Use it to choose an interface for a measurement and identify the stage that produced it.

---

## 1. Boot stages and privilege levels

GB20x uses FSP and immutable on-chip boot ROM to start FMC (First Mutable Code). Earlier Turing, Ampere and Ada devices use a SEC2 Booter stage.

```text
SPI VBIOS / V6 descriptors → FSP (immutable ROM, PL3)
                              │  Host sends NVDM_PAYLOAD_COT over MCTP/NVDM
                              ▼
                        GSP BootROM (PL3)
                              │  gspBootArgsSysmemOffset points to GSP_FMC_BOOT_PARAMS
                              │  → GspFwWprMeta (cold boot) or GspFwSRMeta (resume)
                              ▼
                        FMC (host-supplied, encrypted, signed, PL3)
                              │  Establishes WPR2 and configures the privilege mask
                              ▼
                        GSP-RM (host-supplied signed plaintext, PL2)
                              │  Runtime RPC queue
                              ▼
                        Host kernel driver / CUDA (PL0 from the GPU's perspective)
```

### What the captures establish

1. Four consumer samples (GB202, GB203M, GB205 and GB206) share `FUSE_SS_FMLA32 = 3`, `FUSE_SS_IMLA1/2/4 = 3` and `FEAT_READOUT_1 = 0x00c36180`. The community GB202 RTX PRO 6000 sample reports `FEAT_READOUT_1 = 0` and 518 TFLOP/s for BF16→FP32.
2. `FUSE_FEAT_OVR_DIS (0x8203f0)` and `FUSE_EN_SW_OVERRIDE (0x820040)` both read zero. `FEAT_OVR_PLM (0x823804) = 0xfffffb8f` encodes a read mask of `0xf` and a write mask of `0x8` (PL3 only).
3. The nine 3-bit `FEAT_READOUT_1` selectors start at bits `0, 3, 7, 10, 13, 16, 19, 22, 28`. Bit 6 and bits 27:25 are unused. The captured word decodes to `0 0 3 0 3 3 0 3 0`. On the measured GB205, `fmla32 = 3` accompanies about half-rate FP32 accumulation. The Ada 1/8 rate does not describe that measurement. FP16 accumulation and FP32 SIMT are separate controls.
4. The open driver's Blackwell binding for `kgspGetBinArchiveBooterLoadUcode` selects an empty fallback. GB205 and GB206 both read `SEC2_PLM (0x8403c4) = 0x00000b0f` (readable, no write permission).

---

## 2. Read-only interfaces

| Interface | Address or entry point | Requirements | Measurements |
|---|---|---|---|
| PCIe, NVML, `nvidia-smi` | sysfs / NVML | Standard driver access | PCI ID, VBIOS, BAR1, VRAM, power limits, clocks and throttle reasons. Combine clocks with measured throughput to compute `FLOP/clk/SM`. |
| BAR0 registers | `0x000000`, `0x1fa824..28` (driver: `0x88a824..28`), `0x823800..2c`, `0x8207d4..ec`, `0x504204`, `0x8403c4` | Privileged read access; Linux `IO_STRICT_DEVMEM` may restrict access. | Chip ID, WPR2 bounds, privilege masks, fuse selectors and SS0/SS1 shadows |
| BAR0 topology | `0x409604`, `0x820c1c`, `0x820c38+4g`, `0x820a00..b1c`, `0x822a68/78`, `0x822900+4f` | Same as above | GPC/TPC/SM counts, PES/OFA/LTC/L2 configuration and `GA100_SM_OVR_RGN` observations |
| `NV_PROM` window | `BAR0+0x300000..0x3ffffc` (1 MiB) | Same as above | PCI option ROM chain, BIT directory, token `0x70 v2` ucode table and V6 descriptors within the window |
| Host → FSP/FMC | `kernel_gsp_gh100.c`, `kern_fsp_gh100.c` | Native Linux and trace patch | COT summary, host boot arguments, mailbox status and cold-boot/resume differences. [HAL bindings](abi-fields.md) |
| Host ↔ GSP-RM | Driver RPC queues and `dmesg` | Native Linux | Runtime messages, clocks, power management, context switches and suspend/resume size and timing |

---

## 3. Two research routes and their supporting evidence

| Route | Observation point | Question |
|---|---|---|
| **Route 1 · Boot ABI** | Host driver → FMC (PL3) | Do the submitted structure types, lengths and fields match the source on cold boot, driver reload and resume? Record the boot mode and capture stage so the same GPU can be compared across runs. [Observation checklist](abi-invariants.md) |
| **Route 2 · Software operators** | Userspace CUDA / PyTorch | For a given matrix shape, which K chunk size improves throughput within the workload’s relative L2 error budget? Compare against both full-length FP32 and FP16 accumulation. [Operator and measurements](../software/README.md) |

Three sources help interpret the measurements. GSP-RM runtime RPC follows FMC initialization and can explain clocks, power and resume behavior. The [VBIOS survey](vbios-v6-descriptor.md) checks plaintext layouts and object boundaries across twelve ROMs. The [cross-card matrix](test-matrix.md) records register readbacks by GPU and driver. Distinguish file layout, boot-time observations and runtime performance when interpreting a result.

---

## 4. Evidence sources

- Local GB205 (`10de:2f18`) register captures and benchmarks are archived in the [hardware report](../reports/gb205-2f18/README.md). The [V6 study](vbios-v6-descriptor.md) separates PROM-window observations from full-file measurements.
- The 620.73.04 COT sizes (97 B public key, 96 B signature) support the P-384 interpretation. [Independent source checks](references.md)
- The [host layouts and HAL bindings](abi-fields.md) derive from static analysis of `NVIDIA/open-gpu-kernel-modules`. Hardware traces remain pending.
- Community comparisons draw on `physicsdolphin/nv-uefi-unlocker`, `steviecurrie/gpu-unlock-gb206`, `shuttie/bf16-unlock-blackwell-research`, `phiraml/gpu-unlock-blackwell` and `NVIDIA/open-gpu-kernel-modules`.
