"""GUI-level tests for the Zero Field "Save data" checkbox.

Deliberately narrow: the worker/experiment-level behavior of a None
output_path (no streaming, no final save, save_raw_scans forced off) is
already covered end-to-end by ZeroFieldSavingDisabledTests in
test_zero_field_averaging.py. This file only exercises what genuinely
requires a live ZeroFieldWindow: the one-time warning dialog, the
pre-scan gate being skipped, and save_data()'s copy-vs-serialize dispatch.

This is the first automated test in the repo to construct a full
ZeroFieldWindow/QMainWindow with a live QApplication -- there is no
existing precedent for it here (test_magnet_gui.py is a manual hardware
smoke script, not a pytest case). Two environment quirks were found
empirically while writing it, both worth keeping documented for whoever
touches this file next:

1. Import order: the QApplication instance below is constructed at module
   import time, BEFORE gui.zero_field_window (and transitively pyqtgraph,
   which gui/widgets/zero_field_widget.py depends on) is imported.
   Constructing QApplication lazily, after gui.zero_field_window is
   already imported, reproducibly hung ZeroFieldWindow() construction
   indefinitely in this sandbox (confirmed with a minimal reproduction
   outside pytest too, so it wasn't a pytest/unittest-runner artifact).
   This ordering is a standard requirement for PyQt widgets that query
   display/GL state at import time, not a workaround for a bug in this
   repo's code.
2. Process-lifetime instability: constructing and closing more than two
   ZeroFieldWindow instances in one process reliably segfaults at
   interpreter shutdown in this sandbox (Python 3.14 / PyQt6 / pyqtgraph),
   even though every individual test's assertions pass before the crash --
   confirmed by bisection (2 windows: clean exit; 3: segfault). All tests
   below therefore share ONE ZeroFieldWindow instance for the whole file
   (setUpClass/tearDownClass), reset to a clean baseline before each test,
   rather than one window per test. This is reported as a real,
   environment-specific fragility per instruction, not silently patched
   around by e.g. retries -- if more GUI tests are added here later and
   the shared-instance pattern stops being enough, that should surface as
   a segfault during a full-suite run and should be reported the same way.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv)

from framework.image_cube import ImageCube
from gui.zero_field_window import ZeroFieldWindow


class FakeMagnet:
    def enable(self):
        pass

    def disable(self):
        pass

    def get_vector(self):
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    def set_vector(self, **_fields):
        pass


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.01
    binning = 1


class ZeroFieldWindowSaveToggleTests(unittest.TestCase):
    """Shares a single ZeroFieldWindow across every test in this class --
    see the module docstring's point 2 for why."""

    @classmethod
    def setUpClass(cls):
        cls.window = ZeroFieldWindow(
            hardware_manager=None,
            hardware={"magnet": FakeMagnet()},
            camera=None,
            acquisition_state_getter=lambda: _AcquisitionState(),
        )
        cls._default_output_directory = cls.window.output_directory

    @classmethod
    def tearDownClass(cls):
        cls.window.zero_field_running = False
        cls.window.close()

    def setUp(self):
        window = self.window
        window.output_path = None
        window.image_cube = None
        window.zero_field_running = False
        window.thread = None
        window._scan_timestamp = None
        window._save_off_warning_shown = False
        window.output_directory = self._default_output_directory
        window.widget.save_data_check.setChecked(True)

    def test_unchecking_save_data_shows_warning_once(self):
        window = self.window
        with patch.object(QMessageBox, "warning") as warning:
            window.widget.save_data_check.setChecked(False)
            self.assertEqual(warning.call_count, 1)

            window.widget.save_data_check.setChecked(True)
            window.widget.save_data_check.setChecked(False)
            self.assertEqual(warning.call_count, 1)

    def test_checking_save_data_shows_no_warning(self):
        window = self.window
        with patch.object(QMessageBox, "warning") as warning:
            window.widget.save_data_check.setChecked(True)
            self.assertEqual(warning.call_count, 0)

    def test_start_scan_skips_ensure_writable_directory_when_save_data_unchecked(self):
        window = self.window
        # A directory that cannot possibly be writable -- proves the gate
        # really was skipped, not just coincidentally satisfied.
        window.output_directory = Path("Z:/definitely/does/not/exist")
        window.widget.save_data_check.setChecked(False)

        # ZeroFieldWorker and QThread are both mocked out so start_scan()
        # never spins up a real background thread -- this test is only
        # about the synchronous gate-skip logic that runs before any
        # thread would start, not the worker/thread machinery itself
        # (which is exercised, hardware-free, by ZeroFieldSavingDisabledTests
        # in test_zero_field_averaging.py).
        with patch(
            "gui.zero_field_window.ensure_writable_directory"
        ) as gate, patch.object(QMessageBox, "critical") as critical, patch(
            "gui.zero_field_window.ZeroFieldWorker"
        ), patch("gui.zero_field_window.QThread"):
            window.start_scan()
            gate.assert_not_called()
            critical.assert_not_called()
            self.assertIsNone(window.output_path)

    def test_start_scan_runs_gate_when_save_data_checked(self):
        window = self.window
        window.output_directory = Path("Z:/definitely/does/not/exist")
        window.widget.save_data_check.setChecked(True)

        with patch(
            "gui.zero_field_window.ensure_writable_directory",
            side_effect=OSError("not writable"),
        ) as gate, patch.object(QMessageBox, "critical") as critical:
            window.start_scan()
            gate.assert_called_once()
            critical.assert_called_once()
            # Bailed out before a worker/thread was ever created.
            self.assertIsNone(window.thread)

    def test_save_button_copies_file_when_saving_was_on(self):
        window = self.window
        with tempfile.TemporaryDirectory() as source_dir, \
             tempfile.TemporaryDirectory() as dest_dir:
            cube = ImageCube(data=np.zeros((2, 2, 2)))
            output_path = Path(source_dir) / "zero_field_test.h5"
            cube.save(output_path)

            window.image_cube = cube
            window.output_path = output_path

            with patch(
                "gui.zero_field_window.QFileDialog.getExistingDirectory",
                return_value=dest_dir,
            ):
                window.save_data()

            copied = Path(dest_dir) / output_path.name
            self.assertTrue(copied.exists())
            np.testing.assert_allclose(ImageCube.load(copied).data, cube.data)

    def test_save_button_serializes_cube_when_saving_was_off(self):
        window = self.window
        with tempfile.TemporaryDirectory() as dest_dir:
            cube = ImageCube(data=np.full((2, 3, 3), 7.0))
            window.image_cube = cube
            window.output_path = None
            window._scan_timestamp = "2026-01-01_00-00-00"
            window.total_scans = 1

            with patch(
                "gui.zero_field_window.QFileDialog.getExistingDirectory",
                return_value=dest_dir,
            ):
                window.save_data()

            written = list(Path(dest_dir).glob("*.h5"))
            self.assertEqual(len(written), 1)
            loaded = ImageCube.load(written[0])
            np.testing.assert_allclose(loaded.data, cube.data)

    def test_save_button_reports_nothing_to_save(self):
        window = self.window
        with patch.object(QMessageBox, "critical") as critical:
            window.save_data()
            critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
