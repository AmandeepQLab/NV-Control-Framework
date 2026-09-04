"""Tests for the camera baseline (dark-count) offset: process_frame()'s
arithmetic and guards, AcquisitionState plumbing, and saved metadata.

No real hardware -- process_frame() only operates on numpy arrays handed
to it directly, so these construct ODMRExperiment with a minimal hardware
dict rather than any camera/pulse-streamer double.
"""

import inspect
import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.odmr_experiment import ODMRExperiment, LOGGER as ODMR_LOGGER
from framework.acquisition_state import AcquisitionState
from gui.main_window import MainWindow
from data.data_manager import DataManager


def _make_experiment(**config_overrides):
    return ODMRExperiment(hardware={}, config=dict(config_overrides))


def _pair_frame(i_off, i_on, shape=(2, 2)):
    """One OFF/ON pair, ndim==3 -- process_frame() treats this as a single
    repeat (see the frame[np.newaxis, ...] branch)."""
    return np.stack([np.full(shape, i_off, dtype=float), np.full(shape, i_on, dtype=float)])


def _repeats_frame(pairs):
    """Stack of (i_off, i_on) tuples into an ndim==4 multi-repeat frame."""
    return np.stack([_pair_frame(i_off, i_on) for i_off, i_on in pairs])


def _collect_warnings(fn):
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector()
    prev_level = ODMR_LOGGER.level
    ODMR_LOGGER.addHandler(handler)
    ODMR_LOGGER.setLevel(logging.WARNING)
    try:
        fn()
    finally:
        ODMR_LOGGER.removeHandler(handler)
        ODMR_LOGGER.setLevel(prev_level)
    return [r for r in records if r.levelno >= logging.WARNING]


# =====================================================
# Arithmetic: disabled at default, known baseline
# =====================================================

class BaselineArithmeticTests(unittest.TestCase):
    def test_default_baseline_is_exact_no_op(self):
        """No baseline_counts key at all -- must match today's formula
        exactly, not just approximately."""
        experiment = _make_experiment()
        frame = _pair_frame(i_off=1000.0, i_on=1100.0)

        result = experiment.process_frame(frame)

        self.assertEqual(result, 100.0 * 1100.0 / 1000.0)

    def test_explicit_zero_baseline_is_exact_no_op(self):
        experiment = _make_experiment(baseline_counts=0.0)
        frame = _pair_frame(i_off=1000.0, i_on=1100.0)

        result = experiment.process_frame(frame)

        self.assertEqual(result, 100.0 * 1100.0 / 1000.0)

    def test_known_baseline_produces_predicted_contrast(self):
        i_off, i_on, b = 1000.0, 1100.0, 120.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=i_off, i_on=i_on)

        result = experiment.process_frame(frame)

        expected = 100.0 * (i_on - b) / (i_off - b)
        self.assertAlmostEqual(result, expected)
        # and it must differ from the uncorrected value by the amount the
        # rationale's own dilution formula predicts
        uncorrected = 100.0 * i_on / i_off
        self.assertNotAlmostEqual(result, uncorrected)

    def test_raw_i_off_i_on_stay_uncorrected(self):
        """The deliberate split: signal is baseline-corrected, the raw
        traces are not."""
        i_off, i_on, b = 1000.0, 1100.0, 120.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=i_off, i_on=i_on)

        experiment.process_frame(frame)

        self.assertEqual(experiment._last_i_off, i_off)
        self.assertEqual(experiment._last_i_on, i_on)


# =====================================================
# Guards
# =====================================================

class BaselineGuardTests(unittest.TestCase):
    def test_s_off_at_or_below_zero_raises_with_baseline_and_i_off(self):
        b = 120.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=100.0, i_on=500.0)  # I_off < baseline

        with self.assertRaises(RuntimeError) as ctx:
            experiment.process_frame(frame)

        message = str(ctx.exception)
        self.assertIn(f"{b:.2f}", message)
        self.assertIn("100.00", message)

    def test_s_off_exactly_equal_to_baseline_raises(self):
        b = 120.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=b, i_on=500.0)  # S_off == 0

        with self.assertRaises(RuntimeError):
            experiment.process_frame(frame)

    def test_margin_warning_fires_once_per_multi_repeat_call(self):
        b = 100.0
        # S_off = 50, which is < b -- marginal on every repeat
        experiment = _make_experiment(baseline_counts=b)
        frame = _repeats_frame([(150.0, 400.0)] * 5)

        warnings = _collect_warnings(lambda: experiment.process_frame(frame))

        self.assertEqual(len(warnings), 1)

    def test_margin_warning_fires_once_across_multiple_points_in_a_scan(self):
        """'Once per scan, not per frame' -- across separate
        process_frame() calls (separate points), not just within one."""
        b = 100.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=150.0, i_on=400.0)

        warnings = _collect_warnings(lambda: [
            experiment.process_frame(frame) for _ in range(4)
        ])

        self.assertEqual(len(warnings), 1)

    def test_margin_warning_can_fire_again_after_scan_reset(self):
        b = 100.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=150.0, i_on=400.0)

        first_scan = _collect_warnings(lambda: experiment.process_frame(frame))
        self.assertEqual(len(first_scan), 1)

        # No new scan started yet -- must stay silent.
        still_silent = _collect_warnings(lambda: experiment.process_frame(frame))
        self.assertEqual(len(still_silent), 0)

        experiment.reset_baseline_warning_state()
        second_scan = _collect_warnings(lambda: experiment.process_frame(frame))
        self.assertEqual(len(second_scan), 1)

    def test_margin_warning_silent_when_well_above_baseline(self):
        b = 100.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=1000.0, i_on=1100.0)  # S_off=900 >> b

        warnings = _collect_warnings(lambda: experiment.process_frame(frame))

        self.assertEqual(len(warnings), 0)

    def test_s_on_at_or_below_zero_warns_not_raises_every_occurrence(self):
        b = 100.0
        experiment = _make_experiment(baseline_counts=b)
        # S_off stays large (no margin warning); S_on <= 0 on every repeat
        frame = _repeats_frame([(1000.0, 50.0)] * 3)

        warnings = _collect_warnings(lambda: experiment.process_frame(frame))

        self.assertEqual(len(warnings), 3)  # one per repeat, not deduplicated

    def test_s_on_negative_produces_negative_contrast_not_a_crash(self):
        b = 100.0
        experiment = _make_experiment(baseline_counts=b)
        frame = _pair_frame(i_off=1000.0, i_on=50.0)

        result = experiment.process_frame(frame)

        self.assertLess(result, 0)


# =====================================================
# AcquisitionState plumbing: baseline_counts survives every reconstruction
# =====================================================

class AcquisitionStateReconstructionTests(unittest.TestCase):
    def test_set_exposure_preserves_baseline_counts(self):
        fake_self = type("Fake", (), {})()
        fake_self.acquisition_state = AcquisitionState(baseline_counts=42.0)
        fake_self.apply_acquisition_state = lambda: None

        # set_exposure()'s parameter is milliseconds (the spinbox's display
        # unit) -- it converts to seconds before storing on AcquisitionState.
        MainWindow.set_exposure(fake_self, 50.0)

        self.assertEqual(fake_self.acquisition_state.baseline_counts, 42.0)
        self.assertEqual(fake_self.acquisition_state.exposure_s, 0.05)

    def test_set_binning_preserves_baseline_counts(self):
        fake_self = type("Fake", (), {})()
        fake_self.acquisition_state = AcquisitionState(baseline_counts=42.0)
        fake_self.apply_acquisition_state = lambda: None

        MainWindow.set_binning(fake_self, 2)

        self.assertEqual(fake_self.acquisition_state.baseline_counts, 42.0)
        self.assertEqual(fake_self.acquisition_state.binning, 2)

    def test_set_baseline_counts_does_not_touch_camera(self):
        """Unlike set_exposure()/set_binning(), this must not call
        apply_acquisition_state() -- baseline never reaches the camera."""
        fake_self = type("Fake", (), {})()
        fake_self.acquisition_state = AcquisitionState()
        calls = []
        fake_self.apply_acquisition_state = lambda: calls.append(1)

        MainWindow.set_baseline_counts(fake_self, 99.0)

        self.assertEqual(fake_self.acquisition_state.baseline_counts, 99.0)
        self.assertEqual(calls, [])

    def test_set_acquisition_roi_preserves_baseline_counts(self):
        """_set_acquisition_roi() has heavier dependencies (camera,
        sensor-dimension validation, live-view suspension) that aren't
        worth faking here -- source inspection directly verifies the one
        line this regression would break."""
        source = inspect.getsource(MainWindow._set_acquisition_roi)
        self.assertIn(
            "baseline_counts=self.acquisition_state.baseline_counts", source
        )


# =====================================================
# Metadata
# =====================================================

class BaselineMetadataTests(unittest.TestCase):
    def test_baseline_counts_persisted_in_config_json(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            data_manager = DataManager(base_dir=tmp_dir)
            config = {
                "f_start": 2.8e9, "f_stop": 2.9e9, "steps": 3,
                "exposure_s": 0.02,
                "baseline_counts": 123.4,
            }
            folder = data_manager.save_odmr(
                np.array([2.8e9, 2.85e9, 2.9e9]),
                np.array([1.0, 2.0, 3.0]),
                config,
            )

            with open(Path(folder) / "config.json") as f:
                saved = json.load(f)

            self.assertIn("baseline_counts", saved)
            self.assertEqual(saved["baseline_counts"], 123.4)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
