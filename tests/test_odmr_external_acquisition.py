"""Tests for the held-open external-acquisition camera API.

No real hardware -- covers the AndorNeoAndor3 begin/grab/end contract
against a fake SDK double, and ODMR-worker-level acquisition-safety
guarantees (closed on exception, closed on user stop, camera still usable
by a Zero-Field-style caller afterward) against SimCamera.
"""

import unittest

import numpy as np
from PyQt6.QtCore import QCoreApplication, QTimer  # type: ignore

from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.camera.sim_camera import SimCamera
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer
from framework.camera_ownership import exclusive_camera_access
from gui.odmr_worker import ODMRWorker


def _ensure_qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


# =====================================================
# Fake SDK double for AndorNeoAndor3 -- mimics just the andor3.Andor3
# surface begin_/grab_/end_external_acquisition() actually call.
# =====================================================

class FakeSDKCam:
    def __init__(self, width=2, height=2, fail_wait_on_call=None):
        self.calls = []
        self._wait_call_count = 0
        self.fail_wait_on_call = fail_wait_on_call
        self._width = width
        self._height = height

    def setEnumIndex(self, feature, index):
        self.calls.append(("setEnumIndex", feature, index))

    def command(self, feature):
        self.calls.append(("command", feature))

    def queueBuffer(self, count):
        self.calls.append(("queueBuffer", count))

    def flush(self):
        self.calls.append(("flush",))

    def getInt(self, feature):
        return {
            "AOIWidth": self._width,
            "AOIHeight": self._height,
            "AOIStride": self._width * 2,
        }[feature]

    def waitBuffer(self, timeout_ms, copy=False, requeue=False):
        self._wait_call_count += 1
        self.calls.append(("waitBuffer", timeout_ms, copy, requeue))
        if self.fail_wait_on_call == self._wait_call_count:
            raise RuntimeError("simulated AT_WaitBuffer failure")
        return np.zeros(self._width * self._height * 2, dtype=np.uint8)


class AndorAcquisitionContractTests(unittest.TestCase):
    def _make_camera(self, **kwargs):
        camera = AndorNeoAndor3()
        camera.cam = FakeSDKCam(**kwargs)
        # FakeSDKCam.waitBuffer() returns instantly (no simulated exposure
        # delay); exposure_time=0 keeps the stale-buffer plausibility
        # guard (see grab_external_frame(), and
        # tests/test_pulse_streamer_single_shot.py::StaleBufferGuardTests
        # for its dedicated coverage) from firing on tests that aren't
        # about that guard.
        camera.exposure_time = 0.0
        return camera

    def test_begin_is_idempotent(self):
        camera = self._make_camera()
        camera.begin_external_acquisition()
        camera.begin_external_acquisition()
        start_calls = [c for c in camera.cam.calls if c == ("command", "AcquisitionStart")]
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(camera.get_acquisition_counts(), (1, 0))

    def test_grab_before_begin_raises(self):
        camera = self._make_camera()
        with self.assertRaises(RuntimeError):
            camera.grab_external_frame()

    def test_end_without_begin_is_a_no_op(self):
        camera = self._make_camera()
        camera.end_external_acquisition()
        self.assertEqual(camera.get_acquisition_counts(), (0, 0))

    def test_end_after_begin_closes_and_counts(self):
        camera = self._make_camera()
        camera.begin_external_acquisition()
        camera.end_external_acquisition()
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

    def test_context_manager_closes_on_exception(self):
        camera = self._make_camera()
        with self.assertRaises(ValueError):
            with camera.external_acquisition():
                self.assertTrue(camera._acquisition_open)
                raise ValueError("boom")
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

    def test_grab_failure_after_prior_success_raises_diagnostic_error(self):
        camera = self._make_camera(fail_wait_on_call=2)
        camera.begin_external_acquisition()
        camera.grab_external_frame()
        with self.assertRaisesRegex(RuntimeError, "CycleMode.*Continuous"):
            camera.grab_external_frame()

    def test_grab_failure_on_first_frame_is_not_wrapped(self):
        camera = self._make_camera(fail_wait_on_call=1)
        camera.begin_external_acquisition()
        with self.assertRaises(RuntimeError) as ctx:
            camera.grab_external_frame()
        self.assertNotIn("CycleMode", str(ctx.exception))

    def test_snap_external_frames_unchanged_signature_and_behavior(self):
        camera = self._make_camera()
        frames = camera.snap_external_frames(nframes=3, timeout_ms=1234)
        self.assertEqual(len(frames), 3)
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))
        wait_calls = [c for c in camera.cam.calls if c[0] == "waitBuffer"]
        self.assertTrue(all(c[1] == 1234 and c[2] is True and c[3] is True for c in wait_calls))


# =====================================================
# ODMRWorker-level acquisition-safety tests (sim hardware)
# =====================================================

class _FailingSimCamera(SimCamera):
    """SimCamera whose grab_external_frame() raises after N successes."""

    def __init__(self, fail_after_grabs, **kwargs):
        super().__init__(**kwargs)
        self._fail_after_grabs = fail_after_grabs
        self._grab_calls = 0

    def grab_external_frame(self, timeout_ms=10000):
        self._grab_calls += 1
        if self._grab_calls > self._fail_after_grabs:
            raise RuntimeError("injected mid-scan failure")
        return super().grab_external_frame(timeout_ms)


def _make_sim_hardware(camera):
    mw = SimMicrowave()
    mw.connect()
    ps = SimPulseStreamer()
    ps.connect()
    return {
        "camera": camera,
        "microwave": mw,
        "pulse_streamer": ps,
        "channels": {
            "greenLaser": 1, "MW": 2, "detector": 5,
            "detector2": 6, "flipMF": 3,
        },
    }


def _make_fast_config(**overrides):
    config = {
        "f_start": 2.80e9,
        "f_stop": 2.94e9,
        "steps": 3,
        "averages": 1,
        "repeats": 1,
        "mw_power_dbm": -10,
        "exposure_s": 0.001,
        "trigger_delay_s": 0.001,
        "fire_delay_s": 0.001,
        "reset_delay_s": 0.001,
        "mw_settle_s": 0.0,
        "pulse_lead_s": 0.0005,
        "pulse_tail_s": 0.0005,
        "save_data": False,
    }
    config.update(overrides)
    return config


def _run_worker_to_completion(worker, on_point=None, timeout_s=15.0):
    app = _ensure_qapp()
    state = {"finished": False}

    def on_finished():
        state["finished"] = True
        app.quit()

    worker.finished_signal.connect(on_finished)
    if on_point is not None:
        worker.point_signal.connect(on_point)

    def on_timeout():
        app.quit()

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(on_timeout)
    watchdog.start(int(timeout_s * 1000))

    QTimer.singleShot(0, worker.start)
    app.exec()

    return state["finished"]


class ODMRWorkerAcquisitionSafetyTests(unittest.TestCase):
    def test_acquisition_closed_on_exception_mid_scan(self):
        camera = _FailingSimCamera(fail_after_grabs=1, image_shape=(4, 4))
        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_config(), acquisition_roi=None)

        finished = _run_worker_to_completion(worker)

        self.assertTrue(finished)
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

        # Camera must still be usable afterward -- simulates live view
        # (snap() via the stream loop) or Zero Field resuming.
        frame = camera.snap()
        self.assertIsNotNone(frame)

    def test_acquisition_closed_on_user_stop_mid_scan(self):
        camera = SimCamera(image_shape=(4, 4))
        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_config(), acquisition_roi=None)

        points_done = {"count": 0}

        def on_point(_x, _y):
            points_done["count"] += 1
            if points_done["count"] == 1:
                worker.stop()

        finished = _run_worker_to_completion(worker, on_point=on_point)

        self.assertTrue(finished)
        self.assertGreaterEqual(points_done["count"], 1)
        self.assertFalse(camera._acquisition_open)

        frame = camera.snap()
        self.assertIsNotNone(frame)

    def test_zero_field_style_snap_after_odmr_scan_on_shared_camera(self):
        camera = SimCamera(image_shape=(4, 4))
        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_config(), acquisition_roi=None)

        finished = _run_worker_to_completion(worker)
        self.assertTrue(finished)

        # Zero Field's run() holds exclusive_camera_access and only ever
        # calls camera.snap() -- confirm that still works cleanly on the
        # same camera object right after an ODMR scan.
        with exclusive_camera_access(camera):
            frame = camera.snap()
        self.assertIsNotNone(frame)
        self.assertFalse(camera._acquisition_open)


if __name__ == "__main__":
    unittest.main()
