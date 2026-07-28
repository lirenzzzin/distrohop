"""Structured progress events emitted by the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class Event:
    kind: str
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)


EventCallback = Callable[[Event], None]


def discard_event(_event: Event) -> None:
    """Default callback for callers that do not need progress."""
