"""Hardware-free synthetic tests for the canonical ODMR fit API."""

import unittest
from unittest.mock import patch

import numpy as np

from analysis.odmr_fit import (
    TwoLorentzianInitialGuess,
    fit_double_lorentzian,
    fit_global_odmr_spectrum,
    fit_single_lorentzian,
    fit_two_lorentzian_spectrum,
    two_lorentzian_odmr_model,
)


class SyntheticSpectrumMixin:
    def setUp(self):
        self.frequency_hz = np.linspace(2.860e9, 2.880e9, 121)
        self.expected = TwoLorentzianInitialGuess(
            baseline=100.0,
            amplitude_minus=3.0,
            frequency_minus_hz=2.868e9,
            linewidth_minus_hz=2.2e6,
            amplitude_plus=2.0,
            frequency_plus_hz=2.872e9,
            linewidth_plus_hz=2.8e6,
        )
        self.spectrum = two_lorentzian_odmr_model(
            self.frequency_hz, *self.expected.as_array()
        )


class TwoLorentzianModelTests(SyntheticSpectrumMixin, unittest.TestCase):
    def test_model_matches_requested_common_baseline_equation(self):
        f = np.array([2.868e9, 2.872e9, 2.870e9])
        actual = two_lorentzian_odmr_model(f, *self.expected.as_array())
        p = self.expected
        expected = (
            p.baseline
            - p.amplitude_minus
            / (1 + 4 * (f - p.frequency_minus_hz) ** 2 / p.linewidth_minus_hz**2)
            - p.amplitude_plus
            / (1 + 4 * (f - p.frequency_plus_hz) ** 2 / p.linewidth_plus_hz**2)
        )
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)

    def test_model_rejects_nonphysical_or_unordered_parameters(self):
        params = self.expected.as_array()
        for index, value, message in (
            (1, 0.0, "amplitudes"),
            (3, -1.0, "linewidths"),
            (5, params[2], "f_minus < f_plus"),
        ):
            bad = params.copy()
            bad[index] = value
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, message):
                two_lorentzian_odmr_model(self.frequency_hz, *bad)


class TwoLorentzianFitTests(SyntheticSpectrumMixin, unittest.TestCase):
    def test_noiseless_spectrum_recovers_parameters_and_covariance(self):
        result = fit_two_lorentzian_spectrum(self.frequency_hz, self.spectrum)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.n_valid_points, self.frequency_hz.size)
        self.assertAlmostEqual(result.baseline, self.expected.baseline, places=7)
        self.assertAlmostEqual(result.amplitude_minus, self.expected.amplitude_minus, places=6)
        self.assertAlmostEqual(result.frequency_minus_hz, self.expected.frequency_minus_hz, delta=10.0)
        self.assertAlmostEqual(result.linewidth_minus_hz, self.expected.linewidth_minus_hz, delta=10.0)
        self.assertAlmostEqual(result.amplitude_plus, self.expected.amplitude_plus, places=6)
        self.assertAlmostEqual(result.frequency_plus_hz, self.expected.frequency_plus_hz, delta=10.0)
        self.assertAlmostEqual(result.linewidth_plus_hz, self.expected.linewidth_plus_hz, delta=10.0)
        self.assertLess(result.rmse, 1e-8)
        self.assertAlmostEqual(result.r2, 1.0)
        np.testing.assert_allclose(result.fitted_spectrum, self.spectrum, atol=1e-8)
        self.assertEqual(result.residuals.shape, self.spectrum.shape)
        self.assertTrue(result.covariance is None or result.covariance.shape == (7, 7))
        self.assertTrue(result.standard_errors is None or result.standard_errors.shape == (7,))

    def test_deterministic_noise_keeps_fit_physical_and_ordered(self):
        rng = np.random.default_rng(721)
        noisy = self.spectrum + rng.normal(0.0, 0.015, self.spectrum.size)
        result = fit_two_lorentzian_spectrum(self.frequency_hz, noisy)
        self.assertTrue(result.success, result.message)
        self.assertGreater(result.amplitude_minus, 0)
        self.assertGreater(result.amplitude_plus, 0)
        self.assertGreater(result.linewidth_minus_hz, 0)
        self.assertGreater(result.linewidth_plus_hz, 0)
        self.assertLess(result.frequency_minus_hz, result.frequency_plus_hz)
        self.assertGreaterEqual(result.frequency_minus_hz, self.frequency_hz[0])
        self.assertLessEqual(result.frequency_plus_hz, self.frequency_hz[-1])
        self.assertLessEqual(result.amplitude_minus, 2 * np.ptp(noisy))
        self.assertLessEqual(result.amplitude_plus, 2 * np.ptp(noisy))
        self.assertAlmostEqual(result.frequency_minus_hz, self.expected.frequency_minus_hz, delta=2e5)
        self.assertAlmostEqual(result.frequency_plus_hz, self.expected.frequency_plus_hz, delta=2e5)
        self.assertLess(result.rmse, 0.03)

    def test_nonfinite_signal_samples_are_omitted_and_residuals_marked(self):
        signal = self.spectrum.copy()
        signal[[4, 35, 82]] = np.nan
        result = fit_two_lorentzian_spectrum(self.frequency_hz, signal)
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.n_valid_points, signal.size - 3)
        self.assertTrue(np.all(np.isnan(result.residuals[[4, 35, 82]])))
        self.assertTrue(np.all(np.isfinite(result.fitted_spectrum)))

    def test_fit_result_provides_reusable_initialization_template(self):
        first = fit_two_lorentzian_spectrum(self.frequency_hz, self.spectrum)
        template = first.as_initial_guess()
        self.assertIsInstance(template, TwoLorentzianInitialGuess)
        np.testing.assert_allclose(template.as_array(), first.params)
        second = fit_two_lorentzian_spectrum(
            self.frequency_hz, self.spectrum, initial_guess=template
        )
        self.assertTrue(second.success, second.message)
        np.testing.assert_allclose(second.params, first.params, rtol=1e-10, atol=1e-5)

    def test_raw_parameter_vector_is_accepted_as_initialization(self):
        result = fit_two_lorentzian_spectrum(
            self.frequency_hz, self.spectrum, initial_guess=self.expected.as_array()
        )
        self.assertTrue(result.success, result.message)

    def test_input_validation_rejects_bad_shapes_units_order_and_flat_signal(self):
        with self.assertRaisesRegex(ValueError, "one-dimensional"):
            fit_two_lorentzian_spectrum(self.frequency_hz[:, None], self.spectrum)
        with self.assertRaisesRegex(ValueError, "same shape"):
            fit_two_lorentzian_spectrum(self.frequency_hz[:-1], self.spectrum)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            fit_two_lorentzian_spectrum(self.frequency_hz[::-1], self.spectrum[::-1])
        with self.assertRaisesRegex(ValueError, "finite, non-zero"):
            fit_two_lorentzian_spectrum(self.frequency_hz, np.ones_like(self.spectrum))
        too_sparse = self.spectrum.copy()
        too_sparse[:-7] = np.nan
        with self.assertRaisesRegex(ValueError, "At least 8 finite"):
            fit_two_lorentzian_spectrum(self.frequency_hz, too_sparse)

    def test_bad_initialization_template_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "f_minus < f_plus"):
            fit_two_lorentzian_spectrum(
                self.frequency_hz,
                self.spectrum,
                initial_guess=[100, 2, 2.873e9, 1e6, 3, 2.867e9, 1e6],
            )

    def test_optimizer_failure_is_returned_as_explicit_unsuccessful_result(self):
        result = fit_two_lorentzian_spectrum(
            self.frequency_hz, self.spectrum, maxfev=1
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "optimizer_failed")
        self.assertIsNone(result.covariance)
        self.assertIsNone(result.standard_errors)
        self.assertTrue(np.all(np.isnan(result.fitted_spectrum)))


class LegacyLorentzianInitializationTests(unittest.TestCase):
    def test_single_manual_partial_guess_overrides_only_provided_parameters(self):
        x = np.linspace(2.86, 2.88, 11)
        y = np.linspace(100, 95, 11)
        captured = {}

        def optimizer(_model, _x, _y, p0, **_kwargs):
            captured["p0"] = list(p0)
            return np.asarray(p0), np.eye(4)

        with patch("analysis.odmr_fit.curve_fit", optimizer):
            fit_single_lorentzian(x, y, initial_guess=[None, 2.5, 2.871, 0.003])
        self.assertEqual(captured["p0"], [100.0, 2.5, 2.871, 0.003])

    def test_double_manual_partial_guess_and_width_units_pass_through(self):
        x = np.linspace(2.86, 2.88, 21)
        y = 100 - np.exp(-((x - 2.867) / .001) ** 2) - np.exp(-((x - 2.873) / .001) ** 2)
        supplied = [None, 1.1, 2.867, .002, 1.2, 2.873, .003]
        captured = {}

        def optimizer(_model, _x, _y, p0, **_kwargs):
            captured["p0"] = list(p0)
            return np.asarray(p0), np.eye(7)

        with patch("analysis.odmr_fit.curve_fit", optimizer):
            fit_double_lorentzian(x, y, initial_guess=supplied)
        self.assertEqual(captured["p0"][0], 100.0)
        self.assertEqual(captured["p0"][1:], supplied[1:])

    def test_omitted_initial_guess_preserves_automatic_guess(self):
        x = np.linspace(2.86, 2.88, 11)
        y = np.array([100, 100, 99, 98, 97, 96, 97, 98, 99, 100, 100], dtype=float)
        captured = {}

        def optimizer(_model, _x, _y, p0, **_kwargs):
            captured["p0"] = list(p0)
            return np.asarray(p0), np.eye(4)

        with patch("analysis.odmr_fit.curve_fit", optimizer):
            fit_single_lorentzian(x, y)
        self.assertEqual(captured["p0"], [100.0, 4.0, x[5], (x[-1] - x[0]) / 10])


class GlobalSpectrumTests(SyntheticSpectrumMixin, unittest.TestCase):
    def test_global_spectrum_averages_only_finite_pixels_and_fits_it(self):
        cube = np.stack(
            [
                self.spectrum - 1.0,
                self.spectrum + 1.0,
                self.spectrum + 3.0,
                np.full_like(self.spectrum, np.nan),
            ],
            axis=1,
        ).reshape(self.spectrum.size, 2, 2)
        cube[12, 0, 1] = np.nan
        result = fit_global_odmr_spectrum(cube, self.frequency_hz)
        expected = self.spectrum + 1.0
        expected[12] = (self.spectrum[12] - 1.0 + self.spectrum[12] + 3.0) / 2
        np.testing.assert_allclose(result.spectrum, expected)
        self.assertEqual(result.n_valid_pixels_per_frequency[0], 3)
        self.assertEqual(result.n_valid_pixels_per_frequency[12], 2)
        self.assertEqual(result.n_valid_pixels_per_frequency[-1], 3)
        self.assertTrue(result.success, result.message)
        self.assertAlmostEqual(result.baseline, 101.0, places=6)

    def test_global_spectrum_skips_frequency_planes_with_no_valid_pixels(self):
        cube = np.tile(self.spectrum[:, None, None], (1, 2, 2))
        cube[20, :, :] = np.nan
        result = fit_global_odmr_spectrum(cube, self.frequency_hz)
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.n_valid_pixels_per_frequency[20], 0)
        self.assertTrue(np.isnan(result.spectrum[20]))
        self.assertTrue(np.isnan(result.residuals[20]))
        self.assertTrue(np.isfinite(result.fitted_spectrum[20]))

    def test_global_spectrum_rejects_wrong_cube_or_frequency_shape(self):
        with self.assertRaisesRegex(ValueError, "frequency, y, x"):
            fit_global_odmr_spectrum(self.spectrum, self.frequency_hz)
        with self.assertRaisesRegex(ValueError, "one value per frequency"):
            fit_global_odmr_spectrum(
                np.tile(self.spectrum[:, None, None], (1, 2, 2)),
                self.frequency_hz[:-1],
            )


if __name__ == "__main__":
    unittest.main()
