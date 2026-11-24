[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.cn.md)

# Contributing

We welcome measurements from physical GPUs, fixes to the read-only tools and corrections to the documentation. Userspace operator benchmarks are also in scope.

---

## 1. Files and data to exclude

- Full upstream source files. Submit minimal `.patch` files against a pinned `open-gpu-kernel-modules` tag.
- Firmware, raw VBIOS/PROM bytes, decrypted data, keys, signatures and per-object digests. Record decoded scalars and lengths. The ROM survey uses a whole-file SHA-256 to identify samples.
- GPU or board serials, UUIDs, MAC or IP addresses, hostnames, usernames, disk and partition inventories, unrelated PCI devices and PnP instance identifiers.

Read sanitized files before submitting them. CI checks common identifiers in `tests/test_repo_consistency.py`.

## 2. Submit an observation

1. Create `reports/<lowercase-die>-<device-id>/`, for example `reports/gb203-2c02/`.
2. Fill in the [report template](reports/template.md). Use `unknown` for unknown values and `—` for fields you did not sample.
3. Attach the evidence used in the report.

   | Evidence | Suggested filename | Source |
   |---|---|---|
   | Registers and PROM metadata | `bar0-prom-metadata-<driver>.txt` | Read-only diagnostics, with host inventory removed |
   | Clamp evaluation | `clamp-eval-<driver>-n<N>.json` | `software/clamp_eval.py --json <file>` |
   | K-chunk sweep | `chunked-fp16-<driver>.json` | `software/chunked_fp16_matmul.py --json <file>` |
   | V6 scalars | `v6-descriptors-<version>.json` | `scripts/rom-survey.py <directory> --v6-dir <output-dir>` adds a 12-character SHA-256 prefix to distinguish different ROMs with the same version. |
   | ROM object directory | `rom-directory-<version>.json` | `scripts/rom-directory.py <local-rom> --export <file>` |
   | Cross-card ROM survey | `rom-survey-<date>.json` | `scripts/rom-survey.py <directory> --export <file>`. See the [source notes](docs/references.md). |
   | Route 1 boot trace | `dmesg-gsp-fmc-<driver>-<boot-state>.txt` | Trace patch and `scripts/collect-report.sh` |

4. Add the measurements and their source to the [test matrix](docs/test-matrix.md).

Use the version number in the filename. The ROM-dump pattern in `.gitignore` matches filenames containing `vbios`.

Run the checks relevant to your capture, followed by the test suite.

```bash
python3 scripts/parse-ssdiag.py reports/gb205-2f18/bar0-prom-metadata-621.01.txt
python3 scripts/v6-closure.py reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json
python3 -B -m unittest discover -s tests -v
```

Record `DIFF` results. They identify a GB205 observation that did not hold for the supplied sample.

## 3. Evidence levels

Label each conclusion and provide the information needed to check it.

| Evidence | Meaning | Include |
|---|---|---|
| Hardware measurement | Readback from your own card | Sanitized capture, driver, VBIOS and date |
| Static source analysis | Upstream code or headers | Tag and source path |
| Community measurement | A published third-party capture | Source. Keep conflicting samples side by side. |
| Hypothesis | An interpretation awaiting a test | The observation that would refute it |

Record the commit and license for third-party sources in the [reference register](docs/references.md). Cite format facts from GPL-3.0 or unlicensed projects. Keep their code out of this GPL-2.0-only repository.

If both driver and boot state changed, report both. Agreement between two values establishes a numerical relationship only.

## 4. Code conventions

- Use Python 3.9-compatible syntax. CI checks the AST and runs tests on 3.11 and 3.13. Use the standard library outside `software/`. Import `torch` and `pynvml` lazily so offline tests also run without them.
- Add `SPDX-License-Identifier: GPL-2.0-only` to source files.
- Use ASCII for console output on Windows.
- Keep shell collectors read-only and run `bash -n` and `shellcheck --severity=warning`.
- Use LF line endings as specified in `.gitattributes`.
- Include a regression test with each bug fix.
- Name patches `<tag>-<number>-<purpose>.patch`. Include `Status`, `Tag` and `Commit` headers. Add logging lines only. Mark patches awaiting hardware validation as `RFC`.

## 5. Documentation and data

Keep English in `name.md` and Chinese in `name.cn.md`, with reciprocal links at the top. Update both when a command, measurement or evidence claim changes. CI checks links and reconciles published tables with JSON, register captures and decoded V6 scalars.
