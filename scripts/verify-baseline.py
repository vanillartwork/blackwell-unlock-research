#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Cross-platform Blackwell (GB20x) read-only baseline verifier.

Collects non-sensitive GPU metadata via nvidia-smi (driver, VBIOS, PCI device/
subsystem IDs, VRAM, BAR1, PCIe link, power ceiling), checks whether the card
falls into known Blackwell consumer/workstation ranges, and optionally runs
software/clamp_eval.py to attach a steady-state FLOP/clk/SM baseline.

With --compare, the measured clamp ratios are checked against an archived
clamp-eval (or baseline) JSON, so a driver update that changes the clamp shows up
as a non-zero exit code.

Strictly read-only: no kernel driver load, no BAR0 writes, no serial/UUID output.

Usage:
  python scripts/verify-baseline.py --json baseline.json
  python scripts/verify-baseline.py --bench --compare reports/gb205-2f18/clamp-eval-621.01-n8192.json
  python scripts/verify-baseline.py --clamp-json mine.json --compare archived.json
"""

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Known Blackwell GB20x PCI device ids, names per the public pci.ids database
# (non-exhaustive). Unknown 0x2b/0x2c/0x2d/0x2f ids are still flagged as candidate
# Blackwell for confirmation via PMC_BOOT_0 / CC 12.0.
KNOWN_BLACKWELL_DEVIDS = {
    0x2B85: ("GB202", "RTX 5090 (desktop)"),
    0x2B87: ("GB202", "RTX 5090 D"),
    0x2B8C: ("GB202", "RTX 5090 D V2"),
    0x2BB1: ("GB202", "RTX PRO 6000 Blackwell Workstation"),
    0x2BB2: ("GB202", "RTX PRO 6000D Blackwell Workstation"),
    0x2BB3: ("GB202", "RTX PRO 5000 Blackwell"),
    0x2BB4: ("GB202", "RTX PRO 6000 Blackwell Max-Q"),
    0x2BB5: ("GB202", "RTX PRO 6000 Blackwell Server"),
    0x2BBD: ("GB202", "RTX PRO 5500 Blackwell Workstation"),
    0x2C02: ("GB203", "RTX 5080 (desktop)"),
    0x2C05: ("GB203", "RTX 5070 Ti (desktop)"),
    0x2C09: ("GB203", "RTX 5070 (GB203 variant)"),
    0x2C18: ("GB203", "RTX 5090 Laptop"),
    0x2C19: ("GB203", "RTX 5080 Laptop"),
    0x2C31: ("GB203", "RTX PRO 4500 Blackwell"),
    0x2C33: ("GB203", "RTX PRO 4000 Blackwell SFF"),
    0x2C34: ("GB203", "RTX PRO 4000 Blackwell"),
    0x2C38: ("GB203", "RTX PRO 5000 Blackwell Laptop"),
    0x2C39: ("GB203", "RTX PRO 4000 Blackwell Laptop"),
    0x2C58: ("GB203", "RTX 5090 Laptop"),
    0x2C59: ("GB203", "RTX 5080 Laptop"),
    0x2D04: ("GB206", "RTX 5060 Ti (desktop)"),
    0x2D05: ("GB206", "RTX 5060 (desktop)"),
    0x2D18: ("GB206", "RTX 5070 Laptop"),
    0x2D19: ("GB206", "RTX 5060 Laptop"),
    0x2D30: ("GB206", "RTX PRO 2000 Blackwell"),
    0x2D39: ("GB206", "RTX PRO 2000 Blackwell Laptop"),
    0x2D58: ("GB206", "RTX 5070 Laptop"),
    0x2D59: ("GB206", "RTX 5060 Laptop"),
    0x2D83: ("GB207", "RTX 5050 (desktop)"),
    0x2D98: ("GB207", "RTX 5050 Laptop"),
    0x2DB8: ("GB207", "RTX PRO 1000 Blackwell Laptop"),
    0x2DB9: ("GB207", "RTX PRO 500 Blackwell Laptop"),
    0x2DD8: ("GB207", "RTX 5050 Laptop"),
    0x2F02: ("GB205", "RTX 5070 SUPER"),
    0x2F04: ("GB205", "RTX 5070 (desktop)"),
    0x2F06: ("GB205", "RTX 5060 (GB205 variant)"),
    0x2F18: ("GB205", "RTX 5070 Ti Laptop"),
    0x2F38: ("GB205", "RTX PRO 3000 Blackwell Laptop"),
    0x2F58: ("GB205", "RTX 5070 Ti Laptop"),
}

# Cases whose clamp ratio is compared. int8 and fp32_control carry no clamp verdict.
COMPARE_CASES = ("fp16_fp16acc", "fp16_fp32acc", "bf16_fp32acc", "tf32_fp32acc", "fp8_fp32acc")


def parse_pci_devid(raw):
    """Extract 16-bit PCI device ID from nvidia-smi format like '0x2F1810DE'."""
    if not raw:
        return None
    s = raw.strip().lower().removeprefix("0x")
    try:
        val = int(s, 16)
    except ValueError:
        return None
    if len(s) == 8 and (val & 0xFFFF) == 0x10DE:
        return (val >> 16) & 0xFFFF
    if len(s) <= 4:
        return val & 0xFFFF
    return None


def classify_blackwell_devid(devid):
    if devid is None:
        return {"known_die": None, "label": "unknown", "blackwell_candidate": False}
    if devid in KNOWN_BLACKWELL_DEVIDS:
        die, label = KNOWN_BLACKWELL_DEVIDS[devid]
        return {"known_die": die, "label": label, "blackwell_candidate": True}
    hi = (devid >> 8) & 0xFF
    candidate = hi in (0x2B, 0x2C, 0x2D, 0x2F)
    return {
        "known_die": None,
        "label": "candidate GB20x (confirm via PMC_BOOT_0 / CC 12.0)" if candidate else "non-GB20x or unmapped",
        "blackwell_candidate": candidate,
    }


def parse_bar1_mib(text):
    """Extract the BAR1 aperture size (MiB) from `nvidia-smi -q -d MEMORY` output."""
    m = re.search(r"BAR1 Memory Usage\s*\n\s*Total\s*:\s*(\d+)\s*MiB", text or "")
    return int(m.group(1)) if m else None


def query_bar1_mib(index):
    """BAR1 has no --query-gpu field, so read it from the memory report instead."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "-i", str(index), "-q", "-d", "MEMORY"],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_bar1_mib(out)


def query_nvidia_smi_gpus():
    fields = [
        "index",
        "name",
        "pci.device_id",
        "pci.sub_device_id",
        "driver_version",
        "vbios_version",
        "memory.total",
        "pcie.link.gen.current",
        "pcie.link.gen.max",
        "pcie.link.width.current",
        "pcie.link.width.max",
        "power.default_limit",
        "power.max_limit",
    ]
    try:
        out = subprocess.check_output(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader"],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)

    gpus = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        devid = parse_pci_devid(row["pci.device_id"])
        row["pci_devid_hex"] = f"10de:{devid:04x}" if devid is not None else "unknown"
        row["classification"] = classify_blackwell_devid(devid)
        row["bar1_total_mib"] = query_bar1_mib(row["index"])
        gpus.append(row)
    return gpus, None


def run_clamp_eval(device, n, min_seconds):
    eval_script = Path(__file__).resolve().parents[1] / "software" / "clamp_eval.py"
    with tempfile.TemporaryDirectory(prefix="blackwell-clamp-") as tmp:
        json_out = Path(tmp) / "clamp-eval.json"
        cmd = [
            sys.executable,
            "-B",
            str(eval_script),
            "--device",
            str(device),
            "--n",
            str(n),
            "--min-seconds",
            str(min_seconds),
            "--json",
            str(json_out),
        ]
        # Keep the child's table in order with our own output, and drop its "wrote ..."
        # line: that file lives in a temporary directory removed below.
        sys.stdout.flush()
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace") as proc:
            for line in proc.stdout:
                if not line.startswith("wrote "):
                    print(line, end="", flush=True)
        if proc.returncode != 0 or not json_out.exists():
            return None, f"clamp_eval exited with code {proc.returncode}"
        return json.loads(json_out.read_text(encoding="utf-8")), None


def load_clamp_report(path):
    """Load a clamp-eval JSON, or the clamp_eval section of a baseline JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "results" not in data and isinstance(data.get("clamp_eval"), dict):
        data = data["clamp_eval"]
    if "results" not in data:
        raise ValueError(f"{path} holds no clamp-eval results")
    return data


def compare_clamp_reports(current, reference, tolerance):
    """Compare per-case ratio_of_full values between two clamp-eval reports.

    A case drifts when the absolute difference exceeds `tolerance`. Cases missing on
    either side are reported but do not count as drift, because PyTorch builds differ
    in what they support.
    """
    cur = {r["case"]: r for r in current["results"]}
    ref = {r["case"]: r for r in reference["results"]}
    rows = []
    for case in COMPARE_CASES:
        a = (cur.get(case) or {}).get("ratio_of_full")
        b = (ref.get(case) or {}).get("ratio_of_full")
        delta = a - b if (a is not None and b is not None) else None
        if delta is None:
            status = "missing"
        elif abs(delta) <= tolerance:
            status = "match"
        else:
            status = "drift"
        rows.append({"case": case, "current": a, "reference": b, "delta": delta, "status": status})

    context = {}
    for key in ("gpu", "pci_device_id", "driver", "vbios", "torch", "sm_count"):
        a, b = current.get("info", {}).get(key), reference.get("info", {}).get(key)
        if a != b:
            context[key] = {"current": a, "reference": b}
    n_cur, n_ref = current.get("config", {}).get("n"), reference.get("config", {}).get("n")
    if n_cur != n_ref:
        context["n"] = {"current": n_cur, "reference": n_ref}
    return {
        "tolerance": tolerance,
        "rows": rows,
        "context_differences": context,
        "drifted": [r["case"] for r in rows if r["status"] == "drift"],
        "compared": sum(r["status"] != "missing" for r in rows),
    }


def print_comparison(cmp):
    print(f"\n--- Clamp ratio vs archived reference (tolerance +/-{cmp['tolerance']:.3f}) ---")
    for r in cmp["rows"]:
        a = f"{r['current']:.3f}" if r["current"] is not None else "n/a"
        b = f"{r['reference']:.3f}" if r["reference"] is not None else "n/a"
        d = f"{r['delta']:+.3f}" if r["delta"] is not None else "n/a"
        print(f"  {r['case']:14s} now={a:>6} ref={b:>6} delta={d:>7}  {r['status']}")
    for key, v in cmp["context_differences"].items():
        print(f"  note: {key} differs (now {v['current']}, reference {v['reference']})")
    if cmp["compared"] == 0:
        print("  no comparable case: nothing was verified")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", type=int, default=0, help="CUDA device index for --bench (default: 0)")
    ap.add_argument("--bench", action="store_true", help="also run software/clamp_eval.py steady-state clamp check")
    ap.add_argument("--n", type=int, default=8192, help="matrix size N when --bench is enabled (default: 8192)")
    ap.add_argument("--min-seconds", type=float, default=2.0, help="steady-state window per case for --bench (default: 2.0)")
    ap.add_argument("--clamp-json", type=Path, default=None, help="use an existing clamp-eval JSON instead of running --bench")
    ap.add_argument("--compare", type=Path, default=None, help="archived clamp-eval/baseline JSON to check against (needs --bench or --clamp-json)")
    ap.add_argument("--tolerance", type=float, default=0.06, help="allowed absolute drift of ratio_of_full for --compare (default: 0.06)")
    ap.add_argument("--json", type=Path, default=None, help="write baseline JSON summary to path")
    args = ap.parse_args()

    if args.bench and args.clamp_json:
        ap.error("--bench and --clamp-json are mutually exclusive")
    if args.compare and not (args.bench or args.clamp_json):
        ap.error("--compare needs --bench or --clamp-json")
    if args.tolerance < 0:
        ap.error("--tolerance must not be negative")

    reference = None
    if args.compare:
        try:
            reference = load_clamp_report(args.compare)
        except (OSError, ValueError) as exc:
            ap.error(f"--compare: {exc}")

    gpus, err = query_nvidia_smi_gpus()
    report = {
        "schema": "blackwell-abi-lab/baseline/1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "os": platform.system(),
        "os_release": platform.release(),
        "python": platform.python_version(),
        "gpus": gpus or [],
        "nvidia_smi_error": err,
        "clamp_eval": None,
        "comparison": None,
    }

    if err:
        print(f"nvidia-smi unavailable: {err}", file=sys.stderr)
    else:
        print(f"Discovered {len(gpus)} NVIDIA GPU(s) on {report['os']} {report['os_release']}:")
        for g in gpus:
            cls = g["classification"]
            die_s = cls["known_die"] or "unmapped"
            bar1_s = f"{g['bar1_total_mib']} MiB" if g["bar1_total_mib"] is not None else "n/a"
            print(
                f"  GPU {g['index']}: {g['name']} | {g['pci_devid_hex']} ({die_s}: {cls['label']}) | "
                f"driver {g['driver_version']} | VBIOS {g['vbios_version']} | "
                f"VRAM {g['memory.total']} | BAR1 {bar1_s} | "
                f"PCIe Gen{g['pcie.link.gen.current']}x{g['pcie.link.width.current']} "
                f"(max Gen{g['pcie.link.gen.max']}x{g['pcie.link.width.max']}) | "
                f"Pmax {g['power.max_limit']}"
            )

    if args.bench:
        bench_data, b_err = run_clamp_eval(args.device, args.n, args.min_seconds)
        if b_err:
            print(f"clamp_eval failed: {b_err}", file=sys.stderr)
            return 1
        report["clamp_eval"] = bench_data
    elif args.clamp_json:
        try:
            report["clamp_eval"] = load_clamp_report(args.clamp_json)
        except (OSError, ValueError) as exc:
            ap.error(f"--clamp-json: {exc}")

    exit_code = 0
    if reference is not None:
        report["comparison"] = compare_clamp_reports(report["clamp_eval"], reference, args.tolerance)
        print_comparison(report["comparison"])
        if report["comparison"]["drifted"] or report["comparison"]["compared"] == 0:
            exit_code = 3

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_bytes((json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        print(f"wrote {args.json}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
