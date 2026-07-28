from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from distrohop.capture.extras import sanitize_nix_store_paths
from distrohop.core.engine import plan_restore, plan_resume, run_restore, run_resume
from distrohop.restore.installer import plan_install
from distrohop.restore.nixos import (
    build_nixos_markdown,
    restore_dotfile_guarded,
)
from distrohop.restore.resume import load_resume_state
from distrohop.vault.bundle import assemble_bundle


def _firefox_bundle(root: Path) -> Path:
    payload = root / "payload"
    raw = payload / "browsers" / "firefox" / "default" / "raw"
    raw.mkdir(parents=True)
    (raw / "prefs.js").write_text("restored", encoding="utf-8")
    bundle = root / "bundle"
    assemble_bundle(
        payload,
        bundle,
        metadata={
            "source": {"platform": "linux"},
            "browsers": [{
                "id": "firefox",
                "name": "Firefox",
                "engine": "firefox",
                "profile": "default",
                "bundle_path": "browsers/firefox/default",
            }],
        },
        encrypted=False,
    )
    return bundle


class NixOSGuidanceTests(unittest.TestCase):
    def test_flake_and_classic_configuration_get_exact_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            etc = root / "etc-nixos"
            etc.mkdir()
            flake = etc / "flake.nix"
            flake.write_text("{ outputs = _: {}; }", encoding="utf-8")

            markdown = build_nixos_markdown(
                "firefox",
                home=root / "home",
                etc_nixos=etc,
            )

            self.assertIn(str(flake), markdown)
            self.assertIn("environment.systemPackages", markdown)
            self.assertIn("pkgs.firefox", markdown)
            self.assertIn("sudo nixos-rebuild switch --flake", markdown)

            flake.unlink()
            configuration = etc / "configuration.nix"
            configuration.write_text("{ pkgs, ... }: {}", encoding="utf-8")
            markdown = build_nixos_markdown(
                "firefox",
                home=root / "home",
                etc_nixos=etc,
            )
            self.assertIn(str(configuration), markdown)
            self.assertIn("sudo nixos-rebuild switch", markdown)

    def test_nix_store_dotfile_is_redirected_and_backup_copy_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "nix" / "store" / ("a" * 32 + "-config")
            store.mkdir(parents=True)
            (store / "zshrc").write_text("managed", encoding="utf-8")
            target = root / ".zshrc"
            target.symlink_to(store / "zshrc")
            source = root / "source"
            source.write_text("portable", encoding="utf-8")

            result = restore_dotfile_guarded(
                source,
                target,
                nix_store_root=root / "nix" / "store",
            )

            redirected = root / ".zshrc.distrohop-restore"
            self.assertEqual(result["status"], "redirected")
            self.assertEqual(redirected.read_text(encoding="utf-8"), "portable")
            self.assertEqual(target.read_text(encoding="utf-8"), "managed")

            copied = root / "copied"
            copied.mkdir()
            config = copied / "tool.json"
            config.write_text(
                '{"bin":"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-tool/bin/tool"}',
                encoding="utf-8",
            )
            warnings = sanitize_nix_store_paths(copied)
            self.assertEqual(config.read_text(encoding="utf-8"), '{"bin":"tool"}')
            self.assertTrue(warnings)


class AtomicInstallerTests(unittest.TestCase):
    def test_rpm_ostree_uses_native_recipe_and_never_flatpak_by_accident(self) -> None:
        os_info = {
            "family": "rpm-ostree",
            "manager": "rpm-ostree",
            "strategy": "atômica",
            "install_argv": ["rpm-ostree", "install", "{package}"],
            "query_argv": ["rpm-ostree", "search", "{package}"],
            "requires_reboot": True,
        }
        runner = Mock()
        runner.return_value.returncode = 0
        with patch("distrohop.restore.installer.shutil.which", return_value="/bin/tool"), patch(
            "distrohop.restore.installer.os.geteuid", return_value=1000
        ):
            command = plan_install("firefox", os_info, runner=runner)
        self.assertEqual(
            command,
            ("sudo", "rpm-ostree", "install", "firefox"),
        )


class ResumeFlowTests(unittest.TestCase):
    def test_declarative_restore_generates_guidance_and_waits_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _firefox_bundle(root)
            target = root / "profile"
            inventory = {
                "platform": "linux",
                "os": {
                    "id": "nixos",
                    "family": "nixos",
                    "manager": "nix",
                    "strategy": "declarativa",
                },
                "browsers": [],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }

            plan = plan_restore(
                bundle,
                browser_id="firefox",
                target_profile=target,
                install=True,
                inventory=inventory,
            )
            result = run_restore(
                plan,
                running_check=lambda _browser_id: False,
                home=root / "home",
                etc_nixos=root / "etc-nixos",
            )

            self.assertEqual(plan.preparation, "declarative")
            self.assertTrue(result["pending"])
            self.assertTrue((bundle / "NIXOS.md").is_file())
            self.assertTrue((bundle / ".distrohop-resume.json").is_file())
            self.assertFalse(target.exists())

    def test_atomic_resume_requires_new_boot_then_applies_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = _firefox_bundle(root)
            target = root / "profile"
            absent = {
                "platform": "linux",
                "os": {
                    "id": "bazzite",
                    "family": "rpm-ostree",
                    "manager": "rpm-ostree",
                    "strategy": "atômica",
                    "install_argv": ["rpm-ostree", "install", "{package}"],
                    "query_argv": ["rpm-ostree", "search", "{package}"],
                    "requires_reboot": True,
                },
                "browsers": [],
                "ai_accounts": [],
                "disks": [],
                "warnings": [],
            }
            with patch(
                "distrohop.core.engine.installer.plan_install",
                return_value=("sudo", "rpm-ostree", "install", "firefox"),
            ), patch("distrohop.core.engine.installer.run_install") as install:
                plan = plan_restore(
                    bundle,
                    browser_id="firefox",
                    target_profile=target,
                    install=True,
                    inventory=absent,
                )
                result = run_restore(
                    plan,
                    running_check=lambda _browser_id: False,
                    boot_id_reader=lambda: "boot-before",
                )
            install.assert_called_once()
            self.assertTrue(result["pending"])
            self.assertEqual(load_resume_state(bundle)["boot_id"], "boot-before")

            installed = dict(absent)
            installed["browsers"] = [{
                "id": "firefox",
                "name": "Firefox",
                "engine": "firefox",
                "installed": True,
                "profiles": [],
            }]
            with self.assertRaisesRegex(RuntimeError, "reinicie"):
                plan_resume(
                    bundle,
                    inventory=installed,
                    boot_id_reader=lambda: "boot-before",
                )
            resume_plan = plan_resume(
                bundle,
                inventory=installed,
                boot_id_reader=lambda: "boot-after",
            )
            applied = run_resume(
                resume_plan,
                running_check=lambda _browser_id: False,
            )

            self.assertEqual((target / "prefs.js").read_text(encoding="utf-8"), "restored")
            self.assertFalse((bundle / ".distrohop-resume.json").exists())
            self.assertEqual(applied["mode"], "raw")
