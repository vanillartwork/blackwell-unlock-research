#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify archived Dual QMMA.SF (W8A16/W4A16/FP8-KV) and 517M-block scan results on CPU."""

import argparse
import json
import math
from pathlib import Path
import random
import sys


DEFAULT_ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "gb205-2f18"
    / "route2-dual-qmma-621.01.json"
)


def percentile(sorted_vals, pct):
    pos = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def mean(vals):
    return sum(vals) / len(vals)


def draw_resamples(n, n_boot, seed):
    rng = random.Random(seed)
    resamples = []
    for _ in range(n_boot):
        counts = {}
        for _ in range(n):
            i = rng.randrange(n)
            counts[i] = counts.get(i, 0) + 1
        resamples.append(tuple(counts.items()))
    return resamples


def boot_mean_ci(vals, resamples):
    inv_n = 1.0 / len(vals)
    draws = [sum(vals[i] * c for i, c in r) * inv_n for r in resamples]
    draws.sort()
    return mean(vals), percentile(draws, 2.5), percentile(draws, 97.5)


def boot_ratio_ci(num_vals, den_vals, resamples):
    draws = []
    for r in resamples:
        sn = sum(num_vals[i] * c for i, c in r)
        sd = sum(den_vals[i] * c for i, c in r)
        draws.append(sn / max(sd, 1e-30))
    draws.sort()
    return sum(num_vals) / max(sum(den_vals), 1e-30), percentile(draws, 2.5), percentile(draws, 97.5)


def verify_and_summarize(data, n_boot=None, seed=None):
    if data.get("schema") != "blackwell-abi-lab/route2-dual-qmma/1":
        raise ValueError("unexpected schema")

    sms = data["sm_count"]
    # 1. Verify TMA-WS clock-normalized throughput consistency
    tma_rows = []
    for sh in data["tma_ws_bench"]["shapes"]:
        by_lbl = {r["label"]: r for r in sh["results"]}
        bf = by_lbl["cuBLAS BF16->FP32 (W8 dequant)"]
        for r in sh["results"]:
            if r["ghz"] > 0.1:
                calc_tf_ghz = r["tflops"] / r["ghz"]
                calc_fl_clk = calc_tf_ghz * 1000.0 / sms
                if abs(calc_tf_ghz - r["tflops_per_ghz"]) > 0.02:
                    raise ValueError(f"tflops_per_ghz mismatch for {sh['name']} / {r['label']}")
                if abs(calc_fl_clk - r["flop_sm_clk"]) > 0.5:
                    raise ValueError(f"flop_sm_clk mismatch for {sh['name']} / {r['label']}")
        tma_rows.append({
            "name": sh["name"],
            "bf16_tflops": bf["tflops"],
            "bf16_fl_clk": bf["flop_sm_clk"],
            "w8_pre_wall_x": by_lbl["TMA-WS Dual-FP8 W8A16 (pre-split)"]["tflops"] / bf["tflops"],
            "w8_pre_iso_x": by_lbl["TMA-WS Dual-FP8 W8A16 (pre-split)"]["flop_sm_clk"] / bf["flop_sm_clk"],
            "w8_2k_wall_x": by_lbl["TMA-WS Dual-FP8 W8A16 (2-kernel)"]["tflops"] / bf["tflops"],
            "w8_2k_iso_x": by_lbl["TMA-WS Dual-FP8 W8A16 (2-kernel)"]["flop_sm_clk"] / bf["flop_sm_clk"],
            "w8_on_wall_x": by_lbl["TMA-WS Dual-FP8 W8A16 (online)"]["tflops"] / bf["tflops"],
            "w8_on_iso_x": by_lbl["TMA-WS Dual-FP8 W8A16 (online)"]["flop_sm_clk"] / bf["flop_sm_clk"],
            "w4_pre_wall_x": by_lbl["TMA-WS Dual-FP8 W4A16 (pre-split)"]["tflops"] / bf["tflops"],
            "w4_pre_iso_x": by_lbl["TMA-WS Dual-FP8 W4A16 (pre-split)"]["flop_sm_clk"] / bf["flop_sm_clk"],
            "w4_2k_wall_x": by_lbl["TMA-WS Dual-FP8 W4A16 (2-kernel)"]["tflops"] / bf["tflops"],
            "w4_2k_iso_x": by_lbl["TMA-WS Dual-FP8 W4A16 (2-kernel)"]["flop_sm_clk"] / bf["flop_sm_clk"],
            "w4_on_wall_x": by_lbl["TMA-WS Dual-FP8 W4A16 (online)"]["tflops"] / bf["tflops"],
            "w4_on_iso_x": by_lbl["TMA-WS Dual-FP8 W4A16 (online)"]["flop_sm_clk"] / bf["flop_sm_clk"],
        })

    # 2. Verify 517M-block scan totals and percentages
    total_blocks = 0
    total_elems = 0
    total_int8_pass = 0
    scan_summary = {}
    for mid, tensors in data["block_scan_517m"].items():
        scan_summary[mid] = {}
        for kind, s in tensors.items():
            if s["elems"] != s["blocks"] * 32:
                raise ValueError(f"elems != blocks*32 for {mid}/{kind}")
            if s["nz_elems"] + s["zero_elems"] != s["elems"]:
                raise ValueError(f"nz + zero != elems for {mid}/{kind}")
            if s["in_2m6"] + s["below_2m6"] != s["nz_elems"]:
                raise ValueError(f"in_2m6 + below_2m6 != nz_elems for {mid}/{kind}")
            pct_1_8 = 100.0 * s["in_1_8"] / s["nz_elems"]
            pct_2m6 = 100.0 * s["in_2m6"] / s["nz_elems"]
            pct_exact = 100.0 * s["exact_bit_match"] / s["elems"]
            rel_s = math.sqrt(s["err_single_sq"] / s["ref_sq"])
            rel_d = math.sqrt(s["err_dual_sq"] / s["ref_sq"])
            total_blocks += s["blocks"]
            total_elems += s["elems"]
            total_int8_pass += s["int8_pass"]
            scan_summary[mid][kind] = {
                "blocks": s["blocks"],
                "pct_nz_in_1_8": pct_1_8,
                "pct_nz_in_2m6": pct_2m6,
                "pct_all_bit_exact": pct_exact,
                "rel_l2_single_fp8": rel_s,
                "rel_l2_dual_fp8": rel_d,
                "dual_err_reduction": rel_s / rel_d,
                "pct_b2_pass": 100.0 * s["b2_pass"] / s["blocks"],
                "int8_pass": s["int8_pass"],
            }

    # 3. Recompute 32k WikiText-2-raw KL means and paired bootstrap CIs on CPU
    kl_sec = data["llm_kl_32k"]
    proto = kl_sec["protocol"]
    n_chunks = proto["n_chunks"]
    n_boot = proto["bootstrap_resamples"] if n_boot is None else n_boot
    seed = proto["bootstrap_seed"] if seed is None else seed
    resamples = draw_resamples(n_chunks, n_boot, seed)

    llm_summary = {}
    for mid, mdata in kl_sec["models"].items():
        msum = {"bf16_orig_ppl": mdata["bf16_orig_ppl"], "quant_modes": {}, "fp8_kv_qkt": {}}
        for w_tag, qm in mdata["quant_modes"].items():
            qout = {}
            for am in ("bf16", "single_fp8", "dual_fp8"):
                c_orig = qm[am]["per_chunk_kl_vs_orig"]
                c_wref = qm[am]["per_chunk_kl_vs_wref"]
                if len(c_orig) != n_chunks or len(c_wref) != n_chunks:
                    raise ValueError(f"chunk length mismatch for {mid}/{w_tag}/{am}")
                qout[am] = {
                    "ppl": qm[am]["ppl"],
                    "kl_vs_orig": boot_mean_ci(c_orig, resamples),
                    "kl_vs_wref": boot_mean_ci(c_wref, resamples),
                    "top1_vs_wref": qm[am]["top1_vs_w_dequant"],
                }
            qout["single_over_dual_ratio"] = boot_ratio_ci(
                qm["single_fp8"]["per_chunk_kl_vs_wref"],
                qm["dual_fp8"]["per_chunk_kl_vs_wref"],
                resamples,
            )
            msum["quant_modes"][w_tag] = qout

        kv = mdata["fp8_kv_qkt"]
        kv_out = {}
        for qm_name in ("bf16_q_fp8_k", "single_fp8_q_fp8_k", "dual_fp8_q_fp8_k"):
            c_k = kv[qm_name]["per_chunk_kl_vs_fp8k"]
            if len(c_k) != n_chunks:
                raise ValueError(f"chunk length mismatch for {mid}/fp8_kv/{qm_name}")
            kv_out[qm_name] = {
                "ppl": kv[qm_name]["ppl"],
                "kl_vs_fp8k_bf16q": boot_mean_ci(c_k, resamples),
                "top1_vs_fp8k_bf16q": kv[qm_name]["top1_vs_fp8k_bf16q"],
            }
        kv_out["single_over_dual_ratio"] = boot_ratio_ci(
            kv["single_fp8_q_fp8_k"]["per_chunk_kl_vs_fp8k"],
            kv["dual_fp8_q_fp8_k"]["per_chunk_kl_vs_fp8k"],
            resamples,
        )
        msum["fp8_kv_qkt"] = kv_out
        llm_summary[mid] = msum

    return {
        "gpu": data["gpu"],
        "sm_count": sms,
        "total_blocks_scanned": total_blocks,
        "total_elems_scanned": total_elems,
        "total_int8_exact_pass": total_int8_pass,
        "tma_ws_summary": tma_rows,
        "block_scan_summary": scan_summary,
        "llm_kl_summary": llm_summary,
        "n_chunks": n_chunks,
        "n_boot": n_boot,
        "seed": seed,
    }


def format_report(summary):
    lines = [
        f"GPU     : {summary['gpu']} ({summary['sm_count']} SMs)",
        f"Scanned : {summary['total_blocks_scanned']:,} blocks ({summary['total_elems_scanned']:,} BF16 elems) | Exact INT8 pass: {summary['total_int8_exact_pass']}",
        "",
        "TMA-WS Dual QMMA.SF Speedup vs cuBLAS BF16->FP32 (wall / iso-clk):",
    ]
    for r in summary["tma_ws_summary"]:
        lines.append(
            f"  {r['name']:<38s} | W8A16 pre={r['w8_pre_wall_x']:.2f}x/{r['w8_pre_iso_x']:.2f}x "
            f"2k={r['w8_2k_wall_x']:.2f}x/{r['w8_2k_iso_x']:.2f}x on={r['w8_on_wall_x']:.2f}x/{r['w8_on_iso_x']:.2f}x"
        )
        lines.append(
            f"  {'':<38s} | W4A16 pre={r['w4_pre_wall_x']:.2f}x/{r['w4_pre_iso_x']:.2f}x "
            f"2k={r['w4_2k_wall_x']:.2f}x/{r['w4_2k_iso_x']:.2f}x on={r['w4_on_wall_x']:.2f}x/{r['w4_on_iso_x']:.2f}x"
        )
    lines.append("")
    lines.append(f"32k WikiText-2-raw KL Verification (bootstrap={summary['n_boot']}, seed={summary['seed']}):")
    for mid, m in summary["llm_kl_summary"].items():
        lines.append(f"  Model: {mid} (BF16 orig PPL={m['bf16_orig_ppl']:.4f})")
        for w_tag, q in m["quant_modes"].items():
            s_kl = q["single_fp8"]["kl_vs_wref"][0]
            d_kl = q["dual_fp8"]["kl_vs_wref"][0]
            rat, lo, hi = q["single_over_dual_ratio"]
            lines.append(
                f"    {w_tag:<10s}: single_fp8 KL={s_kl:.3e} -> dual_fp8 KL={d_kl:.3e} | reduction={rat:,.1f}x [{lo:,.1f}x, {hi:,.1f}x]"
            )
        kv = m["fp8_kv_qkt"]
        s_kv = kv["single_fp8_q_fp8_k"]["kl_vs_fp8k_bf16q"][0]
        d_kv = kv["dual_fp8_q_fp8_k"]["kl_vs_fp8k_bf16q"][0]
        r_kv, lo_kv, hi_kv = kv["single_over_dual_ratio"]
        lines.append(
            f"    FP8-KV QKT: single_fp8 KL={s_kv:.3e} -> dual_fp8 KL={d_kv:.3e} | reduction={r_kv:,.1f}x [{lo_kv:,.1f}x, {hi_kv:,.1f}x]"
        )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archive", nargs="?", default=str(DEFAULT_ARCHIVE), help="Path to route2-dual-qmma-621.01.json")
    ap.add_argument("--boot", type=int, default=None, help="Override bootstrap resample count")
    ap.add_argument("--seed", type=int, default=None, help="Override RNG seed")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable summary JSON")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.archive).read_text(encoding="utf-8"))
    summary = verify_and_summarize(data, n_boot=args.boot, seed=args.seed)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_report(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
