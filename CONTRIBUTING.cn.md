[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.cn.md)

# 贡献指南

欢迎提交通用工具修复、文档勘误、真机只读观测和用户态算子基准。

---

## 1. 绝不入库的内容

- 上游 `open-gpu-kernel-modules` 的完整源文件（补丁只能是针对固定 tag 的最小 `.patch`）。
- NVIDIA 固件、VBIOS/PROM 原始字节、解密产物、密钥、签名和对象摘要。描述符只记录解码后的标量与长度。ROM 普查用整份镜像的 SHA-256 标识样本。
- 能识别机器或个人的信息：GPU / 主板序列号、UUID、MAC、IP、主机名、用户名、磁盘与分区清单、无关 PCI 设备、PnP 实例 UID。

`tests/test_repo_consistency.py` 会在 CI 中拦截常见情形，但它只是兜底，提交前仍需人工通读。

## 2. 提交观测报告

1. 在 `reports/` 下建目录，命名为 `<die 小写>-<设备号>`，例如 `gb203-2c02`。
2. 按 [reports/template.md](reports/template.cn.md) 填写 `README.md`。未知项写 `unknown`，未采项写 `—`，**不要**用其他核心的数据补空。
3. 附原始证据，文件名带驱动版本：

   | 内容 | 建议文件名 | 生成方式 |
   |---|---|---|
   | 寄存器与 PROM 元数据 | `bar0-prom-metadata-<驱动>.txt` | 只读诊断工具输出，删去主机清单 |
   | 钳制评估 | `clamp-eval-<驱动>-n<N>.json` | `software/clamp_eval.py --json` |
   | K 分块扫描 | `chunked-fp16-<驱动>.json` | `software/chunked_fp16_matmul.py --json` |
   | V6 描述符标量 | `v6-descriptors-<VBIOS 版本>.json`（同版本的不同 ROM 加 SHA-256 前 12 位） | `scripts/rom-survey.py <目录> --v6-dir`，schema 同 `gb205-2f18` |
   | ROM 对象目录 | `rom-directory-<VBIOS 版本>.json` | `scripts/rom-directory.py <本地 ROM> --export`，ROM 本身不入库 |
   | 跨卡 ROM 比对 | `rom-survey-<日期>.json` | `scripts/rom-survey.py <目录> --export`，来源限制见 [docs/references.md](docs/references.cn.md) 第 3 节 |
   | 路线 1 启动日志 | `dmesg-gsp-fmc-<驱动>-<冷启/重载/恢复>.txt` | 日志补丁 + `scripts/collect-report.sh` |

4. 在 [docs/test-matrix.md](docs/test-matrix.cn.md) 填入对应列，并注明证据来源。

文件名不要包含 `vbios` 字样，`.gitignore` 会把它当作 ROM 转储拦下。

以下命令使用仓库内的示例。核对自己的采集时，请替换文件路径。

```bash
python3 scripts/parse-ssdiag.py reports/gb205-2f18/bar0-prom-metadata-621.01.txt
python3 scripts/v6-closure.py reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json
python3 -B -m unittest discover -s tests -v
```

`v6-closure.py` 报 `DIFF` 不代表提交有误，它只说明 GB205 的某条规律在你的卡上不成立，这正是需要登记的信息。

## 3. 证据分级

每条结论都要能归入以下一级，并写明是哪一级：

| 级别 | 含义 | 要求 |
|---|---|---|
| 真机实测 | 本机读回 | 附原始文件、驱动与 VBIOS 版本、采集日期 |
| 公开源码静态阅读 | 上游头文件或源码 | 注明 tag 与文件路径 |
| 公开社区样本 | 他人公布的读回 | 注明来源；与现有样本冲突时并列，不覆盖 |
| 待验证假设 | 推断 | 明确写“待验证”，并给出能证伪它的观测 |

引用第三方项目时，先核对其 commit 与许可证并登记到 [docs/references.md](docs/references.cn.md)。GPL-3.0 或无许可证的代码不得复制进本仓库，只能引用事实。

结论力度不要超过证据：两次采集之间同时变了多个条件（例如驱动和冷启动），就不能归因于其中一个。数值相符不等于字段含义已确定。

## 4. 代码约定

- 语法保持 Python 3.9 兼容（测试会用 `ast` 检查，CI 实跑 3.11 与 3.13），仅用标准库；`torch` 与 `pynvml` 只允许在 `software/` 中使用，且必须延迟导入，保证无 GPU、无 PyTorch 的环境能跑完测试。
- 每个源文件带 `SPDX-License-Identifier: GPL-2.0-only`。
- 控制台输出保持 ASCII，避免 Windows 默认代码页乱码。
- Shell 脚本须通过 `bash -n` 与 `shellcheck --severity=warning`，并保持只读。
- 换行统一 LF（见 `.gitattributes`）。
- 修缺陷时补一条能复现它的测试。
- 补丁文件命名 `<tag>-<序号>-<用途>.patch`，文件头须有 `Status`、`Tag`、`Commit`，只许新增日志行；未在真机跑过的标 `RFC`。

## 5. 文档与数据一致性

英文使用 `name.md`，中文使用 `name.cn.md`，页首互链。命令、测量值或证据结论有变化时，请同步更新两版。CI 会核对相对链接、报告表与 JSON、矩阵与寄存器原文，以及 V6 文档与机读标量。
