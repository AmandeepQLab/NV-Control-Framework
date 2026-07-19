"""Unit tests for incremental multi-scan Zero Field averaging."""

import unittest

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.analysis.zero_field import mean_fluorescence_vs_field


class FakeCamera:
    streaming = False

    def __init__(self, frames):
        self.frames = iter(frames)

    def snap(self):
        return np.asarray(next(self.frames))


class FakeMagnet:
    def enable(self):
        pass

    def disable(self):
        pass

    def get_vector(self):
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    def set_vector(self, **_fields):
        pass


def run_experiment(frames, **kwargs):
    experiment = ZeroFieldExperiment(
        hardware_manager=None,
        camera=FakeCamera(frames),
        magnet=FakeMagnet(),
        field_start=-1.0,
        field_stop=1.0,
        field_points=2,
        field_axis="X",
        settling_time_ms=0,
        averages=1,
        **kwargs,
    )
    return experiment.run(), experiment


class ZeroFieldAveragingTests(unittest.TestCase):
    def test_one_scan_reproduces_current_data(self):
        frames = [np.ones((2, 2)), np.full((2, 2), 3.0)]

        cube, _ = run_experiment(frames, averaging_enabled=False, num_scans=1)

        np.testing.assert_array_equal(cube.data, np.asarray(frames))

    def test_identical_scans_preserve_original_data(self):
        frames = [
            np.ones((2, 2)), np.full((2, 2), 3.0),
            np.ones((2, 2)), np.full((2, 2), 3.0),
        ]

        cube, _ = run_experiment(frames, averaging_enabled=True, num_scans=2)

        np.testing.assert_allclose(
            cube.data, np.asarray([np.ones((2, 2)), np.full((2, 2), 3.0)])
        )

    def test_averaging_noisy_scans_reduces_noise(self):
        clean = np.full((2, 2), 10.0)
        noise = np.array([[2.0, -2.0], [-2.0, 2.0]])
        frames = [clean + noise, clean - noise, clean - noise, clean + noise]

        cube, _ = run_experiment(frames, averaging_enabled=True, num_scans=2)

        self.assertLess(np.std(cube.data[0]), np.std(frames[0]))
        np.testing.assert_allclose(cube.data, np.asarray([clean, clean]))

    def test_metadata_and_analysis_of_averaged_cube(self):
        frames = [
            np.full((2, 2), 2.0), np.full((2, 2), 4.0),
            np.full((2, 2), 6.0), np.full((2, 2), 8.0),
        ]

        cube, experiment = run_experiment(
            frames, averaging_enabled=True, num_scans=2, save_raw_scans=True
        )
        fields, signals = mean_fluorescence_vs_field(cube)

        self.assertEqual(cube.metadata["averaging_enabled"], True)
        self.assertEqual(cube.metadata["num_scans"], 2)
        self.assertEqual(cube.metadata["save_raw_scans"], True)
        self.assertEqual(cube.metadata["completed_scans"], 2)
        self.assertEqual(len(experiment.raw_scans), 2)
        np.testing.assert_allclose(fields, [-1.0, 1.0])
        np.testing.assert_allclose(signals, [4.0, 6.0])


if __name__ == "__main__":
    unittest.main()
