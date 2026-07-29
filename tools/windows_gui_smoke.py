#!/usr/bin/env python3
"""Interactive Windows GUI smoke that leaves a machine-readable report."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--hold", type=int, default=60)
    arguments = parser.parse_args(argv)
    report_path = Path(arguments.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": False,
        "platform": os.name,
        "status": "starting",
    }
    try:
        import tkinter as tk

        from distrohop.ui.gui import DistrohopApp

        root = tk.Tk()
        app = DistrohopApp(root)
        root.update_idletasks()
        root.update()
        deadline = time.monotonic() + 30
        while app.inventory is None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.05)
        if app.inventory is None:
            raise RuntimeError("GUI inventory did not finish")
        initial_theme = app.theme_name
        initial_language = app.language
        app.toggle_theme()
        app.toggle_language()
        app.show_backup_selection()
        root.update_idletasks()
        root.update()
        if app.theme_name == initial_theme:
            raise RuntimeError("theme toggle did not change state")
        if app.language == initial_language:
            raise RuntimeError("language toggle did not change state")
        if root.winfo_width() < 900 or root.winfo_height() < 600:
            raise RuntimeError("GUI layout is smaller than expected")
        report.update(
            {
                "ok": True,
                "status": "ready",
                "theme": app.theme_name,
                "language": app.language,
                "width": root.winfo_width(),
                "height": root.winfo_height(),
                "detected_browsers": [
                    item["id"] for item in app.inventory["browsers"]
                ],
            }
        )
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        deadline = time.monotonic() + max(1, arguments.hold)
        while time.monotonic() < deadline:
            root.update()
            time.sleep(0.05)
        root.destroy()
        report["status"] = "complete"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
