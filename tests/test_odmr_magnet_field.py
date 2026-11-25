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

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.odmr_experiment import (  # noqa: E402
    COILS_OFF_NOTE,
    FIELD_MODE_CUSTOM,
    FIELD_MODE_LEAVE,
    FIELD_MODE_ZERO,
    MagnetFieldRangeError,
    ODMRExperiment,
)
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


if __name__ == "__main__":
    unittest.main()
