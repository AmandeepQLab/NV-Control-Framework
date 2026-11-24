"""Optional count stability check before experiments.

framework/stability.py (decision rule), gui/stability_gate.py (monitor,
check, wait controller), and its use in the ODMR and Zero Field windows and
the Zero Field run queue. Samples go through the real CountStatisticsPanel
(add_frame -> sample_accepted) on a fake clock; runs are real sim runs.
No real hardware is touched.
"""

import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from framework import stability  # noqa: E402
from framework.acquisition_state import AcquisitionState  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from framework.stability import StabilitySettings, evaluate, window_statistics  # noqa: E402
from gui import zero_field_queue as queue_model  # noqa: E402
from gui.odmr_window import ODMRWindow  # noqa: E402
from gui.stability_gate import StabilityMonitor  # noqa: E402
from gui.widgets.count_statistics_panel import CountStatisticsPanel  # noqa: E402
from gui.zero_field_window import ZeroFieldWindow  # noqa: E402
from hardware.camera.sim_camera import SimCamera  # noqa: E402
from tests.test_odmr_external_acquisition import _make_sim_hardware  # noqa: E402
from tests.test_zero_field_queue import (  # noqa: E402
    QueueWindowTestCase,
    _AcquisitionState,
    _pump_until,
    _thread_running,
)
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402

RATE_HZ = 2.0  # samples per second of fake time


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def stream(kind, seconds, mean=20000.0, seed=0):
    """(times, values) at RATE_HZ: 'stable', 'drift' (0.8 %/min) or 'noisy'."""
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, seconds, 1.0 / RATE_HZ)
    if kind == "stable":
        y = mean * (1 + rng.normal(0, 0.0005, t.size))            # 0.05 % noise
    elif kind == "drift":
        y = mean * (1 + 0.008 * t / 60.0 + rng.normal(0, 0.0005, t.size))
    elif kind == "noisy":
        y = mean * (1 + rng.normal(0, 0.006, t.size))             # 0.6 % noise
    else:
        raise ValueError(kind)
    return t, y


SETTINGS = StabilitySettings(enabled=True, window_s=60.0, max_std_percent=0.30,
                             max_drift_percent_per_min=0.50, timeout_s=600.0)


class GateLogicTests(unittest.TestCase):
    def _judge(self, kind, seconds, settings=SETTINGS):
        t, y = stream(kind, seconds)
        return evaluate(t, y, now=t[-1], settings=settings, collecting_since=0.0)

    def test_stable_stream_passes_only_after_the_window(self):
        self.assertFalse(self._judge("stable", 30).passed)   # window not full
        result = self._judge("stable", 61)
        self.assertTrue(result.window_full)
        self.assertTrue(result.passed)
        self.assertLess(result.std_percent, 0.1)

    def test_drifting_stream_fails_on_drift(self):
        result = self._judge("drift", 90)
        self.assertTrue(result.window_full)
        self.assertLess(result.std_percent, SETTINGS.max_std_percent)
        self.assertAlmostEqual(result.drift_percent_per_min, 0.8, delta=0.1)
        self.assertFalse(result.passed)

    def test_noisy_stream_fails_on_std(self):
        result = self._judge("noisy", 90)
        self.assertTrue(result.window_full)
        self.assertGreater(result.std_percent, SETTINGS.max_std_percent)
        self.assertFalse(result.passed)

    def test_statistics_match_numpy(self):
        t, y = stream("drift", 60, seed=3)
        std, drift = window_statistics(t, y)
        self.assertAlmostEqual(std, y.std(ddof=1) / y.mean() * 100, places=10)
        slope = np.polyfit(t, y, 1)[0]
        self.assertAlmostEqual(drift, slope * 60 / y.mean() * 100, places=8)

    def test_limits_are_inclusive_and_both_apply(self):
        t, y = stream("drift", 61, seed=5)
        std, drift = window_statistics(t[t > t[-1] - 60], y[t > t[-1] - 60])

        def passes(max_std, max_drift):
            s = StabilitySettings(enabled=True, window_s=60.0, max_std_percent=max_std,
                                  max_drift_percent_per_min=max_drift)
            return evaluate(t, y, t[-1], s, 0.0).passed

        self.assertTrue(passes(std + 1e-9, abs(drift) + 1e-9))
        self.assertFalse(passes(std - 1e-6, abs(drift) + 1))   # std over limit
        self.assertFalse(passes(std + 1, abs(drift) - 1e-6))   # drift over limit

    def test_only_the_last_window_counts(self):
        # Noisy at first, then 70 s stable: the noisy part has left the window.
        t1, y1 = stream("noisy", 60, seed=1)
        t2, y2 = stream("stable", 70, seed=2)
        t = np.concatenate([t1, t2 + 60])
        y = np.concatenate([y1, y2])
        self.assertTrue(evaluate(t, y, t[-1], SETTINGS, 0.0).passed)

    def test_window_with_missing_samples_is_not_full(self):
        t, y = stream("stable", 61)
        late = t > 20  # nothing in the first third of the window
        self.assertFalse(evaluate(t[late], y[late], t[-1], SETTINGS, 0.0).window_full)
        stale = t < 55  # frames stopped arriving 6 s ago
        self.assertFalse(evaluate(t[stale], y[stale], 61.0, SETTINGS, 0.0).window_full)

    def test_record_when_disabled_is_one_key(self):
        self.assertEqual(stability.record(StabilitySettings()),
                         {"stability_check_enabled": False})

    def test_status_text(self):
        t, y = stream("noisy", 45)
        result = evaluate(t, y, 45.0, SETTINGS, 0.0)
        text = stability.status_text(result, SETTINGS, 45.0)
        self.assertTrue(text.startswith("Waiting for stable counts: std "))
        self.assertIn("(limit 0.30%)", text)
        self.assertIn("%/min (limit 0.50%/min)", text)
        self.assertIn("45 s elapsed", text)

    def test_settings_round_trip_and_validation(self):
        s = StabilitySettings(enabled=True, window_s=30, on_timeout=stability.ON_TIMEOUT_CANCEL)
        self.assertEqual(StabilitySettings.from_dict(s.to_dict()), s)
        for bad in ({"window_s": -1}, {"enabled": 1}, {"on_timeout": "maybe"},
                    {"max_std_percent": float("nan")}):
            with self.assertRaises(ValueError):
                StabilitySettings.from_dict(bad)


# ---- Qt side -------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeLiveView:
    """Stands in for MainWindow.start_stream()/stop_stream()/timer.isActive()."""

    def __init__(self, running=False):
        self.running = running
        self.starts = self.stops = 0

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


class GateHarness:
    """A real CountStatisticsPanel + StabilityMonitor on a fake clock."""

    def __init__(self, test, live_running=False):
        self.clock = FakeClock()
        self.state = AcquisitionState(None, 0.01, 1)
        self.panel = CountStatisticsPanel(
            settings_getter=lambda: self.state, camera_owned=lambda: False,
            log_directory=tempfile.gettempdir(), clock=self.clock,
        )
        test.addCleanup(_dispose, self.panel)
        self.live = FakeLiveView(live_running)
        self.monitor = StabilityMonitor(
            self.panel, live_view_running=lambda: self.live.running,
            start_live_view=self.live.start, stop_live_view=self.live.stop,
        )
        self.seed = 0

    def feed(self, controller, seconds, kind="stable", until=None):
        """Advance fake time, feeding live frames while the live view runs,
        and tick the active check -- as the 250 ms timer would."""
        rng = np.random.default_rng(self.seed)
        steps = int(seconds * RATE_HZ)
        for _ in range(steps):
            self.clock.t += 1.0 / RATE_HZ
            self.seed += 1
            if self.live.running:
                scale = {"stable": 0.0005, "noisy": 0.006}[kind]
                mean = 20000.0 * (1 + rng.normal(0, scale))
                frame = np.random.default_rng(self.seed).normal(0, 30, (8, 8))
                self.panel.add_frame(frame - frame.mean() + mean)
            if controller.check is not None:
                controller.check.tick()
            _APP.processEvents()
            if until is not None and until():
                return True
            if controller.check is None:
                return True
        return False


def _short(**overrides):
    values = dict(enabled=True, window_s=10.0, max_std_percent=0.30,
                  max_drift_percent_per_min=0.50, timeout_s=60.0,
                  on_timeout=stability.ON_TIMEOUT_START)
    values.update(overrides)
    return StabilitySettings(**values)


class _ODMRState:
    acquisition_roi = None
    exposure_s = 0.001
    binning = 1
    baseline_counts = 0.0


class ODMRStabilityTests(unittest.TestCase):
    def setUp(self):
        self.gate = GateHarness(self)
        camera = SimCamera()
        camera.connect()
        self.window = ODMRWindow(
            hardware=_make_sim_hardware(camera), camera=camera,
            acquisition_state_getter=lambda: _ODMRState(),
            stability_monitor=self.gate.monitor,
        )
        self.addCleanup(self._dispose)
        p = self.window.panel
        p.steps_spin.setValue(3)
        for spin, value in ((p.trigger_delay_spin, 0.001), (p.fire_delay_spin, 0.001),
                            (p.reset_delay_spin, 0.001), (p.pulse_lead_spin, 0.0005),
                            (p.pulse_tail_spin, 0.0005)):
            spin.setValue(value)
        self.started = []
        self.window.run_started.connect(
            lambda: self.started.append(self.gate.live.running)
        )
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)

    def _dispose(self):
        w = self.window
        w.request_stop()
        _pump_until(lambda: not w.odmr_running, 15)
        thread = w.worker_thread()
        _pump_until(lambda: thread is None or not thread.isRunning(), 10)
        _dispose(w)

    def _set(self, settings):
        self.window.panel.stability_group.set_settings(settings)

    def _finish_run(self):
        self.assertTrue(_pump_until(lambda: not self.window.odmr_running, 30))

    def test_stable_counts_pass_and_start_with_live_view_restored(self):
        self._set(_short())
        self.window.panel.run_stop_button.click()
        self.assertTrue(self.window.stability_waiting())
        self.assertEqual(self.gate.live.starts, 1)          # live view was off
        button = self.window.panel.run_stop_button
        self.assertEqual(button.text(), "Waiting for stable counts")
        self.assertFalse(button.isEnabled())
        bar = self.window.panel.stability_wait_bar
        self.assertFalse(bar.isHidden())
        self.assertTrue(self.gate.feed(self.window.stability, 30))
        self.assertEqual(self.started, [False])  # live view off again at start
        self.assertEqual(self.gate.live.stops, 1)
        self.assertTrue(bar.isHidden())
        self._finish_run()
        config = self.window.current_config
        self.assertEqual(config["stability_outcome"], stability.OUTCOME_PASSED)
        self.assertTrue(config["stability_check_enabled"])
        self.assertLess(config["stability_std_percent"], 0.3)
        self.assertLessEqual(abs(config["stability_drift_percent_per_min"]), 0.5)
        self.assertGreaterEqual(config["stability_wait_s"], 10.0)
        self.assertEqual(config["stability_window_s"], 10.0)

    def test_running_live_view_is_left_running(self):
        self.gate.live.running = True
        self._set(_short())
        self.window.request_start()
        self.gate.feed(self.window.stability, 30)
        self.assertEqual((self.gate.live.starts, self.gate.live.stops), (0, 0))
        self.assertEqual(self.started, [True])
        self._finish_run()

    def test_timeout_start_anyway_starts(self):
        self._set(_short(timeout_s=20.0, on_timeout=stability.ON_TIMEOUT_START))
        self.window.request_start()
        self.gate.feed(self.window.stability, 40, kind="noisy")
        self.assertEqual(len(self.started), 1)
        self._finish_run()
        self.assertEqual(self.window.current_config["stability_outcome"],
                         stability.OUTCOME_TIMEOUT_STARTED)
        self.assertGreater(self.window.current_config["stability_std_percent"], 0.3)

    def test_timeout_cancel_does_not_start(self):
        self._set(_short(timeout_s=20.0, on_timeout=stability.ON_TIMEOUT_CANCEL))
        self.window.request_start()
        self.gate.feed(self.window.stability, 40, kind="noisy")
        self.assertEqual(self.started, [])
        self.assertFalse(self.window.stability_waiting())
        self.assertFalse(self.gate.live.running)  # restored
        self.assertIn(stability.OUTCOME_TIMEOUT_CANCELLED, self.window.panel.time_label.text())
        self.assertEqual(self.window.panel.run_stop_button.text(), "Start")

    def test_start_anyway_button(self):
        self._set(_short())
        self.window.request_start()
        self.gate.feed(self.window.stability, 3, kind="noisy")
        self.window.panel.stability_wait_bar.start_anyway_button.click()
        self.assertEqual(len(self.started), 1)
        self._finish_run()
        self.assertEqual(self.window.current_config["stability_outcome"],
                         stability.OUTCOME_USER_STARTED)

    def test_cancel_button(self):
        self._set(_short())
        self.window.request_start()
        self.gate.feed(self.window.stability, 3)
        self.window.panel.stability_wait_bar.cancel_button.click()
        self.assertEqual(self.started, [])
        self.assertFalse(self.window.stability_waiting())
        self.assertEqual(self.gate.live.stops, 1)
        self.assertTrue(self.window.panel.run_stop_button.isEnabled())

    def test_status_line_while_waiting(self):
        self._set(_short(window_s=60.0))
        self.window.request_start()
        self.gate.feed(self.window.stability, 20, kind="noisy")
        text = self.window.panel.stability_wait_bar.status_label.text()
        self.assertRegex(text, r"^Waiting for stable counts: std \d+\.\d\d% \(limit 0\.30%\), "
                               r"drift [+−]\d+\.\d\d%/min \(limit 0\.50%/min\), "
                               r"window \d+/60 s, \d+ s elapsed")
        self.window.cancel_stability_wait()

    def test_checkbox_off_start_path_unchanged(self):
        with patch.object(self.gate.monitor, "begin") as begin, \
             patch.object(self.window, "run_odmr") as run_odmr:
            self.window.panel.run_stop_button.click()
        begin.assert_not_called()
        run_odmr.assert_called_once_with()
        self.window.run_odmr()
        self._finish_run()
        config = self.window.current_config
        self.assertIs(config["stability_check_enabled"], False)
        self.assertEqual([k for k in config if k.startswith("stability")],
                         ["stability_check_enabled"])

    def test_every_new_control_has_a_tooltip(self):
        p = self.window.panel
        for control in p.stability_group.controls() + p.stability_wait_bar.controls():
            self.assertTrue(control.toolTip().strip(), control)


class ZeroFieldStabilityTestCase(QueueWindowTestCase):
    """QueueWindowTestCase's window, plus a stability monitor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.gate = GateHarness(self)
        self.magnet = make_magnet()
        self.window = ZeroFieldWindow(
            hardware_manager=None, hardware={"magnet": self.magnet},
            camera=make_camera(), acquisition_state_getter=lambda: _AcquisitionState(),
            stability_monitor=self.gate.monitor,
        )
        self.addCleanup(self._close_window)
        self.window.output_directory = self.directory
        self.window.widget.set_output_directory(self.directory)
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)

    def _close_window(self):
        if self.window.stability_waiting():
            self.window.stability.cancel()
        super()._close_window()

    def set_stability(self, settings):
        self.window.widget.stability_group.set_settings(settings)

    def wait_idle(self):
        self.assertTrue(_pump_until(lambda: not self.window.zero_field_running, 30))
        _pump_until(lambda: not _thread_running(self.window), 10)

    def metadata(self, path):
        return ImageCube.load(path).metadata


class ZeroFieldStabilityTests(ZeroFieldStabilityTestCase):
    def test_passed_check_is_recorded_in_the_file(self):
        self.set_form(points=3)
        self.set_stability(_short())
        self.window.widget.run_stop_button.click()
        self.assertEqual(self.window.widget.run_stop_button.text(), "Waiting for stable counts")
        self.assertIn("Waiting", self.window.widget.status_label.text())
        self.assertTrue(self.gate.feed(self.window.stability, 30))
        self.wait_idle()
        meta = self.metadata(self.window.output_path)
        self.assertIs(meta["stability_check_enabled"], True)
        self.assertEqual(meta["stability_outcome"], stability.OUTCOME_PASSED)
        for key in ("stability_window_s", "stability_max_std_percent",
                    "stability_max_drift_percent_per_min", "stability_timeout_s",
                    "stability_on_timeout", "stability_std_percent",
                    "stability_drift_percent_per_min", "stability_wait_s"):
            self.assertIn(key, meta)
        self.assertLess(meta["stability_std_percent"], 0.3)
        self.assertNotIn("stability_window_s", meta["scan_parameters"])
        self.assertEqual(self.window.widget.run_stop_button.text(), "Start")

    def test_checkbox_off_start_path_and_file_unchanged(self):
        self.set_form(points=3)
        config = self.window.widget.get_config()
        with patch.object(self.window, "_start_run") as start_run, \
             patch.object(self.gate.monitor, "begin") as begin:
            self.window.widget.run_stop_button.click()
        begin.assert_not_called()
        start_run.assert_called_once_with(config, self.directory)
        self.window.widget.run_stop_button.click()
        self.wait_idle()
        meta = self.metadata(self.window.output_path)
        self.assertEqual([k for k in meta if k.startswith("stability")],
                         ["stability_check_enabled"])
        self.assertIs(meta["stability_check_enabled"], False)
        self.assertEqual(self.gate.live.starts, 0)

    def test_timeout_cancel_does_not_start(self):
        self.set_form(points=3)
        self.set_stability(_short(timeout_s=15.0, on_timeout=stability.ON_TIMEOUT_CANCEL))
        self.window.start_scan()
        self.gate.feed(self.window.stability, 30, kind="noisy")
        self.assertFalse(self.window.zero_field_running)
        self.assertIsNone(self.window.thread)
        self.assertIn("Not started", self.window.widget.status_label.text())
        self.assertEqual(self.window.widget.run_stop_button.text(), "Start")

    def test_unreachable_sweep_is_refused_before_waiting(self):
        self.set_form(start=-5000.0, stop=5000.0)
        self.set_stability(_short())
        self.window.start_scan()
        self.critical.assert_called_once()
        self.assertFalse(self.window.stability_waiting())
        self.assertEqual(self.gate.live.starts, 0)

    def test_every_new_control_has_a_tooltip(self):
        w = self.window.widget
        for control in w.stability_group.controls() + w.stability_wait_bar.controls():
            self.assertTrue(control.toolTip().strip(), control)

    def test_unavailable_without_a_monitor(self):
        window = ZeroFieldWindow(
            hardware_manager=None, hardware={"magnet": make_magnet()},
            camera=make_camera(), acquisition_state_getter=lambda: _AcquisitionState(),
        )
        self.addCleanup(_dispose, window)
        group = window.widget.stability_group
        self.assertFalse(group.enabled_check.isEnabled())
        self.assertFalse(group.get_settings().enabled)


class QueueStabilityTests(ZeroFieldStabilityTestCase):
    def _run_queue(self, kind, seconds=120):
        self.assertTrue(self.window.run_queue())
        gate = self.gate

        def done():
            return not self.window.queue_active()

        # Drive fake time while any entry waits; let real runs proceed.
        deadline_steps = int(seconds * RATE_HZ)
        for _ in range(deadline_steps):
            if done():
                break
            if self.window.stability_waiting():
                gate.feed(self.window.stability, 1.0 / RATE_HZ, kind=kind)
            else:
                _pump_until(lambda: done() or self.window.stability_waiting(), 0.2)
        self.assertTrue(_pump_until(done, 30), "queue did not finish")
        _pump_until(lambda: not _thread_running(self.window), 10)

    def test_unstable_entry_is_skipped_and_queue_continues(self):
        self.set_stability(_short(timeout_s=15.0, on_timeout=stability.ON_TIMEOUT_CANCEL))
        first = self.add(points=3)
        self.set_stability(StabilitySettings())  # second entry: check off
        second = self.add(points=3)
        self.assertTrue(first.stability.enabled)
        self.assertFalse(second.stability.enabled)
        self.assertIn("stability ≤0.3%", first.summary())
        self._run_queue("noisy")
        self.assertEqual(self.statuses(), [queue_model.SKIPPED_UNSTABLE, queue_model.DONE])
        self.assertEqual(first.output_path, "")
        self.assertIn("not stable within 15 s", first.message)
        self.assertIn("1 skipped (unstable)", self.window.queue_widget.queue_status_label.text())
        rows = self.log_rows()
        self.assertEqual([r["status"] for r in rows],
                         [queue_model.SKIPPED_UNSTABLE, queue_model.DONE])
        self.assertEqual(rows[0]["stability_check_enabled"], "True")
        self.assertEqual(rows[0]["stability_outcome"], stability.OUTCOME_TIMEOUT_CANCELLED)
        self.assertGreater(float(rows[0]["stability_std_percent"]), 0.3)
        self.assertGreaterEqual(float(rows[0]["stability_wait_s"]), 15.0)
        self.assertEqual(rows[0]["stability_timeout_s"], "15.0")
        self.assertEqual(rows[1]["stability_check_enabled"], "False")
        self.assertEqual(rows[1]["stability_outcome"], stability.OUTCOME_NOT_USED)
        meta = self.metadata(Path(second.output_path))
        self.assertIs(meta["stability_check_enabled"], False)

    def test_stable_entry_runs_with_its_check_recorded(self):
        self.set_stability(_short())
        entry = self.add(points=3)
        self._run_queue("stable")
        self.assertEqual(self.statuses(), [queue_model.DONE])
        meta = self.metadata(Path(entry.output_path))
        self.assertEqual(meta["stability_outcome"], stability.OUTCOME_PASSED)
        row = self.log_rows()[0]
        self.assertEqual(row["stability_outcome"], stability.OUTCOME_PASSED)
        self.assertEqual(float(row["stability_std_percent"]), meta["stability_std_percent"])
        self.critical.assert_not_called()  # no dialogs during a queue

    def test_stop_queue_during_wait(self):
        self.set_stability(_short(timeout_s=600.0))
        self.add(points=3)
        self.add(points=3)
        self.assertTrue(self.window.run_queue())
        self.assertTrue(self.window.stability_waiting())
        self.window.stop_queue()
        self.assertFalse(self.window.queue_active())
        self.assertEqual(self.statuses(), [queue_model.STOPPED, queue_model.PENDING])
        self.assertFalse(self.gate.live.running)

    def test_queue_file_round_trips_stability(self):
        settings = _short(window_s=45.0, on_timeout=stability.ON_TIMEOUT_CANCEL)
        self.set_stability(settings)
        self.add(points=3)
        path = self.directory / "queue.json"
        self.assertTrue(self.window.save_queue_to(path))
        self.window.queue_entries = []
        self.assertTrue(self.window.load_queue_from(path))
        self.assertEqual(self.window.queue_entries[0].stability, settings)
        self.window.widget.stability_group.set_settings(StabilitySettings())
        self.window.queue_widget.select_row(0)
        self.window.load_entry_into_form()
        self.assertEqual(self.window.widget.stability_group.get_settings(), settings)

    def test_old_queue_file_without_stability_loads_as_off(self):
        entry = queue_model.QueueEntry.snapshot(self.window.widget.get_config(), self.directory)
        path = self.directory / "old.json"
        queue_model.save_queue_file(path, [entry], 0.0, queue_model.ON_FAILURE_STOP)
        text = path.read_text(encoding="utf-8")
        import json
        document = json.loads(text)
        del document["entries"][0]["stability"]
        path.write_text(json.dumps(document), encoding="utf-8")
        entries, _, _ = queue_model.load_queue_file(path)
        self.assertFalse(entries[0].stability.enabled)


if __name__ == "__main__":
    unittest.main()
