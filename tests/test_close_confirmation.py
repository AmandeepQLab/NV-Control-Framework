"""Every window confirms before closing (gui/close_confirmation.py).

- The dialog: Cancel is the default and the Esc answer; no "don't ask again".
- Every window/non-modal dialog class in gui/ uses the shared mixin.
- Each window asks on close; Cancel keeps it open, Yes closes it.
- The message reflects the state: unsaved vs saved scan/run, coils on vs
  off, pending queue entries saved or not.
- No double dialog: a running scan gets only its own question; MainWindow's
  exit closes the other windows without asking again.

The real MainWindow(sim=True); the session-wide auto-answer from
tests/conftest.py is overridden here with auto_answer(True/False).
"""

import importlib
import inspect
import pkgutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import gui  # noqa: E402
from gui import close_confirmation  # noqa: E402
from gui.close_confirmation import (  # noqa: E402
    CloseConfirmation,
    ConfirmCloseMixin,
    auto_answer,
    confirm_close,
)
from gui.main_window import MainWindow  # noqa: E402
from gui.widgets import collapsible_section  # noqa: E402
from tests.test_main_window_close_safety import (  # noqa: E402
    BlockingWorkerThread,
    FakeExperimentWindow,
)


def _pump(seconds=0.05):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# Modal prompts whose closing is their own Cancel answer (not windows).
_MODAL_PROMPTS = {"InitialFitParametersDialog"}


class DialogTests(unittest.TestCase):
    def test_cancel_is_default_and_escape_and_there_is_no_dont_ask_again(self):
        shown = []

        class RecordingBox(QMessageBox):
            def exec(self):  # never shown; nothing clicked
                shown.append(self)
                return 0

        confirmation = CloseConfirmation("Close X", "Close the X window?",
                                         ["Fact one.", "", "Fact two."])
        with auto_answer(None), patch.dict("os.environ",
                                           {close_confirmation.AUTO_ANSWER_ENV: ""}), \
                patch.object(close_confirmation, "QMessageBox", RecordingBox):
            answer = confirm_close(None, confirmation)
        self.assertFalse(answer)  # nothing clicked (X / Esc) = Cancel
        box = shown[0]
        cancel = box.button(QMessageBox.StandardButton.Cancel)
        yes = box.button(QMessageBox.StandardButton.Yes)
        self.assertIs(box.defaultButton(), cancel)
        self.assertIs(box.escapeButton(), cancel)
        self.assertEqual(yes.text(), "Close")
        self.assertIsNone(box.checkBox())
        self.assertEqual(len(box.buttons()), 2)
        self.assertEqual(box.windowTitle(), "Close X")
        self.assertEqual(box.text(), "Close the X window?")
        self.assertEqual(box.informativeText(), "• Fact one.\n• Fact two.")

    def test_auto_answer_and_history(self):
        confirmation = CloseConfirmation("T", "Q?")
        with auto_answer(False) as history:
            self.assertFalse(confirm_close(None, confirmation))
            self.assertIs(history[-1], confirmation)
        with auto_answer(True):
            self.assertTrue(confirm_close(None, confirmation))

    def test_every_window_class_uses_the_shared_confirmation(self):
        missing = []
        for module_info in pkgutil.walk_packages(gui.__path__, "gui."):
            module = importlib.import_module(module_info.name)
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ != module.__name__ or name in _MODAL_PROMPTS:
                    continue
                if not issubclass(cls, (QMainWindow, QDialog)):
                    continue
                if not issubclass(cls, ConfirmCloseMixin) or \
                        cls.close_confirmation is ConfirmCloseMixin.close_confirmation:
                    missing.append(f"{module.__name__}.{name}")
        self.assertEqual(missing, [])


class SimWindowsTestCase(unittest.TestCase):
    def setUp(self):
        collapsible_section.disable_state_persistence()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.main = MainWindow(sim=True)
        self.magnet = self.main.hardware["magnet"]
        self.addCleanup(self._close_main)
        self.main.show()

    def _close_main(self):
        with auto_answer(True):
            self.main.close()
        for window in (self.main.odmr_window, self.main.zero_field_window):
            if window is not None:
                _dispose(window)
        _dispose(self.main)

    def _odmr(self):
        self.main.open_odmr_window()
        return self.main.odmr_window

    def _zero_field(self):
        self.main.open_zero_field_window()
        window = self.main.zero_field_window
        window.output_directory = Path(self.tmp.name)
        window.widget.set_output_directory(window.output_directory)
        return window

    def _magnet(self):
        self.main.open_magnet_window()
        return self.main.magnet_window

    def _analysis(self):
        self.main.open_odmr_analysis_window()
        return self.main.odmr_analysis_window

    def _pulse_sequence(self):
        odmr = self._odmr()
        odmr.show_pulse_sequence()
        return odmr.sequence_window

    def _statistics_dialog(self):
        odmr = self._odmr()
        odmr.panel.show_statistics_explanation()
        return odmr.panel.statistics_dialog

    def _asked(self, window, answer):
        """Close *window* with the given answer; return what it asked."""
        with auto_answer(answer) as history:
            before = len(history)
            window.close()
            _pump()
            return history[before:]


class EachWindowConfirmsTests(SimWindowsTestCase):
    WINDOWS = {
        "ODMR": ("_odmr", "Close ODMR Experiment"),
        "Zero Field": ("_zero_field", "Close Zero-Field Imaging"),
        "Magnet": ("_magnet", "Close Magnet Control"),
        "Analysis": ("_analysis", "Close ODMR Analysis"),
        "Pulse sequence": ("_pulse_sequence", "Close Pulse Sequence Inspector"),
        "Statistics": ("_statistics_dialog", "Close ODMR Statistics Explanation"),
    }

    def test_cancel_keeps_each_window_open_and_yes_closes_it(self):
        for label, (factory, title) in self.WINDOWS.items():
            with self.subTest(window=label):
                window = getattr(self, factory)()
                self.assertTrue(window.isVisible())
                asked = self._asked(window, False)
                self.assertEqual([c.title for c in asked], [title])
                self.assertTrue(window.isVisible(), "Cancel must keep it open")
                self.assertTrue(asked[0].facts, "a window-specific message")
                asked = self._asked(window, True)
                self.assertEqual([c.title for c in asked], [title])
                self.assertFalse(window.isVisible())

    def test_escape_on_the_viewer_dialogs_asks_too(self):
        for factory in ("_pulse_sequence", "_statistics_dialog"):
            with self.subTest(dialog=factory):
                dialog = getattr(self, factory)()
                with auto_answer(False) as history:
                    before = len(history)
                    dialog.reject()  # what Esc does
                    self.assertEqual(len(history), before + 1)
                self.assertTrue(dialog.isVisible())
                with auto_answer(True):
                    dialog.reject()
                self.assertFalse(dialog.isVisible())

    def test_main_window_cancel_keeps_the_app_and_yes_exits_once(self):
        self._odmr()
        self._zero_field()
        self._magnet()
        asked = self._asked(self.main, False)
        self.assertEqual([c.title for c in asked], ["Exit NV Control"])
        self.assertFalse(self.main._shut_down)
        asked = self._asked(self.main, True)
        # Only the exit question: the other windows close without asking.
        self.assertEqual([c.title for c in asked], ["Exit NV Control"])
        self.assertTrue(self.main._shut_down)

    def test_hidden_window_closes_without_asking(self):
        window = self._magnet()
        window.hide()
        self.assertEqual(self._asked(window, False), [])


class MessageContentTests(SimWindowsTestCase):
    def _facts(self, window):
        return window.close_confirmation().text()

    def test_odmr_unsaved_vs_saved_scan(self):
        odmr = self._odmr()
        self.assertIn("No scan has been run in this window.", self._facts(odmr))
        odmr.current_config = {"f_start": 2.84e9, "f_stop": 2.90e9,
                               "averages": 5, "save_data": False}
        odmr.latest_x = list(np.linspace(2.84, 2.90, 61))
        odmr._record_last_scan(5, 5)
        text = self._facts(odmr)
        self.assertIn("The last scan (2.84–2.9 GHz, 61 points, 5 averages) is not "
                      "saved", text)
        self.assertEqual(odmr.unsaved_results(), ["last scan not saved"])
        odmr._record_last_scan(3, 5)
        odmr.last_scan["saved_to"] = r"C:\data\odmr_2026-11-26_10-00-00"
        text = self._facts(odmr)
        self.assertIn("stopped after 3 of 5 averages", text)
        self.assertIn(r"is saved in C:\data\odmr_2026-11-26_10-00-00", text)
        self.assertEqual(odmr.unsaved_results(), [])
        odmr.fit_shown = True
        self.assertIn("The fit result shown is not saved anywhere.", self._facts(odmr))

    def test_magnet_coils_on_vs_off(self):
        window = self._magnet()
        self.assertIn("coils off", self._facts(window))
        self.magnet.enable()
        self.magnet.set_vector(bx=0.2, by=0.0, bz=0.0)
        text = self._facts(window)
        self.assertIn("coils on, model field (2, 0, 0) G", text)
        self.assertIn("the coils stay energized", text)
        main_text = self.main.close_confirmation().text()
        self.assertIn("they are switched off (0 A, outputs off)", main_text)
        odmr_text = self._facts(self._odmr())
        self.assertIn("Magnet: coils on, model field (2, 0, 0) G. Closing this "
                      "window leaves it as it is", odmr_text)

    def test_zero_field_queue_and_last_run(self):
        window = self._zero_field()
        self.assertIn("Run queue: empty.", self._facts(window))
        w = window.widget
        w.field_start_spin.setValue(-1.0)
        w.field_stop_spin.setValue(1.0)
        w.field_points_spin.setValue(3)
        for _ in range(3):
            self.assertTrue(window.add_to_queue())
        self.assertIn("Run queue: 3 entries (3 pending), not saved to a queue file.",
                      self._facts(window))
        path = Path(self.tmp.name) / "q.json"
        self.assertTrue(window.save_queue_to(path))
        self.assertIn("Run queue: 3 entries (3 pending), saved in q.json.",
                      self._facts(window))
        window.queue_widget.select_row(0)
        window.duplicate_queue_entry()
        self.assertIn("changed since it was saved to q.json", self._facts(window))
        self.assertIn("run queue not saved to a file", window.unsaved_results())

        window.last_run_config = w.get_config()
        window.output_path = None
        window.image_cube = SimpleNamespace(data=np.zeros((3, 2, 2)))
        text = self._facts(window)
        self.assertIn("The last run (-1 → 1 G X, 3 points) is not saved", text)
        self.assertIn(f"Output folder for new runs: {window.output_directory}.", text)
        main_text = self.main.close_confirmation().text()
        self.assertIn("Zero-Field Imaging window: last run not saved, run queue "
                      "not saved to a file — lost on exit.", main_text)

    def test_main_window_lists_hardware_release(self):
        text = self.main.close_confirmation().text()
        self.assertIn("Pulse Streamer: all outputs set LOW", text)
        self.assertIn("SG386: RF output switched off, connection closed.", text)
        self.assertIn("Camera: live view and acquisition stopped, camera closed.", text)
        self.assertIn("Magnet: coils off", text)


class NoDoubleDialogTests(unittest.TestCase):
    def test_running_scan_gets_only_the_scan_in_progress_dialog(self):
        from tests.test_main_window_close_safety import _make_main_window
        main = _make_main_window()
        thread = BlockingWorkerThread()
        window = FakeExperimentWindow(thread)
        main._experiment_window_started(window, "ODMR", False)
        thread.start()
        try:
            with patch.object(main, "_ask_stop_and_close", return_value=False) as ask, \
                    auto_answer(True) as history:
                before = len(history)
                self.assertFalse(main.close())
                self.assertEqual(history[before:], [])
            ask.assert_called_once()
        finally:
            thread.release.set()
            thread.wait(5000)
            main._shut_down = True
            _dispose(main)

    def test_running_odmr_window_asks_once_then_stops_and_closes_itself(self):
        from gui.odmr_window import ODMRWindow
        window = ODMRWindow(hardware={}, camera=None,
                            acquisition_state_getter=lambda: None)
        self.addCleanup(lambda: _dispose(window))
        window.show()
        window.odmr_running = True
        window.current_config = {"save_data": False}
        with patch.object(window, "request_stop") as stop, auto_answer(True) as history:
            before = len(history)
            window.close()
            asked = history[before:]
        self.assertEqual([c.confirm_text for c in asked], ["Stop and Close"])
        self.assertIn("not saved", asked[0].text())
        stop.assert_called_once()
        self.assertTrue(window.isVisible())  # until the scan has stopped
        self.assertTrue(window._close_after_stop)
        # The scan ends: the window closes without asking again.
        window.odmr_running = False
        with auto_answer(False) as history:
            before = len(history)
            close_confirmation.close_without_confirmation(window)
            self.assertEqual(history[before:], [])
        self.assertFalse(window.isVisible())


if __name__ == "__main__":
    unittest.main()
