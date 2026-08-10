"""Tests for ODMRWindow.estimate_odmr_time() -- rebuilt from measured
per-stage costs after two structural bugs were found: trigger_delay_s/
reset_delay_s/fire_delay_s were counted once per point instead of once
per frame (2 * repeats per point-average), and camera_overhead_s was a
0.35s/point fudge factor for a per-frame camera arm/disarm regime that no
longer exists (arming is now once per scan).

tests/fixtures/odmr_timing_*.csv are frozen copies of 4 of the 23 real
timing-diagnostics recordings in data/odmr_timing_*.csv (real rig data,
sim mode was not used to produce them), chosen to cover: a high
trigger_delay_s, trigger_delay_s=0.02 (the realistic default range),
trigger_delay_s=0.0 (the known stall regime -- see "Known hardware
quirks (ODMR timing)" in CLAUDE.md), and repeats=1. Copied into
tests/fixtures/ deliberately, rather than reading data/ directly, because
data/ is working data the pinning test must not depend on -- it can be
deleted, moved, or regenerated without warning.

No real hardware -- this only exercises the pure-function time estimate.
"""

import csv
import pathlib
import unittest

from gui.odmr_window import (
    ODMRWindow,
    CAMERA_ARM_DISARM_S,
    CAMERA_PER_FRAME_OVERHEAD_S,
    VISA_POINT_OVERHEAD_S,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _read_fixture(name):
    """Parse a timing-diagnostics CSV's '# key=value' header plus its
    actual point_index/avg_index/repeat_index columns (n_points/averages
    aren't fully recoverable from the header alone -- dense1/dense2
    region config isn't logged there)."""
    path = FIXTURES_DIR / name
    if not path.is_file():
        raise AssertionError(
            f"missing test fixture: {path} -- pinning test cannot run "
            "without it. Was tests/fixtures/ deleted or renamed?"
        )

    with open(path, newline="") as f:
        lines = f.readlines()

    header = {}
    body_start = 0
    for i, line in enumerate(lines):
        if not line.startswith("#"):
            body_start = i
            break
        if "=" in line:
            key, _, rest = line[1:].strip().partition("=")
            val = rest.strip()
            if " (" in val:
                val = val.split(" (")[0]
            header[key.strip()] = val

    rows = list(csv.DictReader(lines[body_start:]))
    n_points = len({r["point_index"] for r in rows if r["point_index"] != ""})
    n_avg = len({r["avg_index"] for r in rows if r["avg_index"] != ""}) or 1
    n_repeat_instances = sum(1 for r in rows if r["stage"] == "repeat_total")
    repeats = n_repeat_instances // (n_points * n_avg) if n_points else 0

    def cfg(key, cast=float, default=None):
        v = header.get(key)
        return default if v is None else cast(v)

    config = {
        # steps stands in directly for the fixture's *actual* recorded
        # point count -- the header doesn't log dense1/dense2 region
        # params, so a plain linspace of n_points points is used instead
        # of trying to reconstruct the original dense-region config.
        "f_start": cfg("f_start_hz"),
        "f_stop": cfg("f_stop_hz"),
        "steps": n_points,
        "averages": n_avg,
        "repeats": repeats,
        "trigger_delay_s": cfg("trigger_delay_s"),
        "fire_delay_s": cfg("fire_delay_s"),
        "reset_delay_s": cfg("reset_delay_s"),
        "exposure_s": cfg("exposure_s"),
        "mw_settle_s": cfg("mw_settle_s", default=0.0),
        "pulse_lead_s": cfg("pulse_lead_s", default=0.002),
        "pulse_tail_s": cfg("pulse_tail_s", default=0.002),
    }

    return config, cfg("measured_scan_total_s")


class RealDataPinningTests(unittest.TestCase):
    """The acceptance test: predicted vs. actually-measured scan wall-clock
    from real rig recordings, not synthetic config."""

    def _check(self, fixture_name, tolerance_pct):
        config, measured_s = _read_fixture(fixture_name)
        predicted_s = ODMRWindow.estimate_odmr_time(None, config)

        pct = 100.0 * abs(predicted_s - measured_s) / measured_s
        self.assertLessEqual(
            pct, tolerance_pct,
            f"{fixture_name}: predicted={predicted_s:.3f}s "
            f"measured={measured_s:.3f}s ({pct:.1f}% off, "
            f"tolerance {tolerance_pct}%)"
        )

    def test_high_trigger_delay(self):
        # trigger_delay_s=0.150 -- realistic range, formula measured <2%
        # off during derivation; 5% tolerance leaves headroom for drift.
        self._check("odmr_timing_trigger_delay_150ms.csv", tolerance_pct=5.0)

    def test_default_trigger_delay(self):
        # trigger_delay_s=0.020 -- the realistic default range. This
        # particular recording (n_points=20, smaller total scan than the
        # other fixtures) measured 5.6% off during formula derivation;
        # tolerance set just above that rather than tightened to a value
        # this specific fixture can't actually meet.
        self._check("odmr_timing_trigger_delay_20ms.csv", tolerance_pct=6.0)

    def test_repeats_one(self):
        # trigger_delay_s=0.020, repeats=1 -- exercises the 2*repeats
        # frame-count math at its smallest nontrivial value.
        self._check("odmr_timing_repeats1.csv", tolerance_pct=5.0)

    def test_near_zero_trigger_delay_known_stall_regime(self):
        # trigger_delay_s=0.0 -- the reproducible ~400ms-per-frame stall
        # regime documented in CLAUDE.md ("Known hardware quirks (ODMR
        # timing)"). The formula is a best-estimate for the typical,
        # non-stalled case and is known to run 15-30% low here; the loose
        # tolerance still catches a formula regression without re-encoding
        # the stall's magnitude as an expected value.
        self._check("odmr_timing_trigger_delay_0ms.csv", tolerance_pct=35.0)


class FrameVsPointScalingTests(unittest.TestCase):
    """Structural regression for the fixed bug: trigger_delay_s/
    reset_delay_s/fire_delay_s must scale with repeats (2x per repeat),
    not be added once per point. Catches a future accidental revert."""

    def _base_config(self, **overrides):
        config = {
            "f_start": 2.80e9, "f_stop": 2.94e9, "steps": 10,
            "averages": 1, "repeats": 3,
            "trigger_delay_s": 0.02, "fire_delay_s": 0.005,
            "reset_delay_s": 0.005, "exposure_s": 0.02,
            "mw_settle_s": 0.0, "pulse_lead_s": 0.002,
        }
        config.update(overrides)
        return config

    def test_estimate_scales_with_repeats_at_2x_per_point(self):
        n_points = 10
        base = ODMRWindow.estimate_odmr_time(None, self._base_config(repeats=3))
        plus_one = ODMRWindow.estimate_odmr_time(None, self._base_config(repeats=4))

        frame_wall_s = (
            0.005 + 0.005 + 0.02 + 0.002 + 0.02 + CAMERA_PER_FRAME_OVERHEAD_S
        )
        expected_delta = n_points * 1 * 2 * frame_wall_s

        self.assertAlmostEqual(plus_one - base, expected_delta, places=6)

    def test_estimate_scales_linearly_with_n_points(self):
        ten_points = ODMRWindow.estimate_odmr_time(None, self._base_config(steps=10))
        twenty_points = ODMRWindow.estimate_odmr_time(None, self._base_config(steps=20))

        frame_wall_s = (
            0.005 + 0.005 + 0.02 + 0.002 + 0.02 + CAMERA_PER_FRAME_OVERHEAD_S
        )
        per_point_avg = VISA_POINT_OVERHEAD_S + 0.0 + 2 * 3 * frame_wall_s
        self.assertAlmostEqual(
            twenty_points - ten_points, 10 * per_point_avg, places=6
        )


class RemovedTermsAbsentTests(unittest.TestCase):
    """pulse_tail_s and camera_overhead_s must not appear in the
    estimate's source at all -- source inspection, matching the existing
    frame_gap_s-absence test style in tests/test_mw_switch_gating.py."""

    def test_pulse_tail_s_not_read(self):
        import inspect
        source = inspect.getsource(ODMRWindow.estimate_odmr_time)
        self.assertNotIn('"pulse_tail_s"', source)
        self.assertNotIn("'pulse_tail_s'", source)

    def test_camera_overhead_s_not_read(self):
        import inspect
        source = inspect.getsource(ODMRWindow.estimate_odmr_time)
        self.assertNotIn("camera_overhead_s", source)

    def test_runs_without_removed_config_keys(self):
        config = {
            "f_start": 2.80e9, "f_stop": 2.94e9, "steps": 5,
            "averages": 1, "repeats": 1,
        }
        self.assertNotIn("camera_overhead_s", config)
        self.assertNotIn("mw_power_settle_s", config)
        result = ODMRWindow.estimate_odmr_time(None, config)
        self.assertGreater(result, 0)


class MwSettleAndPulseLeadContributeTests(unittest.TestCase):
    """mw_settle_s (once per point-average) and pulse_lead_s (once per
    frame, 2*repeats per point-average) must each contribute with the
    right multiplier."""

    def _base_config(self, **overrides):
        config = {
            "f_start": 2.80e9, "f_stop": 2.94e9, "steps": 4,
            "averages": 2, "repeats": 3,
            "trigger_delay_s": 0.02, "fire_delay_s": 0.005,
            "reset_delay_s": 0.005, "exposure_s": 0.02,
            "mw_settle_s": 0.0, "pulse_lead_s": 0.002,
        }
        config.update(overrides)
        return config

    def test_mw_settle_s_scales_with_points_times_averages_only(self):
        n_points, n_avg = 4, 2
        base = ODMRWindow.estimate_odmr_time(None, self._base_config(mw_settle_s=0.0))
        with_settle = ODMRWindow.estimate_odmr_time(None, self._base_config(mw_settle_s=0.01))

        expected_delta = n_points * n_avg * 0.01
        self.assertAlmostEqual(with_settle - base, expected_delta, places=6)

    def test_pulse_lead_s_scales_with_2x_repeats_per_point_avg(self):
        n_points, n_avg, repeats = 4, 2, 3
        base = ODMRWindow.estimate_odmr_time(None, self._base_config(pulse_lead_s=0.002))
        more_lead = ODMRWindow.estimate_odmr_time(None, self._base_config(pulse_lead_s=0.012))

        expected_delta = n_points * n_avg * 2 * repeats * (0.012 - 0.002)
        self.assertAlmostEqual(more_lead - base, expected_delta, places=6)


if __name__ == "__main__":
    unittest.main()
