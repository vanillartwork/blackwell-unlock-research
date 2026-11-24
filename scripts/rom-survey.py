#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Survey several local GB20x ROMs and test which GB205 observations carry over.

For every ROM, using only its own verified object directories (see rom-directory.py):
  - V6 descriptors: every object that starts with a version 6, 80-byte descriptor is
    checked against 80 + hi16(+18) x lo16(+1C) + stored_size = object size;
  - Class A manifests: every type 1 entry must start at the object of the same id with one
    common base for all entries of the ROM;
then, across ROMs:
  - objects of directories whose device field occurs in two or more ROMs without being any
    card's own device (the 0x2b80 images on every ROM so far), compared id by id;
  - the remaining, card-specific objects, compared by id regardless of device;
  - per V6 descriptor, whether equal payloads go with equal opaque tails and vice versa.

Nothing is assumed about file offsets, versions or names, so a ROM from another card, or
one saved with a tool header in front, is tested without any prepared JSON. Objects are
compared by SHA-384 in memory; the output shows only group letters, ids, offsets, sizes
and counts. Keep ROMs out of git.

Every ROM counts once: a file whose content was already read is reported and skipped, so
that a copy cannot support a conclusion a second time. A ROM is "ok" only when all object
hashes and all directory digests verify and every check above holds.

"identical" is a statement about the ROMs that were passed in. It covers the family only
as far as they do: use different dies, and note the VBIOS versions.

--v6-dir additionally writes one v6-descriptors JSON per ROM (decoded scalars only), so
v6-closure.py can test every GB205 observation on that card. The VBIOS version names the
file; when another ROM has the same version, the start of the ROM's SHA-256 is added. The
export names each ROM's file in "v6_file".

Usage:
  python scripts/rom-survey.py C:/roms/*.rom
  python scripts/rom-survey.py <directory> --export rom-survey.json --v6-dir out/
"""

import argparse
from collections import Counter
import glob
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

DESC_SIZE = 80
ROW = 20
CLASS_A_OUTER = 0x00350001
BIT_SIGNATURE = b"\xff\xb8BIT\0"
# Every directory seen so far starts this far into its PCI image.
IMAGE_HEAD = 0x45A
# Second byte of the VBIOS version, as on every ROM surveyed so far.
DIE_BY_VERSION = {0x02: "GB202", 0x03: "GB203", 0x05: "GB205", 0x06: "GB206", 0x07: "GB207"}
# Scalars of the descriptor that may be recorded; the opaque tail from +34 may not.
FIELD_OFFSETS = ("04", "08", "0C", "10", "14", "18", "1C", "20", "24", "28", "2C", "30")
SUFFIXES = {".rom", ".bin", ".bios"}


def _load_directory_tool():
    path = Path(__file__).resolve().parent / "rom-directory.py"
    spec = importlib.util.spec_from_file_location("rom_directory", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def v6_objects(blob, directories):
    """Objects that start with a V6 descriptor: [{id, offset, size, flags, rows, ...}]."""
    out = []
    for d in directories:
        for o in d["objects"]:
            if o["size"] < DESC_SIZE or not o["hash_ok"]:
                continue
            head, stored = struct.unpack_from("<II", blob, o["offset"])
            # Bit 0 = version field valid, bits 15:8 = version, bits 31:16 = size.
            if not head & 1 or (head >> 8) & 0xFF != 6 or head >> 16 != DESC_SIZE:
                continue
            w18, w1c = struct.unpack_from("<II", blob, o["offset"] + 0x18)
            rows, stride = w18 >> 16, w1c & 0xFFFF
            w28, w2c, w30 = struct.unpack_from("<III", blob, o["offset"] + 0x28)
            tag = (w2c.to_bytes(4, "little") + w30.to_bytes(4, "little")).lstrip(b"\0")
            out.append({
                "id": o["id"],
                "offset": o["offset"],
                "size": o["size"],
                "flags": head & 0xFF,
                "stored_size": stored,
                "rows": rows,
                "stride": stride,
                "const_28": w28,
                "tag": tag.decode("ascii") if len(tag) >= 4 and all(0x21 <= c < 0x7F for c in tag) else None,
                "closes": DESC_SIZE + rows * stride + stored == o["size"],
            })
    return out


def manifests(blob, directories, descriptors):
    """Class A manifests matched to objects: [{app object id, base, entries, ...}]."""
    by_id = {}
    for d in directories:
        for o in d["objects"]:
            by_id.setdefault(o["id"], []).append(o)
    out = []
    for desc in descriptors:
        if desc["stride"] != ROW or desc["rows"] < 2:
            continue
        at = desc["offset"] + DESC_SIZE
        if struct.unpack_from("<I", blob, at)[0] != CLASS_A_OUTER:
            continue
        entries = []
        for n in range(1, desc["rows"]):
            kind, eid, a, b, c, dd = struct.unpack_from("<HHIIII", blob, at + n * ROW)
            if kind == 1:
                entries.append((eid, a, c))
        # Candidate bases: wherever a same-id object could place the first entry.
        bases = None
        for eid, a, _ in entries:
            here = {o["offset"] - a for o in by_id.get(eid, [])}
            bases = here if bases is None else bases & here
        base = min(bases) if bases else None
        rel = {"equal": 0, "prefix": 0, "other": 0}
        for eid, a, c in entries:
            hit = next((o for o in by_id.get(eid, []) if base is not None and o["offset"] == base + a), None)
            if hit is None:
                rel["other"] += 1
            elif hit["size"] == c:
                rel["equal"] += 1
            elif hit["size"] > c:
                rel["prefix"] += 1
            else:
                rel["other"] += 1
        out.append({"object": desc["id"], "rows": desc["rows"], "entries": len(entries),
                    "ids": [e[0] for e in entries], "base": base, **rel})
    return out


def _pci_image(blob, start):
    """(length, code type, device) of the PCI image at start, or None."""
    if start < 0 or start + 0x1A > len(blob):
        return None
    pcir = start + struct.unpack_from("<H", blob, start + 0x18)[0]
    if blob[pcir: pcir + 4] not in (b"PCIR", b"NPDS", b"RGIS") or pcir + 0x15 > len(blob):
        return None
    return (
        struct.unpack_from("<H", blob, pcir + 0x10)[0] * 512,
        blob[pcir + 0x14],
        struct.unpack_from("<H", blob, pcir + 6)[0],
    )


def identity(blob, directories):
    """VBIOS version, die and image layout from the checksummed BIT; {} when not found."""
    at = blob.find(BIT_SIGNATURE)
    if at < 0 or at + 12 > len(blob):
        return {}
    hs, ts, n = blob[at + 8], blob[at + 9], blob[at + 10]
    if hs != 12 or ts != 6 or at + hs + n * ts > len(blob) or sum(blob[at: at + hs]) & 0xFF:
        return {}
    starts = [d["offset"] - IMAGE_HEAD for d in directories if d["offset"] - IMAGE_HEAD <= at]
    base = max(starts, default=None)
    image = _pci_image(blob, base) if base is not None else None
    if image is None or image[1] != 0x00:
        return {}
    out = {"bit_offset": at, "bit_tokens": n, "image_base": base, "pci_device": image[2]}
    efi = _pci_image(blob, base + image[0])
    out["expansion"] = efi[0] if efi is not None and efi[1] == 0x03 else None
    for k in range(n):
        tid, tver, size, ptr = struct.unpack_from("<BBHH", blob, at + hs + k * ts)
        if tid == 0x69 and size >= 5:
            v, oem = struct.unpack_from("<IB", blob, base + ptr)
            out["vbios"] = ".".join(f"{x:02X}" for x in (*v.to_bytes(4, "big"), oem))
            out["die"] = DIE_BY_VERSION.get(v >> 16 & 0xFF)
        elif tid == 0x70 and tver == 2 and size >= 4:
            out["ucode_pointer"] = struct.unpack_from("<I", blob, base + ptr)[0]
    return out


def ucode_table(blob, ident, adjust):
    """Slots of the BIT ucode table: {offset, slots, apps: [(app, target, descriptor)]}."""
    if adjust is None or "ucode_pointer" not in ident:
        return None
    at = adjust + ident["ucode_pointer"]
    if at + 6 > len(blob) or blob[at] != 1 or blob[at + 1] != 6 or blob[at + 2] != 6:
        return None
    count = blob[at + 3]
    if at + 6 + count * 6 > len(blob):
        return None
    apps = []
    for k in range(count):
        app, target, ptr = struct.unpack_from("<BBI", blob, at + 6 + k * 6)
        if ptr:
            apps.append((app, target, adjust + ptr))
    return {"offset": at, "slots": count, "apps": apps}


def to_v6_json(blob, result):
    """One ROM in the v6-descriptors schema, decoded scalars only; None when incomplete."""
    ident, table = result["identity"], result["ucode"]
    if table is None or not result["manifests"] or ident.get("expansion") is None:
        return None
    by_offset = {d["offset"]: d for d in result["descriptors"]}
    descriptors, app_of = [], {}
    for app, target, at in sorted(table["apps"], key=lambda a: a[2]):
        desc = by_offset.get(at)
        if desc is None:
            return None
        app_of[desc["id"]] = f"0x{app:02x}"
        descriptors.append({
            "app": f"0x{app:02x}",
            "target": f"0x{target:02x}",
            "descriptor_offset": f"{at:#x}",
            "version": 6,
            "header_size": DESC_SIZE,
            "stored_size": desc["stored_size"],
            "flags": f"0x{desc['flags']:02x}",
            "fields": {
                o: f"0x{struct.unpack_from('<I', blob, at + int(o, 16))[0]:08x}" for o in FIELD_OFFSETS
            },
        })
    sizes = {o["id"]: o for d in result["directories"] for o in d["objects"]}
    out_manifests = []
    for m in result["manifests"]:
        at = next(d["offset"] for d in result["descriptors"] if d["id"] == m["object"]) + DESC_SIZE
        outer = struct.unpack_from("<IIIII", blob, at)
        entries = []
        for n in range(1, m["rows"]):
            kind, eid, a, b, c, dd = struct.unpack_from("<HHIIII", blob, at + n * ROW)
            entry = {
                "entry_id": f"0x{eid:02x}", "type": kind, "field_a": f"0x{a:x}",
                "field_b": f"0x{b:x}", "field_c": f"0x{c:x}", "field_d": f"0x{dd:x}",
            }
            head = table_head(blob, m["base"] + a, c, sizes.get(eid)) if kind == 1 else None
            if head:
                entry["table_header"] = head
            entries.append(entry)
        out_manifests.append({
            "app": app_of[m["object"]],
            "outer_header": {k: f"0x{v:08x}" for k, v in zip(("00", "04", "08", "0C", "10"), outer)},
            "entries": entries,
        })
    return {
        "schema": "blackwell-abi-lab/v6-descriptors/1",
        "note": "Decoded scalars and lengths only; no raw firmware bytes. The opaque 28-byte descriptor tail is omitted.",
        "specimen": {
            "die": ident.get("die"),
            "pci": f"10de:{ident['pci_device']:04x}",
            "vbios": ident.get("vbios"),
            "rom_size": len(blob),
            "rom_sha256": hashlib.sha256(blob).hexdigest(),
            "image_base": f"{ident['image_base']:#x}",
            "expansion": f"{ident['expansion']:#x}",
            "adjust": f"{result['manifests'][0]['base']:#x}",
            "bit_offset": f"{ident['bit_offset']:#x}",
            "ucode_table_offset": f"{table['offset']:#x}",
            "ucode_slots": table["slots"],
            "ucode_empty_slots": table["slots"] - len(table["apps"]),
        },
        "descriptors": descriptors,
        "manifests": out_manifests,
    }


def table_head(blob, start, length, owner):
    """Header of a table entry when it is decoded: fills the range exactly, or is id 0x12.

    Four leading bytes are only a header when the arithmetic closes; otherwise they would
    be undecoded firmware bytes and stay out of the record. 0x12 is recorded because an
    independent parser documents that header (docs/vbios-v6-descriptor.md 6.10.1).
    """
    if start + 4 > len(blob):
        return None
    version, hs, rs, n = blob[start: start + 4]
    known = owner is not None and owner["id"] == 0x12 and (version, hs, rs) == (0x30, 24, 25)
    if not known and (n == 0 or hs + rs * n != length):
        return None
    return {"version": version, "header_size": hs, "record_size": rs, "count": n}


def survey(blob, tool):
    directories = tool.find_directories(blob)
    descriptors = v6_objects(blob, directories)
    found = manifests(blob, directories, descriptors)
    ident = identity(blob, directories)
    bases = {m["base"] for m in found}
    adjust = bases.pop() if len(bases) == 1 else None
    return {
        "directories": directories,
        "descriptors": descriptors,
        "manifests": found,
        "identity": ident,
        "ucode": ucode_table(blob, ident, adjust),
    }


def object_digests(blob, result):
    """{(device, object id): digest}; for in-memory comparison only, never printed.

    An id that occurs in several directories of one device is folded into one value, so
    it only counts as identical when every copy is.
    """
    parts = {}
    for d in result["directories"]:
        for o in d["objects"]:
            digest = hashlib.sha384(blob[o["offset"]: o["offset"] + o["size"]]).digest()
            parts.setdefault((d["device"], o["id"]), []).append(digest)
    return {k: hashlib.sha384(b"".join(v)).digest() for k, v in parts.items()}


def own_device(result):
    """The card's own device field: that of its largest directory."""
    return max(result["directories"], key=lambda d: len(d["objects"]))["device"]


def shared_devices(results):
    """Device fields common to two or more ROMs that are not any card's own device.

    Two ROMs of one product share their own device as well; those objects are card-specific
    and must be compared by id with every other card, not set apart.
    """
    seen, own = {}, set()
    for r in results:
        devices = {d["device"] for d in r["directories"]}
        for device in devices:
            seen[device] = seen.get(device, 0) + 1
        if len(devices) > 1:
            own.add(own_device(r))
    return {device for device, n in seen.items() if n >= 2 and device not in own}


def _letters(values):
    letters, seen = [], {}
    for v in values:
        if v is None:
            letters.append(None)
            continue
        if v not in seen:
            seen[v] = chr(ord("A") + len(seen)) if len(seen) < 26 else f"#{len(seen)}"
        letters.append(seen[v])
    return letters


def group_identical(digest_maps, shared):
    """Compare objects across ROMs; equal letters mean equal bytes.

    Returns (family, own): {(device, id): letters} for shared devices and {id: letters}
    for everything else. Only objects present in two or more ROMs are listed.
    """
    family, own = {}, {}
    for key in sorted(set().union(*digest_maps)) if digest_maps else []:
        if key[0] in shared:
            letters = _letters([m.get(key) for m in digest_maps])
            if sum(x is not None for x in letters) >= 2:
                family[key] = letters
    own_maps = [{k[1]: v for k, v in m.items() if k[0] not in shared} for m in digest_maps]
    for oid in sorted(set().union(*own_maps)) if own_maps else []:
        letters = _letters([m.get(oid) for m in own_maps])
        if sum(x is not None for x in letters) >= 2:
            own[oid] = letters
    return family, own


def descriptor_groups(blobs, results):
    """Per V6 object id, letters for the payload and for the opaque tail in each ROM.

    The tail (+34..+4F) is compared in memory only. Equal groupings say the tail follows
    the payload across the ROMs given; they do not say what the tail is.
    """
    ids = sorted({d["id"] for r in results for d in r["descriptors"]})
    out = []
    for oid in ids:
        payloads, tails = [], []
        for blob, r in zip(blobs, results):
            d = next((x for x in r["descriptors"] if x["id"] == oid), None)
            if d is None:
                payloads.append(None)
                tails.append(None)
                continue
            at = d["offset"] + DESC_SIZE + d["rows"] * d["stride"]
            payloads.append(hashlib.sha384(blob[at: at + d["stored_size"]]).digest())
            tails.append(hashlib.sha384(blob[d["offset"] + 0x34: d["offset"] + DESC_SIZE]).digest())
        out.append({"id": oid, "payload": _letters(payloads), "tail": _letters(tails)})
    return out


def is_identical(letters):
    return len({x for x in letters if x is not None}) == 1


def expand(paths):
    files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files += sorted(x for x in path.iterdir() if x.suffix.lower() in SUFFIXES)
        elif any(c in p for c in "*?["):
            files += sorted(Path(x) for x in glob.glob(p))
        else:
            files.append(path)
    return files


def verdict(result):
    """Counts behind one ROM's conclusion, and whether everything holds ("ok")."""
    ds, ms = result["descriptors"], result["manifests"]
    objects = [o for d in result["directories"] for o in d["objects"]]
    states = Counter(d["digest"] for d in result["directories"])
    v = {
        "closing": sum(d["closes"] for d in ds),
        "descriptors": len(ds),
        "matching": sum(m["equal"] + m["prefix"] for m in ms),
        "entries": sum(m["entries"] for m in ms),
        "bases": sorted({m["base"] for m in ms if m["base"] is not None}),
        "hashes_ok": sum(o["hash_ok"] for o in objects),
        "objects": len(objects),
        "digests_ok": states["verified"],
        "digests_mismatch": states["mismatch"],
        "digests_absent": states["absent"],
        "directories": len(result["directories"]),
    }
    v["ok"] = (
        v["closing"] == v["descriptors"] > 0
        and v["matching"] == v["entries"]
        and len(v["bases"]) <= 1
        and v["hashes_ok"] == v["objects"]
        and v["digests_ok"] == v["directories"]
    )
    return v


def evidence(v):
    """The hash and digest counts of a verdict as text."""
    bad = v["objects"] - v["hashes_ok"]
    off = [f"{v[k]} {word}" for k, word in (("digests_mismatch", "MISMATCH"), ("digests_absent", "absent")) if v[k]]
    return (
        f"object hashes {v['hashes_ok']}/{v['objects']} verify" + (f" ({bad} MISMATCH)" if bad else "")
        + f", directory digests {v['digests_ok']}/{v['directories']} verify" + (f" ({', '.join(off)})" if off else "")
    )


def v6_names(documents, directory):
    """A file name per v6-descriptors document (None stays None); no two ROMs share one.

    The version alone is the name while it identifies the ROM, which keeps the names of
    earlier archives. When another ROM of this run has the same version, or the name already
    holds a different ROM, the start of the ROM's SHA-256 is added.
    """
    versions = Counter(d["specimen"]["vbios"] for d in documents if d is not None)
    names = []
    for d in documents:
        if d is None:
            names.append(None)
            continue
        vbios, sha = d["specimen"]["vbios"], d["specimen"]["rom_sha256"]
        name = f"v6-descriptors-{vbios}.json"
        owner = _v6_owner(directory / name)
        if not vbios or (owner != sha and (owner is not None or versions[vbios] > 1)):
            name = f"v6-descriptors-{vbios or 'unknown'}-{sha[:12]}.json"
        names.append(name)
    return names


def _v6_owner(path):
    """SHA-256 of the ROM an existing document describes; None without one, "" if unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["specimen"]["rom_sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roms", nargs="+", help="ROM files, globs or directories")
    ap.add_argument("--export", type=Path, help="write metadata (no bytes, digests or paths) to JSON")
    ap.add_argument("--v6-dir", type=Path, help="write one v6-descriptors JSON per ROM into this directory")
    args = ap.parse_args(argv)

    tool = _load_directory_tool()
    labels, blobs, results, digest_maps, exported, documents = [], [], [], [], [], []
    seen = {}
    for path in expand(args.roms):
        try:
            if path.stat().st_size > tool.MAX_ROM:
                print(f"{path.name}: larger than {tool.MAX_ROM >> 20} MiB, skipped", file=sys.stderr)
                continue
            blob = path.read_bytes()
        except OSError as exc:
            print(f"rom-survey: {exc}", file=sys.stderr)
            return 2
        sha = hashlib.sha256(blob).hexdigest()
        label = path.name if path.name not in labels else f"{path.parent.name or '.'}/{path.name}"
        if sha in seen:
            print(f"{label}: same content as {seen[sha]}, skipped", file=sys.stderr)
            continue
        result = survey(blob, tool)
        if not result["directories"]:
            print(f"{label}: no verified GB20x object directory, skipped", file=sys.stderr)
            continue
        seen[sha] = label
        labels.append(label)
        blobs.append(blob)
        results.append(result)
        digest_maps.append(object_digests(blob, result))
        exported.append({
            "rom_size": len(blob),
            "rom_sha256": sha,
            "v6_file": None,
            "identity": {k: result["identity"].get(k) for k in ("vbios", "die", "pci_device")},
            "directories": tool.export(result["directories"], None, False)["directories"],
            "descriptors": result["descriptors"],
            "manifests": result["manifests"],
        })
        if args.v6_dir:
            documents.append(to_v6_json(blob, result))
            if documents[-1] is None:
                print(f"{label}: incomplete, no v6-descriptors JSON written", file=sys.stderr)
    if not results:
        print("rom-survey: no usable GB20x ROM", file=sys.stderr)
        return 2
    if args.v6_dir:
        for record, v6, name in zip(exported, documents, v6_names(documents, args.v6_dir)):
            record["v6_file"] = name
            if v6 is not None:
                args.v6_dir.mkdir(parents=True, exist_ok=True)
                (args.v6_dir / name).write_bytes((json.dumps(v6, indent=2) + "\n").encode("utf-8"))

    failed = False
    for label, result in zip(labels, results):
        v = verdict(result)
        devices = sorted({d["device"] for d in result["directories"]})
        failed |= not v["ok"]
        ident = result["identity"]
        print(f"{label}: VBIOS {ident.get('vbios', 'unknown')} ({ident.get('die') or 'unknown die'})")
        print(f"  {evidence(v)}; devices {', '.join(f'{x:#06x}' for x in devices)}")
        print(f"  descriptors {v['closing']}/{v['descriptors']} close; "
              f"manifest entries {v['matching']}/{v['entries']} on same-id objects; "
              f"base {', '.join(f'{x:#x}' for x in v['bases']) or 'n/a'}; {'ok' if v['ok'] else 'DIFF'}")
        if result["ucode"]:
            t = result["ucode"]
            print(f"  ucode table {t['slots']} slots, {len(t['apps'])} in use: "
                  + " ".join(f"{a:#04x}" for a, _, _ in t["apps"]))
        for d in result["descriptors"]:
            print(f"    object {d['id']:#04x} @ {d['offset']:#08x} flags {d['flags']:#04x} "
                  f"rows {d['rows']} x {d['stride']} +28 {d['const_28']:#010x} "
                  f"tag {d['tag'] or '-'} {'closes' if d['closes'] else 'DOES NOT CLOSE'}")

    family, own = group_identical(digest_maps, shared_devices(results))
    if len(results) > 1:
        print("\nacross ROMs (equal letters = identical object content, in the order above):")
        for (device, oid), letters in family.items():
            n = sum(x is not None for x in letters)
            print(f"  device {device:#06x} object {oid:#04x}: {' '.join(x or '-' for x in letters)}  "
                  f"({'identical' if is_identical(letters) else 'differs'} in {n})")
        if not family:
            print("  no device field is shared by two ROMs")
        for g in descriptor_groups(blobs, results):
            print(f"  V6 object {g['id']:#04x}: payload {' '.join(x or '-' for x in g['payload'])}  "
                  f"tail {' '.join(x or '-' for x in g['tail'])}  "
                  f"({'same grouping' if g['payload'] == g['tail'] else 'groupings DIFFER'})")
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                if digest_maps[i] == digest_maps[j]:
                    print(f"  note: {labels[i]} and {labels[j]} hold the same objects throughout; "
                          "count them as one specimen")
                if own_device(results[i]) != own_device(results[j]):
                    continue
                both = [x for x in own.values() if x[i] and x[j]]
                print(f"  same product {own_device(results[i]):#06x}: {labels[i]} and {labels[j]} share "
                      f"{sum(x[i] == x[j] for x in both)} of {len(both)} card objects")
        same = [oid for oid, letters in own.items() if is_identical(letters)]
        print(f"  card-specific objects: {len(own)} ids occur in two or more ROMs, {len(same)} of them identical"
              + (": " + " ".join(f"{x:#04x}" for x in same) if same else ""))
    if args.export:
        data = {
            "schema": "blackwell-abi-lab/rom-survey/1",
            "note": "Metadata only. Letters group identical contents in the order of roms; no digests are stored.",
            "roms": exported,
            "family_objects": [
                {"device": f"{dev:#06x}", "id": f"{oid:#04x}", "groups": letters}
                for (dev, oid), letters in family.items()
            ],
            "card_objects": [{"id": f"{oid:#04x}", "groups": letters} for oid, letters in own.items()],
            "descriptor_groups": [
                {"id": f"{g['id']:#04x}", "payload": g["payload"], "tail": g["tail"]}
                for g in descriptor_groups(blobs, results)
            ],
        }
        args.export.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
        print(f"wrote {args.export}")
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
