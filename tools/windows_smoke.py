#!/usr/bin/env python3
"""Non-destructive smoke test executed on a real Windows CI runner."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

from distrohop import bootstrap
from distrohop.capture.chromium_win import (
    DATA_BLOB,
    capture_profile,
    encrypt_chromium_value,
    unprotect_dpapi,
)
from distrohop.core.engine import list_inventory
from distrohop.vault.crypto import decrypt_archive, encrypt_tree


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SmokeFailure(RuntimeError):
    """A failed Windows runtime contract."""


def _protect_dpapi(value: bytes) -> bytes:
    source_buffer = ctypes.create_string_buffer(value)
    source = DATA_BLOB(
        len(value),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    destination = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Distrohop Windows smoke",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(destination),
    ):
        raise SmokeFailure(
            "CryptProtectData failed with code {}".format(
                ctypes.get_last_error()
            )
        )
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)


def _create_cookie_database(path: Path, encrypted_value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE cookies (
                host_key TEXT, name TEXT, value TEXT, path TEXT,
                expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER,
                samesite INTEGER, source_scheme INTEGER,
                creation_utc INTEGER, last_access_utc INTEGER,
                last_update_utc INTEGER, encrypted_value BLOB
            )
            """
        )
        connection.execute(
            "INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ".example.test",
                "session",
                "",
                "/",
                13_400_000_000_000_000,
                1,
                1,
                0,
                2,
                13_300_000_000_000_000,
                13_300_000_000_000_000,
                13_300_000_000_000_000,
                encrypted_value,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _profile_round_trip(root: Path) -> Dict[str, Any]:
    user_data = root / "User Data"
    profile = user_data / "Default"
    profile.mkdir(parents=True)
    master_key = bytes(range(32))
    protected_key = _protect_dpapi(master_key)
    (user_data / "Local State").write_text(
        json.dumps(
            {
                "os_crypt": {
                    "encrypted_key": base64.b64encode(
                        b"DPAPI" + protected_key
                    ).decode("ascii")
                }
            }
        ),
        encoding="utf-8",
    )
    encrypted_cookie = encrypt_chromium_value(
        "portable-session",
        master_key=master_key,
        nonce=bytes(range(12)),
    )
    _create_cookie_database(
        profile / "Network" / "Cookies",
        encrypted_cookie,
    )
    (profile / "Bookmarks").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {
                                "type": "url",
                                "name": "Distrohop",
                                "url": "https://example.test/",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    captured = root / "captured"
    result = capture_profile(
        profile,
        captured,
        user_data_root=user_data,
    )
    cookies = [
        json.loads(line)
        for line in (
            captured / "neutral" / "cookies.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line
    ]
    if result["cookies"] != 1 or cookies[0]["value"] != "portable-session":
        raise SmokeFailure("Windows Chromium DPAPI capture did not round-trip")
    if result["bookmarks"] != 1:
        raise SmokeFailure("Windows Chromium bookmark capture failed")

    encrypted = root / "bundle.tar.enc"
    encrypt_tree(
        captured,
        encrypted,
        "windows-smoke-password",
        system="windows",
        iterations=10_000,
        chunk_size=4096,
    )
    restored = root / "restored"
    decrypt_archive(encrypted, restored, "windows-smoke-password")
    restored_cookies = restored / "neutral" / "cookies.jsonl"
    if restored_cookies.read_bytes() != (
        captured / "neutral" / "cookies.jsonl"
    ).read_bytes():
        raise SmokeFailure("Windows encrypted bundle did not round-trip")
    return {
        "cookies": result["cookies"],
        "bookmarks": result["bookmarks"],
        "bundle": "verified",
    }


def _powershell_defender_probe() -> Dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        bootstrap.STATUS_SCRIPT,
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    parsed = bootstrap.parse_defender_status(result.stdout)
    return {
        "query_available": result.returncode == 0,
        "active": parsed["defender_active"],
        "real_time": parsed["real_time"],
    }


def _winget_probe() -> Dict[str, Any]:
    executable = shutil.which("winget")
    if not executable:
        return {"available": False}
    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise SmokeFailure("WinGet was found but could not be executed")
    return {
        "available": True,
        "version": result.stdout.strip(),
    }


def main() -> int:
    if os.name != "nt":
        raise SmokeFailure("this smoke test must run on Windows")

    import tkinter

    if tkinter.TkVersion < 8.6:
        raise SmokeFailure("Tk 8.6 or newer is required")

    plaintext = b"distrohop-dpapi-runtime"
    if unprotect_dpapi(_protect_dpapi(plaintext)) != plaintext:
        raise SmokeFailure("CryptProtectData/CryptUnprotectData mismatch")

    inventory = list_inventory(system="Windows")
    if inventory["platform"] != "windows":
        raise SmokeFailure("Windows platform dispatch failed")
    if not any(volume.get("system") for volume in inventory["disks"]):
        raise SmokeFailure("the Windows system volume was not detected")

    with tempfile.TemporaryDirectory(prefix="distrohop-windows-smoke-") as item:
        profile = _profile_round_trip(Path(item))

    report = {
        "ok": True,
        "platform": inventory["platform"],
        "manager": inventory["os"].get("manager"),
        "system_volumes": sum(
            1 for volume in inventory["disks"] if volume.get("system")
        ),
        "detected_browsers": [
            browser["id"] for browser in inventory["browsers"]
        ],
        "defender": _powershell_defender_probe(),
        "winget": _winget_probe(),
        "tk": str(tkinter.TkVersion),
        "dpapi": "verified",
        "profile": profile,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
