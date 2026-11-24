[English](references.md) | [中文](references.cn.md)

# 外部参考与核查记录

核查日期为 2026-11-23。下表列出各资料的用途和许可证；结论以该节注明的版本为准。

| 资料 | 许可证 | 用途 | 处理方式 |
|---|---|---|---|
| [NVIDIA 开源驱动 620.73.04](https://github.com/NVIDIA/open-gpu-kernel-modules/tree/a4b95f6a2db0bba10ccaccef79190a3c18dcf14a) | 见各文件许可证 | Host → FMC 的结构体、HAL、跨版本字段和日志补丁基线 | `cl2080_notification.h` 的 RM 引擎类型范围为 0x00–0x53，不能解释全部 V6 条目 ID |
| [bird/vgpu-unlock-blackwell](https://github.com/bird/vgpu-unlock-blackwell/tree/22d2b112e20981565d543b74745c8c02bf4e8960) | MIT | RTX 5090 vGPU 的第三方观察 | 作者报告 GSP 阶段发生故障；适用范围是该 vGPU 实验，不能由此推定张量吞吐限制的原因 |
| [Linux `nova-core` 与 FSP](https://docs.kernel.org/gpu/nova/core/fsp.html) | GPL-2.0 | 核对路线 1 的字段、编码与时序 | 逐项核对，见第 1 节 |
| [gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery](https://github.com/gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery) | **无** | 故障恢复思路 | 只引用，不集成，见 [recovery.md](recovery.cn.md) |
| [Panchovix/nvidia-vbios-vix-editor](https://github.com/Panchovix/nvidia-vbios-vix-editor) | GPL-3.0 | 核对 VBIOS 清单条目 | 只取格式事实，见第 2 节 |

---

## 1. `nova-core`（torvalds/linux `b6a9fa34ce41`，7.4-rc4）

与 [abi-fields.md](abi-fields.cn.md) 相互独立的第二份实现。它面向 **r570.144** 固件，因此只能直接印证 570 一代的布局；冷启动路径已实现，恢复路径的 `resume` 参数目前恒为假。

| 项目 | `nova-core` | 本仓库 | 结论 |
|---|---|---|---|
| FSP 路径由主机填写的 `GspFwWprMeta` 字段 | `from_sizes` 只写地址、大小与 PMU / VF 数；注释写明偏移由 ACR ucode 计算 | 主机不写 `gspFwWprStart/End`、`fbSize`、`bootCount`、`verified` 与各偏移 | **一致** |
| `GSP_FMC_BOOT_PARAMS`（570） | `r570_144/bindings.rs`：80 B，无 `magic` | `570.124.04`：80 B，无 `magic` | **一致**（字段、顺序与填充） |
| `GspFwWprMeta`（570） | 绑定逐字段相同，`padding[2]` | 相同 | **一致** |
| `NVDM_PAYLOAD_COT` 字段顺序 | `version` … `gsp_boot_args_sysmem_offset`，packed | 860 B | **一致** |
| COT 版本 | GH100 = 1，GB10x / GB20x = 2 | 620.73.04 HAL 字段相同 | **一致** |
| `WPR2` 编码 | `bits 31:4 << 12`，LO 含、**HI 不含**，0 表示未建立 | 编码相同；此前 HI 的含义标为未知 | **补齐** HI 的语义 |
| `WPR2` 地址 | 只在 Turing–Ada 路径读 `0x1fa824/28`；FSP 路径不读 | GB20x 的开放模块读 `0x88a824/28` | 不构成印证，双地址问题仍开放 |
| 邮箱错误判定 | 邮箱等于启动参数地址时继续等待，其他非零值为 FMC 错误 | 开放模块相同 | **一致** |

### 需要更正的说法

- **“FMC 使用 RSA-3K”不适用于 GB20x。** 内核文档 [fsp.rst](https://docs.kernel.org/gpu/nova/core/fsp.html) 写的是 RSA-3K，那是 GH100 的情形（COT v1，公钥与签名各 384 B）。`620.73.04` 的 HAL 字段对 GB100 / GB10x / GB20x 给出签名 96 B、公钥 97 B、COT v2，正是 ECDSA P-384 的尺寸（未压缩点 `1 + 48 × 2`，`r‖s` 为 `48 × 2`）。`nova-core` 代码把 384 B 称为上限并允许更短，与此不矛盾。
- **“从 FMC ELF 提取签名”已过时。** 主线在 2026-08 把 FMC 固件改为 TLV 容器（`VERS` / `BLOB` / `HASH` / `PKEY` / `SIGN`），文档页尚未更新。
- 内核文档的 PRC 协议可以**修改**设备配置。本仓库不使用它，也不收录相关命令。

---

## 2. `nvidia-vbios-vix-editor`（`638ea500f909`）

独立实现的离线解析器，声明支持 GB202 / GB203 / GB205 / GB206 / GB207。它的许可证是 GPL-3.0，与本仓库的 GPL-2.0-only 不兼容，因此**没有复制任何代码**；`scripts/rom-directory.py` 与 `v6-closure.py` 中的检查是按格式事实另写的。

该解析器将明文目录称为 object-directory v3。目录头为 16 B，每个条目为 60 B，包含 ID、偏移、长度和 SHA-384 摘要。以下两种表头可直接与 GB205 读回值核对。

| 清单 `entry_id` | 编辑器对同号对象的要求 | GB205 实测表头 | 长度 |
|---|---|---|---|
| `0x14` | VFE 表，`30 / 14 / 29 / 255 / 25` | `30 / 14 / 29 / 255 / 25` | `14 + 255 × 29 = 7409`，恰为 `field_C` `0x1cf1` |
| `0x12` | 功率器件表，`30 / 24 / 25 / 11` | `30 / 24 / 25 / 11` | `24 + 11 × 25 = 299`，小于 `field_C` 1320，余下部分未解 |

（表头依次为版本、头长、记录长、记录数、下一张表的记录长；版本为十六进制，其余为十进制。）

**已在完整 ROM 上确证**（SHA-256 与 [vbios-v6-descriptor.md](vbios-v6-descriptor.cn.md) §1 一致）：7 个目录、155 个对象的 SHA-384 全部校验通过；清单的 10 个 type 1 编号**全部**命中同号对象且起点相同，9 个长度也相等（`0x14` 只取对象的前 7409 B）；6 个 V6 描述符的长度都恰好等于所在对象。详见 [vbios-v6-descriptor.md §6.10](vbios-v6-descriptor.cn.md)。

本仓库只引用格式事实和表头对照。表的名称属于第三方解释；配套编辑器和加载器不属于只读采集流程。

---

## 3. 公开 ROM 的来源与取用限制

跨卡验证需要别的卡的 ROM。ROM 受版权保护且可能含板卡信息，**一律不入库**，只提交 `scripts/rom-survey.py --export` 的元数据。

| 来源 | 可信度 | 取用方式 |
|---|---|---|
| 自己的卡（GPU-Z 保存，或 `nvflash` 的保存功能） | 最高 | 只读，首选 |
| 板卡 / 笔记本厂商的更新包 | 高 | 自行下载解包，**不要**运行其中的刷写程序 |
| [TechPowerUp VGA BIOS Collection](https://www.techpowerup.com/vgabios/) | 看条目是否已审核 | **只能人工在浏览器里下载**。核查时（2026-11-23）其 robots.txt 对所有自动访问禁止 `/vgabios/*/*.rom`，对多种 AI 抓取器禁止整个 `/vgabios/`，并写明自动抓取须事先书面许可；自动读取返回 403。本仓库不提供下载脚本 |
| 论坛的编程器转储 | 来历不明 | 先看工具报告的对象哈希与目录摘要是否全部校验；任一项未全部通过（`MISMATCH` 或 `absent`，结论为 `DIFF`）的不能作为证据。反之**校验通过不等于原版**：编辑器可以修复哈希与目录摘要，本仓库不核验签名 |

提交结果时写明每份 ROM 的核心、板卡型号、VBIOS 版本与来源类别。首次普查见 [reports/rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md)，其中十一份由仓库所有者自行提供。

## 4. 交互算力图的数据与计算口径

- **规格数组与署名。** [交互图](index.html#zh)的桌面卡型号、核心及 SM 数等数据取自 **physicsdolphin/nv-uefi-unlocker** 的 269 行数组，固定提交 `e912777c9a89`，原仓库采用 GPL-2.0。页面只收录指定的 21 个型号。RTX 5060 Ti 的两种显存容量共用一个计算配置；上游缺少五款所选 GeForce 笔记本和工作站版 PRO 4500，由下列规格补齐。社区寄存器读回及 V2/V3 布局可分别查阅同一版本的状态记录和解析源码。[上游数据文件](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/docs/tools/tflops-ceiling.html) · [许可证](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/LICENSE) · [GB20x 状态记录](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/docs/blackwell-gb20x-status.md) · [V2/V3 字段来源](https://github.com/physicsdolphin/nv-uefi-unlocker/blob/e912777c9a899e4eb9b9a4d9355ec9610b05a315/loader/src/tx_fwsec.rs)
- **GeForce 与 RTX PRO。** 桌面 GeForce 采用参考频率，笔记本使用各型号官方 Boost 范围的上端。RTX PRO 采用桌面工作站配置。5090 D 的 AI TOPS 单独公布，图中的每 SM 估算不包含其产品级持续负载限制。[Blackwell 白皮书](https://images.nvidia.com/aem-dam/Solutions/geforce/blackwell/nvidia-rtx-blackwell-gpu-architecture.pdf) · [桌面规格](https://www.nvidia.cn/geforce/graphics-cards/compare/) · [笔记本规格](https://www.nvidia.com/en-us/geforce/laptops/50-series/) · [PRO 4500 工作站规格](https://marketplace.nvidia.com/en-us/enterprise/laptops-workstations/pny-rtx-pro-4500-blackwell-workstation-edition/) · [5090 D 产品说明](https://blogs.nvidia.cn/blog/nvidia-blackwell-geforce-rtx-50-series-opens-new-world-of-ai-computer-graphics/)
- **系统级参照。** B200 采用 HGX B200 八卡规格除以八，含稀疏加速的张量峰值再除以二；例如 BF16 稠密峰值为 36,000 / 8 / 2 = 2,250 TFLOP/s。GB10 指 6,144 核桌面 DGX Spark。官方页面主要给出 FP4 AI 性能，本图沿用上游对其他精度的估计。[HGX B200 规格及脚注](https://www.nvidia.com/en-au/data-center/hgx/) · [DGX Spark 规格](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
- **路线 2 标线。** 0.8× 对应 GB205 全程 FP16 累加的近似归一化值。分块算子的实测应另算，例如块大小 4096 时为 661.7 / 1024 ≈ 0.65×。[GB205 实测](../reports/gb205-2f18/README.cn.md)
