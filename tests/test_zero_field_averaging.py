"""Unit tests for incremental multi-scan Zero Field averaging."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.analysis.zero_field import mean_fluorescence_vs_field
from framework.camera_ownership import register_camera_state_restorer
from framework.image_cube import ImageCube
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
    """Records set_vector() calls, and can report a caller-chosen vector.

    ``initial_vector`` may be a single dict (returned from every
    get_vector() call, matching FakeMagnet's constant-zero default when
    omitted) or an iterable of dicts (one consumed per get_vector() call,
    to simulate the magnet reading differently at the start of each scan
    in a multi-scan run).
    """

    def __init__(self, events, initial_vector=None):
        self.events = events
        self.vectors = []
        if initial_vector is None:
            self._vector_sequence = None
            self._fixed_vector = {"x": 0.0, "y": 0.0, "z": 0.0}
        elif isinstance(initial_vector, dict):
            self._vector_sequence = None
            self._fixed_vector = dict(initial_vector)
        else:
            self._vector_sequence = iter(initial_vector)
            self._fixed_vector = None

    def get_vector(self):
        if self._vector_sequence is not None:
            return dict(next(self._vector_sequence))
        return dict(self._fixed_vector)

    def set_vector(self, **fields):
        self.events.append("set_current")
        self.vectors.append(dict(fields))


def run_experiment(frames, magnet=None, **kwargs):
    experiment = ZeroFieldExperiment(
        hardware_manager=None,
        camera=FakeCamera(frames),
        magnet=magnet if magnet is not None else FakeMagnet(),
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
            # First "set_current" is setup_scan()'s explicit zero of the
            # non-swept axes (zero_other_axes defaults to True); the rest is
            # the normal one-set_current-per-point pattern.
            "set_current",
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

    def test_zero_other_axes_true_zeros_non_swept_axes_and_records_metadata(self):
        events = []
        magnet = RecordingMagnet(
            events, initial_vector={"x": 0.4, "y": -0.2, "z": 0.0}
        )
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=FakeCamera([np.ones((2, 2)), np.ones((2, 2))]),
            magnet=magnet,
            field_start=0.0,
            field_stop=1.0,
            field_points=2,
            field_axis="Z",
            settling_time_ms=0,
            averages=1,
        )

        cube = experiment.run()

        for vector in magnet.vectors:
            self.assertEqual(vector["bx"], 0.0)
            self.assertEqual(vector["by"], 0.0)
        self.assertEqual(cube.metadata["zero_other_axes"], True)
        self.assertEqual(
            cube.metadata["non_swept_axis_field_mT"], {"x": 0.0, "y": 0.0}
        )
        self.assertEqual(
            cube.metadata["initial_magnet_vector_mT"],
            {"x": 0.4, "y": -0.2, "z": 0.0},
        )
        self.assertEqual(
            cube.metadata["scan_entry_magnet_vectors_mT"],
            [{"x": 0.4, "y": -0.2, "z": 0.0}],
        )

    def test_zero_other_axes_false_preserves_bias_and_warns(self):
        events = []
        magnet = RecordingMagnet(
            events, initial_vector={"x": 0.4, "y": 0.0, "z": 0.0}
        )

        with self.assertLogs(
            "experiments.zero_field_experiment", level="WARNING"
        ) as logs:
            experiment = ZeroFieldExperiment(
                hardware_manager=None,
                camera=FakeCamera([np.ones((2, 2)), np.ones((2, 2))]),
                magnet=magnet,
                field_start=0.0,
                field_stop=1.0,
                field_points=2,
                field_axis="Z",
                settling_time_ms=0,
                averages=1,
                zero_other_axes=False,
            )
            cube = experiment.run()

        self.assertTrue(
            any("bias vector" in message for message in logs.output)
        )
        for vector in magnet.vectors:
            self.assertEqual(vector["bx"], 0.4)
            self.assertEqual(vector["by"], 0.0)
        self.assertEqual(cube.metadata["zero_other_axes"], False)
        self.assertEqual(
            cube.metadata["non_swept_axis_field_mT"], {"x": 0.4, "y": 0.0}
        )
        self.assertEqual(
            cube.metadata["initial_magnet_vector_mT"],
            {"x": 0.4, "y": 0.0, "z": 0.0},
        )

    def test_zero_other_axes_false_with_zero_initial_vector_emits_no_warning(self):
        events = []
        magnet = RecordingMagnet(events)  # defaults to an all-zero vector

        with self.assertNoLogs(
            "experiments.zero_field_experiment", level="WARNING"
        ):
            experiment = ZeroFieldExperiment(
                hardware_manager=None,
                camera=FakeCamera([np.ones((2, 2)), np.ones((2, 2))]),
                magnet=magnet,
                field_start=0.0,
                field_stop=1.0,
                field_points=2,
                field_axis="Z",
                settling_time_ms=0,
                averages=1,
                zero_other_axes=False,
            )
            experiment.run()

    def test_scan_entry_magnet_vector_recorded_per_scan_even_if_it_changes(self):
        # Simulates cleanup_scan() failing to fully zero the magnet between
        # scan 1 and scan 2 -- exactly the failure this metadata exists to
        # reveal rather than hide inside an averaged cube.
        events = []
        magnet = RecordingMagnet(
            events,
            initial_vector=iter(
                [
                    {"x": 0.0, "y": 0.0, "z": 0.0},
                    {"x": 0.05, "y": 0.0, "z": 0.0},
                ]
            ),
        )
        frames = [
            np.full((2, 2), 2.0), np.full((2, 2), 4.0),
            np.full((2, 2), 6.0), np.full((2, 2), 8.0),
        ]

        cube, experiment = run_experiment(
            frames,
            magnet=magnet,
            averaging_enabled=True,
            num_scans=2,
        )

        self.assertEqual(
            cube.metadata["scan_entry_magnet_vectors_mT"],
            [
                {"x": 0.0, "y": 0.0, "z": 0.0},
                {"x": 0.05, "y": 0.0, "z": 0.0},
            ],
        )
        self.assertEqual(
            cube.metadata["initial_magnet_vector_mT"],
            {"x": 0.0, "y": 0.0, "z": 0.0},
        )


class ZeroFieldStreamingTests(unittest.TestCase):
    """stream_scan_path: incremental per-scan writes, gated to .h5 destinations."""

    def test_streaming_matches_non_streaming_regression(self):
        def make_frames():
            return [
                np.full((2, 2), 1.0), np.full((2, 2), 3.0),
                np.full((2, 2), 5.0), np.full((2, 2), 7.0),
            ]

        baseline_cube, _ = run_experiment(
            make_frames(), averaging_enabled=True, num_scans=2,
        )

        with tempfile.TemporaryDirectory() as directory:
            def stream_path(scan_index):
                return Path(directory) / f"scan_{scan_index:03d}.h5"

            streamed_cube, _ = run_experiment(
                make_frames(), averaging_enabled=True, num_scans=2,
                stream_scan_path=stream_path,
            )

        np.testing.assert_allclose(streamed_cube.data, baseline_cube.data)
        self.assertEqual(
            streamed_cube.metadata["completed_scans"],
            baseline_cube.metadata["completed_scans"],
        )

    def test_interrupted_scan_leaves_correct_per_scan_streaming_state(self):
        # Scan 1 completes both points; scan 2 acquires one point, then the
        # camera fails -- simulating a crash partway through the second scan
        # of a multi-scan average.
        frames = [
            np.full((2, 2), 1.0), np.full((2, 2), 2.0),
            np.full((2, 2), 3.0), RuntimeError("camera failed"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            def stream_path(scan_index):
                return Path(directory) / f"scan_{scan_index:03d}.h5"

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
                averaging_enabled=True,
                num_scans=3,
                save_raw_scans=True,
                stream_scan_path=stream_path,
            )

            with self.assertRaisesRegex(RuntimeError, "camera failed"):
                experiment.run()

            # Scan 1 completed and was recorded; scan 2 never got that far.
            self.assertEqual(experiment.raw_scan_filenames, ["scan_001.h5"])

            scan1 = ImageCube.load(Path(directory) / "scan_001.h5")
            self.assertTrue(scan1.metadata["scan_complete"])
            self.assertEqual(scan1.metadata["planes_written"], 2)

            scan2 = ImageCube.load(Path(directory) / "scan_002.h5")
            self.assertFalse(scan2.metadata["scan_complete"])
            self.assertEqual(scan2.metadata["planes_written"], 1)
            np.testing.assert_array_equal(scan2.data[0], np.full((2, 2), 3.0))

    def test_save_raw_scans_false_deletes_streamed_file_after_folding_in(self):
        frames = [np.full((2, 2), 1.0), np.full((2, 2), 3.0)]
        with tempfile.TemporaryDirectory() as directory:
            def stream_path(scan_index):
                return Path(directory) / f"scan_{scan_index:03d}.h5"

            run_experiment(frames, stream_scan_path=stream_path)

            self.assertFalse((Path(directory) / "scan_001.h5").exists())

    def test_save_raw_scans_true_keeps_streamed_file(self):
        frames = [np.full((2, 2), 1.0), np.full((2, 2), 3.0)]
        with tempfile.TemporaryDirectory() as directory:
            def stream_path(scan_index):
                return Path(directory) / f"scan_{scan_index:03d}.h5"

            cube, experiment = run_experiment(
                frames, save_raw_scans=True, stream_scan_path=stream_path,
            )

            self.assertEqual(experiment.raw_scan_filenames, ["scan_001.h5"])
            raw = ImageCube.load(Path(directory) / "scan_001.h5")
            self.assertTrue(raw.metadata["scan_complete"])
            np.testing.assert_allclose(raw.data, cube.data)

    def test_stream_path_is_output_prevents_deletion_regardless_of_save_raw_scans(self):
        # stream_path_is_output=True means this stream file IS the run's own
        # deliverable (the single-scan-direct-streaming case), so it must
        # never be deleted even though save_raw_scans=False would normally
        # trigger the unlink branch.
        frames = [np.full((2, 2), 1.0), np.full((2, 2), 3.0)]
        with tempfile.TemporaryDirectory() as directory:
            stream_path = Path(directory) / "zero_field_test.h5"

            cube, experiment = run_experiment(
                frames,
                save_raw_scans=False,
                stream_scan_path=lambda _scan_index: stream_path,
                stream_path_is_output=True,
            )

            self.assertTrue(stream_path.exists())
            np.testing.assert_allclose(ImageCube.load(stream_path).data, cube.data)

    def test_stream_path_is_output_omits_raw_scan_filenames(self):
        # An empty list would be ambiguous with "raw scans weren't
        # requested" -- since there is no separate raw-scan file in this
        # mode, the key should be absent entirely, not [].
        frames = [np.full((2, 2), 1.0), np.full((2, 2), 3.0)]
        with tempfile.TemporaryDirectory() as directory:
            stream_path = Path(directory) / "zero_field_test.h5"

            cube, experiment = run_experiment(
                frames,
                save_raw_scans=True,
                stream_scan_path=lambda _scan_index: stream_path,
                stream_path_is_output=True,
            )

            self.assertNotIn("raw_scan_filenames", cube.metadata)
            self.assertTrue(stream_path.exists())

    def test_worker_persists_running_average_after_each_scan_and_survives_a_crash(self):
        # Scan 1 completes; scan 2 fails mid-sweep. Exercises the whole
        # ZeroFieldWorker wiring, not just ZeroFieldExperiment directly.
        frames = [
            np.full((2, 2), 1.0), np.full((2, 2), 2.0),
            np.full((2, 2), 3.0), RuntimeError("camera failed"),
        ]
        config = {
            "field_start": -1.0,
            "field_stop": 1.0,
            "field_points": 2,
            "field_axis": "X",
            "settling_time_ms": 0,
            "averages": 1,
            "averaging_enabled": True,
            "num_scans": 3,
            "save_raw_scans": False,
        }
        errors = []

        with tempfile.TemporaryDirectory() as directory:
            average_path = Path(directory) / "zero_field_test_average.h5"
            worker = ZeroFieldWorker(
                None, FakeCamera(frames), FakeMagnet(), config, None, {}, average_path,
            )
            worker.error_signal.connect(errors.append)
            worker.start()

            self.assertEqual(len(errors), 1)
            self.assertIn("camera failed", errors[0])

            self.assertTrue(average_path.exists())
            persisted = ImageCube.load(average_path)
            self.assertEqual(persisted.metadata["completed_scans"], 1)
            self.assertFalse(persisted.metadata["experiment_complete"])
            np.testing.assert_allclose(
                persisted.data,
                np.stack([np.full((2, 2), 1.0), np.full((2, 2), 2.0)]),
            )

            # save_raw_scans is False: scan 1 completed and folded into the
            # average, so its streamed file (not requested to be kept) is
            # gone; scan 2 never completed, so its file -- the crash-safety
            # artifact -- survives.
            self.assertFalse((Path(directory) / "zero_field_test_scan_001.h5").exists())
            self.assertTrue((Path(directory) / "zero_field_test_scan_002.h5").exists())


class StoppingCamera(FakeCamera):
    """Calls .stop() on the experiment placed in holder["experiment"] after
    supplying stop_after frames -- simulates a GUI Stop click landing
    mid-sweep without a real thread. The holder indirection exists because
    the camera must be constructed before the experiment that owns it."""

    def __init__(self, frames, stop_after, holder):
        super().__init__(frames)
        self.stop_after = stop_after
        self.holder = holder
        self.snap_count = 0

    def snap(self):
        self.snap_count += 1
        frame = super().snap()
        if self.snap_count == self.stop_after:
            self.holder["experiment"].stop()
        return frame


class ZeroFieldCooperativeStopTests(unittest.TestCase):
    """A cooperative stop must end the run cleanly with a correctly-shaped
    partial cube, not raise -- and must not corrupt a multi-scan average."""

    def test_mid_sweep_stop_returns_partial_cube_without_raising(self):
        holder = {}
        frames = [np.full((2, 2), value) for value in (1, 2, 3, 4, 5)]
        camera = StoppingCamera(frames, stop_after=3, holder=holder)
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=camera,
            magnet=FakeMagnet(),
            field_start=0.0,
            field_stop=4.0,
            field_points=5,
            field_axis="X",
            settling_time_ms=0,
            averages=1,
        )
        holder["experiment"] = experiment

        cube = experiment.run()

        self.assertEqual(cube.data.shape[0], 3)
        np.testing.assert_allclose(
            cube.data, [np.full((2, 2), v) for v in (1, 2, 3)]
        )
        self.assertEqual(len(cube.scan_axis_values), 3)
        self.assertEqual(len(cube.metadata["field_values_gauss"]), 3)
        self.assertTrue(cube.metadata["stopped_by_user"])
        self.assertFalse(cube.metadata["experiment_complete"])

        # The actual GUI-facing regression this bug produces: analysis must
        # not crash on a cube whose scan-axis length matches its (partial)
        # data length.
        fields, signals = mean_fluorescence_vs_field(cube)
        self.assertEqual(len(fields), 3)
        self.assertEqual(len(signals), 3)

    def test_stop_before_any_frame_completes_without_raising(self):
        holder = {}
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=FakeCamera([np.ones((2, 2))] * 5),
            magnet=FakeMagnet(),
            field_start=0.0,
            field_stop=4.0,
            field_points=5,
            field_axis="X",
            settling_time_ms=0,
            averages=1,
            # Fires after setup_scan() but before the first frame is
            # captured -- the "started but zero frames" case.
            scan_started_callback=lambda *_: holder["experiment"].stop(),
        )
        holder["experiment"] = experiment

        cube = experiment.run()

        self.assertIsNone(cube.data)
        self.assertTrue(cube.metadata["stopped_by_user"])
        self.assertFalse(cube.metadata["experiment_complete"])

    def test_mid_sweep_stop_on_later_scan_does_not_corrupt_completed_average(self):
        # Scan 1 completes both points; scan 2 stops after its first point.
        # Regression test for the merge-into-average bug: without the
        # incomplete_sweep guard in run(), this either assigns a
        # wrong-shaped averaged_cube or crashes with a raw numpy broadcast
        # error inside _update_running_average.
        holder = {}
        frames = [
            np.full((2, 2), 1.0), np.full((2, 2), 2.0),  # scan 1 (complete)
            np.full((2, 2), 9.0),                         # scan 2, point 1 only
        ]
        camera = StoppingCamera(frames, stop_after=3, holder=holder)
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=camera,
            magnet=FakeMagnet(),
            field_start=-1.0,
            field_stop=1.0,
            field_points=2,
            field_axis="X",
            settling_time_ms=0,
            averages=1,
            averaging_enabled=True,
            num_scans=3,
        )
        holder["experiment"] = experiment

        cube = experiment.run()

        self.assertEqual(cube.data.shape[0], 2)
        np.testing.assert_allclose(
            cube.data, [np.full((2, 2), 1.0), np.full((2, 2), 2.0)]
        )
        self.assertEqual(cube.metadata["completed_scans"], 1)
        self.assertTrue(cube.metadata["stopped_by_user"])
        self.assertFalse(cube.metadata["experiment_complete"])


class SingleScanDirectStreamingTests(unittest.TestCase):
    """A single-scan run (via ZeroFieldWorker, as the GUI drives it) streams
    straight into its own output_path -- no _scan_001.h5 companion file, no
    redundant whole-cube rewrite at the end, and the final metadata that
    only becomes known after streaming closes still lands on disk."""

    @staticmethod
    def _config(**overrides):
        config = {
            "field_start": -1.0,
            "field_stop": 1.0,
            "field_points": 3,
            "field_axis": "X",
            "settling_time_ms": 0,
            "averages": 1,
            "averaging_enabled": False,
            "num_scans": 1,
            "save_raw_scans": False,
        }
        config.update(overrides)
        return config

    def test_completed_single_scan_streams_directly_with_no_duplicate_file(self):
        frames = [np.full((2, 2), v) for v in (1.0, 2.0, 3.0)]
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test.h5"
            worker = ZeroFieldWorker(
                None, FakeCamera(frames), FakeMagnet(),
                self._config(), None, {}, output_path,
            )
            worker.start()

            self.assertEqual(list(Path(directory).glob("*.h5")), [output_path])
            cube = ImageCube.load(output_path)
            self.assertEqual(cube.data.shape, (3, 2, 2))
            self.assertTrue(cube.metadata["scan_complete"])
            self.assertEqual(cube.metadata["planes_written"], 3)
            self.assertTrue(cube.metadata["experiment_complete"])
            self.assertFalse(cube.metadata["stopped_by_user"])
            self.assertEqual(cube.metadata["image_height_px"], 2)
            self.assertEqual(cube.metadata["image_width_px"], 2)
            self.assertFalse(cube.metadata["averaging_enabled"])
            self.assertEqual(cube.metadata["num_scans"], 1)
            self.assertEqual(cube.metadata["completed_scans"], 1)

    def test_stopped_single_scan_streams_directly_and_truncates_in_place(self):
        holder = {}
        frames = [np.full((2, 2), v) for v in (1.0, 2.0, 3.0)]
        camera = StoppingCamera(frames, stop_after=2, holder=holder)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test.h5"
            worker = ZeroFieldWorker(
                None, camera, FakeMagnet(),
                self._config(), None, {}, output_path,
            )
            # ZeroFieldWorker.stop() forwards to self.experiment.stop(),
            # and self.experiment is already assigned by the time start()
            # begins acquiring frames -- so the worker itself can stand in
            # for the "holder" indirection StoppingCamera expects.
            holder["experiment"] = worker
            worker.start()

            self.assertEqual(list(Path(directory).glob("*.h5")), [output_path])
            cube = ImageCube.load(output_path)
            self.assertEqual(cube.data.shape, (2, 2, 2))
            self.assertEqual(len(cube.scan_axis_values), 2)
            self.assertFalse(cube.metadata["scan_complete"])
            self.assertEqual(cube.metadata["planes_written"], 2)
            self.assertFalse(cube.metadata["experiment_complete"])
            self.assertTrue(cube.metadata["stopped_by_user"])


if __name__ == "__main__":
    unittest.main()
