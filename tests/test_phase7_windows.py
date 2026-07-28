from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from distrohop import bootstrap
from distrohop.capture.aesgcm import encrypt
from distrohop.capture.chromium_win import decrypt_chromium_bytes
from distrohop.capture.chromium_win import (
    AppBoundEncryption,
    decrypt_chromium_bytes,
    load_master_key,
)
from distrohop.capture.neutral import write_bookmarks, write_cookies, write_logins
from distrohop.core.engine import plan_backup, run_backup
from distrohop.core.selection import Selection
from distrohop.detect import browsers, disks
from distrohop.restore.apply_neutral import apply_neutral_profile
from distrohop.restore.win_installer import plan_install
from distrohop.restore.processes import is_browser_running
from distrohop.vault.crypto import CryptoError, decrypt_archive, encrypt_tree


class WindowsChromiumCryptoTests(unittest.TestCase):
    def test_dpapi_master_key_and_v10_aes_gcm_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local_state = Path(directory) / "Local State"
            local_state.write_text(
                json.dumps({
                    "os_crypt": {
                        "encrypted_key": base64.b64encode(
                            b"DPAPI" + b"wrapped"
                        ).decode("ascii")
                    }
                }),
                encoding="utf-8",
            )
            key = bytes(range(32))
            self.assertEqual(
                load_master_key(
                    local_state,
                    dpapi_unprotect=lambda value: key
                    if value == b"wrapped"
                    else b"",
                ),
                key,
            )
            nonce = bytes(range(12))
            ciphertext, tag = encrypt(key, nonce, b"windows-cookie")
            value = b"v10" + nonce + ciphertext + tag
            self.assertEqual(
                decrypt_chromium_bytes(
                    value,
                    master_key=key,
                    dpapi_unprotect=lambda value: value,
                ),
                b"windows-cookie",
            )

    def test_app_bound_key_is_reported_instead_of_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local_state = Path(directory) / "Local State"
            local_state.write_text(
                json.dumps({
                    "os_crypt": {
                        "app_bound_encrypted_key": base64.b64encode(
                            b"APPB" + b"opaque"
                        ).decode("ascii")
                    }
                }),
                encoding="utf-8",
            )
            with self.assertRaises(AppBoundEncryption):
                load_master_key(local_state, dpapi_unprotect=lambda value: value)

    def test_cross_engine_cookie_is_written_with_windows_aes_gcm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral = root / "neutral"
            write_cookies(
                [{
                    "host": ".example.com",
                    "name": "session",
                    "value": "portable",
                    "path": "/",
                    "expiry": 2_000_000_000_000,
                    "source_engine": "firefox",
                }],
                neutral / "cookies.jsonl",
            )
            write_logins([], neutral / "logins.csv")
            write_bookmarks([], neutral / "bookmarks.html")
            target = root / "User Data" / "Default"
            target.mkdir(parents=True)
            key = bytes(range(32))

            with patch(
                "distrohop.restore.apply_neutral.chromium_win.load_master_key",
                return_value=key,
            ):
                result = apply_neutral_profile(
                    neutral,
                    target,
                    source_engine="firefox",
                    target_engine="chromium",
                    target_platform="windows",
                )

            connection = sqlite3.connect(str(target / "Cookies"))
            try:
                encrypted = connection.execute(
                    "SELECT encrypted_value FROM cookies WHERE name='session'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(result["cookies"], 1)
            self.assertEqual(
                decrypt_chromium_bytes(encrypted, master_key=key),
                b"portable",
            )


class DefenderBootstrapTests(unittest.TestCase):
    def test_fixture_parsing_and_decision_never_disables_antivirus(self) -> None:
        status = bootstrap.parse_defender_status(
            '{"AMServiceEnabled":true,"AntivirusEnabled":true,'
            '"RealTimeProtectionEnabled":true}'
        )
        exclusions = bootstrap.parse_exclusions(
            '{"ExclusionPath":["C:\\\\Safe"]}'
        )
        third_party = bootstrap.parse_antivirus_products(
            '[{"displayName":"Microsoft Defender Antivirus"},'
            '{"displayName":"Example AV"}]'
        )
        app = Path("C:/Distrohop").resolve()

        decision = bootstrap.decide_gate(
            app,
            status=status,
            exclusions=exclusions,
            antivirus_products=third_party,
        )
        command = bootstrap.elevated_exclusion_command(app)
        script = bootstrap.exclusion_script(app)

        self.assertEqual(decision["action"], "defender-consent")
        self.assertIn("Add-MpPreference", script)
        self.assertIn(str(app), script)
        self.assertNotIn("DisableRealtimeMonitoring", script)
        self.assertNotIn("DisableRealtimeMonitoring", " ".join(command))

    def test_existing_exact_exclusion_skips_dialog_but_parent_does_not(self) -> None:
        app = Path("C:/Apps/Distrohop").resolve()
        status = {
            "defender_active": True,
            "real_time": True,
        }
        self.assertEqual(
            bootstrap.decide_gate(
                app,
                status=status,
                exclusions=[str(app)],
                antivirus_products=[],
            )["action"],
            "continue",
        )
        self.assertEqual(
            bootstrap.decide_gate(
                app,
                status=status,
                exclusions=[str(app.parent)],
                antivirus_products=[],
            )["action"],
            "defender-consent",
        )

    def test_refused_uac_returns_to_dialog_and_saved_choice_prevents_reprompt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            security = {
                "status": {"defender_active": True, "real_time": True},
                "exclusions": [],
                "products": [],
            }
            with (
                patch("distrohop.bootstrap.inspect_security", return_value=security),
                patch(
                    "distrohop.ui.defender_dialog.ask_defender",
                    side_effect=("enable", "continue"),
                ) as dialog,
                patch("distrohop.bootstrap.request_exclusion", return_value=False),
            ):
                self.assertTrue(bootstrap.run_gate(app))
                self.assertEqual(dialog.call_count, 2)

            with (
                patch("distrohop.bootstrap.inspect_security", return_value=security),
                patch(
                    "distrohop.ui.defender_dialog.ask_defender"
                ) as repeated_dialog,
            ):
                self.assertTrue(bootstrap.run_gate(app))
                repeated_dialog.assert_not_called()


class WindowsInstallerTests(unittest.TestCase):
    def test_winget_uses_exact_verified_id_and_choco_is_fallback(self) -> None:
        command = plan_install(
            "firefox",
            {"manager": "winget"},
        )
        self.assertEqual(
            command,
            (
                "winget",
                "install",
                "--id",
                "Mozilla.Firefox",
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ),
        )
        self.assertEqual(
            plan_install("brave", {"manager": "choco"}),
            ("choco", "install", "brave", "-y"),
        )


class WindowsBundleCryptoTests(unittest.TestCase):
    def test_stdlib_aes_gcm_bundle_roundtrip_and_tamper_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "credential.txt").write_bytes(b"secret" * 100)
            encrypted = root / "bundle.tar.enc"

            encrypt_tree(
                source,
                encrypted,
                "correct horse",
                system="windows",
                iterations=10_000,
            )
            self.assertEqual(encrypted.read_bytes()[:4], b"DHG1")
            destination = root / "restored"
            decrypt_archive(encrypted, destination, "correct horse")
            self.assertEqual(
                (destination / "credential.txt").read_bytes(),
                b"secret" * 100,
            )

            damaged = bytearray(encrypted.read_bytes())
            damaged[-1] ^= 1
            encrypted.write_bytes(damaged)
            with self.assertRaises(CryptoError):
                decrypt_archive(encrypted, root / "damaged", "correct horse")


class WindowsEngineTests(unittest.TestCase):
    def test_windows_backup_dispatches_capture_and_publishes_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "User Data" / "Default"
            profile.mkdir(parents=True)
            (profile / "Preferences").write_text("{}", encoding="utf-8")
            inventory = {
                "platform": "windows",
                "os": {"manager": "winget", "strategy": "imperativa"},
                "browsers": [{
                    "id": "brave",
                    "name": "Brave",
                    "engine": "chromium",
                    "packaging": "native",
                    "path": str(profile.parent),
                    "installed": True,
                    "profiles": [{"name": "Default", "path": str(profile)}],
                }],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }
            plan = plan_backup(
                Selection(browser_profiles=(str(profile),)),
                (root / "destination",),
                inventory=inventory,
                home=root,
                bundle_name="win-bundle",
            )

            def capture(
                source: Path,
                destination: Path,
                **_kwargs: object,
            ) -> dict:
                (destination / "raw").mkdir(parents=True)
                (destination / "raw" / "Preferences").write_text(
                    "{}", encoding="utf-8"
                )
                write_cookies([], destination / "neutral" / "cookies.jsonl")
                write_logins([], destination / "neutral" / "logins.csv")
                write_bookmarks([], destination / "neutral" / "bookmarks.html")
                return {
                    "cookies": 0,
                    "logins": 0,
                    "bookmarks": 0,
                    "warnings": [],
                }

            with patch(
                "distrohop.core.engine.chromium_win.capture_profile",
                side_effect=capture,
            ) as captured:
                result = run_backup(plan)

            bundle = root / "destination" / "win-bundle"
            manifest = json.loads(
                (bundle / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["destinations"], [str(bundle)])
            self.assertEqual(manifest["source"]["platform"], "windows")
            captured.assert_called_once()


class WindowsBrowserDetectionTests(unittest.TestCase):
    def test_profiles_and_installed_binary_are_detected_from_windows_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "Local"
            roaming = root / "Roaming"
            brave = local / "BraveSoftware" / "Brave-Browser" / "User Data"
            (brave / "Default").mkdir(parents=True)
            firefox = roaming / "Mozilla" / "Firefox"
            profile = firefox / "Profiles" / "abc.default"
            profile.mkdir(parents=True)
            (firefox / "profiles.ini").write_text(
                "[Profile0]\nName=default\nIsRelative=1\n"
                "Path=Profiles/abc.default\nDefault=1\n",
                encoding="utf-8",
            )
            executable = root / "brave.exe"
            executable.write_bytes(b"MZ")

            detected = browsers.detect_windows(
                {
                    "LOCALAPPDATA": str(local),
                    "APPDATA": str(roaming),
                    "USERPROFILE": str(root),
                    "PROGRAMFILES": str(root / "Program Files"),
                },
                which=lambda name: str(executable) if name == "brave.exe" else None,
            )

            self.assertEqual([item["id"] for item in detected], ["brave", "firefox"])
            self.assertTrue(detected[0]["installed"])
            self.assertEqual(detected[1]["profiles"][0]["name"], "default")

    def test_exact_windows_tasklist_process_gate(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                '"firefox.exe","3012","Console","1","120,000 K"\n'
                '"notfirefox.exe","3013","Console","1","2,000 K"\n',
                "",
            )
        )
        self.assertTrue(
            is_browser_running("firefox", system="windows", runner=runner)
        )
        self.assertFalse(
            is_browser_running("brave", system="windows", runner=runner)
        )
        self.assertEqual(
            runner.call_args.args[0],
            ["tasklist.exe", "/FO", "CSV", "/NH"],
        )

    def test_windows_volume_numeric_drive_types_mark_only_safe_candidates(self) -> None:
        volumes = disks.parse_windows_volumes(
            json.dumps([
                {
                    "DriveLetter": "C",
                    "DriveType": 3,
                    "FileSystem": "NTFS",
                    "Size": 1000,
                    "SizeRemaining": 100,
                },
                {
                    "DriveLetter": "E",
                    "DriveType": 2,
                    "FileSystem": "exFAT",
                    "Size": 2000,
                    "SizeRemaining": 1500,
                },
            ]),
            system_drive="C:",
        )
        self.assertTrue(volumes[0]["system"])
        self.assertFalse(volumes[0]["candidate"])
        self.assertTrue(volumes[1]["removable"])
        self.assertTrue(volumes[1]["candidate"])
