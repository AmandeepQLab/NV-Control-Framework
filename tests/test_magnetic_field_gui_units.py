"""Hardware-free checks of the Gauss GUI / mT magnet boundary."""

from copy import deepcopy
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PyQt6.QtWidgets import QApplication, QLabel

# Keep the application alive before importing pyqtgraph, as in the existing
# ZeroFieldWindow GUI tests. Each class also shares a single Qt window/widget.
_APP = QApplication.instance() or QApplication(sys.argv)

from gui.magnet_window import MagnetControlWindow
from gui.widgets.zero_field_widget import ZeroFieldWidget
from hardware.magnet.magnet import Magnet, NEGATIVE, POSITIVE
from hardware.power_supply.sim_power_supply import SimPowerSupply


def _make_magnet():
    # Synthetic calibration stays in mT/A and mT, including a negative offset.
    config = {
        "X": {"name": "X", "max_current": 5.0,
              "magnetic_field_calibration": {
                  "positive": {"magnetic_field_ratio": 0.8, "magnetic_field_offset": 0.1},
                  "negative": {"magnetic_field_ratio": 0.81, "magnetic_field_offset": 0.11},
              }},
        "Y": {"name": "Y", "max_current": 5.0,
              "magnetic_field_calibration": {
                  "positive": {"magnetic_field_ratio": 0.7, "magnetic_field_offset": -0.02},
                  "negative": {"magnetic_field_ratio": 0.71, "magnetic_field_offset": -0.01},
              }},
        "Z": {"name": "Z", "max_current": 5.0,
              "magnetic_field_calibration": {
                  "positive": {"magnetic_field_ratio": 0.9, "magnetic_field_offset": 0.03},
                  "negative": {"magnetic_field_ratio": 0.91, "magnetic_field_offset": 0.04},
              }},
    }
    for name, channel in (("X", 6), ("Y", 7), ("Z", 3)):
        config[name]["flip_direction"] = {
            "available": True,
            "negative_state": True,
            "switch": {"switchChannel": channel, "switchChannelName": f"flipMF_{name}"},
            "discharge_delay_s": 0.0,
            "settle_delay_s": 0.0,
        }
    return Magnet(
        config,
        {name: SimPowerSupply({"name": name}) for name in ("X", "Y", "Z")},
        pulse_streamer=Mock(),
        scpi_write_delay_s=0.0,
    )


class MagnetGuiUnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = MagnetControlWindow({"magnet": _make_magnet()})

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def setUp(self):
        self.magnet = _make_magnet()
        self.original_config = deepcopy(self.magnet.config)
        self.window.hardware = {"magnet": self.magnet}
        self.window.magnet = self.magnet
        self.window.axis_combo.setCurrentText("X")
        self.window.field_spin.setValue(0.0)
        self.magnet.enable()
        self.window.refresh()

    def tearDown(self):
        self.assertEqual(self.magnet.config, self.original_config)

    def test_input_unit_and_equivalent_physical_range(self):
        spin = self.window.field_spin
        self.assertEqual(spin.suffix(), " G")
        self.assertEqual((spin.minimum(), spin.maximum()), (-1000.0, 1000.0))
        self.assertEqual(spin.singleStep(), 1.0)
        self.assertEqual(spin.decimals(), 3)

    def test_apply_gauss_preserves_backend_current_calculation_for_each_axis(self):
        for name in ("X", "Y", "Z"):
            with self.subTest(axis=name):
                self.window.axis_combo.setCurrentText(name)
                axis = getattr(self.magnet, name.lower())
                self.window.field_spin.setValue(5.0)
                with patch.object(axis, "set_field", wraps=axis.set_field) as set_field:
                    self.window.apply_button.click()

                set_field.assert_called_once_with(0.5, keep_output_enabled=True)
                self.assertAlmostEqual(axis.get_field(), 0.5)
                calibration = self.original_config[name]["magnetic_field_calibration"]["positive"]
                expected_current = (
                    0.5 - calibration["magnetic_field_offset"]
                ) / calibration["magnetic_field_ratio"]
                self.assertAlmostEqual(axis.power_supply.get_current(), expected_current)
                self.assertEqual(self.window.current_field_label.text(), "5.000 G")
                self.assertEqual(self.window.status_label.text(), f"{name}-axis commanded to 5.000 G")

    def test_negative_input_preserves_sign_and_automatically_selects_axis_polarity(self):
        self.magnet.set_vector(bx=0.5, by=0.7, bz=1.2)
        for name in ("X", "Y", "Z"):
            with self.subTest(axis=name):
                self.window.axis_combo.setCurrentText(name)
                axis = getattr(self.magnet, name.lower())
                other_axes = {
                    key: (other.get_field(), other.get_current(), other.get_polarity())
                    for key, other in (("X", self.magnet.x), ("Y", self.magnet.y), ("Z", self.magnet.z))
                    if key != name
                }
                self.window.field_spin.setValue(-5.0)
                with patch.object(axis, "set_field", wraps=axis.set_field) as set_field:
                    self.window.apply_button.click()

                set_field.assert_called_once_with(-0.5, keep_output_enabled=True)
                self.assertEqual(axis.get_polarity(), NEGATIVE)
                self.assertAlmostEqual(axis.power_supply.get_current(), abs(-0.5 - axis.get_offset()) / axis.get_ratio())
                self.assertEqual(self.window.current_field_label.text(), "-5.000 G")
                self.assertEqual(self.window.status_label.text(), f"{name}-axis commanded to -5.000 G")
                for key, expected in other_axes.items():
                    other = getattr(self.magnet, key.lower())
                    self.assertEqual((other.get_field(), other.get_current(), other.get_polarity()), expected)

    def test_positive_field_below_offset_shows_negative_coil_polarity(self):
        self.window.field_spin.setValue(0.5)  # 0.05 mT target is below X's 0.1 mT offset.
        self.window.apply_button.click()
        self.assertEqual(self.window.current_field_label.text(), "0.500 G")
        self.assertEqual(self.window.direction_label.text(), "- (Negative)")
        self.assertEqual(self.magnet.x.get_polarity(), NEGATIVE)

    def test_manual_intercept_keeps_output_enabled_for_subsequent_field_commands(self):
        self.window.field_spin.setValue(1.0)  # X's 0.1 mT background needs 0 A.
        self.window.apply_button.click()
        self.assertEqual(self.magnet.x.get_current(), 0.0)
        self.assertTrue(self.magnet.x.power_supply.output_enabled)
        self.window.field_spin.setValue(-5.0)
        self.window.apply_button.click()
        self.assertTrue(self.magnet.x.power_supply.output_enabled)
        self.assertGreater(self.magnet.x.get_current(), 0.0)
        self.assertEqual(self.window.current_field_label.text(), "-5.000 G")

    def test_failed_write_displays_unknown_commanded_state_without_crashing(self):
        self.window.field_spin.setValue(5.0)
        with patch.object(self.magnet.x.power_supply, "set_current", side_effect=RuntimeError("Write failed")):
            self.window.apply_button.click()
        self.assertEqual(self.window.status_label.text(), "Write failed")
        self.assertEqual(self.window.current_field_label.text(), "Unknown G")
        self.assertEqual(self.window.current_current_label.text(), "Unknown A")
        self.assertEqual(self.window.bx_label.text(), "Unknown G")

    def test_read_and_repeated_refresh_show_signed_gauss_without_mutating_backend(self):
        self.magnet.set_vector(bx=0.5, by=-0.7, bz=1.2)
        labels = (self.window.bx_label, self.window.by_label, self.window.bz_label)
        original_vector = self.magnet.get_vector()
        self.magnet.pulse_streamer.reset_mock()
        self.window.read_button.click()
        for _ in range(3):
            self.window.refresh()
            self.assertEqual(tuple(label.text() for label in labels), ("5.000 G", "-7.000 G", "12.000 G"))
            self.assertEqual(self.magnet.get_vector(), original_vector)
        self.assertEqual(self.magnet.pulse_streamer.mock_calls, [])
        self.assertIn("not measured", self.window.status_label.text())

    def test_polarity_status_is_read_only_and_independent_for_each_axis(self):
        self.magnet.set_vector(bx=0.5, by=-0.7, bz=1.2)
        self.window.refresh()
        self.assertFalse(hasattr(self.window, "polarity_button"))
        self.assertFalse(hasattr(self.window, "toggle_polarity"))
        for name, text in (("x", "+ (Positive)"), ("y", "- (Negative)"), ("z", "+ (Positive)")):
            label = self.window.polarity_labels[name]
            self.assertIsInstance(label, QLabel)
            self.assertEqual(label.text(), f"{name.upper()} coil polarity: {text}")
            self.window.axis_combo.setCurrentText(name.upper())
            self.assertEqual(self.window.direction_label.text(), text)

    def test_configuration_tab_displays_gauss_without_changing_calibration(self):
        for name, ratio_text, offset_text, channel in (
            ("X", "8.000 G/A", "1.000 G", 6),
            ("Y", "7.000 G/A", "-0.200 G", 7),
            ("Z", "9.000 G/A", "0.300 G", 3),
        ):
            with self.subTest(axis=name):
                self.window.axis_combo.setCurrentText(name)
                self.window.refresh()
                self.assertEqual(self.window.cfg_ratio_label.text(), ratio_text)
                self.assertEqual(self.window.cfg_offset_label.text(), offset_text)
                self.assertEqual(self.window.cfg_axis_label.text(), name)
                self.assertEqual(self.window.flip_channel_label.text(), str(channel))
                self.assertEqual(self.window.flip_available_label.text(), "Yes")
                axis = getattr(self.magnet, name.lower())
                positive = self.original_config[name]["magnetic_field_calibration"]["positive"]
                self.assertEqual(axis.get_ratio(), positive["magnetic_field_ratio"])
                self.assertEqual(axis.get_offset(), positive["magnetic_field_offset"])

    def test_zero_button_commands_calibrated_zero_gauss_with_compensation_currents(self):
        self.magnet.set_vector(bx=-0.5, by=-0.7, bz=-1.2)
        self.window.field_spin.setValue(5.0)
        with patch.object(self.magnet, "set_vector", wraps=self.magnet.set_vector) as set_vector:
            self.window.zero_button.click()
        set_vector.assert_called_once_with(bx=0.0, by=0.0, bz=0.0)
        self.assertEqual(self.window.field_spin.value(), 0.0)
        self.assertEqual(self.window.current_field_label.text(), "0.000 G")
        for label in (self.window.bx_label, self.window.by_label, self.window.bz_label):
            value, unit = label.text().split()
            self.assertEqual(float(value), 0.0)
            self.assertEqual(unit, "G")
        self.assertEqual(self.magnet.get_vector(), {"x": 0.0, "y": 0.0, "z": 0.0})
        self.assertEqual(self.magnet.get_polarities(), {"x": NEGATIVE, "y": POSITIVE, "z": NEGATIVE})
        for axis in (self.magnet.x, self.magnet.y, self.magnet.z):
            self.assertAlmostEqual(axis.power_supply.get_current(), abs(axis.get_offset()) / axis.get_ratio())
            self.assertGreater(axis.power_supply.get_current(), 0.0)
            self.assertTrue(axis.power_supply.output_enabled)

    def test_power_off_deenergizes_and_shows_background_instead_of_magnetic_zero(self):
        self.magnet.set_vector(bx=0.0, by=0.0, bz=0.0)
        self.window.power_button.click()
        self.assertFalse(self.magnet.is_enabled())
        self.assertEqual(self.window.current_field_label.text(), "1.100 G")
        for axis in (self.magnet.x, self.magnet.y, self.magnet.z):
            self.assertEqual(axis.power_supply.get_current(), 0.0)
            self.assertFalse(axis.power_supply.output_enabled)
            self.assertEqual(axis.get_field(), axis.get_offset())
        self.assertFalse(self.window.zero_button.isEnabled())
        with patch.object(self.magnet, "set_vector") as set_vector:
            self.window.zero_field()
        set_vector.assert_not_called()

    def test_refresh_disables_magnetic_zero_after_external_power_change(self):
        self.assertTrue(self.window.zero_button.isEnabled())
        self.magnet.disable()
        self.window.refresh()
        self.assertFalse(self.window.zero_button.isEnabled())
        self.magnet.enable()
        self.window.refresh()
        self.assertTrue(self.window.zero_button.isEnabled())


class ZeroFieldGuiUnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.widget = ZeroFieldWidget()

    @classmethod
    def tearDownClass(cls):
        cls.widget.close()

    def test_existing_gauss_inputs_plot_and_readout_are_not_converted_again(self):
        widget = self.widget
        widget.field_start_spin.setValue(-5.0)
        widget.field_stop_spin.setValue(5.0)
        config = widget.get_config()
        self.assertEqual(config["field_start"], -5.0)
        self.assertEqual(config["field_stop"], 5.0)
        labels = {label.text() for label in widget.findChildren(QLabel)}
        self.assertIn("Start Field (G):", labels)
        self.assertIn("Stop Field (G):", labels)

        widget.reset_scan(3)
        self.assertEqual(widget.current_field_label.text(), "Current magnetic field: -- G")
        fields = [-5.0, 0.0, 5.0]
        signals = [10.0, 20.0, 15.0]
        widget.update_live_data(fields, signals, 3, 3)
        plotted_fields, plotted_signals = widget.curve.getData()
        np.testing.assert_array_equal(plotted_fields, fields)
        np.testing.assert_array_equal(plotted_signals, signals)
        self.assertEqual(widget.plot.getAxis("bottom").labelText, "Magnetic Field (G)")
        self.assertEqual(widget.current_field_label.text(), "Current magnetic field: 5 G")


if __name__ == "__main__":
    unittest.main()
