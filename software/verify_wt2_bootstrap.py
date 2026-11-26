#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Recompute Route 2 WikiText-2-raw means, delta-PPL and paired bootstrap CIs without a GPU."""

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
    / "route2-wt2-288k-fp64.json"
)


def percentile(sorted_vals, pct):
    pos = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def mean(vals):
    return sum(vals) / len(vals)


def draw_resamples(n, n_boot, seed):
    """Pre-sample chunk multiplicities once so paired comparisons share resamples."""
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
        draws.append(sn / sd)
    draws.sort()
    return sum(num_vals) / sum(den_vals), percentile(draws, 2.5), percentile(draws, 97.5)


def compute_summary(data, n_boot=None, seed=None):
    proto = data["protocol"]
    n_chunks = proto["n_chunks"]
    n_boot = proto["bootstrap_resamples"] if n_boot is None else n_boot
    seed = proto["bootstrap_seed"] if seed is None else seed
    base_key = proto["baseline_key"]

    ref = data["reference"]
    if len(ref["nll_high_res"]) != n_chunks or len(ref["nll_deploy"]) != n_chunks:
        raise ValueError("reference NLL length does not match n_chunks")

    by_key = {}
    for cfg in data["configs"]:
        for field in ("kl_deploy", "kl_high_res", "top1_high_res", "nll_high_res"):
            if len(cfg[field]) != n_chunks:
                raise ValueError(f"{cfg['key']}.{field} length does not match n_chunks")
        by_key[cfg["key"]] = cfg

    base = by_key[base_key]
    resamples = draw_resamples(n_chunks, n_boot, seed)

    ref_ppl_hi = math.exp(mean(ref["nll_high_res"]))
    ref_ppl_dep = math.exp(mean(ref["nll_deploy"]))

    rows = []
    for cfg in data["configs"]:
        ex_dep = [a - b for a, b in zip(cfg["kl_deploy"], base["kl_deploy"])]
        ex_hi = [a - b for a, b in zip(cfg["kl_high_res"], base["kl_high_res"])]
        dep_m, dep_lo, dep_hi = boot_mean_ci(ex_dep, resamples)
        hi_m, hi_lo, hi_hi = boot_mean_ci(ex_hi, resamples)
        ppl_hi = math.exp(mean(cfg["nll_high_res"]))
        rows.append({
            "key": cfg["key"],
            "label": cfg["label"],
            "kl_deploy": mean(cfg["kl_deploy"]),
            "excess_deploy": (dep_m, dep_lo, dep_hi),
            "kl_high_res": mean(cfg["kl_high_res"]),
            "excess_high_res": (hi_m, hi_lo, hi_hi),
            "top1_high_res": mean(cfg["top1_high_res"]),
            "ppl_high_res": ppl_hi,
            "delta_ppl_high_res": ppl_hi - ref_ppl_hi,
        })

    def excess_of(key, field):
        return [a - b for a, b in zip(by_key[key][field], base[field])]

    comp_dep = boot_ratio_ci(
        excess_of("pure_b1_all", "kl_deploy"),
        excess_of("c1b1_kp_act_all", "kl_deploy"),
        resamples,
    )
    comp_hi = boot_ratio_ci(
        excess_of("pure_b1_all", "kl_high_res"),
        excess_of("c1b1_kp_act_all", "kl_high_res"),
        resamples,
    )
    lm_ablation_dep = boot_mean_ci(
        [a - b for a, b in zip(by_key["pure_b1_all"]["kl_deploy"], by_key["pure_b1_keep"]["kl_deploy"])],
        resamples,
    )
    lm_ablation_hi = boot_mean_ci(
        [a - b for a, b in zip(by_key["pure_b1_all"]["kl_high_res"], by_key["pure_b1_keep"]["kl_high_res"])],
        resamples,
    )
    regime_diff = boot_mean_ci(
        [a - b for a, b in zip(base["kl_deploy"], base["kl_high_res"])],
        resamples,
    )

    return {
        "model_id": data["model_id"],
        "model_revision": data["model_revision"],
        "n_chunks": n_chunks,
        "total_tokens": proto["total_tokens"],
        "n_boot": n_boot,
        "seed": seed,
        "ref_ppl_high_res": ref_ppl_hi,
        "ref_ppl_deploy": ref_ppl_dep,
        "rows": rows,
        "mode0_deploy_minus_high_res": regime_diff,
        "compression_pure_b1_all_over_c1b1_kp_all": {
            "deploy": comp_dep,
            "high_res": comp_hi,
        },
        "lm_head_ablation_pure_b1_all_minus_keep": {
            "deploy": lm_ablation_dep,
            "high_res": lm_ablation_hi,
        },
    }


def format_report(summary):
    lines = [
        f"Model   : {summary['model_id']} (rev {summary['model_revision']})",
        f"Chunks  : {summary['n_chunks']} ({summary['total_tokens']} tokens) | bootstrap={summary['n_boot']} seed={summary['seed']}",
        f"Ref PPL : High-Res={summary['ref_ppl_high_res']:.5f}  Deploy={summary['ref_ppl_deploy']:.5f}",
        "",
        f"{'Configuration':<36s} | {'Deploy KL':>9s} | {'Deploy Excess [95% CI]':>29s} | {'High-Res KL':>11s} | {'High-Res Excess [95% CI]':>29s} | {'Top-1':>6s}",
        "-" * 134,
    ]
    for r in summary["rows"]:
        dm, dlo, dhi = r["excess_deploy"]
        hm, hlo, hhi = r["excess_high_res"]
        lines.append(
            f"{r['label']:<36s} | {r['kl_deploy']:9.2e} | {dm:+9.2e} [{dlo:+8.2e}, {dhi:+8.2e}] | "
            f"{r['kl_high_res']:11.2e} | {hm:+9.2e} [{hlo:+8.2e}, {hhi:+8.2e}] | {r['top1_high_res']:5.2f}%"
        )
    cd = summary["compression_pure_b1_all_over_c1b1_kp_all"]["deploy"]
    ch = summary["compression_pure_b1_all_over_c1b1_kp_all"]["high_res"]
    ld = summary["lm_head_ablation_pure_b1_all_minus_keep"]["deploy"]
    lh = summary["lm_head_ablation_pure_b1_all_minus_keep"]["high_res"]
    rd = summary["mode0_deploy_minus_high_res"]
    lines.extend([
        "",
        f"Mode0 Deploy - High-Res diff   : {rd[0]:.3e} [{rd[1]:.3e}, {rd[2]:.3e}]",
        f"Compression PureB1[ALL]/C1+B1+Kp[ALL] High-Res : {ch[0]:.2f}x [{ch[1]:.2f}x, {ch[2]:.2f}x]",
        f"Compression PureB1[ALL]/C1+B1+Kp[ALL] Deploy   : {cd[0]:.2f}x [{cd[1]:.2f}x, {cd[2]:.2f}x]",
        f"lm_head ablation PureB1 ALL-keep High-Res      : {lh[0]:+.2e} [{lh[1]:+.2e}, {lh[2]:+.2e}]",
        f"lm_head ablation PureB1 ALL-keep Deploy        : {ld[0]:+.2e} [{ld[1]:+.2e}, {ld[2]:+.2e}]",
    ])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("archive", nargs="?", default=str(DEFAULT_ARCHIVE), help="Path to route2-wt2-288k-fp64.json")
    ap.add_argument("--boot", type=int, default=None, help="Override bootstrap resample count")
    ap.add_argument("--seed", type=int, default=None, help="Override RNG seed")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable summary JSON")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.archive).read_text(encoding="utf-8"))
    summary = compute_summary(data, n_boot=args.boot, seed=args.seed)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_report(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
