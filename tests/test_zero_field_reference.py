"""Hardware-free tests for in-point reference field normalization.

With reference_enabled, every Zero Field point is also measured at a fixed
reference field on the swept axis, in alternating S,R / R,S order, and the
per-pixel ratio signal/reference is stored next to the raw signal, so slow
multiplicative drift common to both halves divides out.
"""

import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

# QApplication must exist before pyqtgraph (via the widget) is imported;
# see tests/test_zero_field_window_save_toggle.py.
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.zero_field_experiment import ZeroFieldExperiment  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from gui.widgets.zero_field_widget import ZeroFieldWidget  # noqa: E402
from gui.zero_field_window import ZeroFieldWindow  # noqa: E402
from gui.zero_field_worker import ZeroFieldWorker, mean_ratio  # noqa: E402
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402

# Widgets are created once and never deleted mid-process (pyqtgraph
# teardown is fragile here; see tests/test_colormap_selectors.py).
_KEEP_ALIVE = []

FIELD_START, FIELD_STOP = -1.0, 1.0
REFERENCE_GAUSS = 8.0


class RecordingMagnet:
    """Magnet double recording every set_vector() as an (x, y, z) mT tuple.

    get_vector() reports the last command, so model fields equal requests.
    X polarity follows the sign of the commanded X field and a sign change
    is reported as a flip -- enough to exercise flip bookkeeping.
    """

    def __init__(self, entry_vector_mT=None):
        self.current = dict(entry_vector_mT or {"x": 0.0, "y": 0.0, "z": 0.0})
        self.commands = []
        self.x_polarity = 1

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
        self.commands.append((bx, by, bz))
        flipped = set()
        if bx != 0:
            polarity = 1 if bx > 0 else -1
            if polarity != self.x_polarity:
                self.x_polarity = polarity
                flipped.add("X")
        return frozenset(flipped)

    def swept_gauss(self):
        """Every X command in G, including setup_scan()'s zeroing."""
        return [round(command[0] * 10.0, 9) for command in self.commands]


class MultiplicativeDriftCamera:
    """Frame = baseline x slow drift(time) x (1 - dip(field)).

    Time is the global frame counter. The drift is a slow, smooth,
    multiplicative change (~1% over a sweep, like the rig's environmental
    drift); the dip sits at zero field and is far from the reference.
    """

    streaming = False

    def __init__(self, magnet, total_frames, drift_fraction=0.01,
                 dip_fraction=0.003, dip_width_gauss=0.3, zero_reference=False):
        self.magnet = magnet
        self.total_frames = total_frames
        self.drift_fraction = drift_fraction
        self.dip_fraction = dip_fraction
        self.dip_width_gauss = dip_width_gauss
        self.zero_reference = zero_reference
        self.frame_count = 0

    def drift(self, frame):
        phase = frame / max(self.total_frames - 1, 1)
        # Monotonic and slightly curved, not exactly linear.
        return 1.0 + self.drift_fraction * (0.7 * phase + 0.3 * phase ** 2)

    def dip(self, gauss):
        return self.dip_fraction * np.exp(-(np.asarray(gauss) / self.dip_width_gauss) ** 2)

    def snap(self):
        gauss = self.magnet.current["x"] * 10.0
        value = 20000.0 * self.drift(self.frame_count) * (1.0 - self.dip(gauss))
        self.frame_count += 1
        frame = np.full((2, 3), value)
        if self.zero_reference and np.isclose(gauss, REFERENCE_GAUSS):
            frame[0, 0] = 0.0  # one dead reference pixel
        return frame

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
        reference_enabled=True,
        reference_field_gauss=REFERENCE_GAUSS,
    )
    kwargs.update(overrides)
    return ZeroFieldExperiment(**kwargs)


def run(points=5, magnet=None, camera=None, **overrides):
    magnet = magnet or RecordingMagnet()
    scans = overrides.get("num_scans", 1) if overrides.get("averaging_enabled") else 1
    camera = camera or MultiplicativeDriftCamera(magnet, total_frames=2 * points * scans)
    experiment = make_experiment(magnet, camera, points=points, **overrides)
    return experiment.run(), experiment, magnet


class RecordingTime:
    """Stands in for the `time` module inside zero_field_experiment only,
    recording sleeps without sleeping (as in test_zero_field_relay_flips)."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def __getattr__(self, name):
        return getattr(time, name)


class CommandOrderTests(unittest.TestCase):
    def test_halves_alternate_sr_rs_and_reference_vector_is_correct(self):
        points = 5
        _, experiment, magnet = run(points=points)
        fields = np.linspace(FIELD_START, FIELD_STOP, points).round(9).tolist()
        r = REFERENCE_GAUSS
        expected = [0.0]  # setup_scan()'s zeroing
        for i, field in enumerate(fields):
            expected += [field, r] if i % 2 == 0 else [r, field]
        self.assertEqual(magnet.swept_gauss(), expected)
        # Non-swept axes stay at the calibrated zero for every command.
        self.assertTrue(all(c[1] == 0.0 and c[2] == 0.0 for c in magnet.commands))
        self.assertEqual(
            experiment.image_cube.metadata["reference_order"],
            ["SR", "RS", "SR", "RS", "SR"],
        )

    def test_reference_keeps_retained_non_swept_axes(self):
        # zero_other_axes=False keeps the entry vector's Y/Z; the reference
        # vector changes only the swept axis.
        magnet = RecordingMagnet({"x": 0.0, "y": 0.02, "z": -0.01})
        run(points=2, magnet=magnet, zero_other_axes=False)
        reference = [c for c in magnet.commands if np.isclose(c[0] * 10, REFERENCE_GAUSS)]
        self.assertEqual(len(reference), 2)
        for command in magnet.commands:
            self.assertEqual(command[1:], (0.02, -0.01))

    def test_repeated_reference_command_gets_no_settling_sleep(self):
        # S0 R | R S1 | S2 R: the R,R between points 0 and 1 commands an
        # identical vector, so only five of the six halves settle.
        magnet = RecordingMagnet()
        camera = MultiplicativeDriftCamera(magnet, total_frames=6)
        experiment = make_experiment(magnet, camera, points=3, settling_time_ms=100)
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            experiment.run()
        self.assertEqual(clock.sleeps, [0.1] * 5)
        self.assertEqual(len(magnet.commands), 1 + 6)


class DriftRemovalTests(unittest.TestCase):
    def test_ratio_recovers_dip_while_data_does_not(self):
        points = 21
        cube, _, magnet = run(points=points)
        fields = np.linspace(FIELD_START, FIELD_STOP, points)
        camera = MultiplicativeDriftCamera(magnet, total_frames=2 * points)
        expected = 1.0 - camera.dip(fields)  # reference is far off the dip

        ratio = cube.extra_datasets["ratio"].mean(axis=(1, 2))
        # What survives is one frame's drift between the two halves of a
        # point (~0.02-0.03%, small next to the 0.3% dip), with a sign that
        # flips every point because the order alternates S,R / R,S...
        residual = ratio - expected
        self.assertLess(np.max(np.abs(residual)), 4e-4)
        self.assertTrue(np.all(residual[:-1] * residual[1:] < 0))
        # ...so it cancels over neighbouring points.
        self.assertLess(np.max(np.abs(residual[:-1] + residual[1:])), 2e-5)

        # Raw data normalised to its own first point: drift dominates.
        data = cube.data.mean(axis=(1, 2))
        normalised = data / data[0] * expected[0]
        self.assertGreater(np.max(np.abs(normalised - expected)), 5e-3)

        self.assertEqual(cube.extra_datasets["ratio"].dtype, np.float64)
        self.assertEqual(cube.extra_datasets["reference"].shape, cube.data.shape)

    def test_zero_reference_pixel_becomes_nan_and_is_logged(self):
        magnet = RecordingMagnet()
        camera = MultiplicativeDriftCamera(magnet, total_frames=6, zero_reference=True)
        with self.assertLogs("experiments.zero_field_experiment", logging.WARNING) as logs:
            cube = make_experiment(magnet, camera, points=3).run()
        ratio = cube.extra_datasets["ratio"]
        self.assertTrue(np.all(np.isnan(ratio[:, 0, 0])))
        self.assertTrue(np.all(np.isfinite(ratio[:, 1:, :])))
        self.assertTrue(any("3 ratio pixel(s) set to NaN" in line for line in logs.output))
        # The live value ignores the NaN pixel.
        self.assertTrue(np.isfinite(mean_ratio(ratio[0])))


class RelayFlipTests(unittest.TestCase):
    """Real Magnet/MagnetAxis on simulated supplies (zero offsets): a
    positive sweep with a negative reference flips X on every half that
    changes field, and each flip gets post-flip settling."""

    SWEEP = dict(field_start=0.5, field_stop=1.0, field_points=3, field_axis="X")

    def _run(self, reference_gauss):
        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=make_camera(), magnet=make_magnet(),
            settling_time_ms=100, post_flip_settling_ms=250,
            reference_enabled=True, reference_field_gauss=reference_gauss,
            **self.SWEEP,
        )
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            cube = experiment.run()
        return cube.metadata, clock.sleeps

    def test_opposite_branch_reference_flips_on_both_halves(self):
        metadata, sleeps = self._run(-REFERENCE_GAUSS)
        # p0: S0 R(flip) | p1: R(repeat) S1(flip) | p2: S2 R(flip)
        self.assertEqual(metadata["relay_flip_occurred"], [False, True, False])
        self.assertEqual(metadata["relay_flip_axes"], ["", "X", ""])
        self.assertEqual(metadata["reference_relay_flip_occurred"], [True, False, True])
        self.assertEqual(metadata["reference_relay_flip_axes"], ["X", "", "X"])
        self.assertEqual(sleeps, [0.1, 0.1, 0.25, 0.1, 0.25, 0.1, 0.1, 0.25])
        self.assertEqual([p[0] for p in metadata["model_polarity"]], ["+", "+", "+"])
        self.assertEqual(
            [p[0] for p in metadata["reference_model_polarity"]], ["-", "-", "-"]
        )
        np.testing.assert_allclose(
            np.asarray(metadata["reference_model_field_vector_mT"])[:, 0],
            -REFERENCE_GAUSS * 0.1,
        )

    def test_same_branch_reference_never_flips(self):
        metadata, sleeps = self._run(REFERENCE_GAUSS)
        self.assertEqual(metadata["relay_flip_occurred"], [False] * 3)
        self.assertEqual(metadata["reference_relay_flip_occurred"], [False] * 3)
        self.assertEqual(sleeps, [0.1] * 5)


class PreflightTests(unittest.TestCase):
    """_preflight_field_range() called on a stand-in for the window: a
    third ZeroFieldWindow per process is unsafe here (see
    tests/test_zero_field_window_save_toggle.py)."""

    def _preflight(self, config):
        from tests.test_magnetic_field_gui_units import _make_magnet

        holder = SimpleNamespace(magnet=_make_magnet())  # X: about -39 to +41 G
        holder._axis_limit_note = lambda name: ZeroFieldWindow._axis_limit_note(holder, name)
        with patch.object(QMessageBox, "critical") as critical:
            result = ZeroFieldWindow._preflight_field_range(holder, config)
        return result, critical

    def _config(self, **overrides):
        config = dict(
            field_start=-2.0, field_stop=2.0, field_axis="X",
            reference_enabled=True, reference_field_gauss=8.0,
        )
        config.update(overrides)
        return config

    def test_unreachable_reference_is_refused(self):
        ok, critical = self._preflight(self._config(reference_field_gauss=60.0))
        self.assertFalse(ok)
        critical.assert_called_once()
        _parent, title, text = critical.call_args.args
        self.assertEqual(title, "Field range out of bounds")
        self.assertIn("Reference field: 60 G on X", text)

    def test_reachable_reference_passes(self):
        ok, critical = self._preflight(self._config())
        self.assertTrue(ok)
        critical.assert_not_called()

    def test_reference_ignored_when_disabled(self):
        ok, _ = self._preflight(
            self._config(reference_enabled=False, reference_field_gauss=60.0)
        )
        self.assertTrue(ok)


class AverageFileTests(unittest.TestCase):
    def _worker_run(self, directory, points=5, **overrides):
        config = {
            "field_start": FIELD_START, "field_stop": FIELD_STOP,
            "field_points": points, "field_axis": "X",
            "settling_time_ms": 0, "averages": 1,
            "averaging_enabled": True, "num_scans": 2,
            "save_raw_scans": True, "alternate_direction": True,
            "reference_enabled": True, "reference_field_gauss": REFERENCE_GAUSS,
        }
        config.update(overrides)
        magnet = RecordingMagnet()
        scans = config["num_scans"] if config["averaging_enabled"] else 1
        camera = MultiplicativeDriftCamera(magnet, total_frames=2 * points * scans)
        name = "zero_field_test_average.h5" if scans > 1 else "zero_field_test.h5"
        output_path = Path(directory) / name
        worker = ZeroFieldWorker(None, camera, magnet, config, None, {}, output_path)
        finished = []
        worker.finished_signal.connect(lambda cube, *rest: finished.append((cube, rest)))
        worker.start()
        return output_path, finished[0]

    def test_ratio_averages_are_field_aligned_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path, (cube, _) = self._worker_run(directory)
            loaded = ImageCube.load(path)
            extras = loaded.extra_datasets
            self.assertEqual(set(extras), {
                "forward_average", "reverse_average", "reference", "ratio",
                "ratio_forward_average", "ratio_reverse_average",
            })
            for name in extras:
                self.assertEqual(extras[name].shape, loaded.data.shape, name)
                np.testing.assert_allclose(extras[name], cube.extra_datasets[name])
            np.testing.assert_allclose(
                extras["ratio"],
                0.5 * (extras["ratio_forward_average"] + extras["ratio_reverse_average"]),
            )

            raw_1 = ImageCube.load(Path(directory) / "zero_field_test_scan_001.h5")
            raw_2 = ImageCube.load(Path(directory) / "zero_field_test_scan_002.h5")
            # Raw files: acquisition order, with their own reference / ratio.
            self.assertEqual(raw_2.metadata["sweep_direction"], "reverse")
            np.testing.assert_allclose(
                extras["ratio_reverse_average"], raw_2.extra_datasets["ratio"][::-1]
            )
            np.testing.assert_allclose(
                extras["ratio_forward_average"], raw_1.extra_datasets["ratio"]
            )
            np.testing.assert_allclose(
                extras["reference"],
                0.5 * (raw_1.extra_datasets["reference"]
                       + raw_2.extra_datasets["reference"][::-1]),
            )
            # Field-aligned ratio still shows the dip at 0 G (middle point).
            ratio = extras["ratio"].mean(axis=(1, 2))
            self.assertEqual(int(np.argmin(ratio)), 2)

            metadata = loaded.metadata
            self.assertTrue(metadata["reference_enabled"])
            self.assertEqual(metadata["reference_field_gauss"], REFERENCE_GAUSS)
            # 5 points: field j has the same S/R parity in both directions.
            self.assertEqual(metadata["reference_order"], ["SR", "RS", "SR", "RS", "SR"])
            self.assertEqual(len(metadata["scan_reference_point_times_s"]), 2)
            self.assertNotIn("reference_point_time_s", metadata)
            for key in ("reference", "ratio", "reference_order", "reference_point_time_s"):
                self.assertIn(key, metadata["field_value_definitions"])
            self.assertEqual(len(raw_2.metadata["reference_point_time_s"]), 5)

    def test_even_point_count_marks_order_mixed(self):
        # 4 points: field j is index j forward but index 3 - j in reverse,
        # so its S/R order differs between the directions.
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._worker_run(directory, points=4)
            self.assertEqual(ImageCube.load(path).metadata["reference_order"], ["mixed"] * 4)

    def test_reference_flips_merge_as_union(self):
        average = ImageCube(data=None, metadata={
            "reference_order": ["SR"], "reference_model_field_vector_mT": [[0.8, 0, 0]],
            "reference_model_polarity": [["+", "+", "+"]],
            "reference_relay_flip_occurred": [False], "reference_relay_flip_axes": [""],
        })
        scan = ImageCube(data=None, metadata={
            "reference_order": ["RS"], "reference_model_field_vector_mT": [[0.6, 0, 0]],
            "reference_model_polarity": [["-", "+", "+"]],
            "reference_relay_flip_occurred": [True], "reference_relay_flip_axes": ["X"],
        })
        ZeroFieldExperiment._merge_reference_metadata(average, scan, 2)
        metadata = average.metadata
        self.assertEqual(metadata["reference_relay_flip_occurred"], [True])
        self.assertEqual(metadata["reference_relay_flip_axes"], ["X"])
        self.assertEqual(metadata["reference_order"], ["mixed"])
        self.assertEqual(metadata["reference_model_polarity"], [["mixed", "+", "+"]])
        np.testing.assert_allclose(metadata["reference_model_field_vector_mT"], [[0.7, 0, 0]])

    def test_live_curve_plots_mean_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            _, (cube, (fields, signals, _stopped)) = self._worker_run(
                directory, averaging_enabled=False, num_scans=1
            )
        self.assertTrue(np.all(signals < 1.01) and np.all(signals > 0.99))
        np.testing.assert_allclose(
            signals, [mean_ratio(plane) for plane in cube.extra_datasets["ratio"]]
        )
        np.testing.assert_allclose(fields, np.linspace(FIELD_START, FIELD_STOP, 5))
        # The window's completed-scan curve uses the same values.
        holder = SimpleNamespace(_reference_enabled=True)
        curve_fields, curve = ZeroFieldWindow._curve_from_cube(holder, cube)
        np.testing.assert_allclose(curve, signals)
        np.testing.assert_allclose(curve_fields, fields)


class StreamingTests(unittest.TestCase):
    def _experiment(self, path, camera_class=MultiplicativeDriftCamera, **overrides):
        magnet = RecordingMagnet()
        camera = camera_class(magnet, total_frames=10)
        return make_experiment(
            magnet, camera, points=5,
            stream_scan_path=lambda _index: path, stream_path_is_output=True,
            **overrides,
        )

    def test_single_scan_stream_holds_reference_and_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field_test.h5"
            cube = self._experiment(path).run()
            loaded = ImageCube.load(path)
            self.assertTrue(loaded.metadata["scan_complete"])
            for name in ("reference", "ratio"):
                np.testing.assert_allclose(loaded.extra_datasets[name], cube.extra_datasets[name])
            self.assertEqual(len(loaded.metadata["reference_order"]), 5)

    def test_crash_keeps_reference_and_ratio_for_completed_points(self):
        class CrashingCamera(MultiplicativeDriftCamera):
            def snap(self):
                if self.frame_count == 5:  # point 2's first half
                    raise RuntimeError("camera lost")
                return super().snap()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field_test.h5"
            with self.assertRaises(RuntimeError):
                self._experiment(path, camera_class=CrashingCamera).run()
            loaded = ImageCube.load(path)
            self.assertFalse(loaded.metadata["scan_complete"])
            self.assertEqual(loaded.data.shape[0], 2)
            self.assertEqual(loaded.extra_datasets["reference"].shape, loaded.data.shape)
            self.assertEqual(loaded.extra_datasets["ratio"].shape, loaded.data.shape)

    def test_stop_truncates_reference_lists_and_planes(self):
        holder = {}

        def stop_after_two(*_):
            if len(holder["experiment"].image_cube.data) == 2:
                holder["experiment"].stop()

        magnet = RecordingMagnet()
        experiment = make_experiment(
            magnet, MultiplicativeDriftCamera(magnet, total_frames=10),
            live_update_callback=stop_after_two,
        )
        holder["experiment"] = experiment
        cube = experiment.run()
        self.assertEqual(cube.extra_datasets["ratio"].shape[0], 2)
        for key in ("reference_order", "reference_point_time_s",
                    "reference_relay_flip_occurred", "reference_model_polarity"):
            self.assertEqual(len(cube.metadata[key]), 2, key)


class ReferenceOffTests(unittest.TestCase):
    """reference_enabled off: the sweep and its output are what they were
    before the mode existed, plus reference_enabled=False."""

    REFERENCE_KEYS = (
        "reference_field_gauss", "reference_order", "reference_point_time_s",
        "reference_relay_flip_occurred", "reference_relay_flip_axes",
        "reference_model_field_vector_mT", "reference_model_polarity",
        "scan_reference_point_times_s",
    )

    def test_off_is_one_command_per_point_and_no_reference_data(self):
        cube, _, magnet = run(
            points=5, reference_enabled=False, averaging_enabled=True, num_scans=2,
            alternate_direction=True,
        )
        fields = np.linspace(FIELD_START, FIELD_STOP, 5).round(9).tolist()
        self.assertEqual(magnet.swept_gauss(), [0.0] + fields + [0.0] + fields[::-1])
        self.assertEqual(set(cube.extra_datasets), {"forward_average", "reverse_average"})
        self.assertIs(cube.metadata["reference_enabled"], False)
        for key in self.REFERENCE_KEYS:
            self.assertNotIn(key, cube.metadata)
        for key in ("reference", "ratio", "reference_order"):
            self.assertNotIn(key, cube.metadata["field_value_definitions"])

    def test_default_is_off_and_matches_explicit_off(self):
        def metadata(**overrides):
            magnet = RecordingMagnet()
            experiment = ZeroFieldExperiment(
                hardware_manager=None, magnet=magnet,
                camera=MultiplicativeDriftCamera(magnet, total_frames=3),
                field_start=FIELD_START, field_stop=FIELD_STOP, field_points=3,
                field_axis="X", **overrides,
            )
            cube = experiment.run()
            data = {k: v for k, v in cube.metadata.items()
                    if k not in ("acquisition_started_at_utc", "point_time_s")}
            return cube, data, magnet.swept_gauss()

        default_cube, default_meta, default_commands = metadata()
        off_cube, off_meta, off_commands = metadata(reference_enabled=False)
        self.assertEqual(default_meta, off_meta)
        self.assertEqual(default_commands, off_commands)
        np.testing.assert_array_equal(default_cube.data, off_cube.data)
        self.assertEqual(default_cube.extra_datasets, {})

    def test_off_timing_stages_are_unchanged(self):
        magnet = RecordingMagnet()
        experiment = make_experiment(
            magnet, MultiplicativeDriftCamera(magnet, total_frames=3), points=3,
            reference_enabled=False, settling_time_ms=100,
        )
        clock = RecordingTime()
        with patch("experiments.zero_field_experiment.time", clock):
            experiment.run()
        self.assertEqual(clock.sleeps, [0.1] * 3)

    def test_validation(self):
        with self.assertRaises(ValueError):
            make_experiment(RecordingMagnet(), None, reference_enabled=1)
        with self.assertRaises(ValueError):
            make_experiment(RecordingMagnet(), None, reference_field_gauss=float("nan"))


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.widget = ZeroFieldWidget()
        _KEEP_ALIVE.append(cls.widget)

    def setUp(self):
        w = self.widget
        w.reference_enabled_check.setEnabled(True)
        w.reference_enabled_check.setChecked(False)
        w.averaging_enabled_check.setChecked(False)

    def test_defaults(self):
        w = self.widget
        config = w.get_config()
        self.assertFalse(config["reference_enabled"])
        self.assertEqual(config["reference_field_gauss"], 8.0)
        self.assertFalse(w.reference_field_spin.isEnabled())
        self.assertEqual(w.reference_field_spin.decimals(), 4)
        self.assertEqual(w.reference_field_spin.minimum(), -10000)
        self.assertEqual(w.reference_field_spin.maximum(), 10000)
        self.assertIn("signal/reference", w.reference_enabled_check.toolTip())

    def test_works_without_averaging(self):
        w = self.widget
        self.assertTrue(w.reference_enabled_check.isEnabled())
        w.reference_enabled_check.setChecked(True)
        self.assertTrue(w.reference_field_spin.isEnabled())
        w.reference_field_spin.setValue(-12.5)
        config = w.get_config()
        self.assertTrue(config["reference_enabled"])
        self.assertEqual(config["reference_field_gauss"], -12.5)
        w.reference_field_spin.setValue(8.0)

    def test_disabled_but_checked_reports_false(self):
        w = self.widget
        w.reference_enabled_check.setChecked(True)
        w.reference_enabled_check.setEnabled(False)
        self.assertTrue(w.reference_enabled_check.isChecked())
        self.assertFalse(w.get_config()["reference_enabled"])

    def test_signal_mode_relabels_live_curve(self):
        w = self.widget
        w.set_signal_mode(True)
        self.assertEqual(w.plot.getAxis("left").labelText, "Signal / reference")
        w.update_fluorescence_curve([0.0], [0.997])
        self.assertEqual(w.current_signal_label.text(), "Signal / reference: 0.997")
        w.set_signal_mode(False)
        self.assertEqual(
            w.plot.getAxis("left").labelText, "Mean Fluorescence (counts/pixel)"
        )


if __name__ == "__main__":
    unittest.main()
