"""Portable, engine-neutral representations used by cross-engine restore."""

from __future__ import annotations

import html
import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def _write_jsonl(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def write_cookies(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    _write_jsonl(records, path)


def write_logins(records: Iterable[Mapping[str, Any]], path: Path) -> None:
    rows = [dict(record) for record in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    preferred = [
        "origin",
        "action",
        "username",
        "password",
        "date_created",
        "date_last_used",
        "date_password_modified",
    ]
    fields = preferred + sorted(
        {key for row in rows for key in row}.difference(preferred)
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_bookmarks(
    records: Iterable[Mapping[str, Any]],
    path: Path,
    title: str = "Distrohop bookmarks",
) -> None:
    """Write Netscape Bookmark File Format understood by major browsers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>{}</TITLE>".format(html.escape(title)),
        "<H1>{}</H1>".format(html.escape(title)),
        "<DL><p>",
    ]
    for record in records:
        url = html.escape(str(record.get("url", "")), quote=True)
        label = html.escape(str(record.get("title") or record.get("url") or "Sem título"))
        added = record.get("date_added")
        attribute = ' ADD_DATE="{}"'.format(html.escape(str(added), quote=True)) if added else ""
        lines.append('    <DT><A HREF="{}"{}>{}</A>'.format(url, attribute, label))
    lines.extend(("</DL><p>", ""))
    path.write_text("\n".join(lines), encoding="utf-8")
