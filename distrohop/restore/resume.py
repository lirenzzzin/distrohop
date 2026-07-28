"""Durable, clear-text continuation state for manual/reboot restore gates."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping


STATE_NAME = ".distrohop-resume.json"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


def write_resume_state(bundle: Path, state: Mapping[str, Any]) -> Path:
    bundle = Path(bundle)
    if not bundle.is_dir():
        raise FileNotFoundError(str(bundle))
    payload = dict(state)
    payload["format_version"] = 1
    destination = bundle / STATE_NAME
    temporary = bundle / ".{}.{}.partial".format(STATE_NAME, uuid.uuid4().hex)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def load_resume_state(bundle: Path) -> Dict[str, Any]:
    path = Path(bundle) / STATE_NAME
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ValueError("estado de resume inválido ou ausente: {}".format(error)) from error
    required = {
        "format_version",
        "browser_id",
        "source_profile",
        "target_browser_id",
        "target_profile",
        "preparation",
    }
    if not isinstance(state, dict) or state.get("format_version") != 1:
        raise ValueError("estado de resume usa formato desconhecido")
    missing = required.difference(state)
    if missing:
        raise ValueError(
            "estado de resume incompleto: {}".format(", ".join(sorted(missing)))
        )
    return state


def clear_resume_state(bundle: Path) -> None:
    try:
        (Path(bundle) / STATE_NAME).unlink()
    except FileNotFoundError:
        pass
