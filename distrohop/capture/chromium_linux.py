"""Linux Chromium credential capture and AES-CBC compatibility helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from distrohop.capture.neutral import write_bookmarks, write_cookies, write_logins
from distrohop.capture.profile_raw import capture_raw_profile, sqlite_snapshot


SALTY_SALT = b"saltysalt"
LINUX_IV = b" " * 16


class ChromiumDecryptionError(RuntimeError):
    pass


def derive_key(password: bytes) -> bytes:
    """Derive Chromium's historical Linux AES-128 key."""
    return hashlib.pbkdf2_hmac("sha1", password, SALTY_SALT, 1, dklen=16)


def _aes_cbc_decrypt(
    ciphertext: bytes,
    key: bytes,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bytes:
    try:
        result = runner(
            [
                "openssl",
                "enc",
                "-aes-128-cbc",
                "-d",
                "-K",
                key.hex(),
                "-iv",
                LINUX_IV.hex(),
            ],
            input=ciphertext,
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise ChromiumDecryptionError("openssl não está disponível") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ChromiumDecryptionError("OpenSSL não decriptou o valor: {}".format(detail))
    return result.stdout


def strip_cookie_domain_hash(plaintext: bytes, host_key: str) -> bytes:
    """Strip Chrome 130+'s SHA-256(host_key) prefix, but never guess."""
    if host_key and len(plaintext) >= 32:
        expected = hashlib.sha256(host_key.encode("utf-8")).digest()
        if plaintext[:32] == expected:
            return plaintext[32:]
    return plaintext


def decrypt_chromium_bytes(
    encrypted_value: bytes,
    *,
    host_key: str = "",
    secret: Optional[bytes] = None,
    is_cookie: bool = True,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bytes:
    if not encrypted_value:
        return b""
    if encrypted_value.startswith(b"v10"):
        password = b"peanuts"
    elif encrypted_value.startswith(b"v11"):
        if secret is None:
            raise ChromiumDecryptionError(
                "valor v11 exige o segredo do keyring GNOME/KWallet"
            )
        password = secret
    else:
        return encrypted_value
    plaintext = _aes_cbc_decrypt(encrypted_value[3:], derive_key(password), runner)
    return strip_cookie_domain_hash(plaintext, host_key) if is_cookie else plaintext


def decrypt_chromium_value(
    encrypted_value: bytes,
    *,
    host_key: str = "",
    secret: Optional[bytes] = None,
    is_cookie: bool = True,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    return decrypt_chromium_bytes(
        encrypted_value,
        host_key=host_key,
        secret=secret,
        is_cookie=is_cookie,
        runner=runner,
    ).decode("utf-8", errors="replace")


def _secret_labels(browser_id: str) -> List[str]:
    normalized = browser_id.casefold()
    labels = {
        "brave": ["Brave Safe Storage", "Brave-Browser Safe Storage"],
        "chrome": ["Chrome Safe Storage", "Google Chrome Safe Storage"],
        "chromium": ["Chromium Safe Storage"],
        "edge": ["Microsoft Edge Safe Storage", "Edge Safe Storage"],
        "vivaldi": ["Vivaldi Safe Storage"],
        "opera": ["Opera Safe Storage"],
    }
    return labels.get(normalized, ["{} Safe Storage".format(browser_id.title())])


def get_keyring_secret(
    browser_id: str,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[bytes]:
    """Query Secret Service and KWallet CLIs, returning None on clean degradation."""
    applications = [browser_id.casefold()]
    if browser_id.casefold() == "chrome":
        applications.append("chrome")
    secret_tool = which("secret-tool")
    if secret_tool:
        for application in dict.fromkeys(applications):
            try:
                result = runner(
                    [secret_tool, "lookup", "application", application],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and result.stdout.rstrip(b"\r\n"):
                return result.stdout.rstrip(b"\r\n")
    kwallet = which("kwallet-query")
    if kwallet:
        for wallet in ("kdewallet", "kdewallet5"):
            for folder in ("Chrome Keys", "Chromium Keys"):
                for label in _secret_labels(browser_id):
                    try:
                        result = runner(
                            [kwallet, "-f", folder, "-r", label, wallet],
                            check=False,
                            capture_output=True,
                            timeout=5,
                        )
                    except (OSError, subprocess.SubprocessError):
                        continue
                    if result.returncode == 0 and result.stdout.rstrip(b"\r\n"):
                        return result.stdout.rstrip(b"\r\n")
    return None


def _sqlite_rows(path: Path, table: str) -> List[Dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="distrohop-sqlite-") as directory:
        snapshot = Path(directory) / path.name
        sqlite_snapshot(path, snapshot)
        connection = sqlite3.connect(str(snapshot))
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute("SELECT * FROM [{}]".format(table))]
        finally:
            connection.close()


def _decrypt_field(
    row: Dict[str, Any],
    encrypted_name: str,
    plaintext_name: str,
    *,
    host_key: str,
    secret: Optional[bytes],
    is_cookie: bool,
) -> str:
    plaintext = row.get(plaintext_name)
    if plaintext:
        return str(plaintext)
    encrypted = row.get(encrypted_name) or b""
    if isinstance(encrypted, str):
        encrypted = encrypted.encode("utf-8")
    return decrypt_chromium_value(
        encrypted,
        host_key=host_key,
        secret=secret,
        is_cookie=is_cookie,
    )


def _find_cookie_database(profile: Path) -> Optional[Path]:
    return next(
        (path for path in (profile / "Network" / "Cookies", profile / "Cookies") if path.is_file()),
        None,
    )


def _capture_cookies(
    profile: Path,
    secret: Optional[bytes],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    database = _find_cookie_database(profile)
    if database is None:
        return []
    records: List[Dict[str, Any]] = []
    for row in _sqlite_rows(database, "cookies"):
        host = str(row.get("host_key", ""))
        try:
            value = _decrypt_field(
                row,
                "encrypted_value",
                "value",
                host_key=host,
                secret=secret,
                is_cookie=True,
            )
        except ChromiumDecryptionError as error:
            warnings.append("cookie {}/{} pulado: {}".format(host, row.get("name", ""), error))
            continue
        records.append(
            {
                "host": host,
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
            }
        )
    return records


def _capture_logins(
    profile: Path,
    secret: Optional[bytes],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    database = profile / "Login Data"
    if not database.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for row in _sqlite_rows(database, "logins"):
        origin = str(row.get("origin_url") or row.get("signon_realm") or "")
        try:
            password = _decrypt_field(
                row,
                "password_value",
                "",
                host_key="",
                secret=secret,
                is_cookie=False,
            )
        except ChromiumDecryptionError as error:
            warnings.append("senha {} pulada: {}".format(origin, error))
            continue
        records.append(
            {
                "origin": origin,
                "action": row.get("action_url", ""),
                "username": row.get("username_value", ""),
                "password": password,
                "date_created": row.get("date_created", 0),
                "date_last_used": row.get("date_last_used", 0),
                "date_password_modified": row.get("date_password_modified", 0),
                "blacklisted": bool(row.get("blacklisted_by_user", 0)),
            }
        )
    return records


def _walk_bookmarks(nodes: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for node in nodes:
        if node.get("type") == "url" and node.get("url"):
            yield {
                "title": node.get("name") or node.get("url"),
                "url": node["url"],
                "date_added": node.get("date_added", 0),
            }
        children = node.get("children")
        if isinstance(children, list):
            yield from _walk_bookmarks(children)


def _capture_bookmarks(profile: Path) -> List[Dict[str, Any]]:
    path = profile / "Bookmarks"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    roots = payload.get("roots", {})
    nodes = [root for root in roots.values() if isinstance(root, dict)]
    return list(_walk_bookmarks(nodes))


def capture_profile(
    profile: Path,
    destination: Path,
    *,
    browser_id: str,
    secret_provider: Callable[[str], Optional[bytes]] = get_keyring_secret,
) -> Dict[str, Any]:
    raw = destination / "raw"
    neutral = destination / "neutral"
    warnings = capture_raw_profile(profile, raw)
    secret = secret_provider(browser_id)
    try:
        cookies = _capture_cookies(profile, secret, warnings)
    except (OSError, sqlite3.Error) as error:
        cookies = []
        warnings.append("cookies Chromium não lidos: {}".format(error))
    try:
        logins = _capture_logins(profile, secret, warnings)
    except (OSError, sqlite3.Error) as error:
        logins = []
        warnings.append("senhas Chromium não lidas: {}".format(error))
    try:
        bookmarks = _capture_bookmarks(profile)
    except (OSError, ValueError, TypeError) as error:
        bookmarks = []
        warnings.append("favoritos Chromium não lidos: {}".format(error))
    write_cookies(cookies, neutral / "cookies.jsonl")
    write_logins(logins, neutral / "logins.csv")
    write_bookmarks(bookmarks, neutral / "bookmarks.html")
    return {
        "cookies": len(cookies),
        "logins": len(logins),
        "bookmarks": len(bookmarks),
        "warnings": warnings,
    }
