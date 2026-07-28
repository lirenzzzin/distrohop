"""Capability-checked browser installation plans for imperative Linux systems."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "packages.json"

QUERY_COMMANDS: Mapping[str, Tuple[str, ...]] = {
    "pacman": ("pacman", "-Si", "{package}"),
    "apt": ("apt-cache", "show", "{package}"),
    "dnf": ("dnf", "info", "{package}"),
    "zypper": ("zypper", "--non-interactive", "search", "--match-exact", "{package}"),
    "apk": ("apk", "search", "-e", "{package}"),
    "xbps": ("xbps-query", "-Rs", "{package}"),
    "emerge": ("emerge", "--search-exact", "{package}"),
    "eopkg": ("eopkg", "info", "{package}"),
    "swupd": ("swupd", "search", "{package}"),
    "slackpkg": ("slackpkg", "search", "{package}"),
    "urpmi": ("urpmq", "{package}"),
    "apt-rpm": ("apt-cache", "show", "{package}"),
}

ATOMIC_NATIVE_FAMILY: Mapping[str, str] = {
    "rpm-ostree": "fedora",
    "transactional-update": "suse",
}


def load_packages(path: Path = DATA_PATH) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("data/packages.json inválido: {}".format(error)) from error
    if not isinstance(data, dict):
        raise RuntimeError("data/packages.json deve ser um objeto")
    return data


def _available(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess],
) -> bool:
    if shutil.which(command[0]) is None:
        return False
    try:
        result = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def plan_install(
    browser_id: str,
    os_info: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    packages: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, ...]:
    definitions = packages or load_packages()
    definition = definitions.get(browser_id)
    if not isinstance(definition, dict):
        raise RuntimeError("não há receita de instalação para {}".format(browser_id))
    family = str(os_info.get("family") or "")
    manager = str(os_info.get("manager") or "")
    strategy = str(os_info.get("strategy") or "")
    if strategy in ("imperativa", "atômica"):
        query_template = QUERY_COMMANDS.get(manager)
        if strategy == "atômica":
            query_template = tuple(os_info.get("query_argv") or ()) or query_template
        install_template = tuple(os_info.get("install_argv") or ())
        native_family = ATOMIC_NATIVE_FAMILY.get(family, family)
        for package in definition.get("native", {}).get(native_family, []):
            if not query_template or not install_template:
                continue
            query = tuple(
                item.format(
                    package=package,
                    flatpak_id=definition.get("flatpak") or "",
                )
                for item in query_template
            )
            if not _available(query, runner):
                continue
            command = tuple(
                item.format(
                    package=package,
                    flatpak_id=definition.get("flatpak") or "",
                )
                for item in install_template
            )
            if command and command[0] != "flatpak" and getattr(os, "geteuid", lambda: 1)() != 0:
                command = ("sudo",) + command
            return command
        flatpak_id = definition.get("flatpak")
        if (
            strategy == "atômica"
            and flatpak_id
            and install_template
            and any("{flatpak_id}" in item for item in install_template)
        ):
            command = tuple(
                item.format(package="", flatpak_id=flatpak_id)
                for item in install_template
            )
            return command
    flatpak_id = definition.get("flatpak")
    if flatpak_id and shutil.which("flatpak"):
        return (
            "flatpak",
            "install",
            "--user",
            "--noninteractive",
            "-y",
            "flathub",
            str(flatpak_id),
        )
    raise RuntimeError(
        "nenhum pacote seguro disponível para {}; instale manualmente".format(browser_id)
    )


def run_install(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    result = runner(list(command), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "instalação falhou com status {}: {}".format(
                result.returncode, " ".join(command)
            )
        )
