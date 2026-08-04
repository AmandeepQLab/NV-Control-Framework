"""Unit tests for incremental multi-scan Zero Field averaging."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.analysis.zero_field import mean_fluorescence_vs_field
from framework.camera_ownership import register_camera_state_restorer
from gui.zero_field_window import ZeroFieldWindow
from gui.zero_field_worker import ZeroFieldWorker
from gui.image_inspection import line_profile, pixel_value


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


class RecordingMagnet(FakeMagnet):
    def __init__(self, events):
        self.events = events

    def set_vector(self, **_fields):
        self.events.append("set_current")


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
    def test_image_inspection_reads_cached_pixels_and_line_profiles(self):
        image = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        original = image.copy()

        self.assertEqual(pixel_value(image, 2, 1), 6.0)
        self.assertIsNone(pixel_value(image, 3, 1))
        distances, values = line_profile(image, (0, 0), (2, 0))

        np.testing.assert_allclose(distances, [0.0, 1.0, 2.0])
        np.testing.assert_allclose(values, [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(image, original)

    def test_display_settings_do_not_change_line_profile_values(self):
        image = np.array([[2.0, 5.0], [7.0, 11.0]])
        reference = np.array([[1.0, 1.0], [1.0, 1.0]])
        displayed = ZeroFieldWindow._display_image_for_mode(
            "Difference Image", image, reference
        )
        _, expected_values = line_profile(displayed, (0, 0), (1, 1))

        ZeroFieldWindow._prepare_display_parameters(
            displayed, "Auto", 0.0, 1.0, "Gray"
        )
        _, auto_values = line_profile(displayed, (0, 0), (1, 1))
        ZeroFieldWindow._prepare_display_parameters(
            displayed, "Manual", -10.0, 20.0, "Magma"
        )
        _, manual_values = line_profile(displayed, (0, 0), (1, 1))

        np.testing.assert_array_equal(auto_values, expected_values)
        np.testing.assert_array_equal(manual_values, expected_values)
    def test_display_parameters_use_requested_scaling_without_changing_image(self):
        image = np.array([[1.0, 3.0], [5.0, 7.0]])
        original = image.copy()

        auto_parameters = ZeroFieldWindow._prepare_display_parameters(
            image, "Auto", -10.0, 10.0, "Viridis"
        )
        manual_parameters = ZeroFieldWindow._prepare_display_parameters(
            image, "Manual", -2.0, 12.0, "Magma"
        )

        self.assertEqual(auto_parameters["levels"], (1.0, 7.0))
        self.assertEqual(auto_parameters["colormap_name"], "Viridis")
        self.assertEqual(manual_parameters["levels"], (-2.0, 12.0))
        self.assertEqual(manual_parameters["colormap_name"], "Magma")
        np.testing.assert_array_equal(image, original)

    def test_difference_display_uses_one_reference_without_changing_raw_images(self):
        first_image = np.array([[1, 2], [3, 4]], dtype=np.uint16)
        later_image = np.array([[5, 8], [11, 14]], dtype=np.uint16)

        reference = ZeroFieldWindow._capture_reference_image(None, first_image)
        retained_reference = ZeroFieldWindow._capture_reference_image(
            reference, later_image
        )
        difference = ZeroFieldWindow._display_image_for_mode(
            "Difference Image", later_image, retained_reference
        )

        self.assertIs(retained_reference, reference)
        np.testing.assert_array_equal(
            ZeroFieldWindow._display_image_for_mode(
                "Difference Image", first_image, reference
            ),
            np.zeros_like(first_image, dtype=np.float64),
        )
        np.testing.assert_array_equal(difference, [[4.0, 6.0], [8.0, 10.0]])
        np.testing.assert_array_equal(first_image, [[1, 2], [3, 4]])
        np.testing.assert_array_equal(later_image, [[5, 8], [11, 14]])
        self.assertIs(
            ZeroFieldWindow._display_image_for_mode(
                "Raw Image", later_image, reference
            ),
            later_image,
        )

    def test_each_field_point_has_one_deterministic_averaged_measurement(self):
        events = []

        class RecordingCamera(FakeCamera):
            def snap(self):
                events.append("camera_frame")
                return super().snap()

        live_updates = []
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=RecordingCamera([
                np.full((2, 2), 1.0), np.full((2, 2), 3.0),
                np.full((2, 2), 5.0), np.full((2, 2), 7.0),
            ]),
            magnet=RecordingMagnet(events),
            field_start=-1.0,
            field_stop=1.0,
            field_points=2,
            field_axis="X",
            settling_time_ms=0,
            averages=2,
            live_update_callback=lambda field, image: live_updates.append(
                (field, np.array(image, copy=True))
            ),
        )

        cube = experiment.run()

        self.assertEqual(events, [
            "set_current", "camera_frame", "camera_frame",
            "set_current", "camera_frame", "camera_frame",
        ])
        self.assertEqual(cube.data.shape[0], 2)
        np.testing.assert_allclose(cube.data, [
            np.full((2, 2), 2.0), np.full((2, 2), 6.0),
        ])
        self.assertEqual(cube.metadata["experiment_name"], "Zero Field")
        self.assertTrue(cube.metadata["acquisition_started_at_utc"])
        self.assertEqual(cube.metadata["field_values_gauss"], [-1.0, 1.0])
        self.assertEqual(cube.metadata["field_point_count"], 2)
        self.assertEqual(cube.metadata["settling_time_ms"], 0)
        self.assertEqual(cube.metadata["averages_per_point"], 2)
        self.assertEqual(cube.metadata["image_height_px"], 2)
        self.assertEqual(cube.metadata["image_width_px"], 2)
        self.assertEqual(cube.metadata["image_dtype"], str(cube.data.dtype))
        self.assertEqual(len(live_updates), 2)
        np.testing.assert_allclose(
            [image for _, image in live_updates], cube.data
        )

    def test_validation_rejects_field_vector_metadata_mismatch(self):
        cube, experiment = run_experiment(
            [np.ones((2, 2)), np.full((2, 2), 2.0)]
        )
        cube.metadata["field_values_gauss"] = [-1.0]

        with self.assertRaisesRegex(ValueError, "field vector length"):
            experiment._validate_image_cube(cube)

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

    def test_worker_emits_one_live_and_progress_update_per_stored_point(self):
        config = {
            "field_start": -1.0,
            "field_stop": 1.0,
            "field_points": 2,
            "field_axis": "X",
            "settling_time_ms": 0,
            "averages": 2,
            "averaging_enabled": False,
            "num_scans": 1,
            "save_raw_scans": False,
        }
        live_updates = []
        fluorescence_updates = []
        progress_updates = []

        class CountingCamera(FakeCamera):
            def __init__(self, frames):
                super().__init__(frames)
                self.snap_count = 0

            def snap(self):
                self.snap_count += 1
                return super().snap()

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test_average.npz"
            camera = CountingCamera([
                np.full((2, 2), 1.0), np.full((2, 2), 3.0),
                np.full((2, 2), 5.0), np.full((2, 2), 7.0),
            ])
            worker = ZeroFieldWorker(
                None,
                camera,
                FakeMagnet(),
                config,
                None,
                {},
                output_path,
            )
            worker.live_update_signal.connect(
                lambda field, signal, image: live_updates.append((field, signal, image))
            )
            worker.fluorescence_update_signal.connect(
                lambda fields, signals: fluorescence_updates.append(
                    (np.array(fields, copy=True), np.array(signals, copy=True))
                )
            )
            worker.progress_signal.connect(
                lambda current, total: progress_updates.append((current, total))
            )

            worker.start()
            cube = worker.experiment.image_cube

        self.assertEqual(cube.data.shape[0], config["field_points"])
        self.assertEqual(camera.snap_count, 4)
        self.assertEqual(len(live_updates), config["field_points"])
        self.assertEqual(
            [len(fields) for fields, _ in fluorescence_updates], [1, 2]
        )
        self.assertEqual(
            [len(signals) for _, signals in fluorescence_updates], [1, 2]
        )
        np.testing.assert_allclose(
            fluorescence_updates[-1][0], cube.scan_axis_values
        )
        np.testing.assert_allclose(
            fluorescence_updates[-1][1], [2.0, 6.0]
        )
        self.assertEqual(progress_updates, [(1, 2), (2, 2)])
        np.testing.assert_allclose(
            [image for _, _, image in live_updates], cube.data
        )


if __name__ == "__main__":
    unittest.main()
