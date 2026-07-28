"""Explicit, non-elevated Tk consent dialogs for antivirus preparation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from distrohop.ui.i18n import normalize_language, system_language, translate
from distrohop.ui.preferences import load_preferences


def _dialog_language() -> str:
    preferences = load_preferences()
    return normalize_language(
        os.environ.get("DISTROHOP_LANGUAGE")
        or preferences.get("language")
        or system_language()
    )


def ask_defender(app_directory: Path) -> str:
    import tkinter as tk
    from tkinter import ttk

    language = _dialog_language()
    tr = lambda text: translate(text, language)
    result = {"value": "cancel"}
    root = tk.Tk()
    root.title(tr("Distrohop · Windows Defender"))
    root.geometry("620x420")
    root.resizable(False, False)
    root.configure(background="#F5F7FA")
    frame = ttk.Frame(root, padding=30)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=tr("Proteção transparente antes de começar"),
        font=("Segoe UI", 18, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text=tr(
            "Este app lê cookies e senhas do navegador para migrar seus logins. "
            "Isso se parece com o comportamento de um infostealer e o Defender "
            "pode bloquear a operação.\n\n"
            "Habilitar libera somente esta pasta:\n{}\n\n"
            "O Windows mostrará um pedido UAC. O app inteiro nunca roda como "
            "administrador e o antivírus nunca é desativado."
        ).format(app_directory),
        wraplength=555,
        justify="left",
    ).pack(anchor="w", pady=(18, 26))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", side="bottom")

    def choose(value: str) -> None:
        result["value"] = value
        root.destroy()

    ttk.Button(
        buttons,
        text=tr("Continuar sem exclusão"),
        command=lambda: choose("continue"),
    ).pack(side="left")
    ttk.Button(
        buttons,
        text=tr("Habilitar exclusão desta pasta"),
        command=lambda: choose("enable"),
    ).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
    root.mainloop()
    return result["value"]


def ask_third_party(
    app_directory: Path,
    products: Iterable[str],
) -> bool:
    import tkinter as tk
    from tkinter import messagebox

    language = _dialog_language()
    tr = lambda text: translate(text, language)
    root = tk.Tk()
    root.withdraw()
    accepted = messagebox.askokcancel(
        tr("Distrohop · antivírus"),
        tr(
            "Antivírus detectado: {}\n\n"
            "Adicione esta pasta às exclusões pelo painel do seu antivírus:\n"
            "{}\n\n"
            "Clique em OK depois de concluir, ou Cancelar para sair."
        ).format(", ".join(products), app_directory),
        parent=root,
    )
    root.destroy()
    return bool(accepted)
