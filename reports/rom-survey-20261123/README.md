[English](README.md) | [中文](README.cn.md)

# GB20x ROM survey, 23 November 2026

Twelve ROMs cover five dies. The archive records object IDs, offsets, sizes, decoded scalars and letters grouping identical contents. The table gives the input order used for all group comparisons.

| Die | Board | PCI ID | VBIOS | `adjust` | Objects | Decoded scalars |
|---|---|---|---|---|---:|---|
| GB202 | ASUS RTX 5090 | `10de:2b85` | `98.02.2E.80.C8` | `0x50000` | 155 | [v6-descriptors-98.02.2E.80.C8.json](v6-descriptors-98.02.2E.80.C8.json) |
| GB202 | ASUS RTX 5090 D | `10de:2b87` | `98.02.31.40.4C` | `0x50000` | 155 | [v6-descriptors-98.02.31.40.4C.json](v6-descriptors-98.02.31.40.4C.json) |
| GB202 | PNY RTX PRO 6000 | `10de:2bb1` | `98.02.52.00.02` | `0x4d800` | 153 | [v6-descriptors-98.02.52.00.02.json](v6-descriptors-98.02.52.00.02.json) |
| GB203 | ASUS RTX 5080 | `10de:2c02` | `98.03.3B.C0.3E` | `0x50000` | 155 | [v6-descriptors-98.03.3B.C0.3E.json](v6-descriptors-98.03.3B.C0.3E.json) |
| GB203 | ASUS RTX 5070 Ti | `10de:2c05` | `98.03.3B.80.2F` | `0x50000` | 155 | [v6-descriptors-98.03.3B.80.2F.json](v6-descriptors-98.03.3B.80.2F.json) |
| GB203 | ASUS RTX 5070 Ti | `10de:2c05` | `98.03.58.40.B6` | `0x4d400` | 155 | [v6-descriptors-98.03.58.40.B6.json](v6-descriptors-98.03.58.40.B6.json) |
| GB205 | ASUS RTX 5070 | `10de:2f04` | `98.05.28.00.64` | `0x50400` | 152 | [v6-descriptors-98.05.28.00.64.json](v6-descriptors-98.05.28.00.64.json) |
| GB205 | Palit RTX 5070 | `10de:2f04` | `98.05.28.00.1D` | `0x50400` | 152 | [v6-descriptors-98.05.28.00.1D.json](v6-descriptors-98.05.28.00.1D.json) |
| GB205 | RTX 5070 Ti Laptop (local) | `10de:2f18` | `98.05.31.00.44` | `0x4d400` | 155 | [gb205-2f18](../gb205-2f18/v6-descriptors-98.05.31.00.44.json) |
| GB206 | MSI RTX 5060 Ti | `10de:2d04` | `98.06.1F.00.CD` | `0x4d400` | 152 | [v6-descriptors-98.06.1F.00.CD.json](v6-descriptors-98.06.1F.00.CD.json) |
| GB206 | ASUS RTX 5060 | `10de:2d05` | `98.06.21.40.42` | `0x4d400` | 152 | [v6-descriptors-98.06.21.40.42.json](v6-descriptors-98.06.21.40.42.json) |
| GB207 | Gigabyte RTX 5050 | `10de:2d83` | `98.07.1D.00.55` | `0x4d800` | 150 | [v6-descriptors-98.07.1D.00.55.json](v6-descriptors-98.07.1D.00.55.json) |

- Identify each file by the full SHA-256 in [rom-survey.json](rom-survey.json).
- The laptop ROM came from the local GB205. The repository owner supplied eleven other files with names resembling public VBIOS collection entries. Their provenance is unverified. All object hashes and directory digests matched. The survey did not verify signatures or establish that these are stock images.
- The [V6 analysis, §6.11](../../docs/vbios-v6-descriptor.md) gives the measurements and interpretation.

## Findings

| Question | Observation |
|---|---|
| Do the descriptor lengths close? | `80 + hi16(+18) × lo16(+1C) + stored_size` equals the object size for **72/72** descriptors. |
| Do type 1 entry IDs match directory IDs? | **132/132** start at the same-ID object. Sizes match in 120 cases. The other 12 use a prefix of `0x14`. |
| What predicts EFI payload groups? | The `98.03.3B` 5070 Ti grouped with the `98.03.3B` 5080 and `98.02.2E` 5090 as predicted. The `98.03.58` 5070 Ti grouped separately. GB202 and GB203 each span two groups. |
| Does APP `0x24` follow the EFI grouping? | The 5090 D refuted the eight-ROM hypothesis. The 5090 D, PRO 6000 and 5070 each have a distinct APP `0x24` despite sharing EFI payloads with other cards. |
| Are all `0x2B80` images identical? | EFI, APP `0x24` and APP `0x18` each have cross-die matches. None is identical across all five dies. |
| How do the ASUS and Palit desktop 5070s compare? | The two `98.05.28.00` builds have identical descriptors, payloads, tails and both manifests. Of 140 board objects, 17 differ. Thirteen also differ between the 5080 and 5070 Ti. |
| Does the die determine the ucode payload? | All six payloads differ between the desktop and laptop GB205. Their EFI payload matches. |
| Do PRO or 5090 D have distinctive V6 structure? | The compared object IDs, slots, APPs, flags and constants match. Product and version both vary, so the payload differences cannot be attributed to either alone. |
| Which objects differ only on the laptop? | `0x5f`, `0x61`, `0x62`, `0x65`, `0x7d` and `0xe4` match across all eleven desktop/workstation ROMs, including desktop GB205. Mobile platform, product and `98.05.31` remain possible explanations. |
| Where are `0x7a`, `0x7b` and `0x7c` missing? | Desktop GB205, GB206 and GB207 lack these IDs. The laptop GB205 has longer objects at those IDs. |
| What does the 28-byte tail track? | Fifty-one distinct payloads correspond to 51 distinct tails in the per-object comparisons. Some manifest sources change while the tail remains identical. The digest ranges tested did not explain it. |
| Is `desc+08` the base of T? | That interpretation fails. The value equals APP `0x18` field `+0C` in all twelve samples. |
| What does outer `+00C` mean? | Unknown. Objects `0x52`, `0x5c`, `0x74` and `0x76` partition all twelve ROMs into the same two groups as this field. A new ROM can test that association. |

## Reproduce

```bash
python3 scripts/rom-survey.py /path/to/roms --export rom-survey.json --v6-dir v6
python3 scripts/v6-closure.py reports/rom-survey-20261123/v6-descriptors-98.02.2E.80.C8.json
```

Group letters follow input order, shown in the specimen table.

- Identical files count once. The tool reports and skips duplicates.
- `ok` requires all object hashes, directory digests and structural checks to pass. Directory digest states are `verified`, `mismatch` and `absent`. `hashes_failed` lists object IDs that failed verification.
- `v6_file` identifies each scalar file. Different ROMs with the same VBIOS version receive a 12-character SHA-256 suffix. The laptop record lives in the [GB205 report](../gb205-2f18/README.md). The other eleven records are in this directory.
