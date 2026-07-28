"""Read-only Windows environment detection."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Dict, Mapping, Optional


def detect(
    environ: Optional[Mapping[str, str]] = None,
    which=shutil.which,
) -> Dict[str, object]:
    env = os.environ if environ is None else environ
    local = Path(env.get("LOCALAPPDATA", ""))
    roaming = Path(env.get("APPDATA", ""))
    return {
        "name": f"Windows {platform.release()}",
        "version": platform.version(),
        "manager": "winget" if which("winget") else ("choco" if which("choco") else None),
        "strategy": "imperativa",
        "local_app_data": str(local),
        "app_data": str(roaming),
        "user_profile": env.get("USERPROFILE", ""),
    }
