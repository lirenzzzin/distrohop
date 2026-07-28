from __future__ import annotations

import ast
import errno
import io
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from distrohop import __main__ as entrypoint
from distrohop.ui import tk_available
from distrohop.ui.gui import DARK, DistrohopApp, _friendly_error
from distrohop.ui.i18n import system_language, translate, translate_block
from distrohop.ui.preferences import load_preferences, save_preferences
from distrohop.vault.targets import create_private_target


class TkAvailabilityTests(unittest.TestCase):
    def test_probe_reports_import_and_display_failures(self) -> None:
        importer = Mock(side_effect=ImportError("no tkinter"))
        available, reason = tk_available.probe_tk(importer=importer)
        self.assertFalse(available)
        self.assertIn("tkinter", reason)

        class BrokenTk:
            def Tk(self) -> object:
                raise RuntimeError("no display")

        available, reason = tk_available.probe_tk(
            importer=lambda _name: BrokenTk()
        )
        self.assertFalse(available)
        self.assertIn("display", reason)

    def test_probe_destroys_hidden_root(self) -> None:
        root = Mock()
        module = Mock()
        module.Tk.return_value = root

        available, reason = tk_available.probe_tk(
            importer=lambda _name: module
        )

        self.assertTrue(available)
        self.assertEqual(reason, "")
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()


class EntrypointTests(unittest.TestCase):
    def test_subcommands_and_cli_flag_never_probe_tk(self) -> None:
        with patch.object(entrypoint.cli, "main", return_value=17) as cli_main, patch.object(
            entrypoint.tk_available, "probe_tk"
        ) as probe:
            self.assertEqual(entrypoint.main(["list"]), 17)
            self.assertEqual(entrypoint.main(["--cli"]), 17)
        self.assertEqual(cli_main.call_count, 2)
        probe.assert_not_called()

    def test_no_args_prefers_gui_and_forced_gui_has_clear_fallback(self) -> None:
        fake_gui = Mock()
        with patch.object(
            entrypoint.tk_available,
            "probe_tk",
            return_value=(True, ""),
        ), patch.object(entrypoint, "_load_gui", return_value=fake_gui):
            self.assertEqual(entrypoint.main([]), 0)
        fake_gui.launch.assert_called_once_with()

        error = io.StringIO()
        with patch.object(
            entrypoint.tk_available,
            "probe_tk",
            return_value=(False, "sem display"),
        ), patch.object(
            entrypoint.tk_available,
            "install_hint",
            return_value="sudo pacman -S tk",
        ), patch("sys.stderr", error):
            self.assertEqual(entrypoint.main(["--gui"]), 2)
        self.assertIn("sudo pacman -S tk", error.getvalue())

    def test_no_tk_falls_back_to_cli_with_notice(self) -> None:
        error = io.StringIO()
        with patch.object(
            entrypoint.tk_available,
            "probe_tk",
            return_value=(False, "sem display"),
        ), patch.object(entrypoint.cli, "main", return_value=0) as cli_main, patch(
            "sys.stderr", error
        ):
            self.assertEqual(entrypoint.main([]), 0)
        cli_main.assert_called_once_with([])
        self.assertIn("modo texto", error.getvalue())


class GuiRegressionTests(unittest.TestCase):
    def test_theme_toggle_discards_destroyed_panels(self) -> None:
        app = DistrohopApp.__new__(DistrohopApp)
        app.theme_name = "light"
        app.palette = {}
        app.root = Mock()
        app.shell = Mock()
        app.theme_button = Mock()
        app._configure_styles = Mock()
        app._draw_sidebar = Mock()
        dead = Mock()
        dead.winfo_exists.return_value = False
        live = Mock()
        live.winfo_exists.return_value = True
        app.panels = [dead, live]

        app.toggle_theme()

        self.assertEqual(app.palette, DARK)
        self.assertEqual(app.panels, [live])
        dead.recolor.assert_not_called()
        live.recolor.assert_called_once_with(DARK)

    def test_no_space_copy_error_is_short_and_actionable(self) -> None:
        error = shutil.Error(
            [
                (
                    "/large/source/one",
                    "/temporary/destination/one",
                    OSError(errno.ENOSPC, "No space left on device"),
                ),
                (
                    "/large/source/two",
                    "/temporary/destination/two",
                    OSError(errno.ENOSPC, "No space left on device"),
                ),
            ]
        )

        message = _friendly_error(error)

        self.assertIn("Espaço insuficiente", message)
        self.assertLess(len(message), 300)
        self.assertNotIn("/large/source", message)

    def test_language_toggle_translates_existing_page_and_header(self) -> None:
        app = DistrohopApp.__new__(DistrohopApp)
        app.language = "pt"
        app.theme_name = "light"
        app.title_var = Mock()
        app.title_var.get.return_value = "Visão geral"
        app.status_var = Mock()
        app.status_var.get.return_value = "Preparando detecção…"
        app.current_page = Mock()
        app.theme_button = Mock()
        app.language_button = Mock()
        app._translate_widget_tree = Mock()
        app._draw_sidebar = Mock()

        app.toggle_language()

        self.assertEqual(app.language, "en")
        app.title_var.set.assert_called_once_with("Overview")
        app.status_var.set.assert_called_once_with("Preparing detection…")
        app._translate_widget_tree.assert_called_once_with(app.current_page)
        app.language_button.configure.assert_called_once_with(text="PT")


class GuiTranslationTests(unittest.TestCase):
    def test_fixed_dynamic_and_engine_strings_round_trip(self) -> None:
        portuguese = (
            "AVISO: Senhas não são importadas automaticamente entre engines."
        )
        english = "WARNING: Passwords are not imported automatically between engines."
        self.assertEqual(translate(portuguese, "en"), english)
        self.assertEqual(translate(english, "pt"), portuguese)
        self.assertEqual(
            translate(
                "3 navegador(es) · 2 conta(s) de IA · 1 destino(s) candidato(s)",
                "en",
            ),
            "3 browser profile(s) · 2 AI account(s) · 1 candidate destination(s)",
        )
        self.assertEqual(
            translate_block("LEITURAS\n• Iniciando captura\n", "en"),
            "READS\n• Starting capture\n",
        )

    def test_language_uses_portuguese_locale_and_defaults_to_english(self) -> None:
        self.assertEqual(system_language({"LANG": "pt_BR.UTF-8"}), "pt")
        self.assertEqual(system_language({}), "en")

    def test_every_static_widget_label_has_an_english_entry(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "distrohop" / "ui" / "gui.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        visible = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg in ("text", "title")
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    visible.append(keyword.value.value)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_hero"
            ):
                visible.extend(
                    argument.value
                    for argument in node.args[1:]
                    if isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                )
        unchanged = {
            text
            for text in visible
            if translate(text, "en") == text
            and text not in {"D", "DISTROHOP", "☰"}
        }
        self.assertEqual(unchanged, set())


class DestinationFolderTests(unittest.TestCase):
    def test_creates_one_private_subfolder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            created = create_private_target(Path(temporary), "Distrohop backups")

            self.assertEqual(created, Path(temporary) / "Distrohop backups")
            self.assertTrue(created.is_dir())
            if hasattr(stat, "S_IMODE"):
                self.assertEqual(stat.S_IMODE(created.stat().st_mode), 0o700)

    def test_rejects_escape_reserved_and_existing_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            existing = create_private_target(parent, "existing")
            self.assertTrue(existing.exists())
            with self.assertRaises(FileExistsError):
                create_private_target(parent, "existing")
            for invalid in ("", "..", "../escape", "child/grandchild", "COM1"):
                with self.subTest(name=invalid), self.assertRaises(ValueError):
                    create_private_target(parent, invalid)


class GuiPreferenceTests(unittest.TestCase):
    def test_preferences_round_trip_and_ignore_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "prefs.json"
            save_preferences({"language": "en", "theme": "dark"}, target)
            self.assertEqual(
                load_preferences(target),
                {"language": "en", "theme": "dark"},
            )
            target.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_preferences(target), {})
