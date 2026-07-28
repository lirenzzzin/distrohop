from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from distrohop.restore.apply_raw import apply_raw_profile
from distrohop.restore.installer import plan_install
from distrohop.restore.processes import is_browser_running
from distrohop.core.engine import plan_restore, run_restore
from distrohop.vault.bundle import (
    assemble_bundle,
    materialize_payload,
    verify_materialized_payload,
)


class BundleReaderTests(unittest.TestCase):
    def test_plain_and_encrypted_payloads_are_materialized_and_verified(self) -> None:
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = root / "payload"
                raw = payload / "browsers" / "firefox" / "default" / "raw"
                raw.mkdir(parents=True)
                (raw / "prefs.js").write_text("user_pref('x', true);", encoding="utf-8")
                bundle = root / "bundle"
                assemble_bundle(
                    payload,
                    bundle,
                    metadata={
                        "browsers": [{
                            "id": "firefox",
                            "engine": "firefox",
                            "profile": "default",
                            "bundle_path": "browsers/firefox/default",
                        }]
                    },
                    encrypted=encrypted,
                    password="secret" if encrypted else None,
                )

                with materialize_payload(
                    bundle, password="secret" if encrypted else None
                ) as opened:
                    self.assertTrue(verify_materialized_payload(bundle, opened))
                    self.assertEqual(
                        (opened / "browsers/firefox/default/raw/prefs.js").read_text(
                            encoding="utf-8"
                        ),
                        "user_pref('x', true);",
                    )


class RawRestoreTests(unittest.TestCase):
    def test_raw_restore_is_atomic_and_preserves_previous_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            (raw / "cookies.sqlite").write_text("new", encoding="utf-8")
            target = root / "profile"
            target.mkdir()
            (target / "cookies.sqlite").write_text("old", encoding="utf-8")
            (target / "prefs.js").write_text("keep-in-backup", encoding="utf-8")

            result = apply_raw_profile(raw, target)

            backup = Path(result["previous_profile"])
            self.assertEqual((target / "cookies.sqlite").read_text(encoding="utf-8"), "new")
            self.assertFalse((target / "prefs.js").exists())
            self.assertEqual(
                (backup / "prefs.js").read_text(encoding="utf-8"),
                "keep-in-backup",
            )
            self.assertFalse(any(".partial-" in path.name for path in root.iterdir()))

    def test_raw_restore_refuses_to_overwrite_its_safety_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            (raw / "data").write_text("new", encoding="utf-8")
            target = root / "profile"
            target.mkdir()

            first = apply_raw_profile(raw, target, backup_name="fixed-before")
            self.assertEqual(Path(first["previous_profile"]).name, "fixed-before")
            with self.assertRaises(FileExistsError):
                apply_raw_profile(raw, target, backup_name="fixed-before")


class RestoreEngineTests(unittest.TestCase):
    def _bundle(self, root: Path, *, encrypted: bool = False) -> Path:
        payload = root / "payload"
        raw = payload / "browsers" / "firefox" / "default" / "raw"
        raw.mkdir(parents=True)
        (raw / "cookies.sqlite").write_text("restored-cookie-db", encoding="utf-8")
        bundle = root / "bundle"
        assemble_bundle(
            payload,
            bundle,
            metadata={
                "source": {"platform": "linux", "distro": {"id": "test"}},
                "browsers": [{
                    "id": "firefox",
                    "name": "Firefox",
                    "engine": "firefox",
                    "profile": "default",
                    "bundle_path": "browsers/firefox/default",
                }],
            },
            encrypted=encrypted,
            password="secret" if encrypted else None,
        )
        return bundle

    def test_restore_plan_is_read_only_and_file_by_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            target = root / "target-profile"
            inventory = {
                "platform": "linux",
                "os": {"id": "test", "manager": "apt", "family": "debian"},
                "browsers": [{
                    "id": "firefox",
                    "engine": "firefox",
                    "installed": True,
                    "profiles": [{"name": "clean", "path": str(target)}],
                }],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }

            plan = plan_restore(bundle, inventory=inventory)

            self.assertEqual(plan.target_profile, target)
            self.assertTrue(any("cookies.sqlite" in source for source in plan.sources))
            self.assertIn(str(target / "cookies.sqlite"), plan.outputs)
            self.assertFalse(target.exists())

    def test_restore_run_keeps_old_profile_and_applies_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root)
            target = root / "target-profile"
            target.mkdir()
            (target / "old.txt").write_text("old", encoding="utf-8")
            inventory = {
                "platform": "linux",
                "os": {"id": "test", "manager": "apt", "family": "debian"},
                "browsers": [{
                    "id": "firefox",
                    "engine": "firefox",
                    "binary": "/usr/bin/firefox",
                    "installed": True,
                    "profiles": [{"name": "clean", "path": str(target)}],
                }],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }
            plan = plan_restore(bundle, inventory=inventory)

            result = run_restore(plan, running_check=lambda _browser_id: False)

            self.assertEqual(
                (target / "cookies.sqlite").read_text(encoding="utf-8"),
                "restored-cookie-db",
            )
            previous = Path(result["previous_profile"])
            self.assertEqual((previous / "old.txt").read_text(encoding="utf-8"), "old")

    def test_encrypted_restore_requires_and_accepts_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self._bundle(root, encrypted=True)
            target = root / "target-profile"
            inventory = {
                "platform": "linux",
                "os": {"id": "test", "manager": "apt", "family": "debian"},
                "browsers": [{
                    "id": "firefox",
                    "engine": "firefox",
                    "installed": True,
                    "profiles": [{"name": "clean", "path": str(target)}],
                }],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }
            plan = plan_restore(bundle, inventory=inventory)

            with self.assertRaisesRegex(ValueError, "exige senha"):
                run_restore(plan, running_check=lambda _browser_id: False)
            result = run_restore(
                plan,
                password="secret",
                running_check=lambda _browser_id: False,
            )

            self.assertEqual(result["target"], str(target))


class InstallerTests(unittest.TestCase):
    def test_native_recipe_is_capability_checked_before_install(self) -> None:
        os_info = {
            "family": "arch",
            "manager": "pacman",
            "strategy": "imperativa",
            "install_argv": ["pacman", "-S", "--needed", "{package}"],
        }
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        with patch("distrohop.restore.installer.shutil.which", return_value="/bin/pacman"), patch(
            "distrohop.restore.installer.os.geteuid", return_value=1000
        ):
            command = plan_install("firefox", os_info, runner=runner)

        self.assertEqual(
            command,
            ("sudo", "pacman", "-S", "--needed", "firefox"),
        )
        self.assertEqual(runner.call_args.args[0], ["pacman", "-Si", "firefox"])

    def test_unknown_native_package_falls_back_to_flatpak(self) -> None:
        os_info = {
            "family": "generic",
            "manager": "",
            "strategy": "fallback",
        }
        with patch(
            "distrohop.restore.installer.shutil.which",
            side_effect=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
        ):
            command = plan_install("firefox", os_info)

        self.assertEqual(
            command,
            (
                "flatpak",
                "install",
                "--user",
                "--noninteractive",
                "-y",
                "flathub",
                "org.mozilla.firefox",
            ),
        )


class ProcessGateTests(unittest.TestCase):
    def test_exact_process_name_blocks_restore_without_substring_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            exact = proc / "101"
            exact.mkdir()
            (exact / "comm").write_text("firefox\n", encoding="utf-8")
            similar = proc / "102"
            similar.mkdir()
            (similar / "comm").write_text("firefox-helper\n", encoding="utf-8")

            self.assertTrue(is_browser_running("firefox", proc))
            (exact / "comm").write_text("not-firefox\n", encoding="utf-8")
            self.assertFalse(is_browser_running("firefox", proc))
