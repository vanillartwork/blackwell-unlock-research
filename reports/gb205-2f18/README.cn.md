[English](README.md) | [中文](README.cn.md)

# GB205 实测报告：RTX 5070 Ti Laptop

本机 GPU 为 NVIDIA GeForce RTX 5070 Ti Laptop，PCI `10de:2f18`，子系统 `1558:6586`，VBIOS `98.05.31.00.44`。5 个活跃 GPC 包含 23 个 TPC、46 个 SM；显存 12 GiB GDDR7，最大功耗上限 140 W。

## 硬件与寄存器基线

采集环境为 Windows 11 26H2，驱动 `621.01` / `32.0.16.2101`。见[只读采集原文](bar0-prom-metadata-621.01.txt)。

- **芯片识别。** `PMC_BOOT_0 = 0x1b5000a1`（GB205 A1），`GR_TOPO_409604 = 0x06030005`（6 个 GPC 槽位，5 个活跃），`FB_SIZE_MIB = 0x00002fc3`。
- **权限掩码**
  - `FEAT_OVR_PLM (0x00823804) = 0xfffffb8f`，只有 PL3 可写。
  - `FUSE_SS_PLM = 0xfffffbff`, `GFW_PLM (0x00118128) = 0x00008b8f`, `MMU_PLM (0x001fa7c8) = 0x0884cb8f`.
  - `SECURE_FEAT_OVR_QUADRO_WR = 0x00000002`, `FUSE_EN_SW_OVERRIDE = 0x00000000`, `SM_ISSUE_RATE_MODIFIER = 0x00000005`.
- **速率选择器与熔丝镜像**
  - `SS0 (0x0082381c) = 0x76027063`, `SS1 (0x00823820) = 0x00030004`, `READOUT_1 (0x00823818) = 0x00c36180`.
  - 熔丝镜像： `FUSE_SS_FMLA32 = 3`, `FUSE_SS_IMLA1/2/4 = 3`, `FUSE_SS_DP/FFMA/FMLA16/IMLA0/IMLA3 = 0`.
- **WPR2 边界。** 下表来自 [616.64](bar0-core-616.64.txt) 与 [621.01](bar0-prom-metadata-621.01.txt) 两次采集。按 `(raw >> 4) << 12` 换算为字节地址，见 [ABI 字段参考 §7](../../docs/abi-fields.cn.md)。

  | 驱动 | `WPR2_LO` | `WPR2_HI` | 地址区间（MiB） | 跨度 |
  | --- | --- | --- | --- | ---: |
  | `616.64` | `0x02fa7e00` | `0x02fb0400` | 12199.875 – 12208.25 | 8.375 MiB |
  | `621.01` | `0x02fb8800` | `0x02fc1000` | 12216.5 – 12225 | 8.5 MiB |

  两个区间均在 12227 MiB 的显存范围内。`SEC2_MBOX0` 从 `0xbf7b2000` 变为 `0xbf7d4000`，`SS0/SS1` 从 `0x56027063/0x00032004` 变为 `0x76027063/0x00030004`。其余由[解析器](../../scripts/parse-ssdiag.py)跟踪且两次都采得的寄存器一致，`FEAT_OVR_PLM` 保持 `0xfffffb8f`。驱动与冷启动均有变化，无法单独归因。两次均在 Windows `LEGACY` 模式下读取 `0x1fa824/28`；Blackwell 驱动读取的 `0x88a824/28` 尚未采样。
- **VBIOS / PROM 元数据**
  - 已核对 5 个 `PCIR` 镜像。BIT 位于 `PROM+0x361f0`，含 20 个 token，`adjust = 0x4d400`。微码表位于 `PROM+0xa25a1`，35 槽中 6 槽非空，4 项可在 1 MiB BAR0 窗口内读到。6 个描述符均为 80 B V6，bit 2 均置位。
  - 完整 ROM 上，10/10 个不同的 Class A 源条目与同号目录对象起点一致；6/6 个 V6 描述符连同后续数据恰好填满对象。
  - 详见 [V6 结构分析 §6.10](../../docs/vbios-v6-descriptor.cn.md)。

## 稳态张量吞吐基线

使用 PyTorch `2.11.0+cu128`、CUDA `12.8`、驱动 `621.01` 和约 140 W 功耗上限，运行[稳态基准](../../software/clamp_eval.py)。

| 测试项 | `N=8192` TFLOP/s | `N=8192` FLOP/c/SM | `N=8192` 满速比 | `N=8192` 相对 L2 误差 | `N=4096` 满速比 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `fp16_fp16acc` | 82.41 | 853.1 | 0.833× （满速 1024） | `0.003244` | 0.796× | FP16 累加对照 |
| `fp16_fp32acc` | 53.72 | 505.5 | **0.494×** （满速 1024） | `0.000196` | **0.486×** | **约半速** |
| `bf16_fp32acc` | 60.41 | 487.8 | **0.476×** （满速 1024） | `0.001748` | **0.470×** | **约半速** |
| `tf32_fp32acc` | 30.15 | 247.6 | **0.484×** （满速 512） | `0.000296` | **0.492×** | **约半速** |
| `fp8_fp32acc` | 111.80 | 1016.1 | **0.496×** （满速 2048） | `0.001665` | **0.537×** | **约半速** |
| `int8` | 65.04 | 529.6 | 0.259× （满速 2048） | n/a | 0.255× | 仅供参考 |
| `fp32_control` | 17.98 | n/a | n/a | `0.0000016` | n/a | SIMT 对照 |

- `N=8192` 时，按 ABBA（关 → 开 → 开 → 关）切换 `allow_fp16_accumulation`，开启后的原始吞吐是关闭时的 **1.55–1.58 倍**，按时钟归一化后为 **1.68–1.71 倍**。
- `N=4096` 的单次运行较短，`fp16_fp16acc` 和 `fp8_fp32acc` 分别只有 9 个和 6 个有效遥测样本，时钟归一化结果的不确定度较高。
- 完整 JSON 记录： [`clamp-eval-621.01-n8192.json`](clamp-eval-621.01-n8192.json), [`clamp-eval-621.01-n4096.json`](clamp-eval-621.01-n4096.json).

## K 维分块的精度与吞吐（路线 2）

在 `N=K=8192`、持续功耗约 100 W 下运行[分块基准](../../software/chunked_fp16_matmul.py)。见[完整 JSON](chunked-fp16-621.01.json)。

| 方案 | 块数 | TFLOP/s | 相对 `fp32acc` 吞吐 | SM 时钟（MHz） | FLOP/c/SM | 相对 L2 误差 | 全程 FP16 误差 / 本项误差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline_fp32acc` | — | 44.29 | 1.00× | 1852 | 519.9 | `0.000196` | 16.57× |
| `chunk_k=512` | 16 | 28.25 | 0.64× | 1717 | 357.7 | `0.000870` | 3.73× |
| `chunk_k=1024` | 8 | 38.54 | 0.87× | 1785 | 469.3 | `0.001142` | 2.84× |
| `chunk_k=2048` | 4 | **48.90** | **1.10×** | 1800 | 590.6 | `0.001644` | **1.97×（降低约 49%）** |
| `chunk_k=4096` | 2 | **56.27** | **1.27×** | 1848 | 661.7 | `0.002140` | **1.52×（降低约 34%）** |
| `full_fp16acc(k=8192)` | 1 | 65.74 | 1.48× | 1672 | 854.7 | `0.003244` | 1.00× |

在此样本上，分块 2048 和 4096 的吞吐分别比 FP32 累加高 10%、27%，相对 L2 误差分别比全程 FP16 累加低 49%、34%。块更小时，中间矩阵的访存开销抵消了提速。融合内核的效果尚未实测。

## 归档文件

- [`bar0-prom-metadata-621.01.txt`](bar0-prom-metadata-621.01.txt) — 驱动 `621.01` 的 BAR0 与 PROM BIT/V6 脱敏元数据
- [`bar0-core-616.64.txt`](bar0-core-616.64.txt) — 驱动 `616.64` 的寄存器读回值
- [`rom-directory-98.05.31.00.44.json`](rom-directory-98.05.31.00.44.json) — 完整 ROM 的对象编号、偏移与长度（155 个对象，哈希均在本地核对）
- [`v6-descriptors-98.05.31.00.44.json`](v6-descriptors-98.05.31.00.44.json) — V6 描述符与 Class A 清单的解码标量
- [`clamp-eval-621.01-n8192.json`](clamp-eval-621.01-n8192.json) — 稳态张量基准 (`N=8192`)
- [`clamp-eval-621.01-n4096.json`](clamp-eval-621.01-n4096.json) — 稳态张量基准 (`N=4096`)
- [`chunked-fp16-621.01.json`](chunked-fp16-621.01.json) — 路线 2 的分块 FP16 累加扫描 (`N=8192`)
- [`route2-wt2-288k-fp64.json`](route2-wt2-288k-fp64.json) — `unsloth/Llama-3.2-1B` 在全量 WikiText-2-raw（282 × 1,024 = 288,768 tokens）上相对真 FP64 的逐段 KL / Top-1 / NLL 原始数据（可用 [`../../software/verify_wt2_bootstrap.py`](../../software/verify_wt2_bootstrap.py) 在无 GPU 环境下独立复算）
- [`route2-dual-qmma-621.01.json`](route2-dual-qmma-621.01.json) — GB205 单侧双发 `QMMA.SF`（`W8A16` / `W4A16` / 部署冻结 K 平滑 `FP8-KV`）硬件微探针（含 `128×128` 矩阵级长 K 统计）、TMA-WS 稳态 NVML 基准、Prefill 速度–精度前沿（含未修改上游 `vLLM Marlin` commit `3aad861` 及其 SM 12.0 SASS 审计、自研 `Dequant-HMMA.F32`、假设性 `Dequant-HMMA.F16`、`Single-FP8` 与 `W4A4`）、冷 L2 Decode 扫描（`M = 1–64`，含纯 `LDG.E.128` 带宽天花板与 W4 解包消融、对称稳态 `Dual/Single` 吞吐比及真实 `vLLM Marlin`）、同权重完整 Llama-3-8B MLP 端到端数据、两模型 5.17 亿块分布扫描（含精确 INT8 仿射 `0 / 517,031,936` 负结果）与 32k WikiText-2 双口径逐段 KL 数据（含 `W4A4`、AWQ+MSE 校准 `W4` 及 `Prefill-256` / 离线静态 $\mu_{\text{calib}}$ 冻结 K 平滑，可用 [`../../software/verify_dual_fp8.py`](../../software/verify_dual_fp8.py) 在无 GPU 环境下复算）
