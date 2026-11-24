[English](abi-fields.md) | [中文](abi-fields.cn.md)

# 主机侧引导 ABI 字段参考

本页列出 GB20x 主机驱动提交给 FSP 和 GSP-FMC 的结构体。解读快照时，先对照“主机写入”列，再核对 [ABI 不变量](abi-invariants.cn.md)。

> **证据等级：公开源码静态阅读**（`NVIDIA/open-gpu-kernel-modules`，阅读日期 2026-11-23）。
> 尚未在原生 Linux 真机上用日志补丁核对；真机快照若与本表不符，以快照为准并回填。
> 本仓库只记录字段名、类型、顺序与偏移等接口事实，不复制上游源码。

[`scripts/abi-layout.py`](../scripts/abi-layout.py) 计算结构体布局。用 GCC 15.2.0 核对 620.73.04 上游头文件，四个结构体的 85 个字段均与 `offsetof` / `sizeof` 一致。570 布局另经 `nova-core` r570.144 绑定逐字段印证。其他 tag 只核对了总长，包括 615 的 `GSP_FMC_BOOT_PARAMS` 和各 tag 的 `GspFwWprMeta`（上游静态断言均为 256 B）。见[独立核查记录](references.cn.md)。

```bash
python3 scripts/abi-layout.py --list
python3 scripts/abi-layout.py --struct GSP_FMC_BOOT_PARAMS --diff 610.57.04 620.73.04
python3 scripts/abi-layout.py --struct GspFwWprMeta --tag 620.73.04 --decode dump.hex
python3 scripts/abi-layout.py --from-log my-gb20x-report/dmesg-gsp-fmc.txt   # 解码日志补丁的输出
```

---

## 1. 调用链与 HAL 绑定（620.73.04）

GB202 / GB203 / GB205 / GB206 / GB207 的 HAL 绑定取自 `src/nvidia/generated/g_kernel_gsp_nvoc.c` 与 `g_kern_fsp_nvoc.c`。

| 步骤 | 绑定到的实现 | 上游文件 | 写入对象 |
|---|---|---|---|
| 等待 FSP 安全启动完成 | `kfspWaitForSecureBoot_GB202` | `fsp/arch/blackwell/kern_fsp_gb202.c` | — |
| 填写 WPR 元数据 | `kgspPopulateWprMeta_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c` | `GspFwWprMeta` |
| 填写 FMC 启动参数 | `kgspSetupGspFmcArgs_GH100` | 同上 | `GSP_FMC_BOOT_PARAMS` |
| 组装并发送 COT 命令 | `kfspPrepareBootCommands_GH100` → `kfspSendBootCommands_GH100` | `fsp/arch/hopper/kern_fsp_gh100.c` | `NVDM_PAYLOAD_COT`（MCTP/NVDM 消息，`NVDM_TYPE_COT`） |
| 判断 WPR2 是否已建立 | `kgspIsWpr2Up_GB100` | `gsp/arch/blackwell/kernel_gsp_gb100.c` | 只读 |

（路径前缀均为 `src/nvidia/src/kernel/gpu/`。）

因此，观测点在 **Hopper 文件**里，不在 `kernel_gsp_gb202.c`：后者只含 vGPU 堆大小、vGPU 分区数与 ucode 熔丝版本三个函数，不碰这两个结构。`kernel_gsp_fwsec.c` 在 620.73.04 只剩许可证头，Blackwell 也不走 `kernel_gsp_booter.c` 的 Booter 路径。

570.124.04 中对应 `GspFwWprMeta` 的函数名是 `kgspCalculateFbLayout_GH100`。

`kfspSendBootCommands_GH100` 在 COT 命令**失败**时已自带 `LEVEL_ERROR` 打印（`version`、`size`、FMC / FRTS / boot-args 偏移），成功路径不打印。[620.73.04 日志补丁](../patches/README.cn.md)补上了成功路径与两个结构体的快照。

---

## 2. 跨版本漂移

| tag | commit | `GSP_FMC_BOOT_PARAMS` | `GspFwWprMeta` | `NVDM_PAYLOAD_COT` |
|---|---|---:|---:|---:|
| `570.124.04` | `129479b1b734` | 80 B，无 `magic` | 256 B | 860 B |
| `610.57.04` | `e4a5faa2567f` | 88 B，无 `magic` | 256 B | 860 B |
| `615.71.10` | `6f3be8c9c3fb` | **256 B，带 `magic` / `size`** | 256 B（`padding` → `pagingConfig`） | 860 B |
| `620.73.04` | `a4b95f6a2db0` | 256 B（新增 `flags`、TDISP） | 256 B | 860 B |

- **`GSP_FMC_BOOT_PARAMS` 在 610 → 615 之间换了布局**：全部字段后移，SPDM 的 `payloadBufferSize` 与 `payloadBufferOffset` 还对调了顺序。按 570/610 偏移写的补丁用到 615+ 会读错字段。
- 615 → 620 只动了保留区：`bootGspRmParams` 追加 `flags` / `reserved`，末尾追加 `gspTdispParams`，已有字段偏移不变。
- `gsp_fw_wpr_meta.h` 在 615 起移除了 `GspFwHeapFreeList` 与两个 `RECOVERY_MARGIN` 标志；620 另立 `gsp_fw_wpr_meta_v1.h`（`GspFwWprMetaV1`）专供 Turing–Ada 的 Booter 使用，**Blackwell 不用 V1**。
- `NVDM_PAYLOAD_COT` 在所查的 5 个 tag（570.124.04、595.112.07、610.57.04、615.71.10、620.73.04）中头文件逐字节相同。

---

## 3. `GSP_FMC_BOOT_PARAMS`（620.73.04，256 B）

`magic` 是内存序 `"FMC "`（`0x20434d46`）。主机先整体清零再逐项写入，故保留字段应为 0。

| 偏移 | 长度 | 字段 | 主机写入值 |
|---:|---:|---|---|
| `0x000` | 4 | `magic` | `0x20434d46` |
| `0x004` | 2 | `size` | `256` |
| `0x020` | 4 | `initParams.regkeys` | CC / NVLink / TDISP 代理 regkey 按位或 |
| `0x040` | 4 | `bootGspRmParams.target` | 描述符所在地址空间 |
| `0x044` | 4 | `bootGspRmParams.gspRmDescSize` | 冷启动 `sizeof(GspFwWprMeta)`；恢复 `sizeof(GspFwSRMeta)`（均为 256） |
| `0x048` | 8 | `bootGspRmParams.gspRmDescOffset` | 冷启动指向 `GspFwWprMeta`；恢复指向 `GspFwSRMeta` |
| `0x050` | 8 | `bootGspRmParams.wprCarveoutOffset` | 不写（0） |
| `0x058` | 4 | `bootGspRmParams.wprCarveoutSize` | 不写（0） |
| `0x05c` | 1 | `bootGspRmParams.bIsGspRmBoot` | `1` |
| `0x05d` | 1 | `bootGspRmParams.bInstInSysMode` | 不写（0） |
| `0x05e` | 1 | `bootGspRmParams.bIcuEnabled` | 由芯片属性决定 |
| `0x05f` | 1 | `bootGspRmParams.bScrubCbcSr` | 由芯片 HAL 决定 |
| `0x060` | 4 | `bootGspRmParams.flags` | bit 0 = `SEC2_UCODE_SIDE_LOAD` |
| `0x080` | 4 | `gspRmParams.target` | LibOS 参数所在地址空间 |
| `0x088` | 8 | `gspRmParams.bootArgsOffset` | LibOS 初始化参数物理地址 |
| `0x0a0` | 4 | `gspSpdmParams.target` | 仅启用 SPDM 时写 |
| `0x0a4` | 4 | `gspSpdmParams.payloadBufferSize` | 仅启用 SPDM 时写 |
| `0x0a8` | 8 | `gspSpdmParams.payloadBufferOffset` | 仅启用 SPDM 时写 |
| `0x0c0` | 8 | `gspRmMemParams.flushSysmemAddrValLo/Hi` | 仅在 CC 的 5107790 规避项启用时写 |
| `0x0f8` | 1 | `gspTdispParams.bTdispEnabled` | TDISP 未启用时 0 |

其余偏移为 `reserved`。`target` 取值：`0` 本地显存、`1` 一致性系统内存、`2` 非一致性系统内存。

---

## 4. `GspFwWprMeta`（620.73.04，256 B）

“主机写入”列取自 `kgspPopulateWprMeta_GH100` 的赋值语句。595.112.07、610.57.04、615.71.10、620.73.04 四个 tag 的赋值集合一致，仅 `pagingConfig` 自 615 起才有。

| 偏移 | 长度 | 字段 | 主机写入 |
|---:|---:|---|:---:|
| `0x000` | 8 | `magic`（`0xdc3aae21371a60b3`） | 是 |
| `0x008` | 8 | `revision`（`1`） | 是 |
| `0x010` / `0x018` | 8 / 8 | `sysmemAddrOfRadix3Elf` / `sizeOfRadix3Elf` | 是 |
| `0x020` / `0x028` | 8 / 8 | `sysmemAddrOfBootloader` / `sizeOfBootloader` | 是 |
| `0x030` / `0x038` / `0x040` | 8 ×3 | `bootloaderCode` / `Data` / `ManifestOffset` | 是 |
| `0x048` / `0x050` | 8 / 8 | `sysmemAddrOfSignature` / `sizeOfSignature` | 有签名时 |
| `0x058` | 8 | `gspFwRsvdStart` | **否** |
| `0x060` | 8 | `nonWprHeapOffset` | **否** |
| `0x068` | 8 | `nonWprHeapSize` | 是 |
| `0x070` | 8 | `gspFwWprStart` | **否** |
| `0x078` | 8 | `gspFwHeapOffset` | **否** |
| `0x080` | 8 | `gspFwHeapSize` | 是（最小请求值） |
| `0x088` / `0x090` | 8 / 8 | `gspFwOffset` / `bootBinOffset` | **否** |
| `0x098` | 8 | `frtsOffset` | **否** |
| `0x0a0` | 8 | `frtsSize` | 是 |
| `0x0a8` | 8 | `gspFwWprEnd` | **否** |
| `0x0b0` | 8 | `fbSize` | **否** |
| `0x0b8` | 8 | `vgaWorkspaceOffset` | **否** |
| `0x0c0` | 8 | `vgaWorkspaceSize`（128 KiB） | 是 |
| `0x0c8` | 8 | `bootCount` | **否** |
| `0x0d0` | 16 | 联合体：分区 RPC 视图 / 填充 | **否** |
| `0x0e0` / `0x0e8` | 8 / 4 | `sysmemAddrOfCrashReportQueue` / `sizeOfCrashReportQueue` | 队列在系统内存时 |
| `0x0ec` | 4 | `lsUcodeVersion` 联合体填充 | **否** |
| `0x0f0` | 1 | `gspFwHeapVfPartitionCount` | 是（非 vGPU 为 0） |
| `0x0f1` | 1 | `flags` | 仅 `PPCIE_ENABLED` 位 |
| `0x0f2` | 2 | `pagingConfig` | 615 起 |
| `0x0f4` | 4 | `pmuReservedSize` | 是 |
| `0x0f8` | 8 | `verified` | **否** |

上游注释写明：主机只把大小作为请求传下去，真正的偏移由 ACR ucode 在建立 WPR2 时填写。≤610 的 `kernel_gsp.c` 在 GSP-RM 起来之后会把 `nonWprHeapOffset` 与 `frtsOffset` 从静态配置信息里回填到主机副本，这是启动后的读回，不是启动输入。

**推论**：在 GB20x 上，`gspFwWprStart` / `gspFwWprEnd` / `fbSize` / `bootCount` / `verified` 不属于主机输入面，主机侧快照里应当读到 0。这与 Turing–Ada 不同，后者由 `kernel_gsp_tu102.c` 在主机侧算好完整布局。

---

## 5. 挂起 / 恢复：`GspFwSRMeta`（256 B）

`kgspSetupGspFmcArgs_GH100` 按 `bootMode` 选择描述符：

| `bootMode` | 名称 | `gspRmDescOffset` 指向 |
|---:|---|---|
| 0 | `KGSP_BOOT_MODE_NORMAL` | `GspFwWprMeta` |
| 1 | `KGSP_BOOT_MODE_SR_RESUME` | **`GspFwSRMeta`** |
| 2 | `KGSP_BOOT_MODE_GC6_EXIT` | 代码上落入 `GspFwWprMeta` 分支；是否会走到该函数未核对 |
| 3 | `KGSP_BOOT_MODE_SR_WITH_WPR_IN_SYSMEM` | 同上 |

只有 `SR_RESUME` 改用 `GspFwSRMeta` 并**不再提交 `GspFwWprMeta`**，因此该恢复路径不是靠 `bootCount` 区分的。

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| `0x000` / `0x008` | 8 / 8 | `magic` / `revision`（`2`） |
| `0x010` / `0x018` | 8 / 8 | `sysmemAddrOfSuspendResumeData` / `sizeOfSuspendResumeData` |
| `0x020` | 128 | `internal[32]`（安全 ucode 内部使用） |
| `0x0a0` / `0x0a4` | 4 / 4 | `flags` / `subrevision` |
| `0x0a8` | 88 | `padding[22]` |

---

## 6. FSP 边界：`NVDM_PAYLOAD_COT`（860 B，1 字节对齐）

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| `0x000` / `0x002` | 2 / 2 | `version` / `size`（860） |
| `0x004` | 8 | `gspFmcSysmemOffset` |
| `0x00c` / `0x014` | 8 / 4 | `frtsSysmemOffset` / `frtsSysmemSize` |
| `0x018` / `0x020` | 8 / 4 | `frtsVidmemOffset`（**从显存末尾起算**）/ `frtsVidmemSize` |
| `0x024` | 48 | `hash384[12]` |
| `0x054` | 384 | `publicKey[96]` |
| `0x1d4` | 384 | `signature[96]` |
| `0x354` | 8 | `gspBootArgsSysmemOffset` → `GSP_FMC_BOOT_PARAMS` |

`hash384` / `publicKey` / `signature` 三个数组的长度固定，实际用多少由芯片决定，其余补零：

| 芯片 | COT `version` | 公钥 | 签名 | 算法 |
|---|---:|---:|---:|---|
| GH100 | 1 | 384 B | 384 B | RSA-3072 |
| GB100 / GB10x / **GB20x** | **2** | **97 B** | **96 B** | ECDSA P-384（由尺寸判断） |

尺寸与版本取自 `620.73.04` 的 `cotPayload*` HAL 字段，版本与 `nova-core` 一致。内核文档中的“RSA-3K”只对应 GH100。

### 消息封装与应答

COT 载荷前有两个 32 位头（`nova-core` `mctp.rs` 与内核文档一致）：

| 头 | 位 | 含义 |
|---|---|---|
| MCTP | `31` / `30` / `29:28` / `23:16` / `15:8` / `3:0` | SOM / EOM / 序号 / 源端点 / 目的端点 / 版本 |
| NVDM | `31:24` / `23:8` / `6:0` | 类型（`0x14` COT、`0x13` PRC、`0x15` 应答）/ 厂商 `0x10de` / 消息类型 `0x7e` |

应答在两个头之后是 `task_id`、`command_nvdm_type`、`error_code` 三个 `u32`；`error_code` 非零即失败。日志里的 `Task ID` / `Command type` / `Error code` 就是这三项，解读见 [recovery.md](recovery.cn.md)。

发送前主机先等 FSP 就绪：GB20x 读 `0x00ad00bc`，值为 `0xff` 表示 FSP 安全启动完成（GH100 / GB10x 为 `0x000200bc`），`nova-core` 的超时为 5 秒。

`RmDisableCotCmd` 按位段分别控制三类子命令：`1:0` FRTS（系统内存）、`3:2` FRTS（显存）、`5:4` GSP-FMC；默认 `0`。

---

## 7. `WPR2_LO` / `WPR2_HI` 的编码与地址

- **编码**：`VAL` 为位 `31:4`，12 位对齐，所以 `字节地址 = (raw >> 4) << 12`，等价于 `raw << 8`（低 4 位为 0 时）。GB100 的 `NV_HUBMMU_PRI_MMU_WPR2_ADDR_*` 与 TU102 的 `NV_PFB_PRI_MMU_WPR2_ADDR_*` 定义一致。
- **核对**：GB205 `621.01` 的 `0x02fb8800..0x02fc1000` 换算为 12216.5–12225 MiB，落在 `FB_SIZE_MIB = 12227` 之内，跨度 **8.5 MiB**；`616.64` 的 `0x02fa7e00..0x02fb0400` 跨度 **8.375 MiB**。
- **地址**：GB20x 的 `kgspIsWpr2Up_GB100` 读 `NV_HUBMMU0_PRI_BASE (0x880000) + 0xa828`，即 **`0x88a828`**（LO 为 `0x88a824`）。现有社区采集读的是 Turing 时代的 `0x1fa824/28`。GB205 在旧地址上读到了自洽的值，但两处是否互为镜像**未经核对**；后续采集请两处都读。`nova-core` 也定义了旧地址，但只在 Turing–Ada 路径使用，不能作为 GB20x 的印证。
- `LO` 为含边界，`HI` 为**不含**边界，`HI = 0` 表示未建立（`nova-core` `fb/regs.rs` 的说明；上游头文件未写）。跨度为 `HI − LO`。
- 已归档的 GB205 样本采自 Windows `LEGACY` 模式，WPR2 大小不能直接与 Linux GSP-RM 模式比较。
