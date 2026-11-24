# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify-baseline.py"
ARCHIVE = ROOT / "reports" / "gb205-2f18"


def load_verify_module():
    spec = importlib.util.spec_from_file_location("verify_baseline", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VerifyBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_verify_module()

    def test_parse_pci_devid_formats(self):
        self.assertEqual(self.mod.parse_pci_devid("0x2F1810DE"), 0x2F18)
        self.assertEqual(self.mod.parse_pci_devid("0x2b8510de"), 0x2B85)
        self.assertEqual(self.mod.parse_pci_devid("0x2C02"), 0x2C02)
        self.assertEqual(self.mod.parse_pci_devid("2f04"), 0x2F04)
        self.assertIsNone(self.mod.parse_pci_devid("0x2F188086"))
        self.assertIsNone(self.mod.parse_pci_devid("not-hex"))
        self.assertIsNone(self.mod.parse_pci_devid(""))
        self.assertIsNone(self.mod.parse_pci_devid(None))

    def test_classify_blackwell_devid(self):
        gb205 = self.mod.classify_blackwell_devid(0x2F18)
        self.assertEqual(gb205["known_die"], "GB205")
        self.assertTrue(gb205["blackwell_candidate"])

        gb202_pro = self.mod.classify_blackwell_devid(0x2BB1)
        self.assertEqual(gb202_pro["known_die"], "GB202")
        self.assertTrue(gb202_pro["blackwell_candidate"])

        unmapped_candidate = self.mod.classify_blackwell_devid(0x2D99)
        self.assertIsNone(unmapped_candidate["known_die"])
        self.assertTrue(unmapped_candidate["blackwell_candidate"])

        ada = self.mod.classify_blackwell_devid(0x2684)
        self.assertIsNone(ada["known_die"])
        self.assertFalse(ada["blackwell_candidate"])

        none_res = self.mod.classify_blackwell_devid(None)
        self.assertFalse(none_res["blackwell_candidate"])

    def test_known_ids_cover_every_consumer_die(self):
        dies = {die for die, _ in self.mod.KNOWN_BLACKWELL_DEVIDS.values()}
        self.assertEqual(dies, {"GB202", "GB203", "GB205", "GB206", "GB207"})
        self.assertEqual(self.mod.classify_blackwell_devid(0x2D83)["known_die"], "GB207")
        for devid, (die, _) in self.mod.KNOWN_BLACKWELL_DEVIDS.items():
            with self.subTest(devid=hex(devid)):
                self.assertEqual(
                    (devid >> 8), {"GB202": 0x2B, "GB203": 0x2C, "GB205": 0x2F}.get(die, 0x2D)
                )

    def test_parse_bar1(self):
        text = (
            "    FB Memory Usage\n        Total                  : 12227 MiB\n"
            "    BAR1 Memory Usage\n        Total                  : 16384 MiB\n"
            "        Used                   : 1 MiB\n"
        )
        self.assertEqual(self.mod.parse_bar1_mib(text), 16384)
        self.assertIsNone(self.mod.parse_bar1_mib("BAR1 Memory Usage\n    Total : N/A\n"))
        self.assertIsNone(self.mod.parse_bar1_mib(None))


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_verify_module()
        cls.ref = cls.mod.load_clamp_report(ARCHIVE / "clamp-eval-621.01-n8192.json")

    def test_identical_reports_match(self):
        cmp = self.mod.compare_clamp_reports(self.ref, self.ref, 0.0)
        self.assertEqual(cmp["drifted"], [])
        self.assertEqual(cmp["compared"], len(self.mod.COMPARE_CASES))
        self.assertEqual(cmp["context_differences"], {})

    def test_unclamped_result_is_flagged_as_drift(self):
        cur = copy.deepcopy(self.ref)
        row = next(r for r in cur["results"] if r["case"] == "fp16_fp32acc")
        row["ratio_of_full"] = 0.95
        cmp = self.mod.compare_clamp_reports(cur, self.ref, 0.06)
        self.assertEqual(cmp["drifted"], ["fp16_fp32acc"])

    def test_missing_cases_are_reported_not_failed(self):
        cur = copy.deepcopy(self.ref)
        cur["results"] = [r for r in cur["results"] if r["case"] != "fp8_fp32acc"]
        cmp = self.mod.compare_clamp_reports(cur, self.ref, 0.06)
        status = {r["case"]: r["status"] for r in cmp["rows"]}
        self.assertEqual(status["fp8_fp32acc"], "missing")
        self.assertEqual(cmp["drifted"], [])

    def test_context_differences_include_matrix_size(self):
        other = self.mod.load_clamp_report(ARCHIVE / "clamp-eval-621.01-n4096.json")
        cmp = self.mod.compare_clamp_reports(other, self.ref, 0.06)
        self.assertEqual(cmp["context_differences"]["n"], {"current": 4096, "reference": 8192})
        self.assertEqual(cmp["drifted"], [])

    def test_load_accepts_baseline_wrapper_and_rejects_other_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps({"clamp_eval": self.ref}), encoding="utf-8")
            self.assertEqual(self.mod.load_clamp_report(path)["results"], self.ref["results"])
            path.write_text(json.dumps({"clamp_eval": None}), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.mod.load_clamp_report(path)

    def run_main(self, *args):
        sink = io.StringIO()
        with mock.patch.object(sys, "argv", ["verify-baseline.py", *map(str, args)]), \
                mock.patch.object(self.mod, "query_nvidia_smi_gpus", return_value=(None, "no gpu")), \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                return self.mod.main()
            except SystemExit as exc:
                return exc.code

    def test_exit_codes(self):
        good = ARCHIVE / "clamp-eval-621.01-n8192.json"
        self.assertEqual(self.run_main("--clamp-json", good, "--compare", good), 0)
        self.assertEqual(
            self.run_main("--clamp-json", ARCHIVE / "clamp-eval-621.01-n4096.json",
                          "--compare", good, "--tolerance", "0.001"), 3)
        self.assertEqual(self.run_main("--compare", good), 2)
        self.assertEqual(self.run_main("--bench", "--clamp-json", good), 2)
        self.assertEqual(self.run_main("--clamp-json", good, "--compare", ROOT / "missing.json"), 2)

    def test_nothing_comparable_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps({"results": []}), encoding="utf-8")
            good = ARCHIVE / "clamp-eval-621.01-n8192.json"
            self.assertEqual(self.run_main("--clamp-json", empty, "--compare", good), 3)


if __name__ == "__main__":
    unittest.main()
