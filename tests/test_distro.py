import unittest

import tempfile
from pathlib import Path

from distrohop.detect.distro import classify, detect, parse_os_release, select_profile
from distrohop.detect.linux_profiles import PROFILES


class DistroTests(unittest.TestCase):
    def test_cachyos_is_pacman_imperative(self):
        info = parse_os_release('ID=cachyos\nID_LIKE="arch"\nPRETTY_NAME="CachyOS"')
        self.assertEqual(classify(info, which=lambda _command: None), ("pacman", "imperativa"))

    def test_os_release_decodes_quotes_comments_and_escapes(self):
        info = parse_os_release(
            'ID=future\nID_LIKE="ubuntu debian"\n'
            'PRETTY_NAME="Future \\"Clean\\" Linux" # comentário\n'
        )
        self.assertEqual(info["id_like"], "ubuntu debian")
        self.assertEqual(info["pretty_name"], 'Future "Clean" Linux')

    def test_strategies(self):
        cases = {
            "nixos": ("nix", "declarativa"),
            "ubuntu": ("apt", "imperativa"),
            "fedora": ("dnf", "imperativa"),
            "alpine": ("apk", "imperativa"),
            "void": ("xbps", "imperativa"),
            "gentoo": ("emerge", "imperativa"),
            "bazzite": ("rpm-ostree", "atômica"),
            "opensuse-microos": ("transactional-update", "atômica"),
            "solus": ("eopkg", "imperativa"),
            "clear-linux-os": ("swupd", "imperativa"),
            "slackware": ("slackpkg", "imperativa"),
            "mageia": ("urpmi", "imperativa"),
            "blendos": ("akshara", "declarativa"),
            "vanilla": ("abroot", "atômica"),
            "steamos": ("flatpak", "atômica"),
            "endless": ("flatpak", "atômica"),
            "mystery": (None, "fallback"),
        }
        for distro_id, expected in cases.items():
            with self.subTest(distro_id=distro_id):
                self.assertEqual(classify({"id": distro_id}, which=lambda _command: None), expected)

    def test_atomic_variant_precedes_fedora_family(self):
        info = {"id": "fedora", "id_like": "fedora", "variant_id": "silverblue"}
        self.assertEqual(select_profile(info, which=lambda _command: None).key, "rpm-ostree")
        info = {"id": "fedora", "id_like": "fedora", "ostree_version": "44.1"}
        self.assertEqual(select_profile(info, which=lambda _command: None).key, "rpm-ostree")
        info = {"id": "future-suse", "id_like": "opensuse-microos opensuse"}
        self.assertEqual(select_profile(info, which=lambda _command: None).key, "transactional-update")

    def test_installed_atomic_tool_does_not_reclassify_regular_fedora(self):
        profile = select_profile(
            {"id": "fedora", "variant_id": "workstation"},
            which=lambda command: "/usr/bin/" + command,
        )
        self.assertEqual(profile.key, "fedora")

    def test_id_like_is_used_for_unknown_derivative(self):
        profile = select_profile(
            {"id": "future-linux", "id_like": "ubuntu debian"},
            which=lambda _command: None,
        )
        self.assertEqual(profile.key, "debian")

    def test_resolves_dnf5_command_without_changing_family(self):
        with tempfile.TemporaryDirectory() as temp:
            os_release = Path(temp) / "os-release"
            os_release.write_text("ID=fedora\nPRETTY_NAME=Fedora\n", encoding="utf-8")
            info = detect(
                os_release,
                which=lambda command: "/usr/bin/dnf5" if command == "dnf5" else None,
            )
        self.assertEqual(info["manager"], "dnf")
        self.assertEqual(info["manager_command"], "dnf5")
        self.assertEqual(info["install_argv"][0], "dnf5")

    def test_every_declared_distro_id_resolves_to_its_profile(self):
        owners = {}
        for profile in PROFILES:
            for distro_id in profile.ids:
                self.assertNotIn(distro_id, owners, "{} está duplicado".format(distro_id))
                owners[distro_id] = profile.key
                selected = select_profile({"id": distro_id}, which=lambda _command: None)
                self.assertEqual(selected.key, profile.key, distro_id)
