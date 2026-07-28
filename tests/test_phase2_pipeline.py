from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distrohop.capture.chromium_linux import capture_profile as capture_chromium
from distrohop.capture import extras
from distrohop.capture.extras import PACKAGE_COMMANDS
from distrohop.capture.firefox import capture_profile as capture_firefox
from distrohop.capture.profile_raw import copy_path
from distrohop.core.engine import plan_backup, run_backup
from distrohop.core.selection import Selection
from distrohop.vault.bundle import verify_bundle
from distrohop.detect.linux_profiles import PROFILES


def _encrypt_v10(plaintext: bytes) -> bytes:
    key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)
    result = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-e",
            "-K",
            key.hex(),
            "-iv",
            (b" " * 16).hex(),
        ],
        input=plaintext,
        check=True,
        capture_output=True,
    )
    return b"v10" + result.stdout


class BrowserCaptureTests(unittest.TestCase):
    def test_chromium_synthetic_profile_captures_raw_and_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "Default"
            network = profile / "Network"
            network.mkdir(parents=True)
            cookies = sqlite3.connect(network / "Cookies")
            cookies.execute(
                "CREATE TABLE cookies "
                "(host_key TEXT, name TEXT, path TEXT, encrypted_value BLOB, "
                "expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER, "
                "samesite INTEGER, creation_utc INTEGER, last_access_utc INTEGER)"
            )
            host = ".example.com"
            plaintext = hashlib.sha256(host.encode()).digest() + b"session"
            cookies.execute(
                "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (host, "sid", "/", _encrypt_v10(plaintext), 13300000000000000, 1, 1, 1, 13200000000000000, 13250000000000000),
            )
            cookies.commit()
            cookies.close()
            logins = sqlite3.connect(profile / "Login Data")
            logins.execute(
                "CREATE TABLE logins "
                "(origin_url TEXT, action_url TEXT, username_value TEXT, "
                "password_value BLOB, date_created INTEGER, date_last_used INTEGER)"
            )
            logins.execute(
                "INSERT INTO logins VALUES (?, ?, ?, ?, ?, ?)",
                ("https://example.com", "", "alice", _encrypt_v10(b"password"), 1, 2),
            )
            logins.commit()
            logins.close()
            (profile / "Bookmarks").write_text(
                json.dumps(
                    {"roots": {"bookmark_bar": {"children": [
                        {"type": "url", "name": "Example", "url": "https://example.com"}
                    ]}}}
                ),
                encoding="utf-8",
            )
            destination = root / "captured"

            summary = capture_chromium(profile, destination, browser_id="chromium")

            self.assertEqual(summary["cookies"], 1)
            self.assertEqual(summary["logins"], 1)
            self.assertEqual(summary["bookmarks"], 1)
            cookie = json.loads(
                (destination / "neutral" / "cookies.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(cookie["value"], "session")
            self.assertTrue((destination / "neutral" / "logins.csv").is_file())
            self.assertTrue((destination / "raw" / "Network" / "Cookies").is_file())

    def test_firefox_synthetic_profile_captures_plain_data_without_nss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            cookies = sqlite3.connect(profile / "cookies.sqlite")
            cookies.execute(
                "CREATE TABLE moz_cookies "
                "(host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, "
                "isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER, "
                "creationTime INTEGER, lastAccessed INTEGER)"
            )
            cookies.execute(
                "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (".example.org", "sid", "plain", "/", 2000000000, 1, 1, 0, 10, 11),
            )
            cookies.commit()
            cookies.close()
            places = sqlite3.connect(profile / "places.sqlite")
            places.execute(
                "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)"
            )
            places.execute(
                "CREATE TABLE moz_bookmarks "
                "(id INTEGER PRIMARY KEY, fk INTEGER, title TEXT, dateAdded INTEGER, type INTEGER)"
            )
            places.execute("INSERT INTO moz_places VALUES (1, 'https://example.org', 'Site')")
            places.execute("INSERT INTO moz_bookmarks VALUES (1, 1, 'Saved', 123, 1)")
            places.commit()
            places.close()
            destination = root / "captured"

            summary = capture_firefox(profile, destination, decrypt_logins=False)

            self.assertEqual(summary["cookies"], 1)
            self.assertEqual(summary["bookmarks"], 1)
            self.assertTrue((destination / "raw" / "cookies.sqlite").is_file())
            self.assertIn(
                '"value": "plain"',
                (destination / "neutral" / "cookies.jsonl").read_text(encoding="utf-8"),
            )


class BackupEngineTests(unittest.TestCase):
    def test_every_linux_profile_has_a_package_inventory_strategy(self) -> None:
        managers = {profile.manager for profile in PROFILES if profile.manager}
        self.assertEqual(managers.difference(PACKAGE_COMMANDS), set())

    def test_dry_run_is_file_by_file_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / ".codex"
            account.mkdir()
            (account / "auth.json").write_text("{}", encoding="utf-8")
            target = root / "target"
            inventory = {
                "platform": "linux",
                "os": {"id": "test", "name": "Test Linux", "manager": "apt"},
                "browsers": [],
                "ai_accounts": [{"tool": "codex", "slot": "codex", "path": str(account)}],
                "disks": [],
                "warnings": [],
            }
            selection = Selection(ai_accounts=(str(account),))

            plan = plan_backup(selection, (target,), inventory=inventory, home=root)

            self.assertIn(str(account / "auth.json"), plan.sources)
            self.assertFalse(target.exists())

    def test_real_backup_is_verified_in_two_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / ".codex"
            account.mkdir()
            (account / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
            inventory = {
                "platform": "linux",
                "os": {"id": "test", "name": "Test Linux", "manager": "apt"},
                "browsers": [],
                "ai_accounts": [{"tool": "codex", "slot": "codex", "path": str(account)}],
                "disks": [],
                "warnings": [],
            }
            targets = (root / "target-one", root / "target-two")
            plan = plan_backup(
                Selection(ai_accounts=(str(account),)),
                targets,
                inventory=inventory,
                home=root,
                bundle_name="distrohop-test",
            )

            result = run_backup(plan)

            self.assertEqual(len(result["destinations"]), 2)
            for destination in map(Path, result["destinations"]):
                self.assertTrue(verify_bundle(destination))
                self.assertEqual(
                    (destination / "ai" / "codex" / "codex" / "auth.json").read_text(encoding="utf-8"),
                    '{"token":"secret"}',
                )


class ExtrasCaptureTests(unittest.TestCase):
    def test_profile_symlinks_are_materialized_and_cycles_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            (external / "config").write_text("portable", encoding="utf-8")
            source = root / "source"
            source.mkdir()
            (source / "linked").symlink_to(external, target_is_directory=True)
            (external / "cycle").symlink_to(source, target_is_directory=True)
            destination = root / "destination"

            warnings = copy_path(source, destination)

            self.assertEqual(
                (destination / "linked" / "config").read_text(encoding="utf-8"),
                "portable",
            )
            self.assertFalse((destination / "linked").is_symlink())
            self.assertTrue(any("ciclo de symlink" in warning for warning in warnings))

    @patch("distrohop.capture.extras.shutil.which", return_value="/usr/bin/tool")
    def test_package_capture_records_distro_specific_commands(self, _which) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "packages"

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command, 0, stdout="pkg-one\npkg-two\n", stderr=""
                )

            manifest = extras.capture_packages("pacman", destination, runner=runner)

            self.assertEqual(manifest["manager"], "pacman")
            self.assertEqual(manifest["inventories"]["explicit"]["status"], "captured")
            self.assertTrue((destination / "explicit.txt").is_file())
            self.assertTrue((destination / "foreign.txt").is_file())
            self.assertTrue((destination / "flatpak.txt").is_file())
