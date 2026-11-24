# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


TOOL_PATH = Path(__file__).resolve().parents[1] / "software" / "clamp_eval.py"


def load_eval_module():
    spec = importlib.util.spec_from_file_location("clamp_eval", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MissingTorchTests(unittest.TestCase):
    def test_cli_names_the_missing_dependency(self):
        mod = load_eval_module()
        err = io.StringIO()
        # A None entry makes `import torch` raise ImportError even where it is installed.
        with mock.patch.dict(sys.modules, {"torch": None}), \
                mock.patch.object(sys, "argv", ["clamp_eval.py"]), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("requirements.txt", err.getvalue())


class ClampEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_eval_module()

    def test_arch_key_mapping(self):
        self.assertEqual(self.mod.arch_key(12, 0), "blackwell_gb20x")
        self.assertEqual(self.mod.arch_key(8, 9), "ada")
        self.assertEqual(self.mod.arch_key(8, 6), "ampere_ga10x")
        self.assertEqual(self.mod.arch_key(7, 5), "turing")
        self.assertIsNone(self.mod.arch_key(6, 1))

    def test_flop_per_clk_sm_math(self):
        # 46 SMs @ 2000 MHz, 47.104 TFLOPS => 512.0 FLOP/clk/SM
        got = self.mod.flop_per_clk_sm(47.104, 2000.0, 46)
        self.assertAlmostEqual(got, 512.0, places=3)
        self.assertIsNone(self.mod.flop_per_clk_sm(50.0, 0.0, 46))
        self.assertIsNone(self.mod.flop_per_clk_sm(50.0, None, 46))

    def test_classify_case_boundaries(self):
        self.assertIn("CLAMPED", self.mod.classify_case("fp16_fp32acc", 0.51))
        self.assertIn("near full", self.mod.classify_case("bf16_fp32acc", 0.92))
        self.assertIn("intermediate", self.mod.classify_case("tf32_fp32acc", 0.72))
        self.assertIn("SIMT control", self.mod.classify_case("fp32_control", None))
        self.assertIn("informational", self.mod.classify_case("int8", 0.30))

    def test_telemetry_summary_filters_idle_ramp_and_tail_clocks(self):
        samples = [
            {"t": 0.00, "sm_clock_mhz": 247.0, "power_w": 18.0, "temperature_c": 52.0, "utilization_gpu_pct": 10.0, "throttle_reasons": 1},
            {"t": 0.10, "sm_clock_mhz": 1100.0, "power_w": 45.0, "temperature_c": 55.0, "utilization_gpu_pct": 60.0, "throttle_reasons": 4},
            {"t": 0.30, "sm_clock_mhz": 2280.0, "power_w": 96.0, "temperature_c": 60.0, "utilization_gpu_pct": 99.0, "throttle_reasons": 4},
            {"t": 0.40, "sm_clock_mhz": 2310.0, "power_w": 98.0, "temperature_c": 61.0, "utilization_gpu_pct": 100.0, "throttle_reasons": 4},
            {"t": 0.50, "sm_clock_mhz": 2300.0, "power_w": 99.0, "temperature_c": 62.0, "utilization_gpu_pct": 100.0, "throttle_reasons": 4},
            {"t": 0.60, "sm_clock_mhz": 247.0, "power_w": 22.0, "temperature_c": 59.0, "utilization_gpu_pct": 5.0, "throttle_reasons": 1},
        ]
        summary = self.mod.summarize_telemetry_samples(samples, settle_s=0.25)
        self.assertEqual(summary["sm_clock_mhz"], 2300.0)
        self.assertEqual(summary["power_w"], 98.0)
        self.assertEqual(summary["power_peak_w"], 99.0)
        self.assertEqual(summary["temperature_peak_c"], 62.0)
        self.assertEqual(summary["telemetry_active_samples"], 3)

    def test_format_ratio_tolerates_missing_clock_telemetry(self):
        self.assertEqual(self.mod.format_ratio(1.234), "1.23x")
        self.assertEqual(self.mod.format_ratio(None), "n/a")

    def test_pci_bus_id_formatting(self):
        props = types.SimpleNamespace(pci_domain_id=0, pci_bus_id=0x41, pci_device_id=0)
        self.assertEqual(self.mod.pci_bus_id(props), "00000000:41:00.0")
        # Older PyTorch builds expose no PCI location at all.
        self.assertIsNone(self.mod.pci_bus_id(types.SimpleNamespace(name="gpu")))

    def test_nvml_handle_prefers_pci_bus_id_over_index(self):
        fake = mock.Mock()
        with mock.patch.dict(sys.modules, {"pynvml": fake}):
            self.mod.nvml_handle("00000000:41:00.0")
            fake.nvmlDeviceGetHandleByPciBusId.assert_called_once_with("00000000:41:00.0")
            fake.nvmlDeviceGetHandleByIndex.assert_not_called()
            self.mod.nvml_handle(1)
            fake.nvmlDeviceGetHandleByIndex.assert_called_once_with(1)

    def test_smi_queries_use_the_given_device_id(self):
        with mock.patch.object(
            self.mod.subprocess, "check_output", return_value="621.01, 98.05, 0x2F1810DE, [N/A], 140.00 W"
        ) as co:
            meta = self.mod.query_nvidia_smi_meta("00000000:41:00.0")
        self.assertEqual(meta["driver"], "621.01")
        self.assertIn("00000000:41:00.0", co.call_args.args[0])


class TelemetryMatchTests(unittest.TestCase):
    """Telemetry must describe the benchmarked card or nothing at all."""

    UUID = "01234567-89ab-cdef-0123-456789abcdef"

    @classmethod
    def setUpClass(cls):
        cls.mod = load_eval_module()

    def resolve(self, names=(), **props):
        with mock.patch.object(self.mod, "list_smi_names", return_value=list(names)):
            return self.mod.resolve_telemetry_device(types.SimpleNamespace(**props))

    def test_order_of_preference(self):
        pci = {"pci_domain_id": 0, "pci_bus_id": 0x41, "pci_device_id": 0}
        self.assertEqual(
            self.resolve(name="A", uuid=self.UUID, **pci), ("00000000:41:00.0", "pci_bus_id")
        )
        # PyTorch 2.7, the declared minimum, has the UUID but no PCI location.
        self.assertEqual(self.resolve(name="A", uuid=self.UUID), (f"GPU-{self.UUID}", "uuid"))
        self.assertEqual(
            self.resolve(name="A", uuid=f"GPU-{self.UUID.upper()}"), (f"GPU-{self.UUID}", "uuid")
        )

    def test_name_is_used_only_when_it_is_unique(self):
        self.assertEqual(self.resolve(["B", "A"], name="A"), (1, "unique_name"))
        self.assertEqual(self.resolve(["A", "A"], name="A"), (None, "unmatched"))
        self.assertEqual(self.resolve(["B"], name="A"), (None, "unmatched"))
        self.assertEqual(self.resolve([], name="A"), (None, "unmatched"))
        self.assertEqual(self.resolve(["A", "A"], name="A", uuid="MIG-bogus"), (None, "unmatched"))

    def test_unmatched_device_reads_no_telemetry_at_all(self):
        err = io.StringIO()
        with mock.patch.object(self.mod, "list_smi_names", return_value=["A", "A"]), \
                mock.patch.object(self.mod.subprocess, "check_output") as co, \
                contextlib.redirect_stderr(err):
            got = self.mod.open_telemetry(types.SimpleNamespace(name="A"))
            self.assertEqual(got, (None, "unmatched", {}, None, None))
            sampler = self.mod.TelemetrySampler(None, None, device_id=None, interval=0.01).start()
            sampler.stop()
            co.assert_not_called()
        self.assertEqual(sampler.samples, [])
        self.assertIsNone(sampler.summary()["sm_clock_mhz"])
        self.assertIn("cannot tell which physical GPU", err.getvalue())

    def test_sampler_does_not_default_to_the_first_card(self):
        self.assertIsNone(self.mod.TelemetrySampler(None, None).device_id)

    def test_nvml_opens_by_uuid(self):
        fake = mock.Mock()
        with mock.patch.dict(sys.modules, {"pynvml": fake}):
            self.mod.nvml_handle(f"GPU-{self.UUID}")
            self.assertEqual(self.mod.nvml_handle(None), (None, None))
        fake.nvmlDeviceGetHandleByUUID.assert_called_once_with(f"GPU-{self.UUID}")
        fake.nvmlDeviceGetHandleByIndex.assert_not_called()

    def test_both_benchmarks_share_the_matching(self):
        software = TOOL_PATH.parent
        for name in ("clamp_eval.py", "chunked_fp16_matmul.py"):
            text = (software / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("open_telemetry(props)", text)
                self.assertNotRegex(text, r"\bor args\.device\s*\n")
                self.assertNotRegex(text, r"(?:nvml_handle|query_nvidia_smi_meta)\(args\.device\)")


if __name__ == "__main__":
    unittest.main()
