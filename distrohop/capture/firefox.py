"""Firefox/Zen raw and neutral capture, including optional NSS logins."""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from distrohop.capture.neutral import write_bookmarks, write_cookies, write_logins
from distrohop.capture.profile_raw import capture_raw_profile, sqlite_snapshot


class NSSUnavailable(RuntimeError):
    pass


class SECItem(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
        ("len", ctypes.c_uint),
    ]


class NSSDecryptor:
    def __init__(self, profile: Path) -> None:
        windows_roots = (
            Path(os.environ.get(variable, "")) / relative
            for variable, relative in (
                ("PROGRAMFILES", "Mozilla Firefox"),
                ("PROGRAMFILES(X86)", "Mozilla Firefox"),
                ("PROGRAMFILES", "Zen Browser"),
                ("LOCALAPPDATA", "Programs/Zen Browser"),
            )
            if os.environ.get(variable)
        )
        candidates = [
            ctypes.util.find_library("nss3"),
            "nss3.dll",
            "libnss3.so",
            "/usr/lib/libnss3.so",
            "/usr/lib64/libnss3.so",
            "/lib/libnss3.so",
            "/lib64/libnss3.so",
            *(str(root / "nss3.dll") for root in windows_roots),
        ]
        self._nss = None
        self._dll_directories = []
        for library in candidates:
            if not library:
                continue
            try:
                candidate = Path(str(library))
                if (
                    os.name == "nt"
                    and candidate.is_absolute()
                    and hasattr(os, "add_dll_directory")
                ):
                    self._dll_directories.append(
                        os.add_dll_directory(str(candidate.parent))
                    )
                self._nss = ctypes.CDLL(library)
                break
            except OSError:
                continue
        if self._nss is None:
            raise NSSUnavailable("libnss3 não foi localizada")
        try:
            self._nss.NSS_Init.argtypes = [ctypes.c_char_p]
            self._nss.NSS_Init.restype = ctypes.c_int
            self._nss.NSS_Shutdown.argtypes = []
            self._nss.NSS_Shutdown.restype = ctypes.c_int
            self._nss.PK11SDR_Decrypt.argtypes = [
                ctypes.POINTER(SECItem),
                ctypes.POINTER(SECItem),
                ctypes.c_void_p,
            ]
            self._nss.PK11SDR_Decrypt.restype = ctypes.c_int
            self._nss.SECITEM_FreeItem.argtypes = [ctypes.POINTER(SECItem), ctypes.c_int]
        except AttributeError as error:
            raise NSSUnavailable("libnss3 não expõe as funções necessárias") from error
        if self._nss.NSS_Init("sql:{}".format(profile).encode("utf-8")) != 0:
            raise NSSUnavailable(
                "NSS não abriu o perfil; senha primária ou biblioteca incompatível"
            )
        self._open = True

    def close(self) -> None:
        if getattr(self, "_open", False):
            self._nss.NSS_Shutdown()
            self._open = False
        for directory in getattr(self, "_dll_directories", ()):
            directory.close()
        self._dll_directories = []

    def decrypt(self, encoded: str) -> str:
        encrypted = base64.b64decode(encoded)
        buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
        source = SECItem(0, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), len(encrypted))
        result = SECItem()
        if self._nss.PK11SDR_Decrypt(ctypes.byref(source), ctypes.byref(result), None) != 0:
            raise NSSUnavailable("NSS recusou a credencial; talvez haja senha primária")
        try:
            plaintext = ctypes.string_at(result.data, result.len)
            return plaintext.decode("utf-8", errors="replace")
        finally:
            self._nss.SECITEM_FreeItem(ctypes.byref(result), 0)

    def __enter__(self) -> "NSSDecryptor":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _rows(path: Path, query: str) -> List[Dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="distrohop-sqlite-") as directory:
        snapshot = Path(directory) / path.name
        sqlite_snapshot(path, snapshot)
        connection = sqlite3.connect(str(snapshot))
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(query)]
        finally:
            connection.close()


def _cookies(profile: Path) -> List[Dict[str, Any]]:
    path = profile / "cookies.sqlite"
    if not path.is_file():
        return []
    return [
        {
            "host": row.get("host", ""),
            "name": row.get("name", ""),
            "value": row.get("value", ""),
            "path": row.get("path", "/"),
            "expiry": row.get("expiry", 0),
            "secure": bool(row.get("isSecure", 0)),
            "http_only": bool(row.get("isHttpOnly", 0)),
            "same_site": row.get("sameSite", 0),
            "creation_time": row.get("creationTime", 0),
            "last_accessed": row.get("lastAccessed", 0),
            "source_engine": "firefox",
        }
        for row in _rows(path, "SELECT * FROM moz_cookies")
    ]


def _bookmarks(profile: Path) -> List[Dict[str, Any]]:
    path = profile / "places.sqlite"
    if not path.is_file():
        return []
    query = (
        "SELECT COALESCE(b.title, p.title, p.url) AS title, p.url AS url, "
        "b.dateAdded AS date_added FROM moz_bookmarks b "
        "JOIN moz_places p ON p.id = b.fk WHERE b.type = 1 AND p.url IS NOT NULL"
    )
    return _rows(path, query)


def _logins(raw_profile: Path) -> List[Dict[str, Any]]:
    path = raw_profile / "logins.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: List[Dict[str, Any]] = []
    with NSSDecryptor(raw_profile) as decryptor:
        for login in payload.get("logins", []):
            records.append(
                {
                    "origin": login.get("hostname", ""),
                    "action": login.get("formSubmitURL", ""),
                    "username": decryptor.decrypt(login.get("encryptedUsername", "")),
                    "password": decryptor.decrypt(login.get("encryptedPassword", "")),
                    "date_created": login.get("timeCreated", 0),
                    "date_last_used": login.get("timeLastUsed", 0),
                    "date_password_modified": login.get("timePasswordChanged", 0),
                }
            )
    return records


def capture_profile(
    profile: Path,
    destination: Path,
    *,
    decrypt_logins: bool = True,
) -> Dict[str, Any]:
    raw = destination / "raw"
    neutral = destination / "neutral"
    warnings = capture_raw_profile(profile, raw)
    try:
        cookies = _cookies(profile)
    except (OSError, sqlite3.Error) as error:
        cookies = []
        warnings.append("cookies Firefox não lidos: {}".format(error))
    try:
        bookmarks = _bookmarks(profile)
    except (OSError, sqlite3.Error) as error:
        bookmarks = []
        warnings.append("favoritos Firefox não lidos: {}".format(error))
    logins: List[Dict[str, Any]] = []
    if decrypt_logins and (raw / "logins.json").is_file():
        try:
            logins = _logins(raw)
        except (OSError, ValueError, NSSUnavailable) as error:
            warnings.append("senhas Firefox puladas: {}".format(error))
    write_cookies(cookies, neutral / "cookies.jsonl")
    write_logins(logins, neutral / "logins.csv")
    write_bookmarks(bookmarks, neutral / "bookmarks.html")
    return {
        "cookies": len(cookies),
        "logins": len(logins),
        "bookmarks": len(bookmarks),
        "warnings": warnings,
    }
