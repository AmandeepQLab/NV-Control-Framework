"""Hardware-free tests for bidirectional (alternating-direction) Zero Field
averaging.

With alternate_direction on, scans 2, 4, ... sweep stop -> start. Every
per-point quantity is aligned by field before it is averaged, so linear
drift within a sweep cancels in a forward + reverse pair. The average file
also carries forward_average / reverse_average and per-scan timestamps.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

# QApplication must exist before pyqtgraph (via the widget) is imported;
# see tests/test_zero_field_window_save_toggle.py.
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.zero_field_experiment import ZeroFieldExperiment  # noqa: E402
from framework.analysis.zero_field import mean_fluorescence_vs_field  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from gui.widgets.zero_field_widget import ZeroFieldWidget  # noqa: E402
from gui.zero_field_worker import ZeroFieldWorker  # noqa: E402

# Widgets are created once and never deleted mid-process. This predates
# the 2026-11-22 pyqtgraph teardown fix (gui/pyqtgraph_teardown.py) and was
# kept; it has not been re-tested without it.
_KEEP_ALIVE = []

FIELD_START, FIELD_STOP = -1.0, 1.0


class CommandMagnet:
    """Magnet double that remembers every set_vector() command.

    get_vector() reports the last command (so the model field equals the
    request). ``flip_at_gauss`` makes set_vector() report an X relay flip
    whenever that exact swept field is commanded, in either direction.
    ``hysteresis_gauss`` gives X a Schmitt-trigger polarity: it turns '+'
    at or above +h and '-' at or below -h, otherwise it keeps its state --
    a stand-in for the real relay's sweep-history dependence.
    """

    def __init__(self, flip_at_gauss=None, hysteresis_gauss=None):
        self.commands_mT = []
        self.current = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.flip_at_gauss = flip_at_gauss
        self.hysteresis_gauss = hysteresis_gauss
        self.x_polarity = -1

    def enable(self):
        pass

    def disable(self):
        pass

    def get_vector(self):
        return dict(self.current)

    def get_polarities(self):
        return {
            "x": SimpleNamespace(value=self.x_polarity),
            "y": SimpleNamespace(value=1),
            "z": SimpleNamespace(value=1),
        }

    def set_vector(self, bx, by, bz):
        self.current = {"x": bx, "y": by, "z": bz}
        self.commands_mT.append(bx)
        flipped = set()
        gauss = bx * 10.0
        if self.flip_at_gauss is not None and np.isclose(gauss, self.flip_at_gauss):
            flipped.add("X")
        if self.hysteresis_gauss is not None:
            if gauss >= self.hysteresis_gauss - 1e-12:
                self.x_polarity = 1
            elif gauss <= -self.hysteresis_gauss + 1e-12:
                self.x_polarity = -1
        return frozenset(flipped)

    def swept_gauss_per_scan(self, points_per_scan):
        """The swept-axis commands grouped by scan, dropping each scan's
        setup_scan() zeroing command."""
        per_scan = points_per_scan + 1
        return [
            [round(value * 10.0, 9) for value in self.commands_mT[i + 1:i + per_scan]]
            for i in range(0, len(self.commands_mT), per_scan)
        ]


class DriftCamera:
    """Frame value = linear drift in acquisition time + a dip in field.

    Time is the global frame counter, so drift is exactly linear and
    deterministic. The field is read from the magnet's last command.
    """

    streaming = False

    def __init__(self, magnet, drift_per_frame=2.0, dip_depth=3.0, dip_width_gauss=0.3):
        self.magnet = magnet
        self.drift_per_frame = drift_per_frame
        self.dip_depth = dip_depth
        self.dip_width_gauss = dip_width_gauss
        self.frame_count = 0

    def dip(self, gauss):
        return self.dip_depth * np.exp(-(np.asarray(gauss) / self.dip_width_gauss) ** 2)

    def snap(self):
        gauss = self.magnet.current["x"] * 10.0
        value = 1000.0 + self.drift_per_frame * self.frame_count - self.dip(gauss)
        self.frame_count += 1
        return np.full((2, 2), value)

    def set_roi(self, roi):
        self.roi = roi


def make_experiment(magnet, camera, points=5, **overrides):
    kwargs = dict(
        hardware_manager=None,
        camera=camera,
        magnet=magnet,
        field_start=FIELD_START,
        field_stop=FIELD_STOP,
        field_points=points,
        field_axis="X",
        settling_time_ms=0,
        averages=1,
        averaging_enabled=True,
        num_scans=2,
        alternate_direction=True,
    )
    kwargs.update(overrides)
    return ZeroFieldExperiment(**kwargs)


def run(points=5, magnet=None, **overrides):
    magnet = magnet or CommandMagnet()
    camera = DriftCamera(magnet)
    experiment = make_experiment(magnet, camera, points=points, **overrides)
    return experiment.run(), experiment, magnet


class SweepOrderTests(unittest.TestCase):
    def test_second_scan_commands_fields_in_reverse_order(self):
        # Scans are numbered from 1, as in the GUI and raw file names:
        # scan 1 runs start -> stop, scan 2 stop -> start.
        _, _, magnet = run(points=5)
        forward = np.linspace(FIELD_START, FIELD_STOP, 5).round(9).tolist()
        self.assertEqual(magnet.swept_gauss_per_scan(5), [forward, forward[::-1]])

    def test_scan_is_reversed_numbering(self):
        self.assertEqual(
            [ZeroFieldExperiment.scan_is_reversed(i, True) for i in range(1, 5)],
            [False, True, False, True],
        )
        self.assertFalse(ZeroFieldExperiment.scan_is_reversed(2, False))

    def test_no_reversal_without_averaging(self):
        _, experiment, magnet = run(points=3, averaging_enabled=False)
        forward = np.linspace(FIELD_START, FIELD_STOP, 3).round(9).tolist()
        self.assertEqual(magnet.swept_gauss_per_scan(3), [forward])
        self.assertFalse(experiment.image_cube.metadata["alternate_direction"])

    def test_single_scan_with_alternation_is_forward_and_unaveraged(self):
        cube, _, magnet = run(points=3, num_scans=1)
        forward = np.linspace(FIELD_START, FIELD_STOP, 3).round(9).tolist()
        self.assertEqual(magnet.swept_gauss_per_scan(3), [forward])
        self.assertEqual(cube.metadata["sweep_direction"], "forward")
        self.assertEqual(cube.extra_datasets, {})
        for key in ("scan_directions", "forward_scan_count", "scan_point_times_s"):
            self.assertNotIn(key, cube.metadata)

    def test_alternate_direction_must_be_bool(self):
        with self.assertRaises(ValueError):
            make_experiment(CommandMagnet(), None, alternate_direction=1)


class DriftCancellationTests(unittest.TestCase):
    def test_pair_cancels_linear_drift_but_forward_only_is_tilted(self):
        points = 11
        cube, experiment, magnet = run(points=points, num_scans=2)
        fields = np.linspace(FIELD_START, FIELD_STOP, points)
        dip = DriftCamera(magnet).dip(fields)

        # Overall average: drift + dip. With the dip removed, what is left
        # must be flat -- the drift has cancelled.
        _, signal = mean_fluorescence_vs_field(cube)
        self.assertLess(np.ptp(signal + dip), 1e-9)
        # ...and the dip itself is recovered at full depth.
        self.assertAlmostEqual(np.ptp(signal), np.ptp(dip), places=9)

        # Forward-only: residual tilt = drift_per_frame * (points - 1).
        forward = cube.extra_datasets["forward_average"].mean(axis=(1, 2))
        residual = forward + dip
        self.assertAlmostEqual(residual[-1] - residual[0], 2.0 * (points - 1))
        self.assertGreater(np.ptp(residual), 5 * np.ptp(dip))
        # Reverse-only is tilted the other way.
        reverse = cube.extra_datasets["reverse_average"].mean(axis=(1, 2))
        self.assertLess((reverse + dip)[-1] - (reverse + dip)[0], 0)

    def test_average_is_mean_of_direction_averages(self):
        cube, _, _ = run(points=5, num_scans=4)
        np.testing.assert_allclose(
            cube.data,
            0.5 * (cube.extra_datasets["forward_average"]
                   + cube.extra_datasets["reverse_average"]),
        )
        self.assertEqual(cube.metadata["forward_scan_count"], 2)
        self.assertEqual(cube.metadata["reverse_scan_count"], 2)
        self.assertEqual(
            cube.metadata["scan_directions"],
            ["forward", "reverse", "forward", "reverse"],
        )


class FieldAlignmentTests(unittest.TestCase):
    FIELDS = np.linspace(FIELD_START, FIELD_STOP, 5)

    def test_flip_at_field_index_stays_at_that_index(self):
        j = 1
        magnet = CommandMagnet(flip_at_gauss=self.FIELDS[j])
        cube, _, _ = run(points=5, magnet=magnet)
        expected = [i == j for i in range(5)]
        self.assertEqual(cube.metadata["relay_flip_occurred"], expected)
        self.assertEqual(
            cube.metadata["relay_flip_axes"], ["X" if e else "" for e in expected]
        )

    def test_model_fields_align_with_requested_fields(self):
        cube, _, _ = run(points=5)
        np.testing.assert_allclose(
            cube.metadata["model_field_values_gauss"], self.FIELDS
        )
        np.testing.assert_allclose(
            np.asarray(cube.metadata["model_field_vector_mT"])[:, 0],
            self.FIELDS * 0.1,
        )
        self.assertEqual(cube.metadata["field_values_gauss"], self.FIELDS.tolist())
        self.assertEqual(list(cube.scan_axis_values), self.FIELDS.tolist())

    def test_polarity_mixed_only_where_directions_differ_at_same_field(self):
        # Schmitt trigger at +-0.5 G. Forward (from '-'): -,-,-,+,+.
        # Reverse (from '+'), in field order: -,-,+,+,+. Only 0 G differs.
        magnet = CommandMagnet(hysteresis_gauss=0.5)
        cube, _, _ = run(points=5, magnet=magnet)
        self.assertEqual(
            [p[0] for p in cube.metadata["model_polarity"]],
            ["-", "-", "mixed", "+", "+"],
        )


class AverageFileTests(unittest.TestCase):
    def _worker_run(self, directory, num_scans, points=5, save_raw_scans=True):
        config = {
            "field_start": FIELD_START, "field_stop": FIELD_STOP,
            "field_points": points, "field_axis": "X",
            "settling_time_ms": 0, "averages": 1,
            "averaging_enabled": True, "num_scans": num_scans,
            "save_raw_scans": save_raw_scans, "alternate_direction": True,
        }
        magnet = CommandMagnet()
        output_path = Path(directory) / "zero_field_test_average.h5"
        worker = ZeroFieldWorker(
            None, DriftCamera(magnet), magnet, config, None, {}, output_path
        )
        finished = []
        worker.finished_signal.connect(lambda cube, *_: finished.append(cube))
        worker.start()
        return output_path, finished[0]

    def test_direction_datasets_and_metadata_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cube = self._worker_run(directory, num_scans=2)
            loaded = ImageCube.load(path)

            self.assertEqual(
                set(loaded.extra_datasets), {"forward_average", "reverse_average"}
            )
            for name in ("forward_average", "reverse_average"):
                np.testing.assert_allclose(
                    loaded.extra_datasets[name], cube.extra_datasets[name]
                )
                self.assertEqual(loaded.extra_datasets[name].shape, loaded.data.shape)
            np.testing.assert_allclose(loaded.data, cube.data)

            metadata = loaded.metadata
            self.assertTrue(metadata["alternate_direction"])
            self.assertEqual(metadata["forward_scan_count"], 1)
            self.assertEqual(metadata["reverse_scan_count"], 1)
            self.assertEqual(metadata["scan_directions"], ["forward", "reverse"])
            self.assertEqual(len(metadata["scan_point_times_s"]), 2)
            for times in metadata["scan_point_times_s"]:
                self.assertEqual(len(times), 5)
                self.assertTrue(np.all(np.diff(times) > 0))
            # Per-scan keys do not leak into the average.
            self.assertNotIn("point_time_s", metadata)
            self.assertNotIn("sweep_direction", metadata)
            self.assertIn("field_order", metadata["field_value_definitions"])

            # Raw per-scan files: acquisition order, direction recorded.
            raw_2 = ImageCube.load(Path(directory) / "zero_field_test_scan_002.h5")
            self.assertEqual(raw_2.metadata["sweep_direction"], "reverse")
            self.assertEqual(
                list(raw_2.scan_axis_values), np.linspace(1.0, -1.0, 5).tolist()
            )
            self.assertEqual(len(raw_2.metadata["point_time_s"]), 5)
            np.testing.assert_allclose(raw_2.data[::-1], cube.extra_datasets["reverse_average"])
            raw_1 = ImageCube.load(Path(directory) / "zero_field_test_scan_001.h5")
            self.assertEqual(raw_1.metadata["sweep_direction"], "forward")

    def test_direction_with_no_completed_scan_has_no_dataset(self):
        # Stop as scan 2 (the first reverse scan) starts: it acquires
        # nothing and is never folded into any average.
        holder = {}

        def stop_at_scan_two(scan_index, _total):
            if scan_index == 2:
                holder["experiment"].stop()

        magnet = CommandMagnet()
        experiment = make_experiment(
            magnet, DriftCamera(magnet), num_scans=4,
            scan_started_callback=stop_at_scan_two,
        )
        holder["experiment"] = experiment
        cube = experiment.run()
        self.assertEqual(set(cube.extra_datasets), {"forward_average"})
        self.assertEqual(cube.metadata["completed_scans"], 1)
        self.assertEqual(cube.metadata["reverse_scan_count"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "average.h5"
            cube.save(path)
            self.assertEqual(set(ImageCube.load(path).extra_datasets), {"forward_average"})

    def test_partial_reverse_scan_is_not_folded_in(self):
        holder = {}
        magnet = CommandMagnet()

        class StopMidScanTwo(DriftCamera):
            def snap(self):
                frame = super().snap()
                if self.frame_count == 7:  # scan 1 = 5 frames, scan 2 frame 2
                    holder["experiment"].stop()
                return frame

        experiment = make_experiment(magnet, StopMidScanTwo(magnet), num_scans=2)
        holder["experiment"] = experiment
        cube = experiment.run()
        np.testing.assert_allclose(cube.data, cube.extra_datasets["forward_average"])
        self.assertNotIn("reverse_average", cube.extra_datasets)
        self.assertEqual(cube.metadata["scan_directions"], ["forward"])

    def test_offline_plane_averaging_refuses_reverse_raw_scan(self):
        from analysis.zero_field_averaging import average_plane_range

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, direction in enumerate(("forward", "reverse"), start=1):
                path = Path(directory) / f"scan_{index:03d}.h5"
                ImageCube(
                    data=np.zeros((2, 2, 2)),
                    metadata={"sweep_direction": direction},
                    scan_axis_values=[0.0, 1.0],
                ).save(path)
                paths.append(path)
            with self.assertRaisesRegex(ValueError, "stop -> start"):
                average_plane_range(paths, Path(directory) / "planes", 0, 2)
            # Forward-only input is still accepted.
            average_plane_range(paths[:1], Path(directory) / "planes", 0, 2)

    def test_extra_datasets_round_trip_npz(self):
        cube = ImageCube(
            data=np.zeros((2, 2, 2)),
            extra_datasets={"forward_average": np.ones((2, 2, 2))},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cube.npz"
            cube.save(path)
            loaded = ImageCube.load(path)
        np.testing.assert_array_equal(loaded.extra_datasets["forward_average"], 1.0)

    def test_extra_dataset_may_not_shadow_a_reserved_name(self):
        cube = ImageCube(data=np.zeros((1, 2, 2)), extra_datasets={"data": np.ones(1)})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                cube.save(Path(directory) / "cube.h5")


class AlternateOffTests(unittest.TestCase):
    """alternate_direction off: same averaged output as before the mode
    existed, apart from point_time_s / scan_point_times_s and the
    alternate_direction key."""

    def test_off_matches_acquisition_order_average_and_adds_no_direction_data(self):
        cube, _, magnet = run(points=5, num_scans=2, alternate_direction=False)
        forward = np.linspace(FIELD_START, FIELD_STOP, 5).round(9).tolist()
        self.assertEqual(magnet.swept_gauss_per_scan(5), [forward, forward])

        # DriftCamera: frame n = 1000 + 2n - dip. Scan 1 is frames 0-4,
        # scan 2 frames 5-9, both in field order.
        dip = DriftCamera(magnet).dip(np.asarray(forward))
        expected = 1000.0 + 2.0 * (np.arange(5) + 2.5) - dip
        np.testing.assert_allclose(cube.data[:, 0, 0], expected)

        self.assertEqual(cube.extra_datasets, {})
        self.assertFalse(cube.metadata["alternate_direction"])
        for key in ("scan_directions", "forward_scan_count",
                    "reverse_scan_count", "sweep_direction", "point_time_s"):
            self.assertNotIn(key, cube.metadata)
        self.assertEqual(len(cube.metadata["scan_point_times_s"]), 2)

    def test_default_is_off(self):
        magnet = CommandMagnet()
        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=DriftCamera(magnet), magnet=magnet,
            field_start=FIELD_START, field_stop=FIELD_STOP, field_points=3,
            field_axis="X", averaging_enabled=True, num_scans=2,
        )
        experiment.run()
        forward = np.linspace(FIELD_START, FIELD_STOP, 3).round(9).tolist()
        self.assertEqual(magnet.swept_gauss_per_scan(3), [forward, forward])

    def test_single_scan_records_point_times(self):
        cube, _, _ = run(points=4, averaging_enabled=False, num_scans=1)
        times = cube.metadata["point_time_s"]
        self.assertEqual(len(times), 4)
        self.assertTrue(np.all(np.diff(times) > 0))
        self.assertNotIn("scan_point_times_s", cube.metadata)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.widget = ZeroFieldWidget()
        _KEEP_ALIVE.append(cls.widget)

    def setUp(self):
        w = self.widget
        w.averaging_enabled_check.setChecked(False)
        w.alternate_direction_check.setChecked(False)
        w.num_scans_spin.setValue(2)

    def test_defaults_off_and_disabled_without_averaging(self):
        w = self.widget
        self.assertFalse(w.alternate_direction_check.isChecked())
        self.assertFalse(w.alternate_direction_check.isEnabled())
        self.assertFalse(w.get_config()["alternate_direction"])
        self.assertIn("stop→start", w.alternate_direction_check.toolTip())

    def test_enabled_with_averaging_and_reported(self):
        w = self.widget
        w.averaging_enabled_check.setChecked(True)
        self.assertTrue(w.alternate_direction_check.isEnabled())
        w.alternate_direction_check.setChecked(True)
        self.assertTrue(w.get_config()["alternate_direction"])

    def test_disabled_but_checked_reports_false(self):
        w = self.widget
        w.averaging_enabled_check.setChecked(True)
        w.alternate_direction_check.setChecked(True)
        w.averaging_enabled_check.setChecked(False)
        self.assertTrue(w.alternate_direction_check.isChecked())
        self.assertFalse(w.alternate_direction_check.isEnabled())
        self.assertFalse(w.get_config()["alternate_direction"])

    def test_odd_scan_count_shows_warning_without_blocking(self):
        w = self.widget
        w.averaging_enabled_check.setChecked(True)
        w.alternate_direction_check.setChecked(True)
        self.assertTrue(w.alternate_direction_warning.isHidden())
        w.num_scans_spin.setValue(3)
        self.assertFalse(w.alternate_direction_warning.isHidden())
        self.assertTrue(w.get_config()["alternate_direction"])
        w.alternate_direction_check.setChecked(False)
        self.assertTrue(w.alternate_direction_warning.isHidden())
        w.alternate_direction_check.setChecked(True)
        w.averaging_enabled_check.setChecked(False)
        self.assertTrue(w.alternate_direction_warning.isHidden())

    def test_scan_label_shows_direction(self):
        w = self.widget
        w.update_scan_progress(2, 4, "stop→start")
        self.assertEqual(w.current_scan_label.text(), "Current scan: 2 / 4 (stop→start)")
        w.update_scan_progress(1, 1)
        self.assertEqual(w.current_scan_label.text(), "Current scan: 1 / 1")


if __name__ == "__main__":
    unittest.main()
