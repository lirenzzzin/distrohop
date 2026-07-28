"""Atomic publication and post-write verification for one or more targets."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Dict, Iterable, List


class TargetVerificationError(RuntimeError):
    pass


def create_private_target(parent: Path, name: str) -> Path:
    """Create exactly one private backup directory below an existing target."""
    parent_path = Path(parent).expanduser()
    candidate_name = name.strip()
    if not candidate_name:
        raise ValueError("O nome da pasta não pode ficar vazio.")
    if (
        candidate_name in (".", "..")
        or "/" in candidate_name
        or "\\" in candidate_name
        or Path(candidate_name).name != candidate_name
        or any(character in candidate_name for character in '<>:"|?*\0')
        or candidate_name.endswith((".", " "))
    ):
        raise ValueError("O nome deve identificar uma única pasta.")
    stem = candidate_name.split(".", 1)[0].casefold()
    if stem in {"con", "prn", "aux", "nul"} or (
        len(stem) == 4
        and stem[:3] in {"com", "lpt"}
        and stem[3] in "123456789"
    ):
        raise ValueError("Esse nome de pasta é reservado no Windows.")
    if not parent_path.is_dir():
        raise NotADirectoryError(
            "O destino selecionado não é uma pasta: {}".format(parent_path)
        )
    destination = parent_path / candidate_name
    destination.mkdir(mode=0o700, exist_ok=False)
    if os.name != "nt":
        destination.chmod(0o700)
    return destination


def _tree_fingerprints(root: Path) -> Dict[str, str]:
    fingerprints: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprints[str(path.relative_to(root))] = digest.hexdigest()
    return fingerprints


def _has_private_permissions(root: Path) -> bool:
    for path in [root, *root.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = 0o700 if path.is_dir() else 0o600
        if mode & ~expected:
            return False
    return True


def publish_to_targets(
    source: Path,
    targets: Iterable[Path],
    bundle_name: str,
    *,
    require_private_permissions: bool = False,
    adopt_source: bool = False,
) -> List[Path]:
    expected = _tree_fingerprints(source)
    target_roots = [Path(target) for target in targets]
    for target_root in target_roots:
        target_root.mkdir(parents=True, exist_ok=True)
        destination = target_root / bundle_name
        if destination.exists():
            raise FileExistsError(str(destination))
    published: List[Path] = []
    copy_source = source
    start = 0
    if adopt_source and target_roots:
        target_root = target_roots[0]
        destination = target_root / bundle_name
        if require_private_permissions and not _has_private_permissions(source):
            raise TargetVerificationError(
                "o destino {} não suporta permissões privadas 600/700; "
                "use --encrypt ou outro sistema de arquivos".format(target_root)
            )
        os.replace(source, destination)
        if _tree_fingerprints(destination) != expected:
            os.replace(destination, source)
            raise TargetVerificationError(
                "checksum pós-escrita divergiu em {}".format(target_root)
            )
        published.append(destination)
        copy_source = destination
        start = 1
    for target_root in target_roots[start:]:
        destination = target_root / bundle_name
        staging = target_root / ".{}.{}.partial".format(bundle_name, uuid.uuid4().hex)
        try:
            shutil.copytree(copy_source, staging, copy_function=shutil.copy2)
            if _tree_fingerprints(staging) != expected:
                raise TargetVerificationError(
                    "checksum pós-escrita divergiu em {}".format(target_root)
                )
            if require_private_permissions and not _has_private_permissions(staging):
                raise TargetVerificationError(
                    "o destino {} não suporta permissões privadas 600/700; "
                    "use --encrypt ou outro sistema de arquivos".format(target_root)
                )
            os.replace(staging, destination)
            published.append(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    return published
