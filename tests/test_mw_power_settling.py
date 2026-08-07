"""Tests for the MW power settling delay (mw_power_settle_s) and the
startup margin warning, and for frame_gap_s having been fully removed.

No real hardware -- SimMicrowave/SimPulseStreamer/SimCamera doubles only.
"""

import inspect
import logging
import time
import unittest

from experiments.odmr_experiment import (
    ODMRExperiment,
    LOGGER as ODMR_LOGGER,
    SG386_AMPLITUDE_SETTLE_S,
)
from gui.odmr_window import ODMRWindow
from gui.panels.odmr_panel import ODMRPanel
from hardware.camera.sim_camera import SimCamera
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


CHANNELS = {
    "greenLaser": 1, "MW": 2, "detector": 5,
    "detector2": 6, "flipMF": 3,
}


class RecordingMicrowave(SimMicrowave):
    """Records the wall-clock time each set_power() call returns."""

    def __init__(self):
        super().__init__()
        self.power_calls = []  # list of (power_dbm, t_returned)

    def set_power(self, power_dbm):
        super().set_power(power_dbm)
        self.power_calls.append((power_dbm, time.perf_counter()))


class RecordingPulseStreamer(SimPulseStreamer):
    """Records the wall-clock time each load_sequence() call starts --
    load_sequence() is acquire_triggered_frame()'s first action, so this
    marks how soon after a power write the next frame's dispatch begins."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.load_sequence_calls = []  # list of t_start

    def load_sequence(self, sequence):
        self.load_sequence_calls.append(time.perf_counter())
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


class SettlingDelayPlacementTests(unittest.TestCase):
    """Part (a)/(b): the sleep sits between set_power() and the next
    frame's dispatch, and applies the same way to both OFF and ON."""

    def test_delay_elapses_between_power_write_and_next_dispatch(self):
        hardware, mw, pulse = _make_hardware()
        settle_s = 0.05
        config = _make_config(mw_power_settle_s=settle_s)
        experiment = ODMRExperiment(hardware, config)

        experiment.acquire_frame()

        self.assertEqual(len(mw.power_calls), 2)  # OFF, ON
        self.assertEqual(len(pulse.load_sequence_calls), 2)

        tolerance = 0.01  # timer jitter margin
        for (power, t_write), t_dispatch in zip(
            mw.power_calls, pulse.load_sequence_calls
        ):
            self.assertGreaterEqual(
                t_dispatch - t_write, settle_s - tolerance,
                f"settling delay too short after set_power({power})",
            )

    def test_off_and_on_writes_get_identical_treatment(self):
        """Part (b): symmetric code path -- same delay after both the
        90 dB-down OFF write and the 90 dB-up ON write."""
        hardware, mw, pulse = _make_hardware()
        settle_s = 0.04
        config = _make_config(mw_power_settle_s=settle_s, repeats=2)
        experiment = ODMRExperiment(hardware, config)

        experiment.acquire_frame()

        self.assertEqual(len(mw.power_calls), 4)  # 2 repeats x (OFF, ON)
        powers = [p for p, _ in mw.power_calls]
        self.assertEqual(powers, [-100, -10, -100, -10])

        tolerance = 0.01
        deltas = [
            t_dispatch - t_write
            for (_, t_write), t_dispatch in zip(
                mw.power_calls, pulse.load_sequence_calls
            )
        ]
        for delta in deltas:
            self.assertGreaterEqual(delta, settle_s - tolerance)


class SettlingDelayConfigTests(unittest.TestCase):
    """Part (c): default 0.0 (no-op), explicit value overrides it."""

    def test_default_is_zero_and_adds_no_delay(self):
        hardware, mw, pulse = _make_hardware()
        config = _make_config()  # no mw_power_settle_s key at all
        experiment = ODMRExperiment(hardware, config)

        experiment.acquire_frame()

        generous_tolerance = 0.03
        for (_, t_write), t_dispatch in zip(
            mw.power_calls, pulse.load_sequence_calls
        ):
            self.assertLess(t_dispatch - t_write, generous_tolerance)

    def test_explicit_value_is_honored(self):
        hardware, mw, pulse = _make_hardware()
        config = _make_config(mw_power_settle_s=0.03)
        experiment = ODMRExperiment(hardware, config)

        experiment.acquire_frame()

        tolerance = 0.01
        for (_, t_write), t_dispatch in zip(
            mw.power_calls, pulse.load_sequence_calls
        ):
            self.assertGreaterEqual(t_dispatch - t_write, 0.03 - tolerance)


class MwSettleUnaffectedTests(unittest.TestCase):
    """mw_settle_s (frequency settling, set_scan_point) must be untouched:
    same call order, no new sleep injected between its power write and
    rf_on()."""

    def test_set_scan_point_call_order_and_timing_unchanged(self):
        hardware, mw, _pulse = _make_hardware()
        config = _make_config(mw_settle_s=0.0, mw_power_settle_s=0.05)
        experiment = ODMRExperiment(hardware, config)

        experiment.set_scan_point(2.87e9)

        self.assertEqual(mw.frequency, 2.87e9)
        self.assertEqual(len(mw.power_calls), 1)
        self.assertTrue(mw.rf_enabled)

        # No mw_power_settle_s-sized gap should appear between the power
        # write in set_scan_point() and rf_on() -- that sleep only exists
        # in acquire_frame(), which set_scan_point() never calls.
        _, t_power = mw.power_calls[0]
        self.assertLess(time.perf_counter() - t_power, 0.03)


class FrameGapRemovedTests(unittest.TestCase):
    """Part (d): frame_gap_s is gone from the panel, get_config(), the
    pulse-sequence preview, and the GUI time estimate."""

    def test_panel_has_no_frame_gap_control(self):
        # Source inspection, not live instantiation: ODMRPanel is a real
        # QWidget (contains a pyqtgraph PlotWidget) and this test suite
        # deliberately never constructs GUI widgets outside a running
        # application (every other test here uses QCoreApplication/QObject
        # doubles only) -- reading the class source is enough to verify
        # frame_gap_s is gone and mw_power_settle_s is wired into
        # get_config(), without needing a display/platform plugin.
        panel_source = inspect.getsource(ODMRPanel)
        self.assertNotIn("frame_gap", panel_source)
        self.assertIn("mw_power_settle_spin", panel_source)

        config_source = inspect.getsource(ODMRPanel.get_config)
        self.assertNotIn("frame_gap_s", config_source)
        self.assertIn('"mw_power_settle_s"', config_source)

    def test_preview_sequence_builds_without_frame_gap_s(self):
        hardware, _mw, _pulse = _make_hardware()
        config = _make_config(reset_delay_s=0.005, fire_delay_s=0.005)
        self.assertNotIn("frame_gap_s", config)
        experiment = ODMRExperiment(hardware, config)

        sequence = experiment.build_repeated_off_on_sequence(repeats=2)

        self.assertGreater(len(sequence.digital_pulses), 0)

    def test_time_estimate_runs_without_frame_gap_s_and_scales_with_settle(self):
        config = _make_config(
            f_start=2.80e9, f_stop=2.94e9, steps=3, averages=1, repeats=5,
            mw_power_settle_s=0.0,
        )
        self.assertNotIn("frame_gap_s", config)

        baseline = ODMRWindow.estimate_odmr_time(None, config)

        config_with_settle = dict(config, mw_power_settle_s=0.01)
        with_settle = ODMRWindow.estimate_odmr_time(None, config_with_settle)

        # 2x per repeat, per point (part e).
        n_points = 3
        repeats = 5
        expected_added = n_points * 1 * repeats * 2 * 0.01
        self.assertAlmostEqual(
            with_settle - baseline, expected_added, places=6
        )


class MarginWarningTests(unittest.TestCase):
    """Startup margin warning: fires below the SG386 spec, silent above."""

    def _collect_warnings(self, experiment):
        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Collector()
        prev_level = ODMR_LOGGER.level
        ODMR_LOGGER.addHandler(handler)
        ODMR_LOGGER.setLevel(logging.WARNING)
        try:
            experiment.check_mw_power_settling_margin()
        finally:
            ODMR_LOGGER.removeHandler(handler)
            ODMR_LOGGER.setLevel(prev_level)

        return [r for r in records if r.levelno >= logging.WARNING]

    def test_warns_when_margin_below_spec(self):
        hardware = {"channels": CHANNELS}
        config = {
            "reset_delay_s": 0.0,
            "fire_delay_s": 0.0,
            "trigger_delay_s": 0.0,
            "pulse_lead_s": 0.0,
            "mw_power_settle_s": 0.0,
        }
        experiment = ODMRExperiment(hardware, config)

        warnings = self._collect_warnings(experiment)

        self.assertEqual(len(warnings), 1)
        message = warnings[0].getMessage()
        self.assertIn("SG386", message)
        self.assertIn("8.0 ms", message)

    def test_silent_when_margin_meets_spec(self):
        hardware = {"channels": CHANNELS}
        config = {
            "reset_delay_s": 0.005,
            "fire_delay_s": 0.005,
            "trigger_delay_s": 0.05,
            "pulse_lead_s": 0.002,
            "mw_power_settle_s": 0.0,
        }
        experiment = ODMRExperiment(hardware, config)
        self.assertGreaterEqual(
            0.005 + 0.005 + 0.05 + 0.002 + 0.0, SG386_AMPLITUDE_SETTLE_S
        )

        warnings = self._collect_warnings(experiment)

        self.assertEqual(warnings, [])

    def test_silent_exactly_at_threshold(self):
        hardware = {"channels": CHANNELS}
        config = {
            "reset_delay_s": SG386_AMPLITUDE_SETTLE_S,
            "fire_delay_s": 0.0,
            "trigger_delay_s": 0.0,
            "pulse_lead_s": 0.0,
            "mw_power_settle_s": 0.0,
        }
        experiment = ODMRExperiment(hardware, config)

        warnings = self._collect_warnings(experiment)

        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
