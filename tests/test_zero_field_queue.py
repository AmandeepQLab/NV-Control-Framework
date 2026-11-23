"""Tests for the Zero Field run queue (gui/zero_field_queue.py and the queue
section of gui/zero_field_window.py).

Queued runs execute end to end through the real ZeroFieldWindow ->
ZeroFieldWorker -> ZeroFieldExperiment path, on SimCamera and a real
Magnet stack over simulated power supplies and pulse streamer (the same
factories as test_zero_field_timing.py). No real hardware is touched.
"""

import csv
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# QApplication must exist before pyqtgraph (via the widget) is imported;
# see tests/test_zero_field_window_save_toggle.py.
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import gui.zero_field_worker as zero_field_worker_module  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from gui import zero_field_queue as queue_model  # noqa: E402
from gui.widgets.zero_field_widget import ZeroFieldWidget  # noqa: E402
from gui.zero_field_window import ZeroFieldWindow  # noqa: E402
from tests.test_main_window_close_safety import _make_main_window  # noqa: E402
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _pump_until(condition, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.01
    binning = 1


class QueueWindowTestCase(unittest.TestCase):
    """A fresh window on sim hardware per test; output in a temp directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.magnet = make_magnet()  # every axis reaches about +-250 G
        self.window = ZeroFieldWindow(
            hardware_manager=None,
            hardware={"magnet": self.magnet},
            camera=make_camera(),
            acquisition_state_getter=lambda: _AcquisitionState(),
        )
        self.addCleanup(self._close_window)
        self.window.output_directory = self.directory
        self.window.widget.set_output_directory(self.directory)
        # Not a dialog test: any dialog during a queue would block it.
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)

    def _close_window(self):
        window = self.window
        if window.queue_active():
            window.stop_queue()
        _pump_until(lambda: window.thread is None or not _thread_running(window), 10)
        window.zero_field_running = False
        _dispose(window)

    def set_form(self, **values):
        """Fill the form; fast defaults (no settling) unless overridden."""
        w = self.window.widget
        values = {"settling": 0, "post_flip": 0, **values}
        w.field_axis_combo.setCurrentText(values.get("axis", "X"))
        w.field_start_spin.setValue(values.get("start", -1.0))
        w.field_stop_spin.setValue(values.get("stop", 1.0))
        w.field_points_spin.setValue(values.get("points", 3))
        w.averages_spin.setValue(values.get("averages", 1))
        w.settling_time_spin.setValue(values["settling"])
        w.post_flip_settling_spin.setValue(values["post_flip"])
        w.averaging_enabled_check.setChecked(values.get("averaging", False))
        w.num_scans_spin.setValue(values.get("scans", 1))
        w.alternate_direction_check.setChecked(values.get("alternate", False))
        w.reference_enabled_check.setChecked(values.get("reference", False))
        w.reference_field_spin.setValue(values.get("reference_field", -8.0))

    def add(self, **values):
        self.set_form(**values)
        self.assertTrue(self.window.add_to_queue())
        return self.window.queue_entries[-1]

    def run_queue_to_end(self, timeout_s=60.0):
        self.assertTrue(self.window.run_queue())
        self.assertTrue(
            _pump_until(lambda: not self.window.queue_active(), timeout_s),
            "queue did not finish",
        )
        # Let the last worker thread exit before the test inspects files.
        _pump_until(lambda: not _thread_running(self.window), 10)

    def statuses(self):
        return [entry.status for entry in self.window.queue_entries]

    def log_rows(self):
        logs = sorted(self.directory.glob("zero_field_queue_*.csv"))
        self.assertEqual(len(logs), 1, logs)
        with open(logs[0], newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))


def _thread_running(window):
    try:
        return window.thread is not None and window.thread.isRunning()
    except RuntimeError:  # QThread already deleted (deleteLater)
        return False


class QueueExecutionTests(QueueWindowTestCase):
    def test_three_entries_run_in_order_with_their_own_settings(self):
        first = self.add(axis="X", start=-1.0, stop=1.0, points=3)
        second = self.add(axis="Y", start=0.0, stop=2.0, points=4, averages=2)
        third = self.add(
            axis="Z", start=-2.0, stop=0.0, points=5, averaging=True, scans=2,
            alternate=True, reference=True, reference_field=-8.0,
        )
        self.run_queue_to_end()

        entries = (first, second, third)
        self.assertEqual(self.statuses(), ["done"] * 3)
        self.assertEqual(len(self.critical.call_args_list), 0)
        # In order, and each in its own file.
        starts = [entry.start_time for entry in entries]
        self.assertEqual(starts, sorted(starts))
        for earlier, later in zip(entries, entries[1:]):
            self.assertLessEqual(earlier.end_time, later.start_time)
        paths = [Path(entry.output_path) for entry in entries]
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(paths[0].name.startswith("zero_field_"))
        self.assertTrue(paths[2].name.endswith("_average.h5"))  # 2 scans

        for entry, path in zip(entries, paths):
            self.assertTrue(path.exists(), path)
            cube = ImageCube.load(path)
            self.assertEqual(cube.metadata["scan_parameters"], entry.config)
            self.assertEqual(cube.data.shape[0], entry.config["field_points"])
        third_cube = ImageCube.load(paths[2])
        self.assertTrue(third_cube.metadata["reference_enabled"])
        self.assertTrue(third_cube.metadata["alternate_direction"])
        self.assertIn("ratio", third_cube.extra_datasets)

        self.assertEqual(
            self.window.queue_widget.queue_status_label.text(),
            "Queue: finished — 3 done, 0 stopped, 0 failed",
        )
        self.assertTrue(self.window.widget.run_stop_button.isEnabled())

    def test_queue_log_has_one_row_per_finished_entry(self):
        entries = [self.add(points=3), self.add(points=4), self.add(points=2)]
        self.run_queue_to_end()

        rows = self.log_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(list(rows[0]), list(queue_model.LOG_COLUMNS))
        for number, (row, entry) in enumerate(zip(rows, entries), start=1):
            self.assertEqual(row["index"], str(number))
            self.assertEqual(row["status"], "done")
            self.assertEqual(row["output_file"], entry.output_path)
            self.assertEqual(row["summary"], entry.summary())
            self.assertGreater(float(row["first_frame_mean_counts"]), 0.0)
            self.assertGreaterEqual(float(row["duration_s"]), 0.0)
            self.assertTrue(row["start_time"] and row["end_time"])

    def test_log_row_is_written_as_each_run_finishes(self):
        self.add(points=3)
        self.add(points=3)
        rows_when_second_started = []
        original = self.window._start_run

        def recording_start(config, directory, interactive=True):
            if not interactive and self.window.queue_entries[0].status == "done":
                rows_when_second_started.append(len(self.log_rows()))
            return original(config, directory, interactive)

        with patch.object(self.window, "_start_run", side_effect=recording_start):
            self.run_queue_to_end()
        self.assertEqual(rows_when_second_started, [1])
        self.assertEqual(len(self.log_rows()), 2)

    def test_run_button_disabled_while_queue_runs(self):
        self.add(points=3)
        self.add(points=3)
        self.window.run_queue()
        self.assertFalse(self.window.widget.run_stop_button.isEnabled())
        self.assertFalse(self.window.queue_widget.run_queue_button.isEnabled())
        self.assertEqual(
            self.window.queue_widget.queue_status_label.text(), "Queue: run 1 of 2"
        )
        self.assertTrue(_pump_until(lambda: not self.window.queue_active()))
        self.assertTrue(self.window.widget.run_stop_button.isEnabled())

    def test_single_run_button_does_not_touch_the_queue(self):
        self.add(points=3)
        self.set_form(points=3)
        self.window.start_scan()
        self.assertFalse(self.window.queue_widget.run_queue_button.isEnabled())
        self.assertTrue(_pump_until(lambda: not self.window.zero_field_running))
        self.assertEqual(self.statuses(), ["pending"])
        self.assertEqual(list(self.directory.glob("zero_field_queue_*.csv")), [])
        self.assertEqual(len(list(self.directory.glob("zero_field_*.h5"))), 1)


class StopTests(QueueWindowTestCase):
    def _slow_entries(self, count):
        # 40 points x 20 ms settling: long enough to stop mid-run.
        return [self.add(points=40, settling=20) for _ in range(count)]

    def _wait_for_first_frame(self, entry):
        self.assertTrue(_pump_until(lambda: entry.first_frame_mean is not None))

    def test_stop_current_run_moves_on_to_the_next_entry(self):
        first, second = self._slow_entries(2)
        self.window.run_queue()
        self._wait_for_first_frame(first)
        self.window.stop_current_run()
        self.assertTrue(_pump_until(lambda: second.status == "running"))
        self.assertEqual(first.status, "stopped")
        self.assertEqual(first.message, "stopped by user")
        self.assertTrue(_pump_until(lambda: not self.window.queue_active(), 60))
        self.assertEqual(self.statuses(), ["stopped", "done"])
        stopped_cube = ImageCube.load(first.output_path)
        self.assertLess(stopped_cube.data.shape[0], 40)
        self.assertEqual([r["status"] for r in self.log_rows()], ["stopped", "done"])

    def test_stop_queue_ends_it(self):
        entries = self._slow_entries(3)
        finished = []
        self.window.run_finished.connect(lambda: finished.append(True))
        self.window.run_queue()
        self._wait_for_first_frame(entries[0])
        self.window.stop_queue()
        self.assertTrue(_pump_until(lambda: not self.window.queue_active()))
        _pump_until(lambda: not _thread_running(self.window), 10)
        self.assertEqual(self.statuses(), ["stopped", "pending", "pending"])
        self.assertEqual(entries[0].message, "queue stopped")
        self.assertEqual(finished, [True])
        self.assertEqual([r["status"] for r in self.log_rows()], ["stopped"])
        self.assertIn("(queue stopped)", self.window.queue_widget.queue_status_label.text())

    def test_stop_queue_during_pause_ends_it_without_a_new_run(self):
        first, second = self.add(points=3), self.add(points=3)
        self.window.queue_widget.pause_spin.setValue(30.0)
        self.window.run_queue()
        self.assertTrue(_pump_until(lambda: self.window._queue_timer.isActive()))
        self.assertEqual(first.status, "done")
        self.window.stop_queue()
        self.assertFalse(self.window.queue_active())
        self.assertEqual(second.status, "pending")


class FailureTests(QueueWindowTestCase):
    """Entry 2 (7 points) raises inside the experiment's run()."""

    def setUp(self):
        super().setUp()
        experiment_class = zero_field_worker_module.ZeroFieldExperiment
        original_run = experiment_class.run

        def failing_run(experiment):
            if experiment.field_points == 7:
                raise RuntimeError("simulated acquisition failure")
            return original_run(experiment)

        run_patch = patch.object(experiment_class, "run", failing_run)
        run_patch.start()
        self.addCleanup(run_patch.stop)
        self.add(points=3)
        self.add(points=7)
        self.add(points=3)

    def test_failure_stops_the_queue_by_default(self):
        self.assertEqual(
            self.window.queue_widget.on_failure_combo.currentText(), "Stop queue"
        )
        self.run_queue_to_end()
        self.assertEqual(self.statuses(), ["done", "failed", "pending"])
        self.assertEqual(
            self.window.queue_entries[1].message, "simulated acquisition failure"
        )
        self.critical.assert_not_called()  # unattended: no modal dialog
        rows = self.log_rows()
        self.assertEqual([r["status"] for r in rows], ["done", "failed"])
        self.assertEqual(rows[1]["message"], "simulated acquisition failure")

    def test_skip_to_next_continues_after_a_failure(self):
        self.window.queue_widget.on_failure_combo.setCurrentText("Skip to next")
        self.run_queue_to_end()
        self.assertEqual(self.statuses(), ["done", "failed", "done"])
        self.critical.assert_not_called()
        self.assertEqual(
            [r["status"] for r in self.log_rows()], ["done", "failed", "done"]
        )


class ValidationTests(QueueWindowTestCase):
    def test_out_of_range_entry_is_rejected_when_added(self):
        self.set_form(start=0.0, stop=1000.0)  # X reaches about 250 G
        self.assertFalse(self.window.add_to_queue())
        self.assertEqual(self.window.queue_entries, [])
        self.critical.assert_called_once()
        self.assertEqual(self.critical.call_args.args[1], "Field range out of bounds")

    def test_unreachable_reference_is_rejected_when_added(self):
        self.set_form(reference=True, reference_field=-900.0)
        self.assertFalse(self.window.add_to_queue())
        self.assertEqual(self.window.queue_entries, [])

    def test_range_is_checked_again_immediately_before_start(self):
        entry = self.add(start=0.0, stop=100.0)
        self.add(points=3)
        # The coil's limit drops after the entry was queued.
        self.magnet.x.max_current = 1.0  # about 50 G now
        self.run_queue_to_end()
        self.assertEqual(self.statuses(), ["failed", "pending"])
        self.assertIn("exceeds maximum current", entry.message)
        self.assertEqual(entry.output_path, "")
        self.critical.assert_not_called()
        self.assertEqual([r["status"] for r in self.log_rows()], ["failed"])


class EditingTests(QueueWindowTestCase):
    def test_editing_the_form_after_add_does_not_change_the_entry(self):
        entry = self.add(axis="Y", start=-3.0, stop=3.0, points=11, averages=4)
        snapshot = dict(entry.config)
        self.assertEqual(entry.output_directory, str(self.directory))
        self.set_form(axis="Z", start=5.0, stop=6.0, points=2, averages=1,
                      reference=True, averaging=True, scans=3)
        self.window.widget.save_data_check.blockSignals(True)
        self.window.widget.save_data_check.setChecked(False)
        self.window.widget.save_data_check.blockSignals(False)
        self.window.output_directory = Path(self.directory, "elsewhere")
        self.assertEqual(entry.config, snapshot)
        self.assertEqual(entry.output_directory, str(self.directory))
        self.assertTrue(entry.config["save_data"])

    def test_load_entry_into_form_restores_its_settings(self):
        entry = self.add(axis="Z", start=-4.0, stop=2.5, points=9, averages=3,
                         averaging=True, scans=4, alternate=True,
                         reference=True, reference_field=-6.5)
        self.set_form()  # different settings in the form
        self.window.output_directory = Path("C:/somewhere/else")
        self.window.queue_widget.select_row(0)
        self.window.load_entry_into_form()
        self.assertEqual(self.window.widget.get_config(), entry.config)
        self.assertEqual(self.window.output_directory, self.directory)

    def test_remove_move_duplicate_clear(self):
        a, b, c = self.add(points=2), self.add(points=3), self.add(points=4)
        q, w = self.window.queue_widget, self.window
        q.select_row(2)
        w.move_queue_entry(-1)
        self.assertEqual(w.queue_entries, [a, c, b])
        self.assertEqual(q.selected_row(), 1)
        w.move_queue_entry(+1)
        self.assertEqual(w.queue_entries, [a, b, c])
        q.select_row(0)
        w.duplicate_queue_entry()
        self.assertEqual(len(w.queue_entries), 4)
        copy = w.queue_entries[1]
        self.assertIsNot(copy, a)
        self.assertEqual(copy.config, a.config)
        self.assertIsNot(copy.config, a.config)
        q.select_row(1)
        w.remove_queue_entry()
        self.assertEqual(w.queue_entries, [a, b, c])
        a.status = "done"
        w.clear_pending_entries()
        self.assertEqual(w.queue_entries, [a])

    def test_save_then_load_reproduces_the_entries(self):
        self.add(axis="X", start=-1.5, stop=1.5, points=7, averages=2)
        self.add(axis="Z", start=-2.0, stop=0.0, points=5, averaging=True,
                 scans=2, alternate=True, reference=True, reference_field=-7.25)
        self.window.widget.save_data_check.blockSignals(True)
        self.window.widget.save_data_check.setChecked(False)
        self.window.widget.save_data_check.blockSignals(False)
        self.add(axis="Y", points=3)
        self.window.queue_widget.pause_spin.setValue(12.5)
        self.window.queue_widget.on_failure_combo.setCurrentText("Skip to next")
        saved = [(e.config, e.output_directory) for e in self.window.queue_entries]
        path = self.directory / "tonight.json"
        self.assertTrue(self.window.save_queue_to(path))

        self.window.queue_entries = []
        self.window.queue_widget.pause_spin.setValue(0.0)
        self.window.queue_widget.on_failure_combo.setCurrentText("Stop queue")
        self.assertTrue(self.window.load_queue_from(path))
        loaded = [(e.config, e.output_directory) for e in self.window.queue_entries]
        self.assertEqual(loaded, saved)
        self.assertEqual(self.statuses(), ["pending"] * 3)
        self.assertEqual(self.window.queue_widget.pause_spin.value(), 12.5)
        self.assertEqual(
            self.window.queue_widget.on_failure_combo.currentText(), "Skip to next"
        )

    def test_load_refuses_invalid_or_unreachable_files(self):
        self.add(points=3)
        path = self.directory / "queue.json"
        self.window.save_queue_to(path)
        text = path.read_text(encoding="utf-8")
        before = list(self.window.queue_entries)

        path.write_text(text.replace('"field_stop": 1.0', '"field_stop": 1000.0'),
                        encoding="utf-8")
        self.assertFalse(self.window.load_queue_from(path))
        path.write_text(text.replace('"averages": 1,', ""), encoding="utf-8")
        self.assertFalse(self.window.load_queue_from(path))
        self.assertEqual(self.window.queue_entries, before)
        self.assertEqual(self.critical.call_count, 2)


class MagnetLockTests(QueueWindowTestCase):
    """The window wired to MainWindow's bookkeeping as open_zero_field_window()
    wires it, with MainWindow built without hardware."""

    def setUp(self):
        super().setUp()
        self.main = _make_main_window()
        self.addCleanup(self._dispose_main)
        window = self.window
        window.run_started.connect(
            lambda: self.main._experiment_window_started(window, "Zero Field", True)
        )
        window.run_finished.connect(
            lambda: self.main._experiment_window_finished(window)
        )
        self.events = []
        self.main.experiment_started.connect(lambda *_: self.events.append("locked"))
        self.main.experiment_finished.connect(lambda: self.events.append("unlocked"))

    def _dispose_main(self):
        # Closes without the Exit dialog (and without a second shutdown).
        self.main._shut_down = True
        _dispose(self.main)

    def test_magnet_stays_locked_between_queued_runs(self):
        for _ in range(3):
            self.add(points=3)
        locked_at_each_start = []
        original = self.window._start_run

        def recording_start(config, directory, interactive=True):
            locked_at_each_start.append(bool(self.main._running_experiments))
            return original(config, directory, interactive)

        with patch.object(self.window, "_start_run", side_effect=recording_start):
            self.run_queue_to_end()
        self.assertEqual(self.statuses(), ["done"] * 3)
        # Runs 2 and 3 start with the lock still held from run 1.
        self.assertEqual(locked_at_each_start, [False, True, True])
        self.assertEqual(self.events, ["locked"] * 3 + ["unlocked"])
        self.assertEqual(self.main._running_experiments, {})

    def test_main_window_close_between_runs_stops_the_whole_queue(self):
        first, second = self.add(points=3), self.add(points=3)
        self.window.queue_widget.pause_spin.setValue(30.0)
        self.window.run_queue()
        self.assertTrue(_pump_until(lambda: self.window._queue_timer.isActive()))
        _pump_until(lambda: not self.main._worker_threads, 10)
        self.assertEqual(self.main._worker_threads, {})  # no thread in the pause
        with patch.object(self.main, "_ask_stop_and_close", return_value=True) as ask, \
             patch.object(QMessageBox, "question") as question:
            self.assertTrue(self.main.close())
        ask.assert_called_once()  # the close-safety dialog, not plain Exit
        question.assert_not_called()
        self.assertFalse(self.window.queue_active())
        self.assertEqual((first.status, second.status), ("done", "pending"))
        self.assertEqual(self.events[-1], "unlocked")
        self.main.hw_manager.shutdown.assert_called_once()

    def test_main_window_close_during_a_run_stops_the_whole_queue(self):
        first = self.add(points=40, settling=20)
        second = self.add(points=3)
        self.window.run_queue()
        self.assertTrue(_pump_until(lambda: first.first_frame_mean is not None))
        with patch.object(self.main, "_ask_stop_and_close", return_value=True):
            self.assertFalse(self.main.close())  # deferred until the worker exits
            self.assertTrue(
                _pump_until(lambda: self.main.hw_manager.shutdown.called)
            )
        self.assertEqual((first.status, second.status), ("stopped", "pending"))
        self.assertFalse(self.window.queue_active())


class ControlPanelLayoutTests(QueueWindowTestCase):
    """Scrollable control panel and the fixed run/stop button."""

    def test_run_button_is_fixed_and_every_control_group_scrolls(self):
        w = self.window.widget
        scroll = w.control_scroll_area
        self.assertIs(scroll.widget(), w.controls_group)
        self.assertFalse(scroll.isAncestorOf(w.run_stop_button))
        self.assertTrue(w.left_panel.isAncestorOf(w.run_stop_button))
        # Directly above the scroll area in the left column.
        left = w.left_panel.layout()
        self.assertIs(left.itemAt(0).widget(), w.run_stop_button)
        self.assertIs(left.itemAt(1).widget(), scroll)
        # Every control group, in its original order, inside the scroll area.
        groups = [
            left_item.widget().title()
            for left_item in (
                w.controls_group.layout().itemAt(i)
                for i in range(w.controls_group.layout().count())
            )
            if left_item.widget() is not None and hasattr(left_item.widget(), "title")
        ]
        self.assertEqual(
            groups,
            ["Output Directory", "Magnetic Field", "Multi-Scan Averaging",
             "Camera Acquisition"],
        )
        for control in (
            w.output_directory_edit, w.browse_output_button, w.save_data_check,
            w.field_start_spin, w.field_axis_combo, w.post_flip_settling_spin,
            w.averaging_enabled_check, w.alternate_direction_check,
            w.reference_field_spin, w.display_mode_combo, w.colormap_combo,
            w.line_profile_check, w.save_button, w.progress_bar,
            w.status_label, w.current_signal_label,
        ):
            self.assertTrue(scroll.isAncestorOf(control), control)
        # The plot and image stay outside it.
        self.assertFalse(scroll.isAncestorOf(w.plot))
        self.assertFalse(scroll.isAncestorOf(w.image_view))
        self.assertEqual(scroll.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(scroll.verticalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.assertTrue(scroll.widgetResizable())

    def test_controls_fit_the_column_and_scroll_vertically(self):
        window, w = self.window, self.window.widget
        window.show()
        self.addCleanup(window.hide)
        window.resize(window.width(), 560)
        _APP.processEvents()
        scroll = w.control_scroll_area
        self.assertLessEqual(w.controls_group.width(), scroll.viewport().width())
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        _APP.processEvents()
        self.assertTrue(w.run_stop_button.isVisible())
        self.assertFalse(w.run_stop_button.visibleRegion().isEmpty())

    def _assert_button(self, text, colour, enabled=True):
        button = self.window.widget.run_stop_button
        self.assertEqual(button.text(), text)
        self.assertIn(f"QPushButton {{ background-color: {colour};", button.styleSheet())
        self.assertIn("color: white", button.styleSheet())
        self.assertEqual(button.isEnabled(), enabled)

    def _start_single_run(self, **form):
        self.set_form(**form)
        self.window.widget.run_stop_button.click()  # the toggle_scan() path
        self._assert_button("Stop", "red")

    def _wait_idle(self):
        self.assertTrue(_pump_until(lambda: not self.window.zero_field_running))
        _pump_until(lambda: not _thread_running(self.window), 10)

    def test_button_idle_running_and_back_when_run_completes(self):
        self._assert_button("Start", "green")
        self._start_single_run(points=3)
        self._wait_idle()
        self._assert_button("Start", "green")

    def test_button_returns_to_start_after_stop(self):
        self._start_single_run(points=40, settling=20)
        self.assertTrue(_pump_until(lambda: self.window.scan_signals != []))
        self.window.widget.run_stop_button.click()  # Stop
        self._wait_idle()
        self._assert_button("Start", "green")
        self.assertTrue(self.window.widget.status_label.text().startswith("Status: Stopped"))

    def test_button_returns_to_start_after_error(self):
        experiment_class = zero_field_worker_module.ZeroFieldExperiment
        with patch.object(experiment_class, "run",
                          side_effect=RuntimeError("simulated failure")):
            self._start_single_run(points=3)
            self._wait_idle()
        self._assert_button("Start", "green")
        self.critical.assert_called_once()  # single run: error dialog as before

    def test_button_disabled_while_queue_runs(self):
        self.add(points=3)
        self.add(points=3)
        self.window.run_queue()
        button = self.window.widget.run_stop_button
        self.assertFalse(button.isEnabled())
        self.assertEqual(button.text(), "Queue running")
        self.assertIn("Stop queue", button.toolTip())
        self.assertIn("QPushButton:disabled { background-color: #707070;",
                      button.styleSheet())
        # Still blocked between the two runs.
        self.assertTrue(_pump_until(
            lambda: self.window.queue_entries[0].status == "done"))
        self.assertEqual(button.text(), "Queue running")
        self.assertTrue(_pump_until(lambda: not self.window.queue_active()))
        self._assert_button("Start", "green")
        self.assertEqual(button.toolTip(), "")

    def test_get_config_defaults_unchanged(self):
        # The defaults get_config() returned before the layout change.
        widget = ZeroFieldWidget()
        self.addCleanup(_dispose, widget)
        self.assertEqual(widget.get_config(), {
            "field_start": -2.0, "field_stop": 2.0, "field_points": 5,
            "field_axis": "X", "settling_time_ms": 100,
            "post_flip_settling_ms": 500, "averages": 1,
            "averaging_enabled": False, "num_scans": 1, "save_raw_scans": False,
            "alternate_direction": False, "reference_enabled": False,
            "reference_field_gauss": -8.0, "save_data": True,
        })


class QueueModelTests(unittest.TestCase):
    def _config(self, **overrides):
        config = {
            "field_start": -2.0, "field_stop": 2.0, "field_points": 5,
            "field_axis": "X", "settling_time_ms": 100,
            "post_flip_settling_ms": 500, "averages": 1,
            "averaging_enabled": False, "num_scans": 4, "save_raw_scans": False,
            "alternate_direction": False, "reference_enabled": False,
            "reference_field_gauss": -8.0, "save_data": True,
        }
        config.update(overrides)
        return config

    def test_summary(self):
        self.assertEqual(
            queue_model.summarize(self._config()),
            "X -2→2 G, 5 pts, 1 rep, 1 scan, settle 100 ms, ref off, alt off",
        )
        self.assertEqual(
            queue_model.summarize(self._config(
                averaging_enabled=True, alternate_direction=True,
                reference_enabled=True, save_data=False,
            )),
            "X -2→2 G, 5 pts, 1 rep, 4 scans, settle 100 ms, ref -8 G, alt on, "
            "not saved",
        )

    def test_validate_config(self):
        self.assertEqual(queue_model.validate_config(self._config(field_start=-2)),
                         self._config())
        for bad in ({"field_points": 0}, {"field_axis": "W"}, {"averages": 1.5},
                    {"save_data": 1}, {"field_start": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    queue_model.validate_config(self._config(**bad))


if __name__ == "__main__":
    unittest.main()
