from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools import vm_lab
from tools.vm import guest_smoke


class VmMatrixTests(unittest.TestCase):
    def test_core_matrix_obeys_resource_and_network_contract(self) -> None:
        matrix = vm_lab.load_matrix()

        self.assertLessEqual(matrix["defaults"]["memory_mb"], 2048)
        self.assertLessEqual(matrix["defaults"]["cpus"], 2)
        self.assertGreaterEqual(matrix["defaults"]["system_disk_gib"], 8)
        automated = [item for item in matrix["distros"] if item["automated"]]
        self.assertEqual(
            {item["id"] for item in automated},
            {
                "ubuntu-2604",
                "debian-13",
                "fedora-44",
                "arch",
                "opensuse-tumbleweed",
                "alpine-324",
            },
        )
        self.assertEqual(
            len({item["ssh_port"] for item in matrix["distros"]}),
            len(matrix["distros"]),
        )
        for item in automated:
            self.assertTrue(item["image_url"].startswith("https://"))
            self.assertTrue(item["checksum_url"].startswith("https://"))
            self.assertTrue(item["setup_commands"])

    def test_extended_targets_are_never_silently_automated(self) -> None:
        matrix = vm_lab.load_matrix()
        for identifier in ("microos", "nixos"):
            item = vm_lab.distro_entry(matrix, identifier)
            self.assertFalse(item["automated"])
            self.assertTrue(item["reason"])
            with tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(vm_lab.LabError):
                    vm_lab.command_fetch(item, Path(temporary))

    def test_minimal_gui_guests_install_a_real_font(self) -> None:
        matrix = vm_lab.load_matrix()
        for identifier, package in (
            ("arch", "ttf-dejavu"),
            ("opensuse-tumbleweed", "dejavu-fonts"),
            ("alpine-324", "font-dejavu"),
        ):
            setup = " ".join(
                vm_lab.distro_entry(matrix, identifier)["setup_commands"]
            )
            self.assertIn(package, setup)


class ChecksumTests(unittest.TestCase):
    def test_parses_gnu_fedora_and_bare_checksum_forms(self) -> None:
        digest = "a" * 64
        filename = "image.qcow2"

        self.assertEqual(
            vm_lab.parse_checksum(
                "{} *{}\n".format(digest, filename), filename, "sha256"
            ),
            digest,
        )
        self.assertEqual(
            vm_lab.parse_checksum(
                "SHA256 ({}) = {}\n".format(filename, digest),
                filename,
                "sha256",
            ),
            digest,
        )
        self.assertEqual(
            vm_lab.parse_checksum(digest + "\n", filename, "sha256"),
            digest,
        )

    def test_does_not_accept_a_checksum_for_a_different_file(self) -> None:
        with self.assertRaises(vm_lab.LabError):
            vm_lab.parse_checksum(
                "{} other.qcow2\n".format("b" * 64),
                "wanted.qcow2",
                "sha256",
            )


class ResourcePolicyTests(unittest.TestCase):
    def test_cloud_config_uses_key_only_login_and_guest_swap(self) -> None:
        config = vm_lab.cloud_config(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest lab",
            1024,
        )

        self.assertIn("ssh_pwauth: false", config)
        self.assertIn("lock_passwd: true", config)
        self.assertIn("fallocate -l 1024M", config)
        self.assertNotIn("plain_text_passwd", config)

    def test_alpine_unlocks_key_only_user_for_its_ssh_policy(self) -> None:
        matrix = vm_lab.load_matrix()
        distro = vm_lab.distro_entry(matrix, "alpine-324")
        config = vm_lab.cloud_config(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest lab",
            1024,
            unlock_user=distro["unlock_user"],
            user=distro["ssh_user"],
        )

        self.assertIn("- name: alpine", config)
        self.assertIn("lock_passwd: false", config)
        self.assertIn("ssh_pwauth: false", config)

    def test_qemu_is_headless_limited_low_priority_and_localhost_only(self) -> None:
        matrix = vm_lab.load_matrix()
        distro = vm_lab.distro_entry(matrix, "debian-13")
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            vm_lab,
            "_firmware",
            return_value=(Path("/firmware/code.fd"), Path("/firmware/vars.fd")),
        ):
            command = vm_lab.qemu_command(
                distro,
                matrix["defaults"],
                Path(temporary),
            )

        joined = " ".join(command)
        self.assertEqual(command[:6], ["nice", "-n", "15", "ionice", "-c", "3"])
        self.assertIn("-m 2048", joined)
        self.assertIn("-smp 2", joined)
        self.assertIn("-display none", joined)
        self.assertIn("-daemonize", command)
        self.assertIn("hostfwd=tcp:127.0.0.1:22402-:22", joined)
        self.assertNotIn("0.0.0.0", joined)

    def test_state_override_is_resolved_without_using_repo_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = vm_lab.state_root({"DISTROHOP_VM_HOME": temporary})

        self.assertEqual(root, Path(temporary).resolve())
        self.assertNotEqual(root, vm_lab.REPO_ROOT)

    def test_stale_pid_can_never_target_an_unrelated_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pid_file = root / "qemu.pid"
            pid_file.write_text("4242\n", encoding="ascii")
            proc = root / "proc" / "4242"
            proc.mkdir(parents=True)
            paths = {"pid": pid_file}
            with patch("tools.vm_lab.os.kill"):
                (proc / "cmdline").write_bytes(b"/usr/bin/python3\0worker.py\0")
                self.assertIsNone(vm_lab._pid(paths, root / "proc"))

                (proc / "cmdline").write_bytes(
                    b"/usr/bin/qemu-system-x86_64\0-pidfile\0"
                    + str(pid_file).encode()
                    + b"\0"
                )
                self.assertEqual(vm_lab._pid(paths, root / "proc"), 4242)

    def test_destroy_requires_confirmation_and_preserves_base_image(self) -> None:
        matrix = vm_lab.load_matrix()
        distro = vm_lab.distro_entry(matrix, "debian-13")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = vm_lab._paths(root, distro)
            paths["instance"].mkdir(parents=True)
            paths["overlay"].write_text("disposable", encoding="utf-8")
            paths["base"].parent.mkdir(parents=True)
            paths["base"].write_text("verified-base", encoding="utf-8")

            with self.assertRaises(vm_lab.LabError):
                vm_lab.command_destroy(distro, root, False)
            self.assertTrue(paths["instance"].exists())

            vm_lab.command_destroy(distro, root, True)
            self.assertFalse(paths["instance"].exists())
            self.assertTrue(paths["base"].exists())

    def test_gui_smoke_runs_as_a_module_so_repo_imports_are_available(self) -> None:
        with patch.object(
            guest_smoke.shutil, "which", return_value="/usr/bin/xvfb-run"
        ), patch.object(
            guest_smoke,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as runner:
            guest_smoke.gui_smoke()

        argv = runner.call_args.args[0]
        self.assertIn("-m", argv)
        self.assertIn("tools.vm.guest_smoke", argv)

    def test_gui_smoke_starts_xvfb_when_distro_has_no_wrapper(self) -> None:
        server = MagicMock()
        server.stdout.readline.return_value = "7\n"
        with patch.object(
            guest_smoke.shutil,
            "which",
            side_effect=lambda command: None if command == "xvfb-run" else "/usr/bin/Xvfb",
        ), patch.object(guest_smoke.subprocess, "Popen", return_value=server), patch.object(
            guest_smoke,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as runner:
            guest_smoke.gui_smoke()

        self.assertEqual(runner.call_args.kwargs["env"]["DISPLAY"], ":7")
        server.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
