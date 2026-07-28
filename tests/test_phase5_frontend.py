from __future__ import annotations

import errno
import io
import shutil
import unittest
from unittest.mock import Mock, patch

from distrohop import __main__ as entrypoint
from distrohop.ui import tk_available
from distrohop.ui.gui import DARK, DistrohopApp, _friendly_error


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
