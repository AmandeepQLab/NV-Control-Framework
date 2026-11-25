"""ODMR "Magnetic field during scan": set, hold and record the field.

Real scans through ODMRWindow -> ODMRWorker -> ODMRExperiment on SimCamera,
SimMicrowave and SimPulseStreamer, with a real Magnet over simulated power
supplies (tests/test_magnetic_field_gui_units.py::_make_magnet). No real
hardware is touched.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.odmr_experiment import (  # noqa: E402
    COILS_OFF_NOTE,
    FIELD_MODE_CUSTOM,
    FIELD_MODE_LEAVE,
    FIELD_MODE_ZERO,
    VECTOR_ENTRY_COMPONENTS,
    VECTOR_ENTRY_DIRECTION,
    MagnetFieldRangeError,
    ODMRExperiment,
    commanded_vector_gauss,
    format_direction,
)
from framework.field_direction import unit_direction, vector_along  # noqa: E402
from framework.image_cube import ImageCube  # noqa: E402
from gui.magnet_window import (  # noqa: E402
    POST_EXPERIMENT_ELECTRICAL_OFF,
    POST_EXPERIMENT_FIELD_ZERO,
    MagnetControlWindow,
)
from gui.odmr_window import ODMRWindow  # noqa: E402
from hardware.camera.sim_camera import SimCamera  # noqa: E402
from tests.test_magnetic_field_gui_units import _make_magnet  # noqa: E402
from tests.test_odmr_external_acquisition import (  # noqa: E402
    _make_fast_config,
    _make_sim_hardware,
)

# _make_magnet() calibration, in G: X intercepts +1.0/+1.1, Y -0.2/-0.1,
# Z +0.3/+0.4; max 5 A, so X reaches about -39.4 G to +41 G.
_OUT_OF_RANGE_G = 1000.0


def _pump_until(condition, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.001
    binning = 1
    baseline_counts = 0.0


def _record_calls(test, obj, name, log):
    """Log every call of obj.name (instance attribute; removed at cleanup)."""
    original = getattr(obj, name)

    def wrapper(*args, **kwargs):
        log.append((name, args, kwargs))
        return original(*args, **kwargs)

    setattr(obj, name, wrapper)
    test.addCleanup(delattr, obj, name)


_MAGNET_COMMANDS = ("enable", "disable", "set_vector", "electrical_zero", "zero")


class ODMRMagnetFieldTestCase(unittest.TestCase):
    with_magnet = True

    def setUp(self):
        camera = SimCamera()
        camera.connect()
        self.camera = camera
        self.hardware = _make_sim_hardware(camera)
        self.magnet = _make_magnet() if self.with_magnet else None
        if self.magnet is not None:
            self.hardware["magnet"] = self.magnet
        self.log = []
        _record_calls(self, self.hardware["microwave"], "set_frequency", self.log)
        _record_calls(self, camera, "set_roi", self.log)
        if self.magnet is not None:
            for name in _MAGNET_COMMANDS:
                _record_calls(self, self.magnet, name, self.log)
        self.window = ODMRWindow(
            hardware=self.hardware,
            camera=camera,
            acquisition_state_getter=lambda: _AcquisitionState(),
        )
        self.panel = self.window.panel
        self.group = self.panel.magnet_field_group
        self.started = []
        self.window.run_started.connect(lambda: self.started.append(True))
        self.addCleanup(self._dispose)
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)
        self._fast_scan()

    def _dispose(self):
        window = self.window
        if window.odmr_running:
            window.request_stop()
        _pump_until(lambda: not window.odmr_running, 15)
        thread = window.worker_thread()
        _pump_until(lambda: thread is None or not thread.isRunning(), 10)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _fast_scan(self, steps=3):
        p = self.panel
        p.steps_spin.setValue(steps)
        for spin, value in ((p.trigger_delay_spin, 0.001), (p.fire_delay_spin, 0.001),
                            (p.reset_delay_spin, 0.001), (p.pulse_lead_spin, 0.0005),
                            (p.pulse_tail_spin, 0.0005)):
            spin.setValue(value)

    def _calls(self, *names):
        return [entry for entry in self.log if entry[0] in names]

    def _run_scan(self, save_dir=None):
        """Run one scan to completion; return (config.json dict, cube metadata)
        when save_dir is given (DataManager is replaced, nothing goes to data/)."""
        saved = {}
        if save_dir is not None:
            self.panel.save_check.setChecked(True)

            def save_odmr(freqs_hz, signal, config, **_kwargs):
                saved["config"] = dict(config)
                return Path(save_dir)

            self.window.data_manager = Mock(save_odmr=Mock(side_effect=save_odmr))
        self.window.toggle_odmr()
        self.assertTrue(self.window.odmr_running, "scan did not start")
        self.assertTrue(_pump_until(lambda: not self.window.odmr_running))
        thread = self.window.worker_thread()
        _pump_until(lambda: not thread.isRunning(), 10)
        self.critical.assert_not_called()
        if save_dir is None:
            return None, None
        cube = ImageCube.load(Path(save_dir) / "odmr_statistics.h5")
        return saved["config"], cube.metadata

    def _assert_magnet_command_before_acquisition(self):
        names = [name for name, _, _ in self.log]
        first_acquisition = min(names.index("set_frequency"), names.index("set_roi"))
        self.assertLess(names.index("set_vector"), first_acquisition)


class FieldZeroTests(ODMRMagnetFieldTestCase):
    def test_default_mode_is_field_zero(self):
        self.assertEqual(self.group.mode(), FIELD_MODE_ZERO)
        self.assertEqual(self.panel.get_config()["magnet_field_mode"], FIELD_MODE_ZERO)

    def test_enables_and_sets_zero_before_first_point_then_holds(self):
        self.assertFalse(self.magnet.is_enabled())
        self._run_scan()
        set_vector = self._calls("set_vector")
        self.assertEqual(len(set_vector), 1, "field must not change during the scan")
        self.assertEqual(set_vector[0][2], {"bx": 0.0, "by": 0.0, "bz": 0.0})
        names = [name for name, _, _ in self.log]
        self.assertLess(names.index("enable"), names.index("set_vector"))
        self._assert_magnet_command_before_acquisition()
        self.assertEqual(len(self._calls("set_frequency")), 3)
        self.assertEqual(self._calls("disable", "electrical_zero", "zero"), [])
        self.assertTrue(self.magnet.is_enabled())
        self.assertEqual(self.panel.field_label.text(),
                         "Field: calibrated zero (0, 0, 0) G")

    def test_already_enabled_magnet_is_not_re_enabled(self):
        self.magnet.enable()
        self.log.clear()
        self._run_scan()
        self.assertEqual(self._calls("enable"), [])
        self.assertEqual(len(self._calls("set_vector")), 1)

    def test_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        for record in (config, metadata):
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_ZERO)
            self.assertIs(record["magnet_available"], True)
            self.assertIs(record["magnet_enabled"], True)
            self.assertIs(record["magnet_enabled_by_scan"], True)
            self.assertEqual(record["magnet_commanded_vector_gauss"], [0.0, 0.0, 0.0])
            for value in record["magnet_model_vector_gauss"]:
                self.assertAlmostEqual(value, 0.0)
            # 0 G: below the X/Z negative intercepts, above Y's positive one.
            self.assertEqual(record["magnet_model_polarity"], ["-", "+", "-"])
            self.assertNotIn("magnet_field_note", record)
            self.assertNotIn("magnet_calibration_background_gauss", record)
            self.assertIn("not a measurement",
                          record["magnet_field_definitions"]["magnet_model_vector_gauss"])


class CustomVectorTests(ODMRMagnetFieldTestCase):
    def setUp(self):
        super().setUp()
        self.group.set_mode(FIELD_MODE_CUSTOM)

    def test_spinboxes_enabled_only_for_custom(self):
        for mode, enabled in ((FIELD_MODE_CUSTOM, True), (FIELD_MODE_ZERO, False),
                              (FIELD_MODE_LEAVE, False)):
            self.group.set_mode(mode)
            for spin in (self.group.x_spin, self.group.y_spin, self.group.z_spin):
                self.assertEqual(spin.isEnabled(), enabled, mode)

    def test_commands_the_given_vector(self):
        self.group.set_custom_vector(5.0, -2.5, 3.0)
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        set_vector = self._calls("set_vector")
        self.assertEqual(len(set_vector), 1)
        for key, expected in zip(("bx", "by", "bz"), (0.5, -0.25, 0.3)):
            self.assertAlmostEqual(set_vector[0][2][key], expected)
        self._assert_magnet_command_before_acquisition()
        self.assertEqual(self.panel.field_label.text(), "Field: custom (5, -2.5, 3) G")
        for record in (config, metadata):
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_CUSTOM)
            self.assertEqual(record["magnet_commanded_vector_gauss"], [5.0, -2.5, 3.0])
            for value, expected in zip(record["magnet_model_vector_gauss"], (5.0, -2.5, 3.0)):
                self.assertAlmostEqual(value, expected)
            self.assertEqual(record["magnet_model_polarity"], ["+", "-", "+"])

    def test_out_of_range_refused_before_any_mw_or_camera_action(self):
        self.group.set_custom_vector(_OUT_OF_RANGE_G, 0.0, 0.0)
        self.window.toggle_odmr()
        self.critical.assert_called_once()
        self.assertEqual(self.critical.call_args.args[1], "Magnetic field out of range")
        self.assertIn("X-axis reachable range", self.critical.call_args.args[2])
        self.assertFalse(self.window.odmr_running)
        self.assertIsNone(self.window.worker_thread())
        self.assertEqual(self.started, [])
        self.assertEqual(self.log, [], "nothing may be commanded")

    def test_experiment_refuses_out_of_range_before_any_magnet_command(self):
        config = _make_fast_config(
            magnet_field_mode=FIELD_MODE_CUSTOM,
            magnet_custom_vector_gauss=[0.0, 0.0, -_OUT_OF_RANGE_G],
        )
        experiment = ODMRExperiment(self.hardware, config)
        with self.assertRaises(MagnetFieldRangeError) as caught:
            experiment.setup_magnet_field()
        self.assertIn("Z axis out of range", str(caught.exception))
        self.assertEqual(self.log, [])
        self.assertIsNone(experiment.magnet_field_record)


class LeaveAsIsTests(ODMRMagnetFieldTestCase):
    def setUp(self):
        super().setUp()
        self.group.set_mode(FIELD_MODE_LEAVE)

    def test_coils_off_no_magnet_commands_and_background_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        self.assertEqual(self._calls(*_MAGNET_COMMANDS), [])
        self.assertFalse(self.magnet.is_enabled())
        self.assertEqual(self.panel.field_label.text(), "Field: coils off (background)")
        for record in (config, metadata):
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_LEAVE)
            self.assertIs(record["magnet_enabled"], False)
            self.assertIsNone(record["magnet_commanded_vector_gauss"])
            # Never commanded: no model field yet; relays unsynchronized.
            self.assertEqual(record["magnet_model_vector_gauss"], [None, None, None])
            self.assertEqual(record["magnet_model_polarity"], ["?", "?", "?"])
            # Positive-branch intercepts (no branch retained yet), in G.
            for value, expected in zip(record["magnet_calibration_background_gauss"],
                                       (1.0, -0.2, 0.3)):
                self.assertAlmostEqual(value, expected)
            self.assertEqual(record["magnet_field_note"], COILS_OFF_NOTE)
            self.assertIn("calibration estimate",
                          record["magnet_field_definitions"]
                          ["magnet_calibration_background_gauss"])

    def test_coils_on_keeps_the_manual_field(self):
        self.magnet.enable()
        self.magnet.set_vector(bx=0.2, by=0.0, bz=-0.1)
        self.log.clear()
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        self.assertEqual(self._calls(*_MAGNET_COMMANDS), [])
        self.assertEqual(self.panel.field_label.text(),
                         "Field: left as is, coils on, model (2, 0, -1) G")
        for record in (config, metadata):
            self.assertIs(record["magnet_enabled"], True)
            self.assertIsNone(record["magnet_commanded_vector_gauss"])
            for value, expected in zip(record["magnet_model_vector_gauss"], (2.0, 0.0, -1.0)):
                self.assertAlmostEqual(value, expected)
            self.assertNotIn("magnet_field_note", record)
            self.assertNotIn("magnet_calibration_background_gauss", record)

    def test_scan_data_identical_to_a_config_without_the_setting(self):
        """Leave as is == the pre-existing path (no magnet_field_mode key)."""
        base = _make_fast_config()
        for config in (base, dict(base, magnet_field_mode=FIELD_MODE_LEAVE,
                                  magnet_custom_vector_gauss=[1.0, 2.0, 3.0])):
            experiment = ODMRExperiment(self.hardware, config)
            record = experiment.setup_magnet_field()
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_LEAVE)
            self.assertIsNone(experiment.target_field_gauss())
        self.assertEqual(self._calls(*_MAGNET_COMMANDS), [])


class NoMagnetTests(ODMRMagnetFieldTestCase):
    with_magnet = False

    def test_group_disabled_with_reason_and_scan_leaves_field(self):
        self.assertFalse(self.group.isEnabled())
        self.assertIn("No magnet", self.group.toolTip())
        self.assertIn("No magnet", self.group.mode_combo.toolTip())
        self.assertEqual(self.panel.get_config()["magnet_field_mode"], FIELD_MODE_LEAVE)
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        self.assertEqual(self.panel.field_label.text(),
                         "Field: no magnet available (not controlled)")
        for record in (config, metadata):
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_LEAVE)
            self.assertIs(record["magnet_available"], False)
            self.assertIsNone(record["magnet_commanded_vector_gauss"])
            self.assertIsNone(record["magnet_model_vector_gauss"])

    def test_experiment_falls_back_without_magnet(self):
        config = _make_fast_config(magnet_field_mode=FIELD_MODE_ZERO)
        experiment = ODMRExperiment(self.hardware, config)
        self.assertEqual(experiment.magnet_field_mode(), FIELD_MODE_LEAVE)
        self.assertEqual(experiment.setup_magnet_field()["magnet_field_mode"],
                         FIELD_MODE_LEAVE)


class TooltipTests(ODMRMagnetFieldTestCase):
    def test_every_new_control_has_a_tooltip(self):
        g = self.group
        for widget in (g, g.mode_combo, g.x_spin, g.y_spin, g.z_spin,
                       self.panel.field_label):
            self.assertTrue(widget.toolTip().strip(), widget)
        self.assertIn("Field zero at diamond enables the magnet", g.mode_combo.toolTip())
        self.assertIn("Leave magnet as is keeps", g.mode_combo.toolTip())
        for axis, spin in (("X", g.x_spin), ("Y", g.y_spin), ("Z", g.z_spin)):
            self.assertIn(axis, spin.toolTip())
            self.assertIn("gauss", spin.toolTip())


class MagnetLockAndPostActionTests(ODMRMagnetFieldTestCase):
    """The lock and post-experiment action, wired as MainWindow wires them."""

    def setUp(self):
        super().setUp()
        self.magnet_window = MagnetControlWindow({"magnet": self.magnet})
        self.addCleanup(self.magnet_window.close)
        self.window.run_started.connect(
            lambda: self.magnet_window.on_experiment_started(
                "ODMR", ODMRExperiment.MAGNET_IS_SWEPT))
        self.window.run_finished.connect(self.magnet_window.on_experiment_finished)
        self.locked_during_scan = []
        self.panel.progress_bar.valueChanged.connect(
            lambda _v: self.locked_during_scan.append(
                not self.magnet_window.apply_button.isEnabled()))

    def test_static_experiment_flag_unchanged(self):
        self.assertIs(ODMRExperiment.MAGNET_IS_SWEPT, False)

    def test_locked_during_scan_then_field_zero_post_action(self):
        self.magnet_window.post_experiment_combo.setCurrentText(POST_EXPERIMENT_FIELD_ZERO)
        self.group.set_mode(FIELD_MODE_CUSTOM)
        self.group.set_custom_vector(5.0, 0.0, 0.0)
        self._run_scan()
        self.assertTrue(self.locked_during_scan)
        self.assertTrue(all(self.locked_during_scan))
        self.assertTrue(self.magnet_window.apply_button.isEnabled())
        set_vector = self._calls("set_vector")
        self.assertEqual(len(set_vector), 2)  # scan setup, then post-action
        self.assertEqual(set_vector[1][2], {"bx": 0.0, "by": 0.0, "bz": 0.0})
        self.assertTrue(self.magnet.is_enabled())

    def test_electrical_off_post_action(self):
        self.magnet_window.post_experiment_combo.setCurrentText(
            POST_EXPERIMENT_ELECTRICAL_OFF)
        self._run_scan()
        self.assertEqual(len(self._calls("set_vector")), 1)
        self.assertEqual(len(self._calls("disable")), 1)
        self.assertFalse(self.magnet.is_enabled())

    def test_leave_as_is_with_coils_off_post_action_skipped(self):
        self.group.set_mode(FIELD_MODE_LEAVE)
        self._run_scan()
        self.assertEqual(self._calls(*_MAGNET_COMMANDS), [])
        self.assertTrue(self.magnet_window.apply_button.isEnabled())


class DirectionComputationTests(unittest.TestCase):
    """framework.field_direction and the experiment's config -> X, Y, Z."""

    def test_vector_along_normalises(self):
        x, y, z = vector_along(4.0, [1.0, 1.0, 0.0])
        self.assertAlmostEqual(x, 4.0 / 2 ** 0.5)
        self.assertAlmostEqual(y, 4.0 / 2 ** 0.5)
        self.assertEqual(z, 0.0)
        # Scale of the direction doesn't matter.
        np.testing.assert_allclose(vector_along(4.0, [1.0, 1.0, 0.0]),
                                   vector_along(4.0, [7.0, 7.0, 0.0]), rtol=1e-15)
        self.assertEqual(vector_along(5.0, [0.0, 3.0, 4.0]), [0.0, 3.0, 4.0])

    def test_negative_magnitude_points_the_opposite_way(self):
        self.assertEqual(vector_along(-5.0, [3.0, 0.0, 4.0]), [-3.0, 0.0, -4.0])
        for value in vector_along(-2.0, [0.0, 1.0, 0.0]):
            self.assertNotEqual(str(value), "-0.0")

    def test_invalid_direction_or_magnitude_rejected(self):
        for direction in ([0.0, 0.0, 0.0], [1.0, float("nan"), 0.0], [1.0, 2.0], None):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                vector_along(1.0, direction)
        for magnitude in (float("inf"), float("nan"), None, True):
            with self.subTest(magnitude=magnitude), self.assertRaises(ValueError):
                vector_along(magnitude, [1.0, 0.0, 0.0])

    def test_zero_field_experiment_uses_the_same_function(self):
        import experiments.zero_field_experiment as zfe
        self.assertIs(zfe.unit_direction, unit_direction)

    def test_commanded_vector_from_config(self):
        direction = {"magnet_vector_entry": VECTOR_ENTRY_DIRECTION,
                     "magnet_field_direction": [1.0, 1.0, 0.0],
                     "magnet_field_magnitude_gauss": -4.0,
                     # Ignored with a direction entry.
                     "magnet_custom_vector_gauss": [9.0, 9.0, 9.0]}
        self.assertEqual(commanded_vector_gauss(direction, FIELD_MODE_CUSTOM),
                         tuple(vector_along(-4.0, [1.0, 1.0, 0.0])))
        self.assertEqual(commanded_vector_gauss(direction, FIELD_MODE_ZERO),
                         (0.0, 0.0, 0.0))
        self.assertIsNone(commanded_vector_gauss(direction, FIELD_MODE_LEAVE))
        # No entry key: components, as before the setting existed.
        self.assertEqual(commanded_vector_gauss(
            {"magnet_custom_vector_gauss": [1, 2, 3]}, FIELD_MODE_CUSTOM),
            (1.0, 2.0, 3.0))
        with self.assertRaises(ValueError):
            commanded_vector_gauss(dict(direction, magnet_field_direction=[0, 0, 0]),
                                   FIELD_MODE_CUSTOM)
        with self.assertRaises(ValueError):
            commanded_vector_gauss({"magnet_vector_entry": "polar"}, FIELD_MODE_CUSTOM)

    def test_format_direction(self):
        self.assertEqual(format_direction(unit_direction([1, 1, 0])), "[0.707, 0.707, 0]")
        self.assertEqual(format_direction([0.0, -0.0, -1.0]), "[0, 0, -1]")


class DirectionEntryTests(ODMRMagnetFieldTestCase):
    def setUp(self):
        super().setUp()
        self.group.set_mode(FIELD_MODE_CUSTOM)
        self.group.set_vector_entry(VECTOR_ENTRY_DIRECTION)

    def _use_direction(self, direction, magnitude):
        self.group.set_field_direction(direction)
        self.group.set_field_magnitude(magnitude)

    def test_entry_selector_enabled_only_for_custom_and_switches_page(self):
        for mode, enabled in ((FIELD_MODE_CUSTOM, True), (FIELD_MODE_ZERO, False),
                              (FIELD_MODE_LEAVE, False)):
            self.group.set_mode(mode)
            self.assertEqual(self.group.entry_combo.isEnabled(), enabled, mode)
            self.assertEqual(self.group.magnitude_spin.isEnabled(), enabled, mode)
        self.assertEqual(self.group.entry_stack.currentIndex(), 1)
        self.group.set_vector_entry(VECTOR_ENTRY_COMPONENTS)
        self.assertEqual(self.group.entry_stack.currentIndex(), 0)

    def test_presets_and_applied_components_display(self):
        presets = self.group.direction_preset_buttons
        self.assertEqual(list(presets), ["X", "Y", "Z", "<110>", "<1-10>", "<111>"])
        self.group.set_field_magnitude(4.0)
        presets["<1-10>"].click()
        self.assertEqual(self.group.raw_field_direction(), [1.0, -1.0, 0.0])
        self.assertEqual(self.group.normalised_label.text(), "n̂ = (0.707, -0.707, 0.000)")
        self.assertEqual(self.group.applied_label.text(),
                         "Applied: X 2.8284, Y -2.8284, Z 0.0000 G")
        presets["Z"].click()
        self.group.set_field_magnitude(-3.0)
        self.assertEqual(self.group.applied_label.text(),
                         "Applied: X 0.0000, Y 0.0000, Z -3.0000 G")
        config = self.panel.get_config()
        self.assertEqual(config["magnet_vector_entry"], VECTOR_ENTRY_DIRECTION)
        self.assertEqual(config["magnet_custom_vector_gauss"], [0.0, 0.0, -3.0])
        self.assertEqual(config["magnet_field_direction"], [0.0, 0.0, 1.0])
        self.assertEqual(config["magnet_field_magnitude_gauss"], -3.0)

    def test_direction_is_normalised_in_config(self):
        self._use_direction((2.0, 2.0, 0.0), 1.0)
        np.testing.assert_allclose(self.panel.get_config()["magnet_field_direction"],
                                   [2 ** -0.5, 2 ** -0.5, 0.0])

    def test_zero_direction_refused_before_anything_is_commanded(self):
        self._use_direction((0.0, 0.0, 0.0), 4.0)
        self.assertIn("Zero vector", self.group.normalised_label.text())
        self.assertIsNone(self.panel.get_config()["magnet_custom_vector_gauss"])
        self.window.toggle_odmr()
        self.critical.assert_called_once()
        self.assertEqual(self.critical.call_args.args[1], "Invalid field direction")
        self.assertFalse(self.window.odmr_running)
        self.assertEqual(self.started, [])
        self.assertEqual(self.log, [], "nothing may be commanded")

    def test_experiment_refuses_zero_direction_before_any_magnet_command(self):
        config = _make_fast_config(
            magnet_field_mode=FIELD_MODE_CUSTOM,
            magnet_vector_entry=VECTOR_ENTRY_DIRECTION,
            magnet_field_direction=[0.0, 0.0, 0.0],
            magnet_field_magnitude_gauss=4.0,
        )
        with self.assertRaises(ValueError):
            ODMRExperiment(self.hardware, config).setup_magnet_field()
        self.assertEqual(self.log, [])

    def test_out_of_range_magnitude_refused(self):
        self._use_direction((0.0, 0.0, 1.0), -_OUT_OF_RANGE_G)
        self.window.toggle_odmr()
        self.critical.assert_called_once()
        self.assertEqual(self.critical.call_args.args[1], "Magnetic field out of range")
        self.assertIn("Z-axis reachable range", self.critical.call_args.args[2])
        self.assertEqual(self.log, [])

    def test_scan_commands_once_and_records_direction_metadata(self):
        self._use_direction((1.0, 1.0, 0.0), 4.0)
        expected = vector_along(4.0, [1.0, 1.0, 0.0])
        with tempfile.TemporaryDirectory() as tmp:
            config, metadata = self._run_scan(save_dir=tmp)
        set_vector = self._calls("set_vector")
        self.assertEqual(len(set_vector), 1, "field must not change during the scan")
        self.assertEqual(set_vector[0][2], {"bx": expected[0] * 0.1 + 0.0,
                                            "by": expected[1] * 0.1 + 0.0,
                                            "bz": 0.0})
        self._assert_magnet_command_before_acquisition()
        self.assertEqual(self.panel.field_label.text(),
                         "Field: custom 4 G along [0.707, 0.707, 0] = "
                         "(2.8284, 2.8284, 0) G")
        for record in (config, metadata):
            self.assertEqual(record["magnet_field_mode"], FIELD_MODE_CUSTOM)
            self.assertEqual(record["magnet_vector_entry"], VECTOR_ENTRY_DIRECTION)
            self.assertEqual(record["magnet_field_direction"],
                             unit_direction([1.0, 1.0, 0.0]))
            self.assertEqual(record["magnet_field_magnitude_gauss"], 4.0)
            self.assertEqual(record["magnet_field_description"],
                             "4 G along [0.707, 0.707, 0]")
            self.assertEqual(record["magnet_commanded_vector_gauss"], expected)
            for value, want in zip(record["magnet_model_vector_gauss"], expected):
                self.assertAlmostEqual(value, want)
            self.assertIn("magnitude * direction",
                          record["magnet_field_definitions"]
                          ["magnet_field_magnitude_gauss"])

    def test_components_and_other_modes_record_null_direction(self):
        self.group.set_vector_entry(VECTOR_ENTRY_COMPONENTS)
        self.group.set_custom_vector(1.0, 0.0, 0.0)
        for mode, entry in ((FIELD_MODE_CUSTOM, VECTOR_ENTRY_COMPONENTS),
                            (FIELD_MODE_ZERO, None), (FIELD_MODE_LEAVE, None)):
            self.group.set_mode(mode)
            experiment = ODMRExperiment(self.hardware,
                                        _make_fast_config(**self.panel.get_config()))
            record = experiment.setup_magnet_field()
            self.assertEqual(record["magnet_vector_entry"], entry, mode)
            self.assertIsNone(record["magnet_field_direction"], mode)
            self.assertIsNone(record["magnet_field_magnitude_gauss"], mode)
            self.assertNotIn("magnet_field_description", record)

    def test_tooltips_on_new_controls(self):
        g = self.group
        widgets = [g.entry_combo, g.magnitude_spin, g.normalised_label,
                   g.applied_label, *g.direction_spins,
                   *g.direction_preset_buttons.values()]
        for widget in widgets:
            self.assertTrue(widget.toolTip().strip(), widget)
        self.assertIn("gauss", g.magnitude_spin.toolTip())
        self.assertIn("Negative", g.magnitude_spin.toolTip())
        self.assertIn("normalised", g.direction_spins[0].toolTip())


class FoldedSummaryTests(ODMRMagnetFieldTestCase):
    """The section is folded by default; its summary states the field."""

    def test_folded_by_default_with_explicit_field(self):
        self.assertFalse(self.group.is_expanded())
        self.assertFalse(self.group.summary_label.isHidden())
        self.assertEqual(self.group.summary(), "Field: calibrated zero (0, 0, 0) G")

    def test_summary_follows_every_mode(self):
        g = self.group
        g.set_mode(FIELD_MODE_CUSTOM)
        g.set_vector_entry(VECTOR_ENTRY_DIRECTION)
        g.direction_preset_buttons["<110>"].click()
        g.set_field_magnitude(4.0)
        self.assertEqual(
            g.summary(),
            "Field: 4 G along [0.707, 0.707, 0] = (2.8284, 2.8284, 0) G")
        g.set_field_direction((0.0, 0.0, 0.0))
        self.assertIn("invalid direction", g.summary())
        g.set_vector_entry(VECTOR_ENTRY_COMPONENTS)
        g.set_custom_vector(2.0, -1.0, 3.0)
        self.assertEqual(g.summary(), "Field: custom (2, -1, 3) G")
        g.set_mode(FIELD_MODE_LEAVE)
        self.assertEqual(g.summary(), "Field: magnet off (lab background)")

    def test_leave_as_is_summary_tracks_the_magnet(self):
        self.group.set_mode(FIELD_MODE_LEAVE)
        self.magnet.enable()
        self.magnet.set_vector(bx=0.2, by=0.0, bz=-0.1)
        self.group.refresh_summary()  # the 1 s timer does this in the app
        self.assertEqual(self.group.summary(),
                         "Field: left as is, magnet on, model (2, 0, -1) G")
        self.assertTrue(self.group._summary_timer.isActive())

    def test_folding_keeps_the_field_settings(self):
        g = self.group
        g.set_mode(FIELD_MODE_CUSTOM)
        g.set_custom_vector(1.5, 0.0, -2.0)
        before = self.panel.get_config()
        g.set_expanded(True)
        g.set_expanded(False)
        self.assertEqual(self.panel.get_config(), before)


class DirectionMatchesComponentsTests(ODMRMagnetFieldTestCase):
    """Both entries command the identical field through the same path."""

    def _set_vector_args(self, **entry):
        magnet = _make_magnet()
        log = []
        _record_calls(self, magnet, "set_vector", log)
        _record_calls(self, magnet, "enable", log)
        hardware = dict(self.hardware, magnet=magnet)
        config = _make_fast_config(magnet_field_mode=FIELD_MODE_CUSTOM, **entry)
        record = ODMRExperiment(hardware, config).setup_magnet_field()
        model = magnet.get_vector()
        return log, record, model, magnet.get_polarities()

    def test_experiment_level_identical_for_unrounded_components(self):
        for direction, magnitude in (([1.0, 1.0, 0.0], 4.0), ([1.0, 1.0, 1.0], -3.0),
                                     ([1.0, -1.0, 0.0], 2.5), ([0.0, 0.0, 1.0], -1.0)):
            with self.subTest(direction=direction, magnitude=magnitude):
                by_direction = self._set_vector_args(
                    magnet_vector_entry=VECTOR_ENTRY_DIRECTION,
                    magnet_field_direction=direction,
                    magnet_field_magnitude_gauss=magnitude)
                by_components = self._set_vector_args(
                    magnet_custom_vector_gauss=vector_along(magnitude, direction))
                self.assertEqual(by_direction[0], by_components[0])
                self.assertEqual(by_direction[1]["magnet_commanded_vector_gauss"],
                                 by_components[1]["magnet_commanded_vector_gauss"])
                self.assertEqual(by_direction[1]["magnet_relay_flip_axes"],
                                 by_components[1]["magnet_relay_flip_axes"])
                self.assertEqual(by_direction[2], by_components[2])
                self.assertEqual(by_direction[3], by_components[3])

    def test_gui_scans_identical(self):
        """The same window, same X, Y, Z, entered both ways."""
        self.group.set_mode(FIELD_MODE_CUSTOM)
        self.group.set_vector_entry(VECTOR_ENTRY_COMPONENTS)
        self.group.set_custom_vector(-3.0, -4.0, 0.0)
        self._run_scan()
        self.group.set_vector_entry(VECTOR_ENTRY_DIRECTION)
        self.group.set_field_direction((3.0, 4.0, 0.0))
        self.group.set_field_magnitude(-5.0)
        self.magnet.disable()  # start from the same state: off
        self._run_scan()
        set_vector = self._calls("set_vector")
        self.assertEqual(len(set_vector), 2)
        self.assertEqual(set_vector[0][1:], set_vector[1][1:])
        self.assertEqual(len(self._calls("enable")), 2)


if __name__ == "__main__":
    unittest.main()
