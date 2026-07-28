"""Windows Chromium capture using DPAPI and AES-256-GCM."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from distrohop.capture.aesgcm import AuthenticationError, decrypt, encrypt
from distrohop.capture.neutral import write_bookmarks, write_cookies, write_logins
from distrohop.capture.profile_raw import capture_raw_profile, sqlite_snapshot


class WindowsCryptoError(RuntimeError):
    pass


class AppBoundEncryption(WindowsCryptoError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def unprotect_dpapi(data: bytes) -> bytes:
    if os.name != "nt":
        raise WindowsCryptoError("DPAPI só está disponível no Windows")
    buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    destination = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(destination),
    ):
        raise WindowsCryptoError(
            "CryptUnprotectData falhou com código {}".format(
                ctypes.get_last_error()
            )
        )
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)


def _decoded_key(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise WindowsCryptoError("Local State não contém chave Chromium")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise WindowsCryptoError("chave Chromium em base64 inválida") from error


def load_master_key(
    local_state: Path,
    *,
    dpapi_unprotect: Callable[[bytes], bytes] = unprotect_dpapi,
) -> bytes:
    try:
        state = json.loads(local_state.read_text(encoding="utf-8"))
        os_crypt = state["os_crypt"]
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise WindowsCryptoError("Local State inválido: {}".format(error)) from error
    app_bound = os_crypt.get("app_bound_encrypted_key")
    if app_bound:
        encoded = _decoded_key(app_bound)
        if encoded.startswith(b"APPB"):
            raise AppBoundEncryption(
                "perfil usa App-Bound Encryption; cookies podem exigir "
                "consentimento do próprio navegador"
            )
        raise AppBoundEncryption("perfil contém chave app-bound desconhecida")
    encoded = _decoded_key(os_crypt.get("encrypted_key"))
    if encoded.startswith(b"APPB"):
        raise AppBoundEncryption("perfil usa App-Bound Encryption")
    if not encoded.startswith(b"DPAPI"):
        raise WindowsCryptoError("chave Chromium não tem prefixo DPAPI")
    key = dpapi_unprotect(encoded[5:])
    if len(key) != 32:
        raise WindowsCryptoError(
            "DPAPI devolveu chave AES de tamanho inválido: {}".format(len(key))
        )
    return key


def decrypt_chromium_bytes(
    encrypted_value: bytes,
    *,
    master_key: Optional[bytes],
    dpapi_unprotect: Callable[[bytes], bytes] = unprotect_dpapi,
) -> bytes:
    if not encrypted_value:
        return b""
    if encrypted_value[:3] in (b"v10", b"v11"):
        if master_key is None:
            raise AppBoundEncryption("valor AES-GCM sem chave DPAPI disponível")
        if len(encrypted_value) < 3 + 12 + 16:
            raise WindowsCryptoError("valor Chromium AES-GCM truncado")
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:-16]
        tag = encrypted_value[-16:]
        try:
            return decrypt(master_key, nonce, ciphertext, tag)
        except AuthenticationError as error:
            raise WindowsCryptoError("cookie Chromium com tag GCM inválida") from error
    if encrypted_value.startswith(b"v20"):
        raise AppBoundEncryption(
            "valor usa App-Bound Encryption v20 e não pode ser aberto por DPAPI"
        )
    return dpapi_unprotect(encrypted_value)


def decrypt_chromium_value(
    encrypted_value: bytes,
    *,
    master_key: Optional[bytes],
    dpapi_unprotect: Callable[[bytes], bytes] = unprotect_dpapi,
) -> str:
    return decrypt_chromium_bytes(
        encrypted_value,
        master_key=master_key,
        dpapi_unprotect=dpapi_unprotect,
    ).decode("utf-8", errors="replace")


def encrypt_chromium_bytes(
    value: bytes,
    *,
    master_key: bytes,
    nonce: Optional[bytes] = None,
) -> bytes:
    """Encode a Windows Chromium v10 value with its profile master key."""
    if len(master_key) != 32:
        raise WindowsCryptoError("chave Chromium precisa ter 32 bytes")
    selected_nonce = nonce if nonce is not None else os.urandom(12)
    if len(selected_nonce) != 12:
        raise ValueError("nonce Chromium precisa ter 12 bytes")
    ciphertext, tag = encrypt(master_key, selected_nonce, value)
    return b"v10" + selected_nonce + ciphertext + tag


def encrypt_chromium_value(
    value: str,
    *,
    master_key: bytes,
    host_key: str = "",
    modern_cookie: bool = False,
    nonce: Optional[bytes] = None,
) -> bytes:
    plaintext = value.encode("utf-8")
    if modern_cookie:
        import hashlib

        plaintext = hashlib.sha256(host_key.encode("utf-8")).digest() + plaintext
    return encrypt_chromium_bytes(
        plaintext,
        master_key=master_key,
        nonce=nonce,
    )


def _rows(path: Path, table: str) -> List[Dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="distrohop-win-sqlite-") as directory:
        snapshot = Path(directory) / path.name
        sqlite_snapshot(path, snapshot)
        connection = sqlite3.connect(str(snapshot))
        connection.row_factory = sqlite3.Row
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM [{}]".format(table)
                )
            ]
        finally:
            connection.close()


def _cookie_database(profile: Path) -> Optional[Path]:
    return next(
        (
            path
            for path in (
                profile / "Network" / "Cookies",
                profile / "Cookies",
            )
            if path.is_file()
        ),
        None,
    )


def _cookies(
    profile: Path,
    master_key: Optional[bytes],
    warnings: List[str],
    dpapi_unprotect: Callable[[bytes], bytes],
) -> List[Dict[str, Any]]:
    database = _cookie_database(profile)
    if database is None:
        return []
    records: List[Dict[str, Any]] = []
    for row in _rows(database, "cookies"):
        encrypted = row.get("encrypted_value") or b""
        try:
            value = str(row.get("value") or "") or decrypt_chromium_value(
                bytes(encrypted),
                master_key=master_key,
                dpapi_unprotect=dpapi_unprotect,
            )
        except WindowsCryptoError as error:
            warnings.append(
                "cookie {}/{} pulado: {}".format(
                    row.get("host_key", ""),
                    row.get("name", ""),
                    error,
                )
            )
            continue
        records.append({
            "host": row.get("host_key", ""),
            "name": row.get("name", ""),
            "value": value,
            "path": row.get("path", "/"),
            "expires_utc": row.get("expires_utc", 0),
            "secure": bool(row.get("is_secure", 0)),
            "http_only": bool(row.get("is_httponly", 0)),
            "same_site": row.get("samesite", 0),
            "source_scheme": row.get("source_scheme", 0),
            "creation_utc": row.get("creation_utc", 0),
            "last_access_utc": row.get("last_access_utc", 0),
            "last_update_utc": row.get("last_update_utc", 0),
            "source_engine": "chromium",
        })
    return records


def _logins(
    profile: Path,
    master_key: Optional[bytes],
    warnings: List[str],
    dpapi_unprotect: Callable[[bytes], bytes],
) -> List[Dict[str, Any]]:
    database = profile / "Login Data"
    if not database.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for row in _rows(database, "logins"):
        try:
            password = decrypt_chromium_value(
                bytes(row.get("password_value") or b""),
                master_key=master_key,
                dpapi_unprotect=dpapi_unprotect,
            )
        except WindowsCryptoError as error:
            warnings.append(
                "senha {} pulada: {}".format(
                    row.get("origin_url") or row.get("signon_realm") or "",
                    error,
                )
            )
            continue
        records.append({
            "origin": row.get("origin_url") or row.get("signon_realm") or "",
            "action": row.get("action_url", ""),
            "username": row.get("username_value", ""),
            "password": password,
            "date_created": row.get("date_created", 0),
            "date_last_used": row.get("date_last_used", 0),
            "date_password_modified": row.get("date_password_modified", 0),
        })
    return records


def _walk_bookmarks(nodes: Iterable[Mapping[str, Any]]) -> Iterable[Dict[str, Any]]:
    for node in nodes:
        if node.get("type") == "url" and node.get("url"):
            yield {
                "title": node.get("name") or node["url"],
                "url": node["url"],
                "date_added": node.get("date_added", 0),
            }
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk_bookmarks(children)


def _bookmarks(profile: Path) -> List[Dict[str, Any]]:
    path = profile / "Bookmarks"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    roots = data.get("roots", {})
    return list(
        _walk_bookmarks(
            root for root in roots.values() if isinstance(root, dict)
        )
    )


def capture_profile(
    profile: Path,
    destination: Path,
    *,
    user_data_root: Optional[Path] = None,
    key_loader: Callable[..., bytes] = load_master_key,
    dpapi_unprotect: Callable[[bytes], bytes] = unprotect_dpapi,
) -> Dict[str, Any]:
    warnings = capture_raw_profile(profile, destination / "raw")
    root = user_data_root or profile.parent
    master_key: Optional[bytes] = None
    try:
        master_key = key_loader(
            root / "Local State",
            dpapi_unprotect=dpapi_unprotect,
        )
    except AppBoundEncryption as error:
        warnings.append(str(error))
    except WindowsCryptoError as error:
        warnings.append("chave Chromium não aberta: {}".format(error))
    try:
        cookies = _cookies(profile, master_key, warnings, dpapi_unprotect)
    except (OSError, sqlite3.Error) as error:
        cookies = []
        warnings.append("cookies Chromium não lidos: {}".format(error))
    try:
        logins = _logins(profile, master_key, warnings, dpapi_unprotect)
    except (OSError, sqlite3.Error) as error:
        logins = []
        warnings.append("senhas Chromium não lidas: {}".format(error))
    try:
        bookmarks = _bookmarks(profile)
    except (OSError, ValueError, TypeError) as error:
        bookmarks = []
        warnings.append("favoritos Chromium não lidos: {}".format(error))
    neutral = destination / "neutral"
    write_cookies(cookies, neutral / "cookies.jsonl")
    write_logins(logins, neutral / "logins.csv")
    write_bookmarks(bookmarks, neutral / "bookmarks.html")
    return {
        "cookies": len(cookies),
        "logins": len(logins),
        "bookmarks": len(bookmarks),
        "warnings": warnings,
        "app_bound": any("App-Bound" in warning for warning in warnings),
    }
