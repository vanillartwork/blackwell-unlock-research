[English](architecture-map.md) | [中文](architecture-map.cn.md)

# Blackwell 启动阶段与可观测接口

本文按启动阶段整理 GB202、GB203、GB205、GB206 和 GB207 的只读接口，说明每种采集能回答哪些问题。

---

## 1. 启动链与特权级（Privilege Level）模型

与 Turing / Ampere / Ada 不同，Blackwell 引导链**取消了 SEC2 Booter 阶段**，转而由片上不可变 ROM 直接引导加密且 P-384 签名的 FMC（First Mutable Code）：

```text
[SPI VBIOS / V6 描述符] ──► FSP (不可变 ROM, PL3)
                              │  ◄── Host 经 MCTP/NVDM 发送 NVDM_PAYLOAD_COT
                              ▼
                        GSP BootROM (不可变 ROM, PL3)
                              │  ◄── COT 内的 gspBootArgsSysmemOffset 指向
                              │      GSP_FMC_BOOT_PARAMS → GspFwWprMeta / GspFwSRMeta
                              ▼
                        FMC (Host 供入, 加密 + P-384 签名, PL3)
                              │  ──► 建立 WPR2、配置并锁死 PLM (FEAT_OVR_PLM = 0xfffffb8f)
                              ▼
                        GSP-RM (Host 供入, 签名明文, PL2)
                              │  ◄── 运行时 RPC 命令队列
                              ▼
                        Host Kernel Driver / CUDA (PL0)
```

### 跨卡采集结果

1. **消费卡与专业卡的实测差异**：GB202（5090）、GB203M、GB205（5070 Ti Laptop）、GB206（5060 Ti）四款消费卡的 `FUSE_SS_FMLA32 = 3`、`FUSE_SS_IMLA1/2/4 = 3`、`FEAT_READOUT_1 = 0x00c36180` **逐字节一致**；同为 GB202 die 的专业卡 RTX PRO 6000 则为 `FEAT_READOUT_1 = 0` 且 BF16→FP32 满速（518 TF/s）。
2. **熔丝读回与权限掩码**：`FUSE_FEAT_OVR_DIS (0x8203f0) = 0`、`FUSE_EN_SW_OVERRIDE (0x820040) = 0`，但 `FEAT_OVR_PLM (0x823804) = 0xfffffb8f`（读掩码 `0xf`，写掩码 `0x8`，**仅允许 PL3 写入**）。
3. **非连续 `FEAT_READOUT_1` 解码与速率律变化**：9 个 3-bit 字段位于位移 `0, 3, 7, 10, 13, 16, 19, 22, 28`（bit 6 与 bits 27:25 为空位），原字 `0x00c36180` 对应 `0 0 3 0 3 3 0 3 0`。在 Blackwell 上 `fmla32 = 3` 实测约为 **0.5× 半速**（而非 Ada 枚举定义的 1/8），且 FP16→FP16 累加与 FP32 SIMT 不受该钳制影响。
4. **旧 SEC2 载体消失**：公开内核模块中 `kgspGetBinArchiveBooterLoadUcode` 对 Blackwell 落入空回退桩；硬件上 `SEC2_PLM (0x8403c4) = 0x00000b0f`（全读、零写），GB205 与 GB206 实测完全一致。

---

## 2. 所有可观测接口全景（只读面）

| 观测面 | 地址 / 接口 | 访问条件 | 可获取信息 |
|---|---|---|---|
| **PCIe / NVML / `nvidia-smi`** | sysfs / NVML | 免提权（Linux/Windows） | PCI ID、VBIOS 版本、BAR1/显存、功耗墙、时钟、限速原因、稳态 `FLOP/clk/SM` |
| **BAR0 核心与 FEAT/熔丝面** | `0x000000`、`0x1fa824..28`（Blackwell 驱动自身读 `0x88a824..28`）、`0x823800..2c`、`0x8207d4..ec`、`0x504204`、`0x8403c4` | Linux root（受 `IO_STRICT_DEVMEM` 约束）或 Windows 提权工具 | `PMC_BOOT_0`、WPR2 边界、PLM 状态、速度选择器熔丝、Shadow `SS0/SS1`、`SEC2_PLM` |
| **BAR0 拓扑与 Floorsweep 面** | `0x409604`、`0x820c1c`、`0x820c38+4g`、`0x820a00..b1c`、`0x822a68/78`、`0x822900+4f` | 同上 | GPC/TPC/SM 精确计数、PES/OFA/LTC/L2 切片配置、`GA100_SM_OVR_RGN` 解码孤岛 |
| **BAR0 `NV_PROM` 窗口** | `BAR0+0x300000..0x3ffffc`（1 MiB） | 同上 | PCI Option ROM 链、BIT 目录、token `0x70 v2` 微码表元数据与窗口内 V6 描述符 |
| **Host → FSP / FMC 内核驱动边界** | `open-gpu-kernel-modules`（`kernel_gsp_gh100.c`、`kern_fsp_gh100.c`；GB20x 的 HAL 绑定见 [abi-fields.md](abi-fields.cn.md)） | 原生 Linux 物理机 + 日志补丁 | `NVDM_PAYLOAD_COT`、`GSP_FMC_BOOT_PARAMS`、`GspFwWprMeta` 实际传入值、邮箱状态码、冷启/恢复差分 |
| **Host ↔ GSP-RM 运行时通道** | 内核驱动 RPC 队列 / `dmesg` | 原生 Linux | 运行时控制消息、电源/时钟/上下文切换与 Suspend/Resume 状态大小与时序 |

---

## 3. 两条主线与背景证据

| 路线 | 观测位置 | 要回答的问题 |
|---|---|---|
| **路线 1 · 引导 ABI** | 主机驱动 → FMC（PL3） | 冷启动、驱动重载和挂起恢复时，驱动提交的结构类型、长度与字段是否符合源码？记录引导模式和日志阶段，以便比较同一张卡的结果。[观测清单](abi-invariants.cn.md) |
| **路线 2 · 软件算子** | 用户态 CUDA / PyTorch | 对给定矩阵形状，什么 K 分块大小能在可接受的相对 L2 误差下提高吞吐？分别与全程 FP32 累加和全程 FP16 累加比较。[算子与实测](../software/README.cn.md) |

三类背景证据用于解释上述测量。GSP-RM 的运行时 RPC 发生在 FMC 初始化之后，可以解释功耗、时钟和挂起恢复行为；[VBIOS 普查](vbios-v6-descriptor.cn.md)核对了十二份 ROM 的明文布局和对象边界；[跨卡基线](test-matrix.cn.md)记录不同显卡和驱动的寄存器读回。分析报告时须区分文件中的结构、启动时的快照和运行期的吞吐。

---

## 4. 证据分层与参考来源

- 本仓库的 GB205（`10de:2f18`）寄存器、PROM BIT 元数据、V6 逆向与稳态基准为本机一手实测，归档于 [reports/gb205-2f18/](../reports/gb205-2f18/README.cn.md) 与 [docs/vbios-v6-descriptor.md](vbios-v6-descriptor.cn.md)。
- FMC 的 P-384 签名由 `620.73.04` 的 COT 尺寸字段（公钥 97 B、签名 96 B）支持；与 `nova-core`、VBIOS 编辑器等外部资料的核对记录见 [references.md](references.cn.md)。
- 主机侧 ABI 布局与 HAL 绑定为 `NVIDIA/open-gpu-kernel-modules` 公开源码的静态阅读，登记于 [abi-fields.md](abi-fields.cn.md)，尚待真机快照核对。
- GB202 / GB203M / GB206 / RTX PRO 6000 的跨卡对照数据参考公开社区研究（`physicsdolphin/nv-uefi-unlocker`、`steviecurrie/gpu-unlock-gb206`、`shuttie/bf16-unlock-blackwell-research`、`phiraml/gpu-unlock-blackwell` 与 `NVIDIA/open-gpu-kernel-modules`）。
