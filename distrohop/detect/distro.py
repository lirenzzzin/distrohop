"""Linux distribution, family and package strategy detection."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence

from distrohop.detect.linux_profiles import BY_KEY, FALLBACK, PROFILES, LinuxProfile


def parse_os_release(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        try:
            decoded = shlex.split(value, comments=True, posix=True)
            values[key.casefold()] = " ".join(decoded)
        except ValueError:
            values[key.casefold()] = value.strip("\"'")
    return values


def _tokens(info: Mapping[str, str]) -> Iterable[str]:
    yield info.get("id", "").casefold()
    yield from info.get("id_like", "").casefold().split()


def _atomic_profile(
    info: Mapping[str, str],
) -> Optional[LinuxProfile]:
    variant_id = info.get("variant_id", "").casefold()
    pretty_name = info.get("pretty_name", "").casefold()
    related = set(_tokens(info))
    if (
        related.intersection(BY_KEY["rpm-ostree"].ids)
        or variant_id in {
            "silverblue", "kinoite", "sericea", "onyx", "sway-atomic",
            "budgie-atomic", "cosmic-atomic", "coreos", "iot",
        }
        or "ostree_version" in info
    ):
        return BY_KEY["rpm-ostree"]
    if (
        related.intersection(BY_KEY["transactional-update"].ids)
        or variant_id in {"microos", "aeon", "kalpa"}
        or "microos" in pretty_name
    ):
        return BY_KEY["transactional-update"]
    return None


def select_profile(
    info: Mapping[str, str],
    which: Callable[[str], Optional[str]] = shutil.which,
) -> LinuxProfile:
    """Select exact distro first, then ordered ID_LIKE, then local capability."""
    distro_id = info.get("id", "").casefold()
    exact = next((profile for profile in PROFILES if distro_id in profile.ids), None)
    if exact and exact.strategy == "declarativa":
        return exact
    atomic = _atomic_profile(info)
    if atomic:
        return atomic
    if exact:
        return exact
    for related_id in _tokens(info):
        for profile in PROFILES:
            if related_id in profile.id_like or related_id in profile.ids:
                return profile
    # Unknown derivatives sometimes omit ID_LIKE. A known manager is safer than
    # inventing a family; this preserves native behavior when possible.
    for profile in PROFILES:
        if profile.strategy == "imperativa" and any(which(command) for command in profile.manager_commands):
            return profile
    return FALLBACK


def classify(
    info: Mapping[str, str],
    which: Callable[[str], Optional[str]] = shutil.which,
) -> tuple:
    profile = select_profile(info, which)
    return profile.manager, profile.strategy


def _read_os_release(paths: Sequence[Path]) -> Dict[str, str]:
    for path in paths:
        try:
            return parse_os_release(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return {}


def detect(
    path: Optional[Path] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, object]:
    paths = (path,) if path is not None else (Path("/etc/os-release"), Path("/usr/lib/os-release"))
    info = _read_os_release(paths)
    profile = select_profile(info, which)
    command = next((item for item in profile.manager_commands if which(item)), None)
    install_argv = list(profile.install_argv)
    query_argv = list(profile.query_argv)
    if command and install_argv and install_argv[0] in profile.manager_commands:
        install_argv[0] = command
    tk_package = profile.tk_package
    if tk_package:
        tk_package = tk_package.format(
            py_major=sys.version_info.major,
            py_minor=sys.version_info.minor,
        )
    return {
        "id": info.get("id", "unknown"),
        "name": info.get("pretty_name") or info.get("name") or "Linux desconhecido",
        "version": info.get("version_id"),
        "variant": info.get("variant"),
        "variant_id": info.get("variant_id"),
        "id_like": info.get("id_like", "").split(),
        "family": profile.key,
        "family_label": profile.label,
        "manager": profile.manager,
        "app_manager": profile.app_manager or profile.manager,
        "manager_command": command,
        "manager_available": command is not None,
        "strategy": profile.strategy,
        "install_argv": install_argv,
        "query_argv": query_argv,
        "tk_package": tk_package,
        "requires_reboot": profile.requires_reboot,
        "manual_install": profile.manual_install,
    }
