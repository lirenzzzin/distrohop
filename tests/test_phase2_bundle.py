from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distrohop.vault.bundle import assemble_bundle, verify_bundle
from distrohop.vault.targets import publish_to_targets


class BundleTests(unittest.TestCase):
    def test_clear_manifest_has_checksums_and_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            (payload / "browser").mkdir(parents=True)
            (payload / "browser" / "cookies.jsonl").write_text('{"name":"sid"}\n', encoding="utf-8")
            bundle = root / "bundle"

            manifest = assemble_bundle(
                payload,
                bundle,
                metadata={"format_version": 1, "source": {"platform": "linux"}},
                encrypted=False,
            )

            on_disk = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["files"], manifest["files"])
            self.assertTrue(verify_bundle(bundle))
            self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(bundle / "manifest.json").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(bundle / "browser" / "cookies.jsonl").st_mode & 0o777, 0o600)

    def test_publish_verifies_each_destination_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text(
                json.dumps({"files": {"data.txt": {"sha256": "00", "size": 1}}}),
                encoding="utf-8",
            )
            (source / "data.txt").write_text("x", encoding="utf-8")
            targets = [root / "one", root / "two"]

            published = publish_to_targets(source, targets, "named-bundle")
            self.assertEqual(
                published,
                [targets[0] / "named-bundle", targets[1] / "named-bundle"],
            )
            for destination in published:
                self.assertEqual((destination / "data.txt").read_text(encoding="utf-8"), "x")

            with self.assertRaises(FileExistsError):
                publish_to_targets(source, [targets[0]], "named-bundle")

    def test_publish_can_adopt_a_staged_bundle_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir()
            source = target / ".capture" / "bundle"
            source.mkdir(parents=True)
            (source / "data.txt").write_text("verified", encoding="utf-8")

            with patch(
                "distrohop.vault.targets.shutil.copytree",
                side_effect=AssertionError("the staged bundle must be renamed"),
            ):
                published = publish_to_targets(
                    source,
                    [target],
                    "named-bundle",
                    adopt_source=True,
                )

            self.assertEqual(published, [target / "named-bundle"])
            self.assertFalse(source.exists())
            self.assertEqual(
                (target / "named-bundle" / "data.txt").read_text(encoding="utf-8"),
                "verified",
            )

    def test_clear_bundle_can_move_payload_on_the_same_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "large.bin").write_bytes(b"x" * 4096)
            bundle = root / "bundle"

            assemble_bundle(
                payload,
                bundle,
                metadata={"source": {"platform": "linux"}},
                encrypted=False,
                move_payload=True,
            )

            self.assertFalse((payload / "large.bin").exists())
            self.assertEqual((bundle / "large.bin").stat().st_size, 4096)
            self.assertTrue(verify_bundle(bundle))

    def test_encrypted_bundle_keeps_manifest_clear_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "token.txt").write_text("highly-secret", encoding="utf-8")
            bundle = root / "encrypted"

            manifest = assemble_bundle(
                payload,
                bundle,
                metadata={"source": {"platform": "linux"}},
                encrypted=True,
                password="test password",
            )

            clear = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(clear["encrypted"])
            self.assertEqual(clear, manifest)
            self.assertTrue((bundle / "bundle.tar.enc").is_file())
            self.assertFalse((bundle / "token.txt").exists())
            self.assertTrue(verify_bundle(bundle))
