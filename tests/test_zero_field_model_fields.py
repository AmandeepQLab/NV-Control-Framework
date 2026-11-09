"""Hardware-free tests for the modelled field recorded per ZFE point.

field_values_gauss is the requested linspace. model_field_values_gauss /
model_field_vector_mT are what the calibration model says was commanded,
and model_polarity is the relay branch -- the only thing that differs in a
branch overlap, where the field matches the request on either branch.
"""

import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.image_cube import ImageCube
from gui.zero_field_worker import ZeroFieldWorker
from hardware.magnet.magnet import Magnet
from tests.test_zero_field_timing import make_camera, make_experiment, make_magnet


def make_gap_magnet():
    """make_magnet() (zero offsets, sim supplies) but with a rig-like Y
    branch gap: positive intercept 0.0066 mT above negative 0.0062 mT."""
    base = make_magnet()
    config = copy.deepcopy(base.config)
    config["Y"]["magnetic_field_calibration"] = {
        "positive": {"magnetic_field_ratio": 0.74, "magnetic_field_offset": 0.0066},
        "negative": {"magnetic_field_ratio": 0.74, "magnetic_field_offset": 0.0062},
    }
    return Magnet(config, base.power_supplies, base.pulse_streamer, scpi_write_delay_s=0.0)


# Y sweep whose last point (0.064 G = 0.0064 mT) lies inside the gap.
GAP_SWEEP = dict(field_start=-0.1, field_stop=0.064, field_points=3, field_axis="Y")
# X sweep over a zero-offset magnet: -1, -0.5, 0, +0.5, +1 G.
X_SWEEP = dict(field_start=-1.0, field_stop=1.0, field_points=5, field_axis="X")


class ModelFieldTests(unittest.TestCase):
    def test_branch_gap_point_records_intercept_not_request(self):
        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=make_camera(), magnet=make_gap_magnet(),
            **GAP_SWEEP,
        )
        cube = experiment.run()
        requested = cube.metadata["field_values_gauss"]
        model = cube.metadata["model_field_values_gauss"]

        np.testing.assert_allclose(requested, [-0.1, -0.018, 0.064])
        np.testing.assert_allclose(model[:2], requested[:2])
        # In the gap the supply holds 0 A on the retained (negative) branch,
        # so the model is that branch's intercept: 0.0062 mT = 0.062 G.
        self.assertAlmostEqual(model[2], 0.062)
        self.assertNotAlmostEqual(model[2], requested[2])
        self.assertAlmostEqual(cube.metadata["model_field_vector_mT"][2][1], 0.0062)
        self.assertEqual(cube.metadata["model_polarity"][2][1], "-")

    def test_vector_shape_and_consistency_with_swept_axis(self):
        cube = make_experiment(**X_SWEEP).run()
        vectors = np.asarray(cube.metadata["model_field_vector_mT"], dtype=float)
        self.assertEqual(vectors.shape, (5, 3))
        np.testing.assert_allclose(
            vectors[:, 0] * 10.0, cube.metadata["model_field_values_gauss"]
        )
        np.testing.assert_allclose(vectors[:, 1:], 0.0)  # other axes zeroed

    def test_overlap_branch_is_visible_only_in_polarity(self):
        # Zero offsets: 0 G is reachable on both branches, so the model field
        # equals the request there; the retained branch (negative, coming
        # from -0.5 G) is what model_polarity shows.
        cube = make_experiment(**X_SWEEP).run()
        np.testing.assert_allclose(
            cube.metadata["model_field_values_gauss"], cube.metadata["field_values_gauss"]
        )
        self.assertEqual(
            [p[0] for p in cube.metadata["model_polarity"]], ["-", "-", "-", "+", "+"]
        )

    def test_requested_field_values_are_unchanged(self):
        cube = make_experiment(**X_SWEEP).run()
        expected = np.linspace(-1.0, 1.0, 5).tolist()
        self.assertEqual(cube.metadata["field_values_gauss"], expected)
        self.assertEqual(list(cube.scan_axis_values), expected)
        self.assertIn("model_field_values_gauss", cube.metadata["field_value_definitions"])

    def test_stopped_sweep_truncates_model_lists(self):
        holder = {}

        def stop_after_two(*_):
            if len(holder["experiment"].image_cube.data) == 2:
                holder["experiment"].stop()

        experiment = make_experiment(**X_SWEEP, live_update_callback=stop_after_two)
        holder["experiment"] = experiment
        cube = experiment.run()
        for key in ("model_field_values_gauss", "model_field_vector_mT", "model_polarity"):
            self.assertEqual(len(cube.metadata[key]), 2, key)

    def test_average_means_fields_and_marks_differing_polarity_mixed(self):
        def cube_with(field_gauss, vector, polarity):
            return ImageCube(data=None, metadata={
                "model_field_values_gauss": [field_gauss],
                "model_field_vector_mT": [vector],
                "model_polarity": [polarity],
            })

        average = cube_with(0.10, [0.010, 0.0, 0.0], ["-", "+", "+"])
        scan_2 = cube_with(0.20, [0.020, 0.0, 0.0], ["+", "+", "+"])
        ZeroFieldExperiment._merge_model_fields(average, scan_2, 2)
        self.assertAlmostEqual(average.metadata["model_field_values_gauss"][0], 0.15)
        np.testing.assert_allclose(average.metadata["model_field_vector_mT"][0], [0.015, 0, 0])
        self.assertEqual(average.metadata["model_polarity"][0], ["mixed", "+", "+"])


class ModelFieldHdf5Tests(unittest.TestCase):
    def _config(self, **overrides):
        config = {**X_SWEEP, "settling_time_ms": 0, "averages": 1}
        config.update(overrides)
        return config

    def _assert_model_fields(self, path):
        metadata = ImageCube.load(path).metadata
        np.testing.assert_allclose(
            metadata["model_field_values_gauss"], np.linspace(-1.0, 1.0, 5)
        )
        self.assertEqual(np.asarray(metadata["model_field_vector_mT"]).shape, (5, 3))
        self.assertEqual([p[0] for p in metadata["model_polarity"]], ["-", "-", "-", "+", "+"])
        self.assertEqual(metadata["field_values_gauss"], np.linspace(-1.0, 1.0, 5).tolist())

    def test_single_scan_streaming_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test.h5"
            ZeroFieldWorker(
                None, make_camera(), make_magnet(), self._config(), None, {}, output_path
            ).start()
            self._assert_model_fields(output_path)

    def test_multi_scan_average_and_raw_scan_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test_average.h5"
            config = self._config(averaging_enabled=True, num_scans=2, save_raw_scans=True)
            ZeroFieldWorker(
                None, make_camera(), make_magnet(), config, None, {}, output_path
            ).start()
            self._assert_model_fields(output_path)
            raw = sorted(Path(directory).glob("zero_field_test_scan_*.h5"))
            self.assertEqual(len(raw), 2)
            for path in raw:
                self._assert_model_fields(path)


if __name__ == "__main__":
    unittest.main()
