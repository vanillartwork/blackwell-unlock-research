#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Read-only preflight check for native Linux Blackwell (GB20x) ABI observation.
# Checks bare-metal vs WSL/VM, display recovery path, Secure Boot/Lockdown,
# IO_STRICT_DEVMEM, SSH/netconsole, and kernel module build prerequisites.

set -euo pipefail

PASS=0
WARN=0
FAIL=0
HAVE_CONSOLE=0
HAVE_SSH=0
HAVE_SERIAL=0

# Overridable so the tests can point the checks at a fabricated machine.
SYSFS="${BWLAB_SYSFS_ROOT:-/sys}"
PROCFS="${BWLAB_PROCFS_ROOT:-/proc}"

ok()   { printf "[PASS] %s\n" "$1"; PASS=$((PASS + 1)); }
warn() { printf "[WARN] %s\n" "$1"; WARN=$((WARN + 1)); }
fail() { printf "[FAIL] %s\n" "$1"; FAIL=$((FAIL + 1)); }

echo "=== blackwell-abi-lab: Linux Environment Preflight (read-only) ==="
echo "Kernel : $(uname -srm)"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  echo "OS     : ${PRETTY_NAME:-unknown}"
fi
echo

# 1. Native Linux bare-metal check (WSL / container / VM)
if grep -qiE "microsoft|wsl" "$PROCFS/version" 2>/dev/null; then
  fail "Running inside WSL2: kernel module patching and BAR0 sysfs access are unavailable here (use native Linux bare-metal for Route 1; Route 2 userspace benchmarks still apply)"
elif command -v systemd-detect-virt >/dev/null 2>&1; then
  virt="$(systemd-detect-virt 2>/dev/null || true)"
  if [[ -z "$virt" || "$virt" == "none" ]]; then
    ok "Bare-metal Linux host detected (no virtualization layer)"
  else
    fail "Virtualized environment detected (${virt}): Route 1 requires a physical host running native Linux, including when GPU passthrough is available"
  fi
else
  warn "Could not verify virtualization state (systemd-detect-virt unavailable)"
fi

# 2. PCI GPU & independent console
# Route 1 unloads the nvidia modules, so a display only helps recovery when another driver
# is showing something on it now and can carry a text console. Neither the adapter count
# nor boot_vga shows that: two NVIDIA cards share one driver, an iGPU may have nothing
# plugged in, and boot_vga only records which adapter the firmware used at power-on. So an
# adapter counts when it has a driver of its own, a framebuffer, and a connected output that
# it is driving ("enabled"). Anything less has to be made up for by SSH or a serial console.
if [[ -d "$SYSFS/bus/pci/devices" ]]; then
  nv_disp=0
  other_disp=0
  console=""
  doubts=()
  for dev in "$SYSFS"/bus/pci/devices/*; do
    [[ -r "$dev/class" && -r "$dev/vendor" ]] || continue
    cls="$(cat "$dev/class" 2>/dev/null || true)"
    ven="$(cat "$dev/vendor" 2>/dev/null || true)"
    # 0x03xx = display controller: VGA (0300), 3D (0302) and "other" (0380, used by
    # many laptop iGPUs).
    [[ "$cls" == 0x03* ]] || continue
    drv="none"
    if [[ -L "$dev/driver" ]]; then
      drv="$(basename "$(readlink "$dev/driver")")"
    fi
    boot_vga="$(cat "$dev/boot_vga" 2>/dev/null || true)"
    connected=0
    driven=0
    for status in "$dev"/drm/card*/card*-*/status; do
      [[ -r "$status" && "$(cat "$status" 2>/dev/null || true)" == "connected" ]] || continue
      connected=$((connected + 1))
      if [[ "$(cat "${status%/status}/enabled" 2>/dev/null || true)" == "enabled" ]]; then
        driven=$((driven + 1))
      fi
    done
    fbdev="none"
    for fb in "$dev"/graphics/fb*; do
      [[ -e "$fb" ]] || continue
      fbdev="$(basename "$fb")"
    done
    printf "[INFO] display %s vendor=%s driver=%s boot_vga=%s connected_outputs=%d driven_outputs=%d framebuffer=%s\n" \
      "$(basename "$dev")" "$ven" "$drv" "${boot_vga:-?}" "$connected" "$driven" "$fbdev"
    if [[ "$ven" == "0x10de" ]]; then
      nv_disp=$((nv_disp + 1))
      continue
    fi
    # 0x1414 is the Hyper-V/WSL virtual adapter; the listed drivers hand the card to a VM.
    if [[ "$ven" == "0x1414" || "$drv" == "vfio-pci" || "$drv" == "pci-stub" || "$drv" == "pciback" ]]; then
      continue
    fi
    other_disp=$((other_disp + 1))
    if [[ "$drv" == "none" ]]; then
      doubts+=("$(basename "$dev") has no driver")
    elif (( connected == 0 )); then
      doubts+=("$(basename "$dev") has no connected output")
    elif (( driven == 0 )); then
      doubts+=("$(basename "$dev") drives none of its connected outputs")
    elif [[ "$fbdev" == "none" ]]; then
      doubts+=("$(basename "$dev") has no framebuffer for a text console")
    else
      console="$(basename "$dev") (driver ${drv}, ${fbdev})"
    fi
  done
  if (( nv_disp > 0 )); then
    ok "Detected ${nv_disp} NVIDIA display/3D PCI function(s) in sysfs"
  else
    warn "No NVIDIA PCI display function visible in ${SYSFS}/bus/pci/devices (expected in WSL2 or headless host without dGPU)"
  fi
  if [[ -n "$console" ]]; then
    HAVE_CONSOLE=1
    ok "Independent console: ${console} drives a connected output without the nvidia modules (switch to a text console there once, e.g. Ctrl+Alt+F3, before relying on it)"
  elif (( other_disp > 0 )); then
    warn "Non-NVIDIA adapter not counted as a recovery console: $(printf '%s; ' "${doubts[@]}")boot_vga alone does not confirm one"
  elif (( nv_disp >= 2 )); then
    warn "${nv_disp} NVIDIA adapters and no other display: unloading the nvidia modules affects all of them, so the second card is not counted as a recovery console"
  else
    warn "No display outside the NVIDIA driver was found: a Route 1 FMC hang may blank the local console"
  fi
else
  warn "${SYSFS}/bus/pci/devices not present"
fi

# 2b. What a bus reset of each NVIDIA card would reach (read-only; nothing is reset here)
# A secondary bus reset hits everything below the upstream bridge, so anyone considering one
# after a hang should know the neighbours beforehand. See docs/recovery.md.
for dev in "$SYSFS"/bus/pci/devices/*; do
  [[ -r "$dev/class" && -r "$dev/vendor" ]] || continue
  [[ "$(cat "$dev/vendor")" == "0x10de" && "$(cat "$dev/class")" == 0x03* ]] || continue
  bdf="$(basename "$dev")"
  real="$(readlink -f "$dev")"
  bridge_dir="$(dirname "$real")"
  methods="$(cat "$dev/reset_method" 2>/dev/null || true)"
  if [[ ! -r "$bridge_dir/vendor" ]]; then
    echo "[INFO] reset scope ${bdf}: methods=${methods:-none} (no upstream bridge in sysfs)"
    continue
  fi
  others=()
  foreign=0
  while IFS= read -r peer; do
    name="$(basename "$peer")"
    # Other buses (virtio, HD-audio) also carry "vendor" files; keep PCI functions only.
    [[ "$name" =~ ^[0-9a-f]{4,}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$ && "$peer" != "$real" ]] || continue
    others+=("$name")
    # Functions of the card itself (audio, USB-C) share its slot; anything else is foreign.
    [[ "$name" == "${bdf%.*}."* ]] || foreign=$((foreign + 1))
  done < <(find "$bridge_dir" -mindepth 2 -maxdepth 6 -name vendor -path "*:*" -exec dirname {} \; 2>/dev/null | sort)
  echo "[INFO] reset scope ${bdf}: methods=${methods:-none} bridge=$(basename "$bridge_dir") also_below=${others[*]:-none}"
  if (( foreign > 0 )); then
    warn "${bdf}: ${foreign} device(s) outside this card sit below the same bridge and would be reset with it"
  fi
done

# 3. Secure Boot & Kernel Lockdown
sb_state="unknown"
if command -v mokutil >/dev/null 2>&1; then
  sb_out="$(mokutil --sb-state 2>/dev/null || true)"
  if grep -qi "disabled" <<<"$sb_out"; then
    sb_state="OFF"
  elif grep -qi "enabled" <<<"$sb_out"; then
    sb_state="ON"
  fi
fi
if [[ "$sb_state" == "unknown" ]]; then
  # mokutil may be missing; the EFI variable is 4 attribute bytes plus one value byte.
  sb_var="$SYSFS/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
  if [[ -r "$sb_var" ]]; then
    case "$(od -An -t u1 -j 4 -N 1 "$sb_var" 2>/dev/null | tr -d '[:space:]')" in
      0) sb_state="OFF" ;;
      1) sb_state="ON" ;;
    esac
  fi
fi
if [[ "$sb_state" == "OFF" ]]; then
  ok "Secure Boot is OFF (self-built open-gpu-kernel-modules can load without MOK signing)"
elif [[ "$sb_state" == "ON" ]]; then
  warn "Secure Boot is ON: custom open-gpu-kernel-modules builds require MOK signing or disabling Secure Boot"
else
  warn "Secure Boot state unknown (mokutil and the SecureBoot EFI variable are unavailable, or non-EFI environment)"
fi

if [[ -r "$SYSFS/kernel/security/lockdown" ]]; then
  ld="$(cat "$SYSFS/kernel/security/lockdown")"
  if grep -q "\[none\]" <<<"$ld"; then
    ok "Kernel lockdown: none"
  else
    warn "Kernel lockdown active (${ld}): raw BAR0/devmem reads and unsigned modules may be blocked"
  fi
fi

# 4. IO_STRICT_DEVMEM / kernel cmdline check
cmdline="$(cat "$PROCFS/cmdline" 2>/dev/null || true)"
strict_devmem="unknown"
for cfg in "/boot/config-$(uname -r)" "/lib/modules/$(uname -r)/config" "$PROCFS/config.gz"; do
  [[ -r "$cfg" ]] || continue
  if [[ "$cfg" == *.gz ]]; then
    cfg_text="$(zcat "$cfg" 2>/dev/null || true)"
  else
    cfg_text="$(cat "$cfg" 2>/dev/null || true)"
  fi
  [[ -n "$cfg_text" ]] || continue
  if grep -q '^CONFIG_IO_STRICT_DEVMEM=y' <<<"$cfg_text"; then
    strict_devmem="y"
  else
    strict_devmem="n"
  fi
  break
done
if grep -qw "iomem=relaxed" <<<"$cmdline"; then
  ok "Kernel cmdline carries iomem=relaxed (direct BAR0 mmap permitted while driver is bound)"
elif [[ "$strict_devmem" == "n" ]]; then
  ok "CONFIG_IO_STRICT_DEVMEM is not set (BAR0 mmap is not blocked by a bound driver)"
elif [[ "$strict_devmem" == "y" ]]; then
  warn "CONFIG_IO_STRICT_DEVMEM=y without iomem=relaxed: sysfs resource0 mmap fails while nvidia is bound"
else
  warn "Kernel config not readable and iomem=relaxed not set: sysfs resource0 mmap may fail while nvidia is bound"
fi

# 5. Remote logging / SSH recovery readiness
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet ssh sshd ssh.socket 2>/dev/null; then
  HAVE_SSH=1
  ok "SSH daemon is active (log in from another machine once before relying on it)"
elif pgrep -x sshd >/dev/null 2>&1; then
  HAVE_SSH=1
  ok "sshd process is running (log in from another machine once before relying on it)"
else
  warn "No active sshd detected: enable SSH before running Route 1 kernel experiments"
fi

HAVE_NETCONSOLE=0
if grep -qw "netconsole" "$PROCFS/modules" 2>/dev/null || grep -q "netconsole=" <<<"$cmdline"; then
  HAVE_NETCONSOLE=1
  ok "netconsole detected (kernel panic/hang logs can stream off-host; it is not a way back in)"
fi
if grep -qE "console=ttyS[0-9]" <<<"$cmdline"; then
  HAVE_SERIAL=1
  ok "Serial console configured on kernel cmdline"
fi
if (( HAVE_NETCONSOLE == 0 && HAVE_SERIAL == 0 )); then
  warn "Neither netconsole nor serial console detected: FMC hang logs may be lost on hard reboot"
fi

# A hung FMC can take every NVIDIA display down. Without another way in, the machine needs
# a hard power cut and the kernel log is lost.
if (( HAVE_CONSOLE == 0 && HAVE_SSH == 0 && HAVE_SERIAL == 0 )); then
  fail "No confirmed recovery path: provide a non-NVIDIA adapter that drives a connected output, an SSH daemon, or a serial console"
fi

# 6. Kernel module build environment
kbuild="/lib/modules/$(uname -r)/build"
if [[ -d "$kbuild" ]] && command -v make >/dev/null 2>&1 && command -v gcc >/dev/null 2>&1; then
  ok "Kernel headers (${kbuild}) and gcc/make are installed"
else
  warn "Kernel headers or gcc/make missing (required only when building patched open-gpu-kernel-modules; package names are in README section 3.1)"
fi

echo
echo "=== Summary: PASS=${PASS}  WARN=${WARN}  FAIL=${FAIL} ==="
if (( FAIL > 0 )); then
  echo "Verdict: NOT READY for Route 1 (kernel/FMC ABI) on this environment. Hardware baselines and Route 2 benchmarks can run in userspace."
  exit 1
fi
echo "Verdict: Preflight checks completed with zero hard blockers."
