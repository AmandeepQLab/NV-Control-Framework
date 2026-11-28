"""Hardware-free tests for the Zero Field "Saved image data" setting.

The same deterministic acquisition is saved as full images, binned images
and ROI means (framework/saved_image_data.py). Only what is written may
differ: per-point means of data / reference / ratio, magnet commands and the
live display are the same in every mode.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from analysis import zero_field_averaging  # noqa: E402
from experiments.zero_field_experiment import ZeroFieldExperiment  # noqa: E402
from framework import saved_image_data  # noqa: E402
from framework.analysis.zero_field import mean_fluorescence_vs_field  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from gui import zero_field_queue as queue_model  # noqa: E402
from gui.widgets.zero_field_widget import ZeroFieldWidget  # noqa: E402
from gui.zero_field_worker import ZeroFieldWorker, ratio_curve  # noqa: E402
from tests.test_zero_field_reference import RecordingMagnet  # noqa: E402

HEIGHT, WIDTH = 64, 96          # divisible by 2, 4 and 8
REPEATS = 3
POINTS = 5
REFERENCE_GAUSS = 8.0
IMAGE_DATASETS = ("data", "reference", "ratio", "forward_average", "reverse_average",
                  "ratio_forward_average", "ratio_reverse_average")

_KEEP_ALIVE = []


class PatternCamera:
    """Deterministic uint16 frames: a fixed spatial pattern x a per-frame
    wobble (so frames of one point differ) x a field-dependent dip."""

    streaming = False

    def __init__(self, magnet, shape=(HEIGHT, WIDTH)):
        self.magnet = magnet
        y, x = np.indices(shape)
        self.pattern = 20000.0 + 3000.0 * np.sin(x / 7.0) * np.cos(y / 5.0) + 7.0 * x
        self.count = 0

    def snap(self):
        gauss = self.magnet.current["x"] * 10.0
        wobble = 1.0 + 0.004 * np.sin(1.3 * self.count)
        dip = 1.0 - 0.003 * np.exp(-(gauss / 0.4) ** 2)
        self.count += 1
        return np.round(self.pattern * wobble * dip).astype(np.uint16)

    def set_roi(self, roi):
        self.roi = roi


def config_for(mode="full", factor=2, reference=True, scans=1, alternate=False,
               raw=False, points=POINTS):
    return {
        "field_start": -1.0, "field_stop": 1.0, "field_points": points,
        "field_axis": "X", "settling_time_ms": 0, "post_flip_settling_ms": 0,
        "averages": REPEATS, "averaging_enabled": scans > 1, "num_scans": scans,
        "save_raw_scans": raw, "alternate_direction": alternate,
        "reference_enabled": reference, "reference_field_gauss": REFERENCE_GAUSS,
        "save_data": True, "saved_image_mode": mode, "bin_factor": factor,
    }


def run_worker(directory, config, name="run"):
    """Run the real worker path (streaming, averaging, saving) synchronously.
    Returns (output path, cube, magnet commands, live updates)."""
    magnet = RecordingMagnet()
    camera = PatternCamera(magnet)
    scans = config["num_scans"] if config["averaging_enabled"] else 1
    suffix = "_average" if scans > 1 else ""
    output = Path(directory) / f"{name}{suffix}.h5"
    # Metadata as the window passes it.
    worker = ZeroFieldWorker(None, camera, magnet, config, None,
                             {"scan_parameters": dict(config)}, output)
    live = []
    worker.live_update_signal.connect(lambda f, s, image: live.append((f, s, image)))
    finished = []
    worker.finished_signal.connect(lambda cube, *_: finished.append(cube))
    errors = []
    worker.error_signal.connect(errors.append)
    worker.start()
    if errors:
        raise AssertionError(errors)
    return output, finished[0], magnet.commands, live


def per_point(array):
    array = np.asarray(array, dtype=np.float64)
    return array.reshape(array.shape[0], -1).mean(axis=1)


class SameAcquisitionAllModesTests(unittest.TestCase):
    """One acquisition, three files: identical per-point values."""

    def _three(self, **config):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        runs = {}
        for mode, factor in (("full", 2), ("binned", 4), ("roi_mean", 2)):
            runs[mode] = run_worker(
                directory.name, config_for(mode=mode, factor=factor, **config), mode)
        return runs

    def _assert_same_per_point(self, runs, names):
        cubes = {mode: ImageCube.load(path) for mode, (path, *_rest) in runs.items()}
        full = cubes["full"]
        for name in names:
            reference = per_point(full.data if name == "data" else full.extra_datasets[name])
            for mode in ("binned", "roi_mean"):
                cube = cubes[mode]
                values = per_point(cube.data if name == "data" else cube.extra_datasets[name])
                np.testing.assert_allclose(values, reference, rtol=1e-12, atol=0,
                                           err_msg=f"{name} in {mode}")
        return cubes

    def test_single_scan_streamed_file(self):
        runs = self._three()
        cubes = self._assert_same_per_point(runs, ("data", "reference", "ratio"))
        self.assertEqual(cubes["full"].data.shape, (POINTS, HEIGHT, WIDTH))
        self.assertEqual(cubes["binned"].data.shape, (POINTS, HEIGHT // 4, WIDTH // 4))
        self.assertEqual(cubes["roi_mean"].data.shape, (POINTS, 1, 1))

    def test_multi_scan_average_with_alternate_direction_and_raw_files(self):
        runs = self._three(scans=2, alternate=True, raw=True)
        self._assert_same_per_point(runs, IMAGE_DATASETS)
        # Raw per-scan files are reduced the same way.
        for mode, (path, *_rest) in runs.items():
            raw = ImageCube.load(path.with_name(f"{mode}_scan_002.h5"))
            self.assertEqual(raw.metadata["saved_image_mode"], mode)
            full_raw = ImageCube.load(runs["full"][0].with_name("full_scan_002.h5"))
            np.testing.assert_allclose(per_point(raw.data), per_point(full_raw.data),
                                       rtol=1e-12)

    def test_without_reference(self):
        runs = self._three(reference=False)
        cubes = self._assert_same_per_point(runs, ("data",))
        self.assertNotIn("reference", cubes["roi_mean"].extra_datasets)
        self.assertNotIn(saved_image_data.REFERENCE_FRAME_MEANS,
                         cubes["roi_mean"].extra_datasets)

    def test_measurement_and_live_display_identical(self):
        runs = self._three(scans=2, alternate=True)
        commands = [run[2] for run in runs.values()]
        self.assertEqual(commands[0], commands[1])
        self.assertEqual(commands[0], commands[2])
        live = {mode: run[3] for mode, run in runs.items()}
        for mode in ("binned", "roi_mean"):
            self.assertEqual(len(live[mode]), len(live["full"]))
            for (f0, s0, image0), (f1, s1, image1) in zip(live["full"], live[mode]):
                self.assertEqual((f0, s0), (f1, s1))
                # The live image is always the full acquired image.
                self.assertEqual(np.shape(image1), (HEIGHT, WIDTH))
                np.testing.assert_array_equal(image0, image1)


class FileContentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def test_roi_mean_file_has_no_images_and_keeps_every_frame_mean(self):
        path, cube, _, _ = run_worker(
            self.directory, config_for(mode="roi_mean", scans=2, alternate=True))
        loaded = ImageCube.load(path)
        for name in ("data",) + tuple(loaded.extra_datasets):
            array = loaded.data if name == "data" else loaded.extra_datasets[name]
            if name in (saved_image_data.FRAME_MEANS,
                        saved_image_data.REFERENCE_FRAME_MEANS):
                self.assertEqual(array.shape, (POINTS, 2, REPEATS), name)
            else:
                self.assertEqual(array.shape, (POINTS, 1, 1), name)
        # Averaged over scans and frames, the frame means are the data.
        np.testing.assert_allclose(
            per_point(loaded.extra_datasets[saved_image_data.FRAME_MEANS]),
            per_point(loaded.data), rtol=1e-12)
        np.testing.assert_allclose(
            per_point(loaded.extra_datasets[saved_image_data.REFERENCE_FRAME_MEANS]),
            per_point(loaded.extra_datasets["reference"]), rtol=1e-12)
        # Frames of one point really differ (repeat noise is kept).
        frames = loaded.extra_datasets[saved_image_data.FRAME_MEANS]
        self.assertGreater(np.ptp(frames[0, 0]), 0)
        m = loaded.metadata
        self.assertEqual(m["saved_image_mode"], "roi_mean")
        self.assertIsNone(m["bin_factor"])
        self.assertEqual(m["image_shape_acquired"], [HEIGHT, WIDTH])
        self.assertEqual(m["image_shape_saved"], [1, 1])
        self.assertEqual(m["roi_mean_region_px"], [0, 0, WIDTH, HEIGHT])
        self.assertIn("roi_mean_acquisition_roi", m)
        self.assertEqual(m["image_height_px"], 1)
        self.assertEqual(m["reference_order"], ["SR", "RS", "SR", "RS", "SR"])
        self.assertIn(saved_image_data.FRAME_MEANS, m["field_value_definitions"])

    def test_single_scan_roi_mean_frame_means_follow_acquisition(self):
        path, _, _, _ = run_worker(self.directory, config_for(mode="roi_mean"))
        loaded = ImageCube.load(path)
        self.assertTrue(loaded.metadata["scan_complete"])
        self.assertEqual(loaded.extra_datasets["frame_means"].shape, (POINTS, 1, REPEATS))

    def test_binned_images_are_block_means_of_the_full_images(self):
        full_path, _, _, _ = run_worker(self.directory, config_for(mode="full"), "f")
        full = ImageCube.load(full_path)
        for factor in saved_image_data.BIN_FACTORS:
            with self.subTest(factor=factor):
                path, _, _, _ = run_worker(
                    self.directory, config_for(mode="binned", factor=factor), f"b{factor}")
                binned = ImageCube.load(path)
                for name in ("data", "reference", "ratio"):
                    source = full.data if name == "data" else full.extra_datasets[name]
                    stored = binned.data if name == "data" else binned.extra_datasets[name]
                    expected = source.reshape(
                        POINTS, HEIGHT // factor, factor, WIDTH // factor, factor
                    ).mean(axis=(2, 4))
                    np.testing.assert_allclose(stored, expected, rtol=1e-12)
                m = binned.metadata
                self.assertEqual(m["saved_image_mode"], "binned")
                self.assertEqual(m["bin_factor"], factor)
                self.assertEqual(m["image_shape_acquired"], [HEIGHT, WIDTH])
                self.assertEqual(m["image_shape_saved"], [HEIGHT // factor, WIDTH // factor])
                self.assertEqual(m["binning_cropped_px"], [0, 0])
                self.assertNotIn("frame_means", binned.extra_datasets)

    def test_full_mode_layout_is_unchanged(self):
        path, _, _, _ = run_worker(self.directory, config_for(mode="full"))
        loaded = ImageCube.load(path)
        self.assertEqual(sorted(loaded.extra_datasets), ["ratio", "reference"])
        self.assertEqual(loaded.data.shape, (POINTS, HEIGHT, WIDTH))
        m = loaded.metadata
        self.assertEqual((m["saved_image_mode"], m["bin_factor"]), ("full", None))
        self.assertEqual(m["image_shape_saved"], [HEIGHT, WIDTH])
        self.assertNotIn("roi_mean_region_px", m)
        self.assertNotIn("saved_image_mode", m["field_value_definitions"])

    def test_roi_mean_file_is_a_tiny_fraction_of_full(self):
        sizes = {}
        for mode in ("full", "binned", "roi_mean"):
            path, _, _, _ = run_worker(
                self.directory, config_for(mode=mode, factor=4, points=21), mode)
            sizes[mode] = path.stat().st_size
        self.assertLess(sizes["binned"], sizes["full"] / 4)
        self.assertLess(sizes["roi_mean"], sizes["full"] / 20)

    def test_save_data_of_an_unsaved_run_keeps_the_mode(self):
        config = config_for(mode="roi_mean")
        config["save_data"] = False
        magnet = RecordingMagnet()
        worker = ZeroFieldWorker(None, PatternCamera(magnet), magnet, config, None, {}, None)
        cubes = []
        worker.finished_signal.connect(lambda cube, *_: cubes.append(cube))
        worker.start()
        target = Path(self.directory) / "saved_later.h5"
        cubes[0].save(target)
        self.assertEqual(ImageCube.load(target).data.shape, (POINTS, 1, 1))

    def test_stopped_sweep_truncates_frame_means(self):
        magnet = RecordingMagnet()
        camera = PatternCamera(magnet)
        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=camera, magnet=magnet,
            field_start=-1.0, field_stop=1.0, field_points=POINTS, field_axis="X",
            averages=REPEATS, reference_enabled=True,
            reference_field_gauss=REFERENCE_GAUSS, saved_image_mode="roi_mean",
            live_update_callback=lambda *_: (
                experiment.stop() if len(experiment.frame_means) == 2 else None),
        )
        cube = experiment.run()
        self.assertEqual(cube.data.shape, (2, 1, 1))
        self.assertEqual(cube.extra_datasets["frame_means"].shape, (2, 1, REPEATS))
        self.assertEqual(cube.extra_datasets["reference_frame_means"].shape,
                         (2, 1, REPEATS))


class ReduceAndEstimateTests(unittest.TestCase):
    def test_bin_image_mean_and_cropping(self):
        image = np.arange(7 * 10, dtype=np.float64).reshape(7, 10)
        binned = saved_image_data.bin_image(image, 2)
        self.assertEqual(binned.shape, (3, 5))
        self.assertAlmostEqual(binned[0, 0], image[:2, :2].mean())
        self.assertEqual(saved_image_data.cropped_pixels(image.shape, 4), (3, 2))
        meta = saved_image_data.plane_metadata("binned", 4, image.shape, None)
        self.assertEqual(meta["binning_cropped_px"], [3, 2])
        self.assertEqual(meta["image_shape_saved"], [1, 2])
        with self.assertRaises(ValueError):
            saved_image_data.bin_image(np.ones((3, 3)), 4)

    def test_full_mode_stores_the_plane_itself(self):
        plane = np.ones((4, 4))
        self.assertIs(saved_image_data.reduce_plane(plane, "full"), plane)

    def test_validate(self):
        self.assertEqual(saved_image_data.validate("full", 4), ("full", None))
        self.assertEqual(saved_image_data.validate("binned", 4), ("binned", 4))
        for mode, factor in (("binned", 3), ("binned", None), ("bogus", 2)):
            with self.assertRaises(ValueError):
                saved_image_data.validate(mode, factor)
        with self.assertRaises(ValueError):
            ZeroFieldExperiment(None, None, None, 0, 1, 2, "X", saved_image_mode="png")

    def test_estimate_matches_the_rig_file(self):
        # 2026-11-27 rig run: 41 points, 20 frames, reference on, 2 scans,
        # alternating, 450 x 516 px: 293 MB on disk.
        config = config_for(reference=True, scans=2, alternate=True, points=41)
        config["averages"] = 20
        estimate = saved_image_data.estimate_run_bytes(config, (450, 516))
        self.assertLess(abs(estimate - 293e6) / 293e6, 0.10)
        config["saved_image_mode"] = "binned"
        config["bin_factor"] = 4
        self.assertLess(saved_image_data.estimate_run_bytes(config, (450, 516)), 0.1 * estimate)
        config["saved_image_mode"] = "roi_mean"
        self.assertLess(saved_image_data.estimate_run_bytes(config, None), 1e6)
        config["saved_image_mode"] = "full"
        self.assertIsNone(saved_image_data.estimate_run_bytes(config, None))
        config["save_data"] = False
        self.assertEqual(saved_image_data.estimate_run_bytes(config, None), 0)

    def test_estimate_counts_raw_scans(self):
        config = config_for(scans=3)
        without = saved_image_data.estimate_run_bytes(config, (100, 100))
        config["save_raw_scans"] = True
        self.assertGreater(saved_image_data.estimate_run_bytes(config, (100, 100)),
                           2 * without)

    def test_format_bytes(self):
        self.assertEqual(saved_image_data.format_bytes(287e6), "≈ 287 MB")
        self.assertEqual(saved_image_data.format_bytes(1.23e9), "≈ 1.2 GB")
        self.assertEqual(saved_image_data.format_bytes(45_000), "≈ 45 kB")
        self.assertEqual(saved_image_data.format_bytes(None), "size unknown")

    def test_acquired_image_shape(self):
        shape = saved_image_data.acquired_image_shape
        self.assertEqual(shape((10, 20, 110, 70), 2), (25, 50))
        sim = type("Sim", (), {"image_shape": (64, 96)})()
        self.assertEqual(shape(None, 2, sim), (32, 48))
        live = type("Andor", (), {"latest_frame": np.zeros((30, 40))})()
        self.assertEqual(shape(None, 1, live), (30, 40))
        self.assertIsNone(shape(None, 1, object()))


class QueueFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "queue.json"

    def test_old_queue_file_loads_as_full_images(self):
        config = config_for()
        del config["saved_image_mode"], config["bin_factor"]
        self.path.write_text(json.dumps({
            "format": queue_model.QUEUE_FILE_FORMAT, "version": 1,
            "entries": [{"config": config, "output_directory": "x"}],
        }), encoding="utf-8")
        entries, _, _ = queue_model.load_queue_file(self.path)
        self.assertEqual(entries[0].config["saved_image_mode"], "full")
        self.assertEqual(entries[0].config["bin_factor"], 2)
        self.assertNotIn("images", entries[0].summary())

    def test_new_fields_round_trip(self):
        entries = [
            queue_model.QueueEntry.snapshot(config_for(mode="binned", factor=4), "a"),
            queue_model.QueueEntry.snapshot(config_for(mode="roi_mean"), "b"),
        ]
        queue_model.save_queue_file(self.path, entries, 0, queue_model.ON_FAILURE_STOP)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["entries"][0]["config"]["saved_image_mode"], "binned")
        loaded, _, _ = queue_model.load_queue_file(self.path)
        self.assertEqual([e.config for e in loaded], [e.config for e in entries])
        self.assertIn("images binned 4×4", loaded[0].summary())
        self.assertIn("ROI mean only", loaded[1].summary())

    def test_invalid_mode_or_factor_refused(self):
        for bad in ({"saved_image_mode": "png"}, {"bin_factor": 3},
                    {"bin_factor": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    queue_model.validate_config({**config_for(), **bad})


class ReaderTests(unittest.TestCase):
    """Every code path that loads Zero Field files copes with all modes."""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.paths = {}
        for mode in ("full", "binned", "roi_mean"):
            path, *_ = run_worker(cls._directory.name, config_for(mode=mode, factor=2), mode)
            cls.paths[mode] = path

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_mean_fluorescence_and_ratio_curves(self):
        curves = {}
        for mode, path in self.paths.items():
            cube = ImageCube.load(path)
            fields, signals = mean_fluorescence_vs_field(cube)
            _, ratios = ratio_curve(cube)
            curves[mode] = (signals, ratios)
            self.assertEqual(len(fields), POINTS)
        for mode in ("binned", "roi_mean"):
            np.testing.assert_allclose(curves[mode][0], curves["full"][0], rtol=1e-12)
            np.testing.assert_allclose(curves[mode][1], curves["full"][1], rtol=1e-12)

    def test_analysis_averaging_refuses_roi_mean_with_a_clear_message(self):
        out = Path(self._directory.name) / "avg.npz"
        report = Path(self._directory.name) / "avg.txt"
        with self.assertRaises(ValueError) as caught:
            zero_field_averaging.average_scans([self.paths["roi_mean"]], out, report)
        self.assertIn(saved_image_data.NO_IMAGES_MESSAGE, str(caught.exception))
        with self.assertRaises(ValueError):
            zero_field_averaging.average_plane_range(
                [self.paths["roi_mean"]], Path(self._directory.name) / "planes", 0, 1)

    def test_analysis_averaging_accepts_binned_files(self):
        out = Path(self._directory.name) / "binned_avg.npz"
        report = Path(self._directory.name) / "binned_avg.txt"
        zero_field_averaging.average_scans([self.paths["binned"]], out, report)
        averaged = ImageCube.load(out)
        self.assertEqual(averaged.data.shape, (POINTS, HEIGHT // 2, WIDTH // 2))
        self.assertIn(f"(0, 0, {WIDTH // 2}, {HEIGHT // 2})", report.read_text(encoding="utf-8"))

    def test_require_images(self):
        roi = ImageCube.load(self.paths["roi_mean"])
        with self.assertRaises(ValueError) as caught:
            saved_image_data.require_images(roi.metadata, "x.h5")
        self.assertIn("No images saved in this file (ROI mean mode)", str(caught.exception))
        saved_image_data.require_images(ImageCube.load(self.paths["binned"]).metadata)
        saved_image_data.require_images({})  # older files: full images


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.widget = ZeroFieldWidget()
        _KEEP_ALIVE.append(self.widget)
        self.widget.set_image_shape_provider(lambda: (450, 516))

    def test_defaults_and_bin_factor_enabled_only_when_binned(self):
        w = self.widget
        self.assertFalse(w.saved_image_section.is_expanded())
        config = w.get_config()
        self.assertEqual((config["saved_image_mode"], config["bin_factor"]), ("full", 2))
        self.assertFalse(w.bin_factor_combo.isEnabled())
        w.saved_image_mode_combo.setCurrentIndex(w.saved_image_mode_combo.findData("binned"))
        self.assertTrue(w.bin_factor_combo.isEnabled())
        self.assertTrue(w.saved_image_section.summary().startswith("Binned 2 × 2, ≈ "))

    def test_summary_shows_the_choice_and_size(self):
        w = self.widget
        self.assertRegex(w.saved_image_section.summary(),
                         r"^Full images, ≈ [\d.]+ [kMG]B per run$")
        w.saved_image_mode_combo.setCurrentIndex(w.saved_image_mode_combo.findData("roi_mean"))
        self.assertRegex(w.saved_image_section.summary(), r"^ROI mean only, ≈ [\d.]+ kB per run$")
        w.save_data_check.setChecked(False)
        self.assertEqual(w.saved_image_section.summary(), "ROI mean only (saving off)")

    def test_estimate_follows_points_and_reference(self):
        w = self.widget
        before = w.estimated_run_bytes()
        w.field_points_spin.setValue(w.field_points_spin.value() * 2)
        w.reference_enabled_check.setChecked(True)
        self.assertGreater(w.estimated_run_bytes(), 3 * before)

    def test_crop_warning(self):
        w = self.widget
        w.saved_image_mode_combo.setCurrentIndex(w.saved_image_mode_combo.findData("binned"))
        w.bin_factor_combo.setCurrentIndex(w.bin_factor_combo.findData(4))
        self.assertIn("Not divisible by 4", w.saved_image_estimate_label.text())

    def test_set_config_round_trip_and_older_configs(self):
        w = self.widget
        config = config_for(mode="binned", factor=8)
        w.set_config(config)
        self.assertEqual(w.get_config()["saved_image_mode"], "binned")
        self.assertEqual(w.get_config()["bin_factor"], 8)
        old = dict(config)
        del old["saved_image_mode"], old["bin_factor"]
        w.set_config(old)
        self.assertEqual((w.get_config()["saved_image_mode"], w.get_config()["bin_factor"]),
                         ("full", 2))

    def test_folding_never_changes_the_config(self):
        w = self.widget
        w.saved_image_mode_combo.setCurrentIndex(w.saved_image_mode_combo.findData("roi_mean"))
        before = w.get_config()
        for expanded in (True, False):
            w.saved_image_section.set_expanded(expanded)
            self.assertEqual(w.get_config(), before)

    def test_new_controls_have_tooltips(self):
        w = self.widget
        for control in (w.saved_image_mode_combo, w.bin_factor_combo,
                        w.saved_image_estimate_label, w.saved_image_section.toggle_button):
            self.assertTrue(control.toolTip().strip(), control)
        tip = w.saved_image_mode_combo.toolTip()
        for phrase in ("wide-field imaging", "coarse maps", "field sweeps"):
            self.assertIn(phrase, tip)


class WindowTests(unittest.TestCase):
    def test_window_estimate_uses_the_main_window_roi_and_curve_from_roi_file(self):
        from gui.zero_field_window import ZeroFieldWindow

        state = type("State", (), {"acquisition_roi": (0, 0, 200, 100), "binning": 2,
                                   "exposure_s": 0.01, "baseline_counts": 0.0})()
        magnet = RecordingMagnet()
        window = ZeroFieldWindow(None, {"magnet": magnet}, PatternCamera(magnet),
                                 acquisition_state_getter=lambda: state)
        _KEEP_ALIVE.append(window)
        self.assertEqual(window.estimated_image_shape(), (50, 100))
        self.assertIn("50 × 100 px", window.widget.saved_image_estimate_label.text())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path, *_ = run_worker(directory.name, config_for(mode="roi_mean"))
        window._reference_enabled = True
        fields, signals = window._curve_from_cube(ImageCube.load(path))
        self.assertEqual(len(signals), POINTS)
        self.assertTrue(np.all(np.isfinite(signals)))
        with patch.object(QMessageBox, "critical"):
            window.widget.saved_image_mode_combo.setCurrentIndex(
                window.widget.saved_image_mode_combo.findData("roi_mean"))
        self.assertEqual(window.widget.get_config()["saved_image_mode"], "roi_mean")


if __name__ == "__main__":
    unittest.main()
