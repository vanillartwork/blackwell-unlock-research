# SPDX-License-Identifier: GPL-2.0-only

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def find_bash():
    bash = shutil.which("bash")
    if bash is None:
        return None
    if sys.platform == "win32":
        # System32\bash.exe and the WindowsApps alias start WSL, where Windows paths and
        # the working directory mean something else.
        low = bash.lower()
        if "system32" in low or "windowsapps" in low:
            return None
    return bash


BASH = find_bash()


def run(args, stdin="", cwd=SCRIPTS, env=None):
    return subprocess.run(
        [BASH, *args], input=stdin.encode("utf-8"), capture_output=True, cwd=cwd, timeout=60,
        env=env,
    )


@unittest.skipIf(BASH is None, "no POSIX bash available")
class ShellScriptTests(unittest.TestCase):
    def test_syntax(self):
        for name in ("check-env.sh", "collect-report.sh"):
            with self.subTest(script=name):
                proc = run(["-n", name])
                self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def sanitize(self, text):
        proc = run(["collect-report.sh", "--sanitize"], text)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return proc.stdout.decode("utf-8").replace("\r", "")

    def test_identifiers_are_redacted(self):
        secrets = {
            "NVRM: GPU Board Serial Number: 1324021012345": "1324021012345",
            "usb 1-2: SerialNumber: 0123ABCDEF": "0123ABCDEF",
            "GPU-8f1c2d3e-aaaa-bbbb-cccc-0123456789ab": "8f1c2d3e",
            "Xid (PCI:0000:01:00): 79, pid=1, name=secret-app, GPU lost": "secret-app",
            "link/ether aa:bb:cc:dd:ee:ff": "aa:bb:cc",
            "file /home/alice/work.log": "alice",
            "netconsole: local 10.0.0.5 remote 192.168.1.20": "192.168.1.20",
        }
        for line, secret in secrets.items():
            with self.subTest(line=line):
                self.assertNotIn(secret, self.sanitize(line + "\n"))

    COMMAND_LINES = (
        "[    0.000000] Command line: BOOT_IMAGE=/vmlinuz-6.12 root=/dev/mapper/review_disk ro {p}",
        "[    0.034521] Kernel command line: BOOT_IMAGE=/vmlinuz-6.12 resume=/dev/mapper/review_disk {p}",
        "[    0.034522] Kernel command line: {p} \\",
        "[    0.034523] Kernel command line: dyndbg=\"file review_disk +p\" {p}",
        "[    0.034600] Unknown kernel command line parameters \"splash BOOT_IMAGE=/boot/review_disk {p}\","
        " will be passed to user space.",
        "[    2.1] dracut-cmdline[322]: Using kernel command line parameters: rd.lvm.lv=review_disk/root {p}",
        "[    2.2] some-hook[9]: cryptdevice=/dev/sda2:review_disk {p}",
        "[    2.3] early: {p} root=LABEL=review_disk",
        "BOOT_IMAGE=/review_disk {p}",
    )

    def test_command_lines_in_the_log_are_filtered(self):
        keep = "nvidia_drm.modeset=1"
        secret = "netconsole=@10.0.0.2/eth0,@10.0.0.9/aa:bb:cc:dd:ee:ff"
        lines = [x.format(p=f"{keep} {secret}") for x in self.COMMAND_LINES]
        out = self.sanitize("\n".join(lines) + "\n").splitlines()
        self.assertEqual(len(out), len(lines))
        for line, got in zip(lines, out):
            with self.subTest(line=line):
                self.assertIn(keep, got)
                self.assertIn("netconsole=<set>", got)
                self.assertRegex(got, r"\(\d+ other parameter\(s\) omitted\)$")
                for leak in ("review_disk", "BOOT_IMAGE", "10.0.0", "vmlinuz"):
                    self.assertNotIn(leak, got)
        self.assertTrue(out[0].startswith("[    0.000000] Command line: nvidia_drm"))
        self.assertIn("(3 other", out[0])  # BOOT_IMAGE, root, ro
        self.assertIn("(0 other", out[2])  # the wrap mark is no parameter
        self.assertIn("(2 other", out[4])  # splash and BOOT_IMAGE, not the sentence after them

    def test_windows_line_ends_do_not_hide_a_command_line(self):
        out = self.sanitize("Command line: root=/dev/review_disk nvidia.a=1\r\n")
        self.assertIn("nvidia.a=1 (1 other", out)
        self.assertNotIn("review_disk", out)

    def test_adjacent_ip_addresses_are_all_redacted(self):
        out = self.sanitize("10.0.0.1 10.0.0.2 10.0.0.3\n")
        self.assertEqual(out.strip(), "<REDACTED_IP> <REDACTED_IP> <REDACTED_IP>")

    def test_ipv6_addresses_are_redacted(self):
        cases = []
        for address in (
            "2001:db8::42",
            "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
            "2001:DB8:0:0:8:800:200C:417A",
            "::1",
            "fe80::",
            "fe80::1%eth0",
            "::ffff:192.0.2.1",
            "64:ff9b::192.0.2.33",
            "0:0:0:0:0:ffff:192.0.2.5",
        ):
            for text in (
                f"netconsole: remote {address}",
                f"http://[{address}]:8080/",
                f"src={address},dst={address}",
                f"peer:{address}",
                f"from {address}: timeout",
                f"{address} {address} {address} {address}",
                f"{address},10.0.0.1,{address} 10.0.0.2",
                f"cidr {address}/64.",
            ):
                cases.append((address, text))
        lines = self.sanitize("".join(text + "\n" for _, text in cases)).splitlines()
        self.assertEqual(len(lines), len(cases))
        for (address, text), out in zip(cases, lines):
            with self.subTest(text=text):
                self.assertIn("<REDACTED_IP>", out)
                for group in address.replace("%", ":").replace(".", ":").split(":"):
                    # Single digits also occur in port numbers and prefixes.
                    if len(group) > 2:
                        self.assertNotIn(group, out)
                self.assertNotIn("::", out)

    def test_evidence_survives_redaction(self):
        for line in (
            "VBIOS 98.05.31.00.44",
            "NVRM version 620.73.04",
            "driver 32.0.16.2101",
            "WPR2_LO 0x001fa824= 0x02fb8800",
            "GPU at PCI:0000:01:00.0",
            "Xid (PCI:0000:01:00): 79, GPU 00000000:01:00.0 and 10000:e1:00.0",
            "pci_bus 0000:00: root bus resource [bus 00-fe] 10de:2f18 at 12:34:56",
            "nova_core::gsp::boot core::fmt::Debug a :: b",
            "NVRM: GPU0 BWLAB: begin GspFwWprMeta tag=620.73.04 size=256",
            "NVRM: GPU0 BWLAB: 0040: 01 00 00 00 00 01 00 00 10 11 12 13 aa bb cc dd",
            "NVRM: GPU0 BWLAB: cot size=860 version=2 frtsSysmem=0x0 root_size=1",
            "nvidia-modeset: no command line given, chroot=1 rootfs",
            "nvidia 0000:01:00.0: vgaarb: VGA decodes changed: olddecodes=io+mem,decodes=none:owns=none",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.sanitize(line + "\n").strip(), line)

    def test_hostname_is_redacted_on_word_boundaries(self):
        host = run(["-c", "hostname"]).stdout.decode().strip()
        if len(host.split(".")[0]) < 3:
            self.skipTest("hostname too short to redact")
        out = self.sanitize(f"host {host} booted\nx{host}x\n{host} {host} {host}\n").splitlines()
        self.assertEqual(out[0], "host <HOST> booted")
        self.assertIn(host, out[1])
        self.assertEqual(out[2], "<HOST> <HOST> <HOST>")

    def test_bad_arguments(self):
        self.assertEqual(run(["collect-report.sh", "--nope"]).returncode, 2)
        self.assertEqual(run(["collect-report.sh", "--out"]).returncode, 2)

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_collect_report_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report"
            proc = run(["collect-report.sh", "--out", str(out), "--tar"])
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(
                {p.name for p in out.iterdir()},
                {"summary.txt", "nvidia-smi.txt", "pci-topology.txt", "dmesg-gsp-fmc.txt"},
            )
            self.assertTrue(out.with_suffix(".tar.gz").exists())
            self.assertIn("reports/template.md", proc.stdout.decode("utf-8"))
            summary = (out / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("other parameter(s) omitted", summary)
            for leak in ("root=", "UUID=", "netconsole=@"):
                self.assertNotIn(leak, summary)

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_archived_log_holds_no_boot_parameters(self):
        secret = "review_disk"
        log = "\n".join(
            [x.format(p="nvidia_drm.modeset=1 nvidia.NVreg_X=/home/alice/y") for x in self.COMMAND_LINES]
            + ["[    5.0] NVRM: loading NVIDIA UNIX Open Kernel Module for x86_64  620.73.04"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            (Path(tmp) / "log.txt").write_text(log + "\n", encoding="utf-8")
            (bin_dir / "dmesg").write_text(f"#!/bin/sh\ncat '{tmp}/log.txt'\n")
            (bin_dir / "dmesg").chmod(0o755)
            out = Path(tmp) / "report"
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
            proc = run(["collect-report.sh", "--out", str(out), "--tar"], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            with tarfile.open(out.with_suffix(".tar.gz")) as tar:
                members = {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}
        for name, text in members.items():
            for leak in (secret, "BOOT_IMAGE", "root=", "alice"):
                with self.subTest(member=name, leak=leak):
                    self.assertNotIn(leak, text)
        # What the report is for is still there.
        kept = members["report/dmesg-gsp-fmc.txt"]
        self.assertIn("620.73.04", kept)
        self.assertEqual(kept.count("nvidia_drm.modeset=1"), len(self.COMMAND_LINES))

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_missing_awk_is_an_error_not_an_empty_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            for tool in ("sed", "hostname", "cat", "date", "mkdir", "grep"):
                (bin_dir / tool).symlink_to(shutil.which(tool) or "/bin/true")
            out = Path(tmp) / "report"
            proc = run(["collect-report.sh", "--out", str(out)], env=dict(os.environ, PATH=str(bin_dir)))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("awk is required", proc.stderr.decode())
            self.assertFalse(out.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_archive_excludes_files_this_run_did_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report"
            (out / "old").mkdir(parents=True)
            (out / "previous-raw.log").write_text("raw host data\n", encoding="utf-8")
            (out / ".hidden").write_text("raw host data\n", encoding="utf-8")
            (out / "old" / "dump.txt").write_text("raw host data\n", encoding="utf-8")
            (out / "summary.txt").write_text("stale summary\n", encoding="utf-8")
            proc = run(["collect-report.sh", "--out", str(out), "--tar"])
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            with tarfile.open(out.with_suffix(".tar.gz")) as tar:
                names = sorted(tar.getnames())
                summary = tar.extractfile("report/summary.txt").read().decode()
            self.assertEqual(names, [
                "report/dmesg-gsp-fmc.txt", "report/nvidia-smi.txt",
                "report/pci-topology.txt", "report/summary.txt",
            ])
            self.assertNotIn("stale summary", summary)
            self.assertIn("3 entry(ies) this run did not write", proc.stderr.decode())

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_clean_directory_gives_no_leftover_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run(["collect-report.sh", "--out", str(Path(tmp) / "report")])
            self.assertEqual(proc.stderr.decode(), "")

    @unittest.skipUnless(sys.platform.startswith("linux"), "needs Linux sysfs/procfs")
    def test_check_env_reports_a_verdict(self):
        proc = run(["check-env.sh"])
        text = proc.stdout.decode("utf-8")
        self.assertIn(proc.returncode, (0, 1), proc.stderr.decode())
        self.assertIn("=== Summary:", text)
        self.assertEqual("[FAIL]" in text, proc.returncode == 1)

    @unittest.skipUnless(os.name == "posix", "needs POSIX permissions")
    def test_scripts_are_executable(self):
        for name in ("check-env.sh", "collect-report.sh"):
            self.assertTrue(os.access(SCRIPTS / name, os.X_OK))


NVIDIA = {"vendor": "0x10de", "driver": "nvidia"}
IGPU = {"vendor": "0x8086", "driver": "i915"}
LIT = ("connected", "enabled")
DARK = ("disconnected", "disabled")
NO_RECOVERY = "No confirmed recovery path"


@unittest.skipIf(BASH is None, "no POSIX bash available")
@unittest.skipUnless(sys.platform.startswith("linux"), "needs symlinks and Linux tools")
class RecoveryPathTests(unittest.TestCase):
    """check-env.sh against fabricated machines."""

    def preflight(self, adapters, ssh=False, cmdline="", modules=""):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n, spec in enumerate(adapters):
                dev = root / "sys/bus/pci/devices" / f"0000:{n:02x}:00.0"
                dev.mkdir(parents=True)
                (dev / "class").write_text(spec.get("class", "0x030000") + "\n")
                (dev / "vendor").write_text(spec["vendor"] + "\n")
                (dev / "boot_vga").write_text(f"{int(spec.get('boot_vga', False))}\n")
                if spec.get("driver"):
                    drv = root / "sys/bus/pci/drivers" / spec["driver"]
                    drv.mkdir(parents=True, exist_ok=True)
                    (dev / "driver").symlink_to(drv)
                for k, (state, enabled) in enumerate(spec.get("outputs", [])):
                    port = dev / "drm" / f"card{n}" / f"card{n}-DP-{k}"
                    port.mkdir(parents=True)
                    (port / "status").write_text(state + "\n")
                    if enabled is not None:
                        (port / "enabled").write_text(enabled + "\n")
                if spec.get("fb"):
                    (dev / "graphics" / f"fb{n}").mkdir(parents=True)
            (root / "sys/bus/pci/devices").mkdir(parents=True, exist_ok=True)
            proc_dir = root / "proc"
            proc_dir.mkdir()
            (proc_dir / "version").write_text("Linux version 6.12.0\n")
            (proc_dir / "cmdline").write_text(cmdline + "\n")
            (proc_dir / "modules").write_text(modules + "\n")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in {
                "systemctl": f"exit {0 if ssh else 3}",
                "pgrep": "exit 1",
                "systemd-detect-virt": "echo none; exit 1",
                "mokutil": "echo 'SecureBoot disabled'",
            }.items():
                (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
                (bin_dir / name).chmod(0o755)
            env = dict(
                os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                BWLAB_SYSFS_ROOT=str(root / "sys"), BWLAB_PROCFS_ROOT=str(proc_dir),
            )
            proc = run(["check-env.sh"], env=env)
            text = proc.stdout.decode("utf-8")
            self.assertEqual(proc.returncode, int("[FAIL]" in text), text + proc.stderr.decode())
            return text

    def tree(self, root, layout):
        """Build sys/devices with real nesting plus the bus symlinks; returns sys path."""
        bus = root / "sys/bus/pci/devices"
        bus.mkdir(parents=True)
        for path, vendor, cls in layout:
            dev = root / "sys/devices/pci0000:00" / path
            dev.mkdir(parents=True, exist_ok=True)
            (dev / "vendor").write_text(vendor + "\n")
            (dev / "class").write_text(cls + "\n")
            (bus / dev.name).symlink_to(dev)
        return root / "sys"

    def scope(self, layout, methods=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sysfs = self.tree(root, layout)
            for bdf, text in (methods or {}).items():
                (sysfs / "bus/pci/devices" / bdf / "reset_method").write_text(text + "\n")
            (root / "proc").mkdir()
            (root / "proc/version").write_text("Linux version 6.12.0\n")
            (root / "proc/cmdline").write_text("\n")
            env = dict(os.environ, BWLAB_SYSFS_ROOT=str(sysfs), BWLAB_PROCFS_ROOT=str(root / "proc"))
            text = run(["check-env.sh"], env=env).stdout.decode("utf-8")
            return text, [x for x in text.splitlines() if "reset scope" in x]

    def test_reset_scope_lists_the_cards_own_functions_without_warning(self):
        text, lines = self.scope([
            ("0000:00:01.0", "0x8086", "0x060400"),
            ("0000:00:01.0/0000:01:00.0", "0x10de", "0x030000"),
            ("0000:00:01.0/0000:01:00.1", "0x10de", "0x040300"),
            ("0000:00:1f.3", "0x8086", "0x040300"),
        ], {"0000:01:00.0": "flr bus"})
        self.assertEqual(len(lines), 1)
        self.assertIn("methods=flr bus bridge=0000:00:01.0 also_below=0000:01:00.1", lines[0])
        self.assertNotIn("would be reset with it", text)

    def test_reset_scope_warns_about_foreign_devices(self):
        text, lines = self.scope([
            ("0000:00:01.0", "0x8086", "0x060400"),
            ("0000:00:01.0/0000:01:00.0", "0x10de", "0x030200"),
            ("0000:00:01.0/0000:01:01.0", "0x144d", "0x010802"),
            ("0000:00:01.0/0000:01:02.0", "0x8086", "0x060400"),
            ("0000:00:01.0/0000:01:02.0/0000:02:00.0", "0x8086", "0x020000"),
        ])
        self.assertIn("methods=none", lines[0])
        self.assertIn("3 device(s) outside this card", text)

    def test_reset_scope_ignores_non_pci_vendor_files(self):
        with_virtio = [
            ("0000:00:01.0", "0x8086", "0x060400"),
            ("0000:00:01.0/0000:01:00.0", "0x10de", "0x030000"),
            ("0000:00:01.0/0000:01:00.0/virtio0", "0x1af4", "0x000000"),
        ]
        text, lines = self.scope(with_virtio[:2] + [with_virtio[2]])
        self.assertIn("also_below=none", lines[0])
        self.assertNotIn("would be reset with it", text)

    def test_card_on_the_root_bus_has_no_bridge(self):
        _, lines = self.scope([("0000:00:02.0", "0x10de", "0x030000")])
        self.assertIn("no upstream bridge", lines[0])

    def test_two_nvidia_cards_are_not_a_recovery_path(self):
        cards = [dict(NVIDIA, boot_vga=True, outputs=[LIT], fb=True), dict(NVIDIA, outputs=[LIT], fb=True)]
        text = self.preflight(cards)
        self.assertIn(NO_RECOVERY, text)
        self.assertIn("affects all of them", text)
        # The same machine is fine once there is a remote way in.
        self.assertNotIn(NO_RECOVERY, self.preflight(cards, ssh=True))

    def test_single_nvidia_card_needs_remote_access(self):
        card = [dict(NVIDIA, boot_vga=True, outputs=[LIT], fb=True)]
        self.assertIn(NO_RECOVERY, self.preflight(card))
        self.assertNotIn(NO_RECOVERY, self.preflight(card, ssh=True))

    def test_igpu_counts_only_when_it_drives_a_console(self):
        in_use = (
            dict(IGPU, boot_vga=True, outputs=[LIT], fb=True),
            dict(IGPU, outputs=[DARK, LIT], fb=True),
            dict(IGPU, outputs=[LIT], fb=True, **{"class": "0x038000"}),
        )
        for igpu in in_use:
            with self.subTest(igpu=igpu):
                text = self.preflight([NVIDIA, igpu])
                self.assertIn("Independent console: 0000:01:00.0 (driver i915, fb1)", text)
                self.assertNotIn("[FAIL]", text)

    def test_boot_vga_alone_is_not_a_console(self):
        unconfirmed = (
            # The reviewed case: firmware used this adapter, nothing drives it now.
            (dict(IGPU, driver=None, boot_vga=True, outputs=[DARK]), "has no driver"),
            (dict(IGPU, driver=None, boot_vga=True, outputs=[LIT], fb=True), "has no driver"),
            (dict(IGPU, boot_vga=True), "has no connected output"),
            (dict(IGPU, boot_vga=True, outputs=[DARK, DARK], fb=True), "has no connected output"),
            (dict(IGPU, boot_vga=True, outputs=[("connected", "disabled")], fb=True), "drives none"),
            (dict(IGPU, boot_vga=True, outputs=[("connected", None)], fb=True), "drives none"),
            # An output that is enabled but not connected shows nothing.
            (dict(IGPU, boot_vga=True, outputs=[("disconnected", "enabled")], fb=True), "has no connected output"),
            (dict(IGPU, boot_vga=True, outputs=[("unknown", "enabled")], fb=True), "has no connected output"),
            (dict(IGPU, boot_vga=True, outputs=[LIT]), "has no framebuffer"),
        )
        for igpu, reason in unconfirmed:
            with self.subTest(igpu=igpu):
                text = self.preflight([NVIDIA, igpu])
                self.assertNotIn("Independent console", text)
                self.assertIn(f"0000:01:00.0 {reason}", text)
                self.assertIn("boot_vga alone does not confirm one", text)
                self.assertIn(NO_RECOVERY, text)
                # Another confirmed way in is enough.
                self.assertNotIn("[FAIL]", self.preflight([NVIDIA, igpu], ssh=True))
                self.assertNotIn("[FAIL]", self.preflight([NVIDIA, igpu], cmdline="console=ttyS0"))

    def test_one_usable_adapter_is_enough_among_several(self):
        idle = dict(IGPU, driver=None, boot_vga=True, outputs=[DARK])
        good = {"vendor": "0x1002", "driver": "amdgpu", "outputs": [LIT], "fb": True}
        for adapters in ([NVIDIA, idle, good], [NVIDIA, good, idle]):
            text = self.preflight(adapters)
            self.assertIn("(driver amdgpu", text)
            self.assertNotIn("[FAIL]", text)

    def test_adapters_that_cannot_show_a_console_are_ignored(self):
        for spec in (
            dict(IGPU, driver="vfio-pci", boot_vga=True, outputs=[LIT], fb=True),
            {"vendor": "0x1414", "driver": "hyperv_drm", "boot_vga": True, "outputs": [LIT], "fb": True},
        ):
            with self.subTest(spec=spec):
                self.assertIn(NO_RECOVERY, self.preflight([NVIDIA, spec]))

    def test_serial_is_a_way_in_but_netconsole_is_not(self):
        self.assertNotIn(NO_RECOVERY, self.preflight([NVIDIA], cmdline="quiet console=ttyS0,115200"))
        text = self.preflight([NVIDIA], modules="netconsole 16384 0 - Live 0x0")
        self.assertIn("netconsole detected", text)
        self.assertIn(NO_RECOVERY, text)
        both = self.preflight(
            [NVIDIA], cmdline="console=ttyS0", modules="netconsole 16384 0 - Live 0x0"
        )
        self.assertNotIn(NO_RECOVERY, both)


if __name__ == "__main__":
    unittest.main()
