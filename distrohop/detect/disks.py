"""Candidate destination discovery."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


def _walk(
    devices: Iterable[Dict[str, Any]],
    parent: Optional[str] = None,
    root: Optional[str] = None,
):
    for device in devices:
        item = dict(device)
        item["_parent"] = parent
        item["_root"] = root or device.get("name")
        yield item
        yield from _walk(
            device.get("children") or [],
            device.get("name") or parent,
            str(item["_root"]) if item["_root"] else None,
        )


def _mountpoints(device: Dict[str, Any]) -> List[str]:
    values = device.get("mountpoints")
    if values is None:
        values = [device.get("mountpoint")]
    if isinstance(values, str):
        values = [values]
    return [value for value in (values or []) if value]


def _boolean(value: object) -> bool:
    if isinstance(value, str):
        return value.casefold() not in {"", "0", "false", "no"}
    return bool(value)


def parse_lsblk(text: str) -> List[Dict[str, object]]:
    payload = json.loads(text)
    flat = list(_walk(payload.get("blockdevices", [])))
    system_roots = {
        item.get("_root")
        for item in flat
        if "/" in _mountpoints(item) and item.get("_root")
    }
    by_device: Dict[object, Dict[str, object]] = {}
    for item in flat:
        mounts = _mountpoints(item)
        system = item.get("_root") in system_roots
        # Keep the physical system disk visible even though it is not mounted.
        if not mounts and not (system and item.get("type") == "disk"):
            continue
        filesystem = item.get("fstype")
        read_only = _boolean(item.get("ro"))
        destination_mount = any(mount.startswith("/") and mount not in {"/", "/boot", "/boot/efi"} for mount in mounts)
        record = {
            "name": item.get("name"),
            "path": item.get("path") or (f"/dev/{item['name']}" if item.get("name") else None),
            "label": item.get("label"),
            "size": item.get("size"),
            "filesystem": filesystem,
            "mountpoints": mounts,
            "removable": _boolean(item.get("rm")),
            "read_only": read_only,
            "system": system,
            "candidate": destination_mount and not system and filesystem != "swap" and not read_only,
        }
        key = record["name"] or record["path"]
        existing = by_device.get(key)
        if existing is None:
            by_device[key] = record
            continue
        existing["mountpoints"] = list(dict.fromkeys(existing["mountpoints"] + record["mountpoints"]))
        existing["system"] = bool(existing["system"] or record["system"])
        existing["read_only"] = bool(existing["read_only"] or record["read_only"])
        existing["removable"] = bool(existing["removable"] or record["removable"])
        existing["candidate"] = bool(existing["candidate"] and record["candidate"])
    return list(by_device.values())


def detect_linux(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    writable: Callable[[str], bool] = lambda path: os.access(path, os.W_OK),
) -> List[Dict[str, object]]:
    columns = (
        "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,RM,RO",
        "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINT,RM,RO",
    )
    for selected in columns:
        try:
            result = runner(
                ["lsblk", "-J", "-o", selected],
                check=True,
                capture_output=True,
                text=True,
            )
            disks = parse_lsblk(result.stdout)
            for disk in disks:
                mounts = disk["mountpoints"]
                disk["writable"] = any(writable(mount) for mount in mounts if mount.startswith("/"))
                disk["candidate"] = bool(disk["candidate"] and disk["writable"])
            return disks
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return []
