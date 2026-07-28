"""Read-only browser process detection used by destructive restore gates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Set


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


def is_browser_running(browser_id: str, proc_root: Path = Path("/proc")) -> bool:
    expected = PROCESS_NAMES.get(browser_id, {browser_id})
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
