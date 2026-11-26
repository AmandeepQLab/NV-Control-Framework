"""SG386 RF output outside ODMR, and device release at exit (2026-11-26).

- RF output off when an ODMR scan ends: completed, stopped, or failed.
- Zero Field: RF off ensured at the start of every run (single and queued)
  and recorded in the saved file's metadata.
- Exit: SG386 RF off then closed; camera closed; each bounded, independent
  of the other devices, failures logged (never swallowed silently).

Simulators and mocks only.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import hardware.hardware_manager as hardware_manager_module  # noqa: E402
from experiments.odmr_experiment import ODMRExperiment  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from framework.microwave_safety import ensure_rf_off  # noqa: E402
from gui.odmr_worker import ODMRWorker  # noqa: E402
from gui.zero_field_window import ZeroFieldWindow  # noqa: E402
from hardware.camera.sim_camera import SimCamera  # noqa: E402
from hardware.hardware_manager import HardwareManager  # noqa: E402
from hardware.microwave.sim_microwave import SimMicrowave  # noqa: E402
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer  # noqa: E402
from tests.test_odmr_external_acquisition import (  # noqa: E402
    _FailingSimCamera,
    _make_fast_config,
    _make_sim_hardware,
    _run_worker_to_completion,
)
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402


def _pump_until(condition, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# =====================================================================
# ODMR: RF off when the scan ends
# =====================================================================

class ODMRRfOffTests(unittest.TestCase):
    def _worker(self, camera=None):
        hardware = _make_sim_hardware(camera or SimCamera(image_shape=(4, 4)))
        return hardware["microwave"], ODMRWorker(
            hardware, _make_fast_config(), acquisition_roi=None)

    def test_rf_on_during_the_scan_and_off_after_completion(self):
        mw, worker = self._worker()
        seen = []
        worker.point_signal.connect(lambda *_: seen.append(mw.is_rf_on()))
        self.assertTrue(_run_worker_to_completion(worker))
        self.assertTrue(seen and all(seen))  # on while scanning
        self.assertFalse(mw.is_rf_on())

    def test_rf_off_after_a_stop(self):
        mw, worker = self._worker()
        worker.point_signal.connect(lambda *_: worker.stop())
        self.assertTrue(_run_worker_to_completion(worker))
        self.assertFalse(mw.is_rf_on())

    def test_rf_off_after_an_error(self):
        mw, worker = self._worker(_FailingSimCamera(fail_after_grabs=1, image_shape=(4, 4)))
        errors = []
        worker.error_signal.connect(errors.append)
        self.assertTrue(_run_worker_to_completion(worker))
        self.assertTrue(errors)
        self.assertFalse(mw.is_rf_on())

    def test_switch_rf_off_failure_is_logged_not_raised(self):
        mw = Mock()
        mw.rf_off.side_effect = OSError("VISA timeout")
        experiment = ODMRExperiment.__new__(ODMRExperiment)
        experiment.hw = {"microwave": mw}
        with self.assertLogs("experiments.odmr_experiment", level="ERROR"):
            self.assertFalse(experiment.switch_rf_off())


# =====================================================================
# Zero Field: RF off ensured and recorded at every run's start
# =====================================================================

class EnsureRfOffTests(unittest.TestCase):
    def test_record_with_read_back_and_gate(self):
        mw = SimMicrowave()
        mw.connect()
        mw.set_frequency(2.9e9)
        mw.set_power(-12.0)
        mw.rf_on()
        ps = SimPulseStreamer(channel_map={"MW": 2})
        record = ensure_rf_off(mw, ps, 2)
        self.assertFalse(mw.is_rf_on())
        self.assertEqual(record["sg386_connected"], True)
        self.assertEqual(record["sg386_rf_off_commanded"], True)
        self.assertEqual(record["sg386_rf_on"], False)
        self.assertEqual(record["sg386_rf_state_source"], "read back")
        self.assertEqual(record["sg386_frequency_hz"], 2.9e9)
        self.assertEqual(record["sg386_power_dbm"], -12.0)
        self.assertEqual(record["mw_gate_channel"], 2)
        self.assertIs(record["mw_gate_high"], False)
        self.assertIn("sg386_rf_on", record["microwave_state_definitions"])

    def test_no_sg386(self):
        self.assertEqual(ensure_rf_off(None)["sg386_connected"], False)

    def test_failure_is_recorded_and_logged(self):
        mw = Mock(spec=["rf_off", "get_frequency", "get_power"])
        mw.rf_off.side_effect = OSError("VISA timeout")
        mw.get_frequency.return_value = 2.87e9
        mw.get_power.return_value = -10
        with self.assertLogs("framework.microwave_safety", level="ERROR"):
            record = ensure_rf_off(mw)
        self.assertFalse(record["sg386_rf_off_commanded"])
        self.assertIsNone(record["sg386_rf_on"])  # no read-back on this driver
        self.assertEqual(record["sg386_rf_state_source"], "commanded")
        self.assertIn("VISA timeout", record["sg386_error"])


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.01
    binning = 1


class ZeroFieldRunRecordsRfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mw = SimMicrowave()
        self.mw.connect()
        self.mw.set_frequency(2.95e9)
        self.mw.set_power(-7.0)
        self.ps = SimPulseStreamer(channel_map={"MW": 2})
        self.window = ZeroFieldWindow(
            hardware_manager=None,
            hardware={"magnet": make_magnet(), "microwave": self.mw,
                      "pulse_streamer": self.ps, "channels": {"MW": 2}},
            camera=make_camera(),
            acquisition_state_getter=lambda: _AcquisitionState(),
        )
        self.addCleanup(lambda: _dispose(self.window))
        self.window.output_directory = Path(self.tmp.name)
        w = self.window.widget
        w.field_start_spin.setValue(-1.0)
        w.field_stop_spin.setValue(1.0)
        w.field_points_spin.setValue(3)
        w.settling_time_spin.setValue(0)
        w.post_flip_settling_spin.setValue(0)
        critical = patch.object(QMessageBox, "critical")
        critical.start()
        self.addCleanup(critical.stop)

    def _wait_thread(self):
        _pump_until(lambda: self.window.thread is None or not _running(self.window.thread), 10)

    def _metadata(self, path):
        return ImageCube.load(path).metadata

    def test_single_run_switches_rf_off_and_records_it(self):
        self.mw.rf_on()  # e.g. left on by something before the run
        self.window._start_run(self.window.widget.get_config(), self.window.output_directory)
        self.assertTrue(_pump_until(lambda: not self.window.zero_field_running))
        self._wait_thread()
        self.assertFalse(self.mw.is_rf_on())
        metadata = self._metadata(self.window.output_path)
        self.assertIs(metadata["sg386_connected"], True)
        self.assertIs(metadata["sg386_rf_on"], False)
        self.assertEqual(metadata["sg386_rf_state_source"], "read back")
        self.assertEqual(metadata["sg386_frequency_hz"], 2.95e9)
        self.assertEqual(metadata["sg386_power_dbm"], -7.0)
        self.assertEqual(metadata["mw_gate_channel"], 2)
        self.assertIs(metadata["mw_gate_high"], False)

    def test_every_queued_run_switches_rf_off(self):
        window = self.window
        for _ in range(2):
            self.assertTrue(window.add_to_queue())
        turned_on = []

        def rf_on_between_runs():
            # Something switches RF on before each run; the run must undo it.
            self.mw.rf_on()
            turned_on.append(True)

        window.run_started.connect(lambda: None)
        original = window._start_run

        def start_run(*args, **kwargs):
            rf_on_between_runs()
            return original(*args, **kwargs)

        with patch.object(window, "_start_run", side_effect=start_run):
            self.assertTrue(window.run_queue())
            self.assertTrue(_pump_until(lambda: not window.queue_active(), 60))
        self._wait_thread()
        self.assertEqual(len(turned_on), 2)
        self.assertFalse(self.mw.is_rf_on())
        files = sorted(Path(entry.output_path) for entry in window.queue_entries)
        self.assertEqual(len(files), 2)
        for path in files:
            self.assertIs(self._metadata(path)["sg386_rf_on"], False, path)


def _running(thread):
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


# =====================================================================
# Exit: SG386 and camera released, bounded, failures logged
# =====================================================================

class ExitReleaseTests(unittest.TestCase):
    def _sim_manager(self):
        manager = HardwareManager(config_manager=None)
        mw = SimMicrowave()
        mw.connect()
        mw.rf_on()
        camera = SimCamera(image_shape=(4, 4))
        manager.hardware = {
            "magnet": Mock(spec=["disable"]),
            "power_supplies": {},
            "pulse_streamer": Mock(spec=["close"]),
            "microwave": mw,
            "camera": camera,
        }
        return manager, mw, camera

    def test_exit_switches_rf_off_and_closes_sg386_and_camera(self):
        manager, mw, camera = self._sim_manager()
        manager.shutdown()
        self.assertFalse(mw.is_rf_on())
        self.assertFalse(mw.connected)
        self.assertTrue(camera.closed)

    def test_still_released_when_other_devices_fail(self):
        manager, mw, camera = self._sim_manager()
        manager.hardware["magnet"].disable.side_effect = RuntimeError("serial")
        manager.hardware["pulse_streamer"].close.side_effect = RuntimeError("net")
        with self.assertLogs("hardware.hardware_manager", level="ERROR") as logs:
            manager.shutdown()
        self.assertFalse(mw.connected)
        self.assertTrue(camera.closed)
        self.assertIn("Magnet shutdown failed", "\n".join(logs.output))

    def test_close_failure_is_logged(self):
        manager, _mw, _camera = self._sim_manager()
        broken = Mock(spec=["close"])
        broken.close.side_effect = OSError("SDK error")
        manager.hardware["camera"] = broken
        with self.assertLogs("hardware.hardware_manager", level="ERROR") as logs:
            manager.shutdown()
        self.assertIn("camera close() failed", "\n".join(logs.output))

    def test_device_without_close_is_reported(self):
        manager, _mw, _camera = self._sim_manager()
        manager.hardware["camera"] = Mock(spec=[])
        with self.assertLogs("hardware.hardware_manager", level="ERROR") as logs:
            manager.shutdown()
        self.assertIn("has no close() or disconnect()", "\n".join(logs.output))

    def test_hanging_camera_is_bounded(self):
        manager, mw, _camera = self._sim_manager()
        never = threading.Event()
        hanging = Mock(spec=["close"])
        hanging.close.side_effect = lambda: never.wait(30)
        manager.hardware["camera"] = hanging
        with patch.object(hardware_manager_module, "CAMERA_CLOSE_TIMEOUT_S", 0.3):
            start = time.monotonic()
            manager.shutdown()
            elapsed = time.monotonic() - start
        never.set()
        self.assertLess(elapsed, 2.0)
        self.assertFalse(mw.connected)

    def test_real_sim_app_hardware_released(self):
        from config.config_manager import ConfigManager
        config = ConfigManager("config/setupInfo.json")
        config.load()
        manager = HardwareManager(config, force_sim=True)
        manager.initialize()
        mw = manager.hardware["microwave"]
        camera = manager.hardware["camera"]
        mw.rf_on()
        manager.shutdown()
        self.assertFalse(mw.is_rf_on())
        self.assertFalse(mw.connected)
        self.assertTrue(camera.closed)


if __name__ == "__main__":
    unittest.main()
