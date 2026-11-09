"""Hardware-free tests for relay-flip detection in Zero Field scans.

Magnet.set_vector() reports which axes flipped polarity; ZeroFieldExperiment
adds post_flip_settling_ms after such points and records relay_flip_occurred
/ relay_flip_axes per acquired point in the cube (and HDF5) metadata.
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.image_cube import ImageCube
from gui.zero_field_worker import ZeroFieldWorker
from tests.test_magnetic_field_gui_units import _make_magnet as make_offset_magnet
from tests.test_zero_field_timing import make_camera, make_experiment, make_magnet


# make_magnet() has zero calibration offsets and starts POSITIVE after
# enable(): sweeping X over -1, -0.5, 0, +0.5, +1 G flips to NEGATIVE at
# index 0, holds NEGATIVE through 0 G (both branches reach it), and flips
# back to POSITIVE at index 3.
SWEEP = dict(field_start=-1.0, field_stop=1.0, field_points=5, field_axis="X")
EXPECTED_OCCURRED = [True, False, False, True, False]
EXPECTED_AXES = ["X", "", "", "X", ""]


class RecordingTime:
    """Stands in for the `time` module inside zero_field_experiment only,
    recording its sleeps without sleeping. The camera driver keeps the real
    module, so its simulated exposure wait (and stale-frame guard) still work."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def __getattr__(self, name):
        return getattr(time, name)


class SetVectorFlipReportTests(unittest.TestCase):
    def setUp(self):
        self.magnet = make_magnet()
        self.magnet.enable()

    def test_reports_flipped_axes_only(self):
        self.assertEqual(self.magnet.set_vector(0.0, 0.0, 0.0), frozenset())
        self.assertEqual(self.magnet.set_vector(bx=-0.1), frozenset({"X"}))
        self.assertEqual(self.magnet.set_vector(bx=-0.2), frozenset())
        self.assertEqual(self.magnet.set_vector(bx=0.1, bz=-0.1), frozenset({"X", "Z"}))

    def test_flip_report_matches_switch_polarity_calls(self):
        with patch.object(self.magnet.x, "_switch_polarity",
                          wraps=self.magnet.x._switch_polarity) as switch:
            flipped = self.magnet.set_vector(bx=-0.1)
        switch.assert_called_once()
        self.assertEqual(flipped, frozenset({"X"}))


class ZeroFieldFlipTrackingTests(unittest.TestCase):
    def test_sweep_across_x_flip_point_marks_the_flipped_points(self):
        experiment = make_experiment(**SWEEP)
        cube = experiment.run()
        self.assertEqual(cube.metadata["relay_flip_occurred"], EXPECTED_OCCURRED)
        self.assertEqual(cube.metadata["relay_flip_axes"], EXPECTED_AXES)
        self.assertEqual(len(cube.metadata["relay_flip_occurred"]), cube.data.shape[0])

    def test_post_flip_settling_only_at_flip_points(self):
        experiment = make_experiment(
            **SWEEP, settling_time_ms=100, post_flip_settling_ms=250
        )
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            experiment.run()
        # Every point settles 0.1 s; only flip points (0 and 3) add 0.25 s.
        self.assertEqual(clock.sleeps, [0.1, 0.25, 0.1, 0.1, 0.1, 0.25, 0.1])

    def test_no_extra_settling_when_zero(self):
        experiment = make_experiment(**SWEEP, settling_time_ms=100)
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            experiment.run()
        self.assertEqual(clock.sleeps, [0.1] * 5)

    def test_flips_from_scan_setup_are_attributed_to_point_zero(self):
        # Nonzero offsets: setup_scan()'s set_vector(0, 0, 0) moves X and Z
        # onto their negative branches. Point 0 (-1 G on X) is itself on the
        # negative branch, so its own command flips nothing -- the setup
        # flips are what it must be marked (and settled) for.
        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=make_camera(), magnet=make_offset_magnet(),
            field_start=-1.0, field_stop=-0.5, field_points=2, field_axis="X",
            settling_time_ms=0, post_flip_settling_ms=250,
        )
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            cube = experiment.run()
        self.assertEqual(cube.metadata["relay_flip_axes"], ["X,Z", ""])
        self.assertEqual(cube.metadata["relay_flip_occurred"], [True, False])
        self.assertEqual(clock.sleeps, [0.25])  # settling_time_ms=0

    def test_stopped_sweep_truncates_flip_lists(self):
        holder = {}

        def stop_after_two(*_):
            if len(holder["experiment"].image_cube.data) == 2:
                holder["experiment"].stop()

        experiment = make_experiment(**SWEEP, live_update_callback=stop_after_two)
        holder["experiment"] = experiment
        cube = experiment.run()
        self.assertEqual(cube.metadata["relay_flip_occurred"], EXPECTED_OCCURRED[:2])
        self.assertEqual(cube.metadata["relay_flip_axes"], EXPECTED_AXES[:2])

    def test_post_flip_settling_ms_is_validated_and_recorded(self):
        with self.assertRaises(ValueError):
            make_experiment(post_flip_settling_ms=-1)
        cube = make_experiment(**SWEEP, post_flip_settling_ms=0).run()
        self.assertEqual(cube.metadata["post_flip_settling_ms"], 0.0)


class ZeroFieldFlipHdf5Tests(unittest.TestCase):
    def _config(self, **overrides):
        config = {
            "field_start": SWEEP["field_start"],
            "field_stop": SWEEP["field_stop"],
            "field_points": SWEEP["field_points"],
            "field_axis": SWEEP["field_axis"],
            "settling_time_ms": 0,
            "post_flip_settling_ms": 0,
            "averages": 1,
        }
        config.update(overrides)
        return config

    def _assert_flips(self, path):
        metadata = ImageCube.load(path).metadata
        self.assertEqual(metadata["relay_flip_occurred"], EXPECTED_OCCURRED)
        self.assertEqual(metadata["relay_flip_axes"], EXPECTED_AXES)

    def test_single_scan_streaming_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test.h5"
            worker = ZeroFieldWorker(
                None, make_camera(), make_magnet(), self._config(), None, {}, output_path
            )
            worker.start()
            self._assert_flips(output_path)

    def test_multi_scan_average_and_raw_scan_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test_average.h5"
            config = self._config(
                averaging_enabled=True, num_scans=2, save_raw_scans=True
            )
            worker = ZeroFieldWorker(
                None, make_camera(), make_magnet(), config, None, {}, output_path
            )
            worker.start()
            self._assert_flips(output_path)  # atomic-save path
            raw = sorted(Path(directory).glob("zero_field_test_scan_*.h5"))
            self.assertEqual(len(raw), 2)
            for path in raw:  # per-scan streamed files
                self._assert_flips(path)


if __name__ == "__main__":
    unittest.main()
