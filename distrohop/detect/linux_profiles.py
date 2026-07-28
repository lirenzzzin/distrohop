"""Linux family profiles.

The detector selects one profile from os-release and local capabilities. Later
phases reuse the same profile for native commands and distro-specific wording.
Commands are argv templates, never shell strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class LinuxProfile:
    key: str
    label: str
    manager: Optional[str]
    strategy: str
    ids: Tuple[str, ...]
    id_like: Tuple[str, ...] = ()
    manager_commands: Tuple[str, ...] = ()
    app_manager: Optional[str] = None
    install_argv: Tuple[str, ...] = ()
    query_argv: Tuple[str, ...] = ()
    tk_package: Optional[str] = None
    requires_reboot: bool = False
    manual_install: bool = False


PROFILES = (
    LinuxProfile(
        key="nixos",
        label="NixOS",
        manager="nix",
        strategy="declarativa",
        ids=("nixos",),
        manager_commands=("nixos-rebuild", "nix"),
        query_argv=("nix", "search", "nixpkgs"),
        tk_package="python3Full",
        manual_install=True,
    ),
    LinuxProfile(
        key="guix",
        label="GNU Guix System",
        manager="guix",
        strategy="declarativa",
        ids=("guix", "guix-system"),
        manager_commands=("guix",),
        query_argv=("guix", "search"),
        tk_package="python-tkinter",
        manual_install=True,
    ),
    LinuxProfile(
        key="rpm-ostree",
        label="Fedora Atomic",
        manager="rpm-ostree",
        strategy="atômica",
        ids=(
            "bazzite", "bluefin", "aurora", "ublue", "silverblue", "kinoite",
            "sericea", "onyx", "sway-atomic", "budgie-atomic", "cosmic-atomic",
            "coreos", "fedora-coreos", "fedora-iot",
        ),
        manager_commands=("rpm-ostree",),
        install_argv=("rpm-ostree", "install", "{package}"),
        query_argv=("rpm-ostree", "search", "{package}"),
        tk_package="python3-tkinter",
        requires_reboot=True,
    ),
    LinuxProfile(
        key="transactional-update",
        label="openSUSE transacional",
        manager="transactional-update",
        strategy="atômica",
        ids=(
            "opensuse-microos", "microos", "opensuse-aeon", "aeon",
            "opensuse-kalpa", "kalpa",
        ),
        manager_commands=("transactional-update",),
        install_argv=("transactional-update", "pkg", "install", "{package}"),
        query_argv=("zypper", "search", "{package}"),
        tk_package="python{py_major}{py_minor}-tk",
        requires_reboot=True,
    ),
    LinuxProfile(
        key="blendos",
        label="blendOS",
        manager="akshara",
        app_manager="flatpak",
        strategy="declarativa",
        ids=("blendos", "blend-os"),
        manager_commands=("akshara",),
        query_argv=("bpkg", "search", "{package}"),
        tk_package="tk",
        requires_reboot=True,
        manual_install=True,
    ),
    LinuxProfile(
        key="vanillaos",
        label="Vanilla OS",
        manager="abroot",
        app_manager="flatpak",
        strategy="atômica",
        ids=("vanilla", "vanillaos"),
        manager_commands=("abroot",),
        install_argv=("flatpak", "install", "flathub", "{flatpak_id}"),
        query_argv=("flatpak", "search", "{package}"),
        tk_package="python3-tk",
        requires_reboot=False,
    ),
    LinuxProfile(
        key="steamos",
        label="SteamOS",
        manager="flatpak",
        app_manager="flatpak",
        strategy="atômica",
        ids=("steamos",),
        manager_commands=("flatpak",),
        install_argv=("flatpak", "install", "flathub", "{flatpak_id}"),
        query_argv=("flatpak", "search", "{package}"),
        tk_package=None,
    ),
    LinuxProfile(
        key="endless",
        label="Endless OS",
        manager="flatpak",
        app_manager="flatpak",
        strategy="atômica",
        ids=("endless", "eos"),
        manager_commands=("flatpak",),
        install_argv=("flatpak", "install", "flathub", "{flatpak_id}"),
        query_argv=("flatpak", "search", "{package}"),
        tk_package=None,
    ),
    LinuxProfile(
        key="arch",
        label="Arch Linux e derivados",
        manager="pacman",
        strategy="imperativa",
        ids=(
            "arch", "cachyos", "manjaro", "endeavouros", "garuda", "arcolinux",
            "artix", "kaos", "rebornos",
        ),
        id_like=("arch",),
        manager_commands=("pacman",),
        install_argv=("pacman", "-S", "--needed", "{package}"),
        query_argv=("pacman", "-Ss", "{package}"),
        tk_package="tk",
    ),
    LinuxProfile(
        key="debian",
        label="Debian e derivados",
        manager="apt",
        strategy="imperativa",
        ids=(
            "debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin",
            "kali", "parrot", "neon", "mx", "mxlinux", "raspbian", "deepin",
            "devuan", "pureos", "peppermint", "tuxedo",
        ),
        id_like=("debian", "ubuntu"),
        manager_commands=("apt-get", "apt"),
        install_argv=("apt-get", "install", "{package}"),
        query_argv=("apt-cache", "search", "{package}"),
        tk_package="python3-tk",
    ),
    LinuxProfile(
        key="fedora",
        label="Fedora/RHEL e derivados",
        manager="dnf",
        strategy="imperativa",
        ids=(
            "fedora", "rhel", "centos", "rocky", "almalinux", "ol",
            "nobara", "ultramarine", "eurolinux", "openmandriva",
        ),
        id_like=("fedora", "rhel", "centos"),
        manager_commands=("dnf5", "dnf"),
        install_argv=("dnf", "install", "{package}"),
        query_argv=("dnf", "search", "{package}"),
        tk_package="python3-tkinter",
    ),
    LinuxProfile(
        key="suse",
        label="openSUSE/SUSE",
        manager="zypper",
        strategy="imperativa",
        ids=(
            "opensuse", "opensuse-leap", "opensuse-tumbleweed",
            "opensuse-slowroll", "sles", "sled", "suse",
        ),
        id_like=("opensuse", "suse"),
        manager_commands=("zypper",),
        install_argv=("zypper", "install", "{package}"),
        query_argv=("zypper", "search", "{package}"),
        tk_package="python{py_major}{py_minor}-tk",
    ),
    LinuxProfile(
        key="alpine",
        label="Alpine Linux e derivados",
        manager="apk",
        strategy="imperativa",
        ids=("alpine", "postmarketos"),
        id_like=("alpine",),
        manager_commands=("apk",),
        install_argv=("apk", "add", "{package}"),
        query_argv=("apk", "search", "{package}"),
        tk_package="python3-tkinter",
    ),
    LinuxProfile(
        key="void",
        label="Void Linux",
        manager="xbps",
        strategy="imperativa",
        ids=("void",),
        id_like=("void",),
        manager_commands=("xbps-install",),
        install_argv=("xbps-install", "-S", "{package}"),
        query_argv=("xbps-query", "-Rs", "{package}"),
        tk_package="python3-tkinter",
    ),
    LinuxProfile(
        key="gentoo",
        label="Gentoo e derivados",
        manager="emerge",
        strategy="imperativa",
        ids=("gentoo", "calculate"),
        id_like=("gentoo",),
        manager_commands=("emerge",),
        install_argv=("emerge", "--ask", "{package}"),
        query_argv=("emerge", "--search", "{package}"),
        tk_package="dev-lang/python[tk]",
    ),
    LinuxProfile(
        key="solus",
        label="Solus",
        manager="eopkg",
        strategy="imperativa",
        ids=("solus",),
        id_like=("solus",),
        manager_commands=("eopkg",),
        install_argv=("eopkg", "install", "{package}"),
        query_argv=("eopkg", "search", "{package}"),
        tk_package="python3-tkinter",
    ),
    LinuxProfile(
        key="clear-linux",
        label="Clear Linux",
        manager="swupd",
        strategy="imperativa",
        ids=("clear-linux-os", "clear-linux"),
        manager_commands=("swupd",),
        install_argv=("swupd", "bundle-add", "{package}"),
        query_argv=("swupd", "search", "{package}"),
        tk_package="python3-tcl",
    ),
    LinuxProfile(
        key="slackware",
        label="Slackware",
        manager="slackpkg",
        strategy="imperativa",
        ids=("slackware", "salix"),
        id_like=("slackware",),
        manager_commands=("slackpkg",),
        install_argv=("slackpkg", "install", "{package}"),
        query_argv=("slackpkg", "search", "{package}"),
        tk_package="tk",
    ),
    LinuxProfile(
        key="mageia",
        label="Mageia",
        manager="urpmi",
        strategy="imperativa",
        ids=("mageia",),
        id_like=("mageia",),
        manager_commands=("urpmi",),
        install_argv=("urpmi", "{package}"),
        query_argv=("urpmq", "--fuzzy", "{package}"),
        tk_package="tkinter3",
    ),
    LinuxProfile(
        key="pclinuxos",
        label="PCLinuxOS",
        manager="apt-rpm",
        strategy="imperativa",
        ids=("pclinuxos",),
        manager_commands=("apt-get",),
        install_argv=("apt-get", "install", "{package}"),
        query_argv=("apt-cache", "search", "{package}"),
        tk_package="tkinter",
    ),
)


FALLBACK = LinuxProfile(
    key="generic",
    label="Linux genérico",
    manager=None,
    strategy="fallback",
    ids=(),
    manual_install=True,
)


BY_KEY: Dict[str, LinuxProfile] = {profile.key: profile for profile in PROFILES}
