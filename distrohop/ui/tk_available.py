"""Tk runtime probing and distro-specific installation guidance."""

from __future__ import annotations

import importlib
import platform
from typing import Any, Callable, Tuple


def probe_tk(
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> Tuple[bool, str]:
    try:
        tk = importer("tkinter")
    except (ImportError, ModuleNotFoundError) as error:
        return False, "tkinter não está instalado: {}".format(error)
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
    except Exception as error:
        return False, "Tk não conseguiu abrir um display: {}".format(error)
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    return True, ""


def install_hint(system: str = "") -> str:
    name = system or platform.system()
    if name.casefold() == "windows":
        return (
            "reinstale o Python pelo instalador oficial de python.org "
            "com o componente Tcl/Tk habilitado"
        )
    try:
        from distrohop.detect.distro import detect

        info = detect()
        package = str(info.get("tk_package") or "")
        manager = str(info.get("manager") or "")
    except Exception:
        package = ""
        manager = ""
    commands = {
        "pacman": "sudo pacman -S tk",
        "apt": "sudo apt install python3-tk",
        "dnf": "sudo dnf install python3-tkinter",
        "zypper": "sudo zypper install {}".format(package or "python3-tk"),
        "apk": "sudo apk add python3-tkinter",
        "xbps": "sudo xbps-install python3-tkinter",
        "eopkg": "sudo eopkg install python3-tkinter",
        "urpmi": "sudo urpmi tkinter3",
        "apt-rpm": "sudo apt-get install tkinter",
    }
    return commands.get(
        manager,
        "instale o pacote Tcl/Tk ou tkinter da sua distribuição",
    )
