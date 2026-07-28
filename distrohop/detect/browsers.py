"""Browser installation and profile discovery driven by static metadata."""

from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "browsers.json"

WINDOWS_LOCATIONS = {
    "brave": ("chromium", "local", "BraveSoftware/Brave-Browser/User Data"),
    "chrome": ("chromium", "local", "Google/Chrome/User Data"),
    "edge": ("chromium", "local", "Microsoft/Edge/User Data"),
    "firefox": ("firefox", "roaming", "Mozilla/Firefox"),
    "zen": ("firefox", "roaming", "zen"),
}


def load_definitions(path: Path = DATA_PATH) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("data/browsers.json inválido: {}".format(error)) from error
    definitions = payload.get("linux")
    if not isinstance(definitions, list):
        raise RuntimeError("data/browsers.json não contém a lista linux")
    return definitions


def _chromium_profiles(root: Path) -> List[Dict[str, str]]:
    names: Dict[str, str] = {}
    state = root / "Local State"
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
        cache = payload.get("profile", {}).get("info_cache", {})
        if isinstance(cache, dict):
            for directory, details in cache.items():
                if isinstance(details, dict):
                    names[directory] = str(details.get("name") or directory)
    except (OSError, ValueError, TypeError):
        pass
    directories = []
    if (root / "Default").is_dir():
        directories.append(root / "Default")
    directories.extend(path for path in sorted(root.glob("Profile *")) if path.is_dir())
    return [{"name": names.get(path.name, path.name), "path": str(path)} for path in directories]


def _firefox_profiles(root: Path) -> List[Dict[str, str]]:
    ini = root / "profiles.ini"
    profiles: List[Dict[str, str]] = []
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(ini, encoding="utf-8")
            for section in parser.sections():
                if not section.casefold().startswith("profile"):
                    continue
                raw = parser.get(section, "Path", fallback="")
                if not raw:
                    continue
                path = Path(raw)
                if parser.getboolean(section, "IsRelative", fallback=True):
                    path = root / path
                profiles.append({
                    "name": parser.get(section, "Name", fallback=path.name),
                    "path": str(path),
                    "default": parser.getboolean(section, "Default", fallback=False),
                })
        except (OSError, configparser.Error, ValueError):
            pass
    if not profiles:
        patterns = ("*.default*", "*.Default*")
        matches = {path for pattern in patterns for path in root.glob(pattern) if path.is_dir()}
        profiles = [{"name": path.name, "path": str(path), "default": False} for path in sorted(matches)]
    return profiles


def _version(
    binary: Optional[str],
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[str]:
    if not binary:
        return None
    try:
        result = runner(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (result.stdout or result.stderr or "").strip().splitlines()
    return line[0] if line else None


def _record(
    definition: Mapping[str, Any],
    packaging: str,
    root: Path,
    binary: Optional[str] = None,
    version: Optional[str] = None,
) -> Dict[str, object]:
    engine = str(definition["engine"])
    profiles = _chromium_profiles(root) if engine == "chromium" else _firefox_profiles(root)
    return {
        "id": definition["id"],
        "name": definition["name"],
        "engine": engine,
        "packaging": packaging,
        "path": str(root),
        "binary": binary,
        "version": version,
        "installed": binary is not None if packaging == "native" else None,
        "profiles": profiles,
    }


def detect_linux(
    home: Path,
    environ: Optional[Mapping[str, str]] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    definitions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, object]]:
    env = os.environ if environ is None else environ
    config = Path(env.get("XDG_CONFIG_HOME", str(home / ".config")))
    chrome_config = Path(env.get("CHROME_CONFIG_HOME", str(config)))
    variables = {
        "home": str(home),
        "config": str(config),
        "chrome_config": str(chrome_config),
    }
    results: List[Dict[str, object]] = []
    for definition in definitions or load_definitions():
        binary = None
        for item in definition.get("binaries", []):
            candidate = which(item)
            if candidate:
                binary = candidate
                break
        version = _version(binary)
        native_found = False
        for packaging in ("native", "flatpak", "snap"):
            for template in definition.get(packaging, []):
                root = Path(template.format(**variables))
                if root.is_dir():
                    record = _record(
                        definition,
                        packaging,
                        root,
                        binary if packaging == "native" else None,
                        version,
                    )
                    if record["profiles"]:
                        results.append(record)
                        native_found = native_found or packaging == "native"
        if binary and not native_found:
            templates = definition.get("native") or []
            roots = [Path(template.format(**variables)) for template in templates]
            root = next((path for path in roots if path.is_dir()), roots[0] if roots else home)
            results.append(_record(definition, "native", root, binary, version))
    custom = env.get("CHROME_USER_DATA_DIR")
    if custom:
        custom_root = Path(custom)
        known_roots = {item["path"] for item in results}
        if custom_root.is_dir() and str(custom_root) not in known_roots:
            definition = {
                "id": "chromium-custom",
                "name": "Chromium/Chrome personalizado",
                "engine": "chromium",
            }
            results.append(_record(definition, "custom", custom_root))
    return results


def detect_windows(environ: Mapping[str, str]) -> List[Dict[str, object]]:
    bases = {
        "local": Path(environ.get("LOCALAPPDATA", "")),
        "roaming": Path(environ.get("APPDATA", "")),
    }
    results = []
    for name, (engine, base_name, relative) in WINDOWS_LOCATIONS.items():
        root = bases[base_name] / Path(relative.replace("/", os.sep))
        if root.is_dir():
            definition = {"id": name, "name": name.title(), "engine": engine}
            results.append(_record(definition, "native", root))
    return results
