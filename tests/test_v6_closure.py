# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "scripts" / "v6-closure.py"
DATA_PATH = ROOT / "reports" / "gb205-2f18" / "v6-descriptors-98.05.31.00.44.json"


def load_module():
    spec = importlib.util.spec_from_file_location("v6_closure", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class V6ClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    def desc(self, app, data=None):
        return next(d for d in (data or self.data)["descriptors"] if d["app"] == app)

    def failed(self, data):
        return {label for label, ok, _ in self.mod.run_checks(data) if ok is False}

    def test_archived_specimen_closes(self):
        self.assertEqual(self.failed(self.data), set())

    def test_no_raw_bytes_in_archive(self):
        for d in self.data["descriptors"]:
            # +34..+4F is the opaque tail; only scalars up to +30 may be recorded.
            self.assertEqual(max(int(k, 16) for k in d["fields"]), 0x30)

    def test_residuals(self):
        got = {d["app"]: self.mod.residual(d) for d in self.data["descriptors"]}
        self.assertEqual(
            got, {"0x07": 0xA00, "0x19": 0, "0x15": 0, "0x23": 0xA00, "0x18": 0xA00, "0x24": 0}
        )

    def test_tags_span_both_fields(self):
        tags = {d["app"]: self.mod.printable_tag(d) for d in self.data["descriptors"]}
        self.assertEqual(tags["0x23"], "OOBHUB")
        self.assertEqual(tags["0x24"], "FSP_RUN")
        self.assertEqual([a for a, t in tags.items() if t is None], ["0x07", "0x19", "0x15", "0x18"])

    def test_t_space_gaps(self):
        gaps = {(a, b): g for a, b, g in self.mod.t_gaps(self.data["manifests"][0])}
        self.assertEqual(len(gaps), 10)
        self.assertEqual(gaps[("0x89", "0x71")], 0x3E4)
        self.assertEqual(gaps[("0x72", "0xd2")], 0xE30)
        self.assertEqual(gaps[("0x02", "0x12")], 0x1100)
        self.assertTrue(all(0 <= g < 16 for k, g in gaps.items()
                            if k not in {("0x89", "0x71"), ("0x72", "0xd2"), ("0x02", "0x12")}))

    def test_s_space_bounds(self):
        ranges = sorted(self.mod.s_entries(self.data).values())
        adjust = int(self.data["specimen"]["adjust"], 16)
        self.assertEqual(len(ranges), 10)
        self.assertEqual(adjust + ranges[0][0], 0x6676D)
        self.assertEqual(adjust + sum(ranges[-1]), 0xADB7D)

    def test_descriptor_boundaries(self):
        want = {"0x07": 0xCE324, "0x19": 0xEF364, "0x15": 0xF48F0, "0x23": 0x11B940,
                "0x18": 0x1282A4, "0x24": 0x153554}
        self.assertEqual({a: self.mod.descriptor_end(self.desc(a)) for a in want}, want)

    def test_row_area(self):
        got = {d["app"]: self.mod.row_area(d) for d in self.data["descriptors"]}
        self.assertEqual(got, {"0x07": 0, "0x19": 240, "0x15": 60, "0x23": 0, "0x18": 80, "0x24": 0})

    def test_mutations_are_detected(self):
        bad = copy.deepcopy(self.data)
        self.desc("0x19", bad)["fields"]["14"] = "0x00005501"
        self.assertIn("0x19 size residual", self.failed(bad))

        bad = copy.deepcopy(self.data)
        bad["manifests"][0]["entries"].pop()
        self.assertIn("0x19 +18 high half = rows", self.failed(bad))

        bad = copy.deepcopy(self.data)
        bad["manifests"][1]["entries"][1]["field_c"] = "0xc10"
        self.assertIn("shared entry 0xd2 keeps A/C/D", self.failed(bad))

        bad = copy.deepcopy(self.data)
        bad["specimen"]["adjust"] = "0x4d000"
        self.assertIn("adjust = image_base + expansion", self.failed(bad))

    def test_table_heads(self):
        heads = {eid: (h, length) for eid, h, length in self.mod.table_heads(self.data)}
        self.assertEqual(set(heads), {"0x12", "0x14", "0x70", "0x71", "0x81", "0x82"})
        exact = {
            eid for eid, (h, length) in heads.items()
            if h["header_size"] + h["record_size"] * h["count"] == length
        }
        # docs/vbios-v6-descriptor.md 6.9.1 names these five.
        self.assertEqual(exact, {"0x14", "0x70", "0x71", "0x81", "0x82"})

    def test_independent_shape_mismatch_is_reported(self):
        bad = copy.deepcopy(self.data)
        entry = next(e for e in bad["manifests"][0]["entries"] if e["entry_id"] == "0x14")
        entry["table_header"]["record_size"] = 28
        self.assertIn("0x14 head equals the independent parser's VFE table", self.failed(bad))
        entry["table_header"] = {"version": 0x30, "header_size": 14, "record_size": 29, "count": 256}
        self.assertEqual(self.failed(bad), {"0x14 table fits its S range"})

    def test_specimens_without_heads_still_check(self):
        bare = copy.deepcopy(self.data)
        for m in bare["manifests"]:
            for e in m["entries"]:
                e.pop("table_header", None)
        self.assertEqual(self.failed(bare), set())

    def test_cli(self):
        def run(path):
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                return self.mod.main([str(path)]), sink.getvalue()

        code, text = run(DATA_PATH)
        self.assertEqual(code, 0)
        self.assertIn("observations hold", text)
        self.assertEqual(run(ROOT / "README.md")[0], 2)
        self.assertEqual(run(ROOT / "does-not-exist.json")[0], 2)


if __name__ == "__main__":
    unittest.main()
