"""Windows browser installation through exact WinGet IDs or Chocolatey."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "packages.json"


def _definitions(path: Path = DATA_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def plan_install(
    browser_id: str,
    os_info: Mapping[str, Any],
    *,
    packages: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, ...]:
    definition = (packages or _definitions()).get(browser_id)
    if not isinstance(definition, dict):
        raise RuntimeError(
            "não há receita Windows para {}".format(browser_id)
        )
    manager = str(os_info.get("manager") or "")
    if manager == "winget" and definition.get("winget"):
        return (
            "winget",
            "install",
            "--id",
            str(definition["winget"]),
            "--exact",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
        )
    if manager == "choco" and definition.get("choco"):
        return (
            "choco",
            "install",
            str(definition["choco"]),
            "-y",
        )
    raise RuntimeError(
        "WinGet/Chocolatey ou ID verificado não disponível para {}".format(
            browser_id
        )
    )


def run_install(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    result = runner(list(command), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "instalação Windows falhou com status {}".format(
                result.returncode
            )
        )
