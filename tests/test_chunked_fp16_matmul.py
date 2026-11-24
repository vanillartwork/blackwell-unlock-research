# SPDX-License-Identifier: GPL-2.0-only

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

try:
    import torch
except ImportError:
    torch = None


MODULE_PATH = Path(__file__).resolve().parents[1] / "software" / "chunked_fp16_matmul.py"


def load_chunked_module():
    spec = importlib.util.spec_from_file_location("chunked_fp16_matmul", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PlanKChunksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_chunked_module()

    def test_even_partition(self):
        self.assertEqual(
            self.mod.plan_k_chunks(8192, 2048),
            [(0, 2048), (2048, 4096), (4096, 6144), (6144, 8192)],
        )

    def test_uneven_tail_partition(self):
        self.assertEqual(
            self.mod.plan_k_chunks(500, 200),
            [(0, 200), (200, 400), (400, 500)],
        )

    def test_chunk_larger_than_k(self):
        self.assertEqual(self.mod.plan_k_chunks(128, 1024), [(0, 128)])

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            self.mod.plan_k_chunks(0, 64)
        with self.assertRaises(ValueError):
            self.mod.plan_k_chunks(64, 0)
        with self.assertRaises(ValueError):
            self.mod.plan_k_chunks(64, -16)


class MissingTorchTests(unittest.TestCase):
    def test_cli_names_the_missing_dependency(self):
        mod = load_chunked_module()
        err = io.StringIO()
        # A None entry makes `import torch` raise ImportError even where it is installed.
        with mock.patch.dict(sys.modules, {"torch": None}), \
                mock.patch.object(sys, "argv", ["chunked_fp16_matmul.py"]), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("requirements.txt", err.getvalue())


class AnnotateRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_chunked_module()

    @staticmethod
    def row(tflops, err, num_chunks=None):
        r = {"tflops": tflops, "sample_relative_l2_error": err}
        if num_chunks is not None:
            r["num_chunks"] = num_chunks
        return r

    def test_full_fp16_row_is_the_error_reference(self):
        rows = self.mod.annotate_rows([
            self.row(40.0, 2e-4), self.row(44.0, 1e-3, 4), self.row(60.0, 4e-3, 1),
        ])
        self.assertAlmostEqual(rows[1]["speedup_vs_fp32acc"], 1.1)
        self.assertAlmostEqual(rows[1]["error_ratio_vs_fp32acc"], 5.0)
        self.assertAlmostEqual(rows[1]["error_reduction_vs_full_fp16acc"], 4.0)
        self.assertAlmostEqual(rows[2]["error_reduction_vs_full_fp16acc"], 1.0)

    def test_sweep_without_full_row_does_not_invent_a_reference(self):
        rows = self.mod.annotate_rows([
            self.row(40.0, 2e-4), self.row(38.0, 9e-4, 8), self.row(44.0, 1e-3, 4),
        ])
        for r in rows:
            self.assertIsNone(r["error_reduction_vs_full_fp16acc"])
        self.assertAlmostEqual(rows[2]["error_ratio_vs_fp32acc"], 5.0)

    def test_non_finite_sample_leaves_ratios_empty(self):
        rows = self.mod.annotate_rows([self.row(40.0, 2e-4), self.row(60.0, None, 1)])
        self.assertIsNone(rows[1]["error_ratio_vs_fp32acc"])
        self.assertIsNone(rows[0]["error_reduction_vs_full_fp16acc"])


@unittest.skipUnless(torch is not None, "PyTorch not installed in this environment")
class ChunkedFp16MatmulTorchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_chunked_module()

    def test_cpu_2d_and_batched_numerical_equivalence(self):
        gen = torch.Generator().manual_seed(42)
        a = torch.randn(32, 160, dtype=torch.float16, generator=gen)
        b = torch.randn(160, 24, dtype=torch.float16, generator=gen)
        ref = a.float() @ b.float()

        out_fp32 = self.mod.chunked_fp16_mm(a, b, chunk_k=64, out_dtype=torch.float32)
        self.assertEqual(out_fp32.dtype, torch.float32)
        self.assertEqual(out_fp32.shape, (32, 24))
        rel_l2 = (out_fp32 - ref).norm() / ref.norm()
        self.assertLess(rel_l2.item(), 5e-3)

        out_fp16 = self.mod.chunked_fp16_mm(a, b, chunk_k=256)
        self.assertEqual(out_fp16.dtype, torch.float16)
        self.assertEqual(out_fp16.shape, (32, 24))

        a_batched = a.reshape(4, 8, 160)
        out_batched = self.mod.chunked_fp16_matmul(a_batched, b, chunk_k=64, out_dtype=torch.float32)
        self.assertEqual(out_batched.shape, (4, 8, 24))
        self.assertTrue(torch.allclose(out_batched.reshape(32, 24), out_fp32, atol=1e-4))

    def test_validation_errors(self):
        a = torch.zeros((4, 8), dtype=torch.float16)
        b_bad_k = torch.zeros((16, 4), dtype=torch.float16)
        b_bad_dtype = torch.zeros((8, 4), dtype=torch.float32)
        with self.assertRaises(ValueError):
            self.mod.chunked_fp16_mm(a, b_bad_k)
        with self.assertRaises(ValueError):
            self.mod.chunked_fp16_mm(a, b_bad_dtype)
        with self.assertRaises(ValueError):
            self.mod.chunked_fp16_mm(a.unsqueeze(0), torch.zeros((8, 4), dtype=torch.float16))

    def test_autograd_inputs_are_rejected_with_a_clear_error(self):
        a = torch.randn(4, 8, dtype=torch.float16)
        b = torch.randn(8, 4, dtype=torch.float16, requires_grad=True)
        for chunk_k in (4, 8):
            with self.assertRaisesRegex(RuntimeError, "inference-only"):
                self.mod.chunked_fp16_mm(a, b, chunk_k=chunk_k)
        with torch.no_grad():
            self.assertEqual(self.mod.chunked_fp16_mm(a, b, chunk_k=4).shape, (4, 4))

    @unittest.skipUnless(torch is not None and torch.cuda.is_available(), "CUDA GPU not available")
    def test_cuda_restores_allow_fp16_accumulation_flag(self):
        matmul = torch.backends.cuda.matmul
        if not hasattr(matmul, "allow_fp16_accumulation"):
            self.skipTest("allow_fp16_accumulation unavailable")
        a = torch.randn(64, 256, device="cuda", dtype=torch.float16)
        b = torch.randn(256, 64, device="cuda", dtype=torch.float16)
        before = matmul.allow_fp16_accumulation
        try:
            matmul.allow_fp16_accumulation = False
            out = self.mod.chunked_fp16_mm(a, b, chunk_k=128, use_fp16_accumulation=True)
            self.assertEqual(out.shape, (64, 64))
            self.assertFalse(matmul.allow_fp16_accumulation)
        finally:
            matmul.allow_fp16_accumulation = before


if __name__ == "__main__":
    unittest.main()
