"""Hardware-free tests for the experiment-aware magnet control lock.

Covers MagnetControlWindow's lock/override/post-experiment behaviour,
MainWindow's experiment bookkeeping, and ZeroFieldWorker.field_updated.
No pyqtgraph windows are constructed here: test_zero_field_window_save_toggle.py
documents that constructing more than two ZeroFieldWindows per process
segfaults at shutdown in this environment, so the window-level run_started/
run_finished checks live in that file, on its shared instance.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv)

from experiments.odmr_experiment import ODMRExperiment
from experiments.zero_field_experiment import ZeroFieldExperiment
from gui.magnet_window import (
    MagnetControlWindow,
    POST_EXPERIMENT_ELECTRICAL_OFF,
    POST_EXPERIMENT_FIELD_ZERO,
)
from gui.main_window import MainWindow
from gui.zero_field_worker import ZeroFieldWorker
from tests.test_magnetic_field_gui_units import _make_magnet


_OK = QMessageBox.StandardButton.Ok
_CANCEL = QMessageBox.StandardButton.Cancel


class MagnetIsSweptConstantTests(unittest.TestCase):
    def test_constants(self):
        self.assertIs(ZeroFieldExperiment.MAGNET_IS_SWEPT, True)
        self.assertIs(ODMRExperiment.MAGNET_IS_SWEPT, False)


class MagnetWindowLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = MagnetControlWindow({"magnet": _make_magnet()})

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def setUp(self):
        window = self.window
        self.magnet = _make_magnet()
        window.hardware = {"magnet": self.magnet}
        window.magnet = self.magnet
        self.magnet.enable()
        # Reset lock state directly, so a previous test's experiment is left
        # without firing a post-experiment action.
        window._experiment_name = None
        window._magnet_is_swept = False
        window._override_active = False
        window._live_vector_mT = None
        window._set_override_checked(False)
        window.override_check.setVisible(False)
        window.lock_label.setText("")
        window.lock_label.setVisible(False)
        window.vector_box.setTitle(MagnetControlWindow._VECTOR_BOX_TITLE)
        window.post_experiment_combo.setCurrentText(POST_EXPERIMENT_FIELD_ZERO)
        window.refresh()

    def _command_controls_enabled(self):
        w = self.window
        return (w.power_button.isEnabled(), w.apply_button.isEnabled(), w.zero_button.isEnabled())

    def test_start_locks_commands_but_not_readouts(self):
        w = self.window
        self.assertEqual(self._command_controls_enabled(), (True, True, True))
        w.on_experiment_started("Zero Field", True)
        self.assertEqual(self._command_controls_enabled(), (False, False, False))
        for widget in (w.read_button, w.axis_combo, w.post_experiment_combo,
                       w.current_field_label, w.bx_label):
            self.assertTrue(widget.isEnabled())
        self.assertEqual(w.lock_label.text(), "Magnet locked — Zero Field running")
        self.assertFalse(w.override_check.isHidden())
        self.assertFalse(w.override_check.isChecked())

    def test_locked_handlers_send_no_commands(self):
        w = self.window
        w.on_experiment_started("Zero Field", True)
        with patch.object(self.magnet, "set_vector") as set_vector, \
             patch.object(self.magnet, "disable") as disable, \
             patch.object(self.magnet.x, "set_field") as set_field:
            w.apply_field()
            w.zero_field()
            w.toggle_power()
        set_vector.assert_not_called()
        disable.assert_not_called()
        set_field.assert_not_called()

    def test_finish_unlocks_and_resets(self):
        w = self.window
        w.on_experiment_started("ODMR", False)
        with patch.object(QMessageBox, "warning", return_value=_OK):
            w.override_check.setChecked(True)
        w.on_experiment_finished()
        self.assertEqual(self._command_controls_enabled(), (True, True, True))
        self.assertEqual(w.lock_label.text(), "")
        self.assertTrue(w.override_check.isHidden())
        self.assertFalse(w.override_check.isChecked())

    def test_override_ok_on_swept_experiment_unlocks_then_uncheck_relocks(self):
        w = self.window
        w.on_experiment_started("Zero Field", True)
        with patch.object(QMessageBox, "warning", return_value=_OK) as warning:
            w.override_check.setChecked(True)
        message = warning.call_args.args[2]
        self.assertIn("actively sweeping the magnet field", message)
        self.assertIn("corrupt your data", message)
        self.assertEqual(self._command_controls_enabled(), (True, True, True))
        w.override_check.setChecked(False)
        self.assertEqual(self._command_controls_enabled(), (False, False, False))

    def test_override_cancel_unchecks_and_stays_locked(self):
        w = self.window
        w.on_experiment_started("ODMR", False)
        with patch.object(QMessageBox, "warning", return_value=_CANCEL) as warning:
            w.override_check.setChecked(True)
        self.assertEqual(warning.call_args.args[2], "Changing the field will affect your measurement.")
        self.assertFalse(w.override_check.isChecked())
        self.assertEqual(self._command_controls_enabled(), (False, False, False))

    def test_override_ignored_when_idle(self):
        w = self.window
        with patch.object(QMessageBox, "warning") as warning:
            w.override_check.setChecked(True)
        warning.assert_not_called()
        self.assertFalse(w.override_check.isChecked())

    def test_post_action_field_zero(self):
        w = self.window
        w.on_experiment_started("ODMR", False)
        with patch.object(self.magnet, "set_vector") as set_vector, \
             patch.object(self.magnet, "disable") as disable:
            w.on_experiment_finished()
        set_vector.assert_called_once_with(bx=0.0, by=0.0, bz=0.0)
        disable.assert_not_called()

    def test_post_action_electrical_off(self):
        w = self.window
        w.post_experiment_combo.setCurrentText(POST_EXPERIMENT_ELECTRICAL_OFF)
        w.on_experiment_started("ODMR", False)
        with patch.object(self.magnet, "set_vector") as set_vector:
            w.on_experiment_finished()
        set_vector.assert_not_called()
        self.assertFalse(self.magnet.is_enabled())

    def test_post_action_fires_even_with_override_active(self):
        w = self.window
        w.on_experiment_started("ODMR", False)
        with patch.object(QMessageBox, "warning", return_value=_OK):
            w.override_check.setChecked(True)
        with patch.object(self.magnet, "set_vector") as set_vector:
            w.on_experiment_finished()
        set_vector.assert_called_once_with(bx=0.0, by=0.0, bz=0.0)

    def test_post_action_skipped_when_magnet_disabled(self):
        w = self.window
        w.on_experiment_started("Zero Field", True)
        self.magnet.disable()  # what ZeroFieldExperiment.cleanup_scan() does
        with patch.object(self.magnet, "set_vector") as set_vector, \
             patch.object(self.magnet, "disable") as disable:
            w.on_experiment_finished()
        set_vector.assert_not_called()
        disable.assert_not_called()
        self.assertEqual(self._command_controls_enabled(), (True, True, False))

    def test_post_action_selector_always_enabled(self):
        w = self.window
        self.assertTrue(w.post_experiment_combo.isEnabled())
        w.on_experiment_started("Zero Field", True)
        self.assertTrue(w.post_experiment_combo.isEnabled())

    def test_live_field_display_only_during_experiment(self):
        w = self.window
        w.update_field_display(0.5, 0.0, 0.0)  # idle: ignored
        self.assertNotEqual(w.bx_label.text(), "5.000 G")

        w.on_experiment_started("Zero Field", True)
        w.update_field_display(0.5, -0.1, 0.2)
        self.assertEqual(
            (w.bx_label.text(), w.by_label.text(), w.bz_label.text()),
            ("5.000 G", "-1.000 G", "2.000 G"),
        )
        w.refresh()  # refresh keeps the live vector while running
        self.assertEqual(w.bx_label.text(), "5.000 G")

        with patch.object(self.magnet, "set_vector"):
            w.on_experiment_finished()
        self.assertIsNone(w._live_vector_mT)
        self.assertEqual(w.vector_box.title(), MagnetControlWindow._VECTOR_BOX_TITLE)


class _BookkeepingMainWindow:
    """MainWindow's bookkeeping methods on a bare QMainWindow.

    MainWindow.__init__ initializes hardware, so only QMainWindow.__init__
    runs here; the signals and methods under test need nothing else.
    """

    @staticmethod
    def make():
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window._running_experiments = {}
        window.magnet_window = None
        return window


class MainWindowExperimentBookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.main = _BookkeepingMainWindow.make()
        self.started = []
        self.finished = []
        self.main.experiment_started.connect(lambda n, s: self.started.append((n, s)))
        self.main.experiment_finished.connect(lambda: self.finished.append(True))

    def tearDown(self):
        self.main.deleteLater()

    def test_single_experiment(self):
        zfe = object()
        self.main._experiment_window_started(zfe, "Zero Field", True)
        self.main._experiment_window_finished(zfe)
        self.assertEqual(self.started, [("Zero Field", True)])
        self.assertEqual(self.finished, [True])

    def test_overlapping_experiments_stay_locked_until_all_finish(self):
        odmr, zfe = object(), object()
        self.main._experiment_window_started(odmr, "ODMR", False)
        self.main._experiment_window_started(zfe, "Zero Field", True)
        self.main._experiment_window_finished(odmr)
        self.assertEqual(self.finished, [])
        self.main._experiment_window_finished(zfe)
        self.assertEqual(self.started, [
            ("ODMR", False), ("ODMR + Zero Field", True), ("Zero Field", True),
        ])
        self.assertEqual(self.finished, [True])

    def test_unknown_finish_is_ignored(self):
        self.main._experiment_window_finished(object())
        self.assertEqual(self.finished, [])

    def test_field_relay_reaches_magnet_window(self):
        received = []
        self.main.magnet_window = SimpleNamespace(
            update_field_display=lambda *v: received.append(v)
        )
        self.main._relay_zero_field_vector(0.1, 0.2, 0.3)
        self.assertEqual(received, [(0.1, 0.2, 0.3)])
        self.main.magnet_window = None
        self.main._relay_zero_field_vector(0.1, 0.2, 0.3)  # no window: no error


class ZeroFieldWorkerFieldUpdatedTests(unittest.TestCase):
    def test_emits_full_commanded_vector(self):
        worker = ZeroFieldWorker(None, None, None, {"field_points": 3}, None, {}, None)
        worker.experiment = SimpleNamespace(
            _configured_field_mT={"x": 0.0, "y": 0.01, "z": -0.02},
            field_axis="Z",
        )
        vectors = []
        worker.field_updated.connect(lambda x, y, z: vectors.append((x, y, z)))
        worker._on_live_update(1.5, np.ones((2, 2)))
        self.assertEqual(len(vectors), 1)
        x, y, z = vectors[0]
        self.assertEqual((x, y), (0.0, 0.01))
        self.assertAlmostEqual(z, 0.15)  # 1.5 G swept on Z -> 0.15 mT

    def test_no_emit_before_scan_setup(self):
        worker = ZeroFieldWorker(None, None, None, {"field_points": 3}, None, {}, None)
        worker.experiment = SimpleNamespace(_configured_field_mT=None, field_axis="X")
        vectors = []
        worker.field_updated.connect(lambda *v: vectors.append(v))
        worker._on_live_update(1.0, np.ones((2, 2)))
        self.assertEqual(vectors, [])


if __name__ == "__main__":
    unittest.main()
