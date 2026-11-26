"""Application exit closes tool windows, so the process really quits.

Qt ends the event loop when the last *visible* top-level window closes. A
visible magnet window used to survive Exit, so the process kept running
after hardware had already been shut down.

The exit tests launch the real application entry point with --sim (every
device simulated, config/setupInfo.json only read) in a subprocess,
offscreen, choose Exit and click Yes in the real dialog, and require the
process to end by itself with exit code 0 after HardwareManager.shutdown()
has run. Only a separate process can show that the process quits.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from gui.magnet_window import MagnetControlWindow  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from tests.test_magnetic_field_gui_units import _make_magnet  # noqa: E402
from tests.test_main_window_close_safety import _make_main_window  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# Runs the real gui.main_window.main() with --sim. MODE is open / hidden /
# never (the magnet window's state at Exit). A watchdog force-exits with
# code 3 (and lists the visible windows) if the process doesn't end itself.
_DRIVER = textwrap.dedent(
    """
    import os, sys
    sys.argv = ["main.py", "--sim"]
    sys.path.insert(0, os.getcwd())
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox
    import gui.main_window as mw

    MODE = os.environ["EXIT_TEST_MODE"]
    original_show = mw.MainWindow.show

    def say(*args):
        print("EXIT-TEST:", *args, flush=True)

    def show(self):
        original_show(self)
        original_shutdown = self.hw_manager.shutdown

        def traced_shutdown():
            original_shutdown()
            supplies = self.hardware["power_supplies"].values()
            say("shutdown",
                "magnet_enabled=%s" % self.hardware["magnet"].is_enabled(),
                "outputs=%s" % [ps.output_enabled for ps in supplies],
                "connected=%s" % [ps.connected for ps in supplies],
                "magnet_window_visible=%s" % any(
                    w.isVisible() for w in QApplication.topLevelWidgets()
                    if w.windowTitle() == "Magnet Control"))

        self.hw_manager.shutdown = traced_shutdown
        QTimer.singleShot(300, lambda: setup(self))

    def setup(win):
        if MODE in ("open", "hidden"):
            win.open_magnet_button.click()
            if MODE == "hidden":
                win.magnet_window.close()
        QTimer.singleShot(300, _answer_yes)
        QTimer.singleShot(15000, watchdog)
        win.close()

    def _answer_yes():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.button(QMessageBox.StandardButton.Yes).click()
        else:
            QTimer.singleShot(100, _answer_yes)

    def watchdog():
        visible = [w.windowTitle() for w in QApplication.topLevelWidgets()
                   if w.isVisible()]
        say("still running, visible:", visible)
        os._exit(3)

    mw.MainWindow.show = show
    mw.main()
    """
)


def _run_app_and_exit(mode):
    # main() remembers open/closed sections; keep that out of the user's
    # registry by pointing it at a throwaway INI file.
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, EXIT_TEST_MODE=mode, QT_QPA_PLATFORM="offscreen",
                   PYTHONIOENCODING="utf-8",
                   NV_CONTROL_UI_STATE_FILE=os.path.join(tmp, "ui_state.ini"))
        return subprocess.run(
            [sys.executable, "-c", _DRIVER], cwd=REPO, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90,
        )


class ProcessExitTests(unittest.TestCase):
    """The real app, --sim, in its own process."""

    def _assert_quits_after_shutdown(self, mode):
        result = _run_app_and_exit(mode)
        markers = [line for line in result.stdout.splitlines()
                   if line.startswith("EXIT-TEST:")]
        detail = "\n".join(markers) + "\n" + result.stderr[-2000:]
        self.assertEqual(result.returncode, 0, detail)
        self.assertIn("Shutdown complete in", result.stdout, detail)
        self.assertEqual(len(markers), 1, detail)
        self.assertEqual(
            markers[0],
            "EXIT-TEST: shutdown magnet_enabled=False "
            "outputs=[False, False, False] connected=[False, False, False] "
            "magnet_window_visible=False",
        )

    def test_exit_with_magnet_window_open(self):
        self._assert_quits_after_shutdown("open")

    def test_exit_with_magnet_window_hidden(self):
        self._assert_quits_after_shutdown("hidden")

    def test_exit_with_magnet_window_never_opened(self):
        self._assert_quits_after_shutdown("never")


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class MagnetWindowLifecycleTests(unittest.TestCase):
    """MainWindow built without hardware, as in test_main_window_close_safety."""

    def setUp(self):
        self.main = _make_main_window()
        self.main.hardware = {"magnet": _make_magnet()}
        self.addCleanup(self._dispose_main)

    def _dispose_main(self):
        if self.main.magnet_window is not None:
            _dispose(self.main.magnet_window)
        self.main._shut_down = True  # close without the Exit dialog
        _dispose(self.main)

    def test_reopen_after_close_shows_the_same_window(self):
        self.main.open_magnet_window()
        window = self.main.magnet_window
        self.assertTrue(window.isVisible())
        window.close()
        self.assertFalse(window.isVisible())
        self.assertIs(self.main.magnet_window, window)  # hidden, not destroyed
        self.main.open_magnet_window()
        self.assertIs(self.main.magnet_window, window)
        self.assertTrue(window.isVisible())

    def test_shutdown_closes_and_deletes_the_magnet_window_before_hardware(self):
        order = []
        self.main.open_magnet_window()
        window = self.main.magnet_window
        window.destroyed.connect(lambda: order.append("magnet window destroyed"))
        self.main.hw_manager.shutdown = Mock(
            side_effect=lambda: order.append(
                f"hardware shutdown (magnet window visible={window.isVisible()})"
            )
        )
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.main.close())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(self.main.magnet_window)
        self.assertEqual(order, [
            "hardware shutdown (magnet window visible=False)",
            "magnet window destroyed",
        ])

    def _finish_experiment_after_close(self, close_tool_windows):
        """Close with an experiment announced, then let it finish (as a
        worker outliving a forced close would). Returns the magnet calls the
        magnet window's post-experiment action made afterwards."""
        magnet = self.main.hardware["magnet"]
        magnet.enable()
        self.main.open_magnet_window()
        self.main.experiment_started.emit("ODMR", False)
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            if close_tool_windows:
                self.main.close()
            else:
                with patch.object(MainWindow, "_close_tool_windows"):
                    self.main.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        with patch.object(magnet, "set_vector") as set_vector:
            self.main.experiment_finished.emit()
        return set_vector.call_args_list

    def test_closed_magnet_window_sends_no_post_experiment_command(self):
        self.assertEqual(self._finish_experiment_after_close(True), [])

    def test_control_without_tool_window_close_the_command_is_sent(self):
        # Shows the test above can fail: the hidden-but-alive window acts.
        self.assertEqual(len(self._finish_experiment_after_close(False)), 1)

    def test_shutdown_closes_the_analysis_window(self):
        analysis = Mock()
        self.main.odmr_analysis_window = analysis
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.main.close()
        analysis.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
