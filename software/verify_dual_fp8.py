#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify archived Dual QMMA.SF benchmarks, real vLLM Marlin, decode/MLP sweeps, 517M scan & 32k KL."""

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


def _summarize_kl_group(group, modes, n_chunks, resamples, label):
    out = {}
    for am in modes:
        c_orig = group[am]["per_chunk_kl_vs_orig"]
        c_loc = group[am]["per_chunk_kl_vs_local"]
        c_hr = group[am]["per_chunk_hires_kl_vs_local"]
        if len(c_orig) != n_chunks or len(c_loc) != n_chunks or len(c_hr) != n_chunks:
            raise ValueError(f"chunk length mismatch for {label}/{am}")
        out[am] = {
            "ppl": group[am]["ppl"],
            "kl_vs_orig": boot_mean_ci(c_orig, resamples),
            "kl_vs_local": boot_mean_ci(c_loc, resamples),
            "hires_kl_vs_local": boot_mean_ci(c_hr, resamples),
            "top1_vs_local": group[am]["top1_vs_local_ref"],
        }
    b_key, s_key, d_key = modes[0], modes[1], modes[2]
    out["single_over_dual_ratio"] = boot_ratio_ci(
        group[s_key]["per_chunk_kl_vs_local"],
        group[d_key]["per_chunk_kl_vs_local"],
        resamples,
    )
    out["hires_single_over_dual_ratio"] = boot_ratio_ci(
        group[s_key]["per_chunk_hires_kl_vs_local"],
        group[d_key]["per_chunk_hires_kl_vs_local"],
        resamples,
    )
    base_o = out[b_key]["kl_vs_orig"][0]
    out["single_extra_kl_vs_orig_pct"] = 100.0 * (out[s_key]["kl_vs_orig"][0] - base_o) / max(base_o, 1e-30)
    out["dual_extra_kl_vs_orig_pct"] = 100.0 * (out[d_key]["kl_vs_orig"][0] - base_o) / max(base_o, 1e-30)
    if "w4a4_mxfp4" in group:
        w4a4 = group["w4a4_mxfp4"]
        if len(w4a4["per_chunk_kl_vs_orig"]) != n_chunks:
            raise ValueError(f"chunk length mismatch for {label}/w4a4_mxfp4")
        out["w4a4_mxfp4"] = {
            "ppl": w4a4["ppl"],
            "kl_vs_orig": boot_mean_ci(w4a4["per_chunk_kl_vs_orig"], resamples),
            "kl_vs_local": boot_mean_ci(w4a4["per_chunk_kl_vs_local"], resamples),
            "hires_kl_vs_local": boot_mean_ci(w4a4["per_chunk_hires_kl_vs_local"], resamples),
            "top1_vs_local": w4a4["top1_vs_local_ref"],
        }
        out["w4a4_extra_kl_vs_orig_pct"] = (
            100.0 * (out["w4a4_mxfp4"]["kl_vs_orig"][0] - base_o) / max(base_o, 1e-30)
        )
    return out


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

    # 2. Verify Prefill Frontier (W4A4, Single-FP8, Dual-FP8, Dequant-HMMA.F32, Real vLLM Marlin, Dequant-HMMA.F16)
    vllm_real = data["vllm_marlin_real"]
    audit = vllm_real["sm120_mma_audit"]
    if audit["sass_instruction"] != "HMMA.16816.F32.BF16" or audit["use_fp16_accum_on_sm120"]:
        raise ValueError("unexpected upstream vLLM Marlin SM120 audit record")
    vllm_pre_by_mn = {(r["M"], r["N"]): r for r in vllm_real["prefill"]}
    f16_by_mn = {(r["M"], r["N"]): r for r in data["dequant_hmma_f16"]["shapes"]}

    frontier_summary = []
    for sh in data["prefill_frontier"]:
        by_n = {v["name"]: v for v in sh["variants"]}
        for v in sh["variants"]:
            calc_fl = (v["tflops"] * 1e6) / (sms * v["sm_mhz"])
            if abs(calc_fl - v["eff_flps"]) > 1.0:
                raise ValueError(f"frontier eff_flps mismatch for {sh['shape']} / {v['name']}")
        m8 = by_n["Dequant-HMMA.F32 W8A16 (ref)"]
        m4 = by_n["Dequant-HMMA.F32 W4A16 (ref)"]
        d8 = by_n["Dual-FP8 W8A16 (pre-split)"]
        d4 = by_n["Dual-FP8 W4A16 (pre-split)"]
        vr = vllm_pre_by_mn[(sh["M"], sh["N"])]
        for tag in ("mxfp8_auto", "mxfp4_auto"):
            calc_fl = (vr[tag]["tflops"] * 1e6) / (sms * vr[tag]["sm_mhz"])
            if abs(calc_fl - vr[tag]["eff_flps"]) > 1.0:
                raise ValueError(f"vllm_marlin_real eff_flps mismatch for {sh['shape']} / {tag}")
        hf = f16_by_mn[(sh["M"], sh["N"])]
        for tag in ("w8", "w4"):
            calc_fl = (hf[tag]["tflops"] * 1e6) / (sms * hf[tag]["sm_mhz"])
            if abs(calc_fl - hf[tag]["eff_flps"]) > 1.0:
                raise ValueError(f"dequant_hmma_f16 eff_flps mismatch for {sh['shape']} / {tag}")
        frontier_summary.append({
            "shape": sh["shape"],
            "w4a4_tflops": by_n["W4A4 (OMMA.SF.16864)"]["tflops"],
            "w8a16_dual_vs_dequant_wall": d8["tflops"] / m8["tflops"],
            "w8a16_dual_vs_dequant_iso": d8["eff_flps"] / m8["eff_flps"],
            "w4a16_dual_vs_dequant_wall": d4["tflops"] / m4["tflops"],
            "w4a16_dual_vs_dequant_iso": d4["eff_flps"] / m4["eff_flps"],
            "w8a16_dual_vs_vllm_marlin_wall": d8["tflops"] / vr["mxfp8_auto"]["tflops"],
            "w4a16_dual_vs_vllm_marlin_wall": d4["tflops"] / vr["mxfp4_auto"]["tflops"],
            "dequant_f16_w8_tflops": hf["w8"]["tflops"],
            "dequant_f16_w4_tflops": hf["w4"]["tflops"],
            "dequant_f16_rel_l2": hf["w8"]["rel_l2_vs_f32acc"],
        })

    # 3. Verify Cold-L2 Decode Sweep (M=1..64) + Real vLLM Marlin + Steady Vec128 + Raw BW Roof
    dec_sec = data["decode_cold_l2_sweep"]
    ceil_w8 = dec_sec["theoretical_weight_bytes_per_elem"]["w8_ceiling_vs_bf16"]
    ceil_w4 = dec_sec["theoretical_weight_bytes_per_elem"]["w4_ceiling_vs_bf16"]
    vllm_dec_by_m = {r["M"]: r for r in vllm_real["decode_cold_l2"]}
    vec128_by_m = {r["M"]: r for r in dec_sec["steady_vec128_sweep"]["batch_sweeps"]}

    decode_summary = []
    for sw in dec_sec["batch_sweeps"]:
        m_val = sw["M"]
        by_l = {r["label"]: r for r in sw["results"]}
        bf = by_l["cuBLAS BF16->FP32"]["us"]
        m8 = by_l["Dequant-HMMA.F32 W8A16 (ref)"]["us"]
        d8 = by_l["Dual-FP8 W8A16 (pre-split)"]["us"]
        m4 = by_l["Dequant-HMMA.F32 W4A16 (ref)"]["us"]
        d4 = by_l["Dual-FP8 W4A16 (pre-split)"]["us"]
        vr = vllm_dec_by_m[m_val]
        v128 = {r["label"]: r for r in vec128_by_m[m_val]["results"]}
        decode_summary.append({
            "M": m_val,
            "w8_vs_cublas": bf / d8,
            "w8_pct_of_ceil": 100.0 * (bf / d8) / ceil_w8,
            "w8_vs_dequant": m8 / d8,
            "w8_vs_vllm_marlin": vr["mxfp8"]["us"] / d8,
            "w4_vs_cublas": bf / d4,
            "w4_pct_of_ceil": 100.0 * (bf / d4) / ceil_w4,
            "w4_vs_dequant": m4 / d4,
            "w4_vs_vllm_marlin": vr["mxfp4"]["us"] / d4,
            "vllm_marlin_w4_pct_of_ceil": 100.0 * (bf / vr["mxfp4"]["us"]) / ceil_w4,
            "steady_w8_tput_dual_over_single": v128["Single-FP8 W8"]["us"] / v128["Dual-FP8 W8 (pre-split)"]["us"],
            "steady_w4_tput_dual_over_single": v128["Single-FP8 W4"]["us"] / v128["Dual-FP8 W4 (pre-split)"]["us"],
        })

    # 4. Verify Fused MLP End-to-End (both original and Same-Weight W8/W4 comparison)
    mlp_summary = []
    for entry in data["fused_mlp_e2e"]:
        by_p = {p["name"]: p for p in entry["pipelines"]}
        bf = by_p["BF16 Baseline (cuBLAS+elem)"]
        w8f = by_p["Dual-FP8 W8A16 (Fused Epilogue)"]
        w4f = by_p["Dual-FP8 W4A16 (Fused Epilogue)"]
        ew = entry["elementwise_us"]
        mlp_summary.append({
            "M": entry["M"],
            "rmsnorm_delta_us": ew["rmsnorm_fused_split"] - ew["rmsnorm_bf16"],
            "swiglu_delta_us": ew["swiglu_fused_split"] - ew["swiglu_bf16"],
            "w8_fused_wall_x": bf["ms"] / w8f["ms"],
            "w8_fused_iso_x": w8f["eff_flps"] / bf["eff_flps"],
            "w4_fused_wall_x": bf["ms"] / w4f["ms"],
            "w4_fused_iso_x": w4f["eff_flps"] / bf["eff_flps"],
        })

    same_weight_mlp_summary = []
    for run in data["same_weight_mlp_e2e"]["runs"]:
        by_p = {p["name"]: p for p in run["pipelines"]}
        bf = by_p["cuBLAS BF16 + standalone elem"]
        v8 = by_p["Real vLLM Marlin MXFP8 + elem"]
        q8 = by_p["Dequant-HMMA.F32 W8 + elem"]
        d8 = by_p["Dual-FP8 W8A16 (Fused RMS+SwiGLU)"]
        s8 = by_p["Single-FP8 W8A8 (Fused RMS+SwiGLU)"]
        v4 = by_p["Real vLLM Marlin MXFP4 + elem"]
        q4 = by_p["Dequant-HMMA.F32 W4 + elem"]
        d4 = by_p["Dual-FP8 W4A16 (Fused RMS+SwiGLU)"]
        s4 = by_p["Single-FP8 W4A8 (Fused RMS+SwiGLU)"]
        a4 = by_p["W4A4 OMMA.SF (Fused RMS+SwiGLU)"]
        same_weight_mlp_summary.append({
            "M": run["M"],
            "w8_dual_vs_vllm_marlin": v8["ms"] / d8["ms"],
            "w8_dual_vs_dequant": q8["ms"] / d8["ms"],
            "w8_single_vs_dual": d8["ms"] / s8["ms"],
            "w4_dual_vs_vllm_marlin": v4["ms"] / d4["ms"],
            "w4_dual_vs_dequant": q4["ms"] / d4["ms"],
            "w4_single_vs_dual": d4["ms"] / s4["ms"],
            "w4_w4a4_vs_bf16": bf["ms"] / a4["ms"],
        })

    # 5. Verify 517M-block scan totals and percentages
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

    # 6. Recompute 32k WikiText-2-raw KL means & paired bootstrap CIs (Deploy + High-Res, incl. W4A4 & Frozen K)
    kl_sec = data["llm_kl_32k"]
    proto = kl_sec["protocol"]
    n_chunks = proto["n_chunks"]
    n_boot = proto["bootstrap_resamples"] if n_boot is None else n_boot
    seed = proto["bootstrap_seed"] if seed is None else seed
    resamples = draw_resamples(n_chunks, n_boot, seed)

    llm_summary = {}
    kv_modes = ("bf16_q_fp8_k", "single_fp8_q_fp8_k", "dual_fp8_q_fp8_k")
    for mid, mdata in kl_sec["models"].items():
        msum = {"bf16_orig_ppl": mdata["bf16_orig_ppl"], "quant_modes": {}}
        for w_tag, qm in mdata["quant_modes"].items():
            msum["quant_modes"][w_tag] = _summarize_kl_group(
                qm, ("bf16", "single_fp8", "dual_fp8"), n_chunks, resamples, f"{mid}/{w_tag}"
            )
        for kv_key in (
            "fp8_kv_qkt_smoothed",
            "fp8_kv_qkt_prefill256_frozen_full",
            "fp8_kv_qkt_prefill256_frozen_decode_suffix",
            "fp8_kv_qkt_offline_calib_static",
        ):
            msum[kv_key] = _summarize_kl_group(
                mdata[kv_key], kv_modes, n_chunks, resamples, f"{mid}/{kv_key}"
            )
        llm_summary[mid] = msum

    return {
        "gpu": data["gpu"],
        "sm_count": sms,
        "total_blocks_scanned": total_blocks,
        "total_elems_scanned": total_elems,
        "total_int8_exact_pass": total_int8_pass,
        "tma_ws_summary": tma_rows,
        "prefill_frontier_summary": frontier_summary,
        "decode_cold_l2_summary": decode_summary,
        "fused_mlp_summary": mlp_summary,
        "same_weight_mlp_summary": same_weight_mlp_summary,
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
        "Prefill Frontier (Dual-FP8 vs Dequant-HMMA.F32 / Real vLLM Marlin, Dequant-HMMA.F16, W4A4):",
    ]
    for r in summary["prefill_frontier_summary"]:
        lines.append(
            f"  {r['shape']:<32s} | W8A16 vs Deq/vLLM={r['w8a16_dual_vs_dequant_wall']:.2f}x/{r['w8a16_dual_vs_vllm_marlin_wall']:.2f}x "
            f"| W4A16 vs Deq/vLLM={r['w4a16_dual_vs_dequant_wall']:.2f}x/{r['w4a16_dual_vs_vllm_marlin_wall']:.2f}x "
            f"| DeqF16={r['dequant_f16_w8_tflops']:.1f}/{r['dequant_f16_w4_tflops']:.1f} TF/s (L2={r['dequant_f16_rel_l2']:.2e}) "
            f"| W4A4={r['w4a4_tflops']:.1f} TF/s"
        )
    lines.append("")
    lines.append("Cold-L2 Decode Sweep (Llama8B Up 14336x4096; steady throughput ratio = us_Single / us_Dual):")
    for r in summary["decode_cold_l2_summary"]:
        lines.append(
            f"  M={r['M']:2d} | W8 vs BF16={r['w8_vs_cublas']:.2f}x ({r['w8_pct_of_ceil']:.0f}% ceil) vs vLLM={r['w8_vs_vllm_marlin']:.2f}x (tput Dual/Single={r['steady_w8_tput_dual_over_single']:.2f}x) "
            f"| W4 vs BF16={r['w4_vs_cublas']:.2f}x ({r['w4_pct_of_ceil']:.0f}% ceil) vs vLLM={r['w4_vs_vllm_marlin']:.2f}x (tput Dual/Single={r['steady_w4_tput_dual_over_single']:.2f}x)"
        )
    lines.append("")
    lines.append("Same-Weight Llama-3-8B MLP End-to-End (RMSNorm + gate/up + SwiGLU + down):")
    for r in summary["same_weight_mlp_summary"]:
        lines.append(
            f"  M={r['M']:4d} | W8 Dual vs vLLM={r['w8_dual_vs_vllm_marlin']:.2f}x vs Deq={r['w8_dual_vs_dequant']:.2f}x (Single/Dual={r['w8_single_vs_dual']:.2f}x) "
            f"| W4 Dual vs vLLM={r['w4_dual_vs_vllm_marlin']:.2f}x vs Deq={r['w4_dual_vs_dequant']:.2f}x (Single/Dual={r['w4_single_vs_dual']:.2f}x, W4A4/BF16={r['w4_w4a4_vs_bf16']:.2f}x)"
        )
    lines.append("")
    lines.append(f"32k WikiText-2-raw KL Verification (bootstrap={summary['n_boot']}, seed={summary['seed']}):")
    for mid, m in summary["llm_kl_summary"].items():
        lines.append(f"  Model: {mid} (BF16 orig PPL={m['bf16_orig_ppl']:.4f})")
        for w_tag, q in m["quant_modes"].items():
            rat, lo, hi = q["single_over_dual_ratio"]
            hr_rat = q["hires_single_over_dual_ratio"][0]
            extra_w4a4 = ""
            if "w4a4_mxfp4" in q:
                extra_w4a4 = f" | W4A4 PPL={q['w4a4_mxfp4']['ppl']:.2f} ({q['w4a4_extra_kl_vs_orig_pct']:+.1f}% vs orig)"
            lines.append(
                f"    {w_tag:<16s}: Single/Dual={rat:5.1f}x [{lo:4.1f}x, {hi:4.1f}x] (HiRes {hr_rat:4.1f}x) "
                f"| extra KL vs orig: Single={q['single_extra_kl_vs_orig_pct']:+5.1f}%, Dual={q['dual_extra_kl_vs_orig_pct']:+.2f}%{extra_w4a4}"
            )
        for kv_tag, kv_key in (
            ("FP8-KV-FullMean", "fp8_kv_qkt_smoothed"),
            ("FP8-KV-Pre256", "fp8_kv_qkt_prefill256_frozen_full"),
            ("FP8-KV-Dec256+", "fp8_kv_qkt_prefill256_frozen_decode_suffix"),
            ("FP8-KV-OffCalib", "fp8_kv_qkt_offline_calib_static"),
        ):
            kv = m[kv_key]
            r_kv, lo_kv, hi_kv = kv["single_over_dual_ratio"]
            hr_kv = kv["hires_single_over_dual_ratio"][0]
            lines.append(
                f"    {kv_tag:<16s}: Single/Dual={r_kv:5.1f}x [{lo_kv:4.1f}x, {hi_kv:4.1f}x] (HiRes {hr_kv:4.1f}x) "
                f"| BF16-Q KL_o={kv['bf16_q_fp8_k']['kl_vs_orig'][0]:.3e} | Single={kv['single_extra_kl_vs_orig_pct']:+5.1f}%, Dual={kv['dual_extra_kl_vs_orig_pct']:+.2f}%"
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
