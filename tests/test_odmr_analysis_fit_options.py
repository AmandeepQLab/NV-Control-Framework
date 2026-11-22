"""Hardware-free tests for the ODMR analysis window's fit type and initial parameters."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import analysis.odmr_pixel_fit as pixel_fit_module
import gui.odmr_analysis_window as analysis_window_module
import gui.odmr_window as odmr_window_module
import gui.panels.odmr_panel as odmr_panel_module
from analysis.odmr_fit import (
    two_lorentzian_guess_with_overrides,
    two_lorentzian_odmr_model,
)
from framework.image_cube import ImageCube
from gui.odmr_analysis_window import ODMRAnalysisState, ODMRAnalysisWindow
from gui.odmr_analysis_worker import ODMRPixelFitWorker
from gui.panels.odmr_panel import ODMRPanel
from gui.widgets.initial_fit_parameters_dialog import (
    CLEAR_BUTTON_TEXT,
    DOUBLE_LORENTZIAN,
    SINGLE_LORENTZIAN,
    InitialFitParametersDialog,
)


_APP = QApplication.instance() or QApplication(sys.argv)

F_MINUS = 2.850e9
F_PLUS = 2.890e9
CENTRE_TOLERANCE_HZ = 0.5e6
DOUBLE_GUESS = {
    "f01": 2.85, "f02": 2.89,
    "contrast1": None, "fwhm1": None, "contrast2": None, "fwhm2": None,
}


def _dense_frequencies():
    """ODMR "dense points near dips" sampling: 20 coarse + 30 per dip."""
    coarse = np.linspace(2.77e9, 2.97e9, 20)
    dense = [np.linspace(centre - 10e6, centre + 10e6, 30) for centre in (F_MINUS, F_PLUS)]
    return np.unique(np.concatenate([coarse, *dense]))


def _write_statistics(path, frequency_hz, spectrum):
    """A (2 averages, 2 statistics, n_freq, 2, 2) statistics cube.

    Pixels carry the spectrum with small offsets so the final mean is
    deterministic and the per-pixel fits have a real spectrum to fit.
    """
    n = frequency_hz.size
    spatial = np.empty((n, 2, 2), dtype=np.float64)
    for index, offset in enumerate((0.0, 0.3, -0.2, 0.1)):
        spatial[:, index // 2, index % 2] = spectrum + offset
    data = np.empty((2, 2, n, 2, 2), dtype=np.float64)
    data[:, 0] = spatial
    data[:, 1] = 0.2
    ImageCube(
        data=data,
        axes={
            "axis_order": ["average", "statistic", "frequency", "y", "x"],
            "average": [0, 1],
            "statistic": ["normalized_mean", "normalized_sd"],
            "frequency": frequency_hz.tolist(),
            "y": [0, 1],
            "x": [0, 1],
        },
        metadata={
            "configured_averages": 2, "completed_averages": 2, "n_averages": 2,
            "configured_repeats": 5, "n_repeats_per_average": [5, 5],
            "experiment_complete": True, "interrupted": False,
        },
        experiment_type="ODMR",
        scan_axis_name="frequency",
        scan_axis_unit="Hz",
        scan_axis_values=frequency_hz,
    ).save(path)


def _two_dip_spectrum(frequency_hz):
    rng = np.random.default_rng(1)
    clean = two_lorentzian_odmr_model(
        frequency_hz, 100.0, 2.8, F_MINUS, 8e6, 1.6, F_PLUS, 8e6
    )
    return clean + rng.normal(0.0, 0.05, frequency_hz.size)


class _AnalysisWindowCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.window = ODMRAnalysisWindow()
        self.addCleanup(self.window.deleteLater)

    def _load_two_dip(self):
        frequency_hz = _dense_frequencies()
        path = Path(self.temp_dir.name) / "two_dip.h5"
        _write_statistics(path, frequency_hz, _two_dip_spectrum(frequency_hz))
        self.assertTrue(self.window.load_file(path))
        return frequency_hz

    def _select(self, fit_type):
        self.window.panel.fit_type_combo.setCurrentText(fit_type)


class FitTypeSelectorTests(_AnalysisWindowCase):
    def test_selector_has_odmr_window_labels_and_order_and_defaults_to_double(self):
        odmr_panel = ODMRPanel()
        self.addCleanup(odmr_panel.deleteLater)
        odmr_items = [
            odmr_panel.fit_type_combo.itemText(i)
            for i in range(odmr_panel.fit_type_combo.count())
        ]
        combo = self.window.panel.fit_type_combo
        items = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(items, odmr_items)
        self.assertEqual(items, [SINGLE_LORENTZIAN, DOUBLE_LORENTZIAN])
        self.assertEqual(self.window.panel.get_fit_type(), DOUBLE_LORENTZIAN)
        self.assertEqual(
            self.window.panel.initial_fit_parameters_button.text(),
            "Supply Initial Fit Parameters",
        )

    def test_single_lorentzian_fit_runs_on_global_spectrum(self):
        frequency_hz = np.linspace(2.82e9, 2.92e9, 61)
        spectrum = 100.0 - 2.0 / (1 + ((frequency_hz - 2.87e9) / 4e6) ** 2)
        path = Path(self.temp_dir.name) / "single.h5"
        _write_statistics(path, frequency_hz, spectrum)
        self.assertTrue(self.window.load_file(path))
        self._select(SINGLE_LORENTZIAN)

        fit = self.window.fit_global_spectrum()

        self.assertAlmostEqual(fit["f0"], 2.87, delta=1e-4)
        self.assertIs(self.window.single_fit_result, fit)
        self.assertIsNone(self.window.global_fit_result)
        # Pixel fitting needs a two-Lorentzian global fit.
        self.assertFalse(self.window.panel.fit_pixels_button.isEnabled())
        self.assertIn("Double Lorentzian", self.window.panel.status_label.text())
        text = self.window.panel.fit_info_label.text()
        self.assertIn("Fit type: Single Lorentzian", text)
        self.assertIn("Initial parameters: automatic", text)


class SuppliedParameterFitTests(_AnalysisWindowCase):
    def test_double_with_supplied_parameters_recovers_both_dips(self):
        self._load_two_dip()
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)

        result = self.window.fit_global_spectrum()

        self.assertTrue(result.success)
        self.assertAlmostEqual(result.frequency_minus_hz, F_MINUS, delta=CENTRE_TOLERANCE_HZ)
        self.assertAlmostEqual(result.frequency_plus_hz, F_PLUS, delta=CENTRE_TOLERANCE_HZ)
        self.assertEqual(self.window.state, ODMRAnalysisState.GLOBAL_FIT_AVAILABLE)

    def test_automatic_guess_misses_second_dip_but_supplied_parameters_find_it(self):
        self._load_two_dip()

        automatic = self.window.fit_global_spectrum()
        # Dense sampling puts both automatic centres on the deeper dip; the
        # optimiser converges there and reports success.
        self.assertTrue(automatic.success)
        self.assertGreater(abs(automatic.frequency_plus_hz - F_PLUS), 20e6)

        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        supplied = self.window.fit_global_spectrum()
        self.assertAlmostEqual(supplied.frequency_minus_hz, F_MINUS, delta=CENTRE_TOLERANCE_HZ)
        self.assertAlmostEqual(supplied.frequency_plus_hz, F_PLUS, delta=CENTRE_TOLERANCE_HZ)
        self.assertLess(supplied.rmse, automatic.rmse)

    def test_supplied_values_reach_fitter_in_hz_and_ordered(self):
        frequency_hz = self._load_two_dip()
        values = {"f01": 2.89, "f02": 2.85, "contrast1": 1.5, "fwhm1": 6.0,
                  "contrast2": 2.5, "fwhm2": None}
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, values)
        with patch.object(
            analysis_window_module, "fit_global_odmr_spectrum",
            wraps=analysis_window_module.fit_global_odmr_spectrum,
        ) as fitter:
            self.window.fit_global_spectrum()
        guess = fitter.call_args.kwargs["initial_guess"]
        # Supplied in reverse order: the dip groups are reordered by centre.
        self.assertAlmostEqual(guess.frequency_minus_hz, 2.85e9)
        self.assertAlmostEqual(guess.amplitude_minus, 2.5)
        self.assertAlmostEqual(guess.frequency_plus_hz, 2.89e9)
        self.assertAlmostEqual(guess.amplitude_plus, 1.5)
        self.assertAlmostEqual(guess.linewidth_plus_hz, 6.0e6)
        automatic = two_lorentzian_guess_with_overrides(
            frequency_hz, self.window.global_spectrum, [None] * 7
        )
        # Blank FWHM and baseline fall back to the automatic values.
        self.assertAlmostEqual(guess.linewidth_minus_hz, automatic.linewidth_minus_hz)
        self.assertAlmostEqual(guess.baseline, automatic.baseline)

    def test_fit_records_type_and_supplied_values(self):
        self._load_two_dip()
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        self.window.fit_global_spectrum()
        text = self.window.panel.fit_info_label.text()
        self.assertIn("Fit type: Double Lorentzian", text)
        self.assertIn("Initial parameters: supplied (f01=2.85 GHz, f02=2.89 GHz", text)
        self.assertEqual(self.window.global_fit_settings["fit_type"], DOUBLE_LORENTZIAN)
        self.assertEqual(self.window.global_fit_settings["initial_parameters"], DOUBLE_GUESS)
        self.assertIn("supplied", self.window.panel.status_label.text())


class SharedDialogTests(_AnalysisWindowCase):
    def test_dialog_is_the_same_class_in_both_windows(self):
        self.assertIs(odmr_window_module.InitialFitParametersDialog, InitialFitParametersDialog)
        self.assertIs(analysis_window_module.InitialFitParametersDialog, InitialFitParametersDialog)
        self.assertIs(odmr_panel_module.InitialFitParametersDialog, InitialFitParametersDialog)

    def test_odmr_window_dialog_has_no_clear_button(self):
        self.assertIsNone(InitialFitParametersDialog(SINGLE_LORENTZIAN).clear_button)

    def test_analysis_dialog_is_prefilled_and_stores_values(self):
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        with patch.object(analysis_window_module, "InitialFitParametersDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.cleared = False
            dialog.return_value.parsed_values.return_value = dict(DOUBLE_GUESS, f01=2.86)
            self.window.supply_initial_fit_parameters()
        args, kwargs = dialog.call_args
        self.assertEqual(args[0], DOUBLE_LORENTZIAN)
        self.assertEqual(args[1], DOUBLE_GUESS)
        self.assertTrue(kwargs["allow_clear"])
        self.assertEqual(self.window.initial_fit_guesses[DOUBLE_LORENTZIAN]["f01"], 2.86)

    def test_cancel_keeps_values(self):
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        with patch.object(analysis_window_module, "InitialFitParametersDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.supply_initial_fit_parameters()
        self.assertEqual(self.window.initial_fit_guesses[DOUBLE_LORENTZIAN], DOUBLE_GUESS)

    def test_values_survive_loading_another_file(self):
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        self._load_two_dip()
        self._load_two_dip()
        self.assertEqual(self.window.initial_fit_guesses[DOUBLE_LORENTZIAN], DOUBLE_GUESS)


class ClearParametersTests(_AnalysisWindowCase):
    def test_clear_button_accepts_without_validation(self):
        dialog = InitialFitParametersDialog(DOUBLE_LORENTZIAN, allow_clear=True)
        self.assertEqual(dialog.clear_button.text(), CLEAR_BUTTON_TEXT)
        dialog.clear_button.click()  # required fields are blank: no validation
        self.assertTrue(dialog.cleared)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_clearing_restores_automatic_guess_behaviour(self):
        self._load_two_dip()
        automatic = self.window.fit_global_spectrum()

        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        with patch.object(analysis_window_module, "InitialFitParametersDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.cleared = True
            self.window.supply_initial_fit_parameters()
        self.assertIsNone(self.window.initial_fit_guesses[DOUBLE_LORENTZIAN])

        with patch.object(
            analysis_window_module, "fit_global_odmr_spectrum",
            wraps=analysis_window_module.fit_global_odmr_spectrum,
        ) as fitter:
            cleared = self.window.fit_global_spectrum()
        self.assertEqual(fitter.call_args.kwargs, {})
        np.testing.assert_allclose(cleared.params, automatic.params)
        self.assertIn("Initial parameters: automatic", self.window.panel.fit_info_label.text())


class MismatchTests(_AnalysisWindowCase):
    def test_values_for_other_fit_type_are_reported_and_not_used(self):
        self._load_two_dip()
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        self._select(SINGLE_LORENTZIAN)

        label = self.window.panel.initial_parameters_label.text()
        self.assertIn("Initial parameters (Single Lorentzian): automatic", label)
        self.assertIn("Values supplied for Double Lorentzian are not used with Single Lorentzian", label)

        with patch.object(
            analysis_window_module, "fit_single_lorentzian",
            wraps=analysis_window_module.fit_single_lorentzian,
        ) as fitter:
            self.window.fit_global_spectrum()
        self.assertNotIn("initial_guess", fitter.call_args.kwargs)

        self._select(DOUBLE_LORENTZIAN)
        self.assertIn(
            "Initial parameters (Double Lorentzian): supplied",
            self.window.panel.initial_parameters_label.text(),
        )


class FailureReportingTests(_AnalysisWindowCase):
    def test_unsuccessful_double_fit_is_reported(self):
        self._load_two_dip()
        failed = SimpleNamespace(success=False, status="optimizer_failed",
                                 message="Maximum number of function evaluations exceeded")
        with patch.object(analysis_window_module, "fit_global_odmr_spectrum", return_value=failed), \
                patch.object(QMessageBox, "warning") as warning:
            self.window.fit_global_spectrum()
        warning.assert_called_once()
        self.assertIn("Maximum number", warning.call_args.args[2])
        self.assertIn("Global fit failed", self.window.panel.status_label.text())
        self.assertIn("Fit type: Double Lorentzian", self.window.panel.fit_info_label.text())
        self.assertFalse(self.window.panel.fit_pixels_button.isEnabled())

    def test_single_fit_exception_is_reported(self):
        self._load_two_dip()
        self._select(SINGLE_LORENTZIAN)
        with patch.object(analysis_window_module, "fit_single_lorentzian",
                          side_effect=RuntimeError("Optimal parameters not found")), \
                patch.object(QMessageBox, "warning") as warning:
            self.assertIsNone(self.window.fit_global_spectrum())
        warning.assert_called_once()
        self.assertIn("Optimal parameters not found", self.window.panel.status_label.text())
        self.assertIn("Fit failed", self.window.panel.fit_info_label.text())

    def test_supplied_location_outside_measured_range_is_reported_before_fitting(self):
        self._load_two_dip()
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, dict(DOUBLE_GUESS, f02=3.10))
        with patch.object(analysis_window_module, "fit_global_odmr_spectrum") as fitter, \
                patch.object(QMessageBox, "warning") as warning:
            self.assertIsNone(self.window.fit_global_spectrum())
        fitter.assert_not_called()
        warning.assert_called_once()
        self.assertIn("measured range", self.window.panel.status_label.text())


class PixelSeedingTests(_AnalysisWindowCase):
    def test_pixel_fits_are_seeded_from_the_global_result(self):
        self._load_two_dip()
        self.window.set_initial_fit_parameters(DOUBLE_LORENTZIAN, DOUBLE_GUESS)
        global_fit = self.window.fit_global_spectrum()
        self.assertTrue(global_fit.success)

        seeds = []
        real_fit = pixel_fit_module.fit_two_lorentzian_spectrum

        def spy(frequency_hz, spectrum, initial_guess=None, **kwargs):
            seeds.append(initial_guess)
            return real_fit(frequency_hz, spectrum, initial_guess=initial_guess, **kwargs)

        # Run the window's own worker synchronously on its stored global fit.
        worker = ODMRPixelFitWorker(
            self.window.statistics_result.final_mean, self.window.global_fit_result
        )
        results = []
        worker.finished.connect(results.append)
        with patch.object(pixel_fit_module, "fit_two_lorentzian_spectrum", side_effect=spy):
            worker.run()

        self.assertEqual(len(seeds), 4)
        for seed in seeds:
            self.assertEqual(seed.frequency_minus_hz, global_fit.frequency_minus_hz)
            self.assertEqual(seed.frequency_plus_hz, global_fit.frequency_plus_hz)
            self.assertEqual(seed.linewidth_minus_hz, global_fit.linewidth_minus_hz)
            self.assertEqual(seed.linewidth_plus_hz, global_fit.linewidth_plus_hz)
        result = results[0]
        self.assertEqual(result.successful_fit_count, 4)
        np.testing.assert_allclose(result.f_minus_hz, F_MINUS, atol=CENTRE_TOLERANCE_HZ)
        np.testing.assert_allclose(result.f_plus_hz, F_PLUS, atol=CENTRE_TOLERANCE_HZ)

        self.window._pixel_fit_finished(result)
        summary = self.window.panel.pixel_fit_summary_label.text()
        self.assertIn("Seeded from the global Double Lorentzian fit", summary)
        self.assertIn("supplied", summary)

    def test_pixel_fitting_unavailable_without_successful_global_fit(self):
        self._load_two_dip()
        self.assertFalse(self.window.panel.fit_pixels_button.isEnabled())
        self.assertFalse(self.window.fit_pixels())


if __name__ == "__main__":
    unittest.main()
