"""Live count statistics in the main window (framework/count_statistics.py,
gui/widgets/count_statistics_panel.py).

Hardware-free: frames are synthetic arrays; the integration test runs the
real MainWindow with --sim hardware (config/setupInfo.json is only read).
"""

import csv
import math
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from framework.acquisition_state import AcquisitionState  # noqa: E402
from framework.camera_ownership import exclusive_camera_access  # noqa: E402
from framework.count_statistics import (  # noqa: E402
    LOG_COLUMNS,
    RunningStatistics,
    frame_sample,
)
from gui.widgets.count_statistics_panel import (  # noqa: E402
    PAUSED_TEXT,
    CountStatisticsPanel,
)

# Known means, including a repeat of an earlier value.
MEANS = [20000.0, 20100.5, 19950.25, 20200.0, 19800.0, 20100.5, 20050.0]


def _frame(mean, shape=(8, 10), seed=0):
    """A noisy frame whose exact mean is *mean*."""
    noise = np.random.default_rng(seed).normal(0.0, 50.0, size=shape)
    return noise - noise.mean() + mean


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeNow:
    def __init__(self):
        self.value = datetime(2026, 11, 24, 12, 0, 0)

    def __call__(self):
        self.value += timedelta(milliseconds=100)
        return self.value


class RunningStatisticsTests(unittest.TestCase):
    def test_matches_numpy(self):
        stats = RunningStatistics()
        for value in MEANS:
            stats.add(value)
        values = np.array(MEANS)
        self.assertEqual(stats.count, len(MEANS))
        self.assertAlmostEqual(stats.mean, values.mean(), places=9)
        self.assertAlmostEqual(stats.std, values.std(ddof=1), places=9)
        self.assertEqual(stats.minimum, values.min())
        self.assertEqual(stats.maximum, values.max())
        self.assertAlmostEqual(stats.ratio, values.max() / values.min(), places=12)
        self.assertAlmostEqual(
            stats.std_percent, 100 * values.std(ddof=1) / values.mean(), places=9
        )

    def test_empty_and_single_sample(self):
        stats = RunningStatistics()
        self.assertTrue(math.isnan(stats.mean))
        self.assertTrue(math.isnan(stats.ratio))
        stats.add(5.0)
        self.assertEqual((stats.mean, stats.minimum, stats.maximum), (5.0, 5.0, 5.0))
        self.assertTrue(math.isnan(stats.std))

    def test_frame_sample_is_the_frame_mean(self):
        frame = np.random.default_rng(1).integers(0, 65535, (64, 80), dtype=np.uint16)
        mean, _ = frame_sample(frame)
        self.assertAlmostEqual(mean, frame.astype(np.float64).mean(), places=9)

    def test_fingerprint_separates_frames_and_matches_copies(self):
        a, b = _frame(100.0, seed=1), _frame(100.0, seed=2)  # same mean
        self.assertEqual(frame_sample(a)[1], frame_sample(a.copy())[1])
        self.assertNotEqual(frame_sample(a)[1], frame_sample(b)[1])


class PanelTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_directory = Path(self._tmp.name)
        self.state = AcquisitionState(None, 0.01, 1)
        self.owned = False
        self.clock = FakeClock()
        self.panel = CountStatisticsPanel(
            settings_getter=lambda: self.state,
            camera_owned=lambda: self.owned,
            log_directory=self.log_directory,
            clock=self.clock,
            now=FakeNow(),
        )
        self.addCleanup(self._dispose)
        self.seed = 0

    def _dispose(self):
        self.panel.close_log()
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def feed(self, *means):
        for mean in means:
            self.seed += 1
            self.clock.t += 0.05
            self.panel.add_frame(_frame(mean, seed=self.seed))


class PanelStatisticsTests(PanelTestCase):
    def test_statistics_match_numpy_for_known_frames(self):
        self.panel.start()
        self.feed(*MEANS)
        s, values = self.panel.statistics, np.array(MEANS)
        self.assertEqual(s.count, len(MEANS))
        self.assertAlmostEqual(s.mean, values.mean(), places=6)
        self.assertAlmostEqual(s.std, values.std(ddof=1), places=6)
        self.assertAlmostEqual(s.minimum, values.min(), places=6)
        self.assertAlmostEqual(s.maximum, values.max(), places=6)
        self.assertAlmostEqual(s.ratio, values.max() / values.min(), places=9)
        self.panel.refresh()
        labels = self.panel.value_labels
        self.assertEqual(labels["count"].text(), str(len(MEANS)))
        self.assertEqual(labels["actual"].text(), f"{MEANS[-1]:.6g}")
        self.assertEqual(labels["mean"].text(), f"{values.mean():.7g}")
        self.assertIn("%", labels["std"].text())

    def test_repeated_frame_is_counted_once(self):
        self.panel.start()
        frame = _frame(20000.0, seed=7)
        for _ in range(5):  # the timer polled the same frame five times
            self.panel.add_frame(frame.copy())
        self.assertEqual(self.panel.statistics.count, 1)

    def test_no_accumulation_until_started_but_actual_updates(self):
        self.feed(20000.0)
        self.assertEqual(self.panel.statistics.count, 0)
        self.assertAlmostEqual(self.panel.latest_sample, 20000.0, places=6)
        self.panel.start()
        self.feed(20010.0)
        self.panel.stop()
        self.feed(20020.0)
        self.assertEqual(self.panel.statistics.count, 1)
        self.assertEqual(self.panel.start_stop_button.text(), "Start")

    def test_reset_clears_statistics(self):
        self.panel.start()
        self.feed(*MEANS)
        self.clock.t += 10
        self.panel.reset_button.click()
        s = self.panel.statistics
        self.assertEqual(s.count, 0)
        self.assertTrue(math.isnan(s.mean))
        self.assertEqual(self.panel.elapsed_s(), 0.0)
        self.assertTrue(self.panel.accumulating)  # still running
        self.panel.refresh()
        self.assertEqual(self.panel.value_labels["mean"].text(), "--")
        self.assertIn("Statistics reset", self.panel.note_label.text())
        self.feed(30000.0)
        self.assertEqual(s.count, 1)
        self.assertEqual(s.mean, 30000.0)

    def test_elapsed_counts_accumulating_time_only(self):
        self.panel.start()
        self.clock.t += 30
        self.panel.stop()
        self.clock.t += 100
        self.panel.start()
        self.clock.t += 5
        self.assertAlmostEqual(self.panel.elapsed_s(), 35.0)


class PanelPauseTests(PanelTestCase):
    def test_suspension_pauses_and_live_view_resumes_accumulation(self):
        self.panel.start()
        self.feed(20000.0, 20010.0)
        self.owned = True  # experiment holds the camera
        self.panel.refresh()
        self.assertEqual(self.panel.status_text(), PAUSED_TEXT)
        self.assertIn(PAUSED_TEXT, self.panel.status_label.text())
        self.feed(99999.0)  # a frame that slipped in is ignored
        self.assertEqual(self.panel.statistics.count, 2)
        self.owned = False  # live view resumed
        self.feed(20020.0)
        self.panel.refresh()
        self.assertEqual(self.panel.statistics.count, 3)
        self.assertEqual(self.panel.status_text(), "Accumulating")
        self.assertTrue(self.panel.accumulating)
        # The trend line is broken across the pause.
        values = [v for _, v in self.panel._trend]
        self.assertTrue(math.isnan(values[2]))

    def test_waiting_status_without_frames(self):
        self.panel.start()
        self.assertIn("waiting", self.panel.status_text())


class PanelSettingsResetTests(PanelTestCase):
    def _change(self, **fields):
        s = self.state
        values = dict(acquisition_roi=s.acquisition_roi, exposure_s=s.exposure_s,
                      binning=s.binning)
        values.update(fields)
        self.state = AcquisitionState(**values)

    def _assert_reset_by(self, change_text, **fields):
        self.panel.start()
        self.feed(20000.0, 20010.0)
        self._change(**fields)
        self.feed(11111.0)  # may predate the change: skipped
        self.feed(10000.0)
        self.assertEqual(self.panel.statistics.count, 1)
        self.assertEqual(self.panel.statistics.mean, 10000.0)
        self.assertIn("Statistics reset", self.panel.note_label.text())
        self.assertIn(change_text, self.panel.note_label.text())

    def test_exposure_change_resets(self):
        self._assert_reset_by("exposure 10 → 20 ms", exposure_s=0.02)

    def test_roi_change_resets(self):
        self._assert_reset_by("ROI changed", acquisition_roi=(0, 0, 64, 64))

    def test_binning_change_resets(self):
        self._assert_reset_by("binning 1 → 2", binning=2)

    def test_change_without_frames_resets_on_refresh(self):
        self.panel.start()
        self.feed(20000.0)
        self._change(exposure_s=0.05)
        self.panel.refresh()
        self.assertEqual(self.panel.statistics.count, 0)
        self.assertIn("exposure", self.panel.note_label.text())

    def test_baseline_change_does_not_reset(self):
        self.panel.start()
        self.feed(20000.0)
        self.state = AcquisitionState(None, 0.01, 1, baseline_counts=120.0)
        self.feed(20010.0)
        self.assertEqual(self.panel.statistics.count, 2)


class PanelLogTests(PanelTestCase):
    def _rows(self, path):
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.reader(handle))

    def test_csv_has_one_row_per_sample(self):
        self.panel.log_check.setChecked(True)
        self.panel.start()
        frame = _frame(19999.0, seed=99)
        self.panel.add_frame(frame)
        self.panel.add_frame(frame.copy())  # repeat: not a sample
        self.feed(*MEANS)
        path = self.panel.log.path
        self.assertRegex(path.name, r"^count_log_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.csv$")
        self.assertEqual(path.parent, self.log_directory)
        # Flushed per row: readable while the log is still open.
        rows = self._rows(path)
        self.assertEqual(rows[0], LOG_COLUMNS)
        self.assertEqual(len(rows) - 1, self.panel.statistics.count)
        self.assertEqual(len(rows) - 1, len(MEANS) + 1)
        values = [float(r[1]) for r in rows[1:]]
        np.testing.assert_allclose(values, [19999.0] + MEANS, rtol=0, atol=1e-6)
        self.assertEqual(rows[1][2:], ["10", "1", "full"])
        datetime.fromisoformat(rows[1][0])
        self.panel.stop()
        self.assertIsNone(self.panel.log)
        self.feed(1.0)  # not accumulating: not logged
        self.assertEqual(len(self._rows(path)) - 1, len(MEANS) + 1)

    def test_no_file_without_the_checkbox(self):
        self.panel.start()
        self.feed(*MEANS)
        self.assertIsNone(self.panel.log)
        self.assertEqual(list(self.log_directory.iterdir()), [])


class MainWindowIntegrationTests(unittest.TestCase):
    """The real MainWindow on --sim hardware: frames reach the panel only
    through update_image(), and an experiment's camera lease pauses it."""

    @classmethod
    def setUpClass(cls):
        from gui.main_window import MainWindow
        cls.main = MainWindow(sim=True)

    @classmethod
    def tearDownClass(cls):
        main = cls.main
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            main.close()
        main.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.005)

    def test_live_view_feeds_panel_and_experiment_pauses_it(self):
        main, panel = self.main, self.main.count_statistics_panel
        main.start_stream()
        self.addCleanup(main.stop_stream)
        panel.start()
        self.addCleanup(panel.stop)
        self._pump(0.6)
        count = panel.statistics.count
        self.assertGreater(count, 3)
        with exclusive_camera_access(main.camera):  # what an experiment holds
            self._pump(0.5)
            panel.refresh()
            self.assertIn(PAUSED_TEXT, panel.status_label.text())
            self.assertEqual(panel.statistics.count, count)
            self.assertFalse(main.timer.isActive())
        self._pump(0.6)
        self.assertGreater(panel.statistics.count, count)
        panel.refresh()
        self.assertEqual(panel.status_text(), "Accumulating")

    def test_exposure_spinbox_resets_the_statistics(self):
        main, panel = self.main, self.main.count_statistics_panel
        main.start_stream()
        self.addCleanup(main.stop_stream)
        panel.start()
        self.addCleanup(panel.stop)
        self._pump(0.4)
        self.assertGreater(panel.statistics.count, 0)
        main.exposure_spin.setValue(main.exposure_spin.value() + 5.0)
        panel.refresh()
        self.assertEqual(panel.statistics.count, 0)
        self.assertIn("exposure", panel.note_label.text())


if __name__ == "__main__":
    unittest.main()
