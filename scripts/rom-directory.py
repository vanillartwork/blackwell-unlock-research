#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""List the GB20x object directories of a local ROM and match them to a V6 manifest.

GB20x images carry plaintext object directories: a 16-byte header (u32 version 3, header
size 16, object count, PCI device) followed by 60-byte entries (u32 id, u32 offset, u32
size, 48-byte SHA-384), with object data relative to the end of the entry table. The
layout is described by the independent GPL-3.0 project Panchovix/nvidia-vbios-vix-editor;
this is a separate implementation of those format facts.

A candidate counts as a directory only when its object hashes verify, so no file offset,
size or name is assumed. With --v6, every type 1 manifest entry is compared with the
object of the same id, and every V6 descriptor with the object that starts at it. Exit
code 3 means that one of them does not fit, or that the manifests contradict each other.

Reads one local file and prints ids, offsets and sizes. It never prints or stores
firmware bytes or digests, and it never writes to a device. Keep the ROM out of git;
--export writes the metadata that may be archived instead.

Usage:
  python scripts/rom-directory.py card.rom
  python scripts/rom-directory.py card.rom --v6 reports/gb205-2f18/v6-descriptors-98.05.31.00.44.json
  python scripts/rom-directory.py card.rom --v6 <json> --export rom-directory-<version>.json
"""

import argparse
from collections import Counter
import hashlib
import json
import struct
import sys
from pathlib import Path

MARKER = struct.pack("<II", 3, 16)
HEADER = 16
ENTRY = 60
MAX_OBJECTS = 1024
# On every GB20x ROM surveyed a directory starts 0x45a into its PCI image and the SHA-384 of
# the directory itself sits at image + 0x209, so the digest precedes it by this much.
IMAGE_HEAD = 0x45A
DIGEST_BACK = IMAGE_HEAD - 0x209
PCI_SIGNATURE = b"\x55\xaa"
DESC_SIZE = 80
MAX_ROM = 16 << 20
DIGEST_WORDS = {"verified": "verifies", "mismatch": "DOES NOT MATCH", "absent": "not found"}


def find_directories(blob):
    """Return every verified directory as {"offset", "device", "data_base", "objects"}.

    Objects whose hash fails are kept and flagged, but a candidate needs at least two
    objects and a majority of good hashes: random data cannot produce that.
    """
    found = []
    at = blob.find(MARKER)
    while at >= 0:
        candidate = _parse(blob, at)
        if candidate is not None:
            found.append(candidate)
        at = blob.find(MARKER, at + 1)
    return found


def _parse(blob, at):
    if at + HEADER > len(blob):
        return None
    _, _, count, device = struct.unpack_from("<IIII", blob, at)
    base = at + HEADER + count * ENTRY
    if not 2 <= count <= MAX_OBJECTS or base > len(blob):
        return None
    objects = []
    for n in range(count):
        oid, offset, size = struct.unpack_from("<III", blob, at + HEADER + n * ENTRY)
        start = base + offset
        if size == 0 or start + size > len(blob):
            return None
        digest = blob[at + HEADER + n * ENTRY + 12: at + HEADER + (n + 1) * ENTRY]
        objects.append({
            "id": oid,
            "offset": start,
            "size": size,
            "hash_ok": hashlib.sha384(blob[start: start + size]).digest() == digest,
        })
    if 2 * sum(o["hash_ok"] for o in objects) <= count:
        return None
    if len({o["id"] for o in objects}) != count:
        return None
    digest = directory_digest(blob, at, base)
    return {"offset": at, "device": device, "data_base": base, "digest": digest,
            "digest_ok": digest == "verified", "objects": objects}


def directory_digest(blob, at, base):
    """State of the SHA-384 of the directory at [at, base): "verified", "mismatch", "absent".

    "absent" means there is nothing to compare with: no PCI image starts where the digest's
    position is derived from, or the field is blank. A field that holds something else is a
    mismatch. Neither is as good as a digest that verifies.
    """
    digest_at, image = at - DIGEST_BACK, at - IMAGE_HEAD
    if digest_at < 0:
        return "absent"
    stored = blob[digest_at: digest_at + 48]
    if hashlib.sha384(blob[at:base]).digest() == stored:
        return "verified"
    if image < 0 or blob[image: image + 2] != PCI_SIGNATURE or len(set(stored)) == 1:
        return "absent"
    return "mismatch"


def manifest_ranges(v6):
    """Type 1 entries of a v6-descriptors JSON: [{"id", "start", "size", "apps", "conflict"}].

    Entries that agree in id, start and size are one row, since both manifests name some of
    the same objects. An id with more than one range contradicts itself: every version stays
    a row of its own, with the APPs that state it, so a good one cannot hide a bad one.
    """
    adjust = int(v6["specimen"]["adjust"], 16)
    rows = {}
    for m in v6["manifests"]:
        for e in m["entries"]:
            if e["type"] == 1:
                key = (int(e["entry_id"], 16), adjust + int(e["field_a"], 16), int(e["field_c"], 16))
                apps = rows.setdefault(key, [])
                app = m.get("app", "?")
                if app not in apps:
                    apps.append(app)
    versions = Counter(eid for eid, _, _ in rows)
    return [
        {"id": eid, "start": start, "size": size, "apps": rows[eid, start, size], "conflict": versions[eid] > 1}
        for eid, start, size in sorted(rows)
    ]


def match(directories, ranges):
    """Compare manifest ranges (see manifest_ranges) with same-id objects.

    Status per entry: "equal" (same start and size), "prefix" (same start, shorter than
    the object), "overrun" (same start, longer), "inside" (range lies within the object),
    "differs" (same id, other data), "absent".
    """
    order = ("equal", "prefix", "inside", "overrun", "differs")
    rows = []
    for r in ranges:
        eid, start, size = r["id"], r["start"], r["size"]
        best = None
        for d in directories:
            for o in d["objects"]:
                if o["id"] != eid:
                    continue
                if (o["offset"], o["size"]) == (start, size):
                    status = "equal"
                elif o["offset"] == start:
                    status = "prefix" if size < o["size"] else "overrun"
                elif o["offset"] <= start and start + size <= o["offset"] + o["size"]:
                    status = "inside"
                else:
                    status = "differs"
                rank = order.index(status)
                if best is None or rank < best[0]:
                    best = (rank, status, o)
        rows.append({
            **r,
            "status": best[1] if best else "absent",
            "object": {k: best[2][k] for k in ("offset", "size", "hash_ok")} if best else None,
        })
    return rows


def expected_descriptor_size(desc):
    """Descriptor + header rows + stored size, with rows * stride taken from +18 and +1C."""
    f = desc["fields"]
    rows, stride = int(f["18"], 16) >> 16, int(f["1C"], 16) & 0xFFFF
    return DESC_SIZE + rows * stride + desc["stored_size"]


def match_descriptors(directories, v6):
    """Compare each V6 descriptor with the directory object that starts at it."""
    rows = []
    for desc in v6["descriptors"]:
        at, want = int(desc["descriptor_offset"], 16), expected_descriptor_size(desc)
        hit = next((o for d in directories for o in d["objects"] if o["offset"] == at), None)
        if hit is None:
            status = "absent"
        else:
            status = "equal" if hit["size"] == want else "differs"
        rows.append({"app": desc["app"], "offset": at, "expected": want, "status": status,
                     "object": {"id": hit["id"], "size": hit["size"]} if hit else None})
    return rows


def export(directories, v6, same_rom):
    """Archivable metadata: no digests, no bytes, no file path.

    Takes directories as find_directories returns them, where every object's hash state is
    known. "hashes_failed" names the objects whose hash did not verify, by id, which is
    unique within a directory.
    """
    specimen = {}
    if v6 is not None and same_rom:
        keys = ("gpu", "die", "pci", "subsystem", "vbios", "rom_size", "rom_sha256")
        specimen = {k: v6["specimen"][k] for k in keys if k in v6["specimen"]}
    return {
        "schema": "blackwell-abi-lab/rom-directory/1",
        "note": "Object ids, offsets and sizes only; hashes were verified locally and are not stored.",
        "specimen": specimen,
        "directories": [
            {
                "offset": f"{d['offset']:#x}",
                "device": f"{d['device']:#06x}",
                "digest_ok": d["digest_ok"],
                "digest": d["digest"],
                "hashes_ok": sum(o["hash_ok"] for o in d["objects"]),
                "hashes_failed": [f"{o['id']:#04x}" for o in d["objects"] if not o["hash_ok"]],
                "objects": [
                    {"id": f"{o['id']:#04x}", "offset": f"{o['offset']:#x}", "size": f"{o['size']:#x}"}
                    for o in d["objects"]
                ],
            }
            for d in directories
        ],
    }


def load_export(data):
    """Turn an exported JSON back into the structure find_directories returns.

    Archives without "hashes_failed" store only how many hashes verified. Unless that count
    settles every object, hash_ok is None: unknown, neither good nor bad. Likewise an old
    archive with a false digest_ok does not say why, so its digest state is "unverified".
    """
    out = []
    for d in data["directories"]:
        ids = [int(o["id"], 16) for o in d["objects"]]
        if "hashes_failed" in d:
            failed = {int(x, 16) for x in d["hashes_failed"]}
            if not failed <= set(ids) or d["hashes_ok"] + len(failed) != len(ids):
                raise ValueError(f"directory {d['offset']}: hashes_ok and hashes_failed do not fit its objects")
            state = {oid: oid not in failed for oid in ids}
        elif d["hashes_ok"] in (0, len(ids)):
            state = dict.fromkeys(ids, d["hashes_ok"] == len(ids))
        else:
            state = dict.fromkeys(ids)
        out.append({
            "offset": int(d["offset"], 16),
            "device": int(d["device"], 16),
            "digest": d.get("digest", "verified" if d["digest_ok"] else "unverified"),
            "digest_ok": d["digest_ok"],
            "objects": [
                {"id": oid, "offset": int(o["offset"], 16), "size": int(o["size"], 16), "hash_ok": state[oid]}
                for oid, o in zip(ids, d["objects"])
            ],
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", type=Path, help="local ROM export (read only)")
    ap.add_argument("--v6", type=Path, help="v6-descriptors JSON to match against")
    ap.add_argument("--json", action="store_true", help="print JSON (metadata only)")
    ap.add_argument("--export", type=Path, help="write archivable metadata to this JSON file")
    args = ap.parse_args(argv)

    try:
        if args.rom.stat().st_size > MAX_ROM:
            print(f"rom-directory: {args.rom} is larger than {MAX_ROM >> 20} MiB", file=sys.stderr)
            return 2
        blob = args.rom.read_bytes()
        v6 = json.loads(args.v6.read_text(encoding="utf-8")) if args.v6 else None
        ranges = manifest_ranges(v6) if v6 else None
    except (OSError, ValueError, KeyError) as exc:
        print(f"rom-directory: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    directories = find_directories(blob)
    if not directories:
        print("rom-directory: no verified GB20x object directory found", file=sys.stderr)
        return 2
    rows = match(directories, ranges) if ranges is not None else None
    descs = match_descriptors(directories, v6) if v6 is not None else None
    same_rom = None
    if v6 is not None:
        same_rom = hashlib.sha256(blob).hexdigest() == v6["specimen"].get("rom_sha256")

    if args.json:
        print(json.dumps({"directories": directories, "manifest_match": rows,
                          "descriptor_match": descs, "same_rom": same_rom}, indent=2))
    else:
        for d in directories:
            good = sum(o["hash_ok"] for o in d["objects"])
            print(f"directory @ {d['offset']:#x}: device {d['device']:#06x}, "
                  f"{len(d['objects'])} objects, {good} hashes verify, "
                  f"directory digest {DIGEST_WORDS[d['digest']]}")
            for o in d["objects"]:
                flag = "" if o["hash_ok"] else "  HASH MISMATCH"
                print(f"  id {o['id']:#04x}  offset {o['offset']:#08x}  size {o['size']:#x}{flag}")
        if rows is not None:
            if not same_rom:
                print("note: this file is not the ROM the JSON was taken from; ranges may differ for that reason")
            for r in rows:
                print(f"  manifest {r['id']:#04x} [{r['start']:#x}, {r['start'] + r['size']:#x}): {r['status']}"
                      + (f"  CONFLICT, stated by APP {' '.join(r['apps'])}" if r["conflict"] else ""))
            for r in descs:
                print(f"  descriptor APP {r['app']} @ {r['offset']:#x}, expected {r['expected']:#x} bytes: {r['status']}")
            same = sum(r["status"] in ("equal", "prefix") for r in rows)
            exact = sum(r["status"] == "equal" for r in rows)
            print(f"{same}/{len(rows)} manifest entries start at the same-id object ({exact} with equal size)")
            clash = sorted({r["id"] for r in rows if r["conflict"]})
            if clash:
                print(f"{len(clash)} id(s) with contradictory ranges: {' '.join(f'{x:#04x}' for x in clash)}")
            print(f"{sum(r['status'] == 'equal' for r in descs)}/{len(descs)} descriptors fill their object exactly")
    if args.export:
        args.export.write_bytes((json.dumps(export(directories, v6, same_rom), indent=2) + "\n").encode("utf-8"))
        print(f"wrote {args.export}")
    if rows is None:
        return 0
    ok = (
        all(r["status"] in ("equal", "prefix") and not r["conflict"] for r in rows)
        and all(r["status"] == "equal" for r in descs)
    )
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
