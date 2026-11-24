# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "scripts" / "parse-ssdiag.py"
NEW = ROOT / "reports" / "gb205-2f18" / "bar0-prom-metadata-621.01.txt"
OLD = ROOT / "reports" / "gb205-2f18" / "bar0-core-616.64.txt"


def load_module():
    spec = importlib.util.spec_from_file_location("parse_ssdiag", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.new = cls.mod.parse_capture(NEW.read_text(encoding="utf-8"))
        cls.old = cls.mod.parse_capture(OLD.read_text(encoding="utf-8"))

    def test_every_tracked_row_is_found(self):
        for name, _, _ in self.mod.MATRIX_ROWS:
            with self.subTest(row=name):
                self.assertIsNotNone(self.mod.lookup(self.new, name))

    def test_rows_not_sampled_stay_unknown(self):
        self.assertIsNone(self.mod.lookup(self.old, "SM_ISSUE_RATE_MODIFIER"))
        self.assertIsNone(self.mod.lookup(self.old, "SEC2_PLM"))

    def test_prom_offsets_are_not_mistaken_for_registers(self):
        text = "--- PROM BIT TABLE\n  FAKE_REG 0x00000000= 0xdeadbeef\n"
        self.assertEqual(self.mod.parse_capture(text)["by_addr"], {})

    def test_summary(self):
        info = self.mod.summarize(self.new)
        self.assertEqual(info["die"], "GB205")
        self.assertEqual(info["topology"], {"gpcs": 5, "tpcs": 23, "sms": 46})
        self.assertEqual(
            info["readout1_fields"],
            {"imla0": 0, "fmla16": 0, "fmla32": 3, "ffma": 0, "imla1": 3,
             "imla2": 3, "imla3": 0, "imla4": 3, "DP": 0},
        )

    def test_readout1_skips_the_holes(self):
        # Bits 6, 25, 26, 27 and 31 belong to no field.
        holes = (1 << 6) | (0b111 << 25) | (1 << 31)
        self.assertEqual(self.mod.decode_readout1(holes), [0] * 9)
        self.assertEqual(self.mod.decode_readout1(0xFFFFFFFF & ~holes), [7] * 9)

    def test_wpr2_conversion(self):
        new = self.mod.wpr2_bytes(0x02FB8800, 0x02FC1000)
        self.assertEqual(new["span"], 8704 * 1024)  # 8.5 MiB
        self.assertEqual(new["start"], 0x2FB880000)
        old = self.mod.wpr2_bytes(0x02FA7E00, 0x02FB0400)
        self.assertEqual(old["span"], 8576 * 1024)  # 8.375 MiB
        # Low nibble is outside VAL; an empty window is inactive.
        self.assertEqual(self.mod.wpr2_bytes(0x02FB880F, 0x02FC100F), new)
        self.assertFalse(self.mod.wpr2_bytes(0x1FFFFE00, 0)["active"])

    def test_wpr2_fits_in_framebuffer(self):
        for cap in (self.old, self.new):
            w = self.mod.summarize(cap)["wpr2"]
            self.assertTrue(w["active"])
            self.assertLessEqual(w["end"], self.mod.lookup(cap, "FB_SIZE_MIB") << 20)

    def test_driver_delta(self):
        changed = {r["name"] for r in self.mod.diff_captures(self.old, self.new)}
        self.assertEqual(
            changed,
            {"FEAT_SS0", "FEAT_SS1", "WPR2_LO", "WPR2_HI", "SM_ISSUE_RATE_MODIFIER", "SEC2_PLM"},
        )

    def test_shadow_selectors_have_enable_bits_clear(self):
        for cap in (self.old, self.new):
            for name in ("FEAT_SS0", "FEAT_SS1"):
                value = self.mod.lookup(cap, name)
                self.assertEqual(value & 0x88888888, 0)

    def test_cli(self):
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = self.mod.main([str(a) for a in args])
            return code, out.getvalue() + err.getvalue()

        code, text = run(OLD, "--diff", NEW)
        self.assertEqual(code, 0)
        self.assertIn("span 8.375 MiB", text)
        # SM_ISSUE_RATE_MODIFIER and SEC2_PLM were not sampled under 616.64.
        self.assertIn("4 of 28 tracked rows changed, 2 sampled in only one capture", text)
        self.assertEqual(run(NEW, "--markdown")[0], 0)
        self.assertEqual(run(NEW, "--json")[0], 0)
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_text("nothing here\n", encoding="utf-8")
            self.assertEqual(run(empty)[0], 2)
            self.assertEqual(run(Path(tmp) / "missing.txt")[0], 2)
            # A failed second capture must not be reported as register changes.
            for args in ((OLD, "--diff", empty), (empty, "--diff", OLD)):
                code, text = run(*args)
                self.assertEqual(code, 2)
                self.assertIn(str(empty), text)
                self.assertNotIn("tracked rows", text)


if __name__ == "__main__":
    unittest.main()
