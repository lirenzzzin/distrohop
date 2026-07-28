from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from distrohop.capture.chromium_linux import decrypt_chromium_value
from distrohop.core.engine import plan_restore, run_restore
from distrohop.restore.apply_neutral import (
    CHROMIUM_EPOCH_OFFSET_SECONDS,
    apply_neutral_profile,
    chromium_utc_to_firefox_ms,
    firefox_ms_to_chromium_utc,
)
from distrohop.vault.bundle import assemble_bundle


def _neutral_files(root: Path, cookie: dict) -> Path:
    root.mkdir(parents=True)
    (root / "cookies.jsonl").write_text(
        json.dumps(cookie, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with (root / "logins.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("origin", "action", "username", "password"),
        )
        writer.writeheader()
        writer.writerow({
            "origin": "https://example.test",
            "action": "https://example.test/login",
            "username": "alice",
            "password": "secret",
        })
    (root / "bookmarks.html").write_text(
        "\n".join((
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
            "<DL><p>",
            '  <DT><A HREF="https://example.test/path?a=1&amp;b=2" '
            'ADD_DATE="1700000000">Example</A>',
            "</DL><p>",
            "",
        )),
        encoding="utf-8",
    )
    return root


class TimeConversionTests(unittest.TestCase):
    def test_chromium_epoch_conversion_is_exact_and_round_trips(self) -> None:
        chromium = (CHROMIUM_EPOCH_OFFSET_SECONDS + 1_700_000_000) * 1_000_000

        firefox = chromium_utc_to_firefox_ms(chromium)

        self.assertEqual(firefox, 1_700_000_000_000)
        self.assertEqual(firefox_ms_to_chromium_utc(firefox), chromium)
        self.assertEqual(chromium_utc_to_firefox_ms(0), 0)
        self.assertEqual(firefox_ms_to_chromium_utc(0), 0)


class NeutralApplyTests(unittest.TestCase):
    def test_chromium_cookie_and_bookmark_are_applied_to_firefox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral = _neutral_files(
                root / "neutral",
                {
                    "host": ".example.test",
                    "name": "session",
                    "value": "token",
                    "path": "/",
                    "expires_utc": (
                        CHROMIUM_EPOCH_OFFSET_SECONDS + 1_900_000_000
                    ) * 1_000_000,
                    "creation_utc": (
                        CHROMIUM_EPOCH_OFFSET_SECONDS + 1_700_000_000
                    ) * 1_000_000,
                    "last_access_utc": (
                        CHROMIUM_EPOCH_OFFSET_SECONDS + 1_800_000_000
                    ) * 1_000_000,
                    "secure": True,
                    "http_only": True,
                    "same_site": 2,
                    "source_engine": "chromium",
                },
            )
            target = root / "firefox-profile"
            target.mkdir()
            (target / "keep.txt").write_text("old-profile", encoding="utf-8")
            (target / "lock").symlink_to("127.0.0.1:+1")

            result = apply_neutral_profile(
                neutral,
                target,
                source_engine="chromium",
                target_engine="firefox",
            )

            with closing(sqlite3.connect(str(target / "cookies.sqlite"))) as connection:
                row = connection.execute(
                    "SELECT host, name, value, expiry, creationTime, "
                    "lastAccessed, isSecure, isHttpOnly, sameSite "
                    "FROM moz_cookies"
                ).fetchone()
            self.assertEqual(
                row,
                (
                    ".example.test",
                    "session",
                    "token",
                    1_900_000_000_000,
                    1_700_000_000_000,
                    1_800_000_000_000,
                    1,
                    1,
                    2,
                ),
            )
            with closing(sqlite3.connect(str(target / "places.sqlite"))) as connection:
                bookmark = connection.execute(
                    "SELECT url, title FROM moz_places"
                ).fetchone()
            self.assertEqual(
                bookmark,
                ("https://example.test/path?a=1&b=2", "Example"),
            )
            self.assertTrue((target / "distrohop-logins.csv").is_file())
            self.assertTrue(any("manual" in warning.casefold() for warning in result["warnings"]))
            previous = Path(result["previous_profile"])
            self.assertEqual(
                (previous / "keep.txt").read_text(encoding="utf-8"),
                "old-profile",
            )

    def test_firefox_cookie_gets_modern_chromium_domain_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral = _neutral_files(
                root / "neutral",
                {
                    "host": ".example.test",
                    "name": "session",
                    "value": "token",
                    "path": "/",
                    "expiry": 1_900_000_000_000,
                    "creation_time": 1_700_000_000_000,
                    "last_accessed": 1_800_000_000_000,
                    "secure": True,
                    "http_only": True,
                    "same_site": 1,
                    "source_engine": "firefox",
                },
            )
            target = root / "chromium-profile"
            network = target / "Network"
            network.mkdir(parents=True)
            with closing(sqlite3.connect(str(network / "Cookies"))) as connection:
                connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO meta VALUES('version', '24')")
            (target / "Bookmarks").write_text(
                json.dumps({
                    "checksum": "",
                    "roots": {
                        "bookmark_bar": {
                            "children": [],
                            "name": "Bookmarks bar",
                            "type": "folder",
                        }
                    },
                    "version": 1,
                }),
                encoding="utf-8",
            )

            apply_neutral_profile(
                neutral,
                target,
                source_engine="firefox",
                target_engine="chromium",
            )

            with closing(
                sqlite3.connect(str(target / "Network" / "Cookies"))
            ) as connection:
                row = connection.execute(
                    "SELECT host_key, name, encrypted_value, expires_utc, "
                    "creation_utc, last_access_utc FROM cookies"
                ).fetchone()
            self.assertEqual(row[0:2], (".example.test", "session"))
            self.assertEqual(
                decrypt_chromium_value(
                    row[2],
                    host_key=".example.test",
                    is_cookie=True,
                ),
                "token",
            )
            self.assertEqual(
                row[3],
                (CHROMIUM_EPOCH_OFFSET_SECONDS + 1_900_000_000) * 1_000_000,
            )
            bookmarks = json.loads((target / "Bookmarks").read_text(encoding="utf-8"))
            imported = bookmarks["roots"]["bookmark_bar"]["children"][0]
            self.assertEqual(imported["url"], "https://example.test/path?a=1&b=2")


class CrossEnginePlanTests(unittest.TestCase):
    def test_engine_selects_neutral_mode_and_target_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            neutral = _neutral_files(
                payload / "browsers" / "brave" / "Default" / "neutral",
                {
                    "host": ".example.test",
                    "name": "session",
                    "value": "token",
                    "path": "/",
                    "expires_utc": 0,
                    "source_engine": "chromium",
                },
            )
            self.assertTrue(neutral.is_dir())
            raw = payload / "browsers" / "brave" / "Default" / "raw"
            raw.mkdir()
            (raw / "Preferences").write_text("{}", encoding="utf-8")
            bundle = root / "bundle"
            assemble_bundle(
                payload,
                bundle,
                metadata={
                    "source": {"platform": "linux"},
                    "browsers": [{
                        "id": "brave",
                        "name": "Brave",
                        "engine": "chromium",
                        "profile": "Default",
                        "bundle_path": "browsers/brave/Default",
                    }],
                },
                encrypted=False,
            )
            target = root / "zen-profile"
            inventory = {
                "platform": "linux",
                "os": {"family": "arch", "strategy": "imperativa"},
                "browsers": [{
                    "id": "zen",
                    "name": "Zen",
                    "engine": "firefox",
                    "installed": True,
                    "profiles": [{"name": "Default", "path": str(target)}],
                }],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }

            plan = plan_restore(
                bundle,
                browser_id="brave",
                target_browser_id="zen",
                inventory=inventory,
            )
            result = run_restore(
                plan,
                running_check=lambda browser_id: browser_id == "brave",
            )

            self.assertEqual(plan.mode, "neutral")
            self.assertEqual(plan.target_browser_id, "zen")
            self.assertEqual(result["mode"], "neutral")
            self.assertTrue((target / "cookies.sqlite").is_file())
