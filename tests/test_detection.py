import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from distrohop.detect import ai, browsers
from distrohop.detect.disks import detect_linux, parse_lsblk


class DetectionTests(unittest.TestCase):
    def test_browser_profiles_and_ai_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / ".config/BraveSoftware/Brave-Browser/Default").mkdir(parents=True)
            (home / ".config/BraveSoftware/Brave-Browser/Local State").write_text(
                json.dumps({"profile": {"info_cache": {"Default": {"name": "Trabalho"}}}}),
                encoding="utf-8",
            )
            zen = home / ".zen"
            (zen / "abc.default-release").mkdir(parents=True)
            (zen / "profiles.ini").write_text(
                "[Profile0]\nName=default-release\nIsRelative=1\nPath=abc.default-release\n",
                encoding="utf-8",
            )
            (home / ".codex").mkdir()
            (home / ".codex-conta2").mkdir()
            (home / ".codex2").mkdir()

            detected = browsers.detect_linux(
                home,
                environ={},
                which=lambda _command: None,
            )
            self.assertEqual([(item["name"], len(item["profiles"])) for item in detected], [("Brave", 1), ("Zen", 1)])
            self.assertEqual(detected[0]["profiles"][0]["name"], "Trabalho")
            self.assertEqual([item["slot"] for item in ai.detect(home)], ["codex", "codex-conta2", "codex2"])

    def test_lsblk_marks_root_ancestry_and_external_candidate(self):
        fixture = {"blockdevices": [
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": "100G", "mountpoints": [None], "children": [
                {"name": "sdb2", "path": "/dev/sdb2", "size": "90G", "fstype": "ext4", "mountpoints": ["/"], "rm": False}
            ]},
            {"name": "sdc1", "path": "/dev/sdc1", "label": "BACKUP", "size": "1T", "fstype": "ext4",
             "mountpoints": ["/run/media/user/BACKUP"], "rm": True},
        ]}
        disks = parse_lsblk(json.dumps(fixture))
        root = next(item for item in disks if item["name"] == "sdb2")
        system_disk = next(item for item in disks if item["name"] == "sdb")
        backup = next(item for item in disks if item["name"] == "sdc1")
        self.assertTrue(root["system"])
        self.assertTrue(system_disk["system"])
        self.assertFalse(root["candidate"])
        self.assertTrue(backup["candidate"])

    def test_lsblk_falls_back_to_singular_mountpoint(self):
        calls = []
        fixture = {"blockdevices": [{
            "name": "sdc1", "path": "/dev/sdc1", "type": "part", "size": "10G",
            "fstype": "ext4", "mountpoint": "/media/BACKUP", "rm": True,
        }]}

        def runner(command, **_kwargs):
            calls.append(command)
            if "MOUNTPOINTS" in command[-1]:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(fixture), stderr="")

        detected = detect_linux(runner=runner, writable=lambda _path: True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(detected[0]["mountpoints"], ["/media/BACKUP"])
        self.assertTrue(detected[0]["candidate"])

    def test_lsblk_marks_every_raid_parent_as_system(self):
        root = {
            "name": "md0", "path": "/dev/md0", "type": "raid1", "fstype": "ext4",
            "mountpoints": ["/"],
        }
        fixture = {"blockdevices": [
            {"name": "sda", "path": "/dev/sda", "type": "disk", "mountpoints": [], "children": [root]},
            {"name": "sdb", "path": "/dev/sdb", "type": "disk", "mountpoints": [], "children": [root]},
            {"name": "sdc1", "path": "/dev/sdc1", "type": "part", "fstype": "ext4",
             "mountpoints": ["/media/backup"]},
        ]}
        disks = parse_lsblk(json.dumps(fixture))
        self.assertTrue(next(item for item in disks if item["name"] == "sda")["system"])
        self.assertTrue(next(item for item in disks if item["name"] == "sdb")["system"])
        self.assertFalse(next(item for item in disks if item["name"] == "md0")["candidate"])
        self.assertTrue(next(item for item in disks if item["name"] == "sdc1")["candidate"])

    def test_browser_respects_xdg_and_ignores_empty_stale_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            xdg = home / "custom-config"
            chrome_base = home / "chrome-config"
            (xdg / "BraveSoftware/Brave-Browser").mkdir(parents=True)
            (chrome_base / "chromium/Default").mkdir(parents=True)
            detected = browsers.detect_linux(
                home,
                environ={
                    "XDG_CONFIG_HOME": str(xdg),
                    "CHROME_CONFIG_HOME": str(chrome_base),
                },
                which=lambda _command: None,
            )
            self.assertEqual([item["id"] for item in detected], ["chromium"])
