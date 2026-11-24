#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Read-only collector for Blackwell (GB20x) Linux boot/GSP/FMC diagnostics.
# Captures sanitized kernel/PCI/driver metadata and filtered GSP/FMC dmesg lines.
# Deliberately excludes GPU serials, UUIDs, disk/partition inventories, and firmware bytes.

set -euo pipefail

OUT_DIR=""
MAKE_TAR=0
# Everything this script writes. The archive is built from this list, never from the
# directory, so files left there by an earlier run or by hand cannot leak into it.
REPORT_FILES=(summary.txt nvidia-smi.txt pci-topology.txt dmesg-gsp-fmc.txt)

usage() {
  cat <<'EOF'
Usage: collect-report.sh [--out <dir>] [--tar]
       collect-report.sh --sanitize < raw.log > clean.log

Read-only collector for Blackwell ABI & cross-die observation reports.
  --out <dir>   Output directory (default: ./blackwell-report-<UTC-timestamp>)
  --tar         Also create a <dir>.tar.gz archive of the four files written here
  --sanitize    Only filter stdin to stdout with the same redaction rules
                (for netconsole/serial logs captured on another machine)
  -h, --help    Show this message
EOF
}

# Kernel command lines carry disk and volume names, crypt devices and netconsole endpoints,
# so only the parameters that matter for an ABI report are kept and the rest are counted.
# The kernel and the initramfs print the command line into the log as well ("Command line:",
# "Kernel command line:", "Unknown kernel command line parameters", "dracut-cmdline"), so
# the same list is applied to every log line that holds one. Lines that show a boot loader
# or root device parameter without such a label are treated the same way.
# With whole=1 the input is a bare command line.
# shellcheck disable=SC2016
CMDLINE_AWK='
function keep(text,    n, i, w, words, out, omitted) {
  sub(/", will be passed to user space\.?[ \r]*$/, "", text)
  n = split(text, words)
  out = ""
  omitted = 0
  for (i = 1; i <= n; i++) {
    w = words[i]
    gsub(/^"+|[",\r]+$/, "", w)
    # A lone backslash marks a command line that the kernel wrapped.
    if (w == "\\" || w == "") continue
    if (w ~ /^netconsole=/) {
      w = "netconsole=<set>"
    } else if (w !~ /^(iomem=|nvidia|nouveau|nova|nomodeset$|lockdown=|module_blacklist=|modprobe\.blacklist=|iommu=|intel_iommu=|amd_iommu=|pci=|pcie_|mem_sleep_default=|console=ttyS)/) {
      omitted++
      continue
    }
    out = out (out == "" ? "" : " ") w
  }
  return out (out == "" ? "" : " ") "(" omitted " other parameter(s) omitted)"
}
whole { print keep($0); next }
{
  cut = -1
  if (match(tolower($0), /command line( parameters)?( *:| +")[ "]*/)) {
    cut = RSTART + RLENGTH - 1
  } else if ($0 ~ /(^|[ "])(BOOT_IMAGE|root|cryptdevice|rd\.luks[^ =]*|rd\.lvm[^ =]*)=/) {
    cut = match($0, /: /) ? RSTART + 1 : 0
  }
  if (cut >= 0) print substr($0, 1, cut) keep(substr($0, cut + 1))
  else print
}'

if ! command -v awk >/dev/null 2>&1; then
  echo "collect-report: awk is required to filter kernel command lines; nothing was written" >&2
  exit 2
fi

filter_cmdline() {
  LC_ALL=C awk -v whole=1 "$CMDLINE_AWK"
}

# Redact identifiers before anything is written. Rules, in order: kernel command lines,
# UUIDs (covers GPU-<uuid>), MACs, board and device serials, process names in Xid lines,
# home directories, IPv6 addresses, IPv4 addresses, and this machine's hostname.
#
# IPv6 is matched only as a complete address: eight groups, six groups plus an embedded
# IPv4 tail, or a "::" form. PCI addresses (0000:01:00.0) have neither eight groups nor
# "::", so they survive. A colon may follow ("from fe80::1: timeout").
# The IPv4 rule requires a non-digit/non-dot neighbour on both sides so five-part
# VBIOS versions (98.05.31.00.44) survive.
# Address and hostname rules loop instead of using /g: a match consumes the separator
# after it, and under /g the next one would then match from its middle or not at all. The
# IPv6 rules come first so the IPv4 rule cannot eat an embedded tail and strand the rest.
HOST_RAW="$(hostname 2>/dev/null || true)"
sanitize_stream() {
  local host_rules=() h k=0
  for h in "$HOST_RAW" "${HOST_RAW%%.*}"; do
    # Very short names would shred unrelated text ("a", "pc"), so leave those alone.
    # "HOST" would match its own placeholder forever.
    [[ ${#h} -ge 3 && "$h" != "HOST" ]] || continue
    h="$(printf '%s' "$h" | sed -e 's/[][\\.*^$/+?(){}|]/\\&/g')"
    k=$((k + 1))
    host_rules+=(-e ":host${k}" -e "s/(^|[^[:alnum:]_-])${h}([^[:alnum:]_-]|\$)/\\1<HOST>\\2/" -e "thost${k}")
  done
  local h4='[0-9a-fA-F]{1,4}' v4='([0-9]{1,3}\.){3}[0-9]{1,3}'
  local zone='(%[[:alnum:]_.-]+)?' left='(^|[^[:alnum:]_.])' right='(:?([^[:alnum:]_:]|$))'
  local ip_rules=() n=0 rule
  for rule in \
    "s/${left}(${h4}:){7}${h4}${zone}${right}/\\1<REDACTED_IP>\\4/" \
    "s/${left}(${h4}:){6}${v4}${right}/\\1<REDACTED_IP>\\4/" \
    "s/${left}${h4}(:${h4})*::(${h4}:)*(${h4}|${v4})?${zone}${right}/\\1<REDACTED_IP>\\7/" \
    "s/${left}::(${h4}:)*(${h4}|${v4})${zone}${right}/\\1<REDACTED_IP>\\6/" \
    "s/(^|[^0-9.])${v4}([^0-9.]|\$)/\\1<REDACTED_IP>\\3/"; do
    n=$((n + 1))
    ip_rules+=(-e ":ip${n}" -e "$rule" -e "tip${n}")
  done
  LC_ALL=C awk "$CMDLINE_AWK" | sed -E \
    -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<REDACTED_UUID>/g' \
    -e 's/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/<REDACTED_MAC>/g' \
    -e 's/(serial[ _]?(number)?[[:space:]]*[:=][[:space:]]*)[^[:space:],;]+/\1<REDACTED_SERIAL>/Ig' \
    -e 's/(, name=)[^,]+/\1<PROC>/g' \
    -e 's#/home/[^/[:space:]]+#/home/<USER>#g' \
    "${ip_rules[@]}" \
    ${host_rules[@]+"${host_rules[@]}"}
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      [[ $# -ge 2 ]] || { echo "--out requires a directory path" >&2; exit 2; }
      OUT_DIR="$2"
      shift 2
      ;;
    --tar)
      MAKE_TAR=1
      shift
      ;;
    --sanitize)
      sanitize_stream
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="./blackwell-report-${TS_UTC}"
fi
mkdir -p "$OUT_DIR"

# 1. System & driver summary
{
  echo "timestamp_utc=${TS_UTC}"
  echo "kernel=$(uname -srm)"
  if [[ -r /etc/os-release ]]; then
    grep -E '^(NAME|VERSION|PRETTY_NAME)=' /etc/os-release || true
  fi
  if [[ -r /proc/cmdline ]]; then
    printf "cmdline="
    filter_cmdline </proc/cmdline | sanitize_stream
  fi
  echo
  echo "--- loaded nvidia modules ---"
  grep -iE '^nvidia|^nouveau|^nova' /proc/modules 2>/dev/null || echo "(none loaded)"
  echo
  echo "--- nvidia module parameters ---"
  for p in /sys/module/nvidia*/parameters/*; do
    [[ -r "$p" ]] || continue
    printf "%s=%s\n" "$p" "$(cat "$p" 2>/dev/null || echo '<unreadable>')"
  done | sanitize_stream
} >"${OUT_DIR}/summary.txt"

# 2. Safe nvidia-smi query (explicitly omits serial/uuid)
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi \
    --query-gpu=index,name,pci.device_id,pci.sub_device_id,driver_version,vbios_version,memory.total,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max,power.default_limit,power.max_limit \
    --format=csv 2>&1 | sanitize_stream >"${OUT_DIR}/nvidia-smi.txt" || true
else
  echo "(nvidia-smi not found)" >"${OUT_DIR}/nvidia-smi.txt"
fi

# 3. NVIDIA PCI topology & BAR sizes (sysfs, no unrelated PCI devices)
{
  echo "--- NVIDIA PCI functions (sysfs) ---"
  found=0
  for dev in /sys/bus/pci/devices/*; do
    [[ -r "$dev/vendor" && -r "$dev/device" ]] || continue
    [[ "$(cat "$dev/vendor" 2>/dev/null || true)" == "0x10de" ]] || continue
    found=1
    bdf="$(basename "$dev")"
    did="$(cat "$dev/device" 2>/dev/null || echo '?')"
    cls="$(cat "$dev/class" 2>/dev/null || echo '?')"
    subv="$(cat "$dev/subsystem_vendor" 2>/dev/null || echo '?')"
    subd="$(cat "$dev/subsystem_device" 2>/dev/null || echo '?')"
    spd="$(cat "$dev/current_link_speed" 2>/dev/null || echo '?')"
    wid="$(cat "$dev/current_link_width" 2>/dev/null || echo '?')"
    mspd="$(cat "$dev/max_link_speed" 2>/dev/null || echo '?')"
    mwid="$(cat "$dev/max_link_width" 2>/dev/null || echo '?')"
    echo "${bdf}: vendor=0x10de device=${did} subsys=${subv}:${subd} class=${cls} link=${spd} x${wid} (max ${mspd} x${mwid})"
    if [[ -r "$dev/resource" ]]; then
      head -n 6 "$dev/resource" | sed 's/^/  BAR: /'
    fi
  done
  if (( found == 0 )); then
    echo "(no 0x10de PCI functions exposed in /sys/bus/pci/devices)"
  fi
} >"${OUT_DIR}/pci-topology.txt"

# 4. Filtered GSP / FMC / NVRM kernel messages
if dmesg_out="$(dmesg 2>/dev/null)"; then
  grep -iE 'NVRM|nvidia|gsp|fmc|wpr2?|fwsec|nova|nouveau' <<<"$dmesg_out" \
    | sanitize_stream >"${OUT_DIR}/dmesg-gsp-fmc.txt" || true
  if [[ ! -s "${OUT_DIR}/dmesg-gsp-fmc.txt" ]]; then
    echo "(dmesg readable, but no NVIDIA/GSP/FMC lines matched)" >"${OUT_DIR}/dmesg-gsp-fmc.txt"
  fi
else
  echo "(dmesg unreadable without root/CAP_SYSLOG; re-run with sudo if GSP/FMC kernel logs are needed)" >"${OUT_DIR}/dmesg-gsp-fmc.txt"
fi

echo "Wrote sanitized read-only report to: ${OUT_DIR}"
ls -la "$OUT_DIR"

if (( MAKE_TAR == 1 )); then
  abs_dir="$(cd "$OUT_DIR" && pwd)"
  tar_path="${abs_dir}.tar.gz"
  members=()
  for f in "${REPORT_FILES[@]}"; do
    members+=("$(basename "$abs_dir")/${f}")
  done
  tar -czf "$tar_path" -C "$(dirname "$abs_dir")" -- "${members[@]}"
  echo "Created archive: ${tar_path} (${#members[@]} files)"
fi

extra=0
for f in "$OUT_DIR"/* "$OUT_DIR"/.[!.]*; do
  [[ -e "$f" ]] || continue
  case " ${REPORT_FILES[*]} " in
    *" $(basename "$f") "*) ;;
    *) extra=$((extra + 1)) ;;
  esac
done
if (( extra > 0 )); then
  echo "WARNING: ${OUT_DIR} also holds ${extra} entry(ies) this run did not write. They were not sanitized and are not in the archive; do not publish them." >&2
fi

echo "NOTE: Review all files before publishing to confirm no private host info remains."
echo "NEXT: Fill in reports/template.md, then attach it with the four files above to a GitHub issue or open a PR (see README section 5)."
if grep -q "BWLAB: begin" "${OUT_DIR}/dmesg-gsp-fmc.txt"; then
  echo "      Trace patch output found; decode it with: python3 scripts/abi-layout.py --from-log ${OUT_DIR}/dmesg-gsp-fmc.txt"
fi
