[English](route2-mma-bypass.md) | [中文](route2-mma-bypass.cn.md)

# 路线 2 结论摘要与独立仓库迁移说明

> **迁移说明**：路线 2 的完整 CUDA 内核、硬件微探针、真实上游 `vLLM Marlin` 对照、端到端 288k/32k WikiText-2 LLM 评测、原始 JSON 归档与论文复现脚本已独立拆分为单独的混合精度调度研究仓库（**仓库链接待公开后补上**）。
>
> 为确保外部历史引用与 commit 永久可复核，拆分前的完整研究报告、图表、源码与原始数据均保留在本仓库的 **`v1-pre-split`** 标签中（对应提交 `5761a1953358fdef4dcad05bfecfe6165e44a1a9`，此前引用过的 `1f1c489` 等提交同样完整保留）。

---

## 核心结论速查（GB205，RTX 5070 Ti Laptop，驱动 `621.01`，CUDA `13.5`）

> **适用范围**：以下全部结果目前仅在一张 **GB205 笔记本（RTX 5070 Ti Laptop，46 SM，140 W；纯 PyTorch 分块算子取自归档 PyTorch `2.11.0+cu128` ~100 W）** 上实测验证，跨卡验证将在独立新仓库中补充归档。

1. **发射门机理**：SM 12.0 全 4,096 个 12-bit opcode 穷举确认不存在 `HMMA.SF`；`HMMA.F32`、传统非缩放 `QMMA` 以及不含 FP8 操作数的 `QMMA.SF` 共用同一套每 SMSP **32 周期硬冷却发射门（无突发信用）**，底层张量核数据通路仍为 16 周期满速；按 **`1:2`**（`HMMA.F32 : HMMA.F16`）交错发射可零门控惩罚填满空档。
2. **FP8 / FP6 / FP4 / INT8 满速规则**：
   - 升级至 CUDA 13.5 `cuBLASLt` 即可让 `N=8192` FP8→FP32 从 `111.80` 跃升至 **`241.70 TF/s`**（免写内核）；
   - 自定义内核改用单位缩放 `QMMA.SF`（`UE8M0 = 0x7F`），只要至少一侧是 8-bit 浮点即达 **`2045.3 FLOP/SM/clk`**（相对普通 FP8 `2.00×`，`0 / 33,554,432` 位差异；2:4 稀疏 `4026.4`，`1.97×`）；
   - FP6 加载时无损升 `E4M3` 走 `QMMA.SF` 达 **`2048.1 FLOP/SM/clk`**（`2.00×`，`0` 位差异）；
   - 满速 FP4（**`4087.7 FLOP/SM/clk`**）必须用 `OMMA.SF.16864`（`kind::mxf4` / `mxf4nvf4`），切勿走 `kind::mxf8f6f4`（仅 `~1023`）；INT8 `IMMA` 跑满 **`2031.8 OP/SM/clk`**。
3. **纯 BF16 / FP16 → FP32（TMA-WS `Pure B1` / `C1+B1`）**：
   - 相对 `cuBLAS BF16→FP32`：同频 **`1.52–1.69×`**（48 对齐 `K=4080`：`C1+B1` `1.53–1.58×`、`Pure B1` `1.63–1.69×`），140 W 笔记本墙钟 **`1.25–1.47×`**；
   - 288,768 tokens WikiText-2-raw 上 BF16 部署统计不可区分，FP16 部署绝对超额 KL 仅 `+6.46e-7`（`C1+B1 + K-perm`）至 `+1.35e-6`（`Pure B1` 保留 `lm_head`）。
4. **已量化权重 `W8A16` / `W4A16` 与部署冻结 K 平滑 `FP8-KV`（单侧双发 `Dual-FP8 QMMA.SF`）**：
   - `QMMA.SF.16832` 对 E4M3 次正规数无 FTZ，`X = s(H + L/16)` 在 `[2⁻⁶, 256)` 逐位无损（`3,584/3,584`，覆盖真实 LLM `99.94%` 激活）；同期 5.17 亿块普查证实严格无损 INT8 仿射通过率为 **`0 / 517,031,936`**；
   - Prefill 墙钟较未修改上游 `vLLM Marlin`（commit `3aad861`，SASS 证实全为受限 `HMMA.16816.F32.BF16`）快 **`1.53–1.86×`**（同权重完整 Llama-3-8B MLP 快 **`1.41–1.77×`**），较自研 Marlin 式 `Dequant-HMMA.F32` 上界快 **`1.56–1.63×`** 墙钟（同频 `1.97–2.01×`）；
   - 冷 L2 Decode（`M=1–16`）较 `cuBLAS BF16` 快 **`1.71–1.77×`**（W8）/ **`2.66–2.99×`**（W4），对称稳态吞吐比 `Dual/Single = 0.89–1.00×`；纯 `LDG.E.128` 消融证实片上 W4 解包开销 `< 0.1%`（`49.1 vs 49.2 µs`）；
   - 32k WikiText-2 上消除 `W8_MXFP8` 与冻结 K 平滑（`Prefill-256` 含 decode 后缀 `[256:]` 及离线静态 $\mu_{\text{calib}}$）`FP8-KV` 中 Single-FP8 带来的 `+65%–107%` 额外 KL（`W4A4` 则飙升至 `+122%–184%`）；**决策边界**：不要仅为使用 `Dual-FP8` 而量化原 BF16 模型，在已量化权重上 Decode 无权衡首选 `Dual-FP8`，Prefill 则按质量与速度在 `Dual-FP8` 与 `Single-FP8` 间权衡。

---

## 历史文件对照

- 本仓库留存的纯用户态基准与免编译算子见 [software/README.cn.md](../software/README.cn.md)（`clamp_eval.py`、`chunked_fp16_matmul.py`）。
- 若需查看拆分前本仓库中的完整报告、SVG 图表与原始 JSON 归档，请检出本仓库 tag **`v1-pre-split`**（`git checkout v1-pre-split`）。
