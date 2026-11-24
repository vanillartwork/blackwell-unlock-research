[English](recovery.md) | [中文](recovery.cn.md)

# 引导故障、恢复与样本分类

遇到 FMC 超时、`ioctl` 挂起或异常返回码时，请先保存日志，完全断电冷启动后再继续路线 1。见 [ABI 观测清单](abi-invariants.cn.md)。

本仓库的脚本**不执行任何复位**。

---

## 1. 症状对照表

日志串与含义核对自 `open-gpu-kernel-modules` `620.73.04` 源码；邮箱规则与 `nova-core` 的实现一致。

| 日志或现象 | 来源 | 含义 | 需要记录 |
|---|---|---|---|
| `unexpected WPR2 already up, cannot proceed with booting GSP` | `gsp/kernel_gsp.c` | 启动 GSP 前 WPR2 已建立，驱动主动放弃并返回 `NV_ERR_INVALID_STATE`。通常说明上一轮没有清理干净 | 两个地址的 `WPR2_LO/HI`、上一轮的卸载日志 |
| `FSP response reported error. Task ID … Command type … Error code …` | `fsp/arch/hopper/kern_fsp_gh100.c` | FSP 拒绝了消息。`Command type` `0x14` = COT，`0x13` = PRC | 三个数值原样记录；公开头文件只定义了少数错误码，其余不要猜 |
| `FSP boot cmds failed. RM cannot boot.` | 同上 | COT 发送或应答失败，前面会打印 COT 内容 | 整段日志 |
| `Fatal GSP-FMC Error: version=… partition=… error code=… additional info=…` | `gpu/arch/hopper/gpu_gsp_fmc_error_reporting_gh100.c` | FMC 已运行并报告致命错误 | 四个数值 |
| `Fatal GSP-FMC Error: SK error code =… phase …` | 同上 | FMC 报告的另一类错误格式 | 两个数值 |
| GSP `MAILBOX0` 非零 | `_kgspLockdownReleasedOrFmcError`；nova-core `gsp/hal/gh100.rs` | 若 `MAILBOX1:MAILBOX0` 等于启动参数地址，说明 FMC **尚未读取**，不是错误；其他非零值才是 FMC 错误码（上游注释：实际只有 8 位，0 为无错误） | 两个邮箱的值 |
| `Possible bad register read … regvalue: 0xbadf41xx` | `gpu/arch/hopper/kern_gpu_gh100.c` | `0xbadf41xx` 是 GSP 寄存器仍被 PRIV target mask 锁给 FSP 时的读回（nova-core `falcon/gsp.rs`），此时读到的 GSP 寄存器没有意义 | 寄存器地址与值；不要当作寄存器内容 |
| `… heartbeat timed out`、Xid 109 / 154 | `gsp/kernel_gsp.c` | 请结合工作负载与前后日志判断运行期故障。仅凭 Xid 不能归因于引导 ABI。社区讨论见上游 issue [NVIDIA/open-gpu-kernel-modules#1080](https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1080) | 工作负载、Xid 全文 |

---

## 2. 恢复手段对比

| 手段 | 影响范围 | 主要风险 | 之后能否算冷启动 |
|---|---|---|---|
| 完全断电 | 整机 | 关机后按设备厂商的程序切断供电，尤其是带内置电池的笔记本 | 确认 GPU 已掉电时可以 |
| 普通重启 | 整机 | 显卡可能不掉电，FSP / WPR2 状态可能保留 | 不能，登记为“热重启” |
| 内核 `reset`（`reset_method` 列出的 `flr` / `bus` 等） | 该功能或其总线 | 总线上有别的功能（例如显卡自带的音频）时，内核通常会拒绝 `bus` 方式 | 不能 |
| 内核 `reset_subordinate`（桥设备，2024 年起） | **桥下全部设备** | 会波及同一桥下的其他设备 | 不能 |
| 第三方 SBR 脚本（直接写桥控制寄存器） | 桥下全部设备 | 见第 3 节 | 不能 |

`scripts/check-env.sh` 会只读列出每张 NVIDIA 卡的 `reset_method`、上游桥，以及桥下还有哪些设备；桥下有非本卡设备时给出警告。

---

## 3. 对社区 SBR 脚本的核查

[gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery](https://github.com/gengchaogit/blackwell-xid-wpr2-gsp-crash-recovery)（核查 commit `d744382a58e9`，2026-07）确实存在，思路是卸载驱动、移除 NVIDIA 设备、对上游桥做 Secondary Bus Reset、再 FLR 并重扫总线，以清除残留的 WPR2。作者提供了成功日志；**本仓库没有复现过**。

核查记录如下。

1. **没有许可证**，不能复制或改写其代码。
2. **先杀后清**：停止桌面与 `docker` / `containerd`，对占用 `/dev/nvidia*` 的进程执行 `fuser -k -9`，未保存的工作会丢失。
3. **对全部 NVIDIA 卡生效**，没有只处理故障卡的选项；也不检查桥下是否有其他设备。
4. 直接用 `setpci` 写桥控制寄存器，绕过内核 PCI 层的配置保存与恢复；多处失败被 `|| true` 忽略。
5. 依赖写死的 `/root/native_resize_bar.sh`，并会改 BAR 大小。
6. 与本仓库“默认零写路径”和“断电冷启动”两条红线冲突。把它接进观测循环，等于允许在同一会话里反复重试，样本也会失去冷启动前提。

因此它的定位是：**观测已经结束、日志已经保存之后**，在自己的机器上恢复日常使用的一个可选办法。若要用复位，优先考虑内核自带的接口，并先看 `check-env.sh` 给出的影响范围。

---

## 4. 证据规则

- 断电冷启动之外的任何恢复方式之后采集的数据，一律标 `POST_RESET_SAMPLE`，只作旁证，**不得**计入冷启动基线，也不得与冷启动样本做 `WPR2` 差分。
- 复位之后如果 `WPR2_HI` 回到 0，只能说明该寄存器被清零，**不能**推出 FSP、FMC 或熔丝影子态已回到冷启动状态。
- 使用过复位的机器，在下一次断电冷启动之前不再继续路线 1 的任何观测。
- 报告里写清：故障日志、使用的恢复方式、恢复后是否断过电。
