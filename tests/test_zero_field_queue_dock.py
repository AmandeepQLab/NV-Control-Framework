"""Zero Field Run Queue dock (2026-11-26): compact, collapsible, with a
summary, and open/closed state and height remembered at app launch only.

Real ZeroFieldWindow on SimCamera and a real Magnet over simulated supplies.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from gui.widgets import collapsible_section  # noqa: E402
from gui.zero_field_window import QUEUE_DOCK_DEFAULT_HEIGHT, ZeroFieldWindow  # noqa: E402
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402


def _pump_until(condition, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


def _pump_for(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _running(thread):
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.01
    binning = 1


def _zero_field_window():
    return ZeroFieldWindow(
        hardware_manager=None,
        hardware={"magnet": make_magnet()},
        camera=make_camera(),
        acquisition_state_getter=lambda: _AcquisitionState(),
    )


class QueueDockTests(unittest.TestCase):
    def setUp(self):
        collapsible_section.disable_state_persistence()
        self.addCleanup(collapsible_section.disable_state_persistence)

    def _window(self):
        window = _zero_field_window()
        self.addCleanup(lambda: _dispose(window))
        window.show()
        _pump_for(0.05)
        return window

    def test_starts_open_and_compact(self):
        window = self._window()
        self.assertTrue(window.queue_section.is_expanded())
        minimum = window.queue_section.minimumSizeHint().height()
        self.assertEqual(window.queue_dock.height(),
                         max(QUEUE_DOCK_DEFAULT_HEIGHT, minimum))
        self.assertLessEqual(window.queue_dock.height(), 200)
        self.assertTrue(window.queue_widget.isVisible())

    def test_folding_shrinks_the_dock_to_its_header_and_back(self):
        window = self._window()
        open_height = window.queue_dock.height()
        plot_height = window.widget.plot.height()
        window.queue_section.set_expanded(False)
        _pump_for(0.05)
        self.assertFalse(window.queue_widget.isVisible())
        self.assertLess(window.queue_dock.height(), 90)
        self.assertGreater(window.widget.plot.height(), plot_height)
        window.queue_section.set_expanded(True)
        _pump_for(0.05)
        self.assertEqual(window.queue_dock.height(), open_height)

    def test_user_resize_is_kept_when_refolded(self):
        window = self._window()
        window.resizeDocks([window.queue_dock], [260], Qt.Orientation.Vertical)
        _pump_for(0.05)
        resized = window.queue_dock.height()
        self.assertGreater(resized, 200)
        window.queue_section.set_expanded(False)
        _pump_for(0.05)
        window.queue_section.set_expanded(True)
        _pump_for(0.05)
        self.assertEqual(window.queue_dock.height(), resized)

    def test_summary_idle_and_running(self):
        window = self._window()
        self.assertEqual(window.queue_section.summary(), "Queue: idle, 0 entries")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        window.output_directory = Path(directory.name)
        w = window.widget
        w.field_start_spin.setValue(-1.0)
        w.field_stop_spin.setValue(1.0)
        w.field_points_spin.setValue(3)
        w.settling_time_spin.setValue(0)
        w.post_flip_settling_spin.setValue(0)
        for _ in range(2):
            self.assertTrue(window.add_to_queue())
        # Plus the estimated size the pending entries will write.
        self.assertRegex(window.queue_section.summary(),
                         r"^Queue: idle, 2 entries, ≈ [\d.]+ [kMG]?B to write$")
        seen = []
        window.run_started.connect(
            lambda: seen.append(window.queue_section.summary()))
        with patch.object(QMessageBox, "critical"):
            self.assertTrue(window.run_queue())
            self.assertTrue(_pump_until(lambda: not window.queue_active(), 60))
        _pump_until(lambda: window.thread is None or not _running(window.thread), 10)
        self.assertEqual(seen, ["Queue: running 1 / 2", "Queue: running 2 / 2"])
        self.assertEqual(window.queue_section.summary(), "Queue: idle, 2 entries")

    def test_queue_controls_keep_tooltips_and_names(self):
        window = self._window()
        q = window.queue_widget
        for control in (q.add_button, q.remove_button, q.move_up_button,
                        q.move_down_button, q.duplicate_button, q.clear_button,
                        q.load_into_form_button, q.save_queue_button,
                        q.load_queue_button, q.pause_spin, q.on_failure_combo,
                        q.run_queue_button, q.stop_current_button,
                        q.stop_queue_button, q.table, q.queue_status_label,
                        window.queue_section.toggle_button):
            self.assertTrue(control.toolTip().strip(), control)

    def test_open_state_and_height_remembered_only_with_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings(os.path.join(tmp, "ui.ini"),
                                 QSettings.Format.IniFormat)
            collapsible_section.enable_state_persistence(settings)
            first = _zero_field_window()
            first.resize(1100, 960)
            first.show()
            _pump_for(0.05)
            first.resizeDocks([first.queue_dock], [240], Qt.Orientation.Vertical)
            _pump_for(0.6)  # past the save debounce
            height = first.queue_dock.height()
            self.assertEqual(height, 240)
            first.queue_section.set_expanded(False)
            _pump_for(0.05)
            _dispose(first)

            second = _zero_field_window()
            self.addCleanup(lambda: _dispose(second))
            second.resize(1100, 960)
            second.show()
            _pump_for(0.05)
            self.assertFalse(second.queue_section.is_expanded())
            second.queue_section.set_expanded(True)
            _pump_for(0.05)
            self.assertEqual(second.queue_dock.height(), height)

            # Without persistence (tests, scripts): always the defaults.
            collapsible_section.disable_state_persistence()
            third = self._window()
            self.assertTrue(third.queue_section.is_expanded())
            self.assertLessEqual(third.queue_dock.height(), 200)
            del settings



if __name__ == "__main__":
    unittest.main()
