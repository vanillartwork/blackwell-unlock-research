#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host-side boot ABI layout calculator and dump decoder (Route 1, offline).

Computes field offsets of the host-visible boot structures for several pinned
open-gpu-kernel-modules tags, and decodes hex dumps of those structures (as an
observation patch would log them) back into named fields.

Only interface facts (field names, types, order) are recorded here; no upstream
source is copied. Field lists were read from the public headers:
  src/nvidia/arch/nvalloc/common/inc/gsp/gspifpub.h
  src/nvidia/arch/nvalloc/common/inc/gsp/gsp_fw_wpr_meta.h
  src/nvidia/arch/nvalloc/common/inc/gsp/gsp_fw_sr_meta.h
  src/nvidia/inc/kernel/gpu/fsp/kern_fsp_cot_payload.h

Usage:
  python scripts/abi-layout.py --list
  python scripts/abi-layout.py --struct GSP_FMC_BOOT_PARAMS --tag 620.73.04
  python scripts/abi-layout.py --struct GSP_FMC_BOOT_PARAMS --diff 610.57.04 620.73.04
  python scripts/abi-layout.py --struct GspFwWprMeta --tag 620.73.04 --decode dump.hex
  python scripts/abi-layout.py --from-log my-gb20x-report/dmesg-gsp-fmc.txt
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCALARS = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}

WPR_META_MAGIC = 0xDC3AAE21371A60B3
WPR_META_REVISION = 1
WPR_META_VERIFIED = 0xA0A0A0A0A0A0A0A0
SR_META_REVISION = 2
FMC_BOOT_PARAMS_MAGIC = 0x20434D46  # bytes "FMC " in memory order

# Fields of GspFwWprMeta assigned by kgspPopulateWprMeta_GH100, which is the
# implementation GB202/GB203/GB205/GB206/GB207 bind to. Identical from 595.112.07 to
# 620.73.04 apart from pagingConfig (added in 615). Everything else is left zero by
# the host; per the upstream comment, the actual offsets are filled in by ACR ucode
# when it sets up WPR2.
WPR_META_HOST_FIELDS = frozenset({
    "magic", "revision", "sysmemAddrOfRadix3Elf", "sizeOfRadix3Elf",
    "sysmemAddrOfBootloader", "sizeOfBootloader", "bootloaderCodeOffset",
    "bootloaderDataOffset", "bootloaderManifestOffset", "sysmemAddrOfSignature",
    "sizeOfSignature", "nonWprHeapSize", "gspFwHeapSize", "frtsSize",
    "vgaWorkspaceSize", "sysmemAddrOfCrashReportQueue", "sizeOfCrashReportQueue",
    "gspFwHeapVfPartitionCount", "flags", "pagingConfig", "pmuReservedSize",
})

# A field is (name, type) or (name, type, count). Nested structs use a type name.
_INIT_OLD = [("regkeys", "u32")]
_INIT_NEW = [("regkeys", "u32"), ("reserved", "u32")]
_ACR_BASE = [
    ("target", "u32"),
    ("gspRmDescSize", "u32"),
    ("gspRmDescOffset", "u64"),
    ("wprCarveoutOffset", "u64"),
    ("wprCarveoutSize", "u32"),
    ("bIsGspRmBoot", "u8"),
]
_RM_OLD = [("target", "u32"), ("bootArgsOffset", "u64")]
_RM_NEW = [("target", "u32"), ("reserved", "u32"), ("bootArgsOffset", "u64")]
_SPDM_OLD = [("target", "u32"), ("payloadBufferOffset", "u64"), ("payloadBufferSize", "u32")]
_SPDM_NEW = [("target", "u32"), ("payloadBufferSize", "u32"), ("payloadBufferOffset", "u64")]
_MEM = [("flushSysmemAddrValLo", "u32"), ("flushSysmemAddrValHi", "u32")]
_TDISP = [("bTdispEnabled", "u8")]


def _fmc_570():
    return {
        "GSP_FMC_INIT_PARAMS": _INIT_OLD,
        "GSP_ACR_BOOT_GSP_RM_PARAMS": _ACR_BASE,
        "GSP_RM_PARAMS": _RM_OLD,
        "GSP_SPDM_PARAMS": _SPDM_OLD,
        "GSP_FMC_BOOT_PARAMS": [
            ("initParams", "GSP_FMC_INIT_PARAMS"),
            ("bootGspRmParams", "GSP_ACR_BOOT_GSP_RM_PARAMS"),
            ("gspRmParams", "GSP_RM_PARAMS"),
            ("gspSpdmParams", "GSP_SPDM_PARAMS"),
        ],
    }


def _fmc_610():
    t = _fmc_570()
    t["GSP_ACR_BOOT_GSP_RM_PARAMS"] = _ACR_BASE + [("bInstInSysMode", "u8")]
    t["GSP_RM_MEM_PARAMS"] = _MEM
    t["GSP_FMC_BOOT_PARAMS"] = t["GSP_FMC_BOOT_PARAMS"] + [("gspRmMemParams", "GSP_RM_MEM_PARAMS")]
    return t


def _fmc_stable(acr_tail, reserved3, reserved6, tdisp):
    t = {
        "GSP_FMC_INIT_PARAMS": _INIT_NEW,
        "GSP_ACR_BOOT_GSP_RM_PARAMS": _ACR_BASE
        + [("bInstInSysMode", "u8"), ("bIcuEnabled", "u8"), ("bScrubCbcSr", "u8")]
        + acr_tail,
        "GSP_RM_PARAMS": _RM_NEW,
        "GSP_SPDM_PARAMS": _SPDM_NEW,
        "GSP_RM_MEM_PARAMS": _MEM,
        "GSP_FMC_BOOT_PARAMS": [
            ("magic", "u32"),
            ("size", "u16"),
            ("reserved0", "u16"),
            ("reserved1", "u64", 3),
            ("initParams", "GSP_FMC_INIT_PARAMS"),
            ("reserved2", "u64", 3),
            ("bootGspRmParams", "GSP_ACR_BOOT_GSP_RM_PARAMS"),
            ("reserved3", "u64", reserved3),
            ("gspRmParams", "GSP_RM_PARAMS"),
            ("reserved4", "u64", 2),
            ("gspSpdmParams", "GSP_SPDM_PARAMS"),
            ("reserved5", "u64", 2),
            ("gspRmMemParams", "GSP_RM_MEM_PARAMS"),
            ("reserved6", "u64", reserved6),
        ],
    }
    if tdisp:
        t["GSP_TDISP_PARAMS"] = _TDISP
        t["GSP_FMC_BOOT_PARAMS"].append(("gspTdispParams", "GSP_TDISP_PARAMS"))
    return t


def _wpr_meta(paging):
    return [
        ("magic", "u64"),
        ("revision", "u64"),
        ("sysmemAddrOfRadix3Elf", "u64"),
        ("sizeOfRadix3Elf", "u64"),
        ("sysmemAddrOfBootloader", "u64"),
        ("sizeOfBootloader", "u64"),
        ("bootloaderCodeOffset", "u64"),
        ("bootloaderDataOffset", "u64"),
        ("bootloaderManifestOffset", "u64"),
        ("sysmemAddrOfSignature", "u64"),
        ("sizeOfSignature", "u64"),
        ("gspFwRsvdStart", "u64"),
        ("nonWprHeapOffset", "u64"),
        ("nonWprHeapSize", "u64"),
        ("gspFwWprStart", "u64"),
        ("gspFwHeapOffset", "u64"),
        ("gspFwHeapSize", "u64"),
        ("gspFwOffset", "u64"),
        ("bootBinOffset", "u64"),
        ("frtsOffset", "u64"),
        ("frtsSize", "u64"),
        ("gspFwWprEnd", "u64"),
        ("fbSize", "u64"),
        ("vgaWorkspaceOffset", "u64"),
        ("vgaWorkspaceSize", "u64"),
        ("bootCount", "u64"),
        # Anonymous union: the partition-RPC view has partitionRpcAddr,
        # partitionRpcRequestOffset/ReplyOffset and elf*Offset/Size here instead.
        ("partitionRpcPadding", "u32", 4),
        ("sysmemAddrOfCrashReportQueue", "u64"),
        ("sizeOfCrashReportQueue", "u32"),
        ("lsUcodeVersionPadding", "u32"),
        ("gspFwHeapVfPartitionCount", "u8"),
        ("flags", "u8"),
        ("pagingConfig", "u16") if paging else ("padding", "u8", 2),
        ("pmuReservedSize", "u32"),
        ("verified", "u64"),
    ]


_SR_META = [
    ("magic", "u64"),
    ("revision", "u64"),
    ("sysmemAddrOfSuspendResumeData", "u64"),
    ("sizeOfSuspendResumeData", "u64"),
    ("internal", "u32", 32),
    ("flags", "u32"),
    ("subrevision", "u32"),
    ("padding", "u32", 22),
]

_COT = [
    ("version", "u16"),
    ("size", "u16"),
    ("gspFmcSysmemOffset", "u64"),
    ("frtsSysmemOffset", "u64"),
    ("frtsSysmemSize", "u32"),
    ("frtsVidmemOffset", "u64"),
    ("frtsVidmemSize", "u32"),
    ("hash384", "u32", 12),
    ("publicKey", "u32", 96),
    ("signature", "u32", 96),
    ("gspBootArgsSysmemOffset", "u64"),
]

# tag -> commit and struct set. Only tags whose headers were actually read are listed.
TAGS = {
    "570.124.04": {"commit": "129479b1b7345f0055016ab3aa42ae78a4ca2048", "fmc": _fmc_570(), "wpr": _wpr_meta(False), "sr": False},
    "610.57.04": {
        "commit": "e4a5faa2567f28c8eabe0ebb6422b6d0abcf37eb",
        "fmc": _fmc_610(),
        "wpr": _wpr_meta(False),
        "sr": False,
    },
    "615.71.10": {
        "commit": "6f3be8c9c3fb4ed6ffc607a0d32d31ce4db7fba5",
        "fmc": _fmc_stable([], 4, 7, False),
        "wpr": _wpr_meta(True),
        "sr": True,
    },
    "620.73.04": {
        "commit": "a4b95f6a2db0bba10ccaccef79190a3c18dcf14a",
        "fmc": _fmc_stable([("flags", "u32"), ("reserved", "u32")], 3, 6, True),
        "wpr": _wpr_meta(True),
        "sr": True,
    },
}

# Structures the COT payload packs to 1 byte; everything else uses natural alignment.
PACKED = {"NVDM_PAYLOAD_COT"}
TOP_LEVEL = ("GSP_FMC_BOOT_PARAMS", "GspFwWprMeta", "GspFwSRMeta", "NVDM_PAYLOAD_COT")


def struct_table(tag):
    """Return {struct name: field list} for a known tag."""
    if tag not in TAGS:
        raise KeyError(f"unknown tag {tag!r}; known: {', '.join(TAGS)}")
    spec = TAGS[tag]
    table = dict(spec["fmc"])
    table["GspFwWprMeta"] = spec["wpr"]
    table["NVDM_PAYLOAD_COT"] = _COT
    if spec["sr"]:
        table["GspFwSRMeta"] = _SR_META
    return table


def _align_up(value, align):
    return (value + align - 1) // align * align


def layout(tag, name):
    """Flatten a struct into [{name, offset, size, type, count}] plus total size."""
    table = struct_table(tag)
    if name not in table:
        raise KeyError(f"{name!r} is not recorded for tag {tag}")

    def walk(struct, base, prefix, packed):
        rows, off, max_align = [], 0, 1
        for field in table[struct]:
            fname, ftype = field[0], field[1]
            count = field[2] if len(field) > 2 else 1
            if ftype in SCALARS:
                size = SCALARS[ftype]
                align = 1 if packed else size
                off = _align_up(off, align)
                rows.append({
                    "name": prefix + fname,
                    "offset": base + off,
                    "size": size * count,
                    "type": ftype,
                    "count": count,
                })
                off += size * count
            else:
                _, sub_size, sub_align = walk(ftype, 0, "", packed)
                align = 1 if packed else sub_align
                off = _align_up(off, align)
                sub_rows, _, _ = walk(ftype, base + off, prefix + fname + ".", packed)
                rows.extend(sub_rows)
                off += sub_size
            max_align = max(max_align, align)
        return rows, _align_up(off, max_align), max_align

    rows, size, _ = walk(name, 0, "", name in PACKED)
    return {"tag": tag, "struct": name, "size": size, "fields": rows}


def diff_layouts(a, b):
    """List fields added, removed or moved between two layouts of the same struct."""
    fa = {f["name"]: f for f in a["fields"]}
    fb = {f["name"]: f for f in b["fields"]}
    out = []
    for name in list(fa) + [n for n in fb if n not in fa]:
        x, y = fa.get(name), fb.get(name)
        if x is None:
            out.append({"name": name, "change": "added", "old": None, "new": y["offset"]})
        elif y is None:
            out.append({"name": name, "change": "removed", "old": x["offset"], "new": None})
        elif x["offset"] != y["offset"] or x["size"] != y["size"]:
            out.append({"name": name, "change": "moved", "old": x["offset"], "new": y["offset"]})
    return out


def parse_hex(text):
    """Accept plain hex, 0x-prefixed bytes, or `offset: bytes` dump lines."""
    data = bytearray()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        line = re.sub(r"^(?:0x)?[0-9a-fA-F]+:\s*", "", line)
        # hexdump -C style ASCII gutters would otherwise be read as hex digits.
        line = line.split("|", 1)[0]
        line = re.sub(r"0x", "", line, flags=re.IGNORECASE)
        data += bytes.fromhex(re.sub(r"[\s,]+", "", line))
    return bytes(data)


def decode(tag, name, data):
    """Decode a dump into {field: int or list of int}."""
    lay = layout(tag, name)
    if len(data) < lay["size"]:
        raise ValueError(f"{name} for {tag} needs {lay['size']} bytes, got {len(data)}")
    out = {}
    for f in lay["fields"]:
        unit = f["size"] // f["count"]
        vals = [
            int.from_bytes(data[f["offset"] + i * unit: f["offset"] + (i + 1) * unit], "little")
            for i in range(f["count"])
        ]
        out[f["name"]] = vals[0] if f["count"] == 1 else vals
    return out


# COT payload version and the key material sizes that go with it. Version 2 is ECDSA P-384
# (97-byte uncompressed point, 96-byte r||s); version 1 is RSA-3072. GB10x and GB20x use 2.
# Sources: cotPayload* HAL fields in g_kern_fsp_nvoc.c (620.73.04) and nova-core's
# fsp/hal/{gh100,gb100,gb202}.rs. The payload reserves 384 bytes for each either way.
COT_KEY_SIZES = {1: {"public_key": 384, "signature": 384}, 2: {"public_key": 97, "signature": 96}}
GB20X_COT_VERSION = 2

# KernelGspBootMode values printed by the trace patch.
BOOT_MODES = {
    0: "NORMAL",
    1: "SR_RESUME",
    2: "GC6_EXIT",
    3: "SR_WITH_WPR_IN_SYSMEM",
}

_LOG_BEGIN = re.compile(r"BWLAB: begin (\S+) tag=(\S+) size=(\d+)")
_LOG_END = re.compile(r"BWLAB: end (\S+)")
_LOG_ROW = re.compile(r"BWLAB: ([0-9a-f]{4}): ((?:[0-9a-f]{2} ){15}[0-9a-f]{2})\b")
_LOG_MODE = re.compile(r"BWLAB: bootMode=(\d+)")
_LOG_COT = re.compile(r"BWLAB: cot version=(\d+) size=(\d+) frtsSysmemSize=0x([0-9a-f]+) frtsVidmemSize=0x([0-9a-f]+)")


def parse_log(text):
    """Collect the trace patch output from a dmesg text, in log order.

    Returns a list of events:
      "dump"        one begin..end block; "data" holds its bytes, or None plus a "problem"
                    when rows are missing, malformed or out of order
      "cot"         the COT summary line
      "incomplete"  trace lines that belong to no complete block, which means part of the
                    log is gone (typically a wrapped kernel ring buffer)
    A damaged block is never decoded as if it were complete, and a log that holds any
    damage must not count as a clean capture even when its other blocks are intact.
    """
    events = []
    cur = None  # the open dump
    in_stray = False  # inside a run of rows whose begin marker is missing
    mode = None  # bootMode waiting for its GSP_FMC_BOOT_PARAMS dump

    def incomplete(problem):
        events.append({"kind": "incomplete", "problem": problem})

    def close(problem=None):
        nonlocal cur
        cur["problem"] = cur["problem"] or problem
        cur["data"] = None if cur["problem"] else bytes(cur["data"])
        events.append(cur)
        cur = None

    for line in text.splitlines():
        if "BWLAB:" not in line:
            continue
        m = _LOG_BEGIN.search(line)
        if m:
            if cur is not None:
                close("no end marker")
            in_stray = False
            cur = {"kind": "dump", "struct": m.group(1), "tag": m.group(2), "size": int(m.group(3)),
                   "data": bytearray(), "problem": None, "boot_mode": None}
            if cur["struct"] == "GSP_FMC_BOOT_PARAMS":
                cur["boot_mode"], mode = mode, None
            continue
        m = _LOG_ROW.search(line)
        if m:
            if cur is None:
                if not in_stray:
                    incomplete("data rows without a begin marker")
                    in_stray = True
            elif cur["problem"] is None:
                # After the first fault the rest of the block is read but not trusted.
                if int(m.group(1), 16) == len(cur["data"]):
                    cur["data"] += bytes.fromhex(m.group(2))
                else:
                    cur["problem"] = f"row {m.group(1)} out of order"
            continue
        m = _LOG_END.search(line)
        if m:
            if cur is None:
                if not in_stray:
                    incomplete(f"end marker for {m.group(1)} without a begin marker")
                in_stray = False
            elif m.group(1) != cur["struct"]:
                close(f"end marker names {m.group(1)}")
            elif len(cur["data"]) != cur["size"]:
                close(f"{len(cur['data'])} of {cur['size']} declared bytes")
            else:
                close()
            continue
        m = _LOG_MODE.search(line)
        if m:
            if mode is not None:
                incomplete(f"bootMode={mode} without a GSP_FMC_BOOT_PARAMS dump")
            mode = int(m.group(1))
            continue
        m = _LOG_COT.search(line)
        if m:
            events.append({
                "kind": "cot", "version": int(m.group(1)), "size": int(m.group(2)),
                "frtsSysmemSize": int(m.group(3), 16), "frtsVidmemSize": int(m.group(4), 16),
            })
            continue
        if cur is not None:
            cur["problem"] = cur["problem"] or "malformed trace line inside the block"
        elif not in_stray:
            incomplete("unrecognised trace line: " + line[line.index("BWLAB:"):][:60])
            in_stray = True  # rows that follow a cut begin marker are the same fault
    if cur is not None:
        close("no end marker")
    if mode is not None:
        incomplete(f"bootMode={mode} without a GSP_FMC_BOOT_PARAMS dump")
    return events


def dump_problem(event):
    """Why a dump event cannot be decoded with a recorded layout, or None."""
    if event["problem"]:
        return event["problem"]
    if event["tag"] not in TAGS:
        return f"tag {event['tag']} has no recorded layout"
    if event["struct"] not in struct_table(event["tag"]):
        return "structure has no recorded layout"
    want = layout(event["tag"], event["struct"])["size"]
    if event["size"] != want:
        return f"logged size {event['size']} but {event['tag']} defines {want} bytes"
    return None


def report_log(events):
    """Decode log events; returns (lines, all_ok) for the console."""
    lines, all_ok = [], True
    for n, ev in enumerate(events, 1):
        if ev["kind"] == "incomplete":
            all_ok = False
            lines.append(f"#{n} incomplete trace")
            lines.append(f"  [SKIP] {ev['problem']}")
            continue
        if ev["kind"] == "cot":
            lines.append(
                f"#{n} COT accepted by FSP: version={ev['version']} size={ev['size']} "
                f"frtsSysmemSize={ev['frtsSysmemSize']:#x} frtsVidmemSize={ev['frtsVidmemSize']:#x}"
            )
            for label, ok in (
                ("size", ev["size"] == layout("620.73.04", "NVDM_PAYLOAD_COT")["size"]),
                ("version (GB20x uses 2)", ev["version"] == GB20X_COT_VERSION),
            ):
                all_ok &= ok
                lines.append(f"  [{'ok' if ok else 'DIFF'}] {label}")
            continue
        mode = ev["boot_mode"]
        mode_s = f" bootMode={mode} ({BOOT_MODES.get(mode, 'unknown')})" if mode is not None else ""
        lines.append(f"#{n} {ev['struct']} tag={ev['tag']} size={ev['size']}{mode_s}")
        problem = dump_problem(ev)
        if problem:
            all_ok = False
            lines.append(f"  [SKIP] {problem}")
            continue
        values = decode(ev["tag"], ev["struct"], ev["data"])
        for f in layout(ev["tag"], ev["struct"])["fields"]:
            lines.append("  " + format_field(f, values[f["name"]]))
        for label, ok, detail in check_decoded(ev["tag"], ev["struct"], values):
            all_ok &= ok
            lines.append(f"  [{'ok' if ok else 'DIFF'}] {label}: {detail}")
    return lines, all_ok


def format_field(field, value):
    if isinstance(value, list):
        shown = f"[{len(value)} x {field['type']}, {'non-zero' if any(value) else 'zero'}]"
    else:
        shown = f"{value:#x}"
    return f"{field['offset']:#05x}  {field['name']:40s} {shown}"


def check_decoded(tag, name, values):
    """Return [(check, ok, detail)] comparing a decoded dump with the static baseline.

    A failing row means the dump does not match what the public source says the host
    writes; it is a prompt to re-check the tag and capture, not a security verdict.
    """
    checks = []

    def add(label, ok, detail):
        checks.append((label, bool(ok), detail))

    if name == "GspFwWprMeta":
        add("magic", values["magic"] == WPR_META_MAGIC, f"{values['magic']:#018x}")
        add("revision", values["revision"] == WPR_META_REVISION, str(values["revision"]))
        for f in ("sizeOfRadix3Elf", "sizeOfBootloader", "frtsSize"):
            add(f"{f} non-zero", values[f] != 0, f"{values[f]:#x}")
        for f in ("sysmemAddrOfRadix3Elf", "sysmemAddrOfBootloader"):
            add(f"{f} 4 KiB aligned", values[f] % 4096 == 0, f"{values[f]:#x}")
        touched = sorted(
            f for f, v in values.items()
            if f not in WPR_META_HOST_FIELDS and (any(v) if isinstance(v, list) else v != 0)
        )
        add("host-untouched fields are zero", not touched, ", ".join(touched) or "all zero")
    elif name == "GSP_FMC_BOOT_PARAMS":
        lay = layout(tag, name)
        if "magic" in values:
            add("magic", values["magic"] == FMC_BOOT_PARAMS_MAGIC, f"{values['magic']:#010x}")
            add("size", values["size"] == lay["size"], str(values["size"]))
            reserved = [f for f, v in values.items()
                        if "reserved" in f and (any(v) if isinstance(v, list) else v != 0)]
            add("reserved fields are zero", not reserved, ", ".join(reserved) or "all zero")
        add("gspRmDescSize", values["bootGspRmParams.gspRmDescSize"] == 256,
            f"{values['bootGspRmParams.gspRmDescSize']} (GspFwWprMeta and GspFwSRMeta are both 256)")
        for f in ("bootGspRmParams.target", "gspRmParams.target"):
            add(f"{f} in range", values[f] < 3, str(values[f]))
        add("bIsGspRmBoot", values["bootGspRmParams.bIsGspRmBoot"] == 1,
            str(values["bootGspRmParams.bIsGspRmBoot"]))
    elif name == "GspFwSRMeta":
        add("revision", values["revision"] == SR_META_REVISION, str(values["revision"]))
        add("sizeOfSuspendResumeData non-zero", values["sizeOfSuspendResumeData"] != 0,
            f"{values['sizeOfSuspendResumeData']:#x}")
    elif name == "NVDM_PAYLOAD_COT":
        add("size", values["size"] == layout(tag, name)["size"], str(values["size"]))
        version = values["version"]
        add("version (GB20x uses 2)", version == GB20X_COT_VERSION, str(version))
        for field, key in (("publicKey", "public_key"), ("signature", "signature")):
            want = COT_KEY_SIZES.get(version, {}).get(key)
            if want is not None:
                # Only whether the tail is zero is reported, never the material itself.
                raw = b"".join(v.to_bytes(4, "little") for v in values[field])
                add(f"{field} is zero past {want} bytes", not any(raw[want:]), f"{len(raw) - want} tail bytes")
    return checks


def print_layout(lay):
    print(f"{lay['struct']} @ {lay['tag']}  (size {lay['size']} = {lay['size']:#x})")
    host = lay["struct"] == "GspFwWprMeta"
    print("| Offset | Size | Field |" + (" Host writes |" if host else ""))
    print("|---:|---:|---|" + (":---:|" if host else ""))
    for f in lay["fields"]:
        suffix = f"[{f['count']}]" if f["count"] > 1 else ""
        row = f"| `{f['offset']:#05x}` | {f['size']} | `{f['name']}{suffix}` |"
        if host:
            row += " yes |" if f["name"] in WPR_META_HOST_FIELDS else " - |"
        print(row)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list known tags and struct sizes")
    ap.add_argument("--struct", choices=TOP_LEVEL, help="structure to inspect")
    ap.add_argument("--tag", default="620.73.04", help="upstream tag (default: 620.73.04)")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="show layout changes between two tags")
    ap.add_argument("--decode", type=Path, metavar="HEXFILE", help="decode a hex dump of --struct")
    ap.add_argument("--from-log", type=Path, metavar="DMESG", help="decode every BWLAB dump found in a dmesg text")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text (not with --from-log)")
    args = ap.parse_args(argv)

    if args.list:
        for tag in TAGS:
            sizes = []
            for name in TOP_LEVEL:
                try:
                    sizes.append(f"{name}={layout(tag, name)['size']}")
                except KeyError:
                    sizes.append(f"{name}=not recorded")
            print(f"{tag}: " + ", ".join(sizes))
        return 0
    if args.from_log:
        try:
            events = parse_log(args.from_log.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            print(f"abi-layout: {exc}", file=sys.stderr)
            return 2
        if not events:
            print(f"abi-layout: no BWLAB lines in {args.from_log}", file=sys.stderr)
            return 2
        lines, ok = report_log(events)
        print("\n".join(lines))
        return 0 if ok else 3
    if not args.struct:
        ap.error("--struct is required unless --list or --from-log is given")

    try:
        if args.diff:
            rows = diff_layouts(layout(args.diff[0], args.struct), layout(args.diff[1], args.struct))
            if args.json:
                print(json.dumps(rows, indent=2))
                return 0
            for r in rows:
                old = f"{r['old']:#05x}" if r["old"] is not None else "-"
                new = f"{r['new']:#05x}" if r["new"] is not None else "-"
                print(f"{r['change']:8s} {r['name']:40s} {old:>7} -> {new:>7}")
            if not rows:
                print("no layout change")
            return 0

        lay = layout(args.tag, args.struct)
        if args.decode:
            values = decode(args.tag, args.struct, parse_hex(args.decode.read_text(encoding="utf-8")))
            checks = check_decoded(args.tag, args.struct, values)
            if args.json:
                print(json.dumps({"values": values, "checks": checks}, indent=2))
            else:
                for f in lay["fields"]:
                    print("  " + format_field(f, values[f["name"]]))
                for label, ok, detail in checks:
                    print(f"  [{'ok' if ok else 'DIFF'}] {label}: {detail}")
            return 0 if all(ok for _, ok, _ in checks) else 3
        if args.json:
            print(json.dumps(lay, indent=2))
        else:
            print_layout(lay)
    except (KeyError, ValueError, OSError) as exc:
        print(f"abi-layout: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
