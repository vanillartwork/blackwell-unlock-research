[English](README.md) | [中文](README.cn.md)

# 引导 ABI 日志补丁

本目录收录针对 `NVIDIA/open-gpu-kernel-modules` 固定版本的最小日志补丁。

1. **绝不提交上游源码或固件**：不复制 `NVIDIA/open-gpu-kernel-modules` 的任何完整源文件，不引入固件二进制或解密数据。
2. **只记录观测值。** 使用 `NV_PRINTF` 或 `printk` 记录引导结构与返回码。采集前在原生 Linux 物理机运行[环境预检](../scripts/check-env.sh)。
3. **钉死上游版本（Tag Pinning）**：每份补丁文件必须声明目标 tag、commit SHA 与校验说明，禁止跨版本盲打。

---

## 目标版本对齐表

观测点由 GB20x 的 HAL 绑定决定（见 [docs/abi-fields.md](../docs/abi-fields.cn.md)）。路径前缀均为 `src/nvidia/src/kernel/gpu/`。

| 观测内容 | 观测函数 | 关注文件 | 状态 |
|---|---|---|---|
| FMC 启动参数与 COT | `kgspSetupGspFmcArgs_GH100`、`kfspSendBootCommands_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c`<br>`fsp/arch/hopper/kern_fsp_gh100.c` | [620.73.04-01-boot-abi-trace.patch](620.73.04-01-boot-abi-trace.patch)（RFC） |
| WPR 元数据 | `kgspPopulateWprMeta_GH100` | `gsp/arch/hopper/kernel_gsp_gh100.c` | 同上 |
| 挂起 / 恢复边界 | `kgspSetupGspFmcArgs_GH100` 的 `KGSP_BOOT_MODE_SR_RESUME` 分支 | `gsp/arch/hopper/kernel_gsp_gh100.c`<br>`gsp/kernel_gsp.c` | 部分覆盖：同一补丁会打印 `bootMode` 与描述符长度；`GspFwSRMeta` 内容尚未打印 |

### `620.73.04-01-boot-abi-trace.patch` 的验证范围

| 检查 | 结果 |
|---|---|
| `git apply --check` / 应用 / `-R` 撤销（commit `a4b95f6a2db0`） | 通过，撤销后工作树干净 |
| `make -C src/nvidia`（gcc 15.2.0，Ubuntu 26.04） | 通过，无警告；`nv-kernel.o` 中保留全部 `BWLAB` 日志串 |
| 转储函数输出 → `collect-report.sh --sanitize` → `abi-layout.py --from-log` | 用户态桩程序往返通过 |
| 链接 `.ko`、加载、真机启动 | **未做** |

补丁只有新增行，不改上游任何一行，也不读写寄存器。打印级别为 `LEVEL_ERROR`，因为上游发行构建只把该级及以上写入 `dmesg`；每行带偏移，避免被上游的重复日志合并机制吞掉。

> **不要**把补丁打在这些文件上：`kernel_gsp_gb202.c`（不涉及启动参数）、`kernel_gsp_booter.c`（Blackwell 不走 Booter）、`kernel_gsp_fwsec.c`（620.73.04 只剩许可证头）、`inc/kernel/gpu/gsp/kernel_gsp.h`（只有一行 include）。

### 已记录的上游版本

| 上游 tag | commit | `GSP_FMC_BOOT_PARAMS` 布局 | 说明 |
|---|---|---|---|
| `620.73.04` | `a4b95f6a2db0bba10ccaccef79190a3c18dcf14a` | 256 B，带 `magic` | 2026-11-23 时的最新 tag，建议优先钉版 |
| `615.71.10` | `6f3be8c9c3fb4ed6ffc607a0d32d31ce4db7fba5` | 256 B，带 `magic` | 社区 GB206 样本所用驱动 |
| `610.57.04` | `e4a5faa2567f28c8eabe0ebb6422b6d0abcf37eb` | 88 B，无 `magic` | 旧布局末代 |
| `570.124.04` | `129479b1b7345f0055016ab3aa42ae78a4ca2048` | 80 B，无 `magic` | `GspFwWprMeta` 由 `kgspCalculateFbLayout_GH100` 填写 |

610 与 615 之间布局不兼容，同一份按偏移取值的补丁**不能**跨越这条线。补丁应按字段名打印，或整块十六进制输出后交给 `scripts/abi-layout.py --decode` 按 tag 解码。

### 日志约定

- 只打印长度、偏移、标志与状态码；`hash384`、`publicKey`、`signature` 只打印长度。
- 系统内存物理地址会暴露本机内存布局，补丁只输出低 12 位；改动须在私有副本上做，不得触碰交给固件的结构体。
- 每行以 `BWLAB:` 开头。整块输出为 `BWLAB: begin <结构体> tag=<tag> size=<字节>`、若干 `BWLAB: <偏移>: <16 字节>` 与 `BWLAB: end <结构体>`，`abi-layout.py --from-log` 据此解码，缺头、缺行、乱序、长度与该 tag 布局不符都记为不完整（退出码 3），其余完整的块照常解码。

---

## 应用与回滚流程（原生 Linux）

可复制粘贴的完整流程（打补丁 → 编译 → 卸载 → 加载 → 采集 → 回滚）见根目录 [README 实机操作说明](../README.cn.md#四路线-1-原生-linux-实机操作)。只撤销补丁：

```bash
cd "$HOME/ogkm-620.73.04"
git apply -R /path/to/blackwell-abi-lab/patches/620.73.04-01-boot-abi-trace.patch
make modules -j"$(nproc)"
```

采集日志后请使用 `scripts/collect-report.sh` 统一脱敏，并对照 [docs/abi-invariants.md](../docs/abi-invariants.cn.md) 中的编号登记观测结果。
