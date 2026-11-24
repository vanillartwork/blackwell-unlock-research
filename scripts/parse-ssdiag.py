#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Parse sanitized ssdiag / diag-unlock register captures and diff two of them.

Turns the text capture archived under reports/ into named registers, derives the
quantities the docs quote (die, WPR2 bytes, SM count, READOUT_1 fields), and prints
either a Markdown column for docs/test-matrix.md or a diff between two captures.

Works on text files only: it never touches a GPU.

Usage:
  python scripts/parse-ssdiag.py reports/gb205-2f18/bar0-prom-metadata-621.01.txt
  python scripts/parse-ssdiag.py A.txt --diff B.txt
  python scripts/parse-ssdiag.py A.txt --markdown
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Short names used by the docs -> BAR0 offset. Captures print some of these without
# an address (the fuse mirrors), so lookups go by name first, then by address.
MATRIX_ROWS = [
    ("PMC_BOOT_0", 0x000000, "PMC_BOOT_0"),
    ("FEAT_OVR_PLM", 0x823804, None),
    ("FEAT_READOUT_1", 0x823818, None),
    ("FEAT_READOUT_0", 0x823814, None),
    ("FEAT_OVR_ECC", 0x82380C, None),
    ("FEAT_OVR_ECC_1", 0x823810, None),
    ("FEAT_SS0", 0x82381C, None),
    ("FEAT_SS1", 0x823820, None),
    ("FUSE_SS_FMLA32", 0x8207D8, "FUSE_SS_FMLA32"),
    ("FUSE_SS_IMLA1", 0x8207E0, "FUSE_SS_IMLA1"),
    ("FUSE_SS_IMLA2", 0x8207E4, "FUSE_SS_IMLA2"),
    ("FUSE_SS_IMLA4", 0x8207EC, "FUSE_SS_IMLA4"),
    ("FUSE_SS_DP", None, "FUSE_SS_DP"),
    ("FUSE_SS_FFMA", None, "FUSE_SS_FFMA"),
    ("FUSE_SS_FMLA16", 0x8207D4, "FUSE_SS_FMLA16"),
    ("FUSE_SS_IMLA0", None, "FUSE_SS_IMLA0"),
    ("FUSE_SS_IMLA3", None, "FUSE_SS_IMLA3"),
    ("FUSE_FEAT_OVR_DIS", 0x8203F0, None),
    ("FUSE_EN_SW_OVERRIDE", 0x820040, "FUSE_EN_SW_OVERRIDE"),
    ("FUSE_SS_PLM", 0x8200FC, "FUSE_SS_PLM"),
    ("SECURE_FEAT_OVR_QUADRO_WR", 0x82038C, "SECURE_FEAT_OVR_QUADRO_WR"),
    ("SM_ISSUE_RATE_MODIFIER", 0x504204, "SM_ISSUE_RATE_MODIFIER"),
    ("SEC2_PLM", 0x8403C4, "SEC2_PLM"),
    ("GSP_CPUCTL", 0x110100, "GSP_CPUCTL"),
    ("WPR2_LO", 0x1FA824, "WPR2_LO"),
    ("WPR2_HI", 0x1FA828, "WPR2_HI"),
    ("FB_SIZE_MIB", 0x1183A4, "FB_SIZE_MIB"),
    ("GR_TOPO_409604", 0x409604, "GR_TOPO_409604"),
]

CHIP_IDS = {0x1B2: "GB202", 0x1B3: "GB203", 0x1B5: "GB205", 0x1B6: "GB206", 0x1B7: "GB207"}

# READOUT_1 carries nine 3-bit fields; bit 6 and bits 27:25 are holes.
READOUT1_SHIFTS = (0, 3, 7, 10, 13, 16, 19, 22, 28)
READOUT1_NAMES = ("imla0", "fmla16", "fmla32", "ffma", "imla1", "imla2", "imla3", "imla4", "DP")

_ADDR_VALUE = re.compile(
    r"^\s*(?P<name>\S.*?)\s+0x(?P<addr>[0-9a-fA-F]{8})\s*=\s*(?P<vals>0x[0-9a-fA-F]{8}(?:\s+0x[0-9a-fA-F]{8})*)"
)
_NAME_VALUE = re.compile(r"^\s*(?P<name>[A-Z][A-Z0-9_]+)\s*=\s*0x(?P<val>[0-9a-fA-F]{8})\s*$")
_SWEEP_HEAD = re.compile(r"^\s+(0x[0-9a-fA-F]{8}(?:\s+0x[0-9a-fA-F]{8})+)\s*$")
_SWEEP_VALUE = re.compile(r"^\s+value\s+(0x[0-9a-fA-F]{8}(?:\s+0x[0-9a-fA-F]{8})+)\s*$")


def parse_capture(text):
    """Return {"by_name", "by_addr", "arrays", "meta"} for one capture."""
    by_name, by_addr, arrays, meta = {}, {}, {}, {}
    sweep = None
    for line in text.splitlines():
        # The PROM section lists table offsets, not registers.
        if line.startswith("--- PROM BIT"):
            break
        m = _SWEEP_HEAD.match(line)
        if m:
            sweep = [int(x, 16) for x in m.group(1).split()]
            continue
        m = _SWEEP_VALUE.match(line)
        if m and sweep:
            for addr, val in zip(sweep, m.group(1).split()):
                by_addr.setdefault(addr, int(val, 16))
            sweep = None
            continue
        sweep = None
        m = _ADDR_VALUE.match(line)
        if m:
            vals = [int(x, 16) for x in m.group("vals").split()]
            name = re.sub(r"\s+", " ", m.group("name"))
            addr = int(m.group("addr"), 16)
            if len(vals) > 1:
                arrays[name] = vals
            by_addr.setdefault(addr, vals[0])
            by_name.setdefault(name, vals[0])
            continue
        m = _NAME_VALUE.match(line)
        if m:
            by_name.setdefault(m.group("name"), int(m.group("val"), 16))
            continue
        m = re.match(r"^GPU \d+ (PCI|VBIOS|driver): (.+)$", line)
        if m:
            meta.setdefault(m.group(1).lower(), m.group(2).strip())
    return {"by_name": by_name, "by_addr": by_addr, "arrays": arrays, "meta": meta}


def lookup(cap, name):
    for row, addr, key in MATRIX_ROWS:
        if row == name:
            if key is not None and key in cap["by_name"]:
                return cap["by_name"][key]
            return cap["by_addr"].get(addr) if addr is not None else None
    raise KeyError(name)


def decode_readout1(value):
    """Split READOUT_1 into its nine selector fields, in READOUT1_NAMES order."""
    return [(value >> shift) & 7 for shift in READOUT1_SHIFTS]


def wpr2_bytes(lo, hi):
    """Convert raw WPR2_LO/HI words to byte addresses and a span.

    The published headers define VAL as bits 31:4 with a 12-bit alignment (both
    NV_PFB_PRI_MMU_WPR2_ADDR_* and, on Blackwell, NV_HUBMMU_PRI_MMU_WPR2_ADDR_*), so
    the byte address is (raw >> 4) << 12. Those headers do not say whether HI is inclusive;
    nova-core documents LO as inclusive and HI as exclusive with 0 meaning "not set", so
    the span is HI - LO.
    """
    start = (lo >> 4) << 12
    end = (hi >> 4) << 12
    return {"start": start, "end": end, "span": end - start, "active": end != 0 and end > start}


def sm_count(cap):
    """Derive (gpcs, tpcs, sms) from the GPC and per-GPC TPC disable masks."""
    gpc_mask = cap["by_addr"].get(0x820C1C)
    tpc = cap["arrays"].get("GA100_SM_STAT")
    if gpc_mask is None or tpc is None:
        return None
    gpcs = 12 - bin(gpc_mask & 0xFFF).count("1")
    # Only the GPCs left enabled contribute; disabled entries read all-ones.
    tpcs = sum(
        8 - bin(tpc[g] & 0xFF).count("1")
        for g in range(min(12, len(tpc)))
        if not (gpc_mask >> g) & 1
    )
    return {"gpcs": gpcs, "tpcs": tpcs, "sms": tpcs * 2}


def summarize(cap):
    boot0 = lookup(cap, "PMC_BOOT_0")
    readout = lookup(cap, "FEAT_READOUT_1")
    lo, hi = lookup(cap, "WPR2_LO"), lookup(cap, "WPR2_HI")
    return {
        "meta": cap["meta"],
        "die": CHIP_IDS.get(boot0 >> 20) if boot0 is not None else None,
        "registers": {name: lookup(cap, name) for name, _, _ in MATRIX_ROWS},
        "readout1_fields": dict(zip(READOUT1_NAMES, decode_readout1(readout))) if readout is not None else None,
        "wpr2": wpr2_bytes(lo, hi) if lo is not None and hi is not None else None,
        "topology": sm_count(cap),
    }


def has_registers(cap):
    """Whether a capture holds at least one tracked register."""
    return any(lookup(cap, name) is not None for name, _, _ in MATRIX_ROWS)


def diff_captures(a, b):
    """Return matrix rows whose values differ, plus rows only one capture has.

    A row sampled on one side only has None on the other: that is missing evidence, not
    an observed change.
    """
    rows = []
    for name, _, _ in MATRIX_ROWS:
        x, y = lookup(a, name), lookup(b, name)
        if x != y:
            rows.append({"name": name, "a": x, "b": y})
    return rows


def _hex(v):
    return f"0x{v:08x}" if v is not None else "n/a"


def _mib(v):
    return f"{v / (1 << 20):g} MiB"


def _read(path):
    # ssdiag writes a UTF-8 BOM on Windows; raw captures can contain stray bytes.
    return parse_capture(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=Path)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--diff", type=Path, metavar="OTHER", help="compare against a second capture")
    mode.add_argument("--markdown", action="store_true", help="print a docs/test-matrix.md column")
    mode.add_argument("--json", action="store_true", help="print the parsed summary as JSON")
    args = ap.parse_args(argv)

    try:
        cap = _read(args.capture)
        other = _read(args.diff) if args.diff else None
    except OSError as exc:
        print(f"parse-ssdiag: {exc}", file=sys.stderr)
        return 2
    for path, capture in ((args.capture, cap), (args.diff, other)):
        if capture is not None and not has_registers(capture):
            print(f"parse-ssdiag: no known register found in {path}", file=sys.stderr)
            return 2
    info = summarize(cap)

    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    if args.markdown:
        print(f"| Register | {info['die'] or 'unknown die'} |")
        print("|---|---:|")
        for name, value in info["registers"].items():
            print(f"| `{name}` | `{_hex(value)}` |")
        return 0
    if other is not None:
        rows = diff_captures(cap, other)
        print(f"A: {cap['meta'].get('driver', 'unknown driver')}")
        print(f"B: {other['meta'].get('driver', 'unknown driver')}")
        for r in rows:
            print(f"  {r['name']:28s} {_hex(r['a']):>10} -> {_hex(r['b']):>10}")
        for label, c in (("A", cap), ("B", other)):
            w = summarize(c)["wpr2"]
            if w:
                print(f"  WPR2 {label}: start {_mib(w['start'])}, span {_mib(w['span'])}")
        one_sided = sum(r["a"] is None or r["b"] is None for r in rows)
        print(
            f"{len(rows) - one_sided} of {len(MATRIX_ROWS)} tracked rows changed, "
            f"{one_sided} sampled in only one capture"
        )
        return 0

    print(f"die: {info['die'] or 'unknown'}   driver: {info['meta'].get('driver', 'unknown')}")
    for name, value in info["registers"].items():
        print(f"  {name:28s} {_hex(value)}")
    if info["readout1_fields"]:
        print("  READOUT_1 fields: " + " ".join(f"{k}={v}" for k, v in info["readout1_fields"].items()))
    if info["wpr2"]:
        w = info["wpr2"]
        print(f"  WPR2: start {w['start']:#x} ({_mib(w['start'])}), span {_mib(w['span'])}, active={w['active']}")
    if info["topology"]:
        t = info["topology"]
        print(f"  topology: {t['gpcs']} GPC / {t['tpcs']} TPC / {t['sms']} SM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
