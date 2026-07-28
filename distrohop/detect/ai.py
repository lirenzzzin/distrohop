"""Discover local AI tool accounts without reading their secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List


PREFIXES = {
    ".claude": "claude",
    ".codex": "codex",
    ".gemini": "gemini",
    ".kimi-code": "kimi-code",
}


def detect(home: Path) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    candidates = set()
    for prefix in PREFIXES:
        candidates.update(home.glob(f"{prefix}*"))
    claude_json = home / ".claude.json"
    if claude_json.exists():
        candidates.add(claude_json)
    for path in sorted(candidates, key=lambda item: item.name.casefold()):
        matched = next((p for p in PREFIXES if path.name.startswith(p)), None)
        tool = PREFIXES.get(matched, "claude" if path.name == ".claude.json" else "")
        if tool:
            found.append({"tool": tool, "slot": path.name.lstrip("."), "path": str(path)})
    return found
