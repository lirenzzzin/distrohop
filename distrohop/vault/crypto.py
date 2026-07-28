"""Opt-in OpenSSL encryption without exposing passwords in process arguments."""

from __future__ import annotations

import hashlib
import os
import platform
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO, Callable, Optional

from distrohop.capture.aesgcm import AuthenticationError, decrypt, encrypt


class CryptoError(RuntimeError):
    pass


WINDOWS_MAGIC = b"DHG1"
WINDOWS_VERSION = 1
WINDOWS_ITERATIONS = 600_000
WINDOWS_CHUNK_SIZE = 1024 * 1024
_HEADER = struct.Struct(">4sBII16s8s")
_LENGTH = struct.Struct(">I")


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


def _tar_tree(source: Path, archive: Path) -> None:
    with tarfile.open(archive, "w") as tar:
        for item in sorted(source.rglob("*")):
            tar.add(
                item,
                arcname=item.relative_to(source).as_posix(),
                recursive=False,
            )


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise CryptoError("bundle AES-GCM truncado")
    return value


def _password_key(password: str, salt: bytes, iterations: int) -> bytes:
    if not password:
        raise ValueError("a senha de cifra não pode ser vazia")
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )


def _encrypt_windows_archive(
    archive: Path,
    destination: Path,
    password: str,
    *,
    iterations: int,
    chunk_size: int,
) -> None:
    if iterations < 10_000 or iterations > 10_000_000:
        raise ValueError("iterações PBKDF2 fora do limite seguro")
    if chunk_size < 1024 or chunk_size > 16 * 1024 * 1024:
        raise ValueError("tamanho de bloco AES-GCM fora do limite")
    salt = os.urandom(16)
    nonce_prefix = os.urandom(8)
    header = _HEADER.pack(
        WINDOWS_MAGIC,
        WINDOWS_VERSION,
        iterations,
        chunk_size,
        salt,
        nonce_prefix,
    )
    key = _password_key(password, salt, iterations)
    with archive.open("rb") as source, destination.open("xb") as output:
        output.write(header)
        counter = 0
        while True:
            block = source.read(chunk_size)
            length = _LENGTH.pack(len(block))
            nonce = nonce_prefix + counter.to_bytes(4, "big")
            ciphertext, tag = encrypt(
                key,
                nonce,
                block,
                header + counter.to_bytes(4, "big") + length,
            )
            output.write(length)
            output.write(ciphertext)
            output.write(tag)
            counter += 1
            if not block:
                break


def _decrypt_windows_archive(
    source: Path,
    archive: Path,
    password: str,
) -> None:
    try:
        with source.open("rb") as encrypted:
            header = _read_exact(encrypted, _HEADER.size)
            magic, version, iterations, chunk_size, salt, nonce_prefix = (
                _HEADER.unpack(header)
            )
            if magic != WINDOWS_MAGIC or version != WINDOWS_VERSION:
                raise CryptoError("formato de bundle AES-GCM desconhecido")
            if iterations < 10_000 or iterations > 10_000_000:
                raise CryptoError("iterações PBKDF2 inválidas no bundle")
            if chunk_size < 1024 or chunk_size > 16 * 1024 * 1024:
                raise CryptoError("tamanho de bloco inválido no bundle")
            key = _password_key(password, salt, iterations)
            counter = 0
            with archive.open("xb") as output:
                while True:
                    length_data = _read_exact(encrypted, _LENGTH.size)
                    length = _LENGTH.unpack(length_data)[0]
                    if length > chunk_size:
                        raise CryptoError("bloco AES-GCM maior que o permitido")
                    ciphertext = _read_exact(encrypted, length)
                    tag = _read_exact(encrypted, 16)
                    nonce = nonce_prefix + counter.to_bytes(4, "big")
                    plaintext = decrypt(
                        key,
                        nonce,
                        ciphertext,
                        tag,
                        header + counter.to_bytes(4, "big") + length_data,
                    )
                    counter += 1
                    if length == 0:
                        if encrypted.read(1):
                            raise CryptoError(
                                "dados inesperados após o fim do bundle AES-GCM"
                            )
                        break
                    output.write(plaintext)
    except AuthenticationError as error:
        raise CryptoError("senha incorreta ou bundle AES-GCM adulterado") from error


def encrypt_tree(
    source: Path,
    destination: Path,
    password: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    *,
    system: Optional[str] = None,
    iterations: int = WINDOWS_ITERATIONS,
    chunk_size: int = WINDOWS_CHUNK_SIZE,
) -> str:
    if not password:
        raise ValueError("a senha de cifra não pode ser vazia")
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="distrohop-crypto-") as temporary:
        archive = Path(temporary) / "bundle.tar"
        _tar_tree(source, archive)
        partial = destination.with_name(destination.name + ".partial")
        try:
            selected_system = (system or platform.system()).casefold()
            if selected_system == "windows":
                _encrypt_windows_archive(
                    archive,
                    partial,
                    password,
                    iterations=iterations,
                    chunk_size=chunk_size,
                )
            else:
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
        with source.open("rb") as stream:
            magic = stream.read(len(WINDOWS_MAGIC))
        if magic == WINDOWS_MAGIC:
            _decrypt_windows_archive(source, archive, password)
        else:
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
