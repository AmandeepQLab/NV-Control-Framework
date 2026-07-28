"""Unit tests for incremental multi-scan Zero Field averaging."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.analysis.zero_field import mean_fluorescence_vs_field
from framework.camera_ownership import register_camera_state_restorer
from gui.zero_field_worker import ZeroFieldWorker


class FakeCamera:
    streaming = False

    def __init__(self, frames):
        self.frames = iter(frames)

    def snap(self):
        frame = next(self.frames)
        if isinstance(frame, Exception):
            raise frame
        return np.asarray(frame)

    def set_roi(self, roi):
        self.roi = roi


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

        saved_raw_scans = []
        with tempfile.TemporaryDirectory() as directory:
            def save_raw(index, raw_cube):
                path = Path(directory) / f"scan_{index:03d}.npz"
                raw_cube.save(path)
                saved_raw_scans.append(path)
                return path.name

            cube, experiment = run_experiment(
                frames,
                averaging_enabled=True,
                num_scans=2,
                save_raw_scans=True,
                raw_scan_saver=save_raw,
            )
            self.assertTrue(all(path.exists() for path in saved_raw_scans))
        fields, signals = mean_fluorescence_vs_field(cube)

        self.assertEqual(cube.metadata["averaging_enabled"], True)
        self.assertEqual(cube.metadata["num_scans"], 2)
        self.assertEqual(cube.metadata["save_raw_scans"], True)
        self.assertEqual(cube.metadata["completed_scans"], 2)
        self.assertEqual(experiment.raw_scan_filenames, ["scan_001.npz", "scan_002.npz"])
        self.assertEqual(cube.metadata["raw_scan_filenames"], experiment.raw_scan_filenames)
        np.testing.assert_allclose(fields, [-1.0, 1.0])
        np.testing.assert_allclose(signals, [4.0, 6.0])

    def test_acquisition_roi_is_sent_to_camera_stored_without_analysis_crop(self):
        frames = [np.array([[1.0, 100.0], [3.0, 400.0]]), np.array([[2.0, 200.0], [4.0, 500.0]])]
        roi = (0, 0, 1, 2)

        cube, experiment = run_experiment(frames, acquisition_roi=roi)
        _, signals = mean_fluorescence_vs_field(cube)

        self.assertEqual(experiment.acquisition_roi, roi)
        self.assertEqual(experiment.camera.roi, roi)
        self.assertEqual(cube.metadata["acquisition_roi"], roi)
        np.testing.assert_allclose(signals, [126.0, 176.5])

    def test_exception_restores_registered_main_window_roi(self):
        camera = FakeCamera([RuntimeError("camera failed")])
        register_camera_state_restorer(camera, lambda: camera.set_roi((1, 2, 3, 4)))
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=camera,
            magnet=FakeMagnet(),
            field_start=0,
            field_stop=0,
            field_points=1,
            field_axis="X",
            acquisition_roi=(10, 20, 30, 40),
        )

        with self.assertRaisesRegex(RuntimeError, "camera failed"):
            experiment.run()

        self.assertEqual(camera.roi, (1, 2, 3, 4))

    def test_twenty_scans_retain_only_running_average_cube(self):
        frames = [np.full((2, 2), scan) for scan in range(20) for _ in range(2)]
        saved = []

        with tempfile.TemporaryDirectory() as directory:
            def save_raw(index, raw_cube):
                path = Path(directory) / f"scan_{index:03d}.npz"
                raw_cube.save(path)
                saved.append(path.name)
                return path.name

            cube, experiment = run_experiment(
                frames,
                averaging_enabled=True,
                num_scans=20,
                save_raw_scans=True,
                raw_scan_saver=save_raw,
            )

        self.assertFalse(hasattr(experiment, "raw_scans"))
        self.assertEqual(len(experiment.raw_scan_filenames), 20)
        self.assertEqual(saved, experiment.raw_scan_filenames)
        np.testing.assert_allclose(cube.data, np.full((2, 2, 2), 9.5))

    def test_worker_streams_raw_scans_and_saves_one_average_at_completion(self):
        frames = [np.full((2, 2), value) for value in (1, 3, 5, 7)]
        config = {
            "field_start": -1.0,
            "field_stop": 1.0,
            "field_points": 2,
            "field_axis": "X",
            "settling_time_ms": 0,
            "averages": 1,
            "averaging_enabled": True,
            "num_scans": 2,
            "save_raw_scans": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            average_path = Path(directory) / "zero_field_test_average.npz"
            worker = ZeroFieldWorker(
                None,
                FakeCamera(frames),
                FakeMagnet(),
                config,
                None,
                {},
                average_path,
            )
            worker.start()

            self.assertTrue(average_path.exists())
            self.assertTrue((Path(directory) / "zero_field_test_scan_001.npz").exists())
            self.assertTrue((Path(directory) / "zero_field_test_scan_002.npz").exists())
            self.assertEqual(len(list(Path(directory).glob("*.npz"))), 3)


if __name__ == "__main__":
    unittest.main()
