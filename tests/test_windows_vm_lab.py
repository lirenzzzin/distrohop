from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import windows_vm_lab


class WindowsVmResourceTests(unittest.TestCase):
    def test_windows_11_uses_minimum_resources_and_no_gpu_passthrough(self) -> None:
        self.assertEqual(windows_vm_lab.MEMORY_MB, 4096)
        self.assertEqual(windows_vm_lab.CPUS, 2)
        self.assertEqual(windows_vm_lab.DISK_GIB, 64)
        with tempfile.TemporaryDirectory() as item, patch.object(
            windows_vm_lab,
            "_firmware",
            return_value=(Path("/firmware/code.fd"), Path("/firmware/vars.fd")),
        ):
            command = windows_vm_lab.qemu_command(Path(item), installer=True)

        joined = " ".join(command)
        self.assertEqual(command[:6], ["nice", "-n", "15", "ionice", "-c", "3"])
        self.assertIn("-m 4096", joined)
        self.assertIn("-smp 2", joined)
        self.assertIn("-display none", joined)
        self.assertIn("hostfwd=tcp:127.0.0.1:22410-:22", joined)
        self.assertNotIn("vfio", joined.casefold())
        self.assertNotIn("0.0.0.0", joined)

    def test_installed_boot_does_not_attach_iso_or_answer_disk(self) -> None:
        with tempfile.TemporaryDirectory() as item, patch.object(
            windows_vm_lab,
            "_firmware",
            return_value=(Path("/firmware/code.fd"), Path("/firmware/vars.fd")),
        ):
            command = windows_vm_lab.qemu_command(Path(item), installer=False)

        joined = " ".join(command)
        self.assertNotIn(windows_vm_lab.ISO_NAME, joined)
        self.assertNotIn("answer.img", joined)

    def test_unattended_install_does_not_weaken_defender_or_ram_gate(self) -> None:
        answer = windows_vm_lab.autounattend("Safe!Password9")
        setup = windows_vm_lab.setup_script(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest lab"
        )

        self.assertIn("BypassTPMCheck", answer)
        self.assertNotIn("BypassRAMCheck", answer)
        self.assertNotIn("DisableRealtimeMonitoring", answer + setup)
        self.assertNotIn("Add-MpPreference", answer + setup)
        self.assertIn("PasswordAuthentication no", setup)
        self.assertIn("administrators_authorized_keys", setup)

    def test_state_override_stays_outside_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as item:
            root = windows_vm_lab.state_root(
                {"DISTROHOP_WINDOWS_VM_HOME": item}
            )
        self.assertEqual(root, Path(item).resolve())
        self.assertNotEqual(root, windows_vm_lab.REPO_ROOT)

    def test_gui_and_defender_smokes_never_request_an_exclusion(self) -> None:
        source = Path(windows_vm_lab.__file__).read_text(encoding="utf-8")
        defender = source[
            source.index("def command_defender_dialog"):
            source.index("def _monitor")
        ]
        self.assertIn("continued_without_exclusion", defender)
        self.assertIn('"exclusion_requested": False', defender)
        self.assertNotIn("Add-MpPreference", defender)


if __name__ == "__main__":
    unittest.main()
