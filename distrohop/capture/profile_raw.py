"""Faithful raw profile copies and consistent read-only SQLite snapshots."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import Iterable, List
from urllib.parse import quote


TRANSIENT_NAMES = {
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
    ".parentlock",
    "lock",
}


def _is_transient(path: Path) -> bool:
    return path.name in TRANSIENT_NAMES or path.name.endswith(("-wal", "-shm", "-journal"))


def _iter_files(source: Path, ancestors: frozenset) -> Iterable[Path]:
    if _is_transient(source):
        return
    try:
        status = source.stat()
    except OSError:
        return
    identity = (status.st_dev, status.st_ino)
    if stat.S_ISDIR(status.st_mode):
        if identity in ancestors:
            return
        lineage = ancestors.union((identity,))
        try:
            children = sorted(source.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        for child in children:
            yield from _iter_files(child, lineage)
    elif stat.S_ISREG(status.st_mode):
        yield source


def iter_files(source: Path) -> Iterable[Path]:
    yield from _iter_files(source, frozenset())


def list_files(source: Path) -> List[str]:
    return [str(path) for path in iter_files(source)]


def copy_path(source: Path, destination: Path) -> List[str]:
    """Copy a self-contained tree, dereferencing symlinks and stopping cycles."""
    warnings: List[str] = []

    def copy_item(item: Path, target: Path, ancestors: frozenset) -> None:
        if _is_transient(item):
            return
        try:
            status = item.stat()
        except OSError as error:
            warnings.append("{}: {}".format(item, error))
            return
        identity = (status.st_dev, status.st_ino)
        if stat.S_ISDIR(status.st_mode):
            if identity in ancestors:
                warnings.append("{}: ciclo de symlink ignorado".format(item))
                return
            try:
                target.mkdir(parents=True, exist_ok=True)
                children = sorted(item.iterdir(), key=lambda path: path.name)
            except OSError as error:
                warnings.append("{}: {}".format(item, error))
                return
            lineage = ancestors.union((identity,))
            for child in children:
                copy_item(child, target / child.name, lineage)
            try:
                shutil.copystat(item, target, follow_symlinks=True)
            except OSError:
                pass
        elif stat.S_ISREG(status.st_mode):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target, follow_symlinks=True)
            except OSError as error:
                warnings.append("{}: {}".format(item, error))

    destination.parent.mkdir(parents=True, exist_ok=True)
    copy_item(source, destination, frozenset())
    return warnings


def sqlite_snapshot(source: Path, destination: Path) -> None:
    """Use SQLite's backup API so WAL content is included consistently."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = "file:{}?mode=ro".format(quote(str(source.resolve()), safe="/"))
    source_connection = sqlite3.connect(uri, uri=True, timeout=5)
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.execute("PRAGMA busy_timeout=5000")
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def capture_raw_profile(source: Path, destination: Path) -> List[str]:
    warnings = copy_path(source, destination)
    sqlite_names = {
        "Cookies",
        "Login Data",
        "Web Data",
        "History",
        "cookies.sqlite",
        "places.sqlite",
        "permissions.sqlite",
        "formhistory.sqlite",
    }
    for original in iter_files(source):
        if original.name not in sqlite_names:
            continue
        relative = original.relative_to(source)
        copied = destination / relative
        try:
            copied.unlink(missing_ok=True)
            sqlite_snapshot(original, copied)
            (copied.parent / (copied.name + "-wal")).unlink(missing_ok=True)
            (copied.parent / (copied.name + "-shm")).unlink(missing_ok=True)
        except (OSError, sqlite3.Error) as error:
            warnings.append("{}: snapshot SQLite falhou: {}".format(original, error))
            try:
                copied.unlink(missing_ok=True)
                shutil.copy2(original, copied, follow_symlinks=True)
            except OSError as copy_error:
                warnings.append("{}: {}".format(original, copy_error))
    return warnings
