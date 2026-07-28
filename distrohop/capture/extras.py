"""Capture AI state, SSH/GPG material, portable dotfiles and package inventory."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from distrohop.capture.profile_raw import copy_path


DOTFILE_PATHS = (
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".gitconfig",
    ".config/fish",
    ".config/starship.toml",
)

NIX_STORE_BINARY = re.compile(
    r"/nix/store/[0-9a-z]{32}-[^/\"'\s]+/bin/([A-Za-z0-9._+-]+)"
)

PACKAGE_COMMANDS: Mapping[str, Sequence[Tuple[str, Sequence[str]]]] = {
    "pacman": (
        ("explicit", ("pacman", "-Qqe")),
        ("foreign", ("pacman", "-Qqm")),
    ),
    "apt": (("installed", ("dpkg-query", "-W", "-f=${binary:Package}\\n")),),
    "dnf": (
        ("userinstalled", ("dnf", "repoquery", "--userinstalled", "--qf", "%{name}")),
        ("userinstalled-dnf5", ("dnf5", "repoquery", "--userinstalled", "--qf", "%{name}")),
        ("installed", ("rpm", "-qa", "--qf", "%{NAME}\\n")),
    ),
    "rpm-ostree": (("installed", ("rpm-ostree", "status", "--json")),),
    "zypper": (("installed", ("rpm", "-qa", "--qf", "%{NAME}\\n")),),
    "transactional-update": (("installed", ("rpm", "-qa", "--qf", "%{NAME}\\n")),),
    "apk": (("installed", ("apk", "info", "-vv")),),
    "xbps": (("manual", ("xbps-query", "-m")),),
    "emerge": (
        ("installed", ("qlist", "-ICv")),
    ),
    "eopkg": (("installed", ("eopkg", "list-installed")),),
    "swupd": (("bundles", ("swupd", "bundle-list")),),
    "nix": (
        ("profile", ("nix", "profile", "list", "--json")),
        ("legacy-profile", ("nix-env", "-q")),
    ),
    "guix": (("manifest", ("guix", "package", "--list-installed")),),
    "slackpkg": (("installed", ("slackpkg", "search", ".")),),
    "urpmi": (("installed", ("rpm", "-qa", "--qf", "%{NAME}\\n")),),
    "apt-rpm": (("installed", ("rpm", "-qa", "--qf", "%{NAME}\\n")),),
    "abroot": (("status", ("abroot", "status")),),
    "akshara": (("status", ("akshara", "status")),),
    "flatpak": (),
    "winget": (
        ("installed", ("winget", "list", "--accept-source-agreements")),
    ),
    "choco": (("installed", ("choco", "list")),),
}

UNIVERSAL_COMMANDS: Sequence[Tuple[str, Sequence[str]]] = (
    ("flatpak", ("flatpak", "list", "--app", "--columns=application")),
    ("snap", ("snap", "list")),
)


def _safe_component(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "._-" else "_" for character in value)
    return safe.strip("._") or "item"


def copy_resolved(source: Path, destination: Path) -> List[str]:
    """Copy symlink targets as real data so /nix/store links remain portable."""
    return copy_path(source, destination)


def sanitize_nix_store_paths(root: Path) -> List[str]:
    """Make copied text configs portable without touching their source."""
    warnings: List[str] = []
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw or b"/nix/store/" not in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        sanitized, replacements = NIX_STORE_BINARY.subn(r"\1", text)
        if not replacements:
            warnings.append(
                "{} contém referência /nix/store não sanitizada; revise manualmente".format(
                    path
                )
            )
            continue
        temporary = path.with_name(
            ".{}.{}.partial".format(path.name, uuid.uuid4().hex)
        )
        try:
            temporary.write_text(sanitized, encoding="utf-8")
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        warnings.append(
            "{}: {} caminho(s) /nix/store convertido(s) para comando portátil".format(
                path,
                replacements,
            )
        )
    return warnings


def extras_sources(home: Path, selected: Iterable[str]) -> List[Path]:
    requested = set(selected)
    paths: List[Path] = []
    if "ssh" in requested and (home / ".ssh").exists():
        paths.append(home / ".ssh")
    if "gpg" in requested and (home / ".gnupg").exists():
        paths.append(home / ".gnupg")
    if "dotfiles" in requested:
        paths.extend(path for path in (home / item for item in DOTFILE_PATHS) if path.exists())
    return paths


def capture_ai_accounts(
    accounts: Iterable[Mapping[str, str]],
    destination: Path,
) -> Tuple[List[Dict[str, str]], List[str]]:
    captured: List[Dict[str, str]] = []
    warnings: List[str] = []
    for account in accounts:
        source = Path(account["path"])
        tool = _safe_component(account["tool"])
        slot = _safe_component(account["slot"])
        target = destination / tool / slot
        warnings.extend(copy_resolved(source, target))
        captured.append({"tool": tool, "slot": slot, "source": str(source)})
    return captured, warnings


def package_read_sources(manager: str) -> List[Path]:
    if manager == "slackpkg":
        paths = [Path("/var/log/packages")]
        return [path for path in paths if path.exists()]
    if manager == "emerge":
        paths = [Path("/var/db/pkg")]
        return [path for path in paths if path.exists()]
    return []


def _run_command(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess],
) -> Tuple[str, Optional[str], str]:
    if shutil.which(command[0]) is None:
        return "unavailable", None, "{} não encontrado".format(command[0])
    try:
        result = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return "failed", None, str(error)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return "failed", None, detail[0] if detail else "status {}".format(result.returncode)
    return "captured", result.stdout, ""


def _filesystem_packages(manager: str) -> Optional[str]:
    try:
        if manager == "slackpkg":
            root = Path("/var/log/packages")
            if root.is_dir():
                return "\n".join(sorted(path.name for path in root.iterdir() if path.is_file())) + "\n"
        if manager == "emerge":
            root = Path("/var/db/pkg")
            if root.is_dir():
                packages = [
                    "{}/{}".format(category.name, package.name)
                    for category in sorted(root.iterdir())
                    if category.is_dir()
                    for package in sorted(category.iterdir())
                    if package.is_dir()
                ]
                return "\n".join(packages) + "\n"
    except OSError:
        return None
    return None


def capture_packages(
    manager: str,
    destination: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    outputs: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    commands = list(PACKAGE_COMMANDS.get(manager, ())) + list(UNIVERSAL_COMMANDS)
    for label, command in commands:
        status, output, error = _run_command(command, runner)
        record: Dict[str, Any] = {"command": list(command), "status": status}
        if error:
            record["error"] = error
        outputs[label] = record
        if output is None:
            continue
        filename = "{}.txt".format(_safe_component(label))
        (destination / filename).write_text(output, encoding="utf-8")
        record["file"] = filename
    filesystem_output = _filesystem_packages(manager)
    if filesystem_output is not None:
        filename = "installed-filesystem.txt"
        (destination / filename).write_text(filesystem_output, encoding="utf-8")
        outputs["filesystem"] = {
            "sources": [str(path) for path in package_read_sources(manager)],
            "status": "captured",
            "file": filename,
        }
    native_labels = {label for label, _ in PACKAGE_COMMANDS.get(manager, ())}
    native_captured = any(
        details.get("status") == "captured"
        for label, details in outputs.items()
        if label in native_labels or label == "filesystem"
    )
    if manager and manager != "flatpak" and not native_captured:
        warnings.append(
            "não foi possível capturar o inventário nativo de pacotes ({})".format(manager)
        )
    manifest = {"manager": manager, "inventories": outputs, "warnings": warnings}
    (destination / "packages.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def capture_extras(
    home: Path,
    selected: Iterable[str],
    destination: Path,
    *,
    manager: str,
) -> Dict[str, Any]:
    requested = set(selected)
    captured: List[str] = []
    warnings: List[str] = []
    mappings = {
        "ssh": (home / ".ssh", destination / "ssh"),
        "gpg": (home / ".gnupg", destination / "gpg"),
    }
    for name, (source, target) in mappings.items():
        if name in requested and source.exists():
            warnings.extend(copy_resolved(source, target))
            captured.append(name)
    if "dotfiles" in requested:
        dotfiles = destination / "dotfiles"
        for relative in DOTFILE_PATHS:
            source = home / relative
            if source.exists():
                destination_path = dotfiles / relative
                warnings.extend(copy_resolved(source, destination_path))
                warnings.extend(sanitize_nix_store_paths(destination_path))
        captured.append("dotfiles")
    package_data: Dict[str, Any] = {}
    if "packages" in requested:
        package_data = capture_packages(manager, destination / "packages")
        warnings.extend(package_data["warnings"])
        captured.append("packages")
    return {"captured": captured, "packages": package_data, "warnings": warnings}
