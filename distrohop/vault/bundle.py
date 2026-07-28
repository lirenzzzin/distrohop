"""Stable bundle layout, clear manifest and integrity verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

from distrohop.vault.crypto import decrypt_archive, encrypt_tree, sha256_file


README = """NÃO FORMATAR — bundle de migração Distrohop

manifest.json is intentionally clear text so the backup can be identified.
The payload may contain passwords, cookies, SSH/GPG keys and AI tokens.
Keep this directory private. Verify checksums before restore.

Para abrir manualmente um bundle cifrado:
  openssl enc -aes-256-cbc -d -pbkdf2 -in bundle.tar.enc -out bundle.tar -pass stdin
  mkdir payload && tar -xf bundle.tar -C payload

Sem bundle.tar.enc, os diretórios browsers/, ai/ e system/ já são o payload.
"""


def _entry(path: Path, stored: bool = True) -> Dict[str, Any]:
    return {"sha256": sha256_file(path), "size": path.stat().st_size, "stored": stored}


def _payload_entries(root: Path, stored: bool) -> Dict[str, Dict[str, Any]]:
    return {
        str(path.relative_to(root)): _entry(path, stored)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def secure_tree(root: Path) -> None:
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink():
            continue
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError:
            pass


def assemble_bundle(
    payload: Path,
    destination: Path,
    *,
    metadata: Mapping[str, Any],
    encrypted: bool,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.mkdir(parents=True, mode=0o700)
    try:
        manifest: Dict[str, Any] = dict(metadata)
        manifest.setdefault("format_version", 1)
        manifest["encrypted"] = encrypted
        if encrypted:
            plaintext_files = _payload_entries(payload, stored=False)
            encrypted_path = destination / "bundle.tar.enc"
            encrypt_tree(payload, encrypted_path, password or "")
            files = plaintext_files
            files["bundle.tar.enc"] = _entry(encrypted_path, stored=True)
        else:
            for item in sorted(payload.iterdir()):
                target = destination / item.name
                if item.is_dir():
                    shutil.copytree(item, target, symlinks=False)
                else:
                    shutil.copy2(item, target, follow_symlinks=True)
            files = _payload_entries(destination, stored=True)
        (destination / "README.txt").write_text(README, encoding="utf-8")
        files["README.txt"] = _entry(destination / "README.txt", stored=True)
        manifest["files"] = files
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        secure_tree(destination)
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def verify_bundle(bundle: Path) -> bool:
    try:
        root = bundle.resolve()
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["files"]
        if not isinstance(entries, dict):
            return False
        for relative, details in entries.items():
            if details.get("stored", True) is False:
                continue
            path = (bundle / relative).resolve()
            if path != root and root not in path.parents:
                return False
            if not path.is_file():
                return False
            if path.stat().st_size != details["size"] or sha256_file(path) != details["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_manifest(bundle: Path) -> Dict[str, Any]:
    try:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ValueError("manifest.json inválido: {}".format(error)) from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("manifest.json não contém o mapa de arquivos")
    return manifest


@contextmanager
def materialize_payload(
    bundle: Path,
    *,
    password: Optional[str] = None,
) -> Iterator[Path]:
    manifest = read_manifest(bundle)
    if not verify_bundle(bundle):
        raise ValueError("checksums do bundle não conferem")
    if not manifest.get("encrypted"):
        yield bundle
        return
    if not password:
        raise ValueError("o bundle cifrado exige senha")
    encrypted = bundle / "bundle.tar.enc"
    with tempfile.TemporaryDirectory(prefix="distrohop-restore-") as directory:
        payload = Path(directory) / "payload"
        decrypt_archive(encrypted, payload, password)
        yield payload


def verify_materialized_payload(bundle: Path, payload: Path) -> bool:
    try:
        manifest = read_manifest(bundle)
        for relative, details in manifest["files"].items():
            if details.get("stored", True):
                continue
            path = (payload / relative).resolve()
            root = payload.resolve()
            if path != root and root not in path.parents:
                return False
            if not path.is_file():
                return False
            if path.stat().st_size != details["size"]:
                return False
            if sha256_file(path) != details["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
