"""Atomic same-browser profile restore with a mandatory safety copy."""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Optional


def apply_raw_profile(
    source: Path,
    target: Path,
    *,
    backup_name: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    if not source.is_dir():
        raise FileNotFoundError("perfil raw não encontrado: {}".format(source))
    target_parent = target.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    staging = target_parent / ".{}.partial-{}".format(target.name, uuid.uuid4().hex)
    backup = target_parent / (
        backup_name
        or "{}.distrohop-before-{}".format(target.name, time.strftime("%Y%m%d-%H%M%S"))
    )
    if backup.exists():
        raise FileExistsError(str(backup))
    previous: Optional[Path] = None
    try:
        shutil.copytree(source, staging, symlinks=False, copy_function=shutil.copy2)
        if target.exists():
            os.replace(target, backup)
            previous = backup
        try:
            os.replace(staging, target)
        except Exception:
            if previous is not None and not target.exists():
                os.replace(previous, target)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return {
        "target": str(target),
        "previous_profile": str(previous) if previous is not None else None,
    }
