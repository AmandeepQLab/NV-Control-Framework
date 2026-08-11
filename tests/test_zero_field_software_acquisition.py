"""Tests for the held-open software-acquisition camera API (Zero Field).

No real hardware -- covers the AndorNeoAndor3 begin/grab/end_software_*
contract against a fake SDK double (mirroring
tests/test_odmr_external_acquisition.py's FakeSDKCam), mode-coexistence
with the existing external trio, and ZeroFieldExperiment-level
acquisition-safety guarantees (closed on exception, closed on user stop,
camera still usable afterward, no leakage between a Zero Field and an ODMR
scan sharing one camera) against SimCamera.
"""

import unittest

import numpy as np
from PyQt6.QtCore import QCoreApplication, QTimer  # type: ignore

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.camera_ownership import exclusive_camera_access
from gui.odmr_worker import ODMRWorker
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.camera.sim_camera import SimCamera
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


def _ensure_qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


# =====================================================
# Fake SDK double for AndorNeoAndor3 -- mirrors
# test_odmr_external_acquisition.py::FakeSDKCam.
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


def _make_camera(**kwargs):
    camera = AndorNeoAndor3()
    camera.cam = FakeSDKCam(**kwargs)
    # FakeSDKCam.waitBuffer() returns instantly (no simulated exposure
    # delay); exposure_time=0 keeps the stale-buffer plausibility guard
    # from firing on tests that aren't about that guard.
    camera.exposure_time = 0.0
    return camera


class SoftwareAcquisitionContractTests(unittest.TestCase):
    def test_begin_is_idempotent(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        camera.begin_software_acquisition()
        start_calls = [c for c in camera.cam.calls if c == ("command", "AcquisitionStart")]
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(camera.get_acquisition_counts(), (1, 0))
        self.assertEqual(camera._acquisition_mode, "software")

    def test_grab_before_begin_raises(self):
        camera = _make_camera()
        with self.assertRaises(RuntimeError):
            camera.grab_software_frame()

    def test_end_without_begin_is_a_no_op(self):
        camera = _make_camera()
        camera.end_software_acquisition()
        self.assertEqual(camera.get_acquisition_counts(), (0, 0))

    def test_end_after_begin_closes_and_counts(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        camera.end_software_acquisition()
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

    def test_context_manager_closes_on_exception(self):
        camera = _make_camera()
        with self.assertRaises(ValueError):
            with camera.software_acquisition():
                self.assertTrue(camera._acquisition_open)
                raise ValueError("boom")
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

    def test_grab_issues_software_trigger_then_waits_with_copy_and_requeue(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        camera.grab_software_frame(timeout_ms=1234)

        trigger_calls = [c for c in camera.cam.calls if c == ("command", "SoftwareTrigger")]
        self.assertEqual(len(trigger_calls), 1)
        wait_calls = [c for c in camera.cam.calls if c[0] == "waitBuffer"]
        self.assertEqual(len(wait_calls), 1)
        self.assertEqual(wait_calls[0], ("waitBuffer", 1234, True, True))

        trigger_index = camera.cam.calls.index(("command", "SoftwareTrigger"))
        wait_index = camera.cam.calls.index(wait_calls[0])
        self.assertLess(trigger_index, wait_index)

    def test_no_extra_queue_buffer_between_grabs(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        camera.grab_software_frame()
        camera.grab_software_frame()
        camera.grab_software_frame()

        queue_calls = [c for c in camera.cam.calls if c[0] == "queueBuffer"]
        # One queueBuffer from begin_software_acquisition() only --
        # waitBuffer(requeue=True) re-arms the same buffer for every
        # subsequent SoftwareTrigger; no per-frame queueBuffer() call.
        self.assertEqual(len(queue_calls), 1)

    def test_grab_failure_after_prior_success_names_cyclemode_assumption(self):
        camera = _make_camera(fail_wait_on_call=2)
        camera.begin_software_acquisition()
        camera.grab_software_frame()
        with self.assertRaisesRegex(
            RuntimeError, "CycleMode.*Continuous.*software-trigger"
        ):
            camera.grab_software_frame()

    def test_grab_failure_on_first_frame_is_not_wrapped(self):
        camera = _make_camera(fail_wait_on_call=1)
        camera.begin_software_acquisition()
        with self.assertRaises(RuntimeError) as ctx:
            camera.grab_software_frame()
        self.assertNotIn("CycleMode", str(ctx.exception))


class ModeCoexistenceTests(unittest.TestCase):
    """Only one of external/software can be open at a time on the real
    driver -- opening the other side must defensively close whatever is
    currently open, never leave both flagged open."""

    def test_begin_external_while_software_open_closes_software_first(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        self.assertEqual(camera._acquisition_mode, "software")

        camera.begin_external_acquisition()

        self.assertEqual(camera._acquisition_mode, "external")
        stop_calls = [c for c in camera.cam.calls if c == ("command", "AcquisitionStop")]
        start_indices = [
            i for i, c in enumerate(camera.cam.calls) if c == ("command", "AcquisitionStart")
        ]
        self.assertEqual(len(stop_calls), 1)
        self.assertEqual(len(start_indices), 2)
        stop_index = camera.cam.calls.index(stop_calls[0])
        self.assertLess(stop_index, start_indices[1])

    def test_begin_software_while_external_open_closes_external_first(self):
        camera = _make_camera()
        camera.begin_external_acquisition()
        self.assertEqual(camera._acquisition_mode, "external")

        camera.begin_software_acquisition()

        self.assertEqual(camera._acquisition_mode, "software")
        stop_calls = [c for c in camera.cam.calls if c == ("command", "AcquisitionStop")]
        self.assertEqual(len(stop_calls), 1)

    def test_snap_closes_an_open_software_acquisition_first(self):
        camera = _make_camera()
        camera.begin_software_acquisition()
        self.assertTrue(camera._acquisition_open)

        frame = camera.snap()

        self.assertIsNotNone(frame)
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)

    def test_end_external_acquisition_unchanged_after_refactor(self):
        # Re-exercises the pre-existing external contract to confirm the
        # _end_held_open_acquisition() extraction didn't change
        # end_external_acquisition()'s own observable behavior.
        camera = _make_camera()
        camera.begin_external_acquisition()
        camera.end_external_acquisition()
        self.assertFalse(camera._acquisition_open)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))


# =====================================================
# ZeroFieldExperiment-level acquisition-safety tests (sim hardware)
# =====================================================

class FakeMagnet:
    def enable(self):
        pass

    def disable(self):
        pass

    def get_vector(self):
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    def set_vector(self, **_fields):
        pass


class _FailingSimCamera(SimCamera):
    """SimCamera whose grab_software_frame() raises after N successes."""

    def __init__(self, fail_after_grabs, **kwargs):
        super().__init__(**kwargs)
        self._fail_after_grabs = fail_after_grabs
        self._grab_calls = 0

    def grab_software_frame(self, timeout_ms=10000):
        self._grab_calls += 1
        if self._grab_calls > self._fail_after_grabs:
            raise RuntimeError("injected mid-scan failure")
        return super().grab_software_frame(timeout_ms)


def _make_zfe(camera, magnet=None, **overrides):
    kwargs = dict(
        hardware_manager=None,
        camera=camera,
        magnet=magnet if magnet is not None else FakeMagnet(),
        field_start=-1.0,
        field_stop=1.0,
        field_points=3,
        field_axis="X",
        settling_time_ms=0,
        averages=1,
    )
    kwargs.update(overrides)
    return ZeroFieldExperiment(**kwargs)


class ZeroFieldAcquisitionSafetyTests(unittest.TestCase):
    def test_full_run_arms_and_disarms_exactly_once(self):
        camera = SimCamera(image_shape=(2, 2))
        experiment = _make_zfe(camera, field_points=5, averages=3)

        experiment.run()

        # The number the rig test checks: one AcquisitionStart, one
        # AcquisitionStop for the whole run, regardless of field_points or
        # averages -- not once per frame.
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))
        self.assertFalse(camera._acquisition_open)

    def test_multi_scan_run_still_arms_once(self):
        camera = SimCamera(image_shape=(2, 2))
        experiment = _make_zfe(
            camera, field_points=2, averaging_enabled=True, num_scans=3,
        )

        experiment.run()

        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

    def test_acquisition_closed_on_exception_mid_scan(self):
        camera = _FailingSimCamera(fail_after_grabs=1, image_shape=(2, 2))
        experiment = _make_zfe(camera, field_points=5)

        with self.assertRaisesRegex(RuntimeError, "injected mid-scan failure"):
            experiment.run()

        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)
        self.assertEqual(camera.get_acquisition_counts(), (1, 1))

        # Camera must still be usable afterward -- simulates live view or
        # a subsequent scan resuming.
        frame = camera.snap()
        self.assertIsNotNone(frame)

    def test_acquisition_closed_on_user_stop_mid_scan(self):
        camera = SimCamera(image_shape=(2, 2))
        holder = {}
        experiment = _make_zfe(
            camera, field_points=5,
            live_update_callback=lambda field, image: holder["experiment"].stop(),
        )
        holder["experiment"] = experiment

        cube = experiment.run()

        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)
        self.assertLess(cube.data.shape[0], 5)

        frame = camera.snap()
        self.assertIsNotNone(frame)

    def test_fallback_to_snap_for_camera_lacking_new_api(self):
        # Mirrors FakeCamera from test_zero_field_averaging.py: implements
        # only snap(), no held-open API at all.
        class BareCamera:
            def __init__(self, frames):
                self.frames = iter(frames)
                self.roi = None

            def snap(self):
                return np.asarray(next(self.frames))

            def set_roi(self, roi):
                self.roi = roi

        frames = [np.full((2, 2), v) for v in (1.0, 2.0, 3.0)]
        camera = BareCamera(frames)
        experiment = _make_zfe(camera, field_points=3, averages=1)

        cube = experiment.run()

        self.assertEqual(cube.data.shape[0], 3)
        self.assertFalse(experiment._acquisition_open)

    def test_no_behavior_change_between_held_open_and_snap_fallback(self):
        np.random.seed(99)
        held_open_camera = SimCamera(image_shape=(2, 2))
        held_open_experiment = _make_zfe(held_open_camera, field_points=3, averages=1)
        held_open_cube = held_open_experiment.run()

        class SnapOnlyCamera(SimCamera):
            """Same frame-generation physics as SimCamera, but with the
            held-open API genuinely absent (not just set to None -- that
            would still pass hasattr() and then fail with a confusing
            TypeError) so acquire_frame()'s hasattr guard actually falls
            back to plain snap()."""

            _HIDDEN = frozenset({
                "begin_software_acquisition",
                "grab_software_frame",
                "end_software_acquisition",
                "software_acquisition",
            })

            def __getattribute__(self, name):
                if name in SnapOnlyCamera._HIDDEN:
                    raise AttributeError(name)
                return super().__getattribute__(name)

        np.random.seed(99)
        snap_only_camera = SnapOnlyCamera(image_shape=(2, 2))
        self.assertFalse(hasattr(snap_only_camera, "grab_software_frame"))
        snap_only_experiment = _make_zfe(snap_only_camera, field_points=3, averages=1)
        snap_only_cube = snap_only_experiment.run()

        np.testing.assert_array_equal(held_open_cube.data, snap_only_cube.data)


# =====================================================
# Zero-Field-vs-ODMR coexistence on a shared SimCamera
# =====================================================

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


def _make_fast_odmr_config(**overrides):
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


def _run_odmr_worker_to_completion(worker, timeout_s=15.0):
    app = _ensure_qapp()
    state = {"finished": False}

    def on_finished():
        state["finished"] = True
        app.quit()

    worker.finished_signal.connect(on_finished)

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(app.quit)
    watchdog.start(int(timeout_s * 1000))

    QTimer.singleShot(0, worker.start)
    app.exec()

    return state["finished"]


class ZeroFieldOdmrCoexistenceTests(unittest.TestCase):
    def test_zero_field_then_odmr_on_shared_camera(self):
        camera = SimCamera(image_shape=(4, 4))

        zfe = _make_zfe(camera, field_points=3, averages=1)
        zfe_cube = zfe.run()
        self.assertEqual(zfe_cube.data.shape[0], 3)
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)

        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_odmr_config(), acquisition_roi=None)
        finished = _run_odmr_worker_to_completion(worker)

        self.assertTrue(finished)
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)

    def test_odmr_then_zero_field_on_shared_camera(self):
        camera = SimCamera(image_shape=(4, 4))

        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_odmr_config(), acquisition_roi=None)
        finished = _run_odmr_worker_to_completion(worker)
        self.assertTrue(finished)
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)

        zfe = _make_zfe(camera, field_points=3, averages=1)
        zfe_cube = zfe.run()

        self.assertEqual(zfe_cube.data.shape[0], 3)
        self.assertFalse(camera._acquisition_open)
        self.assertIsNone(camera._acquisition_mode)

    def test_zero_field_style_snap_after_odmr_scan_on_shared_camera(self):
        # Mirrors test_odmr_external_acquisition.py's own version of this
        # test, confirming plain snap() (not the held-open path) still
        # works cleanly after an ODMR scan -- unaffected by this change.
        camera = SimCamera(image_shape=(4, 4))
        hardware = _make_sim_hardware(camera)
        worker = ODMRWorker(hardware, _make_fast_odmr_config(), acquisition_roi=None)

        finished = _run_odmr_worker_to_completion(worker)
        self.assertTrue(finished)

        with exclusive_camera_access(camera):
            frame = camera.snap()
        self.assertIsNotNone(frame)
        self.assertFalse(camera._acquisition_open)


if __name__ == "__main__":
    unittest.main()
