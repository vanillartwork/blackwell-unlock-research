#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the V6 descriptor / Class A manifest arithmetic of a VBIOS offline.

Input is a JSON file of decoded scalars (schema blackwell-abi-lab/v6-descriptors/1, see
reports/gb205-2f18/), never a ROM. Running it on another card's JSON shows which of the
GB205 observations in docs/vbios-v6-descriptor.md carry over and which are specimen-only.

Every line is a geometric observation. None of them establishes what a field means, how
firmware parses it, or in which order anything is authenticated.

Usage:
  python scripts/v6-closure.py reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json
"""

import argparse
import json
import sys
from pathlib import Path

ROW = 0x14
DESC_SIZE = 80
CLASS_A_OUTER = 0x00350001
CONST_28 = 0x01012801
KNOWN_RESIDUALS = (0, 0xA00)
# Largest T-space gap treated as alignment padding between neighbouring entries.
ALIGN_SLACK = 16
# Table shapes that the independent parser Panchovix/nvidia-vbios-vix-editor requires for
# the GB20x directory object of the same id: (version, header size, record size, name).
# Counts are left out because they vary by board.
INDEPENDENT_SHAPES = {
    "0x12": (0x30, 24, 25, "power device table"),
    "0x14": (0x30, 14, 29, "VFE table"),
}


def _i(x):
    return int(x, 16) if isinstance(x, str) else int(x)


def residual(desc):
    """stored_size minus the three size terms at +0C, +14 and +20."""
    f = desc["fields"]
    return _i(f["04"]) - (_i(f["0C"]) + _i(f["14"]) + _i(f["20"]))


def tag_bytes(desc):
    """The eight bytes at +2C..+33 in file order."""
    f = desc["fields"]
    return _i(f["2C"]).to_bytes(4, "little") + _i(f["30"]).to_bytes(4, "little")


def printable_tag(desc):
    """NUL-left-padded printable tag spanning +2C..+33, or None."""
    raw = tag_bytes(desc).lstrip(b"\0")
    if len(raw) >= 4 and all(0x21 <= c < 0x7F for c in raw):
        return raw.decode("ascii")
    return None


def t_range(entry):
    """(start, end) of an entry in space T; type 2 keeps it in field_A/field_B."""
    if entry["type"] == 2:
        return _i(entry["field_a"]), _i(entry["field_a"]) + _i(entry["field_b"])
    return _i(entry["field_b"]), _i(entry["field_b"]) + _i(entry["field_d"])


def t_gaps(manifest):
    """Gaps between T-space neighbours: [(from id, to id, gap bytes)]."""
    order = sorted(manifest["entries"], key=lambda e: t_range(e)[0])
    return [
        (a["entry_id"], b["entry_id"], t_range(b)[0] - t_range(a)[1])
        for a, b in zip(order, order[1:])
    ]


def s_entries(data):
    """Distinct type 1 entries as {entry_id: (start, length)} in space S."""
    out = {}
    for m in data["manifests"]:
        for e in m["entries"]:
            if e["type"] == 1:
                out.setdefault(e["entry_id"], (_i(e["field_a"]), _i(e["field_c"])))
    return out


def table_heads(data):
    """Recorded table headers as [(entry id, header dict, S length)], each id once."""
    out, seen = [], set()
    for m in data["manifests"]:
        for e in m["entries"]:
            if "table_header" in e and e["entry_id"] not in seen:
                seen.add(e["entry_id"])
                out.append((e["entry_id"], e["table_header"], _i(e["field_c"])))
    return out


def row_area(desc):
    """Bytes between descriptor and payload: high half of +18 times low half of +1C.

    Confirmed on the full GB205 ROM: with it, all six descriptors fill their hash-verified
    directory objects exactly, including APP 0x18, which carries no Class A manifest.
    """
    return (_i(desc["fields"]["18"]) >> 16) * (_i(desc["fields"]["1C"]) & 0xFFFF)


def descriptor_end(desc):
    """Exclusive file end: descriptor + row area + stored size."""
    return _i(desc["descriptor_offset"]) + DESC_SIZE + row_area(desc) + desc["stored_size"]


def run_checks(data):
    """Return [(id, ok, detail)]; ok is None for measurements that have no pass/fail."""
    out = []
    descs = data["descriptors"]
    manifests = data["manifests"]
    by_app = {d["app"]: d for d in descs}

    spec = data["specimen"]
    out.append((
        "adjust = image_base + expansion",
        _i(spec["adjust"]) == _i(spec["image_base"]) + _i(spec["expansion"]),
        spec["adjust"],
    ))
    out.append((
        "slot count",
        spec["ucode_slots"] == spec["ucode_empty_slots"] + len(descs),
        f"{len(descs)} non-empty + {spec['ucode_empty_slots']} empty = {spec['ucode_slots']}",
    ))

    for d in descs:
        app = d["app"]
        ok_hdr = d["version"] == 6 and d["header_size"] == DESC_SIZE
        out.append((f"{app} header", ok_hdr, f"v{d['version']}, {d['header_size']} B"))
        out.append((f"{app} stored size", _i(d["fields"]["04"]) == d["stored_size"], f"{d['stored_size']:#x}"))
        flags = _i(d["flags"])
        out.append((f"{app} flag bits 0 and 2", flags & 0b101 == 0b101, d["flags"]))
        r = residual(d)
        out.append((f"{app} size residual", r in KNOWN_RESIDUALS, f"{r:#x}"))
        out.append((f"{app} +28 constant", _i(d["fields"]["28"]) == CONST_28, d["fields"]["28"]))
        out.append((f"{app} +2C..+33 tag", None, printable_tag(d) or tag_bytes(d).hex(" ")))

    if "0x18" in by_app:
        # Seen on all six surveyed ROMs: the Class A fields +08 and +10 follow APP 0x18.
        seg_a, seg_b = _i(by_app["0x18"]["fields"]["0C"]), _i(by_app["0x18"]["fields"]["14"])
        for m in manifests:
            f = by_app[m["app"]]["fields"]
            out.append((f"{m['app']} +08 = APP 0x18 +0C", _i(f["08"]) == seg_a, f"{f['08']} vs {seg_a:#x}"))
            out.append((f"{m['app']} +10 = APP 0x18 +14 + 0x2600", _i(f["10"]) == seg_b + 0x2600,
                        f"{f['10']} vs {seg_b + 0x2600:#x}"))

    for m in manifests:
        app = m["app"]
        d = by_app[app]
        outer = {k: _i(v) for k, v in m["outer_header"].items()}
        rows = 1 + len(m["entries"])
        out.append((f"{app} outer word", outer["00"] == CLASS_A_OUTER, f"{outer['00']:#010x}"))
        hi = _i(d["fields"]["18"]) >> 16
        out.append((f"{app} +18 high half = rows", hi == rows, f"{hi} vs {rows}"))
        stride = _i(d["fields"]["1C"]) & 0xFFFF
        out.append((f"{app} +1C low half = row stride", stride == ROW, f"{stride:#x} vs {ROW:#x}"))

        starts = {t_range(e)[0] for e in m["entries"]}
        anchor = outer["08"] + outer["10"]
        out.append((f"{app} outer +08 + +10 is a T start", anchor in starts, f"{anchor:#x}"))
        type2 = [e for e in m["entries"] if e["type"] == 2]
        if type2:
            end = max(t_range(e)[1] for e in type2)
            out.append((f"{app} outer +08 = type 2 end", end == outer["08"], f"{end:#x}"))
        gaps = t_gaps(m)
        out.append((f"{app} T ranges do not overlap", all(g >= 0 for _, _, g in gaps), f"{len(gaps)} gaps"))
        big = [(a, b, g) for a, b, g in gaps if g >= ALIGN_SLACK and g != outer["10"]]
        out.append((
            f"{app} T gaps beyond padding",
            None,
            ", ".join(f"{a}->{b} {g:#x}" for a, b, g in big) or "none",
        ))

    s = s_entries(data)
    starts = {v[0]: k for k, v in s.items()}
    chains = sorted((k, starts[a + n]) for k, (a, n) in s.items() if a + n in starts)
    out.append(("S-space contiguous pairs", None, ", ".join(f"{a}->{b}" for a, b in chains) or "none"))
    ordered = sorted(s.values())
    out.append((
        "S ranges do not overlap",
        all(a + n <= b for (a, n), (b, _) in zip(ordered, ordered[1:])),
        f"{len(ordered)} distinct type 1 entries",
    ))
    if ordered:
        first_desc = min(_i(d["descriptor_offset"]) for d in descs)
        end = _i(spec["adjust"]) + ordered[-1][0] + ordered[-1][1]
        out.append(("S ranges end before first descriptor", end <= first_desc, f"{end:#x} vs {first_desc:#x}"))

    for eid, head, length in table_heads(data):
        filled = head["header_size"] + head["record_size"] * head["count"]
        out.append((
            f"{eid} table header",
            None,
            f"v{head['version']:#04x}: {head['header_size']} + {head['count']} x {head['record_size']}"
            f" = {filled} of {length} bytes",
        ))
        out.append((f"{eid} table fits its S range", filled <= length, f"{filled} <= {length}"))
        shape = INDEPENDENT_SHAPES.get(eid)
        if shape:
            got = (head["version"], head["header_size"], head["record_size"])
            out.append((f"{eid} head equals the independent parser's {shape[3]}", got == shape[:3], str(got)))

    seen = {}
    for m in manifests:
        for e in m["entries"]:
            seen.setdefault(e["entry_id"], []).append(e)
    for eid, es in sorted(seen.items()):
        if len(es) > 1:
            same = len({(e["field_a"], e["field_c"], e["field_d"]) for e in es}) == 1
            out.append((f"shared entry {eid} keeps A/C/D", same, f"{len(es)} manifests"))

    order = sorted(descs, key=lambda d: _i(d["descriptor_offset"]))
    for a, b in zip(order, order[1:]):
        gap = _i(b["descriptor_offset"]) - descriptor_end(a)
        out.append((f"{a['app']} -> {b['app']} no overlap", gap >= 0, f"{gap:#x} unaccounted"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    args = ap.parse_args(argv)
    try:
        data = json.loads(args.path.read_text(encoding="utf-8"))
        checks = run_checks(data)
    except (OSError, ValueError, KeyError) as exc:
        print(f"v6-closure: {args.path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for label, ok, detail in checks:
        mark = "    " if ok is None else ("ok  " if ok else "DIFF")
        print(f"[{mark}] {label}: {detail}")
    failed = [c for c in checks if c[1] is False]
    tested = [c for c in checks if c[1] is not None]
    print(f"{len(tested) - len(failed)}/{len(tested)} GB205 observations hold for {data['specimen']['vbios']}")
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
