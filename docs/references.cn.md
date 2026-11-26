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
| [NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732) | BSD-3-Clause | SM120 FP8 `QMMA` 与单位缩放 `QMMA.SF`（`kind::mxf8f6f4`）对照 | 逐位一致与消费卡/PRO 6000 差异确认；本机 GB205 复现见第 5 节 |
| [flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963) | Apache-2.0 | RTX 5090 FP8 注意力 `QMMA` vs 单位缩放 `QMMA.SF` 实测 | GB202 跨卡印证 |
| [steviecurrie/gpu-unlock-gb206](https://github.com/steviecurrie/gpu-unlock-gb206) | GPL-2.0 | GB206 基线、`FEAT_READOUT_1` 非连续解码与 Debug/Prod FMC 对照 | Debug/Prod FMC 已在 `615.71.10` 和 `620.73.04` 独立复核，见第 5 节 |
| [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049)（issue [#2](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/2) · [#3](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/3)） | CC BY 4.0（文本）；MIT / GPL-2.0-only（工具） | `595.91.07` 下 87 组 GB202 实机实验（另含 `gsp_ga10x.bin` 与 Ada `booter_load` 离线分析） | 署名整理至第 5、6 节、[recovery.cn.md](recovery.cn.md#11-实机-fspfmc-错误码与四级失败模型第三方-gb2025959107) 与 [abi-invariants.cn.md](abi-invariants.cn.md#6-第三方-gb202-实测闭环与路线-1-剩余开放线索)；不复制代码或固件 |

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

---

## 5. 补充核查记录（2026-11-25）

- **跨五个 `open-gpu-kernel-modules` tag 的 Debug 与 Prod 签名 FMC 核验。** 直接比对上游 `g_bindata_kgspGetBinArchiveGspRmFmcGfwDebugSigned_GB202.c` 与 `ProdSigned_GB202.c`，`610` 与 `615` 之间存在明确的打包变化：

  | 驱动 tag | `UCODE_IMAGE`（287,304 B） | `UCODE_HASH`（48 B） | `UCODE_SIG`（96 B） | `UCODE_PKEY`（97 B） |
  |---|---|---|---|---|
  | `595.71.05` | 不同（`42e8e595` vs `960ccfe7`） | 不同 | 不同 | 不同 |
  | `595.91.07` | 不同（`42e8e595` vs `cb93e210`） | 不同 | 不同 | 不同 |
  | `610.57.04` | 不同（`e5dc9028` vs `17a15ce8`） | 不同 | 不同 | 不同 |
  | `615.71.10` | 逐字节相同（`5b74fefd`，熵 `7.9992`） | 相同 | 不同 | 不同 |
  | `620.73.04` | 逐字节相同（`55042efa`，熵 `7.9992`） | 相同 | 不同 | 不同 |

  两种情形下镜像均全加密、无明文前导、仅附单签名且无槽位表，Debug 版 FMC 既无法挖出明文 gadget，也不存在多签名降级面。
- **FP8/FP6/FP4/INT 全矩阵 MMA 与 CUDA 13.5 `cuBLASLt`（本机 GB205 真机实测）。** 在已归档的同一块 RTX 5070 Ti Laptop（`10de:2f18`，46 SM，驱动 `621.01`，`nvcc 13.5`，`-gencode arch=compute_120a,code=sm_120a`，非零随机有限输入，`cuobjdump --dump-sass` 核对指令；完整表格见 [software/README.cn.md 第 4 节](../software/README.cn.md#4-fp8fp32-满速软件路径qmmasf-与-cuda-132)）上实测：
  - `QMMA.SF.16832`（常量单位 UE8M0 `0x7F7F7F7F`）只要至少一个操作数为 FP8，即可从 `1022.4` 翻倍至 **`~2045 FLOP/SM/clk`**（`2.00×`，与普通 `kind::f8f6f4` 逐位一致 `0 / 33,554,432` 差异，缩放 `2.0` 对照组约 `33.55M` 差异），覆盖 `e4m3×e4m3`、`e5m2×e5m2`、`e4m3×e5m2`、`e4m3×e3m2`、`e3m2×e4m3`、`e4m3×e2m1`、`e2m1×e4m3`、`e5m2×e2m1`，且 `256² + 0.125² = 65536.015625`（`0x47800002`）精确无损；2:4 稀疏 `QMMA.SF.SP.16864` 同样从 `2045.9` 升至 **`4026.4 FLOP/SM/clk`**（`1.97×`）。
  - 若两个操作数均不含 FP8，`QMMA.SF.16832` 维持半速（`e3m2×e3m2` / `e2m3×e2m3`：`1023.1`，`e3m2×e2m1`：`1023.2`，`e2m1×e2m1`：`1022.9 FLOP/SM/clk`）。满速 FP4 须走 `OMMA.SF.16864`（`mxf4`/`mxf4nvf4`：**`4087.7 FLOP/SM/clk`**）；INT8 `IMMA.16832.S8.S8` 跑满 **`2031.8 OP/SM/clk`**；INT4 `s4` 由 `IMMA.16832.S8.S8` 软模拟（`213.7 OP/SM/clk`）。
  - **未公开 `E3M4FN`（SASS 类型码 `2`，本机 GB205 真机实证）。** 差分全部 5 种公开 `QMMA.SF.16832` 类型的 `nvdisasm -hex` 编码，定位到 `hi64` 中的非连续 3-bit 类型位：`typeA = (bit83 << 2) | (bit82 << 1) | bit78`、`typeB = (bit85 << 2) | (bit84 << 1) | bit79`（`bit 80` 为稀疏位）。将类型码原位修补为 `2`（`0b010`，`nvdisasm` 静默跳过；`6`/`7` 解码为 `INVALID6`/`INVALID7` 并报 `CUDA_ERROR_ILLEGAL_INSTRUCTION`）后，GB205 硬件原生按 **E3M4FN**（`1s + 3e bias=3 + 4m`，次正规数 `0x01 = 2⁻⁶`..`0x0F = 15/64`，`0x30 = 1.0`，`0x31 = 1.0625`，最大有限值 `0x7E = 30.0`，仅 `0x7F`/`0xFF` 为 NaN）执行，`E3M4×E3M4`（`227.05 TFLOP/s`）与 `E4M3×E3M4`（`225.92 TFLOP/s`）均跑满 **`2045.2 FLOP/SM/clk`**。
  - 同卡同驱动 `N=8192` FP8 E4M3 → FP32 GEMM（BF16 输出）：CUDA 13.5 `cuBLASLt 131000` 在 `FAST_ACCUM=0` 下测得 `241.70 TFLOP/s`（`4.55 ms/iter`），`FAST_ACCUM=1` 为 `239.93 TFLOP/s`（`4.58 ms/iter`），较归档的 PyTorch `2.11.0+cu128`（CUDA 12.8）基线（`111.80 TFLOP/s`，`0.50×`）提升 `2.16×`。结果与 RTX 5080（[NVIDIA/cutlass#3732](https://github.com/NVIDIA/cutlass/issues/3732)）、RTX 5090（[flashinfer-ai/flashinfer#5963](https://github.com/flashinfer-ai/flashinfer/issues/5963)）及 RTX 5070 Laptop（[steviecurrie/gpu-unlock-gb206#1](https://github.com/steviecurrie/gpu-unlock-gb206/issues/1)）一致。传统 `HMMA`（FP16 / BF16 / TF32 → FP32）依然锁在 `0.5×`（`511.4–511.8 FLOP/SM/clk`）。
- **GSP-RM 内部路径、LPWR、`PMU_RISCV_EB` 与 Ada `booter_load`（第三方离线分析）。** 以下条目来自 [shuttie/bf16-unlock-blackwell-research#2](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/2) 与 [#3](https://github.com/shuttie/bf16-unlock-blackwell-research/issues/3)，均属离线静态反汇编或 Unicorn 有界模拟，不作为真机实测：
  - **GSP-RM 写入链与 Control Handler 普查。** 在 `595.91.07`（GB202，#3）与 `596.49` / `595.71.05`（GB206，#2）中均追踪到 `engine-start → Graphics+0xa58 → Graphics+0x938` 写 `0x381c`/`0x3820`，说明 GSP-RM 在引擎初始化时确实尝试写入，但受 PL2 权限限制会被 `FEAT_OVR_PLM`（PL3-only）硬件丢弃。`595.91.07` 的 1,516 个非空 RM Control Handler 中成功反编译 1,363 个（153 个反编译失败），已反编译项无一调用 BAR0 端口助手；公有读取接口（`0x20801230` / `0x2080123c`）仅读内存缓存；全镜像扫描未见写 `0x823804` 的 RM 路径。
  - **注册表开关、LPWR slot-1 与 `PMU_RISCV_EB`。** 在 `596.49` GB206 的 Unicorn 有界仿真中（#2）：合成输入下 `RMDisableFeatureDisablement`（`GPU+0x545a`）仅改变零值回退逻辑，不将非零缓存值 `1/2/3` 清零；`OBJPGCTRL` 的 LPWR slot-1 捕获仅对 Turing（ordinal `37..41`）开启，GB206（`70`）已禁用；`PMU_RISCV_EB`（`falconId=26`）携带 4,944 B `LSF_UCODE_DESC_WRAPPER`（`bLsEncrypted=1`，熵 `7.9999`），属 Light Secure（LS / PL2），无 PL3 写权限。
  - **Ada `booter_load` 栈溢出机理。** 对公开 `595.91.07` AD102/GA102 debug `BooterLoadUcode` 的 Falcon 静态反汇编（#3 评论 `6863650651`）显示，签名加载器（`0x6456`/`0x6556`）缺失对 `sp+0x48` 处 `0x40` B 栈缓冲区的上界校验，在 `host_length ∈ (0x40, 0x60]` 时可越界改写 `sp+0x88` 栈帧而不触发 `sp+0xa8` 处的栈金丝雀。Blackwell 已完全移除 SEC2 `booter_load` 阶段。

---

## 6. 第三方 GB202 实机实验汇总（`shuttie/bf16-unlock-blackwell-research`，`d85201e2465d`）

2026-11-26 核查自 [shuttie/bf16-unlock-blackwell-research](https://github.com/shuttie/bf16-unlock-blackwell-research/tree/d85201e2465dfaefe347644f7a1a5129aceef049)（commit `d85201e2465d`，2026-11-05；文本采用 **CC BY 4.0** 许可；本仓库未复制任何工具源码或固件）。测试平台为 **2× RTX 5090（GB202 `10de:2b85`）**、Linux 驱动/GSP-RM **`595.91.07`**，另附一张 `615.71.09` 下的 **RTX PRO 6000（`10de:2bb1`）** 对照（`FEAT_READOUT_1 = 0`，518 TFLOP/s BF16→FP32）。

下列实机结论均属第三方在 `595.91.07` 上的观测，与本仓库 GB205 本地采集及 `620.73.04` 静态基线严格区分：

- **FSP/FMC 错误码分层与主机驱动侧踩坑。** 实测整理 17 个 FSP COT 与 FMC 解析/鉴权错误码，按“预鉴权拒绝 / WPR2 已拉起鉴权失败（`0x377`、`0x1db`、`0x33`）/ 参数硬卡死 / `SR_RESUME` 静默死机”四级归类，并附带记录 `595.91.07` 上两项非固件侧的主机坑（`kfspCleanupBootState_IMPL` 在 COT 重试路径的对象生命周期 bug，以及早于 `gpuStateLoad` 映射 BAR1 的插桩时序约束）。详见 [recovery.cn.md §1.1](recovery.cn.md#11-实机-fspfmc-错误码与四级失败模型第三方-gb2025959107)。
- **冷启字段活性、`FEAT`/FLR 时序与 `SR_RESUME` 实测。** 实机区分 `WprMeta`/COT 的活字段与忽略字段（`COT.frtsVidmemOffset` 支持 ≥ 1 GiB 1:1 平移整套 WPR；`WprMeta.frtsSize`、`verified`、`sizeOfSignature`、`bootloader{Code,Data}Offset` 被忽略；`sysmemAddrOfRadix3Elf` 被钉死；`sysmemAddrOfSignature` 为活的 4 字节对齐指针），证实 `FEAT` 在 FMC 之前即已定型、`SS0`/`SS1` 仅跨冷断电变化（跨热启与 FLR 保持）、FLR 后 `FEAT_OVR_PLM`（`0x80842bcf → 0xfffffb8f`）在 ~1.16–1.35 s 内由 GPU 侧自主 devinit 重锁，以及 `SRMeta` + 三级 `radix3` 与 `205.875 MiB` 高熵恢复密文。详见 [abi-invariants.cn.md](abi-invariants.cn.md)（§1–§3 及 [§6](abi-invariants.cn.md#6-第三方-gb202-实测闭环与路线-1-剩余开放线索)）。
- **与本仓库的互补定位。** 对方平台停留在 `595.91.07`（因 `615+` 移除了 GSP-RM 安全启动调试串并将引擎 ucode 迁入 `NVUCODES`）；本仓库的 [620.73.04 基线](../patches/README.cn.md) 正好覆盖 post-615 新 ABI（256 B `GSP_FMC_BOOT_PARAMS`），可用于推进 `570` / `595` / `620` 跨版本差分探测。
