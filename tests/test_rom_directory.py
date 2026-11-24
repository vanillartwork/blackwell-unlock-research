# SPDX-License-Identifier: GPL-2.0-only
"""rom-directory.py against synthetic images; no real ROM is used or needed."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "scripts" / "rom-directory.py"
V6_PATH = ROOT / "reports" / "gb205-2f18" / "v6-descriptors-98.05.31.00.44.json"


def load_module():
    spec = importlib.util.spec_from_file_location("rom_directory", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(objects, at=0x100, device=0x2F18, total=0x3000, fill=0xA5, digest=False):
    """Return an image with one directory; objects is [(id, relative offset, data)].

    With digest, a PCI image starts 0x45a before the directory and the SHA-384 of the
    directory is stored 0x251 bytes before it.
    """
    blob = bytearray([fill]) * total
    base = at + 16 + 60 * len(objects)
    header = struct.pack("<IIII", 3, 16, len(objects), device)
    for oid, offset, data in objects:
        header += struct.pack("<III", oid, offset, len(data)) + hashlib.sha384(data).digest()
        blob[base + offset: base + offset + len(data)] = data
    blob[at: at + len(header)] = header
    if digest:
        blob[at - 0x45A: at - 0x458] = b"\x55\xaa"
        blob[at - 0x251: at - 0x251 + 48] = hashlib.sha384(header).digest()
    return bytes(blob), base


def ranges(*entries):
    """Manifest rows as manifest_ranges returns them, for [(id, start, size)]."""
    return [{"id": i, "start": s, "size": z, "apps": ["0x19"], "conflict": False} for i, s, z in entries]


OBJECTS = [(0xFB, 0x000, b"first" * 8), (0xBE, 0x100, b"second" * 9), (0x14, 0x200, bytes(range(200)))]


class DirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_directory_is_found_at_any_offset(self):
        for at in (0, 0x100, 0x123):
            blob, base = build(OBJECTS, at=at)
            found = self.mod.find_directories(blob)
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]["offset"], found[0]["data_base"], found[0]["device"]), (at, base, 0x2F18))
            self.assertEqual([o["id"] for o in found[0]["objects"]], [0xFB, 0xBE, 0x14])
            self.assertEqual(found[0]["objects"][2]["offset"], base + 0x200)
            self.assertTrue(all(o["hash_ok"] for o in found[0]["objects"]))

    def test_single_bad_hash_is_flagged_but_majority_is_required(self):
        blob, base = build(OBJECTS)
        one = bytearray(blob)
        one[base + 0x200] ^= 1
        found = self.mod.find_directories(bytes(one))
        self.assertEqual([o["hash_ok"] for o in found[0]["objects"]], [True, True, False])
        one[base + 0x100] ^= 1
        self.assertEqual(self.mod.find_directories(bytes(one)), [])

    def test_directory_digest(self):
        at = 0x500
        blob, _ = build(OBJECTS, at=at, digest=True)

        def state(change):
            b = bytearray(blob)
            change(b)
            found = self.mod.find_directories(bytes(b))[0]
            self.assertEqual(found["digest_ok"], found["digest"] == "verified")
            return found["digest"]

        def put(offset, data):
            def change(b):
                b[offset: offset + len(data)] = data
            return change

        self.assertEqual(state(lambda b: None), "verified")
        wrong = bytes([blob[at - 0x251] ^ 1])
        self.assertEqual(state(put(at - 0x251, wrong)), "mismatch")
        # A blank field, or no PCI image to derive the position from, leaves nothing to compare.
        for fill in (b"\0", b"\xff"):
            self.assertEqual(state(put(at - 0x251, fill * 48)), "absent")
        both = bytearray(blob)
        both[at - 0x251] ^= 1
        both[at - 0x45A] = 0
        self.assertEqual(self.mod.find_directories(bytes(both))[0]["digest"], "absent")
        # A digest that verifies needs no image header to be believed.
        self.assertEqual(state(put(at - 0x45A, b"\0\0")), "verified")
        # Directories too close to the start of the file have no room for either.
        for near in (0x10, 0x300):
            self.assertEqual(self.mod.find_directories(build(OBJECTS, at=near)[0])[0]["digest"], "absent")

    def test_lookalikes_are_rejected(self):
        blob, _ = build(OBJECTS)
        marker = struct.pack("<II", 3, 16)
        self.assertEqual(self.mod.find_directories(marker * 64), [])
        self.assertEqual(self.mod.find_directories(blob[:0x110]), [])  # truncated table
        self.assertEqual(self.mod.find_directories(b""), [])
        self.assertEqual(self.mod.find_directories(marker + struct.pack("<II", 5000, 0)), [])
        duplicate, _ = build([(0x14, 0, b"a" * 8), (0x14, 0x40, b"b" * 8)])
        self.assertEqual(self.mod.find_directories(duplicate), [])

    def test_two_directories_in_one_image(self):
        a, _ = build(OBJECTS, total=0x1000)
        b, _ = build([(0xFA, 0, b"x" * 16), (0xBD, 0x40, b"y" * 16)], total=0x1000)
        found = self.mod.find_directories(a + b)
        self.assertEqual([len(d["objects"]) for d in found], [3, 2])

    def test_match_statuses(self):
        blob, base = build(OBJECTS)
        dirs = self.mod.find_directories(blob)
        for label, entry, want in (
            ("equal", (0x14, base + 0x200, 200), "equal"),
            ("prefix", (0x14, base + 0x200, 100), "prefix"),
            ("overrun", (0x14, base + 0x200, 300), "overrun"),
            ("inside", (0x14, base + 0x210, 16), "inside"),
            ("differs", (0x14, base + 0x900, 16), "differs"),
            ("absent", (0x89, base, 16), "absent"),
        ):
            with self.subTest(case=label):
                self.assertEqual(self.mod.match(dirs, ranges(entry))[0]["status"], want)

    def test_best_match_wins_across_directories(self):
        a, _ = build([(0x14, 0, b"p" * 32), (0x15, 0x40, b"q" * 32)], total=0x1000)
        b, base = build(OBJECTS, total=0x2000)
        dirs = self.mod.find_directories(a + b)
        row = self.mod.match(dirs, ranges((0x14, 0x1000 + base + 0x200, 200)))[0]
        self.assertEqual(row["status"], "equal")

    def test_manifest_ranges_come_from_the_archived_json(self):
        rows = self.mod.manifest_ranges(json.loads(V6_PATH.read_text(encoding="utf-8")))
        self.assertEqual(len(rows), 10)  # distinct type 1 entries
        by_id = {r["id"]: r for r in rows}
        self.assertEqual((by_id[0x14]["start"], by_id[0x14]["size"]), (0x73B87, 0x1CF1))
        self.assertNotIn(0x02, by_id)  # type 2 entries have no source range
        # 0xD2 is the one id both manifests name, with the same range.
        self.assertEqual({r["id"]: r["apps"] for r in rows if len(r["apps"]) > 1}, {0xD2: ["0x19", "0x15"]})
        self.assertFalse(any(r["conflict"] for r in rows))

    def test_contradicting_ranges_stay_separate(self):
        def v6(*manifests):
            return {"specimen": {"adjust": "0x100"}, "manifests": [
                {"app": app, "entries": [
                    {"entry_id": hex(i), "type": 1, "field_a": hex(a), "field_c": hex(c)} for i, a, c in entries
                ]} for app, entries in manifests
            ]}

        good, shifted, longer = (0x14, 0x10, 8), (0x14, 0x11, 8), (0x14, 0x10, 9)
        same = self.mod.manifest_ranges(v6(("0x19", [good, good]), ("0x15", [good])))
        self.assertEqual(same, [{"id": 0x14, "start": 0x110, "size": 8, "apps": ["0x19", "0x15"], "conflict": False}])
        for other in (shifted, longer):
            for first, second in ((good, other), (other, good)):
                rows = self.mod.manifest_ranges(v6(("0x19", [first, (0x70, 0, 4)]), ("0x15", [second])))
                got = {(r["id"], r["start"], r["size"]): (r["apps"], r["conflict"]) for r in rows}
                self.assertEqual(got[0x14, 0x100 + first[1], first[2]], (["0x19"], True))
                self.assertEqual(got[0x14, 0x100 + second[1], second[2]], (["0x15"], True))
                self.assertEqual(got[0x70, 0x100, 4], (["0x19"], False))


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def damaged(self):
        blob, base = build(OBJECTS, at=0x500, digest=True)
        one = bytearray(blob)
        one[base + 0x100] ^= 1
        return self.mod.find_directories(bytes(one))

    def states(self, directories):
        return [o["hash_ok"] for o in directories[0]["objects"]]

    def test_one_failed_hash_survives_the_round_trip(self):
        live = self.damaged()
        data = self.mod.export(live, None, False)
        self.assertEqual(data["directories"][0]["hashes_failed"], ["0xbe"])
        loaded = self.mod.load_export(json.loads(json.dumps(data)))
        self.assertEqual(self.states(loaded), [True, False, True])
        self.assertEqual(loaded[0]["digest"], live[0]["digest"])

    def legacy(self, hashes_ok, digest_ok=True):
        data = self.mod.export(self.damaged(), None, False)
        d = data["directories"][0]
        del d["hashes_failed"], d["digest"]
        d["hashes_ok"], d["digest_ok"] = hashes_ok, digest_ok
        return self.mod.load_export(data)

    def test_older_archives_give_only_what_they_recorded(self):
        self.assertEqual(self.states(self.legacy(3)), [True] * 3)
        self.assertEqual(self.states(self.legacy(0)), [False] * 3)
        self.assertEqual(self.states(self.legacy(2)), [None] * 3)  # which one failed is unknown
        self.assertEqual(self.legacy(3)[0]["digest"], "verified")
        self.assertEqual(self.legacy(3, digest_ok=False)[0]["digest"], "unverified")

    def test_inconsistent_archives_are_refused(self):
        for change in ({"hashes_ok": 3}, {"hashes_failed": ["0x99"]}, {"hashes_failed": []}):
            data = self.mod.export(self.damaged(), None, False)
            data["directories"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.mod.load_export(data)

    def test_unknown_hash_states_pass_through_a_match(self):
        loaded = self.legacy(2)
        o = loaded[0]["objects"][0]
        row, = self.mod.match(loaded, ranges((o["id"], o["offset"], o["size"])))
        self.assertEqual((row["status"], row["object"]["hash_ok"]), ("equal", None))


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def run_cli(self, blob, *args):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "card.img"
            rom.write_bytes(blob)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.mod.main([str(rom), *args])
            return code, out.getvalue() + err.getvalue()

    def v6(self, tmp, blob, entries, adjust=0, descriptors=(), more=()):
        path = Path(tmp) / "v6.json"
        path.write_text(json.dumps({
            "specimen": {"adjust": hex(adjust), "rom_sha256": hashlib.sha256(blob).hexdigest(),
                         "gpu": "Test GPU"},
            "descriptors": [
                {"app": a, "descriptor_offset": hex(o), "stored_size": s, "fields": {"18": r, "1C": t}}
                for a, o, s, r, t in descriptors
            ],
            "manifests": [
                {"app": app, "entries": [
                    {"entry_id": hex(i), "type": t, "field_a": hex(a), "field_c": hex(c)} for i, t, a, c in group
                ]}
                for app, group in (("0x19", entries), ("0x15", more))
            ],
        }), encoding="utf-8")
        return str(path)

    def test_exit_codes(self):
        blob, base = build(OBJECTS)
        self.assertEqual(self.run_cli(blob)[0], 0)
        self.assertEqual(self.run_cli(b"\xff" * 4096)[0], 2)
        with tempfile.TemporaryDirectory() as tmp:
            good = self.v6(tmp, blob, [(0x14, 1, base + 0x200, 200), (0x02, 2, 1, 2)])
            code, text = self.run_cli(blob, "--v6", good)
            self.assertEqual(code, 0, text)
            self.assertIn("1/1 manifest entries start at the same-id object (1 with equal size)", text)
            self.assertNotIn("not the ROM", text)
            prefix = self.v6(tmp, blob, [(0x14, 1, base + 0x200, 100)])
            code, text = self.run_cli(blob, "--v6", prefix)
            self.assertEqual(code, 0, text)
            self.assertIn("(0 with equal size)", text)
            overrun = self.v6(tmp, blob, [(0x14, 1, base + 0x200, 300)])
            self.assertEqual(self.run_cli(blob, "--v6", overrun)[0], 3)
            bad = self.v6(tmp, blob, [(0x14, 1, base + 0x201, 200)])
            self.assertEqual(self.run_cli(blob, "--v6", bad)[0], 3)
            other = self.v6(tmp, b"other", [(0x14, 1, base + 0x200, 200)])
            self.assertIn("not the ROM", self.run_cli(blob, "--v6", other)[1])
            self.assertEqual(self.run_cli(blob, "--v6", str(Path(tmp) / "none.json"))[0], 2)

    def test_a_contradicted_entry_fails_in_either_order(self):
        blob, base = build(OBJECTS)
        good, bad = (0x14, 1, base + 0x200, 200), (0x14, 1, base + 0x201, 200)
        with tempfile.TemporaryDirectory() as tmp:
            for first, second in ((bad, good), (good, bad)):
                code, text = self.run_cli(blob, "--v6", self.v6(tmp, blob, [first], more=[second]))
                self.assertEqual(code, 3, text)
                self.assertIn("1/2 manifest entries", text)
                self.assertIn("1 id(s) with contradictory ranges: 0x14", text)
                where = {a: n for a, n in (("0x19", first), ("0x15", second))}
                for app, entry in where.items():
                    line = next(x for x in text.splitlines() if f"[{entry[2]:#x}," in x)
                    self.assertIn(f"CONFLICT, stated by APP {app}", line)
            # A prefix next to the whole object is a contradiction too, though each one fits.
            code, _ = self.run_cli(
                blob, "--v6", self.v6(tmp, blob, [good], more=[(0x14, 1, base + 0x200, 100)])
            )
            self.assertEqual(code, 3)
            # The same entry in both manifests is one row.
            code, text = self.run_cli(blob, "--v6", self.v6(tmp, blob, [good], more=[good]))
            self.assertEqual(code, 0, text)
            self.assertIn("1/1 manifest entries", text)
            self.assertNotIn("CONFLICT", text)

    def test_digest_state_is_printed(self):
        blob, _ = build(OBJECTS, at=0x500, digest=True)
        self.assertIn("directory digest verifies", self.run_cli(blob)[1])
        wrong = bytearray(blob)
        wrong[0x500 - 0x251] ^= 1
        self.assertIn("directory digest DOES NOT MATCH", self.run_cli(bytes(wrong))[1])
        self.assertIn("directory digest not found", self.run_cli(build(OBJECTS)[0])[1])

    def test_descriptors_must_fill_their_object(self):
        # 80-byte descriptor + 2 rows of 20 bytes + 80 stored bytes = the 200-byte object.
        blob, base = build(OBJECTS)
        with tempfile.TemporaryDirectory() as tmp:
            fits = [("0x18", base + 0x200, 80, "0x00020001", "0x01010014")]
            self.assertEqual(self.run_cli(blob, "--v6", self.v6(tmp, blob, [], descriptors=fits))[0], 0)
            for bad in (
                ("0x18", base + 0x200, 80, "0x00030001", "0x01010014"),
                ("0x18", base + 0x200, 81, "0x00020001", "0x01010014"),
                ("0x18", base + 0x201, 80, "0x00020001", "0x01010014"),
            ):
                path = self.v6(tmp, blob, [], descriptors=[bad])
                self.assertEqual(self.run_cli(blob, "--v6", path)[0], 3)

    def test_export_round_trips_and_stays_anonymous(self):
        blob, base = build(OBJECTS, at=0x500, digest=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dir.json"
            v6 = self.v6(tmp, blob, [(0x14, 1, base + 0x200, 200)])
            self.assertEqual(self.run_cli(blob, "--v6", v6, "--export", str(out))[0], 0)
            text = out.read_text(encoding="utf-8")
            data = json.loads(text)
            self.assertEqual(data["specimen"]["gpu"], "Test GPU")
            self.assertNotIn("card.img", text)
            self.assertNotIn(tmp, text)
            live, loaded = self.mod.find_directories(blob), self.mod.load_export(data)
            self.assertEqual([o["id"] for o in loaded[0]["objects"]], [o["id"] for o in live[0]["objects"]])
            self.assertEqual(
                self.mod.match(loaded, ranges((0x14, base + 0x200, 200))),
                self.mod.match(live, ranges((0x14, base + 0x200, 200))),
            )
            # A ROM other than the specimen must not borrow the specimen's identity.
            other = self.v6(tmp, b"other", [(0x14, 1, base + 0x200, 200)])
            self.run_cli(blob, "--v6", other, "--export", str(out))
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["specimen"], {})

    def test_output_holds_no_firmware_bytes_or_digests(self):
        blob, _ = build(OBJECTS)
        for args in ((), ("--json",)):
            _, text = self.run_cli(blob, *args)
            for _, _, data in OBJECTS:
                self.assertNotIn(data[:8].hex(), text)
                self.assertNotIn(hashlib.sha384(data).hexdigest()[:16], text)

    def test_oversized_file_is_refused(self):
        self.assertEqual(self.run_cli(b"\0" * ((16 << 20) + 1))[0], 2)


if __name__ == "__main__":
    unittest.main()
