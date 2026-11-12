"""Hardware-free tests for preliminary ODMR manual fit initialization."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication, QDialog

from gui.odmr_window import ODMRWindow
from gui.panels.odmr_panel import InitialFitParametersDialog


_APP = QApplication.instance() or QApplication(sys.argv)


class InitialFitDialogTests(unittest.TestCase):
    def test_fields_follow_selected_fit_model(self):
        single = InitialFitParametersDialog("Single Lorentzian")
        self.assertEqual(set(single.fields), {"f0", "contrast", "fwhm"})
        double = InitialFitParametersDialog("Double Lorentzian")
        self.assertEqual(set(double.fields), {"f01", "f02", "contrast1", "contrast2", "fwhm1", "fwhm2"})

    def test_required_and_optional_validation(self):
        dialog = InitialFitParametersDialog("Single Lorentzian")
        with self.assertRaisesRegex(ValueError, "required"):
            dialog.parsed_values()
        dialog.fields["f0"].setText("2.87")
        self.assertEqual(dialog.parsed_values(), {"f0": 2.87, "contrast": None, "fwhm": None})
        dialog.fields["contrast"].setText("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            dialog.parsed_values()
        dialog.fields["contrast"].setText("0")
        with self.assertRaisesRegex(ValueError, "positive"):
            dialog.parsed_values()

    def test_double_rejects_equal_locations_and_allows_blank_optional_fields(self):
        dialog = InitialFitParametersDialog("Double Lorentzian")
        dialog.fields["f01"].setText("2.87")
        dialog.fields["f02"].setText("2.87")
        with self.assertRaisesRegex(ValueError, "different"):
            dialog.parsed_values()
        dialog.fields["f02"].setText("2.88")
        self.assertIsNone(dialog.parsed_values()["fwhm2"])


class PreliminaryFitWindowTests(unittest.TestCase):
    @staticmethod
    def _window(fit_type="Single Lorentzian"):
        fake = SimpleNamespace(
            latest_x=np.array([2.86, 2.87, 2.88]), latest_y=np.array([100., 95., 100.]),
            initial_fit_guesses={"Single Lorentzian": None, "Double Lorentzian": None},
            panel=SimpleNamespace(
                get_fit_type=lambda: fit_type,
                update_fit_results=lambda _fit: None,
                plot=SimpleNamespace(plot=lambda *_a, **_k: None),
            ),
            refresh_plot=lambda: None,
        )
        return fake

    def test_manual_values_reach_optimizer_as_initial_guess_with_mhz_conversion(self):
        fake = self._window()
        fake.initial_fit_guesses["Single Lorentzian"] = {"f0": 2.87, "contrast": 2.0, "fwhm": 4.0}
        expected = [None, 2.0, 2.87, .004]
        with patch("gui.odmr_window.fit_single_lorentzian", return_value={"fit_y": [1, 2, 3]}) as fitter:
            ODMRWindow.fit_esr(fake)
        fitter.assert_called_once_with(fake.latest_x, fake.latest_y, initial_guess=expected)

    def test_manual_location_outside_measured_range_fails_before_fitting(self):
        fake = self._window()
        fake.initial_fit_guesses["Single Lorentzian"] = {"f0": 2.9, "contrast": None, "fwhm": None}
        with patch("gui.odmr_window.fit_single_lorentzian") as fitter, patch("gui.odmr_window.QMessageBox.critical") as error:
            ODMRWindow.fit_esr(fake)
        fitter.assert_not_called()
        error.assert_called_once()

    def test_fit_failure_is_reported_and_automatic_fit_keeps_legacy_call(self):
        fake = self._window()
        with patch("gui.odmr_window.fit_single_lorentzian", side_effect=RuntimeError("optimizer failed")) as fitter, patch("gui.odmr_window.QMessageBox.critical") as error:
            ODMRWindow.fit_esr(fake)
        fitter.assert_called_once_with(fake.latest_x, fake.latest_y)
        error.assert_called_once()
        self.assertIn("optimizer failed", error.call_args.args[2])

    def test_cancelled_dialog_preserves_stored_guess(self):
        fake = self._window()
        previous = {"f0": 2.87, "contrast": None, "fwhm": None}
        fake.initial_fit_guesses["Single Lorentzian"] = previous
        with patch("gui.odmr_window.InitialFitParametersDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            ODMRWindow.supply_initial_fit_parameters(fake)
        self.assertIs(fake.initial_fit_guesses["Single Lorentzian"], previous)

    def test_models_keep_separate_guesses(self):
        fake = self._window()
        fake.initial_fit_guesses["Single Lorentzian"] = {"f0": 2.87, "contrast": None, "fwhm": None}
        fake.panel.get_fit_type = lambda: "Double Lorentzian"
        with patch("gui.odmr_window.InitialFitParametersDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.parsed_values.return_value = {"f01": 2.87, "f02": 2.88, "contrast1": None, "contrast2": None, "fwhm1": None, "fwhm2": None}
            ODMRWindow.supply_initial_fit_parameters(fake)
        self.assertIsNotNone(fake.initial_fit_guesses["Single Lorentzian"])
        self.assertEqual(fake.initial_fit_guesses["Double Lorentzian"]["f01"], 2.87)


if __name__ == "__main__":
    unittest.main()
