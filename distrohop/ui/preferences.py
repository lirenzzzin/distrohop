"""Small, dependency-free persistence for GUI preferences."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Mapping, Optional


def preferences_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("DISTROHOP_PREFERENCES")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Distrohop" / "preferences.json"
    base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "distrohop" / "preferences.json"


def load_preferences(path: Optional[Path] = None) -> Dict[str, str]:
    target = path or preferences_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value[key])
        for key in ("language", "theme")
        if isinstance(value.get(key), str)
    }


def save_preferences(
    values: Mapping[str, str],
    path: Optional[Path] = None,
) -> None:
    target = path or preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: str(values[key])
        for key in ("language", "theme")
        if key in values
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".preferences-",
        suffix=".json",
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
