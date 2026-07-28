"""Module entry point. Phase 1 exposes the CLI; GUI arrives in phase 5."""

from __future__ import annotations

from distrohop.ui.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
