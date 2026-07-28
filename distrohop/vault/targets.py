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
) -> List[Path]:
    expected = _tree_fingerprints(source)
    published: List[Path] = []
    for target_root in targets:
        target_root = Path(target_root)
        target_root.mkdir(parents=True, exist_ok=True)
        destination = target_root / bundle_name
        if destination.exists():
            raise FileExistsError(str(destination))
        staging = target_root / ".{}.{}.partial".format(bundle_name, uuid.uuid4().hex)
        try:
            shutil.copytree(source, staging, copy_function=shutil.copy2)
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
