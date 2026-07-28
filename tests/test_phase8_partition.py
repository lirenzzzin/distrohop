from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from distrohop.vault.bundle import assemble_bundle
from distrohop.vault.partition import (
    BtrfsState,
    CONFIRMATION_PHRASE,
    DiskLayout,
    Partition,
    VaultError,
    build_plan,
    parse_layout,
    plan_vault,
    _backup_is_independent,
)
from distrohop.ui.cli import build_parser


def layout(
    *,
    label: str = "gpt",
    tail_free: int = 0,
    filesystem: str = "btrfs",
) -> DiskLayout:
    end = 8_388_608
    source = Partition(
        node="/dev/loop7p1",
        number=1,
        start=2048,
        size=end - 2048 - tail_free,
        filesystem=filesystem,
        mountpoint="/mnt/source",
    )
    return DiskLayout(
        device=Path("/dev/loop7"),
        label=label,
        sector_size=512,
        first_lba=2048,
        last_lba=end - 1,
        partitions=(source,),
    )


def state(**changes: object) -> BtrfsState:
    base = BtrfsState(
        partition_node="/dev/loop7p1",
        mountpoint="/mnt/source",
        filesystem="btrfs",
        free_bytes=2 * 1024**3,
    )
    return replace(base, **changes)


class VaultPrecheckTests(unittest.TestCase):
    def build(self, **changes: object):
        arguments = {
            "layout": layout(),
            "size_bytes": 256 * 1024**2,
            "backup_bundle": Path("/mnt/backup/bundle"),
            "confirmation": CONFIRMATION_PHRASE,
            "backup_valid": True,
            "backup_independent": True,
            "btrfs": state(),
            "minimum_size": 1024**2,
        }
        arguments.update(changes)
        return build_plan(**arguments)

    def test_confirmation_is_the_first_gate(self) -> None:
        with self.assertRaisesRegex(VaultError, "confirmação incorreta"):
            self.build(
                confirmation="sim",
                backup_valid=False,
                backup_independent=False,
                layout=layout(label="dos"),
            )

    def test_second_copy_must_be_valid_and_independent(self) -> None:
        with self.assertRaisesRegex(VaultError, "não é um bundle íntegro"):
            self.build(backup_valid=False)
        with self.assertRaisesRegex(VaultError, "mesmo disco"):
            self.build(backup_independent=False)

    def test_only_existing_gpt_is_accepted(self) -> None:
        with self.assertRaisesRegex(VaultError, "tabela GPT"):
            self.build(layout=layout(label="dos"))

    def test_non_btrfs_shrink_is_refused_with_manual_guidance(self) -> None:
        with self.assertRaisesRegex(VaultError, "live USB/GParted"):
            self.build(
                layout=layout(filesystem="ext4"),
                btrfs=state(filesystem="ext4"),
            )

    def test_every_btrfs_risk_gate_aborts(self) -> None:
        cases = (
            ("leitura/escrita", {"writable": False}),
            ("múltiplos dispositivos", {"device_count": 2}),
            ("balance", {"balance_running": True}),
            ("scrub", {"scrub_running": True}),
            ("snapshot", {"snapshot_running": True}),
            ("20%", {"free_bytes": 10}),
        )
        for message, changes in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(VaultError, message):
                    self.build(btrfs=state(**changes))

    def test_existing_tail_space_wins_without_btrfs_commands(self) -> None:
        plan = self.build(
            layout=layout(tail_free=2_000_000, filesystem="xfs"),
            btrfs=None,
        )
        command_text = "\n".join(
            " ".join(command.arguments) for command in plan.commands
        )
        self.assertEqual(plan.strategy, "free-space")
        self.assertNotIn("btrfs", command_text)
        self.assertNotIn("--reorder", command_text)
        self.assertNotIn("fstab", command_text)
        self.assertNotIn("grub", command_text)
        self.assertIn("DISTROHOP-DO-NOT-FORMAT", plan.commands[0].stdin)

    def test_btrfs_plan_shrinks_filesystem_before_partition(self) -> None:
        plan = self.build()
        self.assertEqual(plan.strategy, "shrink-btrfs")
        self.assertGreater(plan.shrink_bytes, 0)
        self.assertEqual(
            plan.commands[0].arguments[:3],
            ("btrfs", "filesystem", "resize"),
        )
        self.assertEqual(plan.commands[1].arguments[0], "sfdisk")
        self.assertEqual(plan.commands[1].arguments[-2:], ("1", "/dev/loop7"))

    def test_overlapping_layout_is_rejected(self) -> None:
        payload = {
            "partitiontable": {
                "label": "gpt",
                "sectorsize": 512,
                "firstlba": 2048,
                "lastlba": 99999,
                "partitions": [
                    {"node": "/dev/loop7p1", "start": 2048, "size": 50000},
                    {"node": "/dev/loop7p2", "start": 40000, "size": 10000},
                ],
            }
        }
        with self.assertRaisesRegex(VaultError, "sobrepostas"):
            parse_layout(json.dumps(payload), Path("/dev/loop7"))

    def test_cli_defaults_to_dry_run_and_requires_explicit_execute(self) -> None:
        parser = build_parser()
        dry = parser.parse_args([
            "vault",
            "create",
            "--disk",
            "/dev/loop7",
            "--size-gib",
            "8",
            "--backup",
            "/mnt/backup/bundle",
            "--confirm",
            CONFIRMATION_PHRASE,
        ])
        live = parser.parse_args([
            "vault",
            "create",
            "--disk",
            "/dev/loop7",
            "--size-gib",
            "8",
            "--backup",
            "/mnt/backup/bundle",
            "--confirm",
            CONFIRMATION_PHRASE,
            "--execute",
        ])
        self.assertFalse(dry.execute)
        self.assertTrue(live.execute)

    def test_btrfs_subvolume_source_on_same_disk_is_not_independent(self) -> None:
        runner = Mock(
            side_effect=(
                subprocess.CompletedProcess([], 0, "/dev/sdb2[/@home]\n", ""),
                subprocess.CompletedProcess(
                    [], 0, "/dev/sdb\n/dev/sdb2\n", ""
                ),
            )
        )
        self.assertFalse(
            _backup_is_independent(
                Path("/mnt/bundle"),
                Path("/dev/sdb"),
                runner,
            )
        )


@unittest.skipUnless(shutil.which("sfdisk"), "sfdisk não disponível")
class VaultDiskImageTests(unittest.TestCase):
    def test_real_sfdisk_plan_and_append_touch_only_regular_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "vault-test.img"
            with image.open("wb") as stream:
                stream.truncate(64 * 1024**2)
            created = subprocess.run(
                [
                    "sfdisk",
                    "--quiet",
                    str(image),
                ],
                input="label: gpt\nsize=16MiB, type=linux\n",
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            payload = root / "payload"
            payload.mkdir()
            (payload / "ok.txt").write_text("verified", encoding="utf-8")
            bundle = root / "second-copy"
            assemble_bundle(
                payload,
                bundle,
                metadata={"source": {"platform": "linux"}, "browsers": []},
                encrypted=False,
            )

            plan = plan_vault(
                image,
                size_bytes=8 * 1024**2,
                backup_bundle=bundle,
                confirmation=CONFIRMATION_PHRASE,
                allow_regular_file=True,
                minimum_size=1024**2,
            )
            self.assertEqual(plan.strategy, "free-space")
            add = plan.commands[0]
            applied = subprocess.run(
                list(add.arguments),
                input=add.stdin,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            inspected = subprocess.run(
                ["sfdisk", "--json", str(image)],
                check=True,
                capture_output=True,
                text=True,
            )
            written = parse_layout(inspected.stdout, image)
            self.assertEqual(len(written.partitions), 2)
            self.assertEqual(
                written.partitions[1].start,
                plan.start_sector,
            )
            self.assertEqual(
                written.partitions[1].size,
                plan.size_sectors,
            )
