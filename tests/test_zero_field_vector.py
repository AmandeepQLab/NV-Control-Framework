"""Hardware-free tests for Zero Field sweeps along an arbitrary direction.

With field_axis "vector" and field_direction (nx, ny, nz), every point
commands B = s * n_hat on all three axes at once, where s is the point's
value of the start/stop linspace and n_hat the normalised direction.
Single-axis sweeps (X/Y/Z) must behave and record exactly as before, apart
from a new field_direction metadata key that is None for them.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

# QApplication must exist before pyqtgraph (via the widget) is imported;
# see tests/test_zero_field_window_save_toggle.py.
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.zero_field_experiment import (  # noqa: E402
    ZeroFieldExperiment,
    unit_direction,
)
from framework.image_cube import ImageCube  # noqa: E402
from gui import zero_field_queue as queue_model  # noqa: E402
from gui.widgets.zero_field_widget import ZeroFieldWidget  # noqa: E402
from gui.zero_field_window import ZeroFieldWindow, field_range_problem  # noqa: E402
from gui.zero_field_worker import ZeroFieldWorker  # noqa: E402
from tests.test_magnetic_field_gui_units import _make_magnet as make_offset_magnet  # noqa: E402
from tests.test_zero_field_queue import (  # noqa: E402
    QueueWindowTestCase,
    _dispose,
    _pump_until,
    _thread_running,
)
from tests.test_zero_field_reference import (  # noqa: E402
    MultiplicativeDriftCamera,
    RecordingMagnet,
)
from tests.test_zero_field_timing import make_camera, make_magnet  # noqa: E402

SQRT3 = np.sqrt(3.0)
N111 = np.array([1.0, 1.0, 1.0]) / SQRT3
N110 = np.array([1.0, 1.0, 0.0]) / np.sqrt(2.0)

# get_config() at its defaults, exactly as before vector sweeps existed.
SINGLE_AXIS_DEFAULTS = {
    "field_start": -2.0, "field_stop": 2.0, "field_points": 5,
    "field_axis": "X", "settling_time_ms": 100,
    "post_flip_settling_ms": 500, "averages": 1,
    "averaging_enabled": False, "num_scans": 1, "save_raw_scans": False,
    "alternate_direction": False, "reference_enabled": False,
    "reference_field_gauss": -8.0, "save_data": True,
}


def vector_experiment(magnet, camera, direction=(1, 1, 1), points=5, **overrides):
    kwargs = dict(
        hardware_manager=None, camera=camera, magnet=magnet,
        field_start=-1.0, field_stop=1.0, field_points=points,
        field_axis="vector", field_direction=list(direction),
        settling_time_ms=0, averages=1,
    )
    kwargs.update(overrides)
    return ZeroFieldExperiment(**kwargs)


def expected_mT(s_gauss, n_hat):
    return [s_gauss * 0.1 * n for n in n_hat]


class VectorCommandTests(unittest.TestCase):
    def test_111_commands_s_times_n_hat_on_all_axes_per_point(self):
        magnet = RecordingMagnet()
        camera = MultiplicativeDriftCamera(magnet, total_frames=5)
        cube = vector_experiment(magnet, camera).run()
        fields = np.linspace(-1.0, 1.0, 5)
        # First command: setup_scan()'s calibrated zero; then one per point.
        self.assertEqual(magnet.commands[0], (0.0, 0.0, 0.0))
        self.assertEqual(len(magnet.commands), 1 + 5)
        for command, s in zip(magnet.commands[1:], fields):
            np.testing.assert_allclose(command, expected_mT(s, N111), atol=1e-15)
            # All three axes equal: a <111> field.
            self.assertEqual(command[0], command[1])
            self.assertEqual(command[1], command[2])
        # field_values_gauss / scan axis are the signed magnitudes s.
        self.assertEqual(cube.metadata["field_values_gauss"], fields.tolist())
        self.assertEqual(list(cube.scan_axis_values), fields.tolist())
        np.testing.assert_allclose(
            cube.metadata["model_field_vector_mT"],
            [expected_mT(s, N111) for s in fields], atol=1e-15,
        )
        # The model value along n_hat equals s when the model follows the request.
        np.testing.assert_allclose(
            cube.metadata["model_field_values_gauss"], fields, atol=1e-12
        )

    def test_110_leaves_z_at_zero_including_negative_s(self):
        magnet = RecordingMagnet()
        camera = MultiplicativeDriftCamera(magnet, total_frames=3)
        vector_experiment(magnet, camera, direction=(1, 1, 0), points=3).run()
        for command, s in zip(magnet.commands[1:], (-1.0, 0.0, 1.0)):
            np.testing.assert_allclose(command, expected_mT(s, N110), atol=1e-15)
            self.assertEqual(command[2], 0.0)
            self.assertFalse(np.signbit(command[2]), "no -0.0 sent to Z")

    def test_metadata_records_axis_direction_and_definitions(self):
        magnet = RecordingMagnet()
        cube = vector_experiment(
            magnet, MultiplicativeDriftCamera(magnet, total_frames=5),
            direction=(2, 2, 2),
        ).run()
        metadata = cube.metadata
        self.assertEqual(metadata["field_axis"], "vector")
        np.testing.assert_allclose(metadata["field_direction"], N111)
        self.assertEqual(metadata["non_swept_axis_field_mT"], {})
        definitions = metadata["field_value_definitions"]
        self.assertIn("field_direction", definitions)
        self.assertIn("signed magnitude", definitions["field_values_gauss"])
        self.assertIn("along field_direction", definitions["model_field_values_gauss"])

    def test_real_magnet_axes_flip_at_different_s(self):
        # Calibration offsets differ per axis (X ~0.1 mT, Y ~-0.02 mT,
        # Z ~0.03 mT), so along <111> each relay changes branch at its own s.
        experiment = vector_experiment(
            make_offset_magnet(), make_camera(), field_start=-3.0,
            field_stop=3.0, points=13,
        )
        cube = experiment.run()
        axes = cube.metadata["relay_flip_axes"]
        # s = -3, -2.5, ..., +3 G; each axis gets s / sqrt(3). Point 0 puts
        # every axis on its negative branch. Going up, each flips back where
        # its own target passes its negative-branch intercept: Y (-0.1 G) at
        # s = 0, Z (+0.4 G) at s = 1, X (+1.1 G) at s = 2.
        self.assertEqual(
            axes, ["X,Y,Z", "", "", "", "", "", "Y", "", "Z", "", "X", "", ""]
        )
        self.assertEqual(
            cube.metadata["relay_flip_occurred"], [bool(a) for a in axes]
        )
        polarity = cube.metadata["model_polarity"]
        self.assertEqual(polarity[0], ["-", "-", "-"])
        self.assertEqual(polarity[-1], ["+", "+", "+"])


class DirectionValidationTests(unittest.TestCase):
    def test_unit_direction_normalises(self):
        np.testing.assert_allclose(unit_direction([1, 1, 1]), N111)
        np.testing.assert_allclose(unit_direction([0, 3, 4]), [0, 0.6, 0.8])
        np.testing.assert_allclose(unit_direction([-2, 0, 0]), [-1, 0, 0])

    def test_invalid_directions_are_rejected(self):
        for bad in ([0, 0, 0], [0.0, 0.0, 1e-15], [1, 1], [1, 1, 1, 1],
                    [1, float("nan"), 0], [1, True, 0], "111", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    unit_direction(bad)

    def test_experiment_normalises_and_rejects_zero(self):
        magnet = RecordingMagnet()
        experiment = vector_experiment(magnet, None, direction=(0, 0, 5))
        self.assertEqual(experiment.field_direction, [0.0, 0.0, 1.0])
        with self.assertRaises(ValueError):
            vector_experiment(magnet, None, direction=(0, 0, 0))
        with self.assertRaises(ValueError):  # vector without a direction
            vector_experiment(magnet, None, field_direction=None)
        with self.assertRaises(ValueError):  # a direction only means something for vector
            ZeroFieldExperiment(
                hardware_manager=None, camera=None, magnet=magnet, field_start=0,
                field_stop=1, field_points=2, field_axis="X", field_direction=[1, 0, 0],
            )

    def test_zero_vector_is_refused_before_a_scan(self):
        config = dict(SINGLE_AXIS_DEFAULTS, field_axis="vector", field_direction=[0, 0, 0])
        title, text = field_range_problem(make_magnet(), config, lambda name: "")
        self.assertEqual(title, "Invalid field direction")
        self.assertIn("zero vector", text)


class VectorPreflightTests(unittest.TestCase):
    """field_range_problem() on a calibrated magnet with reachable ranges of
    about X -39.4..+41, Y -35.6..+34.8, Z -45.1..+45.3 G."""

    def setUp(self):
        self.magnet = make_offset_magnet()
        holder = SimpleNamespace(magnet=self.magnet)
        self.note = lambda name: ZeroFieldWindow._axis_limit_note(holder, name)

    def problem(self, start, stop, direction=(1, 1, 1), **overrides):
        config = dict(
            SINGLE_AXIS_DEFAULTS, field_axis="vector", field_direction=list(direction),
            field_start=start, field_stop=stop,
        )
        config.update(overrides)
        return field_range_problem(self.magnet, config, self.note)

    def test_endpoint_out_of_range_on_any_one_axis_is_refused(self):
        # Each direction puts the named axis past its limit at the endpoint
        # while the other two stay well inside theirs.
        cases = (
            ("X", (1, 0.1, 0.1), 0.0, 42.0),
            ("X", (1, 0.1, 0.1), -40.0, 0.0),
            ("Y", (0.1, 1, 0.1), 0.0, 35.5),
            ("Y", (0.1, 1, 0.1), -36.0, 0.0),
            ("Z", (0.1, 0.1, 1), 0.0, 46.0),
            ("Z", (0.1, 0.1, 1), -46.0, 0.0),
        )
        for axis, direction, start, stop in cases:
            with self.subTest(axis=axis, start=start, stop=stop):
                problem = self.problem(start, stop, direction)
                self.assertIsNotNone(problem)
                title, text = problem
                self.assertEqual(title, "Field range out of bounds")
                self.assertIn(f"{axis}-axis reachable range", text)
                self.assertIn("along n = (", text)
                # Just inside the limit, the same direction is accepted.
                scale = 0.97
                self.assertIsNone(self.problem(start * scale, stop * scale, direction))

    def test_111_sweep_is_limited_by_the_weakest_axis(self):
        # s * 0.577 per axis: 60 G gives 34.6 G on Y (limit +34.8) ...
        self.assertIsNone(self.problem(-60.0, 60.0))
        # ... 61 G gives 35.2 G on Y: refused, and only Y is named.
        title, text = self.problem(-1.0, 61.0)
        self.assertIn("Y-axis reachable range", text)
        title, text = self.problem(-62.0, 1.0)  # -35.8 G on Y (limit -35.6)
        self.assertIn("Y-axis reachable range", text)

    def test_reference_is_checked_along_the_direction(self):
        self.assertIsNone(self.problem(-1.0, 1.0, reference_enabled=True,
                                       reference_field_gauss=-60.0))
        title, text = self.problem(-1.0, 1.0, reference_enabled=True,
                                   reference_field_gauss=-62.0)
        self.assertEqual(title, "Field range out of bounds")
        self.assertIn("Reference field: -62 G along n = (", text)
        # Ignored when the reference is off.
        self.assertIsNone(self.problem(-1.0, 1.0, reference_enabled=False,
                                       reference_field_gauss=-500.0))

    def test_single_axis_check_is_unchanged(self):
        config = dict(SINGLE_AXIS_DEFAULTS, field_start=0.0, field_stop=42.0)
        title, text = field_range_problem(self.magnet, config, self.note)
        self.assertIn("Requested sweep: 0 G to 42 G on X.", text)
        config["field_stop"] = 40.0
        self.assertIsNone(field_range_problem(self.magnet, config, self.note))


class VectorReferenceTests(unittest.TestCase):
    def test_reference_commands_s_ref_times_n_hat(self):
        magnet = RecordingMagnet()
        camera = MultiplicativeDriftCamera(magnet, total_frames=10)
        cube = vector_experiment(
            magnet, camera, reference_enabled=True, reference_field_gauss=-8.0,
        ).run()
        fields = np.linspace(-1.0, 1.0, 5)
        reference = expected_mT(-8.0, N111)
        expected = [[0.0, 0.0, 0.0]]
        for i, s in enumerate(fields):
            signal = expected_mT(s, N111)
            expected += [signal, reference] if i % 2 == 0 else [reference, signal]
        np.testing.assert_allclose(magnet.commands, expected, atol=1e-15)
        np.testing.assert_allclose(
            cube.metadata["reference_model_field_vector_mT"], [reference] * 5, atol=1e-15
        )
        self.assertEqual(cube.extra_datasets["ratio"].shape, cube.data.shape)
        self.assertIn(
            "reference_field_gauss * field_direction",
            cube.metadata["field_value_definitions"]["reference"],
        )


class VectorWorkerFileTests(unittest.TestCase):
    """The full worker path: alternate direction + reference + averaging,
    streamed raw files and the atomic-saved average."""

    def test_average_and_raw_files_in_vector_mode(self):
        config = {
            "field_start": -1.0, "field_stop": 1.0, "field_points": 5,
            "field_axis": "vector", "field_direction": list(N110),
            "settling_time_ms": 0, "post_flip_settling_ms": 0, "averages": 1,
            "averaging_enabled": True, "num_scans": 2, "save_raw_scans": True,
            "alternate_direction": True, "reference_enabled": True,
            "reference_field_gauss": -8.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test_average.h5"
            magnet = make_magnet()
            worker = ZeroFieldWorker(None, make_camera(), magnet, config, None, {}, output_path)
            vectors = []
            worker.field_updated.connect(lambda x, y, z: vectors.append((x, y, z)))
            worker.start()

            fields = np.linspace(-1.0, 1.0, 5)
            # Live magnet-window vector: s * n_hat per stored point, both scans.
            np.testing.assert_allclose(
                vectors,
                [expected_mT(s, N110) for s in list(fields) + list(fields[::-1])],
                atol=1e-15,
            )
            average = ImageCube.load(output_path)
            metadata = average.metadata
            self.assertEqual(metadata["field_axis"], "vector")
            np.testing.assert_allclose(metadata["field_direction"], N110)
            self.assertEqual(metadata["field_values_gauss"], fields.tolist())
            self.assertEqual(metadata["scan_directions"], ["forward", "reverse"])
            np.testing.assert_allclose(
                metadata["model_field_vector_mT"],
                [expected_mT(s, N110) for s in fields], atol=1e-12,
            )
            np.testing.assert_allclose(
                metadata["reference_model_field_vector_mT"],
                [expected_mT(-8.0, N110)] * 5, atol=1e-12,
            )
            self.assertEqual(
                set(average.extra_datasets),
                {"forward_average", "reverse_average", "reference", "ratio",
                 "ratio_forward_average", "ratio_reverse_average"},
            )
            raw_2 = ImageCube.load(Path(directory) / "zero_field_test_scan_002.h5")
            self.assertEqual(raw_2.metadata["sweep_direction"], "reverse")
            self.assertEqual(raw_2.metadata["field_values_gauss"], fields[::-1].tolist())
            np.testing.assert_allclose(raw_2.metadata["field_direction"], N110)

    def test_single_axis_file_has_null_field_direction(self):
        config = {
            "field_start": -1.0, "field_stop": 1.0, "field_points": 3,
            "field_axis": "X", "settling_time_ms": 0, "averages": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "zero_field_test.h5"
            ZeroFieldWorker(
                None, make_camera(), make_magnet(), config, None, {}, output_path
            ).start()
            metadata = ImageCube.load(output_path).metadata
        self.assertIn("field_direction", metadata)
        self.assertIsNone(metadata["field_direction"])
        self.assertEqual(metadata["field_axis"], "X")
        self.assertNotIn("field_direction", metadata["field_value_definitions"])


class VectorWidgetTests(unittest.TestCase):
    def setUp(self):
        self.widget = ZeroFieldWidget()
        self.addCleanup(_dispose, self.widget)

    def test_single_axis_get_config_is_unchanged(self):
        w = self.widget
        self.assertEqual(w.get_config(), SINGLE_AXIS_DEFAULTS)
        # Selecting Vector and going back changes nothing for X/Y/Z.
        w.field_axis_combo.setCurrentText("Vector")
        w.set_field_direction((1, 2, 3))
        for axis in ("X", "Y", "Z"):
            w.field_axis_combo.setCurrentText(axis)
            self.assertEqual(w.get_config(), dict(SINGLE_AXIS_DEFAULTS, field_axis=axis))
            self.assertNotIn("field_direction", w.get_config())

    def test_vector_option_shows_direction_and_reports_normalised(self):
        w = self.widget
        items = [w.field_axis_combo.itemText(i) for i in range(w.field_axis_combo.count())]
        self.assertEqual(items, ["X", "Y", "Z", "Vector"])
        self.assertTrue(w.field_direction_box.isHidden())
        w.field_axis_combo.setCurrentText("Vector")
        self.assertFalse(w.field_direction_box.isHidden())
        self.assertEqual(w.raw_field_direction(), [1.0, 1.0, 1.0])  # default (1, 1, 1)
        config = w.get_config()
        self.assertEqual(config["field_axis"], "vector")
        np.testing.assert_allclose(config["field_direction"], N111)
        self.assertEqual(
            {k: v for k, v in config.items() if k not in ("field_axis", "field_direction")},
            {k: v for k, v in SINGLE_AXIS_DEFAULTS.items() if k != "field_axis"},
        )
        w.direction_110_button.click()
        self.assertEqual(w.raw_field_direction(), [1.0, 1.0, 0.0])
        np.testing.assert_allclose(w.get_config()["field_direction"], N110)
        self.assertIn("0.707, 0.707, 0.000", w.field_direction_label.text())
        w.direction_111_button.click()
        self.assertEqual(w.raw_field_direction(), [1.0, 1.0, 1.0])
        w.field_axis_combo.setCurrentText("Z")
        self.assertTrue(w.field_direction_box.isHidden())

    def test_zero_vector_is_flagged(self):
        w = self.widget
        w.field_axis_combo.setCurrentText("Vector")
        w.set_field_direction((0, 0, 0))
        self.assertIn("Zero vector", w.field_direction_label.text())
        self.assertEqual(w.get_config()["field_direction"], [0.0, 0.0, 0.0])

    def test_set_config_round_trip(self):
        w = self.widget
        config = dict(SINGLE_AXIS_DEFAULTS, field_axis="vector",
                      field_direction=list(N110), field_start=-3.0)
        w.set_config(config)
        self.assertEqual(w.field_axis_combo.currentText(), "Vector")
        result = w.get_config()
        np.testing.assert_allclose(result.pop("field_direction"), N110)
        config.pop("field_direction")
        self.assertEqual(result, config)
        w.set_config(SINGLE_AXIS_DEFAULTS)
        self.assertEqual(w.get_config(), SINGLE_AXIS_DEFAULTS)


class VectorQueueModelTests(unittest.TestCase):
    def vector_config(self, direction=(1, 1, 1)):
        return dict(SINGLE_AXIS_DEFAULTS, field_axis="vector",
                    field_direction=unit_direction(direction))

    def test_summary_shows_the_direction(self):
        self.assertEqual(
            queue_model.summarize(self.vector_config()),
            "vec(0.58,0.58,0.58) -2→2 G, 5 pts, 1 rep, 1 scan, settle 100 ms, "
            "ref off, alt off",
        )
        self.assertTrue(
            queue_model.summarize(self.vector_config((1, -1, 0))).startswith(
                "vec(0.71,-0.71,0) "
            )
        )
        # Single-axis summaries are unchanged.
        self.assertTrue(queue_model.summarize(SINGLE_AXIS_DEFAULTS).startswith("X -2→2 G"))

    def test_validate_config(self):
        clean = queue_model.validate_config(
            dict(self.vector_config(), field_direction=[2, 2, 2])
        )
        np.testing.assert_allclose(clean["field_direction"], N111)  # normalised
        for bad in ([0, 0, 0], [1, 1], "x", [1, None, 0]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    queue_model.validate_config(
                        dict(self.vector_config(), field_direction=bad)
                    )
        missing = self.vector_config()
        del missing["field_direction"]
        with self.assertRaises(ValueError):
            queue_model.validate_config(missing)
        # X/Y/Z: a stray field_direction is dropped, the config is unchanged.
        self.assertEqual(
            queue_model.validate_config(dict(SINGLE_AXIS_DEFAULTS, field_direction=[1, 0, 0])),
            SINGLE_AXIS_DEFAULTS,
        )

    def test_queue_file_round_trip(self):
        entries = [
            queue_model.QueueEntry.snapshot(self.vector_config((1, 1, 0)), "C:/data"),
            queue_model.QueueEntry.snapshot(SINGLE_AXIS_DEFAULTS, "C:/data"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            queue_model.save_queue_file(path, entries, 1.5, queue_model.ON_FAILURE_SKIP)
            saved = json.loads(path.read_text(encoding="utf-8"))
            loaded, pause, on_failure = queue_model.load_queue_file(path)
        self.assertEqual(saved["entries"][0]["config"]["field_axis"], "vector")
        self.assertNotIn("field_direction", saved["entries"][1]["config"])
        self.assertEqual([entry.config for entry in loaded],
                         [entry.config for entry in entries])
        self.assertEqual((pause, on_failure), (1.5, queue_model.ON_FAILURE_SKIP))


class VectorQueueWindowTests(QueueWindowTestCase):
    """Real window, worker and experiment on sim hardware."""

    def set_vector_form(self, direction, **values):
        self.set_form(**values)
        w = self.window.widget
        w.field_axis_combo.setCurrentText("Vector")
        w.set_field_direction(direction)

    def test_vector_entry_round_trips_through_window_save_and_load(self):
        self.set_vector_form((1, 1, 0), start=-2.0, stop=2.0, points=4)
        self.assertTrue(self.window.add_to_queue())
        entry = self.window.queue_entries[0]
        self.assertEqual(entry.config["field_axis"], "vector")
        np.testing.assert_allclose(entry.config["field_direction"], N110)
        texts = [
            self.window.queue_widget.table.item(0, column).text()
            for column in range(self.window.queue_widget.table.columnCount())
            if self.window.queue_widget.table.item(0, column) is not None
        ]
        self.assertTrue(any(text.startswith("vec(0.71,0.71,0) ") for text in texts), texts)

        path = self.directory / "queue.json"
        self.assertTrue(self.window.save_queue_to(path))
        self.window.queue_entries = []
        self.assertTrue(self.window.load_queue_from(path))
        self.assertEqual(len(self.window.queue_entries), 1)
        self.assertEqual(self.window.queue_entries[0].config, entry.config)

        # And back into the form.
        self.window.widget.field_axis_combo.setCurrentText("X")
        self.window.queue_widget.select_row(0)
        self.window.load_entry_into_form()
        self.assertEqual(self.window.widget.field_axis_combo.currentText(), "Vector")
        np.testing.assert_allclose(self.window.widget.get_config()["field_direction"], N110)

    def test_zero_vector_entry_is_not_added(self):
        self.set_vector_form((0, 0, 0))
        self.assertFalse(self.window.add_to_queue())
        self.assertEqual(self.window.queue_entries, [])
        self.critical.assert_called_once()
        self.assertEqual(self.critical.call_args.args[1], "Invalid field direction")

    def test_zero_vector_run_is_refused(self):
        self.set_vector_form((0, 0, 0))
        self.window.start_scan()
        self.assertFalse(self.window.zero_field_running)
        self.assertIsNone(self.window.thread)
        self.critical.assert_called_once()

    def test_queued_vector_run_writes_its_direction(self):
        entry = self.add(points=3)  # an X entry first, unchanged
        self.set_vector_form((1, 1, 1), start=-1.0, stop=1.0, points=3,
                             averaging=True, scans=2, alternate=True,
                             reference=True, reference_field=-8.0)
        self.assertTrue(self.window.add_to_queue())
        self.run_queue_to_end()
        self.assertEqual(self.statuses(), ["done", "done"])
        x_cube = ImageCube.load(entry.output_path)
        self.assertIsNone(x_cube.metadata["field_direction"])
        self.assertEqual(x_cube.metadata["scan_parameters"], entry.config)
        vector_entry = self.window.queue_entries[1]
        cube = ImageCube.load(vector_entry.output_path)
        self.assertEqual(cube.metadata["field_axis"], "vector")
        np.testing.assert_allclose(cube.metadata["field_direction"], N111)
        self.assertEqual(cube.metadata["scan_parameters"], vector_entry.config)
        self.assertIn("ratio", cube.extra_datasets)
        rows = self.log_rows()
        self.assertTrue(rows[1]["summary"].startswith("vec(0.58,0.58,0.58) "))


if __name__ == "__main__":
    unittest.main()
