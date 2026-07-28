"""Atomic cross-engine restore from Distrohop's neutral browser formats."""

from __future__ import annotations

import csv
import html
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from distrohop.capture.chromium_linux import encrypt_chromium_value
from distrohop.capture import chromium_win


CHROMIUM_EPOCH_OFFSET_SECONDS = 11_644_473_600
SESSION_LOCK_NAMES = {
    "lock",
    ".parentlock",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
}


def chromium_utc_to_firefox_ms(value: Any) -> int:
    amount = int(value or 0)
    if amount <= 0:
        return 0
    return int(amount / 1_000_000 - CHROMIUM_EPOCH_OFFSET_SECONDS) * 1000


def firefox_ms_to_chromium_utc(value: Any) -> int:
    amount = int(value or 0)
    if amount <= 0:
        return 0
    return int(amount / 1000 + CHROMIUM_EPOCH_OFFSET_SECONDS) * 1_000_000


def _read_cookies(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError) as error:
                raise ValueError(
                    "cookies.jsonl inválido na linha {}: {}".format(number, error)
                ) from error
            if not isinstance(record, dict):
                raise ValueError(
                    "cookies.jsonl linha {} não é um objeto".format(number)
                )
            records.append(record)
    return records


class _BookmarksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None
        self._label: List[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: Sequence[Tuple[str, Optional[str]]],
    ) -> None:
        if tag.casefold() != "a":
            return
        values = {name.casefold(): value or "" for name, value in attrs}
        if not values.get("href"):
            return
        self._current = {
            "url": html.unescape(values["href"]),
            "date_added": values.get("add_date", "0"),
        }
        self._label = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._current is None:
            return
        record = dict(self._current)
        record["title"] = "".join(self._label).strip() or record["url"]
        self.records.append(record)
        self._current = None
        self._label = []


def _read_bookmarks(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    parser = _BookmarksParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.records


def _columns(connection: sqlite3.Connection, table: str) -> List[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(connection.execute("PRAGMA table_info([{}])".format(table)))


def _generic_required_value(column: sqlite3.Row) -> Any:
    kind = str(column["type"] or "").casefold()
    if any(label in kind for label in ("int", "real", "numeric", "bool")):
        return 0
    if "blob" in kind:
        return sqlite3.Binary(b"")
    return ""


def _insert_dynamic(
    connection: sqlite3.Connection,
    table: str,
    values: Mapping[str, Any],
) -> int:
    schema = _columns(connection, table)
    selected: Dict[str, Any] = {}
    for column in schema:
        name = str(column["name"])
        if name in values:
            selected[name] = values[name]
        elif (
            column["notnull"]
            and column["dflt_value"] is None
            and not column["pk"]
        ):
            selected[name] = _generic_required_value(column)
    if not selected:
        raise RuntimeError("tabela {} não tem colunas compatíveis".format(table))
    names = list(selected)
    placeholders = ", ".join("?" for _ in names)
    connection.execute(
        "INSERT INTO [{}] ({}) VALUES ({})".format(
            table,
            ", ".join("[{}]".format(name) for name in names),
            placeholders,
        ),
        [selected[name] for name in names],
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _checkpoint(connection: sqlite3.Connection) -> None:
    connection.commit()
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def _firefox_cookie_values(record: Mapping[str, Any]) -> Dict[str, Any]:
    source = str(record.get("source_engine") or "")
    if source == "chromium" or "expires_utc" in record:
        expiry = chromium_utc_to_firefox_ms(record.get("expires_utc"))
        creation = chromium_utc_to_firefox_ms(record.get("creation_utc"))
        accessed = chromium_utc_to_firefox_ms(record.get("last_access_utc"))
        updated = chromium_utc_to_firefox_ms(record.get("last_update_utc"))
    else:
        expiry = int(record.get("expiry") or 0)
        creation = int(record.get("creation_time") or 0)
        accessed = int(record.get("last_accessed") or 0)
        updated = int(record.get("update_time") or accessed)
    return {
        "originAttributes": "",
        "name": str(record.get("name") or ""),
        "value": str(record.get("value") or ""),
        "host": str(record.get("host") or ""),
        "path": str(record.get("path") or "/"),
        "expiry": expiry,
        "lastAccessed": accessed,
        "creationTime": creation,
        "updateTime": updated,
        "isSecure": int(bool(record.get("secure"))),
        "isHttpOnly": int(bool(record.get("http_only"))),
        "inBrowserElement": 0,
        "sameSite": int(record.get("same_site") or 0),
        "rawSameSite": int(record.get("same_site") or 0),
        "schemeMap": 0,
        "isPartitioned": 0,
        "partitionKey": "",
    }


def _ensure_firefox_cookies(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS moz_cookies (
            id INTEGER PRIMARY KEY,
            originAttributes TEXT NOT NULL DEFAULT '',
            name TEXT,
            value TEXT,
            host TEXT,
            path TEXT,
            expiry INTEGER,
            lastAccessed INTEGER,
            creationTime INTEGER,
            updateTime INTEGER,
            isSecure INTEGER,
            isHttpOnly INTEGER,
            inBrowserElement INTEGER DEFAULT 0,
            sameSite INTEGER DEFAULT 0,
            rawSameSite INTEGER DEFAULT 0,
            schemeMap INTEGER DEFAULT 0,
            isPartitioned INTEGER DEFAULT 0,
            partitionKey TEXT DEFAULT ''
        )
        """
    )


def _apply_firefox_cookies(profile: Path, records: Iterable[Mapping[str, Any]]) -> int:
    database = profile / "cookies.sqlite"
    with closing(sqlite3.connect(str(database))) as connection:
        _ensure_firefox_cookies(connection)
        count = 0
        for record in records:
            values = _firefox_cookie_values(record)
            connection.execute(
                "DELETE FROM moz_cookies WHERE host=? AND name=? AND path=? "
                "AND originAttributes=?",
                (
                    values["host"],
                    values["name"],
                    values["path"],
                    values["originAttributes"],
                ),
            )
            _insert_dynamic(connection, "moz_cookies", values)
            count += 1
        _checkpoint(connection)
        return count


def _reverse_host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[::-1] + "." if host else ""


def _ensure_firefox_places(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS moz_places (
            id INTEGER PRIMARY KEY,
            url TEXT,
            title TEXT,
            rev_host TEXT DEFAULT '',
            visit_count INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            typed INTEGER DEFAULT 0,
            frecency INTEGER DEFAULT -1,
            last_visit_date INTEGER,
            guid TEXT,
            foreign_count INTEGER DEFAULT 1,
            url_hash INTEGER DEFAULT 0,
            description TEXT,
            preview_image_url TEXT,
            site_name TEXT,
            origin_id INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS moz_bookmarks (
            id INTEGER PRIMARY KEY,
            type INTEGER,
            fk INTEGER,
            parent INTEGER,
            position INTEGER,
            title TEXT,
            keyword_id INTEGER,
            folder_type TEXT,
            dateAdded INTEGER,
            lastModified INTEGER,
            guid TEXT,
            syncStatus INTEGER DEFAULT 0,
            syncChangeCounter INTEGER DEFAULT 1
        )
        """
    )


def _firefox_bookmark_parent(connection: sqlite3.Connection) -> int:
    columns = {str(column["name"]) for column in _columns(connection, "moz_bookmarks")}
    if "guid" in columns:
        row = connection.execute(
            "SELECT id FROM moz_bookmarks WHERE guid IN "
            "('unfiled_____', 'toolbar_____', 'menu________') LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0])
    row = connection.execute(
        "SELECT id FROM moz_bookmarks WHERE type=2 ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        return int(row[0])
    return _insert_dynamic(
        connection,
        "moz_bookmarks",
        {
            "type": 2,
            "fk": None,
            "parent": 0,
            "position": 0,
            "title": "Distrohop",
            "dateAdded": int(time.time() * 1_000_000),
            "lastModified": int(time.time() * 1_000_000),
            "guid": "distrohop___",
            "syncStatus": 0,
            "syncChangeCounter": 1,
        },
    )


def _apply_firefox_bookmarks(
    profile: Path,
    records: Iterable[Mapping[str, Any]],
) -> int:
    database = profile / "places.sqlite"
    with closing(sqlite3.connect(str(database))) as connection:
        _ensure_firefox_places(connection)
        parent = _firefox_bookmark_parent(connection)
        position_row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent=?",
            (parent,),
        ).fetchone()
        position = int(position_row[0])
        count = 0
        for record in records:
            url = str(record.get("url") or "")
            if not url:
                continue
            title = str(record.get("title") or url)
            existing = connection.execute(
                "SELECT id FROM moz_places WHERE url=? LIMIT 1",
                (url,),
            ).fetchone()
            if existing:
                place_id = int(existing[0])
            else:
                place_id = _insert_dynamic(
                    connection,
                    "moz_places",
                    {
                        "url": url,
                        "title": title,
                        "rev_host": _reverse_host(url),
                        "visit_count": 0,
                        "hidden": 0,
                        "typed": 0,
                        "frecency": -1,
                        "guid": uuid.uuid4().hex[:12],
                        "foreign_count": 1,
                        "url_hash": 0,
                    },
                )
            now = int(time.time() * 1_000_000)
            added = int(record.get("date_added") or 0)
            if 0 < added < 10_000_000_000:
                added *= 1_000_000
            _insert_dynamic(
                connection,
                "moz_bookmarks",
                {
                    "type": 1,
                    "fk": place_id,
                    "parent": parent,
                    "position": position,
                    "title": title,
                    "dateAdded": added or now,
                    "lastModified": now,
                    "guid": uuid.uuid4().hex[:12],
                    "syncStatus": 0,
                    "syncChangeCounter": 1,
                },
            )
            position += 1
            count += 1
        _checkpoint(connection)
        return count


def _chromium_cookie_values(
    record: Mapping[str, Any],
    *,
    modern: bool,
    target_platform: str = "linux",
    master_key: Optional[bytes] = None,
) -> Dict[str, Any]:
    source = str(record.get("source_engine") or "")
    if source == "firefox" or "expiry" in record:
        expiry = firefox_ms_to_chromium_utc(record.get("expiry"))
        creation = firefox_ms_to_chromium_utc(record.get("creation_time"))
        accessed = firefox_ms_to_chromium_utc(record.get("last_accessed"))
        updated = firefox_ms_to_chromium_utc(record.get("update_time"))
    else:
        expiry = int(record.get("expires_utc") or 0)
        creation = int(record.get("creation_utc") or 0)
        accessed = int(record.get("last_access_utc") or 0)
        updated = int(record.get("last_update_utc") or accessed)
    host = str(record.get("host") or "")
    value = str(record.get("value") or "")
    if target_platform == "windows":
        if master_key is None:
            raise chromium_win.WindowsCryptoError(
                "chave do perfil Chromium Windows não está disponível"
            )
        encrypted = chromium_win.encrypt_chromium_value(
            value,
            master_key=master_key,
            host_key=host,
            modern_cookie=modern,
        )
    else:
        encrypted = encrypt_chromium_value(
            value,
            host_key=host,
            modern_cookie=modern,
        )
    return {
        "creation_utc": creation,
        "host_key": host,
        "top_frame_site_key": "",
        "name": str(record.get("name") or ""),
        "value": "",
        "encrypted_value": sqlite3.Binary(encrypted),
        "path": str(record.get("path") or "/"),
        "expires_utc": expiry,
        "is_secure": int(bool(record.get("secure"))),
        "is_httponly": int(bool(record.get("http_only"))),
        "last_access_utc": accessed,
        "has_expires": int(expiry > 0),
        "is_persistent": int(expiry > 0),
        "priority": 1,
        "samesite": int(record.get("same_site") or 0),
        "source_scheme": int(record.get("source_scheme") or 0),
        "source_port": -1,
        "last_update_utc": updated,
        "source_type": 0,
        "has_cross_site_ancestor": 0,
    }


def _ensure_chromium_cookies(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cookies (
            creation_utc INTEGER NOT NULL,
            host_key TEXT NOT NULL,
            top_frame_site_key TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            encrypted_value BLOB NOT NULL,
            path TEXT NOT NULL,
            expires_utc INTEGER NOT NULL,
            is_secure INTEGER NOT NULL,
            is_httponly INTEGER NOT NULL,
            last_access_utc INTEGER NOT NULL,
            has_expires INTEGER NOT NULL DEFAULT 1,
            is_persistent INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 1,
            samesite INTEGER NOT NULL DEFAULT 0,
            source_scheme INTEGER NOT NULL DEFAULT 0,
            source_port INTEGER NOT NULL DEFAULT -1,
            last_update_utc INTEGER NOT NULL DEFAULT 0,
            source_type INTEGER NOT NULL DEFAULT 0,
            has_cross_site_ancestor INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _chromium_modern_cookie_format(connection: sqlite3.Connection) -> bool:
    try:
        row = connection.execute(
            "SELECT value FROM meta WHERE key='version'"
        ).fetchone()
        return bool(row and int(row[0]) >= 24)
    except (sqlite3.Error, TypeError, ValueError):
        return False


def _apply_chromium_cookies(
    profile: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    target_platform: str = "linux",
) -> int:
    database = (
        profile / "Network" / "Cookies"
        if (profile / "Network").is_dir()
        else profile / "Cookies"
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    master_key: Optional[bytes] = None
    if target_platform == "windows":
        master_key = chromium_win.load_master_key(profile.parent / "Local State")
    with closing(sqlite3.connect(str(database))) as connection:
        _ensure_chromium_cookies(connection)
        modern = _chromium_modern_cookie_format(connection)
        count = 0
        for record in records:
            values = _chromium_cookie_values(
                record,
                modern=modern,
                target_platform=target_platform,
                master_key=master_key,
            )
            connection.execute(
                "DELETE FROM cookies WHERE host_key=? AND name=? AND path=?",
                (values["host_key"], values["name"], values["path"]),
            )
            _insert_dynamic(connection, "cookies", values)
            count += 1
        _checkpoint(connection)
        return count


def _chromium_bookmark_node(record: Mapping[str, Any]) -> Dict[str, Any]:
    added = int(record.get("date_added") or 0)
    if 0 < added < 10_000_000_000:
        added = (CHROMIUM_EPOCH_OFFSET_SECONDS + added) * 1_000_000
    return {
        "date_added": str(added),
        "date_last_used": "0",
        "guid": str(uuid.uuid4()),
        "id": str(int(time.time_ns() % 9_000_000_000_000_000)),
        "name": str(record.get("title") or record.get("url") or "Sem título"),
        "type": "url",
        "url": str(record.get("url") or ""),
    }


def _apply_chromium_bookmarks(
    profile: Path,
    records: Iterable[Mapping[str, Any]],
) -> int:
    path = profile / "Bookmarks"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {
            "checksum": "",
            "roots": {
                "bookmark_bar": {
                    "children": [],
                    "name": "Bookmarks bar",
                    "type": "folder",
                }
            },
            "version": 1,
        }
    root = payload.setdefault("roots", {}).setdefault(
        "bookmark_bar",
        {"children": [], "name": "Bookmarks bar", "type": "folder"},
    )
    children = root.setdefault("children", [])
    existing = {
        str(node.get("url"))
        for node in children
        if isinstance(node, dict) and node.get("url")
    }
    count = 0
    for record in records:
        url = str(record.get("url") or "")
        if not url or url in existing:
            continue
        children.append(_chromium_bookmark_node(record))
        existing.add(url)
        count += 1
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return count


def _copy_manual_logins(neutral: Path, profile: Path) -> bool:
    source = neutral / "logins.csv"
    if not source.is_file() or source.stat().st_size == 0:
        return False
    destination = profile / "distrohop-logins.csv"
    shutil.copy2(source, destination)
    os.chmod(destination, 0o600)
    return True


def _ignore_session_locks(_directory: str, names: List[str]) -> List[str]:
    return [name for name in names if name in SESSION_LOCK_NAMES]


def apply_neutral_profile(
    neutral: Path,
    target: Path,
    *,
    source_engine: str,
    target_engine: str,
    backup_name: Optional[str] = None,
    target_platform: str = "linux",
) -> Dict[str, Any]:
    if not neutral.is_dir():
        raise FileNotFoundError("dados neutros não encontrados: {}".format(neutral))
    if source_engine == target_engine:
        raise ValueError("restore neutro exige engines diferentes")
    if target_engine not in ("chromium", "firefox"):
        raise ValueError("engine de destino sem suporte: {}".format(target_engine))
    cookies = _read_cookies(neutral / "cookies.jsonl")
    bookmarks = _read_bookmarks(neutral / "bookmarks.html")
    target_parent = target.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    staging = target_parent / ".{}.partial-{}".format(target.name, uuid.uuid4().hex)
    backup = target_parent / (
        backup_name
        or "{}.distrohop-before-{}".format(
            target.name,
            time.strftime("%Y%m%d-%H%M%S"),
        )
    )
    if backup.exists():
        raise FileExistsError(str(backup))
    previous: Optional[Path] = None
    warnings: List[str] = []
    try:
        if target.is_dir():
            shutil.copytree(
                target,
                staging,
                symlinks=False,
                ignore=_ignore_session_locks,
                ignore_dangling_symlinks=True,
                copy_function=shutil.copy2,
            )
        elif target.exists():
            raise ValueError("perfil de destino não é um diretório: {}".format(target))
        else:
            staging.mkdir(mode=0o700)
        if target_engine == "firefox":
            cookie_count = _apply_firefox_cookies(staging, cookies)
            bookmark_count = _apply_firefox_bookmarks(staging, bookmarks)
        else:
            cookie_count = _apply_chromium_cookies(
                staging,
                cookies,
                target_platform=target_platform,
            )
            bookmark_count = _apply_chromium_bookmarks(staging, bookmarks)
        if _copy_manual_logins(neutral, staging):
            warnings.append(
                "Senhas cross-engine exigem importação manual de "
                "distrohop-logins.csv ou novo login."
            )
        if target.exists():
            os.replace(target, backup)
            previous = backup
        try:
            os.replace(staging, target)
        except Exception:
            if previous is not None and not target.exists():
                os.replace(previous, target)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return {
        "target": str(target),
        "previous_profile": str(previous) if previous else None,
        "cookies": cookie_count,
        "bookmarks": bookmark_count,
        "warnings": warnings,
        "source_engine": source_engine,
        "target_engine": target_engine,
    }
