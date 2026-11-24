# SPDX-License-Identifier: GPL-2.0-only
"""rom-survey.py against synthetic images; no real ROM is used or needed."""

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
TOOL_PATH = ROOT / "scripts" / "rom-survey.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rom_survey", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def v6(stored, rows=0, stride=0, tag=b"", body=b"", flags=5):
    """One V6 descriptor followed by its row area and payload."""
    head = bytearray(80)
    struct.pack_into("<II", head, 0, flags | 6 << 8 | 80 << 16, stored)
    struct.pack_into("<II", head, 0x18, rows << 16 | 1, stride)
    struct.pack_into("<I", head, 0x28, 0x01012801)
    head[0x2C:0x34] = tag.rjust(8, b"\0")
    return bytes(head) + body


def rows(entries, outer=0x00350001):
    out = struct.pack("<IIIII", outer, 0, 0, 0, 0)
    for kind, eid, a, c in entries:
        out += struct.pack("<HHIIII", kind, eid, a, 0, c, 0)
    return out


def directory(objects, device):
    """Directory bytes for objects [(id, data)], data packed back to back."""
    table, data = b"", b""
    for oid, content in objects:
        table += struct.pack("<III", oid, len(data), len(content)) + hashlib.sha384(content).digest()
        data += content
    return struct.pack("<IIII", 3, 16, len(objects), device) + table + data


def image(content, count, digest="good"):
    """A PCI image that starts 0x45a before a directory of count objects and holds its digest.

    digest: "good", "wrong", "blank", or "none" for data without an image header.
    """
    head = bytearray(b"\xff" * 0x45A)
    if digest != "none":
        head[0:2] = b"\x55\xaa"
    if digest in ("good", "wrong"):
        value = bytearray(hashlib.sha384(content[: 16 + 60 * count]).digest())
        value[0] ^= digest == "wrong"
        head[0x209: 0x209 + 48] = value
    return bytes(head) + content


def rom(device=0x2F18, shared=b"family payload", table=b"T" * 64, entry_shift=0, prefix=0, stored=32,
        digests=("good", "good")):
    """A card image: one card directory (tables + Class A descriptor) and one 0x2b80 directory.

    Manifest offsets are relative to 0x40 before the card directory's data area, so the
    common base the tool has to find is not the start of anything.
    """
    lead = 0x45A + 16 + 60 * 3
    base_rel = lead - 0x40
    tables = [(0x14, table), (0x70, b"second table" * 4)]
    a14, a70 = 0x40 + entry_shift, 0x40 + len(table)
    manifest = rows([(1, 0x14, a14, len(table)), (2, 0x02, 1, 2), (1, 0x70, a70, 48)])
    desc = v6(stored, rows=4, stride=20, body=manifest + b"P" * 32)
    card = directory(tables + [(0x87, desc)], device)
    fam = directory([(0x86, v6(len(shared), tag=b"FSP_RUN", body=shared)), (0xD7, b"pcir" * 8)], 0x2B80)
    return (
        b"\xff" * prefix + image(card, 3, digests[0]) + b"\xff" * 64 + image(fam, 2, digests[1]),
        prefix + base_rel,
    )


class SurveyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.tool = cls.mod._load_directory_tool()

    def survey(self, blob):
        return self.mod.survey(blob, self.tool)

    def test_descriptors_and_manifest_of_one_rom(self):
        for prefix in (0, 0x9400):  # with and without a tool header in front
            blob, base = rom(prefix=prefix)
            result = self.survey(blob)
            self.assertEqual([d["id"] for d in result["descriptors"]], [0x87, 0x86])
            self.assertTrue(all(d["closes"] for d in result["descriptors"]))
            self.assertEqual([d["tag"] for d in result["descriptors"]], [None, "FSP_RUN"])
            m, = result["manifests"]
            self.assertEqual((m["object"], m["entries"], m["equal"], m["other"], m["base"]), (0x87, 2, 2, 0, base))
            v = self.mod.verdict(result)
            self.assertEqual((v["closing"], v["descriptors"], v["matching"], v["entries"]), (2, 2, 2, 2))
            self.assertEqual((v["hashes_ok"], v["objects"], v["digests_ok"], v["directories"]), (5, 5, 2, 2))
            self.assertTrue(v["ok"])

    def test_wrong_stored_size_does_not_close(self):
        result = self.survey(rom(stored=33)[0])
        self.assertEqual([d["closes"] for d in result["descriptors"]], [False, True])

    def test_entries_need_one_common_base(self):
        # Shifting one entry leaves no base that satisfies both ids.
        result = self.survey(rom(entry_shift=4)[0])
        m, = result["manifests"]
        self.assertIsNone(m["base"])
        self.assertEqual(m["other"], 2)

    def test_prefix_entries_count_as_matches(self):
        blob, _ = rom(table=b"T" * 64)
        result = self.survey(blob)
        self.assertEqual(result["manifests"][0]["equal"], 2)
        table = b"T" * 64
        manifest = rows([(1, 0x14, 0, 32), (1, 0x70, 64, 48)])
        desc = v6(0, rows=3, stride=20, body=manifest)
        card = directory([(0x14, table), (0x70, b"second table" * 4), (0x87, desc)], 0x2C02)
        m, = self.survey(card)["manifests"]
        self.assertEqual((m["equal"], m["prefix"], m["other"]), (1, 1, 0))

    def test_verdict_needs_every_hash_and_every_digest(self):
        for digests, mismatch, absent in (
            (("good", "wrong"), 1, 0),
            (("blank", "good"), 0, 1),
            (("none", "good"), 0, 1),
            (("wrong", "none"), 1, 1),
        ):
            with self.subTest(digests=digests):
                v = self.mod.verdict(self.survey(rom(digests=digests)[0]))
                self.assertEqual((v["digests_mismatch"], v["digests_absent"]), (mismatch, absent))
                self.assertEqual(v["digests_ok"] + mismatch + absent, v["directories"])
                self.assertEqual(v["hashes_ok"], v["objects"])
                self.assertFalse(v["ok"])

    def test_rows_without_the_class_a_word_are_not_a_manifest(self):
        desc = v6(0, rows=4, stride=20, body=rows([(1, 0x14, 0, 4)] * 3, outer=0x12345678))
        card = directory([(0x14, b"data"), (0x86, desc)], 0x2F18)
        result = self.survey(card)
        self.assertEqual(result["manifests"], [])
        self.assertTrue(result["descriptors"][0]["closes"])

    def test_non_v6_objects_are_ignored(self):
        v3 = struct.pack("<I", 1 | 3 << 8 | 44 << 16) + b"\0" * 200
        result = self.survey(directory([(1, v3), (2, b"short")], 0x2F18))
        self.assertEqual(result["descriptors"], [])


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.tool = cls.mod._load_directory_tool()

    def compare(self, *blobs):
        results = [self.mod.survey(b, self.tool) for b in blobs]
        maps = [self.mod.object_digests(b, r) for b, r in zip(blobs, results)]
        return self.mod.group_identical(maps, self.mod.shared_devices(results))

    def test_family_objects_are_compared_across_cards(self):
        family, own = self.compare(rom(0x2F18)[0], rom(0x2C02)[0], rom(0x2B85, shared=b"other payload!")[0])
        self.assertEqual(family[(0x2B80, 0x86)], ["A", "A", "B"])
        self.assertFalse(self.mod.is_identical(family[(0x2B80, 0x86)]))
        self.assertTrue(self.mod.is_identical(family[(0x2B80, 0xD7)]))
        # Card-specific directories never share a device field, so they compare by id.
        self.assertEqual(own[0x14], ["A", "A", "A"])
        self.assertNotIn((0x2F18, 0x14), family)

    def test_two_roms_of_one_product_still_compare_with_other_cards(self):
        family, own = self.compare(
            rom(0x2C05)[0], rom(0x2C05, table=b"U" * 64)[0], rom(0x2C02, table=b"U" * 64)[0]
        )
        self.assertEqual({device for device, _ in family}, {0x2B80})
        self.assertEqual(own[0x14], ["A", "B", "B"])
        self.assertEqual(own[0x70], ["A", "A", "A"])

    def test_missing_objects_show_as_gaps(self):
        lone = directory([(0x86, b"family"), (0x99, b"extra")], 0x2B80)
        family, _ = self.compare(rom()[0], lone)
        self.assertIn((0x2B80, 0x86), family)
        self.assertNotIn((0x2B80, 0x99), family)

    def test_repeated_ids_need_every_copy_to_match(self):
        first = directory([(0x03, b"one"), (0x04, b"pad!")], 0x2F18)
        same = directory([(0x03, b"two"), (0x05, b"pad?")], 0x2F18)
        other = directory([(0x03, b"TWO"), (0x05, b"pad?")], 0x2F18)
        _, own = self.compare(first + b"\xff" * 8 + same, first + b"\xff" * 8 + other)
        self.assertEqual(own, {})  # one device only: nothing marks it as a card's own
        family, _ = self.compare(first + b"\xff" * 8 + same, first + b"\xff" * 8 + other)
        self.assertEqual(family[(0x2F18, 0x03)], ["A", "B"])
        self.assertEqual(family[(0x2F18, 0x04)], ["A", "A"])
        # The same holds when only the first copy differs.
        changed = directory([(0x03, b"ONE"), (0x04, b"pad!")], 0x2F18)
        family, _ = self.compare(first + b"pad" + same, changed + b"pad" + same)
        self.assertEqual(family[(0x2F18, 0x03)], ["A", "B"])
        self.assertEqual(family[(0x2F18, 0x05)], ["A", "A"])


    def test_tail_grouping_is_reported_separately_from_the_payload(self):
        def with_tail(blob, tail):
            at = blob.index(b"FSP_RUN") - 1 - 0x2C  # start of the family descriptor
            patched = bytearray(blob)
            patched[at + 0x34: at + 80] = tail * 28
            # Repair the object hash so the directory still verifies.
            head = blob.index(struct.pack("<IIII", 3, 16, 2, 0x2B80))
            size = struct.unpack_from("<I", blob, head + 16 + 8)[0]
            patched[head + 16 + 12: head + 16 + 60] = hashlib.sha384(patched[at: at + size]).digest()
            return bytes(patched)

        base = rom()[0]
        blobs = [with_tail(base, b"\x01"), with_tail(base, b"\x02"), with_tail(base, b"\x01")]
        results = [self.mod.survey(b, self.tool) for b in blobs]
        groups = {g["id"]: g for g in self.mod.descriptor_groups(blobs, results)}
        self.assertEqual(groups[0x86]["payload"], ["A", "A", "A"])
        self.assertEqual(groups[0x86]["tail"], ["A", "B", "A"])
        self.assertEqual(groups[0x87]["payload"], groups[0x87]["tail"])


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.tool = cls.mod._load_directory_tool()

    def image(self, version=0x98053100, oem=0x44, spoil=False):
        """A legacy PCI image with a BIT (tokens 0x69 and 0x70) and one directory."""
        blob = bytearray(0x1000)
        blob[0:2] = b"\x55\xaa"
        struct.pack_into("<H", blob, 0x18, 0x40)
        blob[0x40:0x44] = b"PCIR"
        struct.pack_into("<HH", blob, 0x44, 0x10DE, 0x2F18)
        struct.pack_into("<H", blob, 0x50, 0x1000 // 512)
        blob[0x54] = 0x00
        data = directory([(0x2D, b"bit object"), (0x99, b"other")], 0x2F18)
        blob[0x45A: 0x45A + len(data)] = data
        bit = bytearray(self.mod.BIT_SIGNATURE + b"\x00\x01" + bytes([12, 6, 2, 0]))
        bit[11] = -sum(bit) & 0xFF
        if spoil:
            bit[11] ^= 1
        bit += struct.pack("<BBHH", 0x69, 2, 5, 0x900) + struct.pack("<BBHH", 0x70, 2, 4, 0x910)
        blob[0x800: 0x800 + len(bit)] = bit
        struct.pack_into("<IB", blob, 0x900, version, oem)
        struct.pack_into("<I", blob, 0x910, 0x1234)
        return bytes(blob)

    def test_version_die_and_pointer(self):
        blob = self.image()
        ident = self.mod.identity(blob, self.tool.find_directories(blob))
        self.assertEqual(ident["vbios"], "98.05.31.00.44")
        self.assertEqual((ident["die"], ident["pci_device"], ident["image_base"]), ("GB205", 0x2F18, 0))
        self.assertEqual(ident["ucode_pointer"], 0x1234)

    def test_unknown_die_is_not_guessed(self):
        blob = self.image(version=0x98993100)
        self.assertIsNone(self.mod.identity(blob, self.tool.find_directories(blob))["die"])

    def test_bad_checksum_or_missing_bit_gives_nothing(self):
        blob = self.image(spoil=True)
        self.assertEqual(self.mod.identity(blob, self.tool.find_directories(blob)), {})
        self.assertEqual(self.mod.identity(b"\0" * 64, []), {})

    def test_table_head_is_recorded_only_when_it_closes(self):
        def head(data, length, oid):
            return self.mod.table_head(bytes(data), 0, length, {"id": oid})

        self.assertEqual(head([0x10, 4, 14, 4], 60, 0x81)["count"], 4)
        self.assertIsNone(head([0x10, 4, 14, 4], 61, 0x81))
        self.assertIsNone(head([0x10, 8, 137, 0], 8, 0x89))
        self.assertEqual(head([0x30, 24, 25, 11], 1320, 0x12)["record_size"], 25)
        self.assertIsNone(head([0x30, 24, 25, 11], 1320, 0x13))


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def run_cli(self, files, *args):
        with tempfile.TemporaryDirectory() as tmp:
            for name, blob in files.items():
                (Path(tmp) / name).write_bytes(blob)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.mod.main([tmp, *[a.replace("TMP", tmp) for a in args]])
            exported = Path(tmp) / "out.json"
            data = exported.read_text(encoding="utf-8") if exported.exists() else None
            return code, out.getvalue() + err.getvalue(), data, tmp

    def test_exit_codes(self):
        good = rom()[0]
        self.assertEqual(self.run_cli({"a.rom": good})[0], 0)
        self.assertEqual(self.run_cli({"a.rom": rom(stored=33)[0]})[0], 3)
        self.assertEqual(self.run_cli({"a.rom": rom(entry_shift=4)[0]})[0], 3)
        self.assertEqual(self.run_cli({"a.rom": b"\xff" * 4096})[0], 2)
        self.assertEqual(self.run_cli({})[0], 2)

    def test_modified_rom_is_flagged(self):
        blob = bytearray(rom()[0])
        at = bytes(blob).index(b"T" * 64)
        blob[at] ^= 1
        code, text, _, _ = self.run_cli({"mod.rom": bytes(blob)})
        self.assertEqual(code, 3)
        self.assertIn("object hashes 4/5 verify (1 MISMATCH), directory digests 2/2 verify", text)
        self.assertIn("DIFF", text)

    def test_unverified_digest_is_never_an_ok(self):
        for digests, want in (
            (("good", "wrong"), "directory digests 1/2 verify (1 MISMATCH)"),
            (("good", "none"), "directory digests 1/2 verify (1 absent)"),
            (("wrong", "blank"), "directory digests 0/2 verify (1 MISMATCH, 1 absent)"),
        ):
            with self.subTest(digests=digests):
                code, text, exported, _ = self.run_cli({"a.rom": rom(digests=digests)[0]}, "--export", "TMP/out.json")
                self.assertEqual(code, 3)
                self.assertIn("object hashes 5/5 verify, " + want, text)
                self.assertIn("; DIFF", text)
                self.assertNotIn("; ok", text)
                record = json.loads(exported)["roms"][0]["directories"]
                names = {"good": "verified", "wrong": "mismatch"}
                self.assertEqual([d["digest"] for d in record], [names.get(x, "absent") for x in digests])
                self.assertEqual([d["digest_ok"] for d in record], [x == "good" for x in digests])
        code, text, _, _ = self.run_cli({"a.rom": rom()[0]})
        self.assertEqual(code, 0)
        self.assertIn("object hashes 5/5 verify, directory digests 2/2 verify;", text)
        self.assertIn("; ok", text)

    def test_a_copy_is_not_a_second_specimen(self):
        a, b = rom(0x2C05)[0], rom(0x2C02, table=b"U" * 64)[0]
        files = {"1.rom": a, "2-copy.rom": a, "3.rom": b, "4-copy.rom": a}
        code, text, exported, _ = self.run_cli(files, "--export", "TMP/out.json")
        self.assertEqual(code, 0, text)
        roms = json.loads(exported)["roms"]
        self.assertEqual([r["rom_sha256"] for r in roms], [hashlib.sha256(x).hexdigest() for x in (a, b)])
        for copy in ("2-copy.rom", "4-copy.rom"):
            self.assertIn(f"{copy}: same content as 1.rom, skipped", text)
        self.assertNotIn("same product", text)
        for o in json.loads(exported)["card_objects"]:
            self.assertEqual(len(o["groups"]), 2)

    def test_a_file_given_twice_counts_once(self):
        # A directory and one of its files, as in the review.
        with tempfile.TemporaryDirectory() as tmp:
            path, sub = Path(tmp) / "a.rom", Path(tmp) / "sub"
            path.write_bytes(rom()[0])
            sub.mkdir()
            (sub / "a.rom").write_bytes(rom(0x2C02, table=b"U" * 64)[0])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.mod.main([tmp, str(path), str(sub / "a.rom")])
        self.assertEqual(code, 0)
        self.assertIn("a.rom: same content as a.rom, skipped", err.getvalue())
        # A different ROM of the same file name keeps a label of its own.
        self.assertIn("\nsub/a.rom: VBIOS", out.getvalue())
        self.assertEqual(out.getvalue().count(": VBIOS"), 2)

    def test_same_objects_in_another_wrapping_are_pointed_out(self):
        files = {"a.rom": rom()[0], "b.rom": rom(prefix=0x400)[0], "c.rom": rom(table=b"U" * 64)[0]}
        code, text, exported, _ = self.run_cli(files, "--export", "TMP/out.json")
        self.assertEqual(code, 0, text)
        self.assertEqual(len(json.loads(exported)["roms"]), 3)  # different files stay separate records
        self.assertIn("note: a.rom and b.rom hold the same objects throughout; count them as one specimen", text)
        self.assertEqual(text.count("note:"), 1)

    def test_unusable_files_are_skipped_not_fatal(self):
        code, text, _, _ = self.run_cli({"a.rom": rom()[0], "b.rom": b"junk", "readme.txt": b"x"})
        self.assertEqual(code, 0)
        self.assertIn("b.rom: no verified GB20x object directory, skipped", text)
        self.assertNotIn("readme.txt", text)

    def test_summary_and_anonymous_export(self):
        files = {"a.rom": rom(0x2F18)[0], "b.rom": rom(0x2C02)[0]}
        code, text, exported, tmp = self.run_cli(files, "--export", "TMP/out.json")
        self.assertEqual(code, 0, text)
        self.assertIn("device 0x2b80 object 0x86: A A  (identical in 2)", text)
        self.assertIn("card-specific objects: 3 ids occur in two or more ROMs", text)
        self.assertNotIn("same product", text)
        _, text, _, _ = self.run_cli({"a.rom": rom(0x2C05)[0], "b.rom": rom(0x2C05, table=b"U" * 64)[0]})
        self.assertIn("same product 0x2c05: a.rom and b.rom share 2 of 3 card objects", text)
        data = json.loads(exported)
        self.assertEqual(len(data["roms"]), 2)
        for secret in (tmp, "a.rom", "family payload", hashlib.sha384(b"T" * 64).hexdigest()[:16]):
            self.assertNotIn(secret, exported)
        for blob_part in (b"T" * 8, b"family"):
            self.assertNotIn(blob_part.hex(), text)


class V6FileTests(unittest.TestCase):
    """File names under --v6-dir; the documents themselves are stand-ins."""

    def setUp(self):
        self.mod = load_module()
        self.versions = {}

        def stand_in(blob, result):
            sha = hashlib.sha256(blob).hexdigest()
            version = self.versions.get(sha, "98.03.00.00.01")
            return None if version == "incomplete" else {"specimen": {"vbios": version, "rom_sha256": sha}}

        self.mod.to_v6_json = stand_in
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def survey(self, *blobs):
        paths = []
        for n, blob in enumerate(blobs):
            paths.append(self.tmp / f"{n}.rom")
            paths[-1].write_bytes(blob)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.mod.main([*map(str, paths), "--export", str(self.tmp / "o.json"), "--v6-dir", str(self.tmp / "v6")])
        return json.loads((self.tmp / "o.json").read_text(encoding="utf-8"))["roms"]

    def stored(self):
        return {
            p.name: json.loads(p.read_text(encoding="utf-8"))["specimen"]["rom_sha256"]
            for p in (self.tmp / "v6").iterdir()
        }

    A, B = rom(0x2C05)[0], rom(0x2C05, table=b"U" * 64)[0]

    def test_one_rom_per_version_keeps_the_plain_name(self):
        self.versions[hashlib.sha256(self.B).hexdigest()] = "98.03.00.00.02"
        records = self.survey(self.A, self.B)
        self.assertEqual(
            [r["v6_file"] for r in records],
            ["v6-descriptors-98.03.00.00.01.json", "v6-descriptors-98.03.00.00.02.json"],
        )

    def test_two_roms_of_one_version_get_a_file_each(self):
        records = self.survey(self.A, self.B)
        self.assertEqual(len({r["v6_file"] for r in records}), 2)
        stored = self.stored()
        self.assertEqual(len(stored), 2)
        for r in records:
            self.assertEqual(stored[r["v6_file"]], r["rom_sha256"])
            self.assertIn(r["rom_sha256"][:12], r["v6_file"])
        # The names do not depend on the order.
        names = {r["rom_sha256"]: r["v6_file"] for r in records}
        self.assertEqual({r["rom_sha256"]: r["v6_file"] for r in self.survey(self.B, self.A)}, names)
        self.assertEqual(self.stored(), stored)

    def test_an_earlier_run_is_not_overwritten(self):
        first, = self.survey(self.A)
        self.assertEqual(first["v6_file"], "v6-descriptors-98.03.00.00.01.json")
        second, = self.survey(self.B)
        self.assertNotEqual(second["v6_file"], first["v6_file"])
        stored = self.stored()
        self.assertEqual(stored[first["v6_file"]], first["rom_sha256"])
        self.assertEqual(stored[second["v6_file"]], second["rom_sha256"])
        # Both together, later: the archived name stays with the ROM it belongs to.
        both = {r["rom_sha256"]: r["v6_file"] for r in self.survey(self.A, self.B)}
        self.assertEqual(both[first["rom_sha256"]], first["v6_file"])
        self.assertEqual(both[second["rom_sha256"]], second["v6_file"])
        self.assertEqual(self.stored(), stored)

    def test_an_unreadable_file_is_left_alone(self):
        (self.tmp / "v6").mkdir()
        taken = self.tmp / "v6" / "v6-descriptors-98.03.00.00.01.json"
        for text in ("not json", "[]", "{}"):
            taken.write_text(text, encoding="utf-8")
            record, = self.survey(self.A)
            self.assertIn(record["rom_sha256"][:12], record["v6_file"])
            self.assertEqual(taken.read_text(encoding="utf-8"), text)

    def test_unknown_version_and_incomplete_rom(self):
        self.versions[hashlib.sha256(self.A).hexdigest()] = None
        self.versions[hashlib.sha256(self.B).hexdigest()] = "incomplete"
        a, b = self.survey(self.A, self.B)
        self.assertEqual(a["v6_file"], f"v6-descriptors-unknown-{a['rom_sha256'][:12]}.json")
        self.assertIsNone(b["v6_file"])
        self.assertEqual(list(self.stored()), [a["v6_file"]])


if __name__ == "__main__":
    unittest.main()
