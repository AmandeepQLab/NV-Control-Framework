"""Synthetic, hardware-free tests for pixel-wise ODMR fitting."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

import analysis.odmr_pixel_fit as pixel_fit
from analysis.odmr_fit import fit_global_odmr_spectrum, two_lorentzian_odmr_model
from analysis.odmr_pixel_fit import PixelFitStatus, fit_odmr_pixels


def _synthetic_cube(
    height=2,
    width=3,
    noise_std=0.0,
    varying_split=False,
    varying_amplitude=False,
    varying_baseline=False,
    seed=481,
):
    frequency_hz = np.linspace(2.860e9, 2.880e9, 121)
    rng = np.random.default_rng(seed)
    cube = np.empty((frequency_hz.size, height, width), dtype=np.float64)
    truth = {}
    for y in range(height):
        for x in range(width):
            split = 4.0e6 + (y * width + x) * 0.35e6 if varying_split else 4.0e6
            center = 2.870e9 + (y - (height - 1) / 2) * 0.45e6 + (
                x - (width - 1) / 2
            ) * 0.25e6
            f_minus = center - split / 2
            f_plus = center + split / 2
            amp_minus = 2.0 + (y * width + x) * 0.18 if varying_amplitude else 2.5
            amp_plus = 1.5 + (y * width + x) * 0.12 if varying_amplitude else 2.0
            baseline = 98.0 + y * 1.5 + x * 0.7 if varying_baseline else 100.0
            spectrum = two_lorentzian_odmr_model(
                frequency_hz,
                baseline,
                amp_minus,
                f_minus,
                2.2e6,
                amp_plus,
                f_plus,
                2.7e6,
            )
            if noise_std:
                spectrum = spectrum + rng.normal(0.0, noise_std, spectrum.size)
            cube[:, y, x] = spectrum
            truth[(y, x)] = {
                "center": center,
                "split": split,
                "f_minus": f_minus,
                "f_plus": f_plus,
                "amplitude_minus": amp_minus,
                "amplitude_plus": amp_plus,
                "baseline": baseline,
            }
    return frequency_hz, cube, truth


def _global_fit(frequency_hz, cube):
    return fit_global_odmr_spectrum(cube, frequency_hz)


class PixelFitTests(unittest.TestCase):
    def test_identical_pixel_spectra_recover_both_resonances(self):
        frequency_hz, cube, truth = _synthetic_cube(height=2, width=3)
        global_fit = _global_fit(frequency_hz, cube)
        result = fit_odmr_pixels(cube, global_fit)
        self.assertTrue(global_fit.success, global_fit.message)
        self.assertEqual(result.f_minus_hz.shape, (2, 3))
        self.assertEqual(result.f_plus_hz.shape, (2, 3))
        self.assertTrue(np.all(result.successful_fit_mask))
        self.assertEqual(result.success_fraction, 1.0)
        for (y, x), expected in truth.items():
            self.assertAlmostEqual(result.f_minus_hz[y, x], expected["f_minus"], delta=100.0)
            self.assertAlmostEqual(result.f_plus_hz[y, x], expected["f_plus"], delta=100.0)

    def test_spatial_splitting_and_center_variation_are_recovered_independently(self):
        frequency_hz, cube, truth = _synthetic_cube(
            varying_split=True, height=2, width=3
        )
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        for (y, x), expected in truth.items():
            self.assertAlmostEqual(result.splitting_hz[y, x], expected["split"], delta=2e4)
            self.assertAlmostEqual(result.center_frequency_hz[y, x], expected["center"], delta=2e4)
        self.assertGreater(np.ptp(result.center_frequency_hz), 0.5e6)
        self.assertGreater(np.ptp(result.splitting_hz), 1.0e6)

    def test_varying_amplitudes_baselines_and_gaussian_noise_fit_stably(self):
        frequency_hz, cube, truth = _synthetic_cube(
            varying_amplitude=True,
            varying_baseline=True,
            noise_std=0.01,
            height=2,
            width=3,
        )
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        self.assertTrue(np.all(result.successful_fit_mask))
        for (y, x), expected in truth.items():
            self.assertAlmostEqual(result.baseline[y, x], expected["baseline"], delta=0.05)
            self.assertAlmostEqual(
                result.amplitude_minus[y, x], expected["amplitude_minus"], delta=0.08
            )
            self.assertAlmostEqual(
                result.amplitude_plus[y, x], expected["amplitude_plus"], delta=0.08
            )
        self.assertGreater(np.ptp(result.baseline), 1.0)

    def test_nan_inf_and_insufficient_spectra_get_explicit_statuses(self):
        frequency_hz, cube, _ = _synthetic_cube(height=2, width=2)
        cube[:, 0, 0] = np.nan
        cube[::3, 0, 1] = np.inf
        cube[:, 1, 0] = np.nan
        cube[:6, 1, 0] = 100.0
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, _synthetic_cube(height=2, width=2)[1]))
        self.assertEqual(result.status[0, 0], PixelFitStatus.INSUFFICIENT_SAMPLES)
        self.assertEqual(result.status[0, 1], PixelFitStatus.SUCCESS)
        self.assertEqual(result.status[1, 0], PixelFitStatus.INSUFFICIENT_SAMPLES)
        self.assertFalse(result.valid_spectrum_mask[0, 0])
        self.assertFalse(result.valid_spectrum_mask[1, 0])
        self.assertTrue(np.isnan(result.f_minus_hz[0, 0]))
        self.assertTrue(np.isnan(result.f_plus_hz[1, 0]))
        self.assertEqual(result.finite_sample_count[0, 1], frequency_hz.size - len(range(0, frequency_hz.size, 3)))

    def test_flat_finite_spectrum_is_marked_unusable(self):
        frequency_hz, cube, _ = _synthetic_cube(height=1, width=2)
        cube[:, 0, 0] = 100.0
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        self.assertEqual(result.status[0, 0], PixelFitStatus.UNUSABLE_SPECTRUM)
        self.assertFalse(result.valid_spectrum_mask[0, 0])
        self.assertTrue(np.isnan(result.rmse[0, 0]))

    def test_optimizer_failure_is_localized_to_one_pixel(self):
        frequency_hz, cube, _ = _synthetic_cube(height=2, width=2)
        global_fit = _global_fit(frequency_hz, cube)
        real_fit = pixel_fit.fit_two_lorentzian_spectrum
        calls = {"count": 0}

        def fail_first_pixel(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return replace(
                    real_fit(*args, **kwargs),
                    success=False,
                    status="optimizer_failed",
                    covariance=None,
                    standard_errors=None,
                )
            return real_fit(*args, **kwargs)

        with patch.object(pixel_fit, "fit_two_lorentzian_spectrum", fail_first_pixel):
            result = fit_odmr_pixels(cube, global_fit)
        self.assertEqual(result.status[0, 0], PixelFitStatus.OPTIMIZER_FAILURE)
        self.assertTrue(np.all(result.status[1:, :] == PixelFitStatus.SUCCESS))
        self.assertTrue(np.isnan(result.f_minus_hz[0, 0]))
        self.assertEqual(result.failed_fit_count, 1)

    def test_invalid_nonphysical_fit_result_is_explicitly_rejected(self):
        frequency_hz, cube, _ = _synthetic_cube(height=1, width=1)
        global_fit = _global_fit(frequency_hz, cube)
        real_fit = pixel_fit.fit_two_lorentzian_spectrum

        def return_unordered_fit(*args, **kwargs):
            fit = real_fit(*args, **kwargs)
            return replace(
                fit,
                frequency_minus_hz=fit.frequency_plus_hz,
                frequency_plus_hz=fit.frequency_minus_hz,
            )

        with patch.object(pixel_fit, "fit_two_lorentzian_spectrum", return_unordered_fit):
            result = fit_odmr_pixels(cube, global_fit)
        self.assertEqual(result.status[0, 0], PixelFitStatus.INVALID_RESULT)
        self.assertTrue(np.isnan(result.f_minus_hz[0, 0]))

    def test_splitting_center_and_diagnostic_counts_are_consistent(self):
        frequency_hz, cube, _ = _synthetic_cube(height=2, width=3)
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        np.testing.assert_allclose(
            result.splitting_hz[result.successful_fit_mask],
            (result.f_plus_hz - result.f_minus_hz)[result.successful_fit_mask],
        )
        np.testing.assert_allclose(
            result.center_frequency_hz[result.successful_fit_mask],
            ((result.f_plus_hz + result.f_minus_hz) / 2)[result.successful_fit_mask],
        )
        self.assertEqual(result.valid_pixel_count, 6)
        self.assertEqual(result.successful_fit_count, 6)
        self.assertEqual(result.failed_fit_count, 0)
        self.assertEqual(result.status.shape, (2, 3))
        self.assertEqual(result.successful_fit_mask.shape, (2, 3))

    def test_global_fit_is_used_as_structural_initialization_template(self):
        frequency_hz, cube, _ = _synthetic_cube(
            height=1, width=3, varying_amplitude=True, varying_baseline=True
        )
        global_fit = _global_fit(frequency_hz, cube)
        real_fit = pixel_fit.fit_two_lorentzian_spectrum
        seen = []

        def record_guess(frequency, spectrum, initial_guess=None, **kwargs):
            seen.append(initial_guess)
            return real_fit(frequency, spectrum, initial_guess=initial_guess, **kwargs)

        with patch.object(pixel_fit, "fit_two_lorentzian_spectrum", record_guess):
            result = fit_odmr_pixels(cube, global_fit)
        self.assertTrue(np.all(result.successful_fit_mask))
        self.assertEqual(len(seen), 3)
        template = global_fit.as_initial_guess()
        for guess in seen:
            self.assertEqual(guess.frequency_minus_hz, template.frequency_minus_hz)
            self.assertEqual(guess.frequency_plus_hz, template.frequency_plus_hz)
            self.assertEqual(guess.linewidth_minus_hz, template.linewidth_minus_hz)
            self.assertEqual(guess.linewidth_plus_hz, template.linewidth_plus_hz)
        self.assertEqual(len({guess.baseline for guess in seen}), 3)
        self.assertGreater(len({guess.amplitude_minus for guess in seen}), 1)

    def test_frequency_uncertainties_follow_covariance_availability(self):
        frequency_hz, cube, _ = _synthetic_cube(
            height=1, width=2, noise_std=0.015
        )
        result = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        self.assertTrue(np.all(result.successful_fit_mask))
        self.assertTrue(np.all(np.isfinite(result.f_minus_uncertainty_hz)))
        self.assertTrue(np.all(np.isfinite(result.f_plus_uncertainty_hz)))

        real_fit = pixel_fit.fit_two_lorentzian_spectrum

        def covariance_unavailable(*args, **kwargs):
            fit = real_fit(*args, **kwargs)
            return replace(fit, covariance=None, standard_errors=None)

        with patch.object(pixel_fit, "fit_two_lorentzian_spectrum", covariance_unavailable):
            no_covariance = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        self.assertTrue(np.all(np.isnan(no_covariance.f_minus_uncertainty_hz)))
        self.assertTrue(np.all(np.isnan(no_covariance.f_plus_uncertainty_hz)))

        def covariance_nonfinite(*args, **kwargs):
            fit = real_fit(*args, **kwargs)
            return replace(fit, covariance=np.full((7, 7), np.nan))

        with patch.object(pixel_fit, "fit_two_lorentzian_spectrum", covariance_nonfinite):
            invalid_covariance = fit_odmr_pixels(cube, _global_fit(frequency_hz, cube))
        self.assertTrue(np.all(np.isnan(invalid_covariance.f_minus_uncertainty_hz)))
        self.assertTrue(np.all(np.isnan(invalid_covariance.f_plus_uncertainty_hz)))

    def test_global_fit_must_be_successful_and_dimensions_must_match(self):
        frequency_hz, cube, _ = _synthetic_cube(height=1, width=1)
        global_fit = _global_fit(frequency_hz, cube)
        with self.assertRaisesRegex(ValueError, "must be successful"):
            fit_odmr_pixels(cube, replace(global_fit, success=False))
        with self.assertRaisesRegex(ValueError, "frequency length"):
            fit_odmr_pixels(cube[:-1], global_fit)


if __name__ == "__main__":
    unittest.main()
