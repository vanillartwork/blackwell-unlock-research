[English](template.md) | [中文](template.cn.md)

# Blackwell 真机观测报告

> 提交前请移除序列号、磁盘和分区清单、无关 PCI 设备、PnP 实例标识、VBIOS 镜像和固件原始字节。

## 1. 硬件与软件基线

| 字段 | 观测值（未知请填 `unknown`） |
|---|---|
| 采集日期（UTC） | `YYYY-MM-DD` |
| GPU 型号与 PCI ID | 如 `RTX 5070 Ti Laptop (10de:2f18, SUBSYS 1558:6586)` |
| `PMC_BOOT_0` / 精确 Die | 如 `0x1b5000a1`（`GB205`） |
| VBIOS 版本 | 如 `98.05.31.00.44` |
| 操作系统与内核 | 如 `Ubuntu 26.04 / Linux 6.12.x`（注明裸机 / 非 WSL） |
| 驱动版本与类型 | 如 `open-gpu-kernel-modules 615.71.10` 或 `Windows 621.01` |
| 显示拓扑 | `iGPU 主显 + dGPU 无头` / `双独显` / `单卡直连`（注明控制台由哪张卡、哪个驱动承担） |
| Secure Boot / Lockdown | `OFF` / `ON` |
| 启动方式 | `断电冷启动` / `热重启` / `驱动重载` / `挂起恢复` / `复位后（SBR / FLR）`（复位后请标 `POST_RESET_SAMPLE`） |

## 2. 观测目标与路线编号

- 研究方向：路线 1（Host → FMC 引导 ABI）或路线 2（软件算子与精度）。提交背景数据时可直接填写「运行时 RPC」「VBIOS 元数据」或「跨卡基线」。
- 对应不变量编号（如适用，见 [docs/abi-invariants.md](../docs/abi-invariants.cn.md)）：`INV-xxx`

## 3. 只读寄存器与拓扑基线（如已采）

| 寄存器 / 指标 | 值 |
|---|---|
| `FEAT_OVR_PLM (0x823804)` | |
| `FEAT_READOUT_1 (0x823818)` | |
| `FUSE_SS_FMLA32 (0x8207d8)` | |
| `FUSE_FEAT_OVR_DIS (0x8203f0)` | |
| `SM_ISSUE_RATE_MODIFIER (0x504204)` | |
| `SEC2_PLM (0x8403c4)` | |
| `WPR2_LO / WPR2_HI (0x1fa824 / 0x1fa828)`（填原始字，不要换算） | |
| `WPR2_LO / WPR2_HI (0x88a824 / 0x88a828)`（Blackwell 驱动自身读取的地址） | |
| `FB_SIZE_MIB (0x1183a4)` | |
| BAR1 大小（`verify-baseline.py` 输出） | |
| `GR_TOPO_409604 (0x409604)` / 活跃 SM 数 | |

## 4. 关键观测输出（脱敏摘录）

- 粘贴 `scripts/collect-report.sh` 生成的 `dmesg` GSP/FMC 关键行、邮箱返回码或 `software/clamp_eval.py` 摘要表。
- 若属于路线 1 观测，请明确记录：冷启动 vs 复位/恢复状态、输入参数差异、内核拦截点或 FMC 返回码、是否发生挂起/超时。

- 别处捕获的日志（netconsole / 串口）请先经 `bash scripts/collect-report.sh --sanitize < raw.log > clean.log` 过滤，再人工复查。

## 5. 结论与复现命令

- 用一句话写出实测结论。仍需验证的解释请单独标明。
- 附上完整复现命令与对应补丁版本号。
