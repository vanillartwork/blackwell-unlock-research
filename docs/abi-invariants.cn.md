[English](abi-invariants.md) | [中文](abi-invariants.cn.md)

# 主机到 FMC 的引导 ABI 观测清单

用下列编号标记 GB20x 的只读快照、驱动日志与寄存器读回值。每项列出静态基线及需要保留的证据。

> **安全红线**：
> 1. 在原生 Linux 物理机上确认独立控制台、SSH 和 netconsole 或串口日志可用。
> 2. 任何单次观测触发 FMC 超时、`ioctl` 挂起或异常返回码后，**严禁同会话连续重试**——必须保存日志、完全断电冷启动后再继续下一项。
> 3. 故障日志的含义、恢复手段与恢复后样本的取舍见 [recovery.md](recovery.cn.md)；复位不能替代断电冷启动。
> 4. 优先通过只读日志补丁采集正常冷启 / 热复位 / 挂起恢复下的基线结构体快照，再逐项登记不变量状态。

> **先分清输入与输出**：按公开源码（595–620），GB20x 主机**不写** `gspFwWprStart` / `gspFwWprEnd` / `fbSize` / `bootCount` / `verified` 以及各 `*Offset` 布局字段，这些由固件在建立 WPR2 时填写。字段布局、主机写入清单与跨版本偏移见 [abi-fields.md](abi-fields.cn.md)；快照由 [620.73.04 日志补丁](../patches/README.cn.md)输出，用 `scripts/abi-layout.py --from-log` 解码并与静态基线比对。

---

## 1. 状态标志位不变量（State & Flag Invariants）

关注 `GspFwWprMeta` 及外层参数中与固件阶段、校验结果、重入次数相关的状态字段：

| 编号 | 关注字段 | 观测路径 | 观测目标与预期不变量 | 需记录的日志证据 |
|---|---|---|---|---|
| `INV-S01` | `verified` | 冷启动 | 主机先清零缓冲区，随后不写该字段，提交前应为 `0`。上游定义的两个值为 `0` 和 `0xa0a0a0a0a0a0a0a0`。 | 快照阶段、邮箱状态、WPR2 边界与日志时序 |
| `INV-S02` | `bootCount` | 冷启动、驱动重载、S3/s2idle 恢复 | 主机不写计数。`bootMode = SR_RESUME` 选择 `GspFwSRMeta`（revision `2`）。 | `bootMode`、描述符类型、`sizeOfSuspendResumeData`、WPR2 和 GSP 邮箱状态 |
| `INV-S03` | 引导模式和 COT 标志 | 冷启动 | 记录当前配置及观察到的主机复位超时。`RmDisableCotCmd` 的位段 `1:0`、`3:2`、`5:4` 分别对应 FRTS 系统内存、FRTS 显存和 GSP-FMC，默认值为 `0`。 | 模块参数、报错原文及不可中断等待状态 |
| `INV-S04` | `FEAT_SS0/SS1` 预驱动影子态 | Cold Boot 间 / 同 Boot 内 | 观测不同冷启动之间 `0x82381c/0x823820` 的非零影子值漂移，确认驱动初始化与合法挂起恢复后 `enable bit`（nibble bit3）及 `FEAT_OVR_PLM` 是否始终锁回 `0xfffffb8f`。 | 跨引导只读读回（GB205 已实测：`0x56027063/0x00032004` → `0x76027063/0x00030004`） |

---

## 2. 内存边界与对齐不变量（Bounds & Alignment Invariants）

关注 `WPR2`、FRTS、GSP-RM 镜像、Radix3 页表与堆（heap）区的物理基址、长度与页对齐字段：

| 编号 | 关注字段 | 观测维度 | 观测目标与预期不变量 | 需记录的日志证据 |
|---|---|---|---|---|
| `INV-B01` | `gspFwWprStart` / `gspFwWprEnd` / `fbSize` | 4 KiB / 64 KiB / 1 MiB 对齐 | GB20x 主机将这些字段留零，固件填写布局。根据 LO/HI 读回值核对实际对齐。 | 主机快照、WPR2 原始字和状态 |
| `INV-B02` | 镜像、FRTS 与堆长度 | 正常采集 | 按驱动与卡记录 `sizeOfRadix3Elf`、`sizeOfBootloader`、`frtsSize`、`nonWprHeapSize`、`gspFwHeapSize`。 | 长度、返回码、WPR2 与 MMU 锁定状态 |
| `INV-B03` | 起点、终点、长度 | WPR2 范围 | 固件计算区间。启用时须有 `LO < HI` 且范围不超过显存；`HI = 0` 表示未建立。 | 快照、显存大小及驱动或 FMC 错误码 |
| `INV-B04` | `gspFwHeapSize`（堆大小档位） | 合法多档位（如已知 heap lever） | 观测合法变更堆大小时，WPR2 保护区边界与非保护区偏移的实际移动规律，确认保护区边界是否严格受控。<br>**静态基线**：上游注释称该值只是给 FMC 的最小请求，实际堆还会吸收对齐填充，故须与 WPR2 实际跨度并列登记。 | 各档位下 `WPR2_LO/HI`、`FB_SIZE_MIB`、`dmesg` 内存布局日志 |

---

## 3. 跨结构一致性不变量（Cross-Field Consistency）

关注外层 `GSP_FMC_BOOT_PARAMS` 与内层 `GspFwWprMeta` 之间存在重叠或派生关系的字段：

| 编号 | 重叠/关联字段组 | 观测目标与预期不变量 | 需记录的日志证据 |
|---|---|---|---|
| `INV-C01` | 外层与内层长度 | 从 615 起，外层 `size` 和 `gspRmDescSize` 均为 `256`。内层的 `sizeOfRadix3Elf`、`sizeOfBootloader` 描述映像长度。`gspFwRsvdStart` 是固件填写的显存偏移。 | 驱动标签、快照与状态 |
| `INV-C02` | 签名元数据与 COT | `sysmemAddrOfSignature`、`sizeOfSignature` 属于 GSP-RM。GB20x 的 COT v2 载荷为 860 B，其中公钥 97 B、签名 96 B，剩余槽位补零。 | 只记录长度与返回码 |
| `INV-C03` | 标记与版本 | `GspFwWprMeta` 的 magic 为 `0xdc3aae21371a60b3`，revision 为 `1`。`GSP_FMC_BOOT_PARAMS` 从 615 起使用 magic `"FMC "`。`GspFwSRMeta` 的 revision 为 `2`。 | 驱动标签、结构体名称与状态 |
| `INV-C04` | Radix3 页表和引导程序地址 | 核对 `sysmemAddrOfRadix3Elf`、`sysmemAddrOfBootloader` 的 4 KiB 对齐及 `target` 的 `0..2` 取值。 | 对齐、地址空间类型、内核/IOMMU 日志及 FMC 状态 |

---

## 4. 跨驱动版本与多核心对照（Cross-Driver & Cross-Die Matrix）

这组比较使用只读采集和固定版本的上游源码。

| 编号 | 对照维度 | 观测目标 | 已有实测基线 |
|---|---|---|---|
| `INV-X01` | 同卡跨驱动版本（`open-gpu-kernel-modules` / 官方驱动） | 对比同一张卡在不同驱动版本下的 `WPR2_LO/HI` 位置与跨度、`SEC2_MBOX0/1`、`GSP-RM` 启动参数变化。 | GB205（`10de:2f18`，Windows `LEGACY` 模式）已测：`616.64` `WPR2=0x02fa7e00..0x02fb0400`（跨度 8.375 MiB）→ `621.01` `WPR2=0x02fb8800..0x02fc1000`（跨度 8.5 MiB），`SEC2_MBOX0` `0xbf7b2000` → `0xbf7d4000`；除 `SS0/SS1` 影子态外，两次都采到的熔丝 / PLM / PROM BIT 结构全部相同。两次采集同时跨了驱动与冷启动，无法单独归因于驱动。 |
| `INV-X02` | 跨核心横向一致性（GB202 / GB203 / GB205 / GB206 / GB207） | 验证全系消费卡在 `FEAT_OVR_PLM`、`FEAT_READOUT_1`、`FUSE_SS_*`、`SM_ISSUE_RATE_MODIFIER`、`SEC2_PLM` 与 `GA100_SM_OVR_RGN` 解码孤岛上是否全系同形。 | GB202 / GB203M / GB205 / GB206 已核验 4/4 同形；GB203 桌面与 GB207 待补（见 [test-matrix.md](test-matrix.cn.md)）。 |
| `INV-X03` | 跨芯片与 OEM 的 VBIOS | 核对已用槽位、目标、`+28`、`+2C` 至 `+33` 的可打印标签，以及长度余量。 | 五种芯片、十二份 ROM 均有 35 个槽位、六个已用 APP、相同目标、`0x01012801`、`OOBHUB` / `FSP_RUN` 和相同的 `0xA00` 余量分布。[V6 研究 §6.11](vbios-v6-descriptor.cn.md#611-覆盖五种-gb20x-芯片的十二份-rom) |
| `INV-X04` | 专业卡（RTX PRO Blackwell）vs 消费卡差分 | 对比同 die 专业卡与消费卡在熔丝、`FEAT_READOUT_1`、V6 标志位和 WPR2 布局上的精确差异。 | GB202 PRO 6000 已确认 `FEAT_READOUT_1=0`、算术速率熔丝全 0，但 `FEAT_OVR_PLM` 仍为 `0xfffffb8f`；V6 元数据已比对（PRO 6000 `98.02.52`）：对象编号、槽位、标志位与常量同消费卡，**没有** PRO 专属标记，载荷差异因版本不同无法归因（[vbios-v6-descriptor.md §6.11.6](vbios-v6-descriptor.cn.md)）。WPR2 布局待补。 |
| `INV-X05` | 主机侧结构体布局随驱动 tag 的漂移 | 每出新 tag 时重读公开头文件，登记 `GSP_FMC_BOOT_PARAMS` / `GspFwWprMeta` / `NVDM_PAYLOAD_COT` 的布局变化（纯静态，任何人可做）。 | 已登记 `570.124.04`、`610.57.04`、`615.71.10`、`620.73.04`：`GSP_FMC_BOOT_PARAMS` 在 610 → 615 换布局（88 B → 256 B），其余见 [abi-fields.md §2](abi-fields.cn.md#2-跨版本漂移)。 |

---

## 5. 标准记录格式

每完成一项观测，请按 [reports/template.md](../reports/template.cn.md) 填写并标明不变量编号（如 `INV-X01`、`INV-B01`）与结果标签：

- `PASS_FAIL_CLOSED`：固件或驱动按预期安全拒绝，无异常副作用。
- `OBSERVED_BASELINE`：只读基线采集完成（如冷启/重载/跨驱动差分）。
- `TIMEOUT_OR_HANG`：触发 `ioctl` 超时或设备无响应（需附完整 `netconsole`/`dmesg` 尾部并记录冷启恢复情况）。
- `POST_RESET_SAMPLE`：在断电冷启动之外的恢复方式（SBR / FLR / 热重启）之后采集，只作旁证，不计入冷启动基线（见 [recovery.md](recovery.cn.md)）。
- `ANOMALY_NEEDS_REPRO`：出现与预期不符的状态码或行为（必须在第二次独立冷启动复现后再定性）。
