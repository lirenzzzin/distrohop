"""Tkinter frontend: animated, dependency-free wizards over the shared engine."""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from distrohop.core.engine import (
    BackupPlan,
    RestorePlan,
    default_selection,
    list_inventory,
    plan_backup,
    plan_restore,
    run_backup,
    run_restore,
)
from distrohop.core.events import Event
from distrohop.core.selection import Selection
from distrohop.detect.browsers import load_definitions
from distrohop.vault.bundle import read_manifest, verify_bundle


LIGHT = {
    "bg": "#F4F6F8",
    "panel": "#FFFFFF",
    "sidebar": "#E9EDF1",
    "pill": "#CBD2D9",
    "text": "#17212B",
    "muted": "#667482",
    "border": "#DDE2E7",
    "accent": "#2962FF",
    "accent_hover": "#1E4DCC",
    "success": "#16865C",
    "warning": "#A85D00",
    "danger": "#C43D4B",
    "input": "#F8FAFB",
}

DARK = {
    "bg": "#11151A",
    "panel": "#1B2128",
    "sidebar": "#171C22",
    "pill": "#3B444D",
    "text": "#F1F5F8",
    "muted": "#A2ADB8",
    "border": "#2C343D",
    "accent": "#6D8DFF",
    "accent_hover": "#89A2FF",
    "success": "#54C999",
    "warning": "#F0A84A",
    "danger": "#FF7A86",
    "input": "#222A32",
}

BACKUP_STEPS = (
    ("◉", "Detectar"),
    ("✓", "Selecionar"),
    ("⌁", "Destino"),
    ("◆", "Proteger"),
    ("⇣", "Copiar"),
    ("✓", "Verificar"),
    ("★", "Concluir"),
)

RESTORE_STEPS = (
    ("▣", "Ler bundle"),
    ("✓", "Validar"),
    ("◉", "Selecionar"),
    ("⌁", "Preparar"),
    ("⇡", "Aplicar"),
    ("✓", "Verificar"),
    ("★", "Concluir"),
)


def _ease(value: float) -> float:
    return 4 * value * value * value if value < 0.5 else 1 - pow(-2 * value + 2, 3) / 2


def _rounded(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **options: Any,
) -> int:
    points = (
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)


class RoundedPanel(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        palette: Mapping[str, str],
        *,
        height: int = 240,
    ) -> None:
        super().__init__(
            parent,
            height=height,
            background=palette["bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        self.palette = palette
        self.shape = _rounded(
            self,
            1,
            1,
            10,
            10,
            18,
            fill=palette["panel"],
            outline=palette["border"],
        )
        self.body = ttk.Frame(self, style="Panel.TFrame")
        self.window = self.create_window(28, 24, anchor="nw", window=self.body)
        self.bind("<Configure>", self._resize)

    def _resize(self, event: tk.Event) -> None:
        width = max(20, int(event.width))
        height = max(20, int(event.height))
        self.coords(
            self.shape,
            19, 1, width - 19, 1, width - 1, 1, width - 1, 19,
            width - 1, height - 19, width - 1, height - 1,
            width - 19, height - 1, 19, height - 1, 1, height - 1,
            1, height - 19, 1, 19, 1, 1,
        )
        self.itemconfigure(self.window, width=max(1, width - 56))

    def recolor(self, palette: Mapping[str, str]) -> None:
        self.palette = palette
        self.configure(background=palette["bg"])
        self.itemconfigure(
            self.shape,
            fill=palette["panel"],
            outline=palette["border"],
        )


class DistrohopApp:
    expanded_width = 232
    collapsed_width = 72

    def __init__(
        self,
        root: tk.Tk,
        *,
        inventory_loader: Callable[..., Dict[str, Any]] = list_inventory,
    ) -> None:
        self.root = root
        self.inventory_loader = inventory_loader
        self.palette: Mapping[str, str] = (
            DARK if os.environ.get("DISTROHOP_THEME", "").casefold() == "dark" else LIGHT
        )
        self.theme_name = "dark" if self.palette is DARK else "light"
        self.reduced_motion = tk.BooleanVar(value=False)
        self.inventory: Optional[Dict[str, Any]] = None
        self.manifest: Optional[Dict[str, Any]] = None
        self.bundle_path: Optional[Path] = None
        self.active_view = "home"
        self.current_page: Optional[ttk.Frame] = None
        self.panels: List[RoundedPanel] = []
        self.jobs: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.handlers: Dict[str, Tuple[Callable[[Any], None], Callable[[str], None]]] = {}
        self.active_step = 0
        self.active_y = 92.0
        self.step_animation_generation = 0
        self.steps: Sequence[Tuple[str, str]] = BACKUP_STEPS
        self.sidebar_width = float(self.expanded_width)
        self.sidebar_after: Optional[str] = None
        self.page_after: Optional[str] = None
        self.busy = False
        self._backup_selection: Optional[Selection] = None
        self._backup_targets: Tuple[Path, ...] = ()
        self._backup_password: Optional[str] = None

        self.root.title("Distrohop")
        self.root.geometry("1120x720")
        self.root.minsize(960, 640)
        self.root.configure(background=self.palette["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._configure_styles()

        self.shell = tk.Frame(self.root, background=self.palette["bg"])
        self.shell.pack(fill="both", expand=True)
        self.sidebar = tk.Canvas(
            self.shell,
            width=self.expanded_width,
            background=self.palette["sidebar"],
            highlightthickness=0,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.bind("<Enter>", lambda _event: self._animate_sidebar(True))
        self.sidebar.bind("<Leave>", lambda _event: self._schedule_collapse())
        self.sidebar.bind("<Button-1>", self._sidebar_click)
        self.content = ttk.Frame(self.shell, style="Base.TFrame")
        self.content.pack(side="left", fill="both", expand=True)
        self.header = ttk.Frame(self.content, style="Base.TFrame")
        self.header.pack(fill="x", padx=36, pady=(24, 8))
        self.title_var = tk.StringVar(value="Distrohop")
        ttk.Label(
            self.header,
            textvariable=self.title_var,
            style="Header.TLabel",
        ).pack(side="left")
        self.theme_button = ttk.Button(
            self.header,
            text="☾  Tema",
            style="Ghost.TButton",
            command=self.toggle_theme,
        )
        self.theme_button.pack(side="right")
        self.page_host = ttk.Frame(self.content, style="Base.TFrame")
        self.page_host.pack(fill="both", expand=True, padx=36, pady=(8, 24))
        self.status_var = tk.StringVar(value="Preparando detecção…")
        ttk.Label(
            self.content,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).place(relx=1.0, rely=1.0, x=-36, y=-10, anchor="se")

        self._draw_sidebar()
        self.show_home()
        self._poll_jobs()
        self._start_inventory()
        self._schedule_collapse()

    def _configure_styles(self) -> None:
        colors = self.palette
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        family = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
        self.root.option_add("*Font", (family, 10))
        style.configure("Base.TFrame", background=colors["bg"])
        style.configure("Panel.TFrame", background=colors["panel"])
        style.configure(
            "TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(family, 10),
        )
        style.configure(
            "Panel.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
        )
        style.configure(
            "Muted.Panel.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
        )
        style.configure(
            "Hero.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(family, 25, "bold"),
        )
        style.configure(
            "Header.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=(family, 13, "bold"),
        )
        style.configure(
            "Section.Panel.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
            font=(family, 14, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=(family, 9),
        )
        style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(18, 10),
            font=(family, 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", colors["accent_hover"]), ("disabled", colors["border"])],
        )
        style.configure(
            "Secondary.TButton",
            background=colors["panel"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=(14, 9),
        )
        style.map("Secondary.TButton", background=[("active", colors["input"])])
        style.configure(
            "Ghost.TButton",
            background=colors["bg"],
            foreground=colors["muted"],
            borderwidth=0,
            padding=(10, 7),
        )
        style.map("Ghost.TButton", foreground=[("active", colors["text"])])
        style.configure(
            "TCheckbutton",
            background=colors["panel"],
            foreground=colors["text"],
        )
        style.map("TCheckbutton", background=[("active", colors["panel"])])
        style.configure(
            "TEntry",
            fieldbackground=colors["input"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            insertcolor=colors["text"],
            padding=8,
        )
        style.configure(
            "TCombobox",
            fieldbackground=colors["input"],
            background=colors["panel"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
            padding=7,
        )
        style.configure(
            "TProgressbar",
            background=colors["accent"],
            troughcolor=colors["border"],
            borderwidth=0,
        )

    def _draw_sidebar(self) -> None:
        canvas = self.sidebar
        canvas.delete("all")
        colors = self.palette
        width = self.sidebar_width
        expanded = width > 145
        canvas.configure(background=colors["sidebar"], width=int(width))
        _rounded(canvas, 20, 18, 52, 50, 9, fill=colors["accent"], outline="")
        canvas.create_text(
            36,
            34,
            text="D",
            fill="#FFFFFF",
            font=("DejaVu Sans", 13, "bold"),
        )
        if expanded:
            canvas.create_text(
                64,
                34,
                text="DISTROHOP",
                anchor="w",
                fill=colors["text"],
                font=("DejaVu Sans", 11, "bold"),
            )
        pill_y = self.active_y
        _rounded(
            canvas,
            12,
            pill_y - 21,
            width - 12,
            pill_y + 21,
            13,
            fill=colors["pill"],
            outline="",
            tags=("active-pill",),
        )
        for index, (icon, label) in enumerate(self.steps):
            y = 92 + index * 58
            done = index < self.active_step
            current = index == self.active_step
            color = colors["success"] if done else colors["text"] if current else colors["muted"]
            if index:
                canvas.create_line(
                    36,
                    y - 37,
                    36,
                    y - 21,
                    fill=colors["success"] if done else colors["border"],
                    width=2,
                )
            canvas.create_oval(
                25,
                y - 11,
                47,
                y + 11,
                fill=colors["success"] if done else colors["panel"],
                outline=colors["success"] if done else colors["border"],
                width=2,
            )
            canvas.create_text(
                36,
                y,
                text="✓" if done else icon,
                fill="#FFFFFF" if done else color,
                font=("DejaVu Sans", 9, "bold"),
            )
            if expanded:
                canvas.create_text(
                    62,
                    y,
                    text=label,
                    anchor="w",
                    fill=color,
                    font=("DejaVu Sans", 10, "bold" if current else "normal"),
                )
        canvas.create_text(
            36,
            514,
            text="☰",
            fill=colors["muted"],
            font=("DejaVu Sans", 14),
            tags=("sidebar-toggle",),
        )
        if expanded:
            canvas.create_text(
                62,
                514,
                text="Recolher menu",
                anchor="w",
                fill=colors["muted"],
                font=("DejaVu Sans", 9),
                tags=("sidebar-toggle",),
            )

    def _sidebar_click(self, event: tk.Event) -> None:
        if 486 <= event.y <= 542:
            self._animate_sidebar(self.sidebar_width < 145)

    def _schedule_collapse(self) -> None:
        if self.sidebar_after:
            self.root.after_cancel(self.sidebar_after)
        if not self.busy:
            self.sidebar_after = self.root.after(
                3800,
                lambda: self._animate_sidebar(False),
            )

    def _animate_sidebar(self, expand: bool) -> None:
        if self.sidebar_after:
            self.root.after_cancel(self.sidebar_after)
            self.sidebar_after = None
        start = self.sidebar_width
        target = float(self.expanded_width if expand else self.collapsed_width)
        if start == target:
            return
        duration = 1 if self.reduced_motion.get() else 220
        started = time.monotonic()

        def frame() -> None:
            elapsed = (time.monotonic() - started) * 1000
            position = min(1.0, elapsed / duration)
            self.sidebar_width = start + (target - start) * _ease(position)
            self._draw_sidebar()
            if position < 1 and self.root.winfo_exists():
                self.root.after(16, frame)

        frame()

    def set_steps(
        self,
        steps: Sequence[Tuple[str, str]],
        index: int = 0,
    ) -> None:
        self.steps = steps
        self.step_animation_generation += 1
        self.active_step = max(0, min(index, len(steps) - 1))
        self.active_y = 92 + self.active_step * 58
        self._draw_sidebar()

    def go_step(self, index: int) -> None:
        index = max(0, min(index, len(self.steps) - 1))
        self.step_animation_generation += 1
        generation = self.step_animation_generation
        start = self.active_y
        target = 92 + index * 58
        self.active_step = index
        duration = 1 if self.reduced_motion.get() else 250
        started = time.monotonic()

        def frame() -> None:
            if generation != self.step_animation_generation:
                return
            position = min(1.0, (time.monotonic() - started) * 1000 / duration)
            self.active_y = start + (target - start) * _ease(position)
            self._draw_sidebar()
            if position < 1 and self.root.winfo_exists():
                self.root.after(16, frame)

        frame()

    def toggle_theme(self) -> None:
        self.palette = DARK if self.theme_name == "light" else LIGHT
        self.theme_name = "dark" if self.palette is DARK else "light"
        self.root.configure(background=self.palette["bg"])
        self.shell.configure(background=self.palette["bg"])
        self._configure_styles()
        for panel in self.panels:
            panel.recolor(self.palette)
        self._draw_sidebar()
        self.theme_button.configure(text="☀  Tema" if self.theme_name == "dark" else "☾  Tema")

    def _panel(self, parent: tk.Misc, height: int = 250) -> RoundedPanel:
        panel = RoundedPanel(parent, self.palette, height=height)
        self.panels.append(panel)
        return panel

    def _show(
        self,
        view: str,
        title: str,
        builder: Callable[[ttk.Frame], None],
    ) -> None:
        self.active_view = view
        self.title_var.set(title)
        new_page = ttk.Frame(self.page_host, style="Base.TFrame")
        builder(new_page)
        old_page = self.current_page
        self.current_page = new_page
        self.panels = [
            panel for panel in self.panels if panel.winfo_exists()
        ]
        distance = 34
        new_page.place(x=distance, y=0, relwidth=1.0, relheight=1.0, width=-distance)
        if old_page is None or self.reduced_motion.get():
            if old_page is not None:
                old_page.destroy()
            new_page.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            return
        started = time.monotonic()

        def frame() -> None:
            position = min(1.0, (time.monotonic() - started) * 1000 / 190)
            eased = _ease(position)
            new_page.place(
                x=int(distance * (1 - eased)),
                y=0,
                relwidth=1.0,
                relheight=1.0,
                width=-int(distance * (1 - eased)),
            )
            if old_page.winfo_exists():
                old_page.place(
                    x=-int(distance * eased),
                    y=0,
                    relwidth=1.0,
                    relheight=1.0,
                    width=int(distance * eased),
                )
            if position < 1 and self.root.winfo_exists():
                self.page_after = self.root.after(16, frame)
            else:
                if old_page.winfo_exists():
                    old_page.destroy()
                new_page.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        frame()

    def _start_inventory(self) -> None:
        self.status_var.set("Detectando sistema, navegadores e destinos…")
        self._worker(
            "inventory",
            lambda callback: self.inventory_loader(callback=callback),
            self._inventory_ready,
        )

    def _inventory_ready(self, result: Any) -> None:
        self.inventory = dict(result)
        distro = self.inventory.get("os", {})
        self.status_var.set(
            "{} · {} · pronto".format(
                distro.get("name") or "Sistema detectado",
                distro.get("manager") or distro.get("strategy") or "fallback",
            )
        )
        if self.active_view == "home":
            self.show_home()

    def _engine_event(self, event: Event) -> None:
        self.status_var.set(event.message)
        if event.kind == "warn":
            self._append_log("⚠ " + event.message)
        elif event.kind in ("started", "step", "done"):
            self._append_log("• " + event.message)
        lowered = event.message.casefold()
        if self.active_view == "progress":
            if "verific" in lowered:
                self.go_step(5)
            elif event.kind == "done":
                self.go_step(6)

    def _worker(
        self,
        token: str,
        operation: Callable[[Callable[[Event], None]], Any],
        on_result: Callable[[Any], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        if token in self.handlers:
            messagebox.showwarning("Distrohop", "Esta operação já está em andamento.")
            return
        self.busy = True
        self.handlers[token] = (
            on_result,
            on_error or self._show_error,
        )

        def run() -> None:
            try:
                result = operation(
                    lambda event: self.jobs.put(("event", event))
                )
            except Exception as error:
                detail = "{}\n\n{}".format(error, traceback.format_exc(limit=8))
                self.jobs.put(("error", (token, detail)))
            else:
                self.jobs.put(("result", (token, result)))

        threading.Thread(target=run, name="distrohop-{}".format(token), daemon=True).start()

    def _poll_jobs(self) -> None:
        try:
            while True:
                kind, payload = self.jobs.get_nowait()
                if kind == "event":
                    self._engine_event(payload)
                    continue
                token, value = payload
                handlers = self.handlers.pop(token, None)
                self.busy = bool(self.handlers)
                if handlers:
                    (handlers[0] if kind == "result" else handlers[1])(value)
                self._schedule_collapse()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(60, self._poll_jobs)

    def _show_error(self, detail: str) -> None:
        self.status_var.set("Operação interrompida com segurança")
        messagebox.showerror("Distrohop", detail.split("\n\n", 1)[0])
        self._append_log("✕ " + detail.splitlines()[0])

    def _append_log(self, line: str) -> None:
        widget = getattr(self, "progress_log", None)
        if widget is None or not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.insert("end", line + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _hero(self, parent: tk.Misc, title: str, subtitle: str) -> None:
        ttk.Label(parent, text=title, style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            parent,
            text=subtitle,
            style="TLabel",
            foreground=self.palette["muted"],
            wraplength=760,
        ).pack(anchor="w", pady=(6, 22))

    def show_home(self) -> None:
        self.set_steps(BACKUP_STEPS, 0)

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Troque de sistema, não de identidade.",
                "Salve perfis e credenciais num bundle verificável e restaure "
                "na próxima distro sem depender de sincronização na nuvem.",
            )
            panel = self._panel(page, 155)
            panel.pack(fill="x", pady=(0, 18))
            body = panel.body
            ttk.Label(body, text="ESTE COMPUTADOR", style="Muted.Panel.TLabel").pack(anchor="w")
            if self.inventory is None:
                ttk.Label(
                    body,
                    text="Detectando ambiente…",
                    style="Section.Panel.TLabel",
                ).pack(anchor="w", pady=(9, 4))
                ttk.Progressbar(body, mode="indeterminate", length=260).pack(anchor="w", pady=8)
            else:
                os_info = self.inventory.get("os", {})
                ttk.Label(
                    body,
                    text=str(os_info.get("name") or "Linux"),
                    style="Section.Panel.TLabel",
                ).pack(anchor="w", pady=(9, 4))
                summary = "{} navegador(es) · {} conta(s) de IA · {} destino(s) candidato(s)".format(
                    len(self.inventory.get("browsers", [])),
                    len(self.inventory.get("ai_accounts", [])),
                    sum(1 for disk in self.inventory.get("disks", []) if disk.get("candidate")),
                )
                ttk.Label(body, text=summary, style="Muted.Panel.TLabel").pack(anchor="w")
            actions = ttk.Frame(page, style="Base.TFrame")
            actions.pack(fill="both", expand=True)
            backup = self._panel(actions, 225)
            backup.pack(side="left", fill="both", expand=True, padx=(0, 9))
            ttk.Label(backup.body, text="⇣  Criar backup", style="Section.Panel.TLabel").pack(anchor="w")
            ttk.Label(
                backup.body,
                text="Escolha perfis, contas e destinos. O bundle só é aceito após verificar os checksums.",
                style="Muted.Panel.TLabel",
                wraplength=330,
            ).pack(anchor="w", pady=(12, 20))
            ttk.Button(
                backup.body,
                text="Começar backup",
                style="Accent.TButton",
                command=self.show_backup_selection,
                state="normal" if self.inventory is not None else "disabled",
            ).pack(anchor="w")
            restore = self._panel(actions, 225)
            restore.pack(side="left", fill="both", expand=True, padx=(9, 0))
            ttk.Label(restore.body, text="⇡  Restaurar", style="Section.Panel.TLabel").pack(anchor="w")
            ttk.Label(
                restore.body,
                text="Abra um bundle, confira o manifesto e aplique raw ou converta entre engines.",
                style="Muted.Panel.TLabel",
                wraplength=330,
            ).pack(anchor="w", pady=(12, 20))
            ttk.Button(
                restore.body,
                text="Abrir restore",
                style="Secondary.TButton",
                command=self.show_restore_bundle,
            ).pack(anchor="w")

        self._show("home", "Visão geral", build)

    def show_backup_selection(self) -> None:
        if self.inventory is None:
            messagebox.showinfo("Distrohop", "A detecção ainda está terminando.")
            return
        self.set_steps(BACKUP_STEPS, 1)
        defaults = default_selection(self.inventory)
        browser_vars: List[Tuple[str, tk.BooleanVar]] = []
        ai_vars: List[Tuple[str, tk.BooleanVar]] = []
        extra_vars: List[Tuple[str, tk.BooleanVar]] = []

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "O que deve atravessar a formatação?",
                "A cópia raw preserva o navegador; o formato neutro permite conversão entre engines.",
            )
            panel = self._panel(page, 410)
            panel.pack(fill="both", expand=True)
            body = panel.body
            columns = ttk.Frame(body, style="Panel.TFrame")
            columns.pack(fill="both", expand=True)
            left = ttk.Frame(columns, style="Panel.TFrame")
            left.pack(side="left", fill="both", expand=True, padx=(0, 18))
            right = ttk.Frame(columns, style="Panel.TFrame")
            right.pack(side="left", fill="both", expand=True)
            ttk.Label(left, text="Navegadores", style="Section.Panel.TLabel").pack(anchor="w", pady=(0, 8))
            for browser in self.inventory.get("browsers", []):
                for profile in browser.get("profiles", []):
                    path = str(profile["path"])
                    variable = tk.BooleanVar(value=path in defaults.browser_profiles)
                    browser_vars.append((path, variable))
                    ttk.Checkbutton(
                        left,
                        text="{} · {}".format(browser.get("name"), profile.get("name")),
                        variable=variable,
                    ).pack(anchor="w", pady=3)
            if not browser_vars:
                ttk.Label(left, text="Nenhum perfil encontrado.", style="Muted.Panel.TLabel").pack(anchor="w")
            ttk.Label(right, text="Contas de IA", style="Section.Panel.TLabel").pack(anchor="w", pady=(0, 8))
            for account in self.inventory.get("ai_accounts", []):
                path = str(account["path"])
                variable = tk.BooleanVar(value=path in defaults.ai_accounts)
                ai_vars.append((path, variable))
                ttk.Checkbutton(
                    right,
                    text="{} · {}".format(account.get("tool"), account.get("slot")),
                    variable=variable,
                ).pack(anchor="w", pady=3)
            if not ai_vars:
                ttk.Label(right, text="Nenhuma conta encontrada.", style="Muted.Panel.TLabel").pack(anchor="w")
            ttk.Label(right, text="Sistema", style="Section.Panel.TLabel").pack(anchor="w", pady=(20, 8))
            labels = {
                "ssh": "Chaves SSH",
                "gpg": "Chaves GPG",
                "dotfiles": "Dotfiles conhecidos",
                "packages": "Inventário de pacotes",
            }
            for key, label in labels.items():
                variable = tk.BooleanVar(value=key in defaults.extras)
                extra_vars.append((key, variable))
                ttk.Checkbutton(right, text=label, variable=variable).pack(anchor="w", pady=3)
            footer = ttk.Frame(body, style="Panel.TFrame")
            footer.pack(fill="x", pady=(18, 0))
            ttk.Button(footer, text="Voltar", style="Secondary.TButton", command=self.show_home).pack(side="left")

            def proceed() -> None:
                self._backup_selection = Selection(
                    browser_profiles=tuple(path for path, variable in browser_vars if variable.get()),
                    ai_accounts=tuple(path for path, variable in ai_vars if variable.get()),
                    extras=tuple(key for key, variable in extra_vars if variable.get()),
                )
                self.show_backup_destination()

            ttk.Button(footer, text="Continuar", style="Accent.TButton", command=proceed).pack(side="right")

        self._show("backup-selection", "Novo backup", build)

    def show_backup_destination(self) -> None:
        self.go_step(2)
        target_items: List[str] = []
        encrypt = tk.BooleanVar(value=False)
        password = tk.StringVar()
        confirmation = tk.StringVar()

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Escolha onde o bundle vai sobreviver.",
                "Você pode gravar e verificar o mesmo snapshot em vários discos.",
            )
            panel = self._panel(page, 400)
            panel.pack(fill="both", expand=True)
            body = panel.body
            ttk.Label(body, text="Destinos", style="Section.Panel.TLabel").pack(anchor="w")
            destinations = tk.Listbox(
                body,
                height=5,
                background=self.palette["input"],
                foreground=self.palette["text"],
                selectbackground=self.palette["accent"],
                borderwidth=0,
                highlightthickness=1,
                highlightbackground=self.palette["border"],
            )
            destinations.pack(fill="x", pady=(10, 8))

            def add_target() -> None:
                selected = filedialog.askdirectory(title="Escolha um destino de backup")
                if selected and selected not in target_items:
                    target_items.append(selected)
                    destinations.insert("end", selected)

            def remove_target() -> None:
                selection = destinations.curselection()
                if selection:
                    index = int(selection[0])
                    destinations.delete(index)
                    target_items.pop(index)

            buttons = ttk.Frame(body, style="Panel.TFrame")
            buttons.pack(fill="x")
            ttk.Button(buttons, text="+ Adicionar pasta", style="Secondary.TButton", command=add_target).pack(side="left")
            ttk.Button(buttons, text="Remover", style="Ghost.TButton", command=remove_target).pack(side="left", padx=8)
            ttk.Separator(body).pack(fill="x", pady=18)
            ttk.Checkbutton(
                body,
                text="Cifrar conteúdo sensível com senha",
                variable=encrypt,
                command=lambda: self.go_step(3 if encrypt.get() else 2),
            ).pack(anchor="w")
            secrets = ttk.Frame(body, style="Panel.TFrame")
            secrets.pack(fill="x", pady=(10, 0))
            ttk.Label(secrets, text="Senha", style="Muted.Panel.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(secrets, text="Confirmar", style="Muted.Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(14, 0))
            ttk.Entry(secrets, textvariable=password, show="•").grid(row=1, column=0, sticky="ew", pady=5)
            ttk.Entry(secrets, textvariable=confirmation, show="•").grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=5)
            secrets.columnconfigure((0, 1), weight=1)
            footer = ttk.Frame(body, style="Panel.TFrame")
            footer.pack(fill="x", pady=(18, 0))
            ttk.Button(footer, text="Voltar", style="Secondary.TButton", command=self.show_backup_selection).pack(side="left")

            def start(dry_run: bool) -> None:
                if not target_items and not dry_run:
                    messagebox.showwarning("Distrohop", "Adicione pelo menos um destino.")
                    return
                chosen_password: Optional[str] = None
                if encrypt.get():
                    if not password.get() or password.get() != confirmation.get():
                        messagebox.showwarning("Distrohop", "Informe duas senhas iguais e não vazias.")
                        return
                    chosen_password = password.get()
                self._backup_targets = tuple(Path(item) for item in target_items)
                self._backup_password = chosen_password
                self._plan_backup_gui(dry_run=dry_run, encrypted=encrypt.get())

            ttk.Button(
                footer,
                text="Ver plano",
                style="Secondary.TButton",
                command=lambda: start(True),
            ).pack(side="right", padx=(8, 0))
            ttk.Button(
                footer,
                text="Criar backup",
                style="Accent.TButton",
                command=lambda: start(False),
            ).pack(side="right")

        self._show("backup-destination", "Destino e proteção", build)

    def _plan_backup_gui(self, *, dry_run: bool, encrypted: bool) -> None:
        if self.inventory is None or self._backup_selection is None:
            return
        self.show_progress("Planejando backup", BACKUP_STEPS, 3 if encrypted else 2)

        def operation(callback: Callable[[Event], None]) -> BackupPlan:
            return plan_backup(
                self._backup_selection or Selection(),
                self._backup_targets,
                encrypted=encrypted,
                inventory=self.inventory,
                callback=callback,
            )

        def ready(plan: BackupPlan) -> None:
            if dry_run:
                self.show_plan(plan)
                return
            self.go_step(4)
            self._append_log("• Plano validado; iniciando captura.")
            self._worker(
                "run-backup",
                lambda callback: run_backup(
                    plan,
                    password=self._backup_password,
                    callback=callback,
                ),
                lambda result: self.show_result("Backup concluído", result, BACKUP_STEPS),
            )

        self._worker("plan-backup", operation, ready)

    def show_restore_bundle(self) -> None:
        self.set_steps(RESTORE_STEPS, 0)
        path_var = tk.StringVar()

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Abra o bundle de origem.",
                "O manifesto permanece legível mesmo quando o conteúdo está cifrado.",
            )
            panel = self._panel(page, 300)
            panel.pack(fill="x")
            body = panel.body
            ttk.Label(body, text="Pasta do bundle", style="Section.Panel.TLabel").pack(anchor="w")
            row = ttk.Frame(body, style="Panel.TFrame")
            row.pack(fill="x", pady=(12, 18))
            ttk.Entry(row, textvariable=path_var).pack(side="left", fill="x", expand=True)
            ttk.Button(
                row,
                text="Procurar",
                style="Secondary.TButton",
                command=lambda: path_var.set(
                    filedialog.askdirectory(title="Escolha um bundle Distrohop") or path_var.get()
                ),
            ).pack(side="left", padx=(10, 0))
            ttk.Label(
                body,
                text="Nada será alterado nesta etapa: primeiro conferimos manifesto e checksums.",
                style="Muted.Panel.TLabel",
            ).pack(anchor="w")
            footer = ttk.Frame(body, style="Panel.TFrame")
            footer.pack(fill="x", pady=(28, 0))
            ttk.Button(footer, text="Voltar", style="Secondary.TButton", command=self.show_home).pack(side="left")

            def load() -> None:
                candidate = Path(path_var.get())
                if not path_var.get():
                    messagebox.showwarning("Distrohop", "Escolha a pasta do bundle.")
                    return
                self.show_progress("Validando bundle", RESTORE_STEPS, 1)

                def operation(_callback: Callable[[Event], None]) -> Dict[str, Any]:
                    manifest = read_manifest(candidate)
                    if not verify_bundle(candidate):
                        raise ValueError("checksums do bundle não conferem")
                    return manifest

                def ready(manifest: Any) -> None:
                    self.manifest = dict(manifest)
                    self.bundle_path = candidate
                    self.show_restore_options()

                self._worker("load-bundle", operation, ready)

            ttk.Button(footer, text="Validar bundle", style="Accent.TButton", command=load).pack(side="right")

        self._show("restore-bundle", "Restaurar", build)

    def show_restore_options(self) -> None:
        if self.manifest is None or self.bundle_path is None:
            self.show_restore_bundle()
            return
        self.go_step(2)
        components = [
            item for item in self.manifest.get("browsers", []) if isinstance(item, dict)
        ]
        source_labels = [
            "{}/{}".format(item.get("id"), item.get("profile")) for item in components
        ]
        source_var = tk.StringVar(value=source_labels[0] if source_labels else "")
        browser_ids = list(
            dict.fromkeys(
                [str(item.get("id")) for item in (self.inventory or {}).get("browsers", [])]
                + [str(item.get("id")) for item in load_definitions()]
            )
        )
        target_var = tk.StringVar(
            value=str(components[0].get("id")) if components else (browser_ids[0] if browser_ids else "")
        )
        profile_var = tk.StringVar()
        install_var = tk.BooleanVar(value=False)
        password_var = tk.StringVar()

        def update_target(_event: Optional[tk.Event] = None) -> None:
            matches = [
                profile
                for browser in (self.inventory or {}).get("browsers", [])
                if browser.get("id") == target_var.get()
                for profile in browser.get("profiles", [])
            ]
            if len(matches) == 1:
                profile_var.set(str(matches[0]["path"]))

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Escolha origem e destino.",
                "{} · {} arquivo(s) verificado(s)".format(
                    "Bundle cifrado" if self.manifest.get("encrypted") else "Bundle sem cifra",
                    len(self.manifest.get("files", {})),
                ),
            )
            panel = self._panel(page, 390)
            panel.pack(fill="both", expand=True)
            body = panel.body
            grid = ttk.Frame(body, style="Panel.TFrame")
            grid.pack(fill="x")
            ttk.Label(grid, text="Perfil de origem", style="Muted.Panel.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(grid, text="Navegador de destino", style="Muted.Panel.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0))
            source_box = ttk.Combobox(grid, textvariable=source_var, values=source_labels, state="readonly")
            source_box.grid(row=1, column=0, sticky="ew", pady=(6, 14))
            target_box = ttk.Combobox(grid, textvariable=target_var, values=browser_ids, state="readonly")
            target_box.grid(row=1, column=1, sticky="ew", padx=(16, 0), pady=(6, 14))
            target_box.bind("<<ComboboxSelected>>", update_target)
            ttk.Label(grid, text="Pasta do perfil de destino", style="Muted.Panel.TLabel").grid(row=2, column=0, columnspan=2, sticky="w")
            ttk.Entry(grid, textvariable=profile_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 14))
            if self.manifest.get("encrypted"):
                ttk.Label(grid, text="Senha do bundle", style="Muted.Panel.TLabel").grid(row=4, column=0, sticky="w")
                ttk.Entry(grid, textvariable=password_var, show="•").grid(row=5, column=0, sticky="ew", pady=(6, 12))
            ttk.Checkbutton(
                grid,
                text="Instalar navegador se estiver ausente",
                variable=install_var,
            ).grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
            grid.columnconfigure((0, 1), weight=1)
            update_target()
            footer = ttk.Frame(body, style="Panel.TFrame")
            footer.pack(fill="x", pady=(24, 0))
            ttk.Button(footer, text="Voltar", style="Secondary.TButton", command=self.show_restore_bundle).pack(side="left")

            def start(dry_run: bool) -> None:
                if not source_var.get() or not target_var.get():
                    messagebox.showwarning("Distrohop", "Escolha os navegadores de origem e destino.")
                    return
                index = source_labels.index(source_var.get())
                component = components[index]
                self._plan_restore_gui(
                    browser_id=str(component.get("id")),
                    source_profile=str(component.get("profile")),
                    target_browser_id=target_var.get(),
                    target_profile=Path(profile_var.get()) if profile_var.get() else None,
                    install=install_var.get(),
                    password=password_var.get() or None,
                    dry_run=dry_run,
                )

            ttk.Button(footer, text="Ver plano", style="Secondary.TButton", command=lambda: start(True)).pack(side="right", padx=(8, 0))
            ttk.Button(footer, text="Restaurar", style="Accent.TButton", command=lambda: start(False)).pack(side="right")

        self._show("restore-options", "Configurar restore", build)

    def _plan_restore_gui(
        self,
        *,
        browser_id: str,
        source_profile: str,
        target_browser_id: str,
        target_profile: Optional[Path],
        install: bool,
        password: Optional[str],
        dry_run: bool,
    ) -> None:
        if self.bundle_path is None or self.inventory is None:
            return
        self.show_progress("Planejando restore", RESTORE_STEPS, 3)

        def operation(callback: Callable[[Event], None]) -> RestorePlan:
            return plan_restore(
                self.bundle_path or Path(),
                browser_id=browser_id,
                source_profile=source_profile,
                target_browser_id=target_browser_id,
                target_profile=target_profile,
                install=install,
                inventory=self.inventory,
                callback=callback,
            )

        def ready(plan: RestorePlan) -> None:
            if dry_run:
                self.show_plan(plan)
                return
            self.go_step(4)
            self._worker(
                "run-restore",
                lambda callback: run_restore(
                    plan,
                    password=password,
                    callback=callback,
                ),
                lambda result: self.show_result("Restore concluído", result, RESTORE_STEPS),
            )

        self._worker("plan-restore", operation, ready)

    def show_progress(
        self,
        title: str,
        steps: Sequence[Tuple[str, str]],
        index: int,
    ) -> None:
        self.set_steps(steps, index)

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                title,
                "A janela continua responsiva enquanto o motor trabalha em segundo plano.",
            )
            panel = self._panel(page, 410)
            panel.pack(fill="both", expand=True)
            body = panel.body
            ttk.Progressbar(body, mode="indeterminate").pack(fill="x", pady=(0, 18))
            self.progress_log = tk.Text(
                body,
                height=15,
                background=self.palette["input"],
                foreground=self.palette["text"],
                insertbackground=self.palette["text"],
                borderwidth=0,
                padx=14,
                pady=12,
                state="disabled",
            )
            self.progress_log.pack(fill="both", expand=True)
            self._append_log("• " + title)

        self._show("progress", title, build)

    def show_plan(self, plan: Any) -> None:
        steps = RESTORE_STEPS if isinstance(plan, RestorePlan) else BACKUP_STEPS
        self.set_steps(steps, 3 if isinstance(plan, RestorePlan) else 2)

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Plano somente leitura",
                "Nenhum arquivo foi alterado. Revise as origens e gravações abaixo.",
            )
            panel = self._panel(page, 430)
            panel.pack(fill="both", expand=True)
            text = tk.Text(
                panel.body,
                background=self.palette["input"],
                foreground=self.palette["text"],
                borderwidth=0,
                padx=14,
                pady=12,
                wrap="none",
            )
            text.pack(fill="both", expand=True)
            text.insert("end", "LEITURAS\n")
            for source in plan.sources:
                text.insert("end", "  {}\n".format(source))
            text.insert("end", "\nGRAVAÇÕES PLANEJADAS\n")
            for output in plan.outputs:
                text.insert("end", "  {}\n".format(output))
            for warning in getattr(plan, "warnings", ()):
                text.insert("end", "\nAVISO: {}\n".format(warning))
            text.configure(state="disabled")
            ttk.Button(
                panel.body,
                text="Voltar ao início",
                style="Secondary.TButton",
                command=self.show_home,
            ).pack(anchor="e", pady=(12, 0))

        self._show("plan", "Dry-run", build)

    def show_result(
        self,
        heading: str,
        result: Mapping[str, Any],
        steps: Sequence[Tuple[str, str]],
    ) -> None:
        self.set_steps(steps, 6)
        self.status_var.set(heading)

        def build(page: ttk.Frame) -> None:
            self._hero(
                page,
                "Tudo conferido.",
                heading + ". A cópia anterior foi preservada quando havia um perfil no destino.",
            )
            panel = self._panel(page, 340)
            panel.pack(fill="x")
            ttk.Label(panel.body, text="✓  {}".format(heading), style="Section.Panel.TLabel").pack(anchor="w")
            summary = tk.Text(
                panel.body,
                height=10,
                background=self.palette["input"],
                foreground=self.palette["text"],
                borderwidth=0,
                padx=14,
                pady=12,
            )
            summary.pack(fill="x", pady=14)
            for key, value in result.items():
                if key == "warnings" and not value:
                    continue
                summary.insert("end", "{}: {}\n".format(key, value))
            summary.configure(state="disabled")
            ttk.Button(
                panel.body,
                text="Voltar ao início",
                style="Accent.TButton",
                command=self.show_home,
            ).pack(anchor="e")

        self._show("result", heading, build)


def launch() -> None:
    root = tk.Tk()
    DistrohopApp(root)
    root.mainloop()
