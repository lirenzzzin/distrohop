"""Opt-in OpenSSL encryption without exposing passwords in process arguments."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Callable


class CryptoError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _openssl(
    arguments: list,
    password: str,
    runner: Callable[..., subprocess.CompletedProcess],
) -> None:
    if "\n" in password or "\r" in password:
        raise ValueError("a senha não pode conter quebra de linha")
    try:
        result = runner(
            arguments,
            input=password.encode("utf-8") + b"\n",
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise CryptoError("openssl não está disponível") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CryptoError("OpenSSL falhou: {}".format(detail))


def encrypt_tree(
    source: Path,
    destination: Path,
    password: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    if not password:
        raise ValueError("a senha de cifra não pode ser vazia")
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="distrohop-crypto-") as temporary:
        archive = Path(temporary) / "bundle.tar"
        with tarfile.open(archive, "w") as tar:
            for item in sorted(source.rglob("*")):
                tar.add(item, arcname=str(item.relative_to(source)), recursive=False)
        partial = destination.with_name(destination.name + ".partial")
        try:
            _openssl(
                [
                    "openssl",
                    "enc",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-salt",
                    "-in",
                    str(archive),
                    "-out",
                    str(partial),
                    "-pass",
                    "stdin",
                ],
                password,
                runner,
            )
            os.chmod(partial, 0o600)
            partial.replace(destination)
        finally:
            if partial.exists():
                partial.unlink()
    return sha256_file(destination)


def decrypt_archive(
    source: Path,
    destination: Path,
    password: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    if destination.exists():
        raise FileExistsError(str(destination))
    with tempfile.TemporaryDirectory(prefix="distrohop-decrypt-") as temporary:
        archive = Path(temporary) / "bundle.tar"
        _openssl(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-d",
                "-pbkdf2",
                "-in",
                str(source),
                "-out",
                str(archive),
                "-pass",
                "stdin",
            ],
            password,
            runner,
        )
        destination.mkdir(parents=True, mode=0o700)
        try:
            with tarfile.open(archive, "r") as tar:
                root = destination.resolve()
                for member in tar.getmembers():
                    if not (member.isfile() or member.isdir()):
                        raise CryptoError("tipo de arquivo inseguro no bundle cifrado")
                    resolved = (destination / member.name).resolve()
                    if resolved != root and root not in resolved.parents:
                        raise CryptoError("caminho inseguro no arquivo cifrado")
                tar.extractall(destination)
            for path in [destination, *destination.rglob("*")]:
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except Exception:
            import shutil

            shutil.rmtree(destination)
            raise
