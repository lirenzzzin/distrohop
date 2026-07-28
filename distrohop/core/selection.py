"""Selections shared by CLI and GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class Selection:
    browser_profiles: Tuple[str, ...] = field(default_factory=tuple)
    ai_accounts: Tuple[str, ...] = field(default_factory=tuple)
    extras: Tuple[str, ...] = field(default_factory=tuple)
