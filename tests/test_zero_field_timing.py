"""Sim-mode tests for Zero Field timing instrumentation.

No real hardware is touched anywhere in this file -- SimCamera and a real
Magnet/MagnetAxis stack built on SimPowerSupply/SimPulseStreamer are used
throughout, so the magnet-side sub-stage timing (which lives inside
hardware/magnet/magnet.py and magnet_axis.py, not a test double) is
exercised for real.
"""

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from gui.zero_field_worker import ZeroFieldWorker
from hardware.camera.sim_camera import SimCamera
from hardware.magnet.magnet import Magnet
from hardware.power_supply.sim_power_supply import SimPowerSupply
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


def make_camera():
    camera = SimCamera(image_shape=(2, 2))
    camera.connect()
    return camera


def make_magnet():
    """A real Magnet/MagnetAxis stack on simulated power supplies and pulse
    streamer -- not a FakeMagnet double -- so the timing hooks added to
    hardware/magnet/*.py are exercised, not bypassed."""
    config = {
        "X": {
            "name": "X", "max_current": 5.0,
            "magnetic_field_ratio": 5.0, "magnetic_field_offset": 0.0,
        },
        "Y": {
            "name": "Y", "max_current": 5.0,
            "magnetic_field_ratio": 5.0, "magnetic_field_offset": 0.0,
        },
        "Z": {
            "name": "Z", "max_current": 5.0,
            "magnetic_field_ratio": 5.0, "magnetic_field_offset": 0.0,
        },
        "flip_direction": {
            "available": True,
            "switch": {"switchChannel": 3, "switchChannelName": "flipMF"},
        },
    }
    power_supplies = {}
    for axis in ("X", "Y", "Z"):
        ps = SimPowerSupply({"name": axis})
        ps.connect()
        power_supplies[axis] = ps
    pulse_streamer = SimPulseStreamer(channel_map={"flipMF": 3})
    pulse_streamer.connect()
    return Magnet(config=config, power_supplies=power_supplies, pulse_streamer=pulse_streamer)


def make_experiment(**overrides):
    kwargs = dict(
        hardware_manager=None,
        camera=make_camera(),
        magnet=make_magnet(),
        field_start=-1.0,
        field_stop=1.0,
        field_points=3,
        field_axis="X",
        settling_time_ms=0,
        averages=1,
    )
    kwargs.update(overrides)
    return ZeroFieldExperiment(**kwargs)


class TimingOffByDefaultTests(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("NV_ZFE_TIMING", None)

    def tearDown(self):
        self._env_patch.stop()

    def test_no_env_var_no_config_key_means_timing_stays_off(self):
        experiment = make_experiment()
        camera = experiment.camera
        magnet = experiment.magnet

        self.assertFalse(experiment.timing_enabled())

        cube = experiment.run()

        self.assertIsNotNone(cube.data)
        self.assertEqual(experiment.pop_timing_log(), [])
        self.assertFalse(camera._timing_enabled)
        self.assertEqual(camera._timing_log, [])
        self.assertFalse(magnet._timing_enabled)
        self.assertEqual(magnet._timing_log, [])
        self.assertFalse(magnet.x._timing_enabled)
        self.assertEqual(magnet.x._timing_log, [])

    def test_off_by_default_writes_no_csv_via_worker(self):
        config = {
            "field_start": -1.0, "field_stop": 1.0, "field_points": 2,
            "field_axis": "X", "settling_time_ms": 0, "averages": 1,
            "averaging_enabled": False, "num_scans": 1, "save_raw_scans": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            previous_cwd = os.getcwd()
            os.chdir(directory)
            try:
                output_path = Path(directory) / "zero_field_test.npz"
                worker = ZeroFieldWorker(
                    None, make_camera(), make_magnet(), config, None, {}, output_path,
                )
                worker.start()
                self.assertEqual(list(Path("data").glob("*.csv")), [])
            finally:
                os.chdir(previous_cwd)


class TimingEnabledTests(unittest.TestCase):
    def test_expected_stages_and_totals_appear(self):
        experiment = make_experiment(
            field_points=3, averages=2, settling_time_ms=1,
            timing_diagnostics=True,
        )
        self.assertTrue(experiment.timing_enabled())

        experiment.run()
        records = experiment.pop_timing_log()

        stages = {r["stage"] for r in records}
        for expected in (
            "determine_polarity",
            "axis_X_set_current",
            "axis_Y_set_current",
            "axis_Z_set_current",
            "settling_sleep",
            "snap",
            "camera.wait_buffer",
            "process_frame",
            "emit_live_update",
            "point_total",
            "scan_total",
            "run_total",
        ):
            self.assertIn(expected, stages, f"missing stage {expected!r}")

        # avg_index should distinguish the two averages() repeats per point.
        snap_rows = [r for r in records if r["stage"] == "snap"]
        self.assertEqual(len(snap_rows), 3 * 2)  # field_points * averages
        self.assertEqual(
            sorted({r["avg_index"] for r in snap_rows}), [0, 1],
        )

        # Exactly one run_total / scan_total row.
        self.assertEqual(sum(1 for r in records if r["stage"] == "run_total"), 1)
        self.assertEqual(sum(1 for r in records if r["stage"] == "scan_total"), 1)
        self.assertEqual(sum(1 for r in records if r["stage"] == "point_total"), 3)

    def test_point_and_scan_totals_are_consistent_with_each_other(self):
        # point_total wraps its whole point body, which includes both a
        # "snap" entry (the whole camera.snap() call) and that same call's
        # own camera.* sub-stages (see acquire_frame()/_merge_camera_log)
        # -- those two overlap in real time by design (the sub-stages
        # happen *inside* snap()'s own span), so summing every row for one
        # point_index is not a meaningful bound on point_total. What *is*
        # true regardless: the sum of point_total rows for one scan can't
        # exceed that scan's own scan_total, and scan_total can't exceed
        # run_total.
        experiment = make_experiment(
            field_points=4, averages=1, timing_diagnostics=True,
        )
        experiment.run()
        records = experiment.pop_timing_log()

        run_total = next(r for r in records if r["stage"] == "run_total")["duration_s"]
        scan_total = next(r for r in records if r["stage"] == "scan_total")["duration_s"]
        point_total_sum = sum(
            r["duration_s"] for r in records if r["stage"] == "point_total"
        )

        self.assertLessEqual(point_total_sum, scan_total)
        self.assertLessEqual(scan_total, run_total)

    def test_double_set_current_write_is_visible_as_two_rows(self):
        # X is the swept axis and field=1.0 G at the last point is non-zero
        # -- MagnetAxis.set_field()'s non-zero branch currently calls
        # power_supply.set_current() twice (see hardware/magnet/magnet_axis.py).
        # This test exercises that existing behavior without changing it.
        experiment = make_experiment(
            field_start=0.0, field_stop=1.0, field_points=2,
            field_axis="X", timing_diagnostics=True,
        )
        experiment.run()
        records = experiment.pop_timing_log()

        nonzero_point = next(
            i for i, v in enumerate(experiment.scan_vector) if abs(v) > 1e-9
        )
        set_current_rows = [
            r for r in records
            if r["point_index"] == nonzero_point and r["stage"] == "axis_X_set_current"
        ]
        self.assertEqual(len(set_current_rows), 2)

        # Y/Z stay at zero the whole sweep (zero_other_axes defaults True)
        # -- the zero-field branch calls set_current() exactly once.
        for axis_stage in ("axis_Y_set_current", "axis_Z_set_current"):
            rows = [
                r for r in records
                if r["point_index"] == nonzero_point and r["stage"] == axis_stage
            ]
            self.assertEqual(len(rows), 1)

    def test_zero_crossing_sweep_shows_exactly_one_flip(self):
        # Sweep starts positive (matching the POSITIVE polarity enable()
        # already syncs to, so no flip at the first point) and crosses to
        # negative once -- exactly one real relay flip should be recorded.
        experiment = make_experiment(
            field_start=2.0, field_stop=-2.0, field_points=5,
            field_axis="X", timing_diagnostics=True,
        )
        experiment.run()
        records = experiment.pop_timing_log()

        flips = [r for r in records if r["stage"] == "apply_global_polarity_flip"]
        noops = [r for r in records if r["stage"] == "apply_global_polarity_noop"]

        # One set_vector() call per scan point, plus one extra from
        # setup_scan()'s own zero_other_axes zeroing call (a real, distinct
        # call, correctly tagged point_index=None -- see setup_scan()).
        self.assertEqual(len(flips), 1)
        self.assertEqual(len(noops), len(experiment.scan_vector))

    def test_multi_scan_run_has_one_scan_total_per_scan_and_one_run_total(self):
        experiment = make_experiment(
            field_points=2, averages=1, averaging_enabled=True, num_scans=3,
            timing_diagnostics=True,
        )
        experiment.run()
        records = experiment.pop_timing_log()

        self.assertEqual(sum(1 for r in records if r["stage"] == "scan_total"), 3)
        self.assertEqual(sum(1 for r in records if r["stage"] == "run_total"), 1)
        self.assertEqual(
            sorted(
                r["scan_index"] for r in records if r["stage"] == "scan_total"
            ),
            [1, 2, 3],
        )


class LiveViewIsolationAndBoundingTests(unittest.TestCase):
    def test_camera_and_magnet_timing_flags_are_off_before_and_after_run(self):
        experiment = make_experiment(timing_diagnostics=True)
        camera = experiment.camera
        magnet = experiment.magnet

        self.assertFalse(camera._timing_enabled)
        self.assertFalse(magnet._timing_enabled)

        experiment.run()

        self.assertFalse(camera._timing_enabled)
        self.assertEqual(camera._timing_log, [])
        self.assertFalse(magnet._timing_enabled)
        self.assertEqual(magnet._timing_log, [])
        self.assertFalse(magnet.x._timing_enabled)
        self.assertEqual(magnet.x._timing_log, [])

    def test_camera_side_log_never_exceeds_one_snap_worth_of_entries(self):
        experiment = make_experiment(
            field_points=3, averages=2, timing_diagnostics=True,
        )
        camera = experiment.camera
        observed_lengths = []
        original_pop = camera.pop_timing_log

        def spy_pop():
            observed_lengths.append(len(camera._timing_log))
            return original_pop()

        camera.pop_timing_log = spy_pop

        experiment.run()

        self.assertTrue(observed_lengths)
        self.assertTrue(all(length == 8 for length in observed_lengths))

    def test_timing_disabled_leaves_camera_and_magnet_flags_untouched(self):
        experiment = make_experiment(timing_diagnostics=False)
        camera = experiment.camera
        magnet = experiment.magnet

        experiment.run()

        self.assertFalse(camera._timing_enabled)
        self.assertFalse(magnet._timing_enabled)


class NoBehaviorChangeTests(unittest.TestCase):
    """Instrumentation must be pure measurement: identical acquired data
    and metadata whether or not timing is enabled, and whether or not it
    is exercised at all."""

    def test_timing_on_vs_off_produce_identical_image_data_and_metadata(self):
        # SimCamera._generate_frame() draws from the global numpy RNG, so
        # both runs must see the same draws to be comparable -- seeding
        # isolates "did instrumentation change the outcome" from ordinary
        # sim-mode randomness, which is unrelated to this test's purpose.
        np.random.seed(1234)
        off_experiment = make_experiment(field_points=3, averages=1)
        off_cube = off_experiment.run()

        np.random.seed(1234)
        on_experiment = make_experiment(
            field_points=3, averages=1, timing_diagnostics=True,
        )
        on_cube = on_experiment.run()

        np.testing.assert_array_equal(off_cube.data, on_cube.data)

        off_metadata = dict(off_cube.metadata)
        on_metadata = dict(on_cube.metadata)
        # acquisition_started_at_utc legitimately differs run to run.
        off_metadata.pop("acquisition_started_at_utc", None)
        on_metadata.pop("acquisition_started_at_utc", None)
        self.assertEqual(off_metadata, on_metadata)


class WorkerCsvWritingTests(unittest.TestCase):
    def test_csv_written_with_expected_header_and_fieldnames(self):
        config = {
            "field_start": -1.0, "field_stop": 1.0, "field_points": 2,
            "field_axis": "X", "settling_time_ms": 0, "averages": 1,
            "averaging_enabled": False, "num_scans": 1, "save_raw_scans": False,
            "timing_diagnostics": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            previous_cwd = os.getcwd()
            os.chdir(directory)
            try:
                output_path = Path(directory) / "zero_field_test.npz"
                worker = ZeroFieldWorker(
                    None, make_camera(), make_magnet(), config,
                    (0, 0, 2, 2), {}, output_path,
                )
                worker.start()

                csv_files = list(Path("data").glob("zero_field_timing_*.csv"))
                self.assertEqual(len(csv_files), 1)

                lines = csv_files[0].read_text().splitlines()
                header_lines = [line for line in lines if line.startswith("#")]
                self.assertTrue(
                    any(line.startswith("# field_start_gauss=") for line in header_lines)
                )
                self.assertTrue(
                    any(line.startswith("# camera_exposure_s=") for line in header_lines)
                )
                self.assertTrue(
                    any(line.startswith("# camera_binning=") for line in header_lines)
                )
                self.assertTrue(
                    any(line.startswith("# measured_run_total_s=") for line in header_lines)
                )

                body = "\n".join(
                    line for line in lines if not line.startswith("#")
                )
                reader = csv.DictReader(body.splitlines())
                self.assertEqual(
                    reader.fieldnames,
                    ["scan_index", "point_index", "field_gauss", "avg_index",
                     "stage", "t_start_rel_s", "duration_s"],
                )
                rows = list(reader)
                self.assertTrue(any(r["stage"] == "run_total" for r in rows))
            finally:
                os.chdir(previous_cwd)

    def test_csv_filename_does_not_collide_with_odmr_or_real_output(self):
        config = {
            "field_start": -1.0, "field_stop": 1.0, "field_points": 2,
            "field_axis": "X", "settling_time_ms": 0, "averages": 1,
            "averaging_enabled": False, "num_scans": 1, "save_raw_scans": False,
            "timing_diagnostics": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            previous_cwd = os.getcwd()
            os.chdir(directory)
            try:
                output_path = Path(directory) / "zero_field_test.npz"
                worker = ZeroFieldWorker(
                    None, make_camera(), make_magnet(), config, None, {}, output_path,
                )
                worker.start()

                csv_files = list(Path("data").glob("*.csv"))
                self.assertEqual(len(csv_files), 1)
                name = csv_files[0].name
                self.assertTrue(name.startswith("zero_field_timing_"))
                self.assertFalse(name.startswith("odmr_timing_"))
            finally:
                os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
