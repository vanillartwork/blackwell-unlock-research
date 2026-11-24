# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


TOOL_PATH = Path(__file__).resolve().parents[1] / "scripts" / "abi-layout.py"


def load_module():
    spec = importlib.util.spec_from_file_location("abi_layout", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def offsets(self, tag, name):
        return {f["name"]: f["offset"] for f in self.mod.layout(tag, name)["fields"]}

    def test_sizes_match_upstream_asserts(self):
        # Upstream ct_asserts 256 for both structs on the tags that carry the assert.
        for tag in ("615.71.10", "620.73.04"):
            self.assertEqual(self.mod.layout(tag, "GSP_FMC_BOOT_PARAMS")["size"], 256)
            self.assertEqual(self.mod.layout(tag, "GspFwSRMeta")["size"], 256)
        for tag in self.mod.TAGS:
            self.assertEqual(self.mod.layout(tag, "GspFwWprMeta")["size"], 256)
            self.assertEqual(self.mod.layout(tag, "NVDM_PAYLOAD_COT")["size"], 860)

    def test_pre_magic_sizes(self):
        self.assertEqual(self.mod.layout("570.124.04", "GSP_FMC_BOOT_PARAMS")["size"], 80)
        self.assertEqual(self.mod.layout("610.57.04", "GSP_FMC_BOOT_PARAMS")["size"], 88)

    def test_fields_are_aligned_and_disjoint(self):
        for tag in self.mod.TAGS:
            for name in self.mod.TOP_LEVEL:
                try:
                    lay = self.mod.layout(tag, name)
                except KeyError:
                    continue
                end = 0
                for f in lay["fields"]:
                    with self.subTest(tag=tag, struct=name, field=f["name"]):
                        self.assertGreaterEqual(f["offset"], end)
                        unit = f["size"] // f["count"]
                        if name not in self.mod.PACKED:
                            self.assertEqual(f["offset"] % unit, 0)
                        end = f["offset"] + f["size"]
                self.assertLessEqual(end, lay["size"])

    def test_key_offsets(self):
        fmc = self.offsets("620.73.04", "GSP_FMC_BOOT_PARAMS")
        self.assertEqual(fmc["bootGspRmParams.gspRmDescOffset"], 0x48)
        self.assertEqual(fmc["gspTdispParams.bTdispEnabled"], 0xF8)
        wpr = self.offsets("620.73.04", "GspFwWprMeta")
        self.assertEqual((wpr["bootCount"], wpr["verified"]), (0xC8, 0xF8))
        cot = self.offsets("620.73.04", "NVDM_PAYLOAD_COT")
        self.assertEqual(cot["gspBootArgsSysmemOffset"], 0x354)

    def test_host_fields_exist_and_exclude_firmware_outputs(self):
        names = set(self.offsets("620.73.04", "GspFwWprMeta"))
        self.assertLessEqual(self.mod.WPR_META_HOST_FIELDS, names)
        for f in ("gspFwWprStart", "gspFwWprEnd", "fbSize", "bootCount", "verified"):
            self.assertNotIn(f, self.mod.WPR_META_HOST_FIELDS)

    def test_diff_reports_the_615_reshuffle(self):
        rows = {
            r["name"]: r
            for r in self.mod.diff_layouts(
                self.mod.layout("610.57.04", "GSP_FMC_BOOT_PARAMS"),
                self.mod.layout("615.71.10", "GSP_FMC_BOOT_PARAMS"),
            )
        }
        self.assertEqual(rows["magic"]["change"], "added")
        self.assertEqual((rows["gspRmParams.bootArgsOffset"]["old"], rows["gspRmParams.bootArgsOffset"]["new"]), (0x30, 0x88))
        same = self.mod.layout("615.71.10", "GspFwWprMeta")
        self.assertEqual(self.mod.diff_layouts(same, self.mod.layout("620.73.04", "GspFwWprMeta")), [])

    def test_unknown_tag_and_unrecorded_struct(self):
        with self.assertRaises(KeyError):
            self.mod.layout("1.2.3", "GspFwWprMeta")
        with self.assertRaises(KeyError):
            self.mod.layout("610.57.04", "GspFwSRMeta")


class DecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def build(self, tag, name, values):
        lay = self.mod.layout(tag, name)
        data = bytearray(lay["size"])
        for f in lay["fields"]:
            if f["name"] in values:
                data[f["offset"]: f["offset"] + f["size"]] = values[f["name"]].to_bytes(f["size"], "little")
        return bytes(data)

    def wpr(self, **extra):
        values = {
            "magic": self.mod.WPR_META_MAGIC,
            "revision": 1,
            "sysmemAddrOfRadix3Elf": 0x1_0000_0000,
            "sizeOfRadix3Elf": 0x4000000,
            "sysmemAddrOfBootloader": 0x1_1000_0000,
            "sizeOfBootloader": 0x2000,
            "frtsSize": 0x100000,
        }
        values.update(extra)
        return self.build("620.73.04", "GspFwWprMeta", values)

    def failures(self, name, data, tag="620.73.04"):
        decoded = self.mod.decode(tag, name, data)
        return [label for label, ok, _ in self.mod.check_decoded(tag, name, decoded) if not ok]

    def test_parse_hex_formats(self):
        want = bytes(range(16))
        plain = want.hex()
        spaced = " ".join(f"{b:02x}" for b in want)
        cstyle = ", ".join(f"0x{b:02x}" for b in want)
        dump = "0000: " + spaced + "  |................|"
        split = "0x00: " + spaced[:23] + "\n0x08: " + spaced[24:] + "  # comment"
        for text in (plain, spaced, cstyle, dump, split, "\n" + plain + "\n\n"):
            with self.subTest(text=text):
                self.assertEqual(self.mod.parse_hex(text), want)
        with self.assertRaises(ValueError):
            self.mod.parse_hex("zz")

    def test_wpr_meta_baseline_passes(self):
        self.assertEqual(self.failures("GspFwWprMeta", self.wpr()), [])

    def test_wpr_meta_anomalies_are_reported(self):
        self.assertIn("host-untouched fields are zero", self.failures("GspFwWprMeta", self.wpr(bootCount=1)))
        self.assertIn("magic", self.failures("GspFwWprMeta", self.wpr(magic=0)))
        self.assertIn(
            "sysmemAddrOfRadix3Elf 4 KiB aligned",
            self.failures("GspFwWprMeta", self.wpr(sysmemAddrOfRadix3Elf=0x1_0000_0800)),
        )

    def test_fmc_params_round_trip(self):
        good = self.build("620.73.04", "GSP_FMC_BOOT_PARAMS", {
            "magic": self.mod.FMC_BOOT_PARAMS_MAGIC,
            "size": 256,
            "bootGspRmParams.target": 1,
            "bootGspRmParams.gspRmDescSize": 256,
            "bootGspRmParams.bIsGspRmBoot": 1,
            "gspRmParams.target": 1,
        })
        self.assertEqual(self.failures("GSP_FMC_BOOT_PARAMS", good), [])
        self.assertEqual(good[:4], b"FMC ")
        # Reading the new layout with an old tag must not look healthy.
        self.assertTrue(self.failures("GSP_FMC_BOOT_PARAMS", good, tag="610.57.04"))

    def test_cot_key_sizes_match_the_version(self):
        def payload(version, key, sig):
            data = bytearray(860)
            data[0:2] = version.to_bytes(2, "little")
            data[2:4] = (860).to_bytes(2, "little")
            data[0x54: 0x54 + key] = b"\x5a" * key
            data[0x1D4: 0x1D4 + sig] = b"\xa5" * sig
            return bytes(data)

        self.assertEqual(self.failures("NVDM_PAYLOAD_COT", payload(2, 97, 96)), [])
        self.assertEqual(
            self.failures("NVDM_PAYLOAD_COT", payload(2, 98, 96)), ["publicKey is zero past 97 bytes"]
        )
        self.assertEqual(
            self.failures("NVDM_PAYLOAD_COT", payload(2, 97, 384)), ["signature is zero past 96 bytes"]
        )
        self.assertEqual(
            self.failures("NVDM_PAYLOAD_COT", payload(1, 384, 384)), ["version (GB20x uses 2)"]
        )

    def test_short_dump_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.decode("620.73.04", "GspFwWprMeta", b"\0" * 100)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.hex"
            for data, code in ((self.wpr(), 0), (self.wpr(verified=1), 3), (b"\0" * 8, 2)):
                path.write_text(data.hex(), encoding="utf-8")
                sink = io.StringIO()
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    got = self.mod.main(["--struct", "GspFwWprMeta", "--decode", str(path)])
                self.assertEqual(got, code, sink.getvalue())


class LogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def dump(self, struct, data, tag="620.73.04", size=None):
        lines = [f"BWLAB: begin {struct} tag={tag} size={len(data) if size is None else size}"]
        for off in range(0, len(data), 16):
            lines.append(f"BWLAB: {off:04x}: " + data[off: off + 16].hex(" "))
        lines.append(f"BWLAB: end {struct}")
        return ["[   12.345678] NVRM: GPU0 " + x for x in lines]

    def wpr(self):
        data = bytearray(256)
        data[0:8] = self.mod.WPR_META_MAGIC.to_bytes(8, "little")
        data[8] = 1
        data[0x18:0x20] = (0x4000000).to_bytes(8, "little")
        data[0x28:0x30] = (0x3000).to_bytes(8, "little")
        data[0xA0:0xA8] = (0x100000).to_bytes(8, "little")
        return bytes(data)

    def run_cli(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dmesg.txt"
            path.write_text(text, encoding="utf-8")
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                return self.mod.main(["--from-log", str(path)]), sink.getvalue()

    def test_healthy_log(self):
        lines = ["noise"] + self.dump("GspFwWprMeta", self.wpr()) + ["more noise"]
        code, out = self.run_cli("\n".join(lines))
        self.assertEqual(code, 0, out)
        self.assertIn("#1 GspFwWprMeta tag=620.73.04", out)

    def test_identical_rows_are_kept(self):
        events = self.mod.parse_log("\n".join(self.dump("GspFwWprMeta", bytes(256))))
        self.assertEqual(events[0]["data"], bytes(256))

    def test_anomaly_sets_exit_code(self):
        bad = bytearray(self.wpr())
        bad[0xC8] = 1  # bootCount, which the host never writes
        code, out = self.run_cli("\n".join(self.dump("GspFwWprMeta", bytes(bad))))
        self.assertEqual(code, 3)
        self.assertIn("[DIFF] host-untouched fields are zero: bootCount", out)

    def test_damaged_dumps_are_never_decoded(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        cases = {
            "missing row": good[:5] + good[6:],
            "no end": good[:-1],
            "cut short": good[:9] + good[-1:],
            "restarted": good[:4] + good,
        }
        for label, lines in cases.items():
            with self.subTest(case=label):
                events = self.mod.parse_log("\n".join(lines))
                self.assertIsNone(events[0]["data"])
                self.assertTrue(events[0]["problem"])
                code, out = self.run_cli("\n".join(lines))
                self.assertEqual(code, 3)
                self.assertIn("[SKIP]", out)
        # The complete dump after a restart is still usable.
        self.assertEqual(self.mod.parse_log("\n".join(cases["restarted"]))[-1]["data"], self.wpr())

    def test_size_that_disagrees_with_the_layout_is_rejected(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        for label, data in {
            "oversized": self.wpr() + bytes(16),
            "undersized": self.wpr()[:16],
            "empty": b"",
        }.items():
            with self.subTest(case=label):
                code, out = self.run_cli("\n".join(self.dump("GspFwWprMeta", data)))
                self.assertEqual(code, 3, out)
                self.assertIn("defines 256 bytes", out)
                # A later intact block is still decoded, and the run still fails.
                code, out = self.run_cli("\n".join(self.dump("GspFwWprMeta", data) + good))
                self.assertEqual(code, 3)
                self.assertIn("[ok] host-untouched fields are zero", out)

    def test_headless_remains_make_the_log_incomplete(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        for label, remains in {
            "rows and end": good[3:],
            "end only": good[-1:],
            "rows only": good[3:6],
            "cut begin marker": ["BWLAB: begin GspFwW"] + good[1:],
        }.items():
            with self.subTest(case=label):
                code, out = self.run_cli("\n".join(remains + good))
                self.assertEqual(code, 3, out)
                self.assertIn("incomplete trace", out)
                self.assertIn("[ok] host-untouched fields are zero", out)
                self.assertEqual(out.count("incomplete trace"), 1)

    def test_every_headless_block_is_counted(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        events = self.mod.parse_log("\n".join(good[2:] + good[4:] + good))
        self.assertEqual([e["kind"] for e in events], ["incomplete", "incomplete", "dump"])

    def test_one_fault_yields_one_event(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        events = self.mod.parse_log("\n".join(good[:5] + good[6:]))
        self.assertEqual([e["kind"] for e in events], ["dump"])
        self.assertIn("out of order", events[0]["problem"])

    def test_malformed_lines_are_not_skipped_silently(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        broken = list(good)
        broken[4] = broken[4][:-6]  # row cut short
        events = self.mod.parse_log("\n".join(broken))
        self.assertIn("malformed", events[0]["problem"])
        for line in ("BWLAB: cot version=2 si", "BWLAB: bootMode="):
            with self.subTest(line=line):
                code, out = self.run_cli("\n".join(good + [line]))
                self.assertEqual(code, 3)
                self.assertIn("unrecognised trace line", out)

    def test_boot_mode_without_its_dump_is_incomplete(self):
        good = self.dump("GspFwWprMeta", self.wpr())
        code, out = self.run_cli("\n".join(good + ["BWLAB: bootMode=0"]))
        self.assertEqual(code, 3)
        self.assertIn("bootMode=0 without a GSP_FMC_BOOT_PARAMS dump", out)

    def test_unknown_tag_or_struct_is_skipped(self):
        for lines in (
            self.dump("GspFwWprMeta", self.wpr(), tag="999.1"),
            self.dump("SomethingElse", bytes(16)),
        ):
            code, out = self.run_cli("\n".join(lines))
            self.assertEqual(code, 3)
            self.assertIn("no recorded layout", out)

    def test_boot_mode_belongs_to_the_next_fmc_dump_only(self):
        lines = ["BWLAB: bootMode=1"] + self.dump("GSP_FMC_BOOT_PARAMS", bytes(256))
        lines += self.dump("GSP_FMC_BOOT_PARAMS", bytes(256))
        events = self.mod.parse_log("\n".join(lines))
        self.assertEqual([e["boot_mode"] for e in events], [1, None])
        self.assertIn("SR_RESUME", self.run_cli("\n".join(lines))[1])

    def test_cot_size_is_checked(self):
        row = "BWLAB: cot version=2 size={} frtsSysmemSize=0x0 frtsVidmemSize=0x100000"
        self.assertEqual(self.run_cli(row.format(860))[0], 0)
        self.assertEqual(self.run_cli(row.format(900))[0], 3)

    def test_cot_version_is_checked(self):
        row = "BWLAB: cot version={} size=860 frtsSysmemSize=0x0 frtsVidmemSize=0x100000"
        self.assertEqual(self.run_cli(row.format(2))[0], 0)
        code, out = self.run_cli(row.format(1))
        self.assertEqual(code, 3)
        self.assertIn("[DIFF] version (GB20x uses 2)", out)

    def test_no_trace_lines(self):
        code, out = self.run_cli("NVRM: loading NVIDIA UNIX Open Kernel Module\n")
        self.assertEqual(code, 2)
        self.assertIn("no BWLAB lines", out)


if __name__ == "__main__":
    unittest.main()
