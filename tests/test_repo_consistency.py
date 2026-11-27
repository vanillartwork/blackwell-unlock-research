# SPDX-License-Identifier: GPL-2.0-only
"""Keep docs, archived evidence and the publication rules in step with each other."""

import ast
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from urllib.parse import unquote
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GB205 = ROOT / "reports" / "gb205-2f18"

FORBIDDEN_SUFFIXES = {
    ".rom", ".bin", ".fw", ".elf", ".ucode", ".dmp", ".raw", ".gz", ".tgz", ".zip",
}
# The SecureBoot EFI variable GUID is a published constant, not a machine identifier.
PUBLIC_GUIDS = {"8be4df61-93ca-11d2-aa0d-00e098032b8c"}


def load(rel):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def repo_files():
    """Files that are tracked or would be added; falls back to a walk without git."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8")
        files = [ROOT / name for name in out.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    return sorted(p for p in files if p.exists() and "__pycache__" not in p.parts)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def table_after(text, heading):
    """Return (header cells, body rows) of the first Markdown table after `heading`."""
    start = text.index(heading)
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.lstrip().startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif rows:
            break
    return rows[0], rows[2:]


def plain(cell):
    return cell.replace("*", "").replace("`", "").replace("<br>", " ").strip()


def first_number(cell):
    return float(re.search(r"-?\d+(?:\.\d+)?", plain(cell)).group(0))


def slug(heading):
    """Approximate GitHub's heading anchors: keep word characters, spaces and hyphens."""
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


class PublicationRulesTests(unittest.TestCase):
    def test_no_firmware_like_or_oversized_files(self):
        for p in repo_files():
            with self.subTest(path=str(p.relative_to(ROOT))):
                self.assertNotIn(p.suffix.lower(), FORBIDDEN_SUFFIXES)
                self.assertLess(p.stat().st_size, 1 << 20)
                self.assertNotIn(b"\0", p.read_bytes(), "binary content")

    def test_no_machine_identifiers(self):
        patterns = {
            "uuid": re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.I),
            "mac": re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", re.I),
            "windows home": re.compile(r"[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+"),
            "unix home": re.compile(r"/home/[a-z][a-z0-9._-]+"),
            "serial": re.compile(r"serial(?: number)?\s*[:=]\s*[0-9]{6,}", re.I),
        }
        for p in repo_files():
            rel = p.relative_to(ROOT)
            # Sanitizer fixtures have to contain sample identifiers.
            if rel.parts[0] == "tests" or rel.name == "LICENSE":
                continue
            text = p.read_text(encoding="utf-8")
            for label, pat in patterns.items():
                for hit in pat.findall(text):
                    with self.subTest(path=str(rel), kind=label):
                        self.assertIn(hit.lower(), PUBLIC_GUIDS)

    def test_sources_carry_spdx_and_stay_portable(self):
        for p in repo_files():
            rel = p.relative_to(ROOT)
            if p.suffix not in (".py", ".sh", ".yml"):
                continue
            with self.subTest(path=str(rel)):
                text = p.read_text(encoding="utf-8")
                self.assertIn("SPDX-License-Identifier: GPL-2.0-only", "\n".join(text.splitlines()[:3]))
                self.assertNotIn("\r", p.read_bytes().decode("utf-8"), "CRLF line ending")
                if p.suffix == ".py":
                    ast.parse(text, feature_version=(3, 9))
                    if rel.parts[0] != "tests":
                        self.assertTrue(text.isascii(), "non-ASCII text breaks Windows consoles")


class MarkdownLinkTests(unittest.TestCase):
    def test_documents_have_language_switches(self):
        pages = {p for p in repo_files() if p.suffix == ".md"}
        for page in sorted(pages):
            english = page.with_name(page.stem.removesuffix(".cn") + ".md")
            chinese = english.with_suffix(".cn.md")
            with self.subTest(page=page.relative_to(ROOT)):
                self.assertIn(english, pages)
                self.assertIn(chinese, pages)
                switch = f"[English]({english.name}) | [中文]({chinese.name})"
                self.assertEqual(page.read_text(encoding="utf-8").splitlines()[0], switch)

    def test_relative_links_and_anchors_resolve(self):
        pages = [p for p in repo_files() if p.suffix == ".md"]
        anchors = {}
        for p in pages:
            body = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
            anchors[p] = {slug(m) for m in re.findall(r"^#{1,6}\s+(.+?)\s*$", body, flags=re.M)}
        for p in pages:
            body = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
            # The language switch is the only intentional cross-language link.
            _, _, content = body.partition("\n")
            for target in re.findall(r"\]\(([^)\s]+)\)", content):
                if re.match(r"[a-z]+:", target):
                    continue
                path, _, frag = target.partition("#")
                dest = (p.parent / unquote(path)).resolve() if path else p
                with self.subTest(page=str(p.relative_to(ROOT)), link=target):
                    self.assertTrue(dest.exists(), "missing target")
                    if dest.suffix == ".md":
                        self.assertEqual(p.name.endswith(".cn.md"), dest.name.endswith(".cn.md"))
                    if frag and dest.suffix == ".md":
                        self.assertIn(unquote(frag), anchors[dest])

    def test_readme_tree_lists_every_tool(self):
        english, chinese = read("README.md"), read("README.cn.md")
        for p in repo_files():
            rel = p.relative_to(ROOT)
            if rel.parts[0] in ("scripts", "software", "docs", "patches"):
                with self.subTest(path=str(rel)):
                    if rel.suffix == ".md":
                        self.assertIn(rel.name, chinese if rel.name.endswith(".cn.md") else english)
                    else:
                        self.assertIn(rel.name, english)
                        self.assertIn(rel.name, chinese)


    def test_paths_in_command_blocks_exist(self):
        for p in repo_files():
            if p.suffix != ".md":
                continue
            for block in re.findall(r"```bash\n(.*?)```", p.read_text(encoding="utf-8"), flags=re.S):
                for path in re.findall(
                    r"(?<![\w./<>-])(?:scripts|software|patches|reports)/[\w./<>-]+", block
                ) + re.findall(r"blackwell-abi-lab/((?:scripts|patches)/[\w./<>-]+)", block):
                    if "<" in path:
                        continue
                    with self.subTest(page=str(p.relative_to(ROOT)), path=path):
                        self.assertTrue((ROOT / path).exists())

    def test_route1_commands_match_the_pinned_patch(self):
        readme = read("README.md")
        tags = load("scripts/abi-layout.py").TAGS
        for patch in (ROOT / "patches").glob("*.patch"):
            tag = patch.name.split("-", 1)[0]
            with self.subTest(patch=patch.name):
                self.assertIn(f"--branch {tag} ", readme)
                self.assertIn(tags[tag]["commit"], readme)
                self.assertIn(f"patches/{patch.name}", readme)

    def test_all_svgs_parse_as_valid_xml(self):
        svgs = [p for p in repo_files() if p.suffix.lower() == ".svg"]
        self.assertTrue(svgs)
        for p in svgs:
            with self.subTest(path=str(p.relative_to(ROOT))):
                root = ET.fromstring(p.read_text(encoding="utf-8"))
                self.assertTrue(root.tag.endswith("svg"))


@unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+")
class DependencyTests(unittest.TestCase):
    PROVIDES = {"torch": "torch", "pynvml": "nvidia-ml-py"}

    def test_third_party_imports_are_declared(self):
        declared = {
            re.split(r"[<>=!~\s]", x)[0]
            for x in read("requirements.txt").splitlines() if x and not x.startswith("#")
        }
        for p in repo_files():
            rel = p.relative_to(ROOT)
            if p.suffix != ".py":
                continue
            used = set()
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    used |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    used.add(node.module.split(".")[0])
            for name in used - sys.stdlib_module_names:
                with self.subTest(path=str(rel), module=name):
                    self.assertIn(self.PROVIDES.get(name), declared)
                    self.assertIn(rel.parts[0], ("software", "tests"))


class ArchivedJsonTests(unittest.TestCase):
    def test_clamp_eval_records_are_self_consistent(self):
        for path in sorted(GB205.glob("clamp-eval-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            sm = data["info"]["sm_count"]
            self.assertEqual(data["schema"], "nvuu-clamp-eval/1")
            for r in data["results"]:
                with self.subTest(file=path.name, case=r["case"]):
                    flops = 2.0 * r["n"] ** 3 / (r["median_ms"] * 1e9)
                    self.assertAlmostEqual(r["tflops"], flops, places=6)
                    if r["flop_clk_sm"] is not None:
                        fsm = r["tflops"] * 1e12 / (r["sm_clock_mhz"] * 1e6 * sm)
                        self.assertAlmostEqual(r["flop_clk_sm"], fsm, places=6)
                        self.assertAlmostEqual(
                            r["ratio_of_full"], fsm / r["full_rate_flop_clk_sm"], places=9
                        )

    def test_chunked_record_is_self_consistent(self):
        data = json.loads((GB205 / "chunked-fp16-621.01.json").read_text(encoding="utf-8"))
        mod = load("software/chunked_fp16_matmul.py")
        fresh = mod.annotate_rows([
            {k: r[k] for k in ("tflops", "sample_relative_l2_error", "num_chunks") if k in r}
            for r in data["results"]
        ])
        for r, f in zip(data["results"], fresh):
            with self.subTest(label=r["label"]):
                for key in ("speedup_vs_fp32acc", "error_ratio_vs_fp32acc", "error_reduction_vs_full_fp16acc"):
                    self.assertAlmostEqual(r[key], f[key], places=9)

    def test_route2_wt2_bootstrap_archive_matches_report(self):
        data = json.loads((GB205 / "route2-wt2-288k-fp64.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "blackwell-abi-lab/route2-wt2-bootstrap/1")
        mod = load("software/verify_wt2_bootstrap.py")
        summary = mod.compute_summary(data)
        self.assertEqual(summary["n_chunks"], 282)
        self.assertEqual(summary["total_tokens"], 288768)
        doc = read("docs/route2-mma-bypass.md")
        self.assertIn(f"PPL_hi = {summary['ref_ppl_high_res']:.5f}", doc)
        self.assertIn(f"PPL_dep = {summary['ref_ppl_deploy']:.5f}", doc)
        comp_hi = summary["compression_pure_b1_all_over_c1b1_kp_all"]["high_res"]
        comp_dep = summary["compression_pure_b1_all_over_c1b1_kp_all"]["deploy"]
        self.assertIn(f"{comp_hi[0]:.2f}×", doc)
        self.assertIn(f"{comp_dep[0]:.2f}×", doc)
        _, body = table_after(doc, "### 3.2 Full WikiText-2-raw")
        self.assertEqual(len(body), len(summary["rows"]))
        for row, r in zip(body, summary["rows"]):
            with self.subTest(key=r["key"]):
                dep_nums = [float(x) for x in re.findall(r"[+-]?\d+\.\d+e[+-]\d+", plain(row[1]))]
                hi_nums = [float(x) for x in re.findall(r"[+-]?\d+\.\d+e[+-]\d+", plain(row[2]))]
                self.assertAlmostEqual(dep_nums[0], r["kl_deploy"], delta=max(r["kl_deploy"] * 0.01, 1e-7))
                self.assertAlmostEqual(hi_nums[0], r["kl_high_res"], delta=max(r["kl_high_res"] * 0.01, 1e-7))
                if r["key"] != data["protocol"]["baseline_key"]:
                    self.assertAlmostEqual(dep_nums[1], r["excess_deploy"][0], delta=max(abs(r["excess_deploy"][0]) * 0.01, 5e-9))
                    self.assertAlmostEqual(hi_nums[1], r["excess_high_res"][0], delta=max(abs(r["excess_high_res"][0]) * 0.01, 5e-9))
                self.assertAlmostEqual(first_number(row[3]), r["top1_high_res"], delta=0.011)

    def test_route2_dual_qmma_archive_matches_report(self):
        data = json.loads((GB205 / "route2-dual-qmma-621.01.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "blackwell-abi-lab/route2-dual-qmma/1")
        mod = load("software/verify_dual_fp8.py")
        summary = mod.verify_and_summarize(data)
        self.assertEqual(summary["total_blocks_scanned"], 517031936)
        self.assertEqual(summary["total_int8_exact_pass"], 0)
        doc_en = read("docs/route2-mma-bypass.md")
        doc_zh = read("docs/route2-mma-bypass.cn.md")
        for doc in (doc_en, doc_zh):
            self.assertIn("517,031,936", doc)
            # Prefill frontier (W4A4, Marlin-HMMA, Single-FP8, Dual-FP8)
            fr_up = next(sh for sh in data["prefill_frontier"] if sh["M"] == 4096 and sh["N"] == 14336)
            by_fn = {v["name"]: v for v in fr_up["variants"]}
            for name in (
                "W4A4 (OMMA.SF.16864)",
                "Single-FP8 W4A8 (QMMA.SF)",
                "Single-FP8 W8A8 (QMMA.SF)",
                "Dual-FP8 W4A16 (pre-split)",
                "Dual-FP8 W8A16 (pre-split)",
                "Marlin-HMMA W4A16 (dequant)",
                "Marlin-HMMA W8A16 (dequant)",
            ):
                self.assertIn(f"{by_fn[name]['tflops']:.2f}", doc)
            # Cold-L2 decode sweep M=1..64
            for sw in data["decode_cold_l2_sweep"]["batch_sweeps"]:
                by_dl = {r["label"]: r for r in sw["results"]}
                self.assertIn(f"{by_dl['Dual-FP8 W4A16 (pre-split)']['us']:.1f}", doc)
                self.assertIn(f"{by_dl['Marlin-HMMA W4A16 (dequant)']['us']:.1f}", doc)
            # Fused MLP end-to-end
            for mlp in data["fused_mlp_e2e"]:
                by_p = {p["name"]: p for p in mlp["pipelines"]}
                self.assertIn(f"{by_p['Dual-FP8 W8A16 (Fused Epilogue)']['ms']:.2f}", doc)
                self.assertIn(f"{by_p['Dual-FP8 W4A16 (Fused Epilogue)']['ms']:.2f}", doc)
            # TMA-WS split deployment modes
            up = next(sh for sh in data["tma_ws_bench"]["shapes"] if sh["N"] == 14336 and sh["M"] == 4096)
            by_lbl = {r["label"]: r for r in up["results"]}
            for lbl in (
                "TMA-WS Dual-FP8 W8A16 (pre-split)",
                "TMA-WS Dual-FP8 W8A16 (2-kernel)",
                "TMA-WS Dual-FP8 W8A16 (online)",
                "TMA-WS Dual-FP8 W4A16 (pre-split)",
                "TMA-WS Dual-FP8 W4A16 (2-kernel)",
                "TMA-WS Dual-FP8 W4A16 (online)",
            ):
                self.assertIn(f"{by_lbl[lbl]['tflops']:.2f}", doc)
            # 32k LLM KL (W8, RTN W4, AWQ+MSE W4, K-smoothed FP8-KV; Deploy & High-Res)
            for mid, msum in summary["llm_kl_summary"].items():
                for w_tag in ("W8_MXFP8", "W4_MXFP4", "W4_MXFP4_AWQ_MSE"):
                    rat = msum["quant_modes"][w_tag]["single_over_dual_ratio"][0]
                    hr_rat = msum["quant_modes"][w_tag]["hires_single_over_dual_ratio"][0]
                    self.assertIn(f"{rat:.1f}×", doc)
                    self.assertIn(f"{hr_rat:.1f}×", doc)
                kv_rat = msum["fp8_kv_qkt_smoothed"]["single_over_dual_ratio"][0]
                kv_hr = msum["fp8_kv_qkt_smoothed"]["hires_single_over_dual_ratio"][0]
                self.assertIn(f"{kv_rat:.1f}×", doc)
                self.assertIn(f"{kv_hr:.1f}×", doc)


class ReportTableTests(unittest.TestCase):
    """Numbers quoted in Markdown must round to what the archived JSON says."""

    @classmethod
    def setUpClass(cls):
        def results(name):
            return json.loads((GB205 / name).read_text(encoding="utf-8"))["results"]

        cls.n8192 = {r["case"]: r for r in results("clamp-eval-621.01-n8192.json")}
        cls.n4096 = {r["case"]: r for r in results("clamp-eval-621.01-n4096.json")}
        cls.chunked = results("chunked-fp16-621.01.json")

    def close(self, cell, value, digits):
        self.assertAlmostEqual(first_number(cell), value, delta=0.5 * 10 ** -digits + 1e-9)

    def test_gb205_clamp_table(self):
        _, body = table_after(read("reports/gb205-2f18/README.md"), "## Steady-State Tensor Clamp Baseline")
        self.assertEqual({plain(r[0]) for r in body}, set(self.n8192))
        for row in body:
            case = plain(row[0])
            with self.subTest(case=case):
                big, small = self.n8192[case], self.n4096[case]
                self.close(row[1], big["tflops"], 2)
                if big["flop_clk_sm"] is not None:
                    self.close(row[2], big["flop_clk_sm"], 1)
                    self.close(row[3], big["ratio_of_full"], 3)
                    self.close(row[5], small["ratio_of_full"], 3)
                if big["sample_relative_l2_error"] is not None and "n/a" not in row[4]:
                    self.close(row[4], big["sample_relative_l2_error"], 6)

    def test_gb205_chunk_table(self):
        _, body = table_after(read("reports/gb205-2f18/README.md"), "## Route 2 Software Trade-Off")
        self.assertEqual(len(body), len(self.chunked))
        for row, r in zip(body, self.chunked):
            with self.subTest(label=r["label"]):
                self.close(row[2], r["tflops"], 2)
                self.close(row[3], r["speedup_vs_fp32acc"], 2)
                self.close(row[4], r["sm_clock_mhz"], 0)
                self.close(row[5], r["flop_clk_sm"], 1)
                self.close(row[6], r["sample_relative_l2_error"], 6)
                self.close(row[7], r["error_reduction_vs_full_fp16acc"], 2)

    def test_software_readme_chunk_table(self):
        _, body = table_after(read("software/README.md"), "### GB205")
        self.assertEqual(len(body), len(self.chunked))
        for row, r in zip(body, self.chunked):
            with self.subTest(label=r["label"]):
                self.close(row[2], r["tflops"], 2)
                self.close(row[3], r["speedup_vs_fp32acc"], 2)
                self.close(row[4], r["sm_clock_mhz"], 0)
                self.close(row[5], r["power_w"], 1)
                self.close(row[6], r["flop_clk_sm"], 1)
                self.close(row[7], r["sample_relative_l2_error"], 6)
                self.close(row[8], r["error_ratio_vs_fp32acc"], 2)

    def test_matrix_clamp_table(self):
        header, body = table_after(read("docs/test-matrix.md"), "## 3. ")
        cases = {i: plain(h) for i, h in enumerate(header) if "acc" in h}
        for row, data in zip(body[:2], (self.n8192, self.n4096)):
            for i, case in cases.items():
                with self.subTest(row=plain(row[2]), case=case):
                    self.close(row[i], data[case]["ratio_of_full"], 2)
                    tflops = float(re.search(r"([\d.]+) TF/s", row[i]).group(1))
                    self.assertAlmostEqual(tflops, data[case]["tflops"], delta=0.005001)


class RegisterDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = load("scripts/parse-ssdiag.py")

        def capture(name):
            return cls.parser.parse_capture((GB205 / name).read_text(encoding="utf-8"))

        cls.new = capture("bar0-prom-metadata-621.01.txt")
        cls.old = capture("bar0-core-616.64.txt")

    def test_matrix_gb205_column_matches_capture(self):
        header, body = table_after(read("docs/test-matrix.md"), "## 1. ")
        col = next(i for i, h in enumerate(header) if "GB205" in h)
        rows = {plain(r[0]): plain(r[col]) for r in body}
        groups = {
            "PMC_BOOT_0": ["PMC_BOOT_0"],
            "FEAT_OVR_PLM": ["FEAT_OVR_PLM"],
            "FEAT_READOUT_1": ["FEAT_READOUT_1"],
            "FEAT_READOUT_0": ["FEAT_READOUT_0"],
            "FEAT_OVR_ECC / ECC_1": ["FEAT_OVR_ECC", "FEAT_OVR_ECC_1"],
            "FUSE_SS_FMLA32": ["FUSE_SS_FMLA32"],
            "FUSE_SS_IMLA1 / 2 / 4": ["FUSE_SS_IMLA1", "FUSE_SS_IMLA2", "FUSE_SS_IMLA4"],
            "FUSE_FEAT_OVR_DIS": ["FUSE_FEAT_OVR_DIS"],
            "FUSE_EN_SW_OVERRIDE": ["FUSE_EN_SW_OVERRIDE"],
            "FUSE_SS_PLM": ["FUSE_SS_PLM"],
            "SECURE_FEAT_OVR_QUADRO_WR": ["SECURE_FEAT_OVR_QUADRO_WR"],
            "SM_ISSUE_RATE_MODIFIER": ["SM_ISSUE_RATE_MODIFIER"],
            "SEC2_PLM": ["SEC2_PLM"],
            "GSP_CPUCTL": ["GSP_CPUCTL"],
            "WPR2_LO / HI ②": ["WPR2_LO", "WPR2_HI"],
        }
        for label, names in groups.items():
            with self.subTest(row=label):
                quoted = [int(x, 0) for x in re.findall(r"0x[0-9a-f]+|\d+", rows[label])]
                self.assertEqual(quoted, [self.parser.lookup(self.new, n) for n in names])
        for name in ("FUSE_SS_DP", "FUSE_SS_FFMA", "FUSE_SS_FMLA16", "FUSE_SS_IMLA0", "FUSE_SS_IMLA3"):
            self.assertEqual(self.parser.lookup(self.new, name), 0)

    def test_wpr2_words_quoted_in_docs_come_from_the_captures(self):
        known = {
            self.parser.lookup(c, n) for c in (self.old, self.new) for n in ("WPR2_LO", "WPR2_HI")
        }
        for rel in ("docs/abi-invariants.md", "docs/abi-fields.md", "reports/gb205-2f18/README.md"):
            text = read(rel)
            quoted = {int(x, 16) for x in re.findall(r"0x02f[0-9a-f]{5}\b", text)}
            with self.subTest(page=rel):
                self.assertTrue(quoted)
                self.assertLessEqual(quoted, known)

    def test_readout1_decode_quoted_in_docs(self):
        value = self.parser.lookup(self.new, "FEAT_READOUT_1")
        fields = " ".join(str(x) for x in self.parser.decode_readout1(value))
        self.assertIn(fields, read("docs/architecture-map.md"))


class RomDirectoryTests(unittest.TestCase):
    """The archived directory metadata must support what the V6 document says."""

    @classmethod
    def setUpClass(cls):
        cls.tool = load("scripts/rom-directory.py")
        cls.doc = read("docs/vbios-v6-descriptor.md")
        cls.archive = json.loads((GB205 / "rom-directory-98.05.31.00.44.json").read_text(encoding="utf-8"))
        cls.v6 = json.loads((GB205 / "v6-descriptors-98.05.31.00.44.json").read_text(encoding="utf-8"))
        cls.dirs = cls.tool.load_export(cls.archive)
        cls.by_id = {}
        for d in cls.dirs:
            for o in d["objects"]:
                cls.by_id.setdefault(o["id"], []).append(o)

    def test_same_specimen(self):
        for key in ("vbios", "pci", "rom_size", "rom_sha256"):
            self.assertEqual(self.archive["specimen"][key], self.v6["specimen"][key])

    def test_counts_quoted_in_the_document(self):
        self.assertEqual(len(self.dirs), 7)
        self.assertEqual(sum(len(d["objects"]) for d in self.dirs), 155)
        self.assertTrue(all(d["digest"] == "verified" for d in self.dirs))
        self.assertTrue(all(o["hash_ok"] for d in self.dirs for o in d["objects"]))
        self.assertIn("seven directories with 155 objects", self.doc)
        _, body = table_after(self.doc, "### 6.10 The GB20x object directory")
        quoted = [(int(r[0], 16), int(r[2], 16), int(r[3])) for r in body]
        self.assertEqual(quoted, [(d["offset"], d["device"], len(d["objects"])) for d in self.dirs])

    def test_manifest_ids_are_object_ids(self):
        rows = self.tool.match(self.dirs, self.tool.manifest_ranges(self.v6))
        got = {r["id"]: r["status"] for r in rows}
        self.assertEqual(len(got), 10)
        self.assertEqual({i for i, s in got.items() if s != "equal"}, {0x14})
        self.assertEqual(got[0x14], "prefix")
        _, body = table_after(self.doc, "#### 6.10.1 Manifest entry ids")
        for r, row in zip(rows, body):
            with self.subTest(entry=hex(r["id"])):
                self.assertEqual(int(row[0], 16), r["id"])
                self.assertEqual([int(x, 16) for x in re.findall(r"0x[0-9A-F]+", row[1])], [r["start"], r["size"]])
                self.assertEqual(
                    [int(x, 16) for x in re.findall(r"0x[0-9A-F]+", row[2])],
                    [r["object"]["offset"], r["object"]["size"]],
                )
                self.assertEqual(row[3].startswith("equal"), r["status"] == "equal")

    def test_descriptors_fill_their_objects(self):
        rows = {r["app"]: r for r in self.tool.match_descriptors(self.dirs, self.v6)}
        self.assertEqual({r["status"] for r in rows.values()}, {"equal"})
        _, body = table_after(self.doc, "#### 6.10.2 Descriptors")
        self.assertEqual([r[0] for r in body], list(rows))
        fields = {d["app"]: d["fields"] for d in self.v6["descriptors"]}
        for app, oid, product, size, predicted in body:
            with self.subTest(app=app):
                self.assertEqual(int(oid, 16), rows[app]["object"]["id"])
                self.assertEqual(int(size, 16), rows[app]["object"]["size"])
                self.assertEqual(int(predicted, 16), rows[app]["expected"])
                n, stride = (int(x) for x in re.findall(r"\d+", product))
                self.assertEqual(n, int(fields[app]["18"], 16) >> 16)
                self.assertEqual(stride, int(fields[app]["1C"], 16) & 0xFFFF)

    def test_other_closed_lengths(self):
        spec = self.v6["specimen"]
        table, = [o for o in self.by_id[0x2B] if o["offset"] == int(spec["ucode_table_offset"], 16)]
        self.assertEqual(table["size"], 6 + spec["ucode_slots"] * 6)
        bit, = [o for o in self.by_id[0x2D] if o["offset"] == int(spec["bit_offset"], 16)]
        self.assertEqual(bit["size"], 12 + 20 * 6)

    def test_intervals_between_descriptors_are_accounted_for(self):
        ends = {d["app"]: int(d["descriptor_offset"], 16) + self.tool.expected_descriptor_size(d)
                for d in self.v6["descriptors"]}
        start = {d["app"]: int(d["descriptor_offset"], 16) for d in self.v6["descriptors"]}
        _, body = table_after(self.doc, "Both are fully accounted for")
        for row in body:
            a, b = re.findall(r"0x[0-9a-f]{2}\b", row[0])[:2]
            parts = [int(re.search(r"0x[0-9A-F]+", c).group(0), 16) for c in row[1:6]]
            with self.subTest(interval=row[0]):
                self.assertEqual(sum(parts[:4]), parts[4])
                self.assertEqual(parts[4], start[b] - ends[a])

    def test_outer_constant_interval_is_not_an_object(self):
        lo, hi = 0x51BA7, 0x52064
        owners = [o for d in self.dirs for o in d["objects"] if o["offset"] < hi and lo < o["offset"] + o["size"]]
        self.assertEqual([(o["offset"], o["size"]) for o in owners], [(0x45350, 0x17943)])


class RomSurveyTests(unittest.TestCase):
    """The cross-die survey archive must support section 6.11 and the matrix."""

    @classmethod
    def setUpClass(cls):
        cls.dir = ROOT / "reports" / "rom-survey-20261123"
        cls.survey = json.loads((cls.dir / "rom-survey.json").read_text(encoding="utf-8"))
        cls.closure = load("scripts/v6-closure.py")
        cls.doc = read("docs/vbios-v6-descriptor.md")
        files = [*cls.dir.glob("v6-descriptors-*.json"), GB205 / "v6-descriptors-98.05.31.00.44.json"]
        cls.v6 = {}
        for p in files:
            data = json.loads(p.read_text(encoding="utf-8"))
            cls.v6[data["specimen"]["rom_sha256"]] = data
        cls.ordered = [cls.v6[r["rom_sha256"]] for r in cls.survey["roms"]]

    def test_every_rom_has_its_scalars_and_every_die_is_covered(self):
        self.assertEqual(len(self.survey["roms"]), 12)
        self.assertEqual(len(self.v6), 12)
        self.assertEqual(
            [r["identity"]["die"] for r in self.survey["roms"]],
            ["GB202"] * 3 + ["GB203"] * 3 + ["GB205"] * 3 + ["GB206"] * 2 + ["GB207"],
        )
        known = load("scripts/verify-baseline.py").KNOWN_BLACKWELL_DEVIDS
        for r in self.survey["roms"]:
            self.assertEqual(known[r["identity"]["pci_device"]][0], r["identity"]["die"])

    def test_all_hashes_digests_and_laws_hold(self):
        descriptors = entries = equal = prefix = 0
        for r in self.survey["roms"]:
            self.assertEqual(len(r["directories"]), 7)
            for d in r["directories"]:
                self.assertTrue(d["digest_ok"])
                self.assertEqual(d["digest"], "verified")
                self.assertEqual((d["hashes_ok"], d["hashes_failed"]), (len(d["objects"]), []))
            self.assertTrue(all(d["closes"] for d in r["descriptors"]))
            descriptors += len(r["descriptors"])
            for m in r["manifests"]:
                self.assertEqual(m["other"], 0)
                entries += m["entries"]
                equal += m["equal"]
                prefix += m["prefix"]
        self.assertEqual((descriptors, entries, equal, prefix), (72, 132, 120, 12))
        self.assertIn(f"{descriptors} of {descriptors} descriptors", self.doc)
        self.assertIn(
            f"{entries} of {entries} type 1 entries, {equal} equal and {prefix} prefixes", self.doc
        )

    def test_every_record_is_a_rom_of_its_own_with_its_own_scalars(self):
        roms = self.survey["roms"]
        for key in (lambda r: r["rom_sha256"], lambda r: r["identity"]["vbios"], lambda r: r["v6_file"]):
            self.assertEqual(len({key(r) for r in roms}), len(roms))
        for r in roms:
            with self.subTest(vbios=r["identity"]["vbios"]):
                here = [p for p in (self.dir / r["v6_file"], GB205 / r["v6_file"]) if p.exists()]
                self.assertEqual(len(here), 1)
                spec = json.loads(here[0].read_text(encoding="utf-8"))["specimen"]
                self.assertEqual(spec["rom_sha256"], r["rom_sha256"])

    def test_no_manifest_contradicts_another(self):
        tool = load("scripts/rom-directory.py")
        for data in self.ordered:
            rows = tool.manifest_ranges(data)
            with self.subTest(vbios=data["specimen"]["vbios"]):
                self.assertEqual(len(rows), 10)
                self.assertFalse(any(r["conflict"] for r in rows))

    def test_every_closure_check_holds_on_every_rom(self):
        counts = set()
        for data in self.ordered:
            checks = [c for c in self.closure.run_checks(data) if c[1] is not None]
            counts.add(len(checks))
            with self.subTest(vbios=data["specimen"]["vbios"]):
                self.assertEqual([c[0] for c in checks if not c[1]], [])
        self.assertEqual(len(counts), 1)
        self.assertIn(f"runs {counts.pop()} checks per ROM", self.doc)

    def test_specimen_table(self):
        _, body = table_after(self.doc, "#### 6.11.1 Specimens")
        for row, rom, data in zip(body, self.survey["roms"], self.ordered):
            spec = data["specimen"]
            with self.subTest(vbios=spec["vbios"]):
                self.assertEqual(row[0], rom["identity"]["die"])
                self.assertEqual(row[2:4], [spec["pci"], spec["vbios"]])
                self.assertEqual(int(row[4], 16), int(spec["adjust"], 16))
                self.assertEqual(int(row[5], 16), int(spec["bit_offset"], 16))
                self.assertEqual(int(row[6]), sum(len(d["objects"]) for d in rom["directories"]))
                self.assertTrue(spec["rom_sha256"].startswith(plain(row[7]).rstrip("\u2026")))

    def test_identity_table_and_tail_grouping(self):
        family = {o["id"]: o["groups"] for o in self.survey["family_objects"]}
        groups = {g["id"]: g for g in self.survey["descriptor_groups"]}
        _, body = table_after(self.doc, "#### 6.11.5 Which images are common")
        self.assertEqual(len(body), 7)
        for row in body:
            oid = row[1].lower()
            want = family[oid] if row[0] == "0x2B80" else groups[oid]["payload"]
            with self.subTest(object=oid):
                self.assertEqual(row[3:], want)
        for g in groups.values():
            self.assertEqual(g["payload"], g["tail"])
        distinct = sum(len(set(g["payload"])) for g in groups.values())
        self.assertIn(f"{distinct} distinct payloads, {distinct} distinct tails", self.doc)

    def test_common_card_objects(self):
        rows = [o["groups"] for o in self.survey["card_objects"]]
        everywhere = [g for g in rows if None not in g]
        same = [g for g in everywhere if len(set(g)) == 1]
        self.assertIn(f"{len(everywhere)} ids occur in all twelve ROMs and {len(same)} of them", self.doc)
        missing = {o["id"] for o in self.survey["card_objects"] if None in o["groups"]}
        self.assertEqual(missing, {"0x7a", "0x7b", "0x7c"})

    def test_desc08_is_not_a_t_space_base(self):
        above = []
        for data in self.ordered:
            m = next(m for m in data["manifests"] if m["app"] == "0x19")
            d = next(d for d in data["descriptors"] if d["app"] == "0x19")
            first = min(self.closure.t_range(e)[0] for e in m["entries"])
            above.append(int(d["fields"]["08"], 16) > first)
        self.assertEqual(
            above,
            [True, True, False, True, True, True, True, True, False, True, False, False],
        )
        self.assertIn("above the first T start on eight ROMs", self.doc)

    def test_outer_0c_takes_two_values(self):
        values = []
        for d in self.ordered:
            one = {m["outer_header"]["0C"] for m in d["manifests"]}
            self.assertEqual(len(one), 1)
            values.append(int(one.pop(), 16))
        self.assertEqual(
            values,
            [0x4BD, 0x4BD, 0x3FC, 0x4BD, 0x4BD, 0x4BD, 0x3FC, 0x3FC, 0x4BD, 0x3FC, 0x3FC, 0x3FC],
        )
        # Both lengths occur over one byte-identical EFI payload (6.11.3).
        efi = next(o["groups"] for o in self.survey["family_objects"] if o["id"] == "0x55")
        by_payload = {}
        for group, value in zip(efi, values):
            by_payload.setdefault(group, set()).add(value)
        self.assertEqual(by_payload["B"], {0x4BD, 0x3FC})

    def test_generation_prediction(self):
        index = {r["identity"]["vbios"]: n for n, r in enumerate(self.survey["roms"])}
        old_ti, new_ti = index["98.03.3B.80.2F"], index["98.03.58.40.B6"]
        s5080, s5090 = index["98.03.3B.C0.3E"], index["98.02.2E.80.C8"]
        groups = {g["id"]: g["payload"] for g in self.survey["descriptor_groups"]}
        groups["0x55"] = next(o["groups"] for o in self.survey["family_objects"] if o["id"] == "0x55")
        die_specific = {"0x36", "0x9e"}
        for oid, letters in groups.items():
            with self.subTest(object=oid):
                self.assertEqual(letters[old_ti], letters[s5080])
                self.assertNotEqual(letters[old_ti], letters[new_ti])
                self.assertEqual(letters[old_ti] == letters[s5090], oid not in die_specific)

    def test_pro_and_5090d_carry_no_structural_marker(self):
        ids = [{o["id"] for d in r["directories"] for o in d["objects"]} for r in self.survey["roms"]]
        for device in (0x2BB1, 0x2B87):
            at = next(n for n, r in enumerate(self.survey["roms"]) if r["identity"]["pci_device"] == device)
            with self.subTest(device=hex(device)):
                self.assertEqual(ids[at], ids[0])  # same id set as the RTX 5090
                for o in self.survey["card_objects"]:
                    others = [x for n, x in enumerate(o["groups"]) if n != at]
                    if None not in o["groups"] and len(set(others)) == 1:
                        self.assertEqual(o["groups"][at], others[0])
                fixed = ("24", "28", "2C", "30")
                for a, b in zip(self.ordered[at]["descriptors"], self.ordered[0]["descriptors"]):
                    self.assertEqual((a["app"], a["flags"], a["target"]), (b["app"], b["flags"], b["target"]))
                    self.assertEqual([a["fields"][f] for f in fixed], [b["fields"][f] for f in fixed])

    def test_row_area_of_app_23_goes_with_efi_group_a(self):
        efi = next(o["groups"] for o in self.survey["family_objects"] if o["id"] == "0x55")
        areas = []
        for data in self.ordered:
            d = next(d for d in data["descriptors"] if d["app"] == "0x23")
            areas.append(self.closure.row_area(d))
        self.assertEqual([a == 60 for a in areas], [g == "A" for g in efi])
        self.assertEqual(set(areas), {0, 60})

    def test_5090d_separates_app_24_from_the_efi_generation(self):
        family = {o["id"]: o["groups"] for o in self.survey["family_objects"]}
        self.assertEqual(family["0x55"][0], family["0x55"][1])
        self.assertNotEqual(family["0x9f"][0], family["0x9f"][1])
        mains = [family[o] for o in ("0x55", "0x86", "0x9f")]
        dies = [r["identity"]["die"] for r in self.survey["roms"]]
        for letters in mains:
            self.assertGreater(len(set(letters)), 1)
            by = {}
            for letter, die in zip(letters, dies):
                by.setdefault(letter, set()).add(die)
            self.assertTrue(any(len(x) > 1 for x in by.values()))
        self.assertEqual(len(set(family["0xce"])), 1)

    def at(self, vbios):
        return next(n for n, r in enumerate(self.survey["roms"]) if r["identity"]["vbios"] == vbios)

    def differ_only_on(self, at):
        ref = 0 if at else 1
        return [
            o["id"] for o in self.survey["card_objects"]
            if None not in o["groups"]
            and len({x for n, x in enumerate(o["groups"]) if n != at}) == 1
            and o["groups"][at] != o["groups"][ref]
        ]

    def test_objects_that_differ_only_on_the_laptop(self):
        lap = self.at("98.05.31.00.44")
        # Two desktop cards with the same die are in the sample, so the die is not the reason.
        same_die = [n for n, r in enumerate(self.survey["roms"]) if r["identity"]["die"] == "GB205"]
        self.assertEqual(len(same_die), 3)
        only = self.differ_only_on(lap)
        self.assertEqual(only, ["0x5f", "0x61", "0x62", "0x65", "0x7d", "0xe4"])
        self.assertIn(", ".join("0x" + x[2:].upper() for x in only), self.doc)

    def test_absent_ids_do_not_follow_the_die(self):
        groups = {o["id"]: o["groups"] for o in self.survey["card_objects"]}
        for oid in ("0x7a", "0x7b", "0x7c"):
            with self.subTest(object=oid):
                have = {
                    die: [g is not None for g, r in zip(groups[oid], self.survey["roms"]) if r["identity"]["die"] == die]
                    for die in ("GB202", "GB203", "GB205", "GB206", "GB207")
                }
                self.assertTrue(all(have["GB202"] + have["GB203"]))
                self.assertFalse(any(have["GB206"] + have["GB207"]))
                self.assertEqual(have["GB205"], [False, False, True])
                sizes = {
                    r["identity"]["vbios"]: o["size"]
                    for r in self.survey["roms"] for d in r["directories"] for o in d["objects"] if o["id"] == oid
                }
                laptop = sizes.pop("98.05.31.00.44")
                self.assertEqual(len(set(sizes.values())), 1)
                self.assertGreater(int(laptop, 16), int(next(iter(sizes.values())), 16))

    def test_board_vendor_alone_changes_no_v6_layer(self):
        a, b = self.at("98.05.28.00.64"), self.at("98.05.28.00.1D")
        self.assertEqual(self.ordered[a]["descriptors"], self.ordered[b]["descriptors"])
        self.assertEqual(self.ordered[a]["manifests"], self.ordered[b]["manifests"])
        for o in self.survey["family_objects"]:
            self.assertEqual(o["groups"][a], o["groups"][b])
        for g in self.survey["descriptor_groups"]:
            self.assertEqual((g["payload"][a], g["tail"][a]), (g["payload"][b], g["tail"][b]))
        both = [o for o in self.survey["card_objects"] if o["groups"][a] and o["groups"][b]]
        diff = [o["id"] for o in both if o["groups"][a] != o["groups"][b]]
        self.assertIn(f"{len(both) - len(diff)} of {len(both)} card objects are equal; the other {len(diff)} are "
                      + ", ".join("0x" + x[2:].upper() for x in diff), self.doc)
        s5080, ti = self.at("98.03.3B.C0.3E"), self.at("98.03.3B.80.2F")
        also = [o["id"] for o in both if o["id"] in diff and o["groups"][s5080] != o["groups"][ti]]
        self.assertIn(f"{len(also)} of these also differ between the ASUS 5080 and 5070 Ti", self.doc)
        self.assertEqual(self.differ_only_on(b), ["0x4f"])

    def test_manifest_sources_change_under_an_unchanged_tail(self):
        sources = {e["entry_id"] for m in self.ordered[0]["manifests"] for e in m["entries"] if e["type"] == 1}
        groups = {o["id"]: o["groups"] for o in self.survey["card_objects"]}
        tails = {g["id"]: g["tail"] for g in self.survey["descriptor_groups"]}
        for x, y, want in (
            ("98.05.28.00.64", "98.05.28.00.1D", ["0x89"]),
            ("98.03.3B.C0.3E", "98.03.3B.80.2F", ["0x14", "0x89", "0xd2"]),
        ):
            a, b = self.at(x), self.at(y)
            with self.subTest(pair=(x, y)):
                self.assertEqual(sorted(i for i in sources if groups[i][a] != groups[i][b]), want)
                for oid in ("0x87", "0x88"):
                    self.assertEqual(tails[oid][a], tails[oid][b])

    def test_die_alone_decides_no_payload(self):
        lap, desk = self.at("98.05.31.00.44"), self.at("98.05.28.00.64")
        for g in self.survey["descriptor_groups"]:
            self.assertNotEqual(g["payload"][lap], g["payload"][desk])
        efi = next(o["groups"] for o in self.survey["family_objects"] if o["id"] == "0x55")
        self.assertEqual(efi[lap], efi[desk])

    def test_objects_that_split_the_roms_as_outer_0c_does(self):
        value = [d["manifests"][0]["outer_header"]["0C"] for d in self.ordered]
        rows = {o["id"]: o["groups"] for o in self.survey["family_objects"] + self.survey["card_objects"]}

        def in_step(letters, use):
            pairs = {(letters[n], value[n]) for n in use}
            return len(pairs) == 2 == len({p[0] for p in pairs}) == len({p[1] for p in pairs})

        everyone = range(len(value))
        ten = [n for n in everyone if self.survey["roms"][n]["identity"]["pci_device"] != 0x2F04]
        whole = {k: g for k, g in rows.items() if None not in g}
        self.assertEqual(sorted(k for k, g in whole.items() if in_step(g, ten)), ["0x52", "0x5c", "0x74", "0x76"])
        self.assertEqual(sorted(k for k, g in whole.items() if in_step(g, everyone)), ["0x52", "0x5c", "0x74", "0x76"])
        two = sum(len({g[n] for n in ten}) == 2 for g in whole.values())
        self.assertIn(f"out of {two} objects with two contents", self.doc)
        sizes = {int(o["size"], 16) for r in self.survey["roms"] for d in r["directories"] for o in d["objects"]}
        self.assertFalse(sizes & {0x4BD, 0x3FC})

    def test_matrix_rows(self):
        _, body = table_after(read("docs/test-matrix.md"), "## 2. ")
        rows = {plain(r[1]): r for r in body}
        for data in self.ordered:
            spec = data["specimen"]
            with self.subTest(vbios=spec["vbios"]):
                row = rows[spec["vbios"]]
                self.assertEqual(
                    [int(x, 16) for x in plain(row[2]).split(" / ")],
                    [int(spec["bit_offset"], 16), int(spec["adjust"], 16)],
                )
                used = spec["ucode_slots"] - spec["ucode_empty_slots"]
                self.assertEqual(
                    plain(row[3]), f"{spec['ucode_slots']} / {used} / {spec['ucode_empty_slots']}"
                )

    def test_archive_is_anonymous(self):
        for p in self.dir.iterdir():
            text = p.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?i)users[\\\\/]|downloads|\.rom\"")


class ExternalFactTests(unittest.TestCase):
    """Facts quoted from outside sources must be quoted the same way everywhere."""

    def test_cot_key_sizes(self):
        sizes = load("scripts/abi-layout.py").COT_KEY_SIZES[2]
        row = next(x for x in read("docs/abi-fields.md").splitlines() if "**GB20x**" in x and "ECDSA" in x)
        self.assertIn(f"{sizes['public_key']} B", row)
        self.assertIn(f"{sizes['signature']} B", row)
        self.assertIn(f"{sizes['public_key']} B public key", read("docs/references.md"))
        self.assertIn(f"公钥 {sizes['public_key']} B", read("docs/references.cn.md"))

    def test_every_external_repository_is_registered(self):
        refs = read("docs/references.md")
        upstream = {"NVIDIA/open-gpu-kernel-modules"}
        for p in repo_files():
            if p.suffix not in (".md", ".py", ".sh"):
                continue
            for repo in re.findall(r"github\.com/([\w.-]+/[\w.-]+?)(?:\.git|[/)\s\"#]|$)", p.read_text(encoding="utf-8")):
                if repo not in upstream:
                    with self.subTest(path=str(p.relative_to(ROOT)), repo=repo):
                        self.assertIn(repo, refs)

    def test_no_step_by_step_reset_or_flash_commands(self):
        forbidden = re.compile(
            r"BRIDGE_CONTROL|setpci\s+-s|>\s*/sys/bus/pci/[^\s]*reset|nvflash\s+--?[a-z]|/reset_subordinate\b"
        )
        for p in repo_files():
            if p.suffix not in (".md", ".sh", ".py") or p.parts[-2] == "tests":
                continue
            with self.subTest(path=str(p.relative_to(ROOT))):
                self.assertIsNone(forbidden.search(p.read_text(encoding="utf-8")))


class V6DocTests(unittest.TestCase):
    """The machine-readable scalars must match the tables they were taken from."""

    @classmethod
    def setUpClass(cls):
        cls.doc = read("docs/vbios-v6-descriptor.md")
        cls.data = json.loads((GB205 / "v6-descriptors-98.05.31.00.44.json").read_text(encoding="utf-8"))
        cls.by_app = {d["app"]: d for d in cls.data["descriptors"]}

    def test_specimen_table(self):
        _, body = table_after(self.doc, "## 1. Specimens")
        self.assertEqual({r[0] for r in body}, set(self.by_app))
        for app, target, off, ver, hdr, size, flags in body:
            d = self.by_app[app]
            with self.subTest(app=app):
                self.assertEqual(int(target, 16), int(d["target"], 16))
                self.assertEqual(int(off, 16), int(d["descriptor_offset"], 16))
                self.assertEqual((int(ver), int(hdr)), (d["version"], d["header_size"]))
                self.assertEqual(int(size.replace(",", "")), d["stored_size"])
                self.assertEqual(int(flags, 16), int(d["flags"], 16))

    def test_field_tables(self):
        for heading in ("### 2.2 Structural", "### 2.3 Constant"):
            header, body = table_after(self.doc, heading)
            for row in body:
                off = row[0].lstrip("+")
                for i, h in enumerate(header):
                    if h.startswith("APP "):
                        with self.subTest(offset=off, app=h):
                            self.assertEqual(
                                int(row[i], 16), int(self.by_app[h[4:]]["fields"][off], 16)
                            )

    def test_independent_parser_comparison(self):
        heads = {
            e["entry_id"]: e["table_header"]
            for m in self.data["manifests"] for e in m["entries"] if "table_header" in e
        }
        for eid in ("0x12", "0x14"):
            h = heads[eid]
            long_form = f"0x{h['version']:02x} / {h['header_size']} / {h['record_size']} / {h['count']}"
            with self.subTest(entry=eid):
                self.assertIn(long_form, self.doc)
                self.assertIn(long_form[2:], read("docs/references.md"))

    def test_open_questions_are_numbered_consecutively(self):
        for page in (self.doc, read("docs/vbios-v6-descriptor.cn.md")):
            section = page.split("\n## 8. ", 1)[1].split("\n---\n", 1)[0]
            numbers = [int(n) for n in re.findall(r"(?m)^(\d+)\. ", section)]
            self.assertTrue(numbers)
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_manifest_tables(self):
        for m, heading in zip(self.data["manifests"], ("### 6.2 APP 0x19", "### 6.3 APP 0x15")):
            _, body = table_after(self.doc, heading)
            quoted = [[int(plain(x), 16) for x in (r[1], r[3], r[4], r[5], r[6])] + [int(plain(r[2]))] for r in body]
            stored = [
                [int(e[k], 16) for k in ("entry_id", "field_a", "field_b", "field_c", "field_d")] + [e["type"]]
                for e in m["entries"]
            ]
            with self.subTest(app=m["app"]):
                self.assertEqual(quoted, stored)


if __name__ == "__main__":
    unittest.main()
