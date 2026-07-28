"""Platform selection. This must precede every OS-specific probe."""

from __future__ import annotations

import platform
from typing import Optional


def current_platform(system: Optional[str] = None) -> str:
    name = system if system is not None else platform.system()
    normalized = name.casefold()
    if normalized == "linux":
        return "linux"
    if normalized == "windows":
        return "windows"
    raise RuntimeError(f"Plataforma não suportada: {name or 'desconhecida'}")
