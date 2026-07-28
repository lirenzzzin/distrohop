"""Select GUI or CLI without importing Tk before the frontend decision."""

from __future__ import annotations

import sys
from typing import Any, Optional, Sequence

from distrohop.ui import cli, tk_available


def _load_gui() -> Any:
    from distrohop.ui import gui

    return gui


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    forced_gui = "--gui" in arguments
    if forced_gui:
        arguments.remove("--gui")
    if arguments and not forced_gui:
        return cli.main(arguments)
    available, reason = tk_available.probe_tk()
    if available:
        _load_gui().launch()
        return 0
    hint = tk_available.install_hint()
    if forced_gui:
        print(
            "Erro: GUI indisponível ({}). Para habilitar: {}.".format(
                reason,
                hint,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        "Aviso: Tk não encontrado, usando modo texto. Para habilitar a GUI: {}.".format(
            hint
        ),
        file=sys.stderr,
    )
    return cli.main([])


if __name__ == "__main__":
    raise SystemExit(main())
