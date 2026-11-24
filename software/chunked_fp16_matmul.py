#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Route 2: K-chunked FP16-acc GEMM with FP32 reduction.

On consumer Blackwell (RTX 50 / GB20x) GPUs, FP16->FP32 tensor-core accumulation
is hardware-clamped to ~0.5x rate, whereas full FP16->FP16 accumulation runs near
full rate (~1.5-1.7x faster) at the cost of higher L2 error on long inner
dimensions (e.g., K=4096..8192).

Splitting the inner K dimension into chunks of `chunk_k`:
  - executes each sub-GEMM on the unclamped `allow_fp16_accumulation=True` path
  - accumulates partial products across chunks in FP32 (`acc.add_(partial)`)

This gives practitioners a controllable throughput-vs-accuracy Pareto knob
without kernel drivers, firmware changes, or custom CUDA C++ extensions.

Usage:
  python software/chunked_fp16_matmul.py --n 8192 --chunks 512,1024,2048,4096,8192 --json out.json
"""

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

# Reuse steady-state telemetry helpers from sibling clamp_eval.py when run as CLI.
_THIS_DIR = Path(__file__).resolve().parent


def plan_k_chunks(k, chunk_k):
    """Return contiguous (start, end) half-open slices partitioning [0, k)."""
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    if chunk_k < 1:
        raise ValueError(f"chunk_k must be positive, got {chunk_k}")
    return [(start, min(start + chunk_k, k)) for start in range(0, k, chunk_k)]


def chunked_fp16_mm(a, b, chunk_k=2048, out_dtype=None, use_fp16_accumulation=True):
    """2D FP16 matrix multiply with K-chunked FP16-acc GEMMs and FP32 reduction.

    Args:
        a: 2D float16 tensor of shape (M, K)
        b: 2D float16 tensor of shape (K, N)
        chunk_k: inner-dimension chunk size per sub-GEMM
        out_dtype: output dtype (defaults to a.dtype, i.e. float16; pass torch.float32
            to retain full FP32 reduction precision)
        use_fp16_accumulation: toggle `torch.backends.cuda.matmul.allow_fp16_accumulation`
            during the sub-GEMMs when available on CUDA.

    Inference only: the chunk loop writes into reused buffers (`out=` / `add_`), so it
    is not differentiable. Each partial product is held in FP16 and overflows to inf
    above 65504, so scale the inputs if a chunk can reach that magnitude.

    The accumulation flag is process-global and is restored on exit; do not run this
    concurrently with other CUDA matmuls in the same process.
    """
    import torch

    if a.ndim != 2 or b.ndim != 2:
        raise ValueError(f"chunked_fp16_mm expects 2D tensors, got {a.ndim}D and {b.ndim}D")
    if a.size(1) != b.size(0):
        raise ValueError(f"inner dimensions mismatch: {a.shape} vs {b.shape}")
    if a.dtype != torch.float16 or b.dtype != torch.float16:
        raise ValueError(f"expected float16 inputs, got {a.dtype} and {b.dtype}")
    if torch.is_grad_enabled() and (a.requires_grad or b.requires_grad):
        raise RuntimeError(
            "chunked_fp16_mm is inference-only; wrap the call in torch.no_grad() "
            "or torch.inference_mode()"
        )

    m, k = a.shape
    n = b.size(1)
    target_dtype = out_dtype if out_dtype is not None else a.dtype
    chunks = plan_k_chunks(k, chunk_k)

    matmul = torch.backends.cuda.matmul
    can_toggle = a.is_cuda and hasattr(matmul, "allow_fp16_accumulation")
    old_flag = matmul.allow_fp16_accumulation if can_toggle else None
    try:
        if can_toggle:
            matmul.allow_fp16_accumulation = bool(use_fp16_accumulation)
        if len(chunks) == 1:
            res = torch.mm(a, b)
            return res if res.dtype == target_dtype else res.to(target_dtype)

        acc = torch.zeros((m, n), dtype=torch.float32, device=a.device)
        partial = torch.empty((m, n), dtype=torch.float16, device=a.device)
        for k0, k1 in chunks:
            torch.mm(a[:, k0:k1], b[k0:k1, :], out=partial)
            acc.add_(partial)
        return acc if target_dtype == torch.float32 else acc.to(target_dtype)
    finally:
        if can_toggle:
            matmul.allow_fp16_accumulation = old_flag


def chunked_fp16_matmul(a, b, chunk_k=2048, out_dtype=None, use_fp16_accumulation=True):
    """Batched wrapper for (*, M, K) @ (K, N) linear-layer projections."""
    if a.ndim < 2 or b.ndim != 2:
        raise ValueError(f"expected a.ndim >= 2 and b.ndim == 2, got {a.ndim}D and {b.ndim}D")
    lead_shape = a.shape[:-1]
    k = a.shape[-1]
    n = b.shape[1]
    a_2d = a.reshape(-1, k)
    out_2d = chunked_fp16_mm(
        a_2d,
        b,
        chunk_k=chunk_k,
        out_dtype=out_dtype,
        use_fp16_accumulation=use_fp16_accumulation,
    )
    return out_2d.reshape(*lead_shape, n)


def _load_clamp_eval_helpers():
    import importlib.util

    path = _THIS_DIR / "clamp_eval.py"
    spec = importlib.util.spec_from_file_location("clamp_eval_helpers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def annotate_rows(rows):
    """Attach speed/error ratios to sweep rows in place and return them.

    The first row must be the clamped FP32-accumulate baseline. The unchunked FP16
    reference is the row that ran as a single chunk; when the sweep did not include
    one, its ratio column stays None instead of silently using another chunk size.
    """
    base = rows[0]
    full_err = next(
        (r["sample_relative_l2_error"] for r in rows if r.get("num_chunks") == 1), None
    )
    for r in rows:
        err = r["sample_relative_l2_error"]
        base_err = base["sample_relative_l2_error"]
        r["speedup_vs_fp32acc"] = r["tflops"] / base["tflops"] if base["tflops"] else None
        r["error_ratio_vs_fp32acc"] = err / base_err if (err and base_err) else None
        r["error_reduction_vs_full_fp16acc"] = full_err / err if (full_err and err) else None
    return rows


def _bench_variant(torch, ce, device_id, a, b, ref, label, op_fn, warmup, min_iters, min_seconds, max_iters, sm_count, nv, handle):
    out_holder = [None]

    def run_one():
        out_holder[0] = op_fn()
        return out_holder[0]

    for _ in range(warmup):
        run_one()
    torch.cuda.synchronize()

    p0 = torch.cuda.Event(enable_timing=True)
    p1 = torch.cuda.Event(enable_timing=True)
    p0.record()
    run_one()
    p1.record()
    torch.cuda.synchronize()
    pilot_s = max(p0.elapsed_time(p1) / 1000.0, 1e-4)
    iters = min(max_iters, max(min_iters, int(min_seconds / pilot_s) + 1))

    events = [
        (torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True))
        for _ in range(iters)
    ]
    sampler = ce.TelemetrySampler(nv, handle, device_id=device_id).start()
    for start, end in events:
        start.record()
        run_one()
        end.record()
    torch.cuda.synchronize()
    sampler.stop()

    ms_list = sorted(s.elapsed_time(e) for s, e in events)
    median_ms = statistics.median(ms_list)
    m, k = a.shape
    n = b.size(1)
    tflops = (2.0 * m * n * k) / (median_ms * 1e9)
    stats = sampler.summary(settle_s=min(0.25, (iters * median_ms / 1000.0) * 0.25))
    fsm = ce.flop_per_clk_sm(tflops, stats["sm_clock_mhz"], sm_count)

    sample = out_holder[0][: ref.size(0), : ref.size(1)].detach().cpu().double()
    finite = bool(sample.isfinite().all().item())
    rel_l2 = ((sample - ref).norm() / ref.norm()).item() if finite else None

    return {
        "label": label,
        "iters": iters,
        "median_ms": median_ms,
        "tflops": tflops,
        "flop_clk_sm": fsm,
        "sample_finite": finite,
        "sample_relative_l2_error": rel_l2,
        **stats,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", type=int, default=0, help="CUDA device index (default: 0)")
    ap.add_argument("--n", type=int, default=8192, help="square matrix size M=N=K (default: 8192)")
    ap.add_argument(
        "--chunks",
        type=str,
        default="512,1024,2048,4096,8192",
        help="comma-separated chunk_k values to sweep (default: 512,1024,2048,4096,8192)",
    )
    ap.add_argument("--warmup", type=int, default=8)
    ap.add_argument("--min-iters", type=int, default=30)
    ap.add_argument("--min-seconds", type=float, default=2.0)
    ap.add_argument("--max-iters", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    chunk_sizes = sorted({int(x.strip()) for x in args.chunks.split(",") if x.strip()})
    if not chunk_sizes or min(chunk_sizes) < 1:
        ap.error("--chunks must contain positive integers")
    if min(args.n, args.warmup, args.min_iters, args.max_iters) < 1 or args.min_seconds <= 0:
        ap.error("--n, --warmup, --min-iters, --max-iters, and --min-seconds must be positive")

    try:
        import torch
    except ImportError:
        ap.error("PyTorch is not installed; see requirements.txt for the CUDA build")

    if not torch.cuda.is_available() or torch.version.cuda is None:
        ap.error("CUDA PyTorch + NVIDIA GPU required")
    if not hasattr(torch.backends.cuda.matmul, "allow_fp16_accumulation"):
        ap.error("this PyTorch build lacks allow_fp16_accumulation")
    if args.device < 0 or args.device >= torch.cuda.device_count():
        ap.error(f"--device {args.device} out of range (found {torch.cuda.device_count()} CUDA device(s))")

    ce = _load_clamp_eval_helpers()
    torch.cuda.set_device(args.device)
    device = f"cuda:{args.device}"
    props = torch.cuda.get_device_properties(args.device)
    device_id, telemetry_match, smi_meta, nv, handle = ce.open_telemetry(props)

    gen = torch.Generator(device=device).manual_seed(args.seed)
    a = torch.randn(args.n, args.n, device=device, dtype=torch.float16, generator=gen)
    b = torch.randn(args.n, args.n, device=device, dtype=torch.float16, generator=gen)
    ref = a[:16, :].cpu().double() @ b[:, :16].cpu().double()

    print("=" * 94)
    print(f"GPU    : {props.name} (cc {props.major}.{props.minor}, {props.multi_processor_count} SMs)")
    print(f"Driver : {smi_meta.get('driver', 'n/a')} / VBIOS {smi_meta.get('vbios', 'n/a')} | PyTorch {torch.__version__}")
    print(f"Sweep  : N=K={args.n}, chunks={chunk_sizes}, min_s={args.min_seconds}")
    print("=" * 94)

    rows = []
    # 1. Clamped baseline: standard FP16 -> FP32 accumulate
    base_out = torch.empty((args.n, args.n), device=device, dtype=torch.float16)

    def run_fp32acc_base():
        matmul = torch.backends.cuda.matmul
        old = matmul.allow_fp16_accumulation
        try:
            matmul.allow_fp16_accumulation = False
            return torch.mm(a, b, out=base_out)
        finally:
            matmul.allow_fp16_accumulation = old

    base_row = _bench_variant(
        torch, ce, device_id, a, b, ref, "baseline_fp32acc", run_fp32acc_base,
        args.warmup, args.min_iters, args.min_seconds, args.max_iters,
        props.multi_processor_count, nv, handle,
    )
    base_row["chunk_k"] = None
    rows.append(base_row)

    # 2. K-chunked FP16-acc + FP32 outer reduction sweep (pre-allocated work buffers)
    acc_buf = torch.empty((args.n, args.n), device=device, dtype=torch.float32)
    part_buf = torch.empty((args.n, args.n), device=device, dtype=torch.float16)
    out_buf = torch.empty((args.n, args.n), device=device, dtype=torch.float16)

    for ck in chunk_sizes:
        slices = plan_k_chunks(args.n, ck)

        def make_chunked_op(local_slices):
            def op():
                matmul = torch.backends.cuda.matmul
                old = matmul.allow_fp16_accumulation
                try:
                    matmul.allow_fp16_accumulation = True
                    if len(local_slices) == 1:
                        return torch.mm(a, b, out=out_buf)
                    acc_buf.zero_()
                    for k0, k1 in local_slices:
                        torch.mm(a[:, k0:k1], b[k0:k1, :], out=part_buf)
                        acc_buf.add_(part_buf)
                    out_buf.copy_(acc_buf)
                    return out_buf
                finally:
                    matmul.allow_fp16_accumulation = old

            return op

        lbl = f"chunk_k={ck}" if len(slices) > 1 else f"full_fp16acc(k={ck})"
        r = _bench_variant(
            torch, ce, device_id, a, b, ref, lbl, make_chunked_op(slices),
            args.warmup, args.min_iters, args.min_seconds, args.max_iters,
            props.multi_processor_count, nv, handle,
        )
        r["chunk_k"] = ck
        r["num_chunks"] = len(slices)
        rows.append(r)

    annotate_rows(rows)

    hdr = f"{'variant':22s}{'chunks':>7}{'TFLOP/s':>9}{'vsBase':>8}{'clk':>7}{'W':>7}{'FLOP/c/SM':>11}{'relL2':>11}{'errRatio':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        speedup = r["speedup_vs_fp32acc"]
        err_ratio = r["error_ratio_vs_fp32acc"]
        ck_s = str(r.get("num_chunks", "-"))
        clk_s = f"{r['sm_clock_mhz']:.0f}" if r["sm_clock_mhz"] else "n/a"
        pw_s = f"{r['power_w']:.1f}" if r["power_w"] else "n/a"
        fsm_s = f"{r['flop_clk_sm']:.1f}" if r["flop_clk_sm"] else "-"
        err_s = f"{r['sample_relative_l2_error']:.6f}" if r["sample_relative_l2_error"] is not None else "-"
        er_s = f"{err_ratio:.2f}x" if err_ratio is not None else "-"
        print(
            f"{r['label']:22s}{ck_s:>7}{r['tflops']:9.2f}{ce.format_ratio(speedup):>8}{clk_s:>7}{pw_s:>7}"
            f"{fsm_s:>11}{err_s:>11}{er_s:>10}"
        )

    payload = {
        "schema": "blackwell-abi-lab/chunked-fp16/1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "info": {
            "os": platform.system(),
            "os_release": platform.release(),
            "torch": str(torch.__version__),
            "cuda": torch.version.cuda,
            "gpu": props.name,
            "compute_capability": [props.major, props.minor],
            "sm_count": props.multi_processor_count,
            "telemetry_match": telemetry_match,
            **smi_meta,
        },
        "config": {
            "n": args.n,
            "chunks": chunk_sizes,
            "warmup": args.warmup,
            "min_iters": args.min_iters,
            "min_seconds": args.min_seconds,
            "seed": args.seed,
        },
        "results": rows,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_bytes((json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
