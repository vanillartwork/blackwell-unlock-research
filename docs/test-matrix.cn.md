[English](test-matrix.md) | [中文](test-matrix.cn.md)

# Blackwell 跨卡测量与基线

本表分别列出各芯片的寄存器读回值、ROM 元数据和稳态吞吐。`—` 表示缺少测量；跨卡比较时只使用各卡实际采得的数据。

---

## 1. 跨核心只读寄存器与特权门基线

| 寄存器 / 指标 | BAR0 偏移 | GB202<br>（RTX 5090） | GB203M | GB203<br>（5080 / 5070 Ti） | **GB205**<br>（5070 Ti Laptop） | GB206<br>（5060 Ti 16G） | GB207<br>（5050） | RTX PRO 6000<br>（GB202 专业卡） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PCI Device ID | — | — | — | 待采 | **`10de:2f18`** | `10de:2d04` | 待采 | `10de:2bb1` |
| `PMC_BOOT_0` | `0x000000` | `0x21120000`（待复核 ①） | `0x1b3000a1` | 待采 | **`0x1b5000a1`** | `0x1b6000a1` | 待采 | — |
| `FEAT_OVR_PLM` | `0x823804` | **`0xfffffb8f`** | **`0xfffffb8f`** | 待采 | **`0xfffffb8f`** | **`0xfffffb8f`** | 待采 | **`0xfffffb8f`** |
| `FEAT_READOUT_1` | `0x823818` | **`0x00c36180`** | **`0x00c36180`** | 待采 | **`0x00c36180`** | **`0x00c36180`** | 待采 | **`0x00000000`** |
| `FEAT_READOUT_0` | `0x823814` | `0x00000233` | — | 待采 | `0x00000233` | `0x00000233` | 待采 | — |
| `FEAT_OVR_ECC` / `ECC_1` | `0x82380c/10` | `0x00888888` / `0x002a2aaa` | — | 待采 | `0x00888888` / `0xaa2a2aaa` | `0x00888888` / `0xaa2a2aaa` | 待采 | — |
| `FUSE_SS_FMLA32` | `0x8207d8` | **3** | **3** | 待采 | **3** | **3** | 待采 | **0** |
| `FUSE_SS_IMLA1 / 2 / 4` | `0x8207e0/e4/ec` | 3 / 3 / 3 | 3 / 3 / 3 | 待采 | 3 / 3 / 3 | 3 / 3 / 3 | 待采 | 0 / 0 / 0 |
| `FUSE_SS_DP/FFMA/FMLA16/IMLA0/3` | — | 全 0 | 全 0 | 待采 | 全 0 | 全 0 | 待采 | 全 0 |
| `FUSE_FEAT_OVR_DIS` | `0x8203f0` | 0 | 0 | 待采 | 0 | 0 | 待采 | — |
| `FUSE_EN_SW_OVERRIDE` | `0x820040` | 0 | 0 | 待采 | 0 | 0 | 待采 | — |
| `FUSE_SS_PLM` | `0x8200fc` | — | `0xfffffbff` | 待采 | `0xfffffbff` | `0xfffffbff` | 待采 | — |
| `SECURE_FEAT_OVR_QUADRO_WR` | `0x82038c` | 2 | — | 待采 | 2 | 2 | 待采 | — |
| `SM_ISSUE_RATE_MODIFIER` | `0x504204` | 5 | — | 待采 | **5** | 5 | 待采 | — |
| `SEC2_PLM` | `0x8403c4` | — | — | 待采 | **`0x00000b0f`** | `0x00000b0f` | 待采 | — |
| `GSP_CPUCTL` | `0x110100` | `0xbadf4100` | — | 待采 | `0xbadf5720` | `0xbadf5720` | 待采 | — |
| `WPR2_LO / HI` ② | `0x1fa824/28` | — | — | 待采 | `0x02fb8800` / `0x02fc1000` | — | 待采 | — |
| 证据来源 | — | 公开社区样本 | 公开社区样本 | 征集中 | [reports/gb205-2f18/](../reports/gb205-2f18/README.cn.md) | 公开社区样本（`nv-uefi-unlocker` #168） | 征集中 | 公开社区样本 |

- ① 该值照录自社区样本，但与其他三个 GB20x 样本的格式不符：`PMC_BOOT_0[31:20]` 在 GB203 / GB205 / GB206 上依次为 `0x1b3` / `0x1b5` / `0x1b6`，GB202 按同一规律应为 `0x1b2`。在拿到新的 GB202 读回之前，不要用它识别芯片。
- ② 随驱动与引导变化，不参与“全系同形”比对；编码与地址注意事项见 [abi-fields.md §7](abi-fields.cn.md#7-wpr2_lo--wpr2_hi-的编码与地址)。
- `GSP_CPUCTL` 的 `0xbadf4100`（GB202）属于 `0xbadf41xx`，是 GSP 寄存器仍锁给 FSP 时的读回，不是寄存器内容（`nova-core` `falcon/gsp.rs`）；GB205 / GB206 的 `0xbadf5720` 不属于该模式，含义未知。
- GB205 一列可由归档原文重新生成：`python3 scripts/parse-ssdiag.py reports/gb205-2f18/bar0-prom-metadata-621.01.txt --markdown`。

---

## 2. 跨核心 VBIOS PROM BIT / V6 元数据对比

| 核心 / 型号 | VBIOS 版本 | BIT 偏移 / `adjust` | 微码表槽位（总 / 非空 / 空） | 非空 App ID | 描述符格式 | 详细笔记 |
|---|---|---|---|---|---|---|
| **GB205（RTX 5070 Ti Laptop `2f18`）** | `98.05.31.00.44` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | `0x07, 0x15, 0x18, 0x19, 0x23, 0x24`（`0x18/0x24` 超出 1 MiB BAR0 窗口，完整 ROM 中全为 80 B V6） | 全部 80 B **V6**（`vDesc bit2` 均置位；`0x19/0x15` 含 20 B Class A 二级清单） | [vbios-v6-descriptor.md](vbios-v6-descriptor.cn.md) |
| GB202（RTX 5090 `2b85`） | `98.02.2E.80.C8` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB202（RTX 5090 D `2b87`） | `98.02.31.40.4C` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB202（RTX PRO 6000 `2bb1`） | `98.02.52.00.02` | `0x365F0` / `0x4D800` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB203（RTX 5080 `2c02`） | `98.03.3B.C0.3E` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB203（RTX 5070 Ti `2c05`） | `98.03.3B.80.2F` | `0x38DF0` / `0x50000` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB203（RTX 5070 Ti `2c05`） | `98.03.58.40.B6` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB205（RTX 5070 ASUS `2f04`） | `98.05.28.00.64` | `0x391F0` / `0x50400` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB205（RTX 5070 Palit `2f04`） | `98.05.28.00.1D` | `0x391F0` / `0x50400` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB206（RTX 5060 Ti `2d04`） | `98.06.1F.00.CD` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB206（RTX 5060 `2d05`） | `98.06.21.40.42` | `0x361F0` / `0x4D400` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |
| GB207（RTX 5050 `2d83`） | `98.07.1D.00.55` | `0x365F0` / `0x4D800` | 35 / 6 / 29 | 同本机 | 全部 80 B **V6** | [rom-survey-20261123](../reports/rom-survey-20261123/README.cn.md) |

解码标量已整理为机读文件 [v6-descriptors-98.05.31.00.44.json](../reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json)。其他核心按同一 schema 提交后，用 `python3 scripts/v6-closure.py <文件>` 即可看出 GB205 的哪些规律仍成立。对象目录（7 个目录、155 个对象，哈希全部校验）的编号、偏移与长度见 [rom-directory-98.05.31.00.44.json](../reports/gb205-2f18/rom-directory-98.05.31.00.44.json)。

---

## 3. 稳态张量钳制评估对比（`software/clamp_eval.py` 口径）

时钟归一化发射率 `ratio = 实测 FLOP/clk/SM ÷ 架构未钳制满速上限`：

| 核心 / 型号 | 驱动 | 规模 | `fp16_fp16acc` | `fp16_fp32acc` | `bf16_fp32acc` | `tf32_fp32acc` | `fp8_fp32acc` | 原始档案 |
|---|---|---|---:|---:|---:|---:|---:|---|
| **GB205（5070 Ti Laptop, 46 SM）** | `621.01` | `N=8192` | **0.83×**（82.41 TF/s） | **0.49×**（53.72 TF/s） | **0.48×**（60.41 TF/s） | **0.48×**（30.15 TF/s） | **0.50×**（111.80 TF/s） | [clamp-eval-621.01-n8192.json](../reports/gb205-2f18/clamp-eval-621.01-n8192.json) |
| **GB205（5070 Ti Laptop, 46 SM）** | `621.01` | `N=4096`（400 iters） | **0.80×**（83.21 TF/s） | **0.49×**（54.07 TF/s） | **0.47×**（59.98 TF/s） | **0.49×**（29.60 TF/s） | **0.54×**（117.68 TF/s） | [clamp-eval-621.01-n4096.json](../reports/gb205-2f18/clamp-eval-621.01-n4096.json) |
| GB202 / GB203 / GB206 / GB207 | — | 待采 | — | — | — | — | — | 按 [reports/template.md](../reports/template.cn.md) 提交 |

与归档基线比对（驱动升级后建议跑一次，`ratio` 漂移超过容差时退出码为 3）：

```bash
python3 scripts/verify-baseline.py --bench --compare reports/gb205-2f18/clamp-eval-621.01-n8192.json
```

`N=4096` 一组因单次耗时短，`fp16_fp16acc` / `fp8_fp32acc` 只有 9 / 6 个有效遥测样本，其时钟归一化值的不确定度高于 `N=8192`。

---

## 4. 待补充的测量

1. **GB203（RTX 5080 / RTX 5070 Ti 桌面）** 与 **GB207（RTX 5050）** 的完整只读寄存器 + `--prom-bit` 样本。
2. **原生 Linux（`open-gpu-kernel-modules`）** 下任意 GB20x 卡片的冷启动 vs 合法挂起恢复（S3/s2idle）`WPR2` / `GSP` 日志对照（路线 1 `INV-S02` / `INV-X01`）。
3. 任意 GB20x 上 `WPR2` 的**双地址**读回（`0x1fa824/28` 与 `0x88a824/28`），用于确认两处是否镜像。
4. 一份新的 **GB202** `PMC_BOOT_0` 读回（见 §1 注 ①）。
5. GB20x 的 FSP 就绪寄存器 `0x00ad00bc` 只读读回（预期 `0xff`，见 [abi-fields.md §6](abi-fields.cn.md)）。
6. 更多 ROM 的 `scripts/rom-survey.py --export` 输出（只含编号、偏移与长度；来源见 [references.md §3](references.cn.md)）。按价值排序：
   - **任意其他笔记本**（GB203 / GB206 / GB207 移动版，或另一版本的 5070 Ti Laptop）：判断只有本机笔记本不同的 6 个对象属于移动版、该产品还是 `98.05.31` 版本。桌面 5070 已排除 GB205 核心；
   - **任意型号**：检验对象 `0x52` / `0x5c` / `0x74` / `0x76` 的内容是否仍与外层 `+00C` 同步（§6.11.3）；
   - 版本为 `98.02.5x` 的消费级 GB202，或 GB203 / GB206 上的 RTX PRO（版本须与某张消费卡相同）：区分“产品”与“版本”的影响。公开库同型号多为同一版本族，较难找到；
   - 任意已有型号的另一个版本族（同型号第三字节不同）。
7. **RTX PRO Blackwell** 的只读寄存器样本（ROM 结构已比对，见 §2；熔丝与 PLM 只能从真机读）。
