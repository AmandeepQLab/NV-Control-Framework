"""Tests for gating MW via channel 2 (the ZASWA-2-50DRA+ switch) instead
of per-frame VISA power writes: build_sequence()'s mw_on split,
acquire_frame()'s resulting sequence content, set_scan_point() being the
only power write per point, and SimCamera's sequence-based contrast model
(with its power-based fallback for callers that don't wire a
pulse_streamer).

Also carries two regression groups relocated here from the deleted
tests/test_mw_power_settling.py after mw_power_settle_s/
check_mw_power_settling_margin() were removed (the switch-gating change
they were kept around to allow reverting is now confirmed at the rig):
MwSettleUnaffectedTests (mw_settle_s frequency settling is a separate,
still-live knob) and FrameGapRemovedTests (frame_gap_s-absence checks,
unrelated to mw_power_settle_s and still valid).

No real hardware.
"""

import inspect
import time
import unittest

import numpy as np
from PyQt6.QtCore import QCoreApplication, QTimer  # type: ignore

from experiments.odmr_experiment import ODMRExperiment
from gui.odmr_worker import ODMRWorker
from gui.panels.odmr_panel import ODMRPanel
from hardware.camera.sim_camera import SimCamera
from hardware.hardware_manager import HardwareManager
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


def _ensure_qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


CHANNELS = {
    "greenLaser": 1, "MW": 2, "detector": 5,
    "detector2": 6, "flipMF": 3,
}


class RecordingMicrowave(SimMicrowave):
    def __init__(self):
        super().__init__()
        self.power_calls = []

    def set_power(self, power_dbm):
        super().set_power(power_dbm)
        self.power_calls.append(power_dbm)


class RecordingPulseStreamer(SimPulseStreamer):
    """Records the exact Sequence object loaded for every frame, in order."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loaded_sequences = []

    def load_sequence(self, sequence):
        self.loaded_sequences.append(sequence)
        super().load_sequence(sequence)


def _make_hardware():
    mw = RecordingMicrowave()
    mw.connect()
    pulse = RecordingPulseStreamer(channel_map=CHANNELS)
    pulse.connect()
    camera = SimCamera(image_shape=(2, 2))
    camera.exposure_time = 0.001
    return {
        "camera": camera,
        "microwave": mw,
        "pulse_streamer": pulse,
        "channels": CHANNELS,
    }, mw, pulse


def _make_config(**overrides):
    config = {
        "mw_power_dbm": -10,
        "repeats": 1,
        "exposure_s": 0.001,
        "trigger_delay_s": 0.001,
        "fire_delay_s": 0.001,
        "reset_delay_s": 0.001,
        "pulse_lead_s": 0.0005,
        "pulse_tail_s": 0.0005,
    }
    config.update(overrides)
    return config


def _has_mw_pulse(sequence, mw_channel=CHANNELS["MW"]):
    return any(p.channel == mw_channel and p.state for p in sequence.digital_pulses)


# =====================================================
# build_sequence(): the mechanism -- the test named explicitly in the plan
# =====================================================

class BuildSequenceMwOnTests(unittest.TestCase):
    def test_mw_on_false_has_no_channel_2_pulse(self):
        hardware, _mw, _pulse = _make_hardware()
        experiment = ODMRExperiment(hardware, _make_config())

        seq = experiment.build_sequence(mw_on=False)

        self.assertFalse(_has_mw_pulse(seq))

    def test_mw_on_true_has_channel_2_pulse(self):
        hardware, _mw, _pulse = _make_hardware()
        experiment = ODMRExperiment(hardware, _make_config())

        seq = experiment.build_sequence(mw_on=True)

        self.assertTrue(_has_mw_pulse(seq))

    def test_default_matches_mw_on_true(self):
        hardware, _mw, _pulse = _make_hardware()
        experiment = ODMRExperiment(hardware, _make_config())

        seq = experiment.build_sequence()

        self.assertTrue(_has_mw_pulse(seq))


# =====================================================
# acquire_frame(): the real OFF/ON sequences it dispatches
# =====================================================

class AcquireFrameSequenceContentTests(unittest.TestCase):
    def test_off_sequence_has_no_mw_pulse_on_sequence_does(self):
        hardware, _mw, pulse = _make_hardware()
        experiment = ODMRExperiment(hardware, _make_config(repeats=3))

        experiment.acquire_frame()

        self.assertEqual(len(pulse.loaded_sequences), 6)  # 3 repeats x (off, on)
        off_sequences = pulse.loaded_sequences[0::2]
        on_sequences = pulse.loaded_sequences[1::2]

        for seq in off_sequences:
            self.assertFalse(_has_mw_pulse(seq))
        for seq in on_sequences:
            self.assertTrue(_has_mw_pulse(seq))

    def test_exactly_one_set_power_call_per_point(self):
        """The source sits at working power for the whole point --
        set_scan_point() is the only place set_power() is called now."""
        hardware, mw, _pulse = _make_hardware()
        experiment = ODMRExperiment(hardware, _make_config(repeats=5))

        experiment.acquire_configured_point(2.87e9)

        self.assertEqual(mw.power_calls, [-10])


# =====================================================
# set_scan_point(): mw_settle_s (frequency settling) is a separate knob
# from the removed mw_power_settle_s/check_mw_power_settling_margin()
# machinery and must stay unaffected by switch gating -- relocated from
# the now-deleted tests/test_mw_power_settling.py.
# =====================================================

class MwSettleUnaffectedTests(unittest.TestCase):
    """mw_settle_s (frequency settling, set_scan_point) is untouched by
    switch gating: same call order (frequency, one power write, rf_on),
    no sleep injected beyond mw_settle_s itself."""

    def test_set_scan_point_call_order_and_timing_unchanged(self):
        hardware, mw, _pulse = _make_hardware()
        config = _make_config(mw_settle_s=0.0)
        experiment = ODMRExperiment(hardware, config)

        t0 = time.perf_counter()
        experiment.set_scan_point(2.87e9)
        elapsed = time.perf_counter() - t0

        self.assertEqual(mw.frequency, 2.87e9)
        self.assertEqual(mw.power_calls, [-10])
        self.assertTrue(mw.rf_enabled)
        self.assertLess(elapsed, 0.03)


# =====================================================
# frame_gap_s absence regressions -- relocated from the now-deleted
# tests/test_mw_power_settling.py (their mw_power_settle_s-specific
# assertions were dropped along with that feature).
# =====================================================

class FrameGapRemovedTests(unittest.TestCase):
    def test_panel_has_no_frame_gap_control(self):
        # Source inspection, not live instantiation: ODMRPanel is a real
        # QWidget (contains a pyqtgraph PlotWidget) and this test suite
        # deliberately never constructs GUI widgets outside a running
        # application -- reading the class source is enough to verify
        # frame_gap_s is gone, without needing a display/platform plugin.
        panel_source = inspect.getsource(ODMRPanel)
        self.assertNotIn("frame_gap", panel_source)

        config_source = inspect.getsource(ODMRPanel.get_config)
        self.assertNotIn("frame_gap_s", config_source)

    def test_preview_sequence_builds_without_frame_gap_s(self):
        hardware, _mw, _pulse = _make_hardware()
        config = _make_config(reset_delay_s=0.005, fire_delay_s=0.005)
        self.assertNotIn("frame_gap_s", config)
        experiment = ODMRExperiment(hardware, config)

        sequence = experiment.build_repeated_off_on_sequence(repeats=2)

        self.assertGreater(len(sequence.digital_pulses), 0)


# =====================================================
# SimCamera: sequence-based MW state, and the power-based fallback
# =====================================================

class SimCameraMwStateTests(unittest.TestCase):
    def test_asserted_when_wired_sequence_has_mw_pulse(self):
        mw = SimMicrowave()
        mw.connect()
        mw.set_power(-10)
        pulse = SimPulseStreamer(channel_map=CHANNELS)
        camera = SimCamera(microwave=mw, pulse_streamer=pulse)

        experiment_hw = {"channels": CHANNELS}
        experiment = ODMRExperiment(experiment_hw, _make_config())
        pulse.load_sequence(experiment.build_sequence(mw_on=True))

        self.assertTrue(camera._mw_asserted())

    def test_not_asserted_when_wired_sequence_has_no_mw_pulse(self):
        mw = SimMicrowave()
        mw.connect()
        mw.set_power(-10)  # power alone must NOT drive this when wired
        pulse = SimPulseStreamer(channel_map=CHANNELS)
        camera = SimCamera(microwave=mw, pulse_streamer=pulse)

        experiment_hw = {"channels": CHANNELS}
        experiment = ODMRExperiment(experiment_hw, _make_config())
        pulse.load_sequence(experiment.build_sequence(mw_on=False))

        self.assertFalse(camera._mw_asserted())

    def test_not_asserted_when_wired_but_no_sequence_loaded_yet(self):
        pulse = SimPulseStreamer(channel_map=CHANNELS)
        camera = SimCamera(pulse_streamer=pulse)

        self.assertFalse(camera._mw_asserted())

    def test_falls_back_to_power_when_no_pulse_streamer_wired(self):
        mw = SimMicrowave()
        mw.connect()
        camera = SimCamera(microwave=mw)  # no pulse_streamer

        mw.set_power(-10)
        self.assertTrue(camera._mw_asserted())

        mw.set_power(-100)
        self.assertFalse(camera._mw_asserted())


class HardwareManagerWiringTests(unittest.TestCase):
    def test_sim_branch_wires_camera_to_pulse_streamer(self):
        """HardwareManager.initialize() has heavy real/sim device
        construction (magnet, power supplies) not exercised by the safe
        suite anywhere else -- source inspection directly verifies the
        one line this feature depends on, rather than constructing the
        whole manager."""
        source = inspect.getsource(HardwareManager)
        self.assertIn("camera.pulse_streamer = pulse", source)


# =====================================================
# End-to-end: sim ODMR scan still produces a visible Lorentzian dip
# =====================================================

def _run_worker_to_completion(worker, timeout_s=15.0):
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


class SimLorentzianDipSurvivesTests(unittest.TestCase):
    def test_sim_scan_still_shows_a_dip(self):
        mw = SimMicrowave()
        mw.connect()
        pulse = SimPulseStreamer(channel_map=CHANNELS)
        pulse.connect()
        camera = SimCamera(image_shape=(4, 4), microwave=mw, pulse_streamer=pulse)
        camera.exposure_time = 0.001
        camera.resonance_freq = 2.87e9
        camera.linewidth = 5e6
        camera.contrast = 0.05

        hardware = {
            "camera": camera, "microwave": mw, "pulse_streamer": pulse,
            "channels": CHANNELS,
        }
        config = {
            "f_start": 2.85e9, "f_stop": 2.89e9, "steps": 9,
            "averages": 1, "repeats": 3,
            "mw_power_dbm": -10,
            "exposure_s": 0.001,
            "trigger_delay_s": 0.001,
            "fire_delay_s": 0.001,
            "reset_delay_s": 0.001,
            "pulse_lead_s": 0.0005,
            "pulse_tail_s": 0.0005,
            "save_data": False,
        }

        worker = ODMRWorker(hardware, config, acquisition_roi=None)
        finished = _run_worker_to_completion(worker)

        self.assertTrue(finished)
        results = np.asarray(worker.results)
        self.assertFalse(np.any(np.isnan(results)))

        # A real dip: the point nearest resonance must read visibly lower
        # than the points at the sweep edges.
        freqs = worker.freqs_sorted
        center_index = int(np.argmin(np.abs(freqs - camera.resonance_freq)))
        edge_value = max(results[0], results[-1])
        self.assertLess(results[center_index], edge_value - 1.0)


if __name__ == "__main__":
    unittest.main()
