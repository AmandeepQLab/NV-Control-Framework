"""Hardware-free tests for reusable ODMR statistics processing."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from framework.analysis.odmr_statistics import (
    calculate_odmr_statistics,
    get_average_statistics,
    load_odmr_statistics,
    validate_odmr_statistics,
)
from framework.image_cube import ImageCube


def _cube(means, sds, repeats=None, **metadata_overrides):
    means = np.asarray(means, dtype=np.float64)
    sds = np.asarray(sds, dtype=np.float64)
    if means.ndim == 1:
        means = means[:, None, None, None]
    if sds.ndim == 1:
        sds = sds[:, None, None, None]
    data = np.stack((means, sds), axis=1)
    n_averages, _, n_frequencies, height, width = data.shape
    frequencies = np.arange(n_frequencies, dtype=np.float64) + 2.8e9
    if repeats is None:
        repeats = [4] * n_averages
    metadata = {
        "configured_averages": max(n_averages, 1),
        "completed_averages": n_averages,
        "n_averages": n_averages,
        "configured_repeats": max(repeats, default=4),
        "n_repeats_per_average": list(repeats),
        "experiment_complete": n_averages > 0,
    }
    metadata.update(metadata_overrides)
    return ImageCube(
        data=data,
        axes={
            "axis_order": ["average", "statistic", "frequency", "y", "x"],
            "average": list(range(n_averages)),
            "statistic": ["normalized_mean", "normalized_sd"],
            "frequency": frequencies.tolist(),
            "y": list(range(height)),
            "x": list(range(width)),
        },
        metadata=metadata,
        experiment_type="ODMR",
        scan_axis_name="frequency",
        scan_axis_unit="Hz",
        scan_axis_values=frequencies,
    )


class LoadAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.cube = _cube([10.0, 20.0], [2.0, 4.0])

    def test_load_accepts_imagecube_and_hdf5_path(self):
        self.assertIs(load_odmr_statistics(self.cube), self.cube)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "odmr_statistics.h5"
            self.cube.save(path)
            loaded = load_odmr_statistics(path)
        self.assertEqual(loaded.experiment_type, "ODMR")
        self.assertEqual(loaded.data.dtype, np.dtype("float64"))
        np.testing.assert_array_equal(loaded.data, self.cube.data)

    def test_valid_cube_schema(self):
        self.assertTrue(validate_odmr_statistics(self.cube))

    def test_average_selection_preserves_frequency_and_does_not_mutate_cube(self):
        frequency, mean, sd = get_average_statistics(self.cube, 1)
        np.testing.assert_array_equal(frequency, self.cube.scan_axis_values)
        np.testing.assert_array_equal(mean, self.cube.data[1, 0])
        np.testing.assert_array_equal(sd, self.cube.data[1, 1])
        mean[:] = -100
        np.testing.assert_array_equal(self.cube.data[1, 0], 20.0)

    def test_average_index_is_validated(self):
        for index in (-1, 2, 1.5, True):
            with self.subTest(index=index), self.assertRaises((ValueError, IndexError)):
                get_average_statistics(self.cube, index)

    def test_invalid_schema_is_rejected_with_clear_reason(self):
        bad = _cube([1.0], [0.1])
        bad.axes["axis_order"] = ["average", "frequency", "statistic", "y", "x"]
        with self.assertRaisesRegex(ValueError, "axis_order"):
            validate_odmr_statistics(bad)

        bad = _cube([1.0], [0.1])
        bad.axes["statistic"] = ["mean", "sd"]
        with self.assertRaisesRegex(ValueError, "statistic labels"):
            validate_odmr_statistics(bad)

        bad = _cube([1.0], [0.1])
        bad.experiment_type = "Zero Field"
        with self.assertRaisesRegex(ValueError, "experiment_type"):
            validate_odmr_statistics(bad)

        bad = _cube([1.0], [0.1])
        bad.axes["frequency"] = [1.0]
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_odmr_statistics(bad)

    def test_completion_and_repeat_metadata_must_match_cube(self):
        bad = _cube([1.0], [0.1], completed_averages=0)
        with self.assertRaisesRegex(ValueError, "average dimension"):
            validate_odmr_statistics(bad)
        bad = _cube([1.0, 2.0], [0.1, 0.2], repeats=[4])
        with self.assertRaisesRegex(ValueError, "one count per completed average"):
            validate_odmr_statistics(bad)


class CombinedStatisticsTests(unittest.TestCase):
    def test_final_mean_fast_noise_slow_noise_and_combined_sem(self):
        cube = _cube([10.0, 12.0], [2.0, 4.0], repeats=[4, 16])
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.final_mean.shape, (1, 1, 1))
        self.assertAlmostEqual(result.final_mean.item(), 11.0)
        self.assertAlmostEqual(result.fast_sd.item(), np.sqrt(10.0))
        # (2^2/4 + 4^2/16) / 2^2 = 0.5
        self.assertAlmostEqual(result.fast_sem.item(), np.sqrt(0.5))
        # sample variance([10, 12]) - mean([1, 1]) = 2 - 1 = 1
        self.assertAlmostEqual(result.slow_variance.item(), 1.0)
        self.assertAlmostEqual(result.slow_sd.item(), 1.0)
        self.assertAlmostEqual(result.slow_sem.item(), 1.0 / np.sqrt(2.0))
        self.assertAlmostEqual(result.combined_sem.item(), 1.0)
        self.assertTrue(result.slow_noise_estimable)

    def test_slow_variance_clips_at_zero(self):
        cube = _cube([10.0, 10.1], [3.0, 3.0], repeats=[2, 2])
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.slow_variance.item(), 0.0)
        self.assertEqual(result.slow_sd.item(), 0.0)
        self.assertEqual(result.slow_sem.item(), 0.0)

    def test_one_average_has_fast_sem_and_unavailable_slow_noise(self):
        cube = _cube([8.0], [6.0], repeats=[9])
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.final_mean.item(), 8.0)
        self.assertEqual(result.fast_sd.item(), 6.0)
        self.assertEqual(result.fast_sem.item(), 2.0)
        self.assertEqual(result.slow_variance.item(), 0.0)
        self.assertFalse(result.slow_noise_estimable)
        self.assertFalse(result.slow_noise_estimable_per_pixel.item())

    def test_zero_completed_averages_return_nan_maps_and_empty_repeat_metadata(self):
        cube = _cube(
            np.empty((0, 2, 2, 2)), np.empty((0, 2, 2, 2)), repeats=[],
            configured_averages=2, configured_repeats=4,
        )
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.final_mean.shape, (2, 2, 2))
        self.assertTrue(np.isnan(result.final_mean).all())
        self.assertTrue(np.isnan(result.fast_sem).all())
        self.assertEqual(result.n_averages, 0)
        self.assertEqual(result.n_repeats_per_average, ())
        self.assertFalse(result.slow_noise_estimable)

    def test_nan_values_are_omitted_per_pixel_and_all_invalid_pixel_stays_nan(self):
        means = np.array([
            [[[1.0, np.nan]]],
            [[[np.nan, np.nan]]],
            [[[5.0, np.nan]]],
        ])
        sds = np.array([
            [[[2.0, np.nan]]],
            [[[3.0, np.nan]]],
            [[[4.0, np.nan]]],
        ])
        cube = _cube(means, sds, repeats=[4, 4, 4])
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.final_mean[0, 0, 0], 3.0)
        self.assertEqual(result.valid_average_count[0, 0, 0], 2)
        self.assertTrue(np.isnan(result.final_mean[0, 0, 1]))
        self.assertEqual(result.valid_average_count[0, 0, 1], 0)
        for name in ("fast_sd", "slow_sd", "fast_sem", "slow_sem", "combined_sem"):
            self.assertTrue(np.isnan(getattr(result, name)[0, 0, 1]))

    def test_pixelwise_shape_frequency_and_spatial_axes_are_preserved(self):
        means = np.arange(2 * 3 * 2 * 4, dtype=float).reshape(2, 3, 2, 4)
        sds = np.ones_like(means)
        cube = _cube(means, sds, repeats=[3, 5])
        result = calculate_odmr_statistics(cube)
        self.assertEqual(result.final_mean.shape, (3, 2, 4))
        self.assertEqual(result.slow_sd.shape, (3, 2, 4))
        np.testing.assert_array_equal(result.frequency_values, cube.scan_axis_values)
        self.assertEqual(result.spatial_shape, (2, 4))


if __name__ == "__main__":
    unittest.main()
