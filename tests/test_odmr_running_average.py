"""ODMR plot: running mean of the completed sweeps, redrawn after each sweep.

Real scans through ODMRWindow -> ODMRWorker -> ODMRExperiment on SimCamera,
SimMicrowave and SimPulseStreamer. No real hardware is touched.
"""

import sys
import time
import unittest
from collections import defaultdict
from unittest.mock import patch

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import gui.odmr_window as odmr_window_module  # noqa: E402
from experiments.odmr_experiment import ODMRExperiment  # noqa: E402
from gui.odmr_window import ODMRWindow  # noqa: E402
from hardware.camera.sim_camera import SimCamera  # noqa: E402
from tests.test_odmr_external_acquisition import _make_sim_hardware  # noqa: E402


def _pump_until(condition, timeout_s=60.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.002)
    return True


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.001
    binning = 1
    baseline_counts = 0.0


class RunningAverageTestCase(unittest.TestCase):
    STEPS = 6

    def setUp(self):
        camera = SimCamera(microwave=None)
        camera.connect()
        hardware = _make_sim_hardware(camera)
        self.window = ODMRWindow(
            hardware=hardware, camera=camera,
            acquisition_state_getter=lambda: _AcquisitionState())
        self.addCleanup(self._dispose)
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)
        p = self.window.panel
        p.steps_spin.setValue(self.STEPS)
        for spin, value in ((p.trigger_delay_spin, 0.001), (p.fire_delay_spin, 0.001),
                            (p.reset_delay_spin, 0.001), (p.pulse_lead_spin, 0.0005),
                            (p.pulse_tail_spin, 0.0005)):
            spin.setValue(value)

        # Every acquired scalar per frequency, in acquisition order.
        self.values = defaultdict(list)
        self.acquired = 0
        self.stop_after = None
        original = ODMRExperiment.acquire_pixel_statistics
        test = self

        def recording(experiment, frequency):
            result = original(experiment, frequency)
            test.values[float(frequency)].append(result[0])
            test.acquired += 1
            if test.stop_after is not None and test.acquired == test.stop_after:
                test.window.request_stop()
            return result

        patcher = patch.object(ODMRExperiment, "acquire_pixel_statistics", recording)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Every drawing of the normalized plot, with the title set after it.
        self.draws = []
        panel = self.window.panel
        original_draw = panel.update_plot_normalized
        original_title = panel.set_plot_title

        def draw(x, y):
            self.draws.append({"x": list(x), "y": list(y), "title": None,
                               "running": self.window.odmr_running})
            original_draw(x, y)

        def title(text):
            if self.draws and self.draws[-1]["title"] is None:
                self.draws[-1]["title"] = text
            original_title(text)

        panel.update_plot_normalized = draw
        panel.set_plot_title = title

    def _dispose(self):
        window = self.window
        if window.odmr_running:
            window.request_stop()
        _pump_until(lambda: not window.odmr_running, 15)
        thread = window.worker_thread()
        _pump_until(lambda: thread is None or not thread.isRunning(), 10)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _run(self, averages):
        self.window.panel.avg_spin.setValue(averages)
        self.window.toggle_odmr()
        self.assertTrue(self.window.odmr_running)
        self.assertTrue(_pump_until(lambda: not self.window.odmr_running))
        thread = self.window.worker_thread()
        _pump_until(lambda: not thread.isRunning(), 10)
        self.critical.assert_not_called()

    def _key(self, x_ghz):
        return min(self.values, key=lambda f: abs(f / 1e9 - x_ghz))


class RunningAverageTests(RunningAverageTestCase):
    def test_plot_after_each_sweep_is_the_mean_of_the_completed_sweeps(self):
        with patch.object(odmr_window_module, "fit_single_lorentzian") as fit_one, \
                patch.object(odmr_window_module, "fit_double_lorentzian") as fit_two:
            self._run(averages=3)
        fit_one.assert_not_called()  # no automatic fit on any average
        fit_two.assert_not_called()
        during = [d for d in self.draws if d["running"]]
        self.assertEqual([d["title"] for d in during],
                         ["Average 1 / 3", "Average 2 / 3", "Average 3 / 3"])
        for k, draw in enumerate(during, start=1):
            self.assertEqual(len(draw["x"]), self.STEPS, "never a partial sweep")
            np.testing.assert_allclose(
                draw["y"], [np.mean(self.values[self._key(x)][:k]) for x in draw["x"]],
                rtol=1e-12)

    def test_final_plot_identical_to_the_saved_and_previous_display(self):
        self._run(averages=3)
        final = self.draws[-1]
        self.assertFalse(final["running"])
        # Before this change the final plot was latest_x/latest_y (also what
        # is saved); it is still exactly that.
        self.assertEqual(final["x"], list(self.window.latest_x))
        self.assertEqual(final["y"], list(self.window.latest_y))
        self.assertEqual(final["title"], "Average 3 / 3")
        np.testing.assert_allclose(
            final["y"], [np.mean(self.values[self._key(x)]) for x in final["x"]],
            rtol=1e-12)

    def test_stop_mid_sweep_keeps_the_completed_sweeps_mean(self):
        self.stop_after = 2 * self.STEPS + 2  # in sweep 3 of 4
        self._run(averages=4)
        self.assertEqual(self.window.odmr_worker.completed_averages, 2)
        final = self.draws[-1]
        self.assertFalse(final["running"])
        self.assertEqual(final["title"], "Average 2 / 4 (stopped)")
        self.assertEqual(len(final["x"]), self.STEPS)
        np.testing.assert_allclose(
            final["y"], [np.mean(self.values[self._key(x)][:2]) for x in final["x"]],
            rtol=1e-12)
        # The saved spectrum (latest_y) is unchanged by the display: it still
        # includes the partly done sweep, as before.
        self.assertNotEqual(final["y"], list(self.window.latest_y))

        # "Fit" fits what is shown.
        with patch.object(odmr_window_module, "fit_single_lorentzian",
                          side_effect=RuntimeError("stop here")) as fit:
            self.window.fit_esr()
        self.assertEqual(list(fit.call_args.args[0]), final["x"])
        self.assertEqual(list(fit.call_args.args[1]), final["y"])

    def test_single_average_unchanged(self):
        self._run(averages=1)
        self.assertEqual(self.window.panel.plot_title, "")
        final = self.draws[-1]
        self.assertEqual(final["y"], list(self.window.latest_y))

    def test_fit_button_enabled_after_first_sweep(self):
        enabled = []
        original = self.window.show_completed_sweeps

        def spy(*args):
            original(*args)
            enabled.append(self.window.panel.fit_button.isEnabled())

        with patch.object(self.window, "show_completed_sweeps", spy):
            # The worker's signal is connected in run_odmr, after this patch.
            self._run(averages=2)
        self.assertEqual(enabled, [True, True])


if __name__ == "__main__":
    unittest.main()
