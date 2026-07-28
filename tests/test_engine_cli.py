import unittest
from unittest.mock import patch

from distrohop.core.engine import BackupPlan, list_inventory
from distrohop.core.selection import Selection
from distrohop.ui.cli import build_parser, render_backup_plan, render_inventory


class EngineCliTests(unittest.TestCase):
    @patch("distrohop.core.engine.disks.detect_linux", return_value=[])
    @patch("distrohop.core.engine.browsers.detect_linux", return_value=[])
    @patch("distrohop.core.engine.ai.detect", return_value=[])
    @patch("distrohop.core.engine.distro.detect", return_value={
        "id": "cachyos", "name": "CachyOS", "manager": "pacman", "strategy": "imperativa"
    })
    def test_engine_emits_events_and_cli_only_renders(self, _distro, _ai, _browsers, _disks):
        events = []
        inventory = list_inventory(system="Linux", callback=events.append)
        self.assertEqual([event.kind for event in events], ["started", "step", "warn", "done"])
        output = render_inventory(inventory)
        self.assertIn("Sistema: CachyOS", output)
        self.assertIn("Gerenciador: pacman", output)

    def test_explicit_cli_flag_keeps_subcommand(self):
        args = build_parser().parse_args(["--cli", "list"])
        self.assertTrue(args.cli)
        self.assertEqual(args.command, "list")

    def test_backup_parser_and_dry_run_renderer(self):
        args = build_parser().parse_args(
            ["backup", "--dry-run", "--encrypt", "--target", "/media/backup"]
        )
        self.assertTrue(args.dry_run)
        self.assertTrue(args.encrypt)
        self.assertEqual(args.targets, ["/media/backup"])
        plan = BackupPlan(
            selection=Selection(),
            targets=(),
            bundle_name="distrohop-test",
            inventory={},
            home=__import__("pathlib").Path("/home/test"),
            sources=("/home/test/.codex/auth.json",),
            outputs=("<destino>/distrohop-test/bundle.tar.enc",),
            encrypted=True,
        )
        output = render_backup_plan(plan)
        self.assertIn("nenhum arquivo será escrito", output)
        self.assertIn("/home/test/.codex/auth.json", output)
        self.assertIn("bundle.tar.enc", output)


if __name__ == "__main__":
    unittest.main()
