"""Read-only browser process detection used by destructive restore gates."""

from __future__ import annotations

import csv
import os
import platform
import subprocess
from io import StringIO
from pathlib import Path
from typing import Callable, Mapping, Optional, Set


PROCESS_NAMES: Mapping[str, Set[str]] = {
    "brave": {"brave", "brave-browser", "brave-browser-stable"},
    "chrome": {"chrome", "google-chrome", "google-chrome-stable"},
    "chromium": {"chromium", "chromium-browser"},
    "edge": {"microsoft-edge", "microsoft-edge-stable", "msedge"},
    "vivaldi": {"vivaldi", "vivaldi-bin"},
    "opera": {"opera"},
    "firefox": {"firefox", "firefox-bin"},
    "zen": {"zen", "zen-browser", "zen-bin"},
    "librewolf": {"librewolf", "librewolf-bin"},
    "floorp": {"floorp", "floorp-bin"},
    "waterfox": {"waterfox", "waterfox-bin"},
}


def _windows_processes(
    runner: Callable[..., subprocess.CompletedProcess],
) -> Set[str]:
    try:
        result = runner(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode:
        return set()
    names: Set[str] = set()
    for row in csv.reader(StringIO(result.stdout or "")):
        if row:
            names.add(row[0].strip().casefold())
    return names


def is_browser_running(
    browser_id: str,
    proc_root: Path = Path("/proc"),
    *,
    system: Optional[str] = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    expected = PROCESS_NAMES.get(browser_id, {browser_id})
    if (system or platform.system()).casefold() == "windows":
        windows_expected = set()
        for name in expected:
            normalized = name.casefold()
            windows_expected.add(
                normalized if normalized.endswith(".exe") else normalized + ".exe"
            )
        return bool(windows_expected & _windows_processes(runner))
    current = os.getpid()
    try:
        processes = proc_root.iterdir()
    except OSError:
        return False
    for process in processes:
        if not process.name.isdigit() or int(process.name) == current:
            continue
        try:
            executable = (process / "exe").resolve().name
            command = (process / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if executable in expected or command in expected:
            return True
    return False
