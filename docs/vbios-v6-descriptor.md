[English](vbios-v6-descriptor.md) | [中文](vbios-v6-descriptor.cn.md)

# GB205 Blackwell V6 firmware descriptors

- **Date**: 2026-11-22
- **Target**: NVIDIA GeForce RTX 5070 Ti Laptop GPU (PCI `10de:2f18`, SUBSYS `1558:6586`)
- **VBIOS**: 98.05.31.00.44
- **Die**: GB205 (see specimen provenance in §1).
- **Sources**: Full static ROM export and an earlier read-only ssdiag v0.8.5 capture.
- **Scope**: Check observations from the original four descriptors against all six nonempty entries in the local ROM, then compare them across twelve ROMs.

---

## 1. Specimens

The archived capture identifies PCI 10de:2f18, subsystem 1558:6586, VBIOS 98.05.31.00.44. PMC_BOOT_0 is 0x1b5000a1. Its upper 12 bits give the GB205 chipset ID 0x1b5. ssdiag reports the broader family blackwell-gb2 (reproduced identically on driver 621.01 in [bar0-prom-metadata-621.01.txt](../reports/gb205-2f18/bar0-prom-metadata-621.01.txt)).

The full ROM is 1,961,983 bytes, SHA-256 `06f94e60c400414cade6f28ebd8c4c2536fd66b5ef22ad094d9f41bea0e2813d`. Its checksummed BIT directory is at 0x361F0. The containing legacy image starts at 0x35400; the following EFI image contributes 0x18000 bytes to the extended-pointer translation. The six descriptors below account for all nonempty entries of the 35-slot ucode table. Four were visible in the original 1 MiB capture. The other two are readable in the full file. These observations cover one VBIOS revision; §6.11 repeats them on eleven further ROMs, so that every GB20x die is represented.

| APP | Target | File offset | Version | Header (bytes) | Stored size (bytes) | Flags |
| --- | --- | --- | --- | --- | --- | --- |
| 0x07 | 0x6 | 0xADCD4 | 6 | 80 | 132,608 | 0x1D |
| 0x19 | 0x1 | 0xCE324 | 6 | 80 | 134,912 | 0x5 |
| 0x15 | 0x1 | 0xEF364 | 6 | 80 | 21,760 | 0x5 |
| 0x23 | 0xE | 0xF48F0 | 6 | 80 | 159,744 | 0x1F |
| 0x18 | 0x1 | 0x11BF04 | 6 | 80 | 49,920 | 0x1F |
| 0x24 | 0xF | 0x128904 | 6 | 80 | 175,104 | 0xD |

---

## 2. V6 Field Map (80 Bytes)

Offsets below are hexadecimal and relative to each descriptor. Values through +33 are decoded little-endian scalars. Section 2.4 summarizes the remaining 28 bytes without reproducing them.

### 2.1 Header word (+00)

All six headers have version 6 and size 80. Bits 0 and 2 are set in all six flags. Bit 1 is set on APP 0x18 and 0x23. Bits 3 and 4 are both set on 0x07, 0x18 and 0x23; only bit 3 of this pair is set on 0x24. Neither bit is set on 0x19 or 0x15. The sample alone does not establish an algorithm or an exhaustive class definition.

For comparison, the public header-word definition (`src/nvidia/src/kernel/gpu/vbios/kern_vbios.c`, tag 620.73.04) assigns bit 0 = version available, bit 1 = reserved, bit 2 = encrypted, bits 7:3 = reserved, bits 15:8 = version and bits 31:16 = descriptor size. It names versions 1 to 4 only and parses V2 and V3, so V6 and the meaning of bits 1, 3 and 4 are not covered by public source.

### 2.2 Structural Fields (+04 to +27, 36B)

| Offset | Size | V2 equivalent | V3 equivalent | APP 0x07 | APP 0x19 | APP 0x15 | APP 0x23 | APP 0x18 | APP 0x24 | V6 observation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| +04 | 4B | stored_size | stored_size | 0x20600 | 0x20F00 | 0x5500 | 0x27000 | 0xC300 | 0x2AC00 | Declared stored size in bytes |
| +08 | 4B | uncompressedSize | pkcDataOff | 0x0 | 0x8800 | 0x8800 | 0x0 | 0x0 | 0x15800 | On 0x19 and 0x15 equals +0C of APP 0x18 on all twelve ROMs (§6.11.3); meaning open |
| +0C | 4B | virtualEntry | interfaceOff | 0x1DA00 | 0x1BA00 | 0x4C00 | 0x1D000 | 0x8800 | 0x26C00 | First size term in §2.2.1 |
| +10 | 4B | interfaceOff | imemPhys | 0x0 | 0x3800 | 0x3800 | 0x0 | 0x0 | 0x1400 | On 0x19 and 0x15 equals +14 of APP 0x18 plus 0x2600 on all twelve ROMs (§6.11.3); meaning open |
| +14 | 4B | imemPhys | imemLoad | 0x1100 | 0x5500 | 0x900 | 0x8800 | 0x1200 | 0x4000 | Second size term in §2.2.1 |
| +18 | 4B | imemLoad | imemVA | 0x200 | 0xC0001 | 0x30001 | 0x0 | 0x40101 | 0x301 | High half = row count of the area after the descriptor, confirmed 6/6 (§6.10.2) |
| +1C | 4B | imemVA | dmemPhys | 0x0 | 0x14 | 0x14 | 0x0 | 0x1010014 | 0x1030000 | Low half = row stride of that area, confirmed 6/6 (§6.10.2); high half unconfirmed |
| +20 | 4B | imemSecBase | dmemLoad | 0x1100 | 0x0 | 0x0 | 0xE00 | 0x1F00 | 0x0 | Third size term where nonzero |
| +24 | 4B | imemSecSize | engineId+sig | 0x0 | 0x0 | 0x0 | 0x0 | 0x0 | 0x0 | Observed scalar; purpose unconfirmed |

### 2.2.1 Stored-size accounting across six descriptors

| APP | +0C + +14 + +20 | stored_size | Residual | +18 high half | Class A outer word after header |
| --- | --- | --- | --- | --- | --- |
| 0x07 | 0x1DA00 + 0x1100 + 0x1100 | 0x20600 | 0xA00 | 0 | differs |
| 0x19 | 0x1BA00 + 0x5500 + 0x0 | 0x20F00 | 0x0 | 12 | matches |
| 0x15 | 0x4C00 + 0x900 + 0x0 | 0x5500 | 0x0 | 3 | matches |
| 0x23 | 0x1D000 + 0x8800 + 0xE00 | 0x27000 | 0xA00 | 0 | differs |
| 0x18 | 0x8800 + 0x1200 + 0x1F00 | 0xC300 | 0xA00 | 4 | differs |
| 0x24 | 0x26C00 + 0x4000 + 0x0 | 0x2AC00 | 0x0 | 0 | differs |

The two original Class A specimens (0x19, 0x15) share a length equality and a recognizable secondary manifest. APP 0x18 shares their target but has the 0xA00 residual, a nonzero third size term, and a different post-descriptor word. Its high-half value of four does give a four-row, 80-byte area after the descriptor (§6.10.2), but the content of that area is not a Class A manifest. APP 0x24 has the two-term equality, a different flag pattern, and no matching Class A outer word. Its residual is zero. The two size patterns divide the sample three to three; a size equality alone does not identify the post-header format.

### 2.3 Constant and Per-Image Fields (+28 to +33, 12B)

| Offset (4-byte scalar) | APP 0x07 | APP 0x19 | APP 0x15 | APP 0x23 | APP 0x18 | APP 0x24 | Observation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| +28 | 0x01012801 | 0x01012801 | 0x01012801 | 0x01012801 | 0x01012801 | 0x01012801 | Same value in 6/6; meaning open |
| +2C | 0x5000000 | 0x4000000 | 0x4000000 | 0x4F4F0000 | 0x4000000 | 0x50534600 | Varies by APP; holds the start of the printable tag on 0x23 and 0x24 (§4) |
| +30 | 0x11000000 | 0x12000000 | 0x11000000 | 0x42554842 | 0x2000000 | 0x4E55525F | Holds the end of the printable tag on 0x23 and 0x24 (§4) |

### 2.4 Opaque tail (+34 to +4F, 28 bytes)

All six tails differ from one another. Empirical Shannon entropy over each individual 28-byte sample is shown in bits per byte. Its upper bound for this sample size is log2(28), about 4.807. These figures and the distinctness check do not identify the field's function. Across twelve ROMs the tail follows the payload one to one (§6.11.4).

| APP | 0x07 | 0x19 | 0x15 | 0x23 | 0x18 | 0x24 |
| --- | --- | --- | --- | --- | --- | --- |
| Sample entropy | 4.664 | 4.807 | 4.522 | 4.664 | 4.495 | 4.664 |

---

## 3. Class patterns in the original four specimens

### 3.1 Class A — Target 0x01 (Apps 0x19, 0x15)

- Minimal vDesc flags (0x05)
- +08 and +10 are nonzero. Their meanings are unknown.
- +1C equals 0x14 in both Class A samples. That is also the manifest row stride, and the high half of +18 is the row count, so `(+18 >> 16) × (+1C & 0xFFFF)` reproduces both manifest lengths (12 × 20 = 240, 3 × 20 = 60). APP 0x18 also has 0x14 in the low half of +1C (0x1010014). The same product gives 4 × 20 = 80 bytes there, and the hash-verified object boundary confirms it (§6.10.2), although those rows do not carry the Class A marker.
- The high half of +18 agrees with the manifest row count in these two specimens.
- **Plaintext secondary header** in image data

The secondary headers in these two samples have the same first two decoded words. Other APPs are compared separately in §2.2.1.

Speculative decode of secondary header:
- +0: u16 = 0x0001 (format version or magic low word)
- +2: u16 = 0x0035 (53 — semantics unknown; NOT the header byte count or entry count)
- +4: u32 = 0x000047a7 (18343, constant across both specimens)
- +8: u32 = varies (0x189e0 vs 0x58c0 — correlated with image content, not equal to stored_size)
- +C: u32 = 0x000004bd (1213, constant across both specimens)

**Header length**: The total plaintext header = 0x14 + N × 0x14 bytes, where N = number of engine entries. The entry count N is NOT stored in the secondary header itself — it is derived from V6 descriptor field +18, whose high u16 gives the total row count (including the 20-byte outer header as row 0). See §6.1.

### 3.2 Class B — Targets 0x06, 0x0E (Apps 0x07, 0x23)

- Extended vDesc flags (0x1d, 0x1f — bits 3,4 set)
- +08 and +10 are zero. Their meanings are unknown.
- +1C is zero. Its meaning is unknown.
- Segment C size (+20) is nonzero.
- The Class A secondary-header marker was absent at the first word after both descriptors.

The sampled prefixes lack the Class A header. They do not establish a specific encryption algorithm.

---

## 4. Printable tags spanning +2C to +33

Read as eight bytes in file order, +2C..+33 hold a printable name on two of the six descriptors, padded with NUL on the left:

| APP | Bytes +2C..+33 (file order) | Printable reading |
| --- | --- | --- |
| 0x23 | 00 00 4F 4F 42 48 55 42 | `OOBHUB` |
| 0x24 | 00 46 53 50 5F 52 55 4E | `FSP_RUN` |
| 0x07 | 00 00 00 05 00 00 00 11 | none |
| 0x19 | 00 00 00 04 00 00 00 12 | none |
| 0x15 | 00 00 00 04 00 00 00 11 | none |
| 0x18 | 00 00 00 04 00 00 00 02 | none |

An earlier revision of this note read only the scalar at +30 and therefore reported the truncated tags `BHUB` and `_RUN`, with "OO" listed separately as an anomaly at +2C. The two fields are adjacent, and the name runs across them.

On the other four descriptors the same eight bytes look like two small big-endian values (for example 5 and 0x11 on 0x07), unlike the little-endian scalars elsewhere in the header. Whether the region is one eight-byte name or two four-byte fields is open. The spelling alone does not identify an engine or establish a class.

---

## 5. V6 vs V2 vs V3 Layout Comparison

```
Offset    V2 (60B, Turing)       V3 (44B, GA10x+)           V6 (80B, Blackwell)
------    ------------------     ----------------------     ------------------
+00       vDesc header           vDesc header               vDesc header (+ new flag bits 3,4)
+04       stored_size            stored_size                stored_size
+08       uncompressedSize       pkcDataOff                 unconfirmed
+0C       virtualEntry           interfaceOff               segment A size
+10       interfaceOff           imemPhys                   unconfirmed
+14       imemPhys               imemLoad                   segment B size
+18       imemLoad               imemVA                     row count (hi16), confirmed 6/6 (§6.10.2); lo16 open
+1C       imemVA                 dmemPhys                   row stride (lo16), confirmed 6/6 (§6.10.2); hi16 open
+20       imemSecBase            dmemLoad                   segment C size
+24       imemSecSize            engineId, ucodeId, sigCnt  zero in 6/6; purpose unconfirmed
+28       dmemOffset             sigVersions, reserved      CONSTANT 0x01012801 (semantics unconfirmed)
+2C       dmemPhys               beyond V3                  per-image bytes; printable name on 0x23/0x24 (§4)
+30       dmemLoad               —                          continuation of +2C (§4)
+34       altImemLoad            —                          28-byte opaque tail (+34..+4F)
+38       altDmemLoad            —                          opaque tail
+3C..+4F  beyond V2              —                          opaque tail (purpose unconfirmed)
```

V2 and V3 columns follow `FALCON_UCODE_DESC_V2` / `FALCON_UCODE_DESC_V3` in the public `kern_vbios.c` (tag 620.73.04).

V6 fundamentally reorganizes the descriptor: image segment sizes occupy +0C/+14/+20 (§2.2.1 conservation law), and the +28..+4F region spans 40 bytes — a constant dword at +28 followed by per-image fields and a 28-byte opaque tail. This replaces V3's trailing RSA3K signature area (which followed the 44-byte descriptor as variable-length data at pkcDataOff).

---

## 6. Class A secondary header and entry manifest

### 6.1 Structure Overview

Class A images (apps 0x19, 0x15) contain a plaintext secondary header immediately after the 80-byte V6 descriptor. This header is a **per-entry manifest** whose entries appear to describe firmware segments, though the exact field semantics (source vs destination, offset vs address) remain unconfirmed.

**Total header size** = 0x14 + N × 0x14, where N = number of entries.

**Entry count in the two recognized manifests.** For APP 0x19 and APP 0x15, multiplying the high half of +18 by 20 reproduces the observed manifest length. On APP 0x18 the same product is the length of the area after the descriptor (§6.10.2).
- APP 0x19: desc+18 = 0x000c0001 → high u16 = 12 → 12 × 0x14 = 0xF0 = 240 bytes ✓
- APP 0x15: desc+18 = 0x00030001 → high u16 = 3 → 3 × 0x14 = 0x3C = 60 bytes ✓
- Class B (0x07): desc+18 = 0x00000200 → high u16 = 0 → no matching Class A prefix ✓
- Class B (0x23): desc+18 = 0x00000000 → high u16 = 0 → no manifest ✓

The low u16 of field +18 (0x0001 for Class A, 0x0200/0x0000 for Class B) may encode a manifest type or format selector.

**Layout:**
```
+000  u32   header_word = 0x00350001 (constant across both specimens)
+004  u32   constant_a  = 0x000047a7 (constant across both specimens)
+008  u32   variable    (varies per image: 0x189e0 / 0x58c0)
+00C  u32   constant_b  = 0x000004bd (identical in both)
+010  u32   constant_c  = 0x00001100 (identical in both)
+014  [N × 20B]  entry table (N varies per image)
...   ciphertext begins immediately after last entry
```

Each entry spans 20 bytes (five 32-bit words). The start and extent relationships in §6.5 give the following interpretation.
```
+0   u16   type (0x0001 = standard, 0x0002 = alternate)
     u16   entry_id (per-entry identifier, unique within each manifest)
+4   u32   field_A — start in S (type 1) or in T (type 2)
+8   u32   field_B — start position in space T, type 1; size in space T, type 2 (§6.5.2)
+C   u32   field_C — extent in space S (§6.5.1); zero for type 2
+10  u32   field_D — extent in space T (§6.5.2); zero for type 2
```

Type 1 has measured start/extent pairs in S and T. Type 2 has a start/extent pair in T only. The physical backing of T remains unknown (§6.9.2).
- **field_A** can exceed the parent APP's stored_size (e.g., entry_id 0xd2 in APP 0x15: field_A=0x1936d vs stored_size=0x5500). Space S spans both APPs (§6.5.1 cross-image chain).
- **field_C and field_D** are typically close in value (Δ = 0, +1, or +4); both are zero for type 2 entries.
- **Cross-image identity**: entry_id 0xd2 appears in both APPs with identical field_A, field_C, and field_D values, but different field_B values (§6.4).

### 6.2 APP 0x19 Manifest (11 Entries, 240 Bytes Plaintext)

| # | Entry ID | Type | field_A | field_B | field_C | field_D | C↔D Δ |
|---|----------|------|---------|---------|---------|---------|-------|
| 0 | 0x89 | 1 | 0x55279 | 0x8d00 | 0xad74 | 0xad74 | 0 |
| 1 | 0x12 | 1 | 0x3ec78 | 0x19ae0 | 0x528 | 0x52c | +4 |
| 2 | 0x14 | 1 | 0x26787 | 0x1a010 | 0x1cf1 | 0x1cf5 | +4 |
| 3 | 0x02 | **2** | 0x16570 | 0x2470 | 0 | 0 | — |
| 4 | 0x03 | **2** | 0x16500 | 0x70 | 0 | 0 | — |
| 5 | 0x81 | 1 | 0x4327c | 0x1be30 | 0x3c | 0x40 | +4 |
| 6 | 0x82 | 1 | 0x432b8 | 0x1bd10 | 0x10d | 0x111 | +4 |
| 7 | 0x70 | 1 | 0x1f554 | 0x140a0 | 0x184 | 0x184 | 0 |
| 8 | 0x71 | 1 | 0x1f6d8 | 0x13e58 | 0x244 | 0x244 | 0 |
| 9 | 0x72 | 1 | 0x1f91c | 0x14228 | 0x887 | 0x888 | +1 |
| 10 | 0xd2 | 1 | 0x1936d | 0x158e0 | 0xc0f | 0xc13 | +4 |

Ciphertext begins at header offset +0xF0 (240 bytes from image start).

Note: the field_A values range from 0x16500 to 0x55279 and mix two spaces: T for the type 2 entries, S for the others (§6.5). The reference space is NOT the individual APP's encrypted payload — several field_A values exceed APP 0x19's stored_size (0x20f00).

### 6.3 APP 0x15 Manifest (2 Entries, 60 Bytes Plaintext)

| # | Entry ID | Type | field_A | field_B | field_C | field_D | C↔D Δ |
|---|----------|------|---------|---------|---------|---------|-------|
| 0 | 0x8a | 1 | 0x5ffed | 0x4100 | 0x790 | 0x790 | 0 |
| 1 | **0xd2** | 1 | **0x1936d** | 0x69c0 | **0xc0f** | **0xc13** | +4 |

Ciphertext begins at header offset +0x3C (60 bytes from image start).

Note: Both field_A values exceed APP 0x15's stored_size (0x5500): entry 0x8a has field_A=0x5ffed, entry 0xd2 has field_A=0x1936d. This confirms field_A cannot be an intra-image offset.

### 6.4 Cross-Image Entry Correlation

**Entry ID 0xd2** appears in both manifests with partially identical field values:

| Field | APP 0x19 (entry 10) | APP 0x15 (entry 1) | Match? |
|-------|--------------------|--------------------|--------|
| field_A | 0x1936d | 0x1936d | **YES** |
| field_B | 0x158e0 | 0x69c0 | No |
| field_C | 0xc0f | 0xc0f | **YES** |
| field_D | 0xc13 | 0xc13 | **YES** |

The two manifests use ID 0xd2 for the same PROM source range and lengths. Only the T start, field_B, changes between images. The independent object directory confirms the shared ID in §6.10.1.

### 6.5 Arithmetic Closure Chains

The following checks identify starts and extents in two coordinate spaces and compare their boundaries with the outer header.

#### 6.5.1 Source-Space Contiguity: field_A + field_C = next entry's field_A

Within sequential entry ID groups, each entry's field_A + field_C exactly equals the next entry's field_A:

| From → To | field_A(from) + field_C(from) | = field_A(to) | Residual |
|-----------|-------------------------------|---------------|----------|
| 0x81 → 0x82 | 0x4327c + 0x3c | = 0x432b8 | **0** |
| 0x70 → 0x71 | 0x1f554 + 0x184 | = 0x1f6d8 | **0** |
| 0x71 → 0x72 | 0x1f6d8 + 0x244 | = 0x1f91c | **0** |
| 0x89 → 0x8a *(cross-image)* | 0x55279 + 0xad74 | = 0x5ffed | **0** |

This proves field_A is a start position and field_C is an extent (size) in a shared address space ("space S"). Entries within each group tile space S contiguously. The 0x89→0x8a chain spanning APP 0x19 and APP 0x15 proves both images share the same space S.

#### 6.5.2 Destination-Space Contiguity: field_B + align(field_D) ≈ next entry's field_B

In a different ordering, each entry's field_B + field_D (with small upward alignment) equals the next entry's field_B:

| From → To | field_B(from) + field_D(from) | raw sum | = field_B(to) | Align |
|-----------|-------------------------------|---------|---------------|-------|
| 0x71 → 0x70 | 0x13e58 + 0x244 | 0x1409c | 0x140a0 | +4 |
| 0x70 → 0x72 | 0x140a0 + 0x184 | 0x14224 | 0x14228 | +4 |
| 0x12 → 0x14 | 0x19ae0 + 0x52c | 0x1a00c | 0x1a010 | +4 |
| 0x14 → 0x82 | 0x1a010 + 0x1cf5 | 0x1bd05 | 0x1bd10 | +11 |
| 0x82 → 0x81 | 0x1bd10 + 0x111 | 0x1be21 | 0x1be30 | +15 |
| 0xd2 → 0x03 | 0x158e0 + 0xc13 | 0x164f3 | 0x16500 | +13 |
| 0x03 → 0x02 | 0x16500 + 0x70 | 0x16570 | 0x16570 | **0** |

This supports reading field_B as a start position and field_D as an extent in a second address space ("space T"), with small alignment padding between entries. Padding does not account for every neighbouring pair, however. The remaining intervals are:

| From → To | End of first | Start of next | Unaccounted |
|-----------|--------------|---------------|-------------|
| 0x89 → 0x71 (APP 0x19) | 0x13a74 | 0x13e58 | 0x3e4 (996 B) |
| 0x72 → 0xd2 (APP 0x19) | 0x14ab0 | 0x158e0 | 0xe30 (3,632 B) |
| 0x02 → 0x12 (APP 0x19) | 0x189e0 | 0x19ae0 | 0x1100 (outer+010, §6.5.3) |
| 0x8a → 0xd2 (APP 0x15) | 0x4890 | 0x69c0 | 0x2130 (8,496 B) |

No two ranges overlap. Note the ordering in space T differs from space S (e.g., 0x71 precedes 0x70 in T but follows it in S).

For type 2 entries (0x02, 0x03 — which have field_C=field_D=0), field_A and field_B take the roles of start and size *within space T*, chaining with the type 1 field_B allocations.

#### 6.5.3 Outer Header Anchors: +008 and +010 Tie to Entry Boundaries

The secondary header's variable field (+008) and constant (+010 = 0x1100) anchor precisely to entry boundaries in space T:

**APP 0x19:**
- Entry 0x02 terminal: field_A(0x02) + field_B(0x02) = 0x16570 + 0x2470 = **0x189e0** = outer+008 (exact)
- outer+008 + outer+010: 0x189e0 + 0x1100 = **0x19ae0** = field_B of entry 0x12 (exact)

**APP 0x15:**
- outer+008 + outer+010: 0x58c0 + 0x1100 = **0x69c0** = field_B of entry 0xd2 (exact)

In APP 0x19, outer+008 marks the end of the type 2 range, and outer+010 is the gap to the next type 1 range. APP 0x15 satisfies the same sum but has no type 2 entry in its manifest.

**Cross-image outer header comparison** (empirically confirmed by S-space probe):

| Field | APP 0x19 | APP 0x15 | Cross-image? | Interpretation |
|-------|----------|----------|-------------|----------------|
| +004 | 0x000047a7 | 0x000047a7 | **constant** | Unknown (newly revealed; possibly capability flags or checksum) |
| +008 | 0x000189e0 | 0x000058c0 | variable | End of the type 2 range in APP 0x19 (§6.5.3) |
| +00c | 0x000004bd | 0x000004bd | **constant** | Unknown (cross-image invariant) |
| +010 | 0x00001100 | 0x00001100 | **constant** | Gap from +008 to the following type 1 start |

Three of these four fields have the same value in the two manifests. Only +008 differs. For variation in +00C across ROMs, see §6.11.3.

#### 6.5.4 Implications for Field Semantics

The arithmetic chains constrain the interpretation to:

| Field | Role | Evidence |
|-------|------|----------|
| field_A | Start in S for type 1; start in T for type 2 | §6.5.1: contiguous tiling, shared across images |
| field_B | Start position in space T (type 1); size in space T (type 2) | §6.5.2: contiguous tiling with alignment; §6.5.3: anchored by outer header |
| field_C | Extent in space S | §6.5.1: field_A + field_C = next field_A |
| field_D | Extent in space T | §6.5.2: field_B + align(field_D) = next field_B |

Type 1 has measured start/extent pairs in S and T. Type 2 has a start/extent pair in T only. The physical backing of T remains unknown (§6.9.2).

### 6.6 Entry ID Namespace Analysis

**Excluded namespace: NV2080_ENGINE_TYPE.** The RM engine type enum (from `cl2080_notification.h` in open-gpu-kernel-modules) defines engine types 0x00–0x53 (NV2080_ENGINE_TYPE_LAST = 0x54). The V6 manifest entry IDs include values 0x70, 0x81, 0x82, 0x89, 0x8a, and 0xd2 — all well above the RM enum's maximum. These entry IDs are therefore not NV2080_ENGINE_TYPE values.

The twelve distinct IDs belong to the manifests. All ten distinct type 1 entries start at the same-numbered object in the plaintext GB20x directory (§6.10.1). The two type 2 entries have no source range to compare.

**Structural grouping** (observed, not interpreted as hardware identity):

| Group | IDs | Count | Shared Structural Traits |
|-------|-----|-------|--------------------------|
| Low | 0x02, 0x03 | 2 | Type 2, zero field_C/field_D, small field_B values |
| Mid | 0x12, 0x14 | 2 | Type 1, C↔D Δ=+4, moderate field_B values |
| 0x7x | 0x70, 0x71, 0x72 | 3 | Type 1, C↔D Δ=0 or +1, similar field_B values (~0x14000) |
| 0x8x-low | 0x81, 0x82 | 2 | Type 1, C↔D Δ=+4, largest field_B values (~0x1bd00) |
| 0x8x-high | 0x89, 0x8a | 2 | Type 1, C↔D Δ=0, appear in different APPs (0x89→0x19, 0x8a→0x15) |
| Isolated | 0xd2 | 1 | Type 1, C↔D Δ=+4, shared across both APPs (§6.4) |

Sequential IDs within each group suggest a systematic numbering scheme, but the assignment principle (PRI topology, die-level routing, or something else) is unknown.

### 6.7 Entry ID Summary

| Entry ID | Appears In | Type | Distinguishing Observations |
|----------|-----------|------|-----------------------------|
| 0x02 | 0x19 | 2 | Type 2, zero field_C/field_D. field_A contiguous with 0x03 (0x03's field_A + field_B = 0x02's field_A). |
| 0x03 | 0x19 | 2 | Type 2, zero field_C/field_D. Smallest field_B (0x70). |
| 0x12 | 0x19 | 1 | C↔D Δ=+4, field_B=0x19ae0. |
| 0x14 | 0x19 | 1 | C↔D Δ=+4, field_B=0x1a010 (similar to 0x12). |
| 0x70 | 0x19 | 1 | C↔D Δ=0, field_B≈0x14000. Sequential trio with 0x71, 0x72. |
| 0x71 | 0x19 | 1 | C↔D Δ=0, field_B≈0x13e00. |
| 0x72 | 0x19 | 1 | C↔D Δ=+1 (differs from 0x70-71 Δ=0), field_B≈0x14200. |
| 0x81 | 0x19 | 1 | C↔D Δ=+4, largest field_B (0x1be30). Sequential pair with 0x82. |
| 0x82 | 0x19 | 1 | C↔D Δ=+4, field_B=0x1bd10 (near-identical to 0x81). |
| 0x89 | 0x19 only | 1 | C↔D Δ=0, field_B=0x8d00. |
| 0x8a | 0x15 only | 1 | C↔D Δ=0, field_A=0x5ffed (highest of all entries). |
| 0xd2 | Both | 1 | C↔D Δ=+4. Only cross-image entry (§6.4). No sequential neighbors. |

The BIT directory lists 20 tokens. After omitting one zero-length token and using PROM coordinates for both sides, none of the 19 top-level targets equals a start among the 10 distinct type 1 entries. The two type 2 entries have no source range to compare. This comparison checked only the 19 top-level BIT directory pointers without recursively expanding secondary tables. A shared address would establish a reference to the same bytes; the referring field would still need a documented meaning to assign a table identity. The separate engine-enum comparison is in §6.6. (See [bar0-prom-metadata-621.01.txt](../reports/gb205-2f18/bar0-prom-metadata-621.01.txt) for the sanitized BAR0 / PROM BIT summary.)

### 6.8 field_C / field_D Δ Pattern

Across the 10 distinct type 1 entries, D − C is zero in four cases, +1 in one case and +4 in five cases. Rounding a length up to the nearest multiple of four can add at most three bytes. It cannot explain the five +4 cases; 0x3C → 0x40 even starts from a multiple of four. For 0x72, 0x887 → 0x888 is compatible with rounding but has no unique explanation. The capture records lengths; it contains no observation of an added tag, length word or checksum. The gaps between consecutive T ranges discussed in §6.5.2 are a separate quantity.

### 6.9 Physical Identity of Spaces S and T

#### 6.9.1 Space S: VBIOS-Relative PROM Offsets (base = adjust)

**Hypothesis**: field_A values are offsets relative to the same `adjust` base (0x4D400) used by the BIT ucode table for descriptor lookups. Under this interpretation, `PROM[adjust + field_A]` is the source data address for each type 1 entry.

**Verification — all S-space addresses fall within the VBIOS data region:**

The PROM layout has a clear boundary: the first firmware descriptor (APP 0x07) begins at PROM+0xADCD4. Everything before that address is VBIOS data (PCI ROM headers, BIT tables, devinit scripts, power tables, display configuration, etc.).

| Type 1 entry | field_A | PROM offset | Signed offset from first descriptor |
|---|---:|---:|---:|
| 0xd2 | 0x1936d | 0x6676D | −0x47567 |
| 0x70 | 0x1f554 | 0x6C954 | −0x41380 |
| 0x71 | 0x1f6d8 | 0x6CAD8 | −0x411FC |
| 0x72 | 0x1f91c | 0x6CD1C | −0x40FB8 |
| 0x14 | 0x26787 | 0x73B87 | −0x3A14D |
| 0x12 | 0x3ec78 | 0x8C078 | −0x21C5C |
| 0x81 | 0x4327c | 0x9067C | −0x1D658 |
| 0x82 | 0x432b8 | 0x906B8 | −0x1D61C |
| 0x89 | 0x55279 | 0xA2679 | −0x0B65B |
| 0x8a | 0x5ffed | 0xAD3ED | −0x008E7 |

Type 2 entries 0x02 and 0x03 have ranges in T only (§6.5.2, §6.8).

The exclusive end is 0xADB7D. The gap to the first observed descriptor is 0xADCD4 − 0xADB7D = 0x157 (343 bytes).

**Why adjust and not image_base**: The BIT ucode table uses `adjust` (= expansion_offset_abs, computed as image_base + expansion_offset_within_image = 0x35400 + 0x18000 = 0x4D400) for all PROM-relative address translation. The manifest entries use the same coordinate system — this is the natural VBIOS offset space.

The listed type 1 ranges precede the first observed descriptor. The capture records bytes from PROM, without tracing subsequent use of those bytes.

**Scope.** The calculated PROM offsets match the archived type 1 sample rows.

**Empirical confirmation (ssdiag v0.8.5, S-space probe patch, admin PROM read):**

Shannon entropy at PROM[adjust+field_A] (256-byte window):
- Entry 0x89 (APP 0x19 primary, fc=0xad74): **2.36 bits/byte** — structured VBIOS data
- Entry 0x8a (APP 0x15 primary, fc=0x790): **0.18 bits/byte** — near-empty (mostly 0x00 fill)

These entropy estimates describe the two 256-byte samples. They do not identify an algorithm or establish the encoding of an entire image.

**S-space data header classification** (first bytes at PROM[adjust+field_A]):

| Prefix (scalar) | Entries | Count | Notes |
|--------|---------|----------------------|-------|
| 0x10 | 0x89, 0x8a, 0x81, 0x82, 0x70, 0x71, 0x72 | 7 | IDs in 0x70–0x8x range |
| 0x30 | 0x12, 0x14, 0xd2 | 3 | IDs outside that range |

For 0x89 and 0x8a, byte 2 matches the entry ID in both samples. Treating bytes 1, 2 and 3 (zero-based) as header size, record size and count gives an exact match to field_C for 5 of the 10 distinct type 1 entries (0x14, 0x70, 0x71, 0x81 and 0x82). Among prefix 0x10 entries, 4 of 7 match; among prefix 0x30 entries, 1 of 3 matches. The other five disagree with that particular layout. The prefix could still encode a version for some tables. Its meaning and table family remain unverified; entropy alone identifies neither.

**5-byte header closure and 6-block periodicity on 0x70 / 0x71 / 0x72** (verified on the full local ROM at `PROM[0x4D400 + field_A]`):
- **0x72 (`PROM+0x6CD1C`, `field_C = 0x887` = 2,183 B)**: uses a 5-byte header (`header_size = 5`, little-endian `u16` `record_size = 363`, `u8` `count = 6`), closing exactly to `5 + 363 × 6 = 2,183 = 0x887`. Splitting its 2,178-byte body into six 363-byte blocks shows 362/363 bytes identical between blocks 0 and 1 (1 differing byte), while blocks 2–5 are byte-identical to one another and differ from block 1 at 80 bytes.
- **0x70 (`PROM+0x6C954`, `field_C = 0x184` = 388 B)** and **0x71 (`PROM+0x6CAD8`, `field_C = 0x244` = 580 B)**: follow the 4-byte header closure above (`4 + 64 × 6 = 388` and `4 + 96 × 6 = 580`, respectively). Splitting each body into six equal blocks shows 1 differing byte between blocks 0 and 1 (63/64 identical for 0x70; 95/96 identical for 0x71), and blocks 2–5 byte-identical to one another (each differing from block 0 at 1 byte).
- These length closures and 6-block periodicities are structural observations only and do not identify the semantic table family.

**Cross-image shared entry verification.** Both archived probes for entry 0xd2 returned identical sampled data at PROM+0x6676D.

#### 6.9.2 Space T: Target Destination Address Space

For type 1 entries, field_B gives a start coordinate in T. The physical interpretations are discussed below.

**APP 0x19 ranges in increasing T-coordinate order.** Endpoints are exclusive.

| Region | T Range | Size (bytes) | Contents |
|--------|---------|------|----------|
| desc+08 value | 0x8800 | — | Not a T base: on eight ROMs of four dies it exceeds the first T start (§6.11.3) |
| gap | 0x8800–0x8d00 | 1,280 | Gap from the tentative base to the first entry |
| type 1 low | 0x8d00–0x164f3 | 55,283 | Entries 0x89, 0x71, 0x70, 0x72, 0xd2 (50,647 B of ranges and 8 B of padding, plus the 0x3e4 and 0xe30 intervals of §6.5.2) |
| alignment gap | 0x164f3–0x16500 | 13 | Between listed ranges |
| type 2 | 0x16500–0x189e0 | 9,440 | Entries 0x03, 0x02 (data-only regions) |
| reserved | 0x189e0–0x19ae0 | 4,352 | outer+010 constant gap |
| type 1 high | 0x19ae0–0x1be70 | 9,104 | Entries 0x12, 0x14, 0x82, 0x81 |
| total span | 0x8800–0x1be70 | 79,472 | From desc+08 to last entry end |

**Boundary clues:**
- desc+08 (0x8800) sits 0x500 below the first type 1 entry (0x89 at 0x8d00) on this ROM. The reading of desc+08 as a T-space base is refuted by eight ROMs of GB202, GB203, GB205 and GB206, where desc+08 is 0x9200 or 0x9400 and the first entry starts at 0x8A00, 0x8C00 or 0x8D00 (§6.11.3). The spans computed from 0x8800 in this section have no confirmed meaning.
- In APP 0x19, outer+010 (0x1100) measures the gap from the end of the type 2 region to the next type 1 start. Both Class A headers contain the same value (§6.5.3).
- Exclusive T endpoint: 0x1be70 = 111.6 KiB. The target engine (PMU, target 0x01) DMEM capacity is unknown (PMU registers are PLM-gated). T-space may address on-die DMEM, VRAM buffer (FRTS/WPR2), or virtual address space — see §6.9.2 capacity cross-check.

**APP 0x15 T-space**: entries 0x8a (T=[0x4100, 0x4890)) and 0xd2 (T=[0x69c0, 0x75d3)). Note that 0xd2 appears in both APP manifests with **different field_B** (0x158e0 in APP 0x19 vs 0x69c0 in APP 0x15), confirming field_B is image-specific (T coordinate varies by image) while field_A/C/D are entry-intrinsic (same PROM source data).

**DMEM capacity cross-check**:

**Target engine identification**: Class A images (APP 0x19/0x15) carry V6 descriptor target 0x01, which in NVIDIA VBIOS convention corresponds to PMU (NV_PPWR, BAR0+0x10a000), not GSP (0x110000) or SEC2 (0x840000). The PMU's on-die DMEM capacity is independent of GSP/SEC2 and has historically been larger on some architectures.

1. **Source code.** The reviewed `open-gpu-kernel-modules` files (`kernel_gsp_gb202.c`, `kernel_gsp_gb100.c`, `kernel_gsp.c` and `kernel_falcon.c`) did not provide the PMU DMEM capacity.

2. **Hardware registers** (from the earlier 2026-11-22 driver 616.64 experimental ssdiag probe, register rows archived in [bar0-core-616.64.txt](../reports/gb205-2f18/bar0-core-616.64.txt); standard ssdiag 0.8.8 leaves these six engine-capability registers out of default `CORE_ROWS`):

| Register | Address | Value | Notes |
|----------|---------|-------|-------|
| PMU_CAPS | 0x10a108 | **0xbadf5040** | PRIV_VIOLATION — entire PMU block PLM-gated |
| PMU_HWCFG3 | 0x10a0f0 | **0xbadf5040** | PRIV_VIOLATION |
| GSP_CAPS | 0x110108 | 0x80420100 | Readable (but wrong engine for target 0x01) |
| GSP_HWCFG3 | 0x1100f0 | 0x00000f00 | Readable |
| SEC2_CAPS | 0x840108 | 0x80420100 | Readable (same core config as GSP) |
| SEC2_HWCFG3 | 0x8400f0 | 0x00000f00 | Readable |

Both sampled PMU capacity registers returned PRIV_VIOLATION. GSP and SEC2 readings describe different engines and cannot establish PMU capacity.

3. **Range and capacity.** The observed exclusive endpoint is 114,288 (0x1BE70). With the tentative base of 0x8800, the span is 79,472 bytes. The register reads do not establish physical capacity.
- **H-CAPS-A**: PMU on-die DMEM ≥ 128 KiB (0x20000), the smallest power-of-2 that fits. PMU engines on some prior architectures have had DMEM larger than GSP/SEC2.
- **H-CAPS-B**: T-space addresses are virtual, mapped through the engine's RISC-V standard paging (e.g., Sv39) or NVRISCV MPU to physical DMEM plus external memory, allowing a larger address range than physical DMEM.
- **H-CAPS-C**: T-space destinations are not on-die DMEM at all, but offsets into a VRAM buffer (e.g., the ~1 MiB FRTS region within WPR2). In modern NVIDIA boot flows, VBIOS runtime configuration tables (which S-space confirmed as plaintext VBIOS data, §6.9.1) are commonly DMA-copied to protected VRAM regions rather than squeezed into microcontroller SRAM. Under this interpretation, 111.6 KiB fits trivially.

H-CAPS-C is the preferred interpretation in this note. The observations do not distinguish VRAM, on-die DMEM and a virtual address space.

#### 6.9.3 Adjacent descriptor boundaries in the full file

For each adjacent pair in file order, the exclusive end below is the current descriptor offset plus 80 bytes, the row area `hi16(+18) × lo16(+1C)` and the declared stored size.

| APP pair | Calculated exclusive end | Next descriptor | Difference (bytes) |
|---|---:|---:|---:|
| 0x07 → 0x19 | 0xCE324 | 0xCE324 | 0 |
| 0x19 → 0x15 (240-byte row area) | 0xEF364 | 0xEF364 | 0 |
| 0x15 → 0x23 (60-byte row area) | 0xF48F0 | 0xF48F0 | 0 |
| 0x23 → 0x18 | 0x11B940 | 0x11BF04 | 1,476 (0x5C4) |
| 0x18 → 0x24 (80-byte row area) | 0x1282A4 | 0x128904 | 1,632 (0x660) |

An earlier revision left the last two intervals unexplained and computed the final one without the row area (0x6B0). Both are fully accounted for by the start of the next expansion image and its directory (§6.10):

| Interval | Fill to the next image | Image head before the directory | Directory (16 + 2 × 60) | First object | Sum |
|---|---:|---:|---:|---:|---:|
| 0x23 → 0x18, image 0x11BA00 | 0xC0 | 0x45A | 0x88 | 0x22 (id 0xD7) | 0x5C4 |
| 0x18 → 0x24, image 0x128400 | 0x15C | 0x45A | 0x88 | 0x22 (id 0xC0) | 0x660 |

The final APP, 0x24, ends at 0x153554 within the file; it has no successor.

### 6.10 The GB20x object directory

The independent offline editor [Panchovix/nvidia-vbios-vix-editor](https://github.com/Panchovix/nvidia-vbios-vix-editor) (commit `638ea500f909`, GPL-3.0; no code was copied) parses a plaintext *object-directory v3* in GB20x images: a 16-byte header (version 3, header size 16, count, PCI device), then 60-byte entries (u32 id, u32 offset, u32 size, SHA-384), with offsets relative to the end of the entry table.

`scripts/rom-directory.py`, a separate implementation, read the directories of the full ROM identified in §1 (matched by SHA-256). It found seven directories with 155 objects. **All 155 object hashes verify**, and so does the SHA-384 of every directory, which sits 0x251 bytes before it. Each directory starts 0x45A into its image. The metadata is archived as [rom-directory-98.05.31.00.44.json](../reports/gb205-2f18/rom-directory-98.05.31.00.44.json) (ids, offsets and sizes; no digests or bytes).

| Directory | Image | Device field | Objects | Contents relevant here |
|---|---|---|---:|---|
| 0x3465A | 0x34200 | 0x2F18 | 2 | — |
| 0x3505A | 0x34C00 | 0x2F18 | 4 | — |
| 0x3585A | 0x35400 (legacy) | 0x2F18 | 35 | BIT directory, object 0x2D |
| 0x4525A | 0x44E00 (EFI) | 0x2B80 | 3 | EFI payload, object 0x55 |
| 0x5D25A | 0x5CE00 | 0x2F18 | 107 | all manifest source tables, the ucode table, APPs 0x07 / 0x19 / 0x15 / 0x23 |
| 0x11BE5A | 0x11BA00 | 0x2B80 | 2 | APP 0x18 |
| 0x12885A | 0x128400 | 0x2B80 | 2 | APP 0x24 |

Three images carry device ID 0x2B80, outside the GB205 range. Section 6.11.5 compares their contents across dies.

#### 6.10.1 Manifest entry ids are object ids

| Entry id | Manifest range | Same-id object | Relation |
|---|---|---|---|
| 0x12 | 0x8C078, 0x528 B | 0x8C078, 0x528 B | equal |
| 0x14 | 0x73B87, 0x1CF1 B | 0x73B87, 0x184F1 B | same start; the manifest takes the first 7,409 of 99,569 bytes |
| 0x70 | 0x6C954, 0x184 B | 0x6C954, 0x184 B | equal |
| 0x71 | 0x6CAD8, 0x244 B | 0x6CAD8, 0x244 B | equal |
| 0x72 | 0x6CD1C, 0x887 B | 0x6CD1C, 0x887 B | equal |
| 0x81 | 0x9067C, 0x3C B | 0x9067C, 0x3C B | equal |
| 0x82 | 0x906B8, 0x10D B | 0x906B8, 0x10D B | equal |
| 0x89 | 0xA2679, 0xAD74 B | 0xA2679, 0xAD74 B | equal |
| 0x8A | 0xAD3ED, 0x790 B | 0xAD3ED, 0x790 B | equal |
| 0xD2 | 0x6676D, 0xC0F B | 0x6676D, 0xC0F B | equal |

All ten distinct type 1 ids name an object of the same number at exactly the predicted start, and nine have exactly its size. This settles the entry id namespace for this ROM and independently confirms the space S base (`adjust`) of §6.9.1. Type 2 entries have no source range to compare.

Two of the objects can be compared with the editor's table requirements. Both headers are equal (version / header / record / count / next record):

| Object | Editor's requirement | GB205 header | Length |
|---|---|---|---|
| 0x14 | VFE table, 0x30 / 14 / 29 / 255 / 25 | 0x30 / 14 / 29 / 255 / 25 | 14 + 255 × 29 = 7,409, the part the manifest takes |
| 0x12 | power device table, 0x30 / 24 / 25 / 11 | 0x30 / 24 / 25 / 11 | 24 + 11 × 25 = 299 of 1,320; remainder undecoded |

The table names are the editor author's inference.

#### 6.10.2 Descriptors, BIT and the ucode table are objects too

Each V6 descriptor starts an object, and its size equals `80 + hi16(+18) × lo16(+1C) + stored_size` in all six cases:

| APP | Object id | Rows × stride | Object size | Predicted |
|---|---|---|---:|---:|
| 0x07 | 0x36 | 0 × 0 | 0x20650 | 0x20650 |
| 0x19 | 0x87 | 12 × 20 | 0x21040 | 0x21040 |
| 0x15 | 0x88 | 3 × 20 | 0x558C | 0x558C |
| 0x23 | 0x9E | 0 × 0 | 0x27050 | 0x27050 |
| 0x18 | 0x86 | 4 × 20 | 0xC3A0 | 0xC3A0 |
| 0x24 | 0x9F | 0 × 0 | 0x2AC50 | 0x2AC50 |

The object boundaries come from a separate, hashed structure, so this confirms the row-count and row-stride reading of +18 and +1C beyond the two Class A specimens. It says nothing about the content of the rows on 0x18.

Two more lengths close exactly: the ucode table is object 0x2B with 216 bytes = 6 + 35 × 6, and the BIT directory is object 0x2D with 132 bytes = 12 + 20 × 6.

#### 6.10.3 Testing other cards

`scripts/rom-survey.py` repeats §6.10.1 and §6.10.2 on any number of local ROMs from their own directories, without a prepared JSON, and compares objects across them. On this ROM alone it reproduces 6/6 descriptors, 11/11 manifest entries (0xD2 counted in both manifests) and, from the entries themselves, the single base 0x4D400.

```bash
python3 scripts/rom-survey.py /path/to/roms
```

How to read a run with several cards:

| Question | Supports | Refutes | Not decided by it |
|---|---|---|---|
| Size law `80 + hi16(+18) × lo16(+1C) + stored_size` | every descriptor closes on every die sampled | any `DOES NOT CLOSE` on a ROM whose hashes verify | dies that were not sampled |
| Entry id = object id | all entries match with one base per ROM | any ROM with `DIFF` and verified hashes | type 2 entries |
| 0x2B80 images common to the family | the same letter for every 0x2B80 object on different dies | a different letter between ROMs of the **same** firmware build, or a die without those directories | different letters between different VBIOS builds: the component may be common and merely updated |

Equal letters mean identical bytes in the ROMs compared. They do not show that a component is identical on every GB20x product.

### 6.11 Survey of twelve ROMs covering every GB20x die

#### 6.11.1 Specimens

| Die | Board | PCI | VBIOS | adjust | BIT | Objects | ROM SHA-256 |
|---|---|---|---|---|---|---:|---|
| GB202 | ASUS RTX 5090 | 10de:2b85 | 98.02.2E.80.C8 | 0x50000 | 0x38DF0 | 155 | `b4469a4621b43d2e…` |
| GB202 | ASUS RTX 5090 D | 10de:2b87 | 98.02.31.40.4C | 0x50000 | 0x38DF0 | 155 | `40a5325387ee9f37…` |
| GB202 | PNY RTX PRO 6000 | 10de:2bb1 | 98.02.52.00.02 | 0x4D800 | 0x365F0 | 153 | `befbc36e00d40f8a…` |
| GB203 | ASUS RTX 5080 | 10de:2c02 | 98.03.3B.C0.3E | 0x50000 | 0x38DF0 | 155 | `4e90933a782cf1a7…` |
| GB203 | ASUS RTX 5070 Ti | 10de:2c05 | 98.03.3B.80.2F | 0x50000 | 0x38DF0 | 155 | `955d37c5c05a1955…` |
| GB203 | ASUS RTX 5070 Ti | 10de:2c05 | 98.03.58.40.B6 | 0x4D400 | 0x361F0 | 155 | `fef2462b0935bda5…` |
| GB205 | ASUS RTX 5070 | 10de:2f04 | 98.05.28.00.64 | 0x50400 | 0x391F0 | 152 | `abcb16479c675a1d…` |
| GB205 | Palit RTX 5070 | 10de:2f04 | 98.05.28.00.1D | 0x50400 | 0x391F0 | 152 | `ff98f999242f1795…` |
| GB205 | RTX 5070 Ti Laptop (local) | 10de:2f18 | 98.05.31.00.44 | 0x4D400 | 0x361F0 | 155 | `06f94e60c400414c…` |
| GB206 | MSI RTX 5060 Ti | 10de:2d04 | 98.06.1F.00.CD | 0x4D400 | 0x361F0 | 152 | `53aba3d263d997c6…` |
| GB206 | ASUS RTX 5060 | 10de:2d05 | 98.06.21.40.42 | 0x4D400 | 0x361F0 | 152 | `0a4e741524e3b8a8…` |
| GB207 | Gigabyte RTX 5050 | 10de:2d83 | 98.07.1D.00.55 | 0x4D800 | 0x365F0 | 150 | `4c17fbb3e66eb6b1…` |

Eleven ROMs were supplied as local files by the repository owner (file names in the style of a public VBIOS collection; provenance not verified here). All object hashes and all directory digests in all twelve verify. That makes the images self-consistent; it does not prove them stock, because an editor can repair hashes and digests and the signature over them was not checked here. The VBIOS version, the PCI device and the die agree: the second byte of the version is 02, 03, 05, 06 or 07 for GB202, GB203, GB205, GB206 and GB207. Metadata: [reports/rom-survey-20261123](../reports/rom-survey-20261123/README.md).

#### 6.11.2 What holds on all twelve

- Size law of §6.10.2: 72 of 72 descriptors fill their object exactly.
- Manifest ids are object ids: 132 of 132 type 1 entries, 120 equal and 12 prefixes (always 0x14), one base per ROM, and that base is always image base plus EFI image length.
- Seven directories, each 0x45A into its image, each with a verifying digest 0x251 bytes earlier.
- 35 ucode slots with the same six used APPs and targets; APP to object mapping 0x07→0x36, 0x19→0x87, 0x15→0x88, 0x23→0x9E, 0x18→0x86, 0x24→0x9F; same file order.
- Flags, target, +24, +28 (0x01012801), +2C and +30 of every APP, hence the names `OOBHUB` and `FSP_RUN`.
- The size residual is 0xA00 on 0x07, 0x23 and 0x18 and 0 on the other three.
- Both manifests list the same ids in the same order, with the same four S-space chains; outer words +00, +04 and +10 are constant.
- The table headers of §6.10.1 and the lengths of 0x12, 0x14, 0x70, 0x71, 0x72, 0x81, 0x82 and 0xD2.

`scripts/v6-closure.py` runs 63 checks per ROM; all pass on all twelve.

#### 6.11.3 What varies, and what that refutes

- **Row area of APP 0x23.** 3 × 20 bytes on four ROMs (versions 98.02.2E, 98.02.31 and twice 98.03.3B), none on the other eight. These four are exactly the ROMs of EFI group A (§6.11.5), so the row area goes with a firmware generation, not with an APP or a die. The two RTX 5070 ROMs were added after this was written; they are in group B and have no rows.
- **desc+08 is not a T-space base.** It is above the first T start on eight ROMs of four dies (0x9200 or 0x9400 against 0x8A00, 0x8C00 or 0x8D00). Instead, +08 of 0x19 and 0x15 equals +0C of APP 0x18, and +10 equals +14 of APP 0x18 plus 0x2600, on all twelve. The 5060 Ti supports this with a value (0x9400) that no other ROM has.
- **Outer +00C.** 0x4BD on six ROMs and 0x3FC on six (PRO 6000, both 5070, both GB206, GB207). GB202 and GB205 each have both values, so it is not fixed per die. The seven ROMs of EFI group B carry a byte-identical EFI payload, in which the candidate interval of §8 lies, yet both lengths occur among them. One constant offset with two lengths into identical bytes argues further against the offset/length reading with the `adjust` base.
- **What +00C may go with.** Four small card objects (0x52, 0x5C, 0x74, 0x76) have exactly two contents, and these split the twelve ROMs exactly as +00C does. Selected on the ten ROMs without the 5070, they are the only four that do so, out of 20 objects with two contents there; the 5070 ROMs agree for all four. Since the selection was made with all twelve at hand, this is a lead to test on the next ROM and not a confirmed prediction. No object has a size of 0x4BD or 0x3FC on any ROM.

#### 6.11.4 The opaque tail follows the payload

Across the twelve ROMs the 72 tails and the 72 payloads fall into the same groups for every APP: 51 distinct payloads, 51 distinct tails, equal payload if and only if equal tail. Every one of the 28 byte positions varies (42 to 50 distinct values among 51 tails; 7.866 bits/byte over the 1,428 bytes, against 8 for uniform bytes).

A 12-byte window of the tail was looked for in MD5, SHA-1, SHA-2 and SHA-3 digests of fifteen plausible ranges (payload, row area, descriptor prefix and size segments, alone or combined). There was no hit. The tail is bound to the payload, but it is not a plain unkeyed digest of any range tried. Whether it is an initialization value, a keyed tag or something else is open.

The tail stayed unchanged in two comparisons where manifest sources differed. Between the two 5070 ROMs the manifest source 0x89 differs, and between the 5080 and the 5070 Ti of 98.03.3B the sources 0x89 and 0xD2 differ (0x14 as well, though a manifest takes only its first 7,409 bytes), while payload and tail of APP 0x19 and 0x15 stay identical. The sources are covered by the object hashes of the directory; whether a signature covers them is not shown here.

#### 6.11.5 Which images are common

Equal letters mean byte-identical content within one row.

| Device | Object | Content | GB202 5090 02.2E | GB202 5090 D 02.31 | GB202 PRO 02.52 | GB203 5080 03.3B | GB203 5070 Ti 03.3B | GB203 5070 Ti 03.58 | GB205 5070 ASUS 05.28 | GB205 5070 Palit 05.28 | GB205 Laptop 05.31 | GB206 5060 Ti 06.1F | GB206 5060 06.21 | GB207 07.1D |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0x2B80 | 0x55 | EFI payload | A | A | B | A | A | B | B | B | B | B | B | C |
| 0x2B80 | 0x9F | APP 0x24 (`FSP_RUN`) | A | B | C | A | A | D | E | E | D | D | D | F |
| 0x2B80 | 0x86 | APP 0x18 | A | B | C | A | A | D | E | E | F | G | H | I |
| card | 0x87 | APP 0x19, payload | A | B | C | A | A | D | E | E | F | G | H | I |
| card | 0x88 | APP 0x15, payload | A | A | B | A | A | C | D | D | E | F | E | G |
| card | 0x36 | APP 0x07, payload | A | B | C | D | D | E | F | F | G | H | I | J |
| card | 0x9E | APP 0x23 (`OOBHUB`), payload | A | B | C | D | D | E | F | F | G | H | I | J |

Versions are shortened to their second and third bytes. Reading the table:

- **EFI payload: by firmware generation, not by die.** Three contents occur: A (GB202, GB203), B (GB202, GB203, GB205, GB206) and C (GB207). GB202 and GB203 each appear in two groups, and the GB203 5070 Ti changes group with its VBIOS version. This was predicted before the second 5070 Ti ROM was read: its 98.03.3B version had to fall in with the 5080 of 98.03.3B and the 5090 of 98.02.2E. It equals the 5080 in every row and the 5090 in the five rows that are not die-specific.
- **APP 0x24** is shared across dies in two sets (A: GB202 5090, GB203; D: GB203, GB205 laptop, GB206), but it does not follow the EFI generation: group A has two contents of it and group B has three. The 5090 D, the PRO 6000 and the 5070 each have one of their own. An earlier revision of this section, based on eight ROMs, called APP 0x24 common within a generation; the 5090 D refutes that.
- **APP 0x18 and 0x19** are shared across dies only by the 5090 (98.02.2E) and the two 98.03.3B ROMs. **APP 0x15** is also shared by the 5090 D, and by the GB205 laptop with the GB206 5060.
- **APP 0x07 and 0x23** are never equal across dies. They are equal only within one die and version: on the 5080 and 5070 Ti of 98.03.3B, and on the two 5070 ROMs.
- **The die alone decides none of the six payloads.** The GB205 laptop (98.05.31) and the GB205 5070 (98.05.28) differ in all six; only their EFI payload is equal.
- **Board vendor.** The ASUS and the Palit 5070 (98.05.28.00.64 and 98.05.28.00.1D) are the only pair that shares the die, product and first four VBIOS version components. All six descriptors, payloads and tails, both manifests and all seven 0x2B80 objects are identical. 123 of 140 card objects are equal; the other 17 are 0x13, 0x18, 0x1D, 0x1E, 0x24, 0x26, 0x2C, 0x2E, 0x35, 0x3D, 0x4F, 0x67, 0x69, 0x83, 0x85, 0x89, 0xBD. 13 of these also differ between the ASUS 5080 and 5070 Ti of 98.03.3B.
- The statement "0x2B80 images are identical across the family" is **refuted**. The device field marks images that can be shared. The EFI payload, APP 0x24 and APP 0x18 are each shared by at least two dies somewhere, and none of the three by all five. Only a two-byte object (0xCE) is identical everywhere.
- Among the card-specific objects, 140 ids occur in all twelve ROMs and 39 of them are byte-identical in all twelve, including the manifest sources 0x81 and 0x82. 0x4F is identical on all except the Palit 5070.
- Three ids (0x7A, 0x7B, 0x7C) are absent on both desktop GB205 ROMs, GB206 and GB207. They are present on the GB205 laptop, where all three are larger than on GB202 and GB203, so their absence does not follow the die.
- **Only the laptop differs** in 6 further objects (0x5F, 0x61, 0x62, 0x65, 0x7D, 0xE4): they are identical on all eleven desktop and workstation ROMs. These include two desktop GB205 ROMs, so the difference does **not** belong to the GB205 die, which an earlier revision left open. The laptop is the only mobile ROM and the only one of its version, so whether the form factor, the product or 98.05.31 is the reason is open.
- Pairs of card objects: same die, product and version, different vendor (5070) 123 of 140 equal; same die and version, different product (5080 and 5070 Ti, 98.03.3B) 113 of 143; same product, different version (5070 Ti) 101 of 143; same die, different product and version: 5090 and 5090 D 113 of 143, 5060 and 5060 Ti 96 of 140, 5090 and PRO 6000 58 of 143, 5070 and the GB205 laptop 52 of 140.

#### 6.11.6 RTX PRO and RTX 5090 D against the other consumer cards

The PNY RTX PRO 6000 (GB202, 10de:2bb1, 98.02.52.00.02) and the ASUS RTX 5090 D (GB202, 10de:2b87, 98.02.31.40.4C) have the same 143 object ids as the RTX 5090, the same 35 slots and six APPs, and the same flags, targets, +24, +28, +2C and +30 as the other ROMs. All 63 checks pass on both. Every object that is identical on all the other ROMs is identical on them too. **No structural V6 or directory marker separates either product from the other cards.**

Their ucode payloads differ from those of the 5090 (the 5090 D in all but APP 0x15), but product and version differ at once, so this cannot be attributed to the product. Public collections tend to carry one version family per product, which is why no sample here separates the two. None of this bears on the tensor clamp: the survey compares plaintext structure and whether payloads are equal. It reads neither payloads nor fuses.

---

## 7. Limits of the evidence

The archived readback establishes sample values and geometric relationships. Runtime parser behavior and authentication order were not observed.

### 7.1 Previously proposed C3 (withdrawn)

C3 is withdrawn. For both Class A samples the observed spacing accounts for the manifest separately from stored_size. The segment sizes in §2.2.1 already account for stored_size. The next visible descriptor provides a sample boundary for the arithmetic in §6.9.3.

### 7.2 Class comparison

The roles of +08, +10 and the high half of +1C remain open. The low half of +1C gives the row stride (§6.10.2). Section 2.2.1 shows how segments A, B and C account for stored_size. Three entries leave a residual of 0xA00 bytes whose purpose is unknown.

### 7.3 Destination storage

T denotes a target destination address space. Its backing and capacity remain unverified (§6.9.2).

---

## 8. Resolved and open questions

1. **~~Secondary header full size~~** → RESOLVED: Full manifest captured. APP 0x19 has 240 bytes plaintext (20B header + 11×20B entries), APP 0x15 has 60 bytes (20B header + 2×20B entries).
2. **~~Row count and stride~~** → RESOLVED: `hi16(+18) × lo16(+1C)` is the length of the area after the descriptor on all six APPs (§6.10.2). The content of that area on 0x18 is open.
3. **+28 constant.** The same scalar, 0x01012801, appears in all 72 descriptors of twelve ROMs (§6.11.2). Its meaning remains open.
4. **Printable tags.** The names `OOBHUB` (APP 0x23) and `FSP_RUN` (APP 0x24) span +2C..+33 on all twelve ROMs (§6.11.2); their function, and whether the region is one field or two, remains unknown (§4).
5. **Descriptor version and size on APP 0x18 and 0x24 — resolved for this file.** Both are V6 with 80-byte headers. Their physical locations are outside the old 1 MiB capture. Their post-header interpretation is qualified in §2.2.1.
6. **Opaque 28-byte tail.** It follows the payload one to one across twelve ROMs and is not a plain digest of the ranges tried (§6.11.4). Its function remains open.
7. **Type 1 source coordinates.** The smallest listed start is PROM+0x6676D. The largest exclusive end is PROM+0xADB7D. Individual ranges and the two sampled entropy estimates are in §6.9.1.
8. **~~Entry id namespace~~** → RESOLVED for type 1: the ids are object ids of the GB20x directory, 10/10 by start and 9/10 by size (§6.10.1). The two type 2 ids remain open.
9. **Manifest geometry.** The sampled start/extent relationships are established (§6.5). **Space T backing.** Physical identity remains open (§6.9.2).
10. **Header word 0x0035**: The u16 at secondary header +2 is constant (0x0035 = 53) across both Class A specimens. Its role is unknown — it is neither the entry count (which comes from V6 desc +18) nor the header byte length.
11. **Outer constants +004 and +00C.** With the proposed base and offset/length interpretation, the candidate interval is [0x51BA7, 0x52064), 1,213 bytes. It lies within the nominal 1 MiB window. Measured on the full local ROM without reproducing raw bytes, that 1,213-byte span contains 3 `0x00` bytes (0.25%), 8 `0xFF` bytes (0.66%), 1,202 bytes that are neither `0x00` nor `0xFF` (99.09%; 99.75% non-`0x00`, 99.34% non-`0xFF`), 254/256 distinct byte values, and an empirical Shannon entropy of 7.821 bits/byte. The interval lies in the middle of the EFI payload object (0x45350, 0x17943 bytes) and is not an object of its own, unlike every confirmed S range; this weighs against the offset/length interpretation with the `adjust` base without excluding another base. The survey adds that +00C takes two values over byte-identical EFI payloads, and that the two values coincide with the two contents of objects 0x52, 0x5C, 0x74 and 0x76 (§6.11.3). Recognizable bytes alone would not establish that the fields reference them, and irregular-looking bytes would not exclude a valid reference.

---

## 9. Methodology

- Directory and six-descriptor measurements use a local, read-only full ROM export identified in §1. Earlier measurements explicitly attributed to ssdiag use the archived 1 MiB window.
- The full-file comparison reads decoded little-endian scalars and summarizes the opaque tail statistically.
- All decoded scalars are also available as [v6-descriptors-98.05.31.00.44.json](../reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json). `python3 scripts/v6-closure.py <json>` recomputes the arithmetic of §2.2.1, §3.1, §4, §6.5 and §6.9 from it, and runs unchanged on another card's JSON.
- Class A secondary headers: 256 bytes dumped per image (auto-detected via magic word check on first dword).
- Directory measurements (§6.10) come from `scripts/rom-directory.py` on the same local ROM. Object and directory hashes were verified in memory and are not stored.
- The original four descriptors supplied the hypotheses. APP 0x18 and APP 0x24 test those hypotheses on additional entries from the same VBIOS.
- The V2 and V3 comparison uses the public `FALCON_UCODE_DESC_V2` and `FALCON_UCODE_DESC_V3` definitions cited in §5.
- Entry 0xd2 has the same A, C and D values in both Class A manifests. Its PROM source matches the same-numbered directory object (§6.10.1).
- S-space physical identity confirmed by ssdiag v0.8.5 S-space probe: Shannon entropy measurement (256-byte window at PROM[adjust+field_A]) and first-byte header classification across all type 1 entries in both Class A images.
- NV2080_ENGINE_TYPE enum sourced from `open-gpu-kernel-modules` HEAD `src/common/sdk/nvidia/inc/class/cl2080_notification.h` (MIT license, 2022-2026). Full enum spans 0x00–0x53 (84 numeric values). Used to exclude RM engine types as the namespace for V6 manifest entry IDs (§6.6).
