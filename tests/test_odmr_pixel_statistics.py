"""Hardware-free tests for ODMR per-average pixel statistics."""

import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import numpy as np
import h5py
from PyQt6.QtCore import QCoreApplication, QTimer

from data.data_manager import DataManager
from experiments.odmr_experiment import (
    InvalidPixelNeighborhoodError,
    ODMRExperiment,
    normalize_odmr_pair_per_pixel,
)
from framework.image_cube import ImageCube
from gui.odmr_worker import ODMRWorker
from gui.odmr_window import ODMRWindow
from hardware.camera.sim_camera import SimCamera
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


class PixelNormalizationTests(unittest.TestCase):
    def test_repeats_are_normalized_pixelwise_then_sample_mean_and_sd(self):
        exp = ODMRExperiment({"pulse_streamer": object()}, {"repeats": 3, "baseline_counts": 2.0})
        off_frames = [
            np.array([[12., 22.], [32., 42.]]),
            np.array([[22., 32.], [42., 52.]]),
            np.array([[32., 42.], [52., 62.]]),
        ]
        ratios = [1.0, 1.2, 1.5]
        frames = iter([(off, off * ratio) for off, ratio in zip(off_frames, ratios)])
        current = {"pair": None}
        exp.set_scan_point = MethodType(lambda self, _f: None, exp)
        exp.build_sequence = MethodType(lambda self, mw_on=False: mw_on, exp)

        def grab(self, _pulse, sequence, **_kwargs):
            if not sequence:
                current["pair"] = frames.__next__()
            off, on = current["pair"]
            return on if sequence else off

        exp.acquire_triggered_frame = MethodType(grab, exp)
        scalar, mean, sd, n, invalid, repaired = exp.acquire_pixel_statistics(2.87e9)

        samples = np.stack([
            100.0 * (off * ratio - 2.0) / (off - 2.0)
            for off, ratio in zip(off_frames, ratios)
        ])
        np.testing.assert_allclose(mean, samples.mean(axis=0))
        np.testing.assert_allclose(sd, samples.std(axis=0, ddof=1))
        self.assertEqual(n, 3)
        self.assertEqual((invalid, repaired), (0, 0))
        self.assertTrue(np.isfinite(scalar))
        self.assertFalse(hasattr(exp, "raw_repeat_frames"))

    def test_single_repeat_has_zero_sample_sd(self):
        exp = ODMRExperiment({"pulse_streamer": object()}, {"repeats": 1})
        exp.set_scan_point = MethodType(lambda self, _f: None, exp)
        exp.build_sequence = MethodType(lambda self, mw_on=False: mw_on, exp)
        exp.acquire_triggered_frame = MethodType(
            lambda self, _pulse, sequence, **_kw: np.full((2, 3), 12.0 if sequence else 10.0),
            exp,
        )
        _, mean, sd, n, invalid, repaired = exp.acquire_pixel_statistics(1.0)
        self.assertEqual(n, 1)
        self.assertEqual((invalid, repaired), (0, 0))
        np.testing.assert_allclose(mean, 120.0)
        np.testing.assert_allclose(sd, 0.0)

    def test_single_interior_invalid_uses_valid_three_by_three_neighbors(self):
        off = np.ones((3, 3))
        on = np.arange(1.0, 10.0).reshape(3, 3)
        off[1, 1] = 0.0
        normalized, invalid, repaired = normalize_odmr_pair_per_pixel(off, on)
        expected_valid = 100.0 * on
        self.assertEqual(normalized[1, 1], np.mean(np.delete(expected_valid.ravel(), 4)))
        np.testing.assert_array_equal(normalized[off > 0], expected_valid[off > 0])
        self.assertEqual((invalid, repaired), (1, 1))

    def test_invalid_neighbors_are_excluded_and_adjacent_invalids_do_not_feed_each_other(self):
        off = np.ones((3, 4))
        on = np.arange(1.0, 13.0).reshape(3, 4)
        off[1, 1:3] = 0.0
        normalized, invalid, repaired = normalize_odmr_pair_per_pixel(off, on)
        source = 100.0 * on
        valid = off > 0
        for x in (1, 2):
            ys = slice(0, 3)
            xs = slice(max(0, x - 1), min(4, x + 2))
            local_valid = valid[ys, xs].copy()
            local_valid[1, x - xs.start] = False
            self.assertEqual(normalized[1, x], np.mean(source[ys, xs][local_valid]))
        np.testing.assert_array_equal(normalized[valid], source[valid])
        self.assertEqual((invalid, repaired), (2, 2))

    def test_corner_invalid_uses_only_available_valid_neighbors(self):
        off = np.ones((2, 3))
        on = np.arange(1.0, 7.0).reshape(2, 3)
        off[0, 0] = -1.0
        normalized, invalid, repaired = normalize_odmr_pair_per_pixel(off, on)
        self.assertEqual(normalized[0, 0], np.mean([200.0, 400.0, 500.0]))
        np.testing.assert_array_equal(normalized[off > 0], (100.0 * on)[off > 0])
        self.assertEqual((invalid, repaired), (1, 1))

    def test_invalid_pixel_is_repaired_independently_in_each_repeat_before_statistics(self):
        exp = ODMRExperiment({"pulse_streamer": object()}, {"repeats": 2})
        exp.set_scan_point = MethodType(lambda self, _f: None, exp)
        exp.build_sequence = MethodType(lambda self, mw_on=False: mw_on, exp)
        pairs = [
            (np.array([[1., 1., 1.], [1., 0., 1.], [1., 1., 1.]]), np.ones((3, 3))),
            (np.ones((3, 3)), np.full((3, 3), 2.0)),
        ]
        current = {"pair": None}

        def grab(self, _pulse, sequence, **_kwargs):
            if not sequence:
                current["pair"] = pairs.pop(0)
            off, on = current["pair"]
            return on if sequence else off

        exp.acquire_triggered_frame = MethodType(grab, exp)
        _, mean, sd, n, invalid, repaired = exp.acquire_pixel_statistics(1.0)
        self.assertEqual(n, 2)
        self.assertEqual((invalid, repaired), (1, 1))
        self.assertEqual(mean[1, 1], 150.0)
        self.assertAlmostEqual(sd[1, 1], np.std([100.0, 200.0], ddof=1))

    def test_invalid_pixel_without_valid_neighbor_fails_explicitly(self):
        with self.assertRaises(InvalidPixelNeighborhoodError) as ctx:
            normalize_odmr_pair_per_pixel(np.array([[0.0]]), np.array([[100.0]]))
        self.assertIn("no valid neighbor", str(ctx.exception))
        self.assertEqual(ctx.exception.invalid_count, 1)


def _qapp():
    app = QCoreApplication.instance()
    return app if app is not None else QCoreApplication([])


class WorkerStatisticsTests(unittest.TestCase):
    def _run_worker(self, averages, repeats, stop_after_sweeps=None, stop_after_points=None):
        camera = SimCamera(image_shape=(3, 4))
        mw = SimMicrowave()
        pulse = SimPulseStreamer()
        hardware = {
            "camera": camera, "microwave": mw, "pulse_streamer": pulse,
            "channels": {"greenLaser": 1, "MW": 2, "detector": 5},
        }
        config = {
            "f_start": 2.8e9, "f_stop": 2.9e9, "steps": 2,
            "averages": averages, "repeats": repeats, "save_data": False,
        }
        worker = ODMRWorker(hardware, config)
        worker.order = np.array([1, 0])
        worker.freqs = worker.freqs_sorted[worker.order]
        # Keep the worker's timer/averaging/orientation path, but replace all
        # hardware-dependent experiment actions with deterministic arrays.
        worker.experiment.configure_acquisition = lambda: None
        worker.experiment.reset_baseline_warning_state = lambda: None
        worker.experiment.reset_acquisition_counts = lambda: None
        worker.experiment.begin_camera_acquisition = lambda: None
        worker.experiment.end_camera_acquisition = lambda: None
        call_count = {"n": 0}

        def acquire(_frequency):
            call_count["n"] += 1
            image = np.full((3, 4), call_count["n"], dtype=np.float64)
            worker.experiment._last_i_off = 10.0
            worker.experiment._last_i_on = 11.0
            return 110.0, image, np.full_like(image, repeats), repeats, 1, 1

        worker.experiment.acquire_pixel_statistics = acquire
        app = _qapp()
        done = {"value": False}
        if stop_after_sweeps is not None or stop_after_points is not None:
            points = {"count": 0}

            def stop_at_boundary(_x, _y):
                points["count"] += 1
                threshold = (
                    stop_after_points if stop_after_points is not None
                    else stop_after_sweeps * len(worker.freqs)
                )
                if points["count"] == threshold:
                    worker.stop()

            worker.point_signal.connect(stop_at_boundary)
        worker.finished_signal.connect(lambda: (done.__setitem__("value", True), app.quit()))
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(app.quit)
        watchdog.start(5000)
        QTimer.singleShot(0, worker.start)
        app.exec()
        self.assertTrue(done["value"])
        return worker

    def test_configured_averages_and_repeats_define_shape_and_metadata(self):
        worker = self._run_worker(averages=3, repeats=2)
        cube = worker.statistics_cube
        self.assertEqual(cube.data.shape, (3, 2, 2, 3, 4))
        self.assertEqual(cube.metadata["configured_averages"], 3)
        self.assertEqual(cube.metadata["completed_averages"], 3)
        self.assertEqual(cube.metadata["n_repeats_per_average"], [2, 2, 2])
        self.assertEqual(cube.metadata["invalid_pixels_encountered"], 6)
        self.assertEqual(cube.metadata["repaired_pixels"], 6)
        self.assertEqual(
            cube.metadata["invalid_pixel_counts_per_average_frequency"],
            [[1, 1], [1, 1], [1, 1]],
        )
        self.assertEqual(cube.axes["statistic"], ["normalized_mean", "normalized_sd"])
        np.testing.assert_array_equal(cube.scan_axis_values, [2.8e9, 2.9e9])
        self.assertEqual(cube.metadata["frequency_acquisition_order_hz"], [2.9e9, 2.8e9])
        self.assertEqual(cube.axes["axis_order"], ["average", "statistic", "frequency", "y", "x"])
        self.assertEqual(cube.metadata["image_height_px"], 3)
        self.assertEqual(cube.metadata["image_width_px"], 4)
        self.assertEqual(cube.metadata["camera_binning"], 1)
        self.assertEqual(cube.experiment_type, "ODMR")
        self.assertTrue(cube.metadata["experiment_complete"])
        self.assertFalse(np.array_equal(cube.data[0], cube.data[1]))

    def test_stop_records_only_completed_averages_and_marks_incomplete(self):
        worker = self._run_worker(averages=3, repeats=5, stop_after_sweeps=1)
        cube = worker.statistics_cube
        self.assertEqual(cube.data.shape, (1, 2, 2, 3, 4))
        self.assertEqual(cube.metadata["configured_averages"], 3)
        self.assertEqual(cube.metadata["completed_averages"], 1)
        self.assertEqual(cube.metadata["n_repeats_per_average"], [5])
        self.assertFalse(cube.metadata["experiment_complete"])
        self.assertTrue(cube.metadata["stopped_by_user"])
        self.assertTrue(cube.metadata["interrupted"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stopped.h5"
            cube.save(path)
            loaded = ImageCube.load(path)
        self.assertEqual(loaded.data.shape, (1, 2, 2, 3, 4))
        self.assertFalse(loaded.metadata["experiment_complete"])
        self.assertTrue(loaded.metadata["stopped_by_user"])

    def test_stop_before_first_completed_sweep_persists_zero_completed_count(self):
        worker = self._run_worker(averages=2, repeats=3, stop_after_points=1)
        cube = worker.statistics_cube
        self.assertEqual(cube.data.shape, (0, 2, 2, 3, 4))
        self.assertEqual(cube.metadata["completed_averages"], 0)
        self.assertFalse(cube.metadata["experiment_complete"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "partial.h5"
            cube.save(path)
            loaded = ImageCube.load(path)
        self.assertEqual(loaded.data.shape, (0, 2, 2, 3, 4))
        self.assertEqual(loaded.metadata["completed_averages"], 0)
        self.assertFalse(loaded.metadata["experiment_complete"])
        self.assertTrue(loaded.metadata["stopped_by_user"])

    def test_hdf5_round_trip_and_existing_imagecube_loader(self):
        worker = self._run_worker(averages=2, repeats=4)
        cube = worker.statistics_cube
        with tempfile.TemporaryDirectory() as temp:
            folder = DataManager(temp).save_odmr(
                np.array([2.8e9, 2.9e9]), np.array([100., 100.]),
                {"averages": 2, "repeats": 4},
            )
            cube.save(Path(folder) / "odmr_statistics.h5")
            loaded = ImageCube.load(Path(folder) / "odmr_statistics.h5")
        self.assertEqual(loaded.data.shape, (2, 2, 2, 3, 4))
        self.assertEqual(loaded.axes["axis_order"], ["average", "statistic", "frequency", "y", "x"])
        np.testing.assert_array_equal(loaded.data, cube.data)

    def test_actual_synthetic_odmr_hdf5_structure_values_and_round_trip(self):
        """Exercise the worker result and exact persistence path without hardware."""
        averages, repeats, height, width = 3, 4, 8, 6
        camera = SimCamera(image_shape=(height, width))
        hardware = {
            "camera": camera,
            "microwave": SimMicrowave(),
            "pulse_streamer": SimPulseStreamer(),
            "channels": {"greenLaser": 1, "MW": 2, "detector": 5},
        }
        config = {
            "f_start": 2.80e9, "f_stop": 2.84e9, "steps": 5,
            "averages": averages, "repeats": repeats,
            "baseline_counts": 10.0, "save_data": True,
            "mw_power_dbm": -8, "trigger_delay_s": 0.01,
        }
        worker = ODMRWorker(hardware, config, acquisition_roi=None)
        worker.order = np.array([4, 2, 0, 3, 1])
        worker.freqs = worker.freqs_sorted[worker.order]
        current_frequency = {"value": None}

        def set_frequency(_self, frequency):
            current_frequency["value"] = frequency

        def synthetic_pair(_self, _pulse, repeat_index):
            freq_index = int(np.argmin(np.abs(
                worker.freqs_sorted - current_frequency["value"]
            )))
            avg_index = worker.average_index
            y, x = np.indices((height, width))
            off = 100.0 + 2.0 * y + x
            ratio = 1.0 + 0.01 * avg_index + 0.005 * freq_index + 0.002 * repeat_index
            on = 10.0 + ratio * (off - 10.0)
            # Exactly one invalid pixel per average/frequency, in repeat 0.
            if repeat_index == 0:
                off[3, 2] = 10.0
                on[3, 2] = 10.0
            return off, on

        worker.experiment.set_scan_point = MethodType(set_frequency, worker.experiment)
        worker.experiment.acquire_repeat_pair = MethodType(synthetic_pair, worker.experiment)

        app = _qapp()
        finished = {"value": False}
        worker.finished_signal.connect(
            lambda: (finished.__setitem__("value", True), app.quit())
        )
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(app.quit)
        watchdog.start(10000)
        QTimer.singleShot(0, worker.start)
        app.exec()
        self.assertTrue(finished["value"])
        cube = worker.statistics_cube
        self.assertEqual(cube.data.shape, (3, 2, 5, 8, 6))

        with tempfile.TemporaryDirectory() as temp:
            # Invoke the real GUI completion/save method with lightweight UI
            # doubles so the validation follows the production save route.
            fake = SimpleNamespace(
                odmr_running=True,
                panel=SimpleNamespace(
                    set_running_state=lambda *_: None,
                    fit_button=SimpleNamespace(setEnabled=lambda *_: None),
                    update_time_label=lambda **_: None,
                ),
                refresh_plot=lambda: None,
                scan_start_time=None,
                current_config=dict(config),
                estimate_odmr_time=lambda _config: 1.0,
                latest_x=(worker.freqs_sorted / 1e9).tolist(),
                latest_y=worker.results.tolist(),
                odmr_worker=worker,
                data_manager=DataManager(temp),
                run_finished=SimpleNamespace(emit=lambda: None),
            )
            ODMRWindow.odmr_finished(fake)
            folders = list(Path(temp).glob("odmr_*"))
            self.assertEqual(len(folders), 1)
            folder = folders[0]
            output = Path(folder) / "odmr_statistics.h5"
            self.assertTrue(output.exists())

            with h5py.File(output, "r") as handle:
                self.assertEqual(set(handle.keys()), {
                    "data", "scan_axis_values", "metadata_json", "axes_json"
                })
                data = handle["data"]
                self.assertEqual(data.shape, (3, 2, 5, 8, 6))
                self.assertEqual(data.dtype, np.dtype("float32"))
                data_values = data[()]
                self.assertEqual(handle["scan_axis_values"].shape, (5,))
                self.assertEqual(handle["scan_axis_values"].dtype, np.dtype("float64"))
                metadata = json.loads(handle["metadata_json"][()].decode("utf-8"))
                axes = json.loads(handle["axes_json"][()].decode("utf-8"))
                scan_values = handle["scan_axis_values"][()]
                attrs = dict(handle.attrs)

            self.assertEqual(set(attrs), {
                "experiment_type", "experiment_type_is_none",
                "scan_axis_name", "scan_axis_name_is_none",
                "scan_axis_unit", "scan_axis_unit_is_none",
                "scan_axis_values_is_none",
            })
            self.assertEqual(attrs["experiment_type"], "ODMR")
            self.assertFalse(attrs["experiment_type_is_none"])
            self.assertEqual(attrs["scan_axis_name"], "frequency")
            self.assertEqual(attrs["scan_axis_unit"], "Hz")
            self.assertFalse(attrs["scan_axis_values_is_none"])
            expected_frequencies = np.linspace(2.80e9, 2.84e9, 5)
            np.testing.assert_array_equal(scan_values, expected_frequencies)
            np.testing.assert_array_equal(axes["frequency"], expected_frequencies)
            self.assertEqual(axes["axis_order"], ["average", "statistic", "frequency", "y", "x"])
            self.assertEqual(axes["statistic"], ["normalized_mean", "normalized_sd"])
            self.assertEqual(metadata["configured_averages"], 3)
            self.assertEqual(metadata["completed_averages"], 3)
            self.assertEqual(metadata["configured_repeats"], 4)
            self.assertEqual(metadata["n_repeats_per_average"], [4, 4, 4])
            self.assertEqual(
                metadata["normalization_method"],
                "100 * (ON - baseline_counts) / (OFF - baseline_counts), per pixel per repeat",
            )
            self.assertTrue(metadata["experiment_complete"])
            self.assertFalse(metadata["stopped_by_user"])
            self.assertFalse(metadata["interrupted"])
            self.assertEqual(metadata["image_height_px"], 8)
            self.assertEqual(metadata["image_width_px"], 6)
            self.assertEqual(metadata["image_dtype"], "float32")
            self.assertEqual(metadata["camera_model"], "SimCamera")
            self.assertEqual(metadata["camera_binning"], 1)
            self.assertEqual(metadata["camera_exposure_actual_s"], camera.exposure_time)
            self.assertEqual(metadata["frequency_acquisition_order_hz"], worker.freqs.tolist())
            self.assertEqual(metadata["frequency_acquisition_order_indices"], [4, 2, 0, 3, 1])
            self.assertEqual(metadata["invalid_pixels_encountered"], 15)
            self.assertEqual(metadata["repaired_pixels"], 15)
            self.assertEqual(
                metadata["invalid_pixel_counts_per_average_frequency"],
                [[1] * 5 for _ in range(averages)],
            )
            self.assertEqual(
                metadata["repaired_pixel_counts_per_average_frequency"],
                [[1] * 5 for _ in range(averages)],
            )
            self.assertNotIn("scan_complete", metadata)
            self.assertTrue(np.isfinite(data_values).all())

            # Independently calculate one frequency/pixel's average and
            # sample SD. The invalid repeat's replacement is the same
            # normalized local signal as its valid neighbors by construction.
            avg_index, freq_index, y, x = 2, 3, 3, 2
            samples = np.array([
                100.0 * (1.0 + 0.01 * avg_index + 0.005 * freq_index + 0.002 * r)
                for r in range(repeats)
            ])
            np.testing.assert_allclose(
                data_values[avg_index, 0, freq_index, y, x],
                samples.mean(), rtol=1e-7, atol=1e-6,
            )
            np.testing.assert_allclose(
                data_values[avg_index, 1, freq_index, y, x],
                samples.std(ddof=1), rtol=1e-7, atol=1e-6,
            )
            self.assertNotEqual(data_values[0, 0, 0, 0, 0], data_values[1, 0, 0, 0, 0])
            self.assertNotEqual(data_values[0, 0, 0, 0, 0], data_values[0, 0, 1, 0, 0])
            self.assertEqual(data_values.shape[1], 2)  # no repeat or OFF/ON axis

            loaded = ImageCube.load(output)
            self.assertEqual(loaded.data.shape, cube.data.shape)
            np.testing.assert_array_equal(loaded.data, cube.data)
            self.assertEqual(loaded.axes, cube.axes)
            self.assertEqual(loaded.metadata, cube.metadata)
            np.testing.assert_array_equal(loaded.scan_axis_values, cube.scan_axis_values)


if __name__ == "__main__":
    unittest.main()
