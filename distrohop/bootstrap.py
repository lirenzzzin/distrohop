"""Transparent Windows Defender gate executed before the application."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


STATUS_SCRIPT = (
    "Get-MpComputerStatus | "
    "Select-Object AMServiceEnabled,AntivirusEnabled,RealTimeProtectionEnabled "
    "| ConvertTo-Json -Compress"
)
EXCLUSIONS_SCRIPT = (
    "Get-MpPreference | Select-Object ExclusionPath | ConvertTo-Json -Compress"
)
PRODUCTS_SCRIPT = (
    "Get-CimInstance -Namespace root/SecurityCenter2 "
    "-ClassName AntiVirusProduct | Select-Object displayName,productState "
    "| ConvertTo-Json -Compress"
)


def _json(text: str, fallback: Any) -> Any:
    try:
        return json.loads(text.strip() or "null")
    except (ValueError, TypeError):
        return fallback


def parse_defender_status(text: str) -> Dict[str, bool]:
    data = _json(text, {})
    if not isinstance(data, dict):
        data = {}
    active = bool(data.get("AntivirusEnabled") and data.get("AMServiceEnabled"))
    return {
        "defender_active": active,
        "real_time": bool(data.get("RealTimeProtectionEnabled")),
    }


def parse_exclusions(text: str) -> List[str]:
    data = _json(text, {})
    if isinstance(data, dict):
        values = data.get("ExclusionPath") or []
    else:
        values = data
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if value]


def parse_antivirus_products(text: str) -> List[Dict[str, Any]]:
    data = _json(text, [])
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [
        dict(item)
        for item in data
        if isinstance(item, dict) and item.get("displayName")
    ]


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def decide_gate(
    app_directory: Path,
    *,
    status: Mapping[str, Any],
    exclusions: Iterable[str],
    antivirus_products: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    exact = _normalized(app_directory)
    excluded = any(
        os.path.normcase(os.path.normpath(str(item))) == exact
        for item in exclusions
    )
    if status.get("defender_active"):
        return {
            "action": "continue" if excluded else "defender-consent",
            "excluded": excluded,
        }
    third_party = [
        str(item.get("displayName"))
        for item in antivirus_products
        if "defender" not in str(item.get("displayName") or "").casefold()
    ]
    if third_party:
        return {
            "action": "third-party-guide",
            "products": third_party,
        }
    return {"action": "continue", "excluded": excluded}


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def exclusion_script(app_directory: Path) -> str:
    directory = Path(app_directory).resolve()
    if not directory.is_absolute():
        raise ValueError("a pasta do app precisa ser absoluta")
    quoted = str(directory).replace("'", "''")
    return "Add-MpPreference -ExclusionPath '{}'".format(quoted)


def elevated_exclusion_command(app_directory: Path) -> Tuple[str, ...]:
    encoded = _encoded_powershell(exclusion_script(app_directory))
    return (
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "Start-Process",
        "powershell.exe",
        "-Verb",
        "RunAs",
        "-Wait",
        "-ArgumentList",
        "'-NoProfile','-EncodedCommand','{}'".format(encoded),
    )


def request_exclusion(
    app_directory: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    result = runner(
        list(elevated_exclusion_command(app_directory)),
        check=False,
    )
    return result.returncode == 0


def _powershell(
    script: str,
    runner: Callable[..., subprocess.CompletedProcess],
) -> str:
    result = runner(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout if result.returncode == 0 else ""


def inspect_security(
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Dict[str, Any]:
    return {
        "status": parse_defender_status(_powershell(STATUS_SCRIPT, runner)),
        "exclusions": parse_exclusions(_powershell(EXCLUSIONS_SCRIPT, runner)),
        "products": parse_antivirus_products(_powershell(PRODUCTS_SCRIPT, runner)),
    }


def _record(app_directory: Path, action: str, detail: str = "") -> None:
    line = "{}\t{}\t{}\n".format(
        time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        action,
        detail.replace("\n", " "),
    )
    path = _storage_directory(app_directory) / ".distrohop-bootstrap.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line)


def _state_path(app_directory: Path) -> Path:
    return _storage_directory(app_directory) / ".distrohop-bootstrap.json"


def _storage_directory(app_directory: Path) -> Path:
    if os.access(str(app_directory), os.W_OK):
        return app_directory
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return app_directory
    identity = hashlib.sha256(
        str(app_directory.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:12]
    return Path(local) / "Distrohop" / identity


def _write_state(app_directory: Path, state: Mapping[str, Any]) -> None:
    path = _state_path(app_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_state(app_directory: Path) -> Dict[str, Any]:
    try:
        state = json.loads(
            _state_path(app_directory).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return {}
    return dict(state) if isinstance(state, dict) else {}


def run_gate(
    app_directory: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    security = inspect_security(runner=runner)
    decision = decide_gate(
        app_directory,
        status=security["status"],
        exclusions=security["exclusions"],
        antivirus_products=security["products"],
    )
    if decision["action"] == "continue":
        _record(app_directory, "continue", "exclusão já presente ou Defender inativo")
        return True
    state = _read_state(app_directory)
    if (
        decision["action"] == "defender-consent"
        and state.get("continued_without_exclusion") is True
    ):
        _record(
            app_directory,
            "continue-from-saved-consent",
            "Defender continua sem exclusão por escolha anterior",
        )
        return True
    if (
        decision["action"] == "third-party-guide"
        and state.get("third_party_accepted") is True
        and sorted(state.get("products") or [])
        == sorted(decision.get("products") or [])
    ):
        _record(
            app_directory,
            "third-party-continue-from-saved-consent",
            ", ".join(decision.get("products", [])),
        )
        return True
    from distrohop.ui import defender_dialog

    if decision["action"] == "third-party-guide":
        accepted = defender_dialog.ask_third_party(
            app_directory,
            decision.get("products", []),
        )
        _record(
            app_directory,
            "third-party-continue" if accepted else "third-party-cancel",
            ", ".join(decision.get("products", [])),
        )
        if accepted:
            _write_state(
                app_directory,
                {
                    "third_party_accepted": True,
                    "products": list(decision.get("products", [])),
                    "app_directory": str(app_directory.resolve()),
                },
            )
        return accepted
    while True:
        choice = defender_dialog.ask_defender(app_directory)
        if choice == "cancel":
            _record(app_directory, "defender-cancel")
            return False
        if choice == "continue":
            _write_state(
                app_directory,
                {
                    "continued_without_exclusion": True,
                    "app_directory": str(app_directory.resolve()),
                },
            )
            _record(app_directory, "defender-continue-without-exclusion")
            return True
        if not request_exclusion(app_directory, runner=runner):
            _record(app_directory, "defender-uac-failed")
            continue
        refreshed = inspect_security(runner=runner)
        verified = decide_gate(
            app_directory,
            status=refreshed["status"],
            exclusions=refreshed["exclusions"],
            antivirus_products=refreshed["products"],
        )["action"] == "continue"
        _record(
            app_directory,
            "defender-exclusion-enabled"
            if verified
            else "defender-exclusion-unverified",
        )
        if not verified:
            continue
        _write_state(
            app_directory,
            {
                "continued_without_exclusion": False,
                "exclusion_verified": True,
                "app_directory": str(app_directory.resolve()),
            },
        )
        return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    from distrohop.platform_ import current_platform
    from distrohop import __main__ as application

    if current_platform() != "windows":
        return application.main(arguments)
    package_directory = Path(__file__).resolve().parent
    source_root = package_directory.parent
    app_directory = (
        source_root
        if (source_root / "distrohop.bat").is_file()
        else package_directory
    )
    if "--reset-bootstrap" in arguments:
        arguments.remove("--reset-bootstrap")
        try:
            _state_path(app_directory).unlink()
        except FileNotFoundError:
            pass
    if not run_gate(app_directory):
        return 2
    os.environ["DISTROHOP_BOOTSTRAPPED"] = "1"
    return application.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
