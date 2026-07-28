#!/usr/bin/env python3
"""Guest-side Distrohop smoke suite.

This script is intentionally safe only for the disposable VM created by
``tools/vm_lab.py``.  It refuses to format anything except the lab's exact 8
GiB secondary virtio disk and never reads host credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


EXPECTED = {
    "ubuntu-2604": {"ids": {"ubuntu"}, "manager": "apt"},
    "debian-13": {"ids": {"debian"}, "manager": "apt"},
    "fedora-44": {"ids": {"fedora"}, "manager": "dnf"},
    "arch": {"ids": {"arch"}, "manager": "pacman"},
    "opensuse-tumbleweed": {
        "ids": {"opensuse-tumbleweed", "opensuse"},
        "manager": "zypper",
    },
    "alpine-324": {"ids": {"alpine"}, "manager": "apk"},
}
REPORT = Path.home() / "distrohop-vm-report.json"
DATA_DEVICE = Path("/dev/vdb")
DATA_BYTES = 8 * 1024**3
SIZE_TOLERANCE = 32 * 1024**2


class SmokeFailure(RuntimeError):
    """A smoke-test assertion with an actionable message."""


def run(
    argv: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(argv),
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise SmokeFailure(
            "{} failed with exit {}{}".format(
                " ".join(argv),
                result.returncode,
                ": " + detail[-1000:] if detail else "",
            )
        )
    return result


def record(results: List[Dict[str, Any]], name: str, operation: Any) -> Any:
    started = time.monotonic()
    try:
        value = operation()
    except Exception as error:
        results.append(
            {
                "name": name,
                "ok": False,
                "seconds": round(time.monotonic() - started, 3),
                "detail": str(error),
            }
        )
        raise
    results.append(
        {
            "name": name,
            "ok": True,
            "seconds": round(time.monotonic() - started, 3),
        }
    )
    return value


def check_detection(lab_distro: str) -> Dict[str, Any]:
    result = run(("./bin/distrohop", "--cli", "list", "--json"), capture=True)
    try:
        inventory = json.loads(result.stdout)
    except ValueError as error:
        raise SmokeFailure("list --json returned invalid JSON") from error
    os_info = inventory.get("os") or {}
    expected = EXPECTED.get(lab_distro)
    if expected is None:
        raise SmokeFailure("no detection expectation for {}".format(lab_distro))
    if os_info.get("id") not in expected["ids"]:
        raise SmokeFailure(
            "detected distro {!r}, expected one of {}".format(
                os_info.get("id"), sorted(expected["ids"])
            )
        )
    if os_info.get("manager") != expected["manager"]:
        raise SmokeFailure(
            "detected manager {!r}, expected {!r}".format(
                os_info.get("manager"), expected["manager"]
            )
        )
    if inventory.get("platform") != "linux":
        raise SmokeFailure("guest was not detected as Linux")
    return inventory


def prepare_fixture() -> Path:
    fixture = Path.home() / ".codex-vm-test"
    fixture.mkdir(mode=0o700, exist_ok=True)
    token = fixture / "session.json"
    token.write_text(
        '{"fixture": true, "value": "not-a-real-token"}\n',
        encoding="utf-8",
    )
    token.chmod(0o600)
    return fixture


def backup_dry_run(fixture: Path) -> None:
    result = run(
        (
            "./bin/distrohop",
            "--cli",
            "backup",
            "--dry-run",
            "--no-browsers",
            "--ai-account",
            str(fixture),
            "--no-extras",
        ),
        capture=True,
    )
    if str(fixture) not in result.stdout or "DRY-RUN" not in result.stdout:
        raise SmokeFailure("dry-run did not include the synthetic AI fixture")


def _data_disk_size() -> int:
    result = run(
        ("sudo", "blockdev", "--getsize64", str(DATA_DEVICE)),
        capture=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError as error:
        raise SmokeFailure("could not read virtual data-disk size") from error


def validate_data_disk() -> None:
    if not DATA_DEVICE.is_block_device():
        raise SmokeFailure("{} is not a block device".format(DATA_DEVICE))
    size = _data_disk_size()
    if abs(size - DATA_BYTES) > SIZE_TOLERANCE:
        raise SmokeFailure(
            "{} has {} bytes; expected the dedicated 8 GiB lab disk".format(
                DATA_DEVICE, size
            )
        )
    root_source = run(("findmnt", "-n", "-o", "SOURCE", "/"), capture=True).stdout.strip()
    if root_source.startswith(str(DATA_DEVICE)):
        raise SmokeFailure("refusing to format the guest root device")
    mounted = run(
        ("findmnt", "-n", "-S", str(DATA_DEVICE)),
        check=False,
        capture=True,
    )
    if mounted.returncode == 0:
        raise SmokeFailure("{} is already mounted".format(DATA_DEVICE))


def real_backup(fixture: Path) -> Path:
    validate_data_disk()
    mountpoint = Path("/mnt/distrohop-target")
    run(("sudo", "mkdir", "-p", str(mountpoint)))
    run(("sudo", "mkfs.ext4", "-F", "-L", "DISTROHOP_TEST", str(DATA_DEVICE)))
    run(("sudo", "mount", str(DATA_DEVICE), str(mountpoint)))
    try:
        run(("sudo", "chown", "{}:{}".format(os.getuid(), os.getgid()), str(mountpoint)))
        run(
            (
                "./bin/distrohop",
                "--cli",
                "backup",
                "--target",
                str(mountpoint),
                "--no-browsers",
                "--ai-account",
                str(fixture),
                "--no-extras",
            )
        )
        bundles = sorted(mountpoint.glob("distrohop-*"))
        if len(bundles) != 1:
            raise SmokeFailure("expected one bundle, found {}".format(len(bundles)))
        bundle = bundles[0]
        verify = run(
            (
                "python3",
                "-c",
                "from pathlib import Path; "
                "from distrohop.vault.bundle import verify_bundle; "
                "raise SystemExit(0 if verify_bundle(Path({!r})) else 1)".format(
                    str(bundle)
                ),
            )
        )
        if verify.returncode:
            raise SmokeFailure("published bundle did not verify")
        return bundle
    finally:
        run(("sudo", "umount", str(mountpoint)), check=False)


def gui_child() -> int:
    import tkinter as tk

    from distrohop.ui.gui import DistrohopApp

    root = tk.Tk()
    app = DistrohopApp(root)
    root.update_idletasks()
    root.update()
    deadline = time.monotonic() + 15
    while app.inventory is None and time.monotonic() < deadline:
        root.update()
        time.sleep(0.05)
    if app.inventory is None:
        root.destroy()
        raise SmokeFailure("GUI inventory did not finish")
    initial_theme = app.theme_name
    initial_language = app.language
    app.toggle_theme()
    app.toggle_language()
    app.show_backup_selection()
    root.update_idletasks()
    root.update()
    if app.theme_name == initial_theme or app.language == initial_language:
        root.destroy()
        raise SmokeFailure("GUI theme/language toggle did not change state")
    if root.winfo_width() < 900 or root.winfo_height() < 600:
        root.destroy()
        raise SmokeFailure("GUI requested an implausibly small layout")
    root.destroy()
    return 0


def gui_smoke() -> None:
    result = run(
        (
            "xvfb-run",
            "-a",
            "-s",
            "-screen 0 1280x800x24",
            "python3",
            str(Path(__file__).resolve()),
            "--gui-child",
        ),
        capture=True,
    )
    if result.returncode:
        raise SmokeFailure("GUI smoke failed")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui-child", action="store_true")
    args = parser.parse_args(argv)
    if args.gui_child:
        return gui_child()
    lab_distro = os.environ.get("DISTROHOP_VM_DISTRO", "")
    results: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {
        "schema_version": 1,
        "distro": lab_distro,
        "python": sys.version.split()[0],
        "results": results,
        "ok": False,
    }
    try:
        record(
            results,
            "unit-suite",
            lambda: run(
                ("python3", "-m", "unittest", "discover", "-s", "tests", "-v")
            ),
        )
        inventory = record(
            results,
            "distro-detection",
            lambda: check_detection(lab_distro),
        )
        report["detected_os"] = inventory["os"]
        fixture = record(results, "synthetic-fixture", prepare_fixture)
        record(results, "backup-dry-run", lambda: backup_dry_run(fixture))
        bundle = record(results, "real-backup-and-verify", lambda: real_backup(fixture))
        report["bundle_name"] = bundle.name
        record(results, "gui-xvfb", gui_smoke)
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        REPORT.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        REPORT.chmod(0o600)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
