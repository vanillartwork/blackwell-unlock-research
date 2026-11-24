# SPDX-License-Identifier: GPL-2.0-only
"""Patches must stay log-only, pinned, and readable by the decoder."""

import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCHES = sorted((ROOT / "patches").glob("*.patch"))


def load_layout():
    spec = importlib.util.spec_from_file_location("abi_layout", ROOT / "scripts" / "abi-layout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def split(text):
    """Return (header, hunks); a hunk is (old_len, new_len, lines)."""
    head, _, body = text.partition("diff --git ")
    hunks = []
    for part in re.split(r"(?m)^@@ ", "diff --git " + body)[1:]:
        m = re.match(r"-\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@[^\n]*\n", part)
        lines = []
        for line in part[m.end():].split("\n"):
            if line[:1] not in (" ", "+", "-"):
                break
            lines.append(line)
        hunks.append((int(m.group(1) or 1), int(m.group(2) or 1), lines))
    return head, hunks


def added(text):
    return [x[1:] for _, _, lines in split(text)[1] for x in lines if x.startswith("+")]


class PatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = load_layout()
        cls.texts = {p.name: p.read_bytes().decode("utf-8") for p in PATCHES}

    def test_there_is_a_patch(self):
        self.assertTrue(PATCHES)

    def test_header_pins_a_known_tag_and_commit(self):
        for name, text in self.texts.items():
            with self.subTest(patch=name):
                head, _ = split(text)
                fields = dict(re.findall(r"(?m)^(Status|Upstream|Tag|Commit|Scope)\s*:\s*(.+)$", head))
                self.assertEqual(set(fields), {"Status", "Upstream", "Tag", "Commit", "Scope"})
                self.assertTrue(name.startswith(fields["Tag"] + "-"))
                self.assertEqual(fields["Commit"], self.layout.TAGS[fields["Tag"]]["commit"])

    def test_patch_only_adds_lines(self):
        for name, text in self.texts.items():
            with self.subTest(patch=name):
                self.assertNotIn("\r", text)
                for old, new, lines in split(text)[1]:
                    removed = [x for x in lines if x.startswith("-")]
                    self.assertEqual(removed, [])
                    context = sum(x.startswith(" ") for x in lines)
                    plus = sum(x.startswith("+") for x in lines)
                    self.assertEqual((old, new), (context, context + plus))

    def test_added_code_cannot_change_hardware_or_boot_inputs(self):
        forbidden = re.compile(
            r"REG_WR|_WR32|RegWrite|osWriteReg|MEM_WR|BAR0_|NVreg_|module_param|DRF_DEF|"
            r"kflcn|gpuReset|return\b|goto\b"
        )
        for name, text in self.texts.items():
            for line in added(text):
                with self.subTest(patch=name, line=line):
                    code = line.split("//", 1)[0]
                    self.assertIsNone(forbidden.search(code))
                    # Assignments may only touch the private copy or the helper's locals.
                    m = re.match(r"\s*([A-Za-z_][\w.>\-\[\]]*)\s*(?:[|&^+\-]?=)(?!=)", code)
                    if m:
                        self.assertRegex(m.group(1), r"^copy\.")
                    self.assertNotRegex(code, r"portMemCopy\((?!&copy, sizeof\(copy\), )")

    def test_every_address_field_is_masked_in_the_copy(self):
        for name, text in self.texts.items():
            code = "\n".join(added(text))
            if "_kgspBwlabDump(" not in code:
                continue
            for field in (
                "sysmemAddrOfRadix3Elf", "sysmemAddrOfBootloader", "sysmemAddrOfSignature",
                "sysmemAddrOfCrashReportQueue", "bootGspRmParams.gspRmDescOffset",
                "gspRmParams.bootArgsOffset", "gspSpdmParams.payloadBufferOffset",
                "gspRmMemParams.flushSysmemAddrValLo",
            ):
                with self.subTest(patch=name, field=field):
                    self.assertRegex(
                        code, re.escape(f"copy.{field}") + r"\s*= (?:\(NvU32\))?BWLAB_PAGE_OFFSET\("
                    )
            self.assertRegex(code, r"copy\.gspRmMemParams\.flushSysmemAddrValHi\s*= 0;")
            for secret in ("hash384", "publicKey", "signature["):
                self.assertNotIn(secret, code)

    def test_decoder_reads_the_formats_the_patch_prints(self):
        for name, text in self.texts.items():
            formats = [
                f.encode().decode("unicode_escape")
                for f in re.findall(r'"(BWLAB: [^"]*)"', "\n".join(added(text)))
            ]
            begin = next(f for f in formats if "begin" in f)
            row = next(f for f in formats if "%04x: " in f)
            end = next(f for f in formats if "end" in f)
            mode = next(f for f in formats if "bootMode" in f)
            cot = next(f for f in formats if "cot " in f)

            def c_format(fmt, *args):
                return "[    1.000000] NVRM: GPU0 " + fmt.replace("%u", "%d") % args

            log = c_format(mode, 1) + c_format(begin, "GSP_FMC_BOOT_PARAMS", "620.73.04", 32)
            log += c_format(row, 0, *range(16)) + c_format(row, 16, *range(16, 32))
            log += c_format(end, "GSP_FMC_BOOT_PARAMS") + c_format(cot, 2, 860, 0, 0x100000)
            events = self.layout.parse_log(log)
            with self.subTest(patch=name):
                self.assertEqual([e["kind"] for e in events], ["dump", "cot"])
                self.assertEqual(events[0]["data"], bytes(range(32)))
                self.assertEqual(events[0]["boot_mode"], 1)
                self.assertEqual(events[1]["frtsVidmemSize"], 0x100000)


if __name__ == "__main__":
    unittest.main()
