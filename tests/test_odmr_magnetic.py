"""Hardware-free tests for ODMR splitting-to-field reconstruction."""

import unittest

import numpy as np

from analysis.odmr_fit import fit_global_odmr_spectrum, two_lorentzian_odmr_model
from analysis.odmr_magnetic import (
    DEFAULT_GAMMA_E_HZ_PER_GAUSS,
    ODMRMagneticReconstruction,
    reconstruct_bz,
)
from analysis.odmr_pixel_fit import ODMRPixelFitResult, PixelFitStatus, fit_odmr_pixels


def _pixel_result(
    f_minus,
    f_plus,
    sigma_minus=None,
    sigma_plus=None,
    status=None,
    successful=None,
    splitting_override=None,
):
    f_minus = np.asarray(f_minus, dtype=np.float64)
    f_plus = np.asarray(f_plus, dtype=np.float64)
    shape = f_minus.shape
    if sigma_minus is None:
        sigma_minus = np.full(shape, np.nan)
    if sigma_plus is None:
        sigma_plus = np.full(shape, np.nan)
    if status is None:
        status = np.full(shape, PixelFitStatus.SUCCESS, dtype=np.uint8)
    if successful is None:
        successful = np.ones(shape, dtype=bool)
    return ODMRPixelFitResult(
        baseline=np.full(shape, 100.0),
        amplitude_minus=np.ones(shape),
        amplitude_plus=np.ones(shape),
        f_minus_hz=f_minus,
        f_plus_hz=f_plus,
        splitting_hz=(
            f_plus - f_minus if splitting_override is None else splitting_override
        ),
        center_frequency_hz=(f_plus + f_minus) / 2.0,
        linewidth_minus_hz=np.ones(shape),
        linewidth_plus_hz=np.ones(shape),
        rmse=np.zeros(shape),
        r2=np.ones(shape),
        f_minus_uncertainty_hz=np.asarray(sigma_minus, dtype=np.float64),
        f_plus_uncertainty_hz=np.asarray(sigma_plus, dtype=np.float64),
        status=np.asarray(status, dtype=np.uint8),
        valid_spectrum_mask=np.ones(shape, dtype=bool),
        successful_fit_mask=np.asarray(successful, dtype=bool),
        finite_sample_count=np.full(shape, 10, dtype=np.int32),
        valid_pixel_count=int(np.size(f_minus)),
        successful_fit_count=int(np.size(f_minus)),
        failed_fit_count=0,
        success_fraction=1.0,
        frequency_hz=np.arange(10, dtype=np.float64),
    )


class MagneticReconstructionTests(unittest.TestCase):
    def test_known_splitting_gives_expected_nv_projection_in_gauss(self):
        b_nv_expected = 1.25
        splitting_hz = 2.0 * DEFAULT_GAMMA_E_HZ_PER_GAUSS * b_nv_expected
        fit = _pixel_result([[2.86e9]], [[2.86e9 + splitting_hz]])
        result = reconstruct_bz(fit, nv_projection_angle_deg=0.0)
        self.assertAlmostEqual(result.bz_nv_projection[0, 0], b_nv_expected, places=12)
        self.assertAlmostEqual(result.bz_magnitude[0, 0], b_nv_expected, places=12)
        self.assertEqual(result.field_units, "G")
        self.assertEqual(result.gamma_e_units, "Hz/G")

    def test_default_geometry_matches_sqrt_three_projection_relation(self):
        b_nv = 0.7
        delta_hz = 2.0 * DEFAULT_GAMMA_E_HZ_PER_GAUSS * b_nv
        fit = _pixel_result([[2.86e9]], [[2.86e9 + delta_hz]])
        result = reconstruct_bz(fit)
        expected = np.sqrt(3.0) * b_nv
        self.assertAlmostEqual(result.bz_nv_projection[0, 0], b_nv, places=12)
        self.assertAlmostEqual(result.bz_magnitude[0, 0], expected, delta=4e-7)
        self.assertAlmostEqual(
            result.bz_magnitude[0, 0],
            result.bz_nv_projection[0, 0] / result.projection_factor,
            places=12,
        )
        self.assertAlmostEqual(result.projection_factor, 1 / np.sqrt(3), delta=2e-7)

    def test_multi_pixel_map_uses_hz_and_preserves_spatial_values(self):
        expected_bz = np.array([[0.2, 0.5], [1.0, 2.5]])
        delta = (
            2.0 * DEFAULT_GAMMA_E_HZ_PER_GAUSS * expected_bz
            * (1.0 / np.sqrt(3.0))
        )
        f_minus = np.full(expected_bz.shape, 2.86e9)
        result = reconstruct_bz(_pixel_result(f_minus, f_minus + delta))
        self.assertEqual(result.bz_magnitude.shape, (2, 2))
        np.testing.assert_allclose(result.bz_magnitude, expected_bz, rtol=5e-7)
        np.testing.assert_allclose(result.splitting_hz, delta, rtol=0, atol=1e-6)
        self.assertTrue(np.all(result.valid_mask))
        self.assertEqual(result.metadata["convention"], "non_negative_magnitude")

    def test_invalid_frequencies_status_or_nonpositive_splitting_make_nan_outputs(self):
        f_minus = np.array([[2.86e9, np.nan], [2.86e9, 2.86e9]])
        f_plus = np.array([[2.861e9, 2.862e9], [2.86e9, 2.859e9]])
        status = np.array(
            [[PixelFitStatus.SUCCESS, PixelFitStatus.SUCCESS],
             [PixelFitStatus.OPTIMIZER_FAILURE, PixelFitStatus.SUCCESS]],
            dtype=np.uint8,
        )
        successful = np.array([[True, True], [False, True]])
        result = reconstruct_bz(
            _pixel_result(f_minus, f_plus, status=status, successful=successful)
        )
        self.assertTrue(result.valid_mask[0, 0])
        self.assertFalse(result.valid_mask[0, 1])
        self.assertFalse(result.valid_mask[1, 0])
        self.assertFalse(result.valid_mask[1, 1])
        self.assertTrue(np.isfinite(result.bz_magnitude[0, 0]))
        self.assertTrue(np.all(np.isnan(result.bz_magnitude[~result.valid_mask])))
        self.assertTrue(np.all(np.isnan(result.bz_uncertainty)))

    def test_uncertainty_propagation_uses_independent_frequency_errors(self):
        sigma_minus = np.array([[120_000.0]])
        sigma_plus = np.array([[160_000.0]])
        fit = _pixel_result(
            [[2.86e9]], [[2.87e9]], sigma_minus=sigma_minus, sigma_plus=sigma_plus
        )
        result = reconstruct_bz(fit)
        expected = np.sqrt(120_000.0**2 + 160_000.0**2)
        expected /= 2 * DEFAULT_GAMMA_E_HZ_PER_GAUSS * result.projection_factor
        self.assertAlmostEqual(result.bz_uncertainty[0, 0], expected, places=12)

    def test_missing_or_nonfinite_uncertainties_leave_valid_field_and_nan_error(self):
        fit = _pixel_result(
            [[2.86e9, 2.86e9]],
            [[2.87e9, 2.871e9]],
            sigma_minus=[[np.nan, 1000.0]],
            sigma_plus=[[2000.0, np.inf]],
        )
        result = reconstruct_bz(fit)
        self.assertTrue(np.all(result.valid_mask))
        self.assertTrue(np.all(np.isfinite(result.bz_magnitude)))
        self.assertTrue(np.all(np.isnan(result.bz_uncertainty)))

    def test_valid_field_is_nonnegative_and_does_not_encode_direction(self):
        fit = _pixel_result(
            [[2.86e9, 2.87e9]],
            [[2.861e9, 2.871e9]],
        )
        result = reconstruct_bz(fit)
        self.assertTrue(np.all(result.bz_magnitude[result.valid_mask] >= 0))
        self.assertEqual(result.convention, "non_negative_magnitude")

    def test_function_does_not_modify_pixel_fit_result(self):
        fit = _pixel_result(
            [[2.86e9, np.nan]],
            [[2.87e9, 2.88e9]],
            sigma_minus=[[100.0, np.nan]],
            sigma_plus=[[200.0, np.nan]],
        )
        before = {
            name: np.array(getattr(fit, name), copy=True)
            for name in (
                "f_minus_hz", "f_plus_hz", "splitting_hz", "status",
                "successful_fit_mask", "f_minus_uncertainty_hz",
                "f_plus_uncertainty_hz",
            )
        }
        reconstruct_bz(fit)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(fit, name), values)

    def test_custom_gamma_and_projection_angle_are_applied(self):
        gamma = 3.0e6
        angle = 30.0
        delta = 2.0 * gamma * 0.8
        fit = _pixel_result([[2.86e9]], [[2.86e9 + delta]])
        result = reconstruct_bz(
            fit, gamma_e=gamma, nv_projection_angle_deg=angle
        )
        self.assertAlmostEqual(result.bz_nv_projection[0, 0], 0.8, places=12)
        self.assertAlmostEqual(result.bz_magnitude[0, 0], 0.8 / np.cos(np.deg2rad(angle)))
        self.assertEqual(result.gamma_e_hz_per_gauss, gamma)
        self.assertEqual(result.nv_projection_angle_deg, angle)

    def test_invalid_gamma_and_projection_angles_are_rejected(self):
        fit = _pixel_result([[2.86e9]], [[2.87e9]])
        for angle in (90.0, 120.0, -1.0, np.nan, np.inf):
            with self.subTest(angle=angle), self.assertRaisesRegex(ValueError, "angle"):
                reconstruct_bz(fit, nv_projection_angle_deg=angle)
        for gamma in (0.0, -1.0, np.nan, np.inf):
            with self.subTest(gamma=gamma), self.assertRaisesRegex(ValueError, "gamma_e"):
                reconstruct_bz(fit, gamma_e=gamma)

    def test_result_shapes_and_metadata_are_explicit(self):
        fit = _pixel_result(np.full((2, 3), 2.86e9), np.full((2, 3), 2.87e9))
        result = reconstruct_bz(fit)
        self.assertIsInstance(result, ODMRMagneticReconstruction)
        for name in (
            "bz_magnitude", "bz_nv_projection", "splitting_hz",
            "bz_uncertainty", "valid_mask",
        ):
            self.assertEqual(getattr(result, name).shape, (2, 3))
        self.assertEqual(result.metadata["gamma_e_units"], "Hz/G")
        self.assertEqual(result.metadata["field_units"], "G")
        self.assertAlmostEqual(result.metadata["projection_factor"], 1 / np.sqrt(3), delta=2e-7)

    def test_end_to_end_synthetic_cube_fit_and_bz_reconstruction(self):
        gamma = DEFAULT_GAMMA_E_HZ_PER_GAUSS
        expected_bz = 2.0
        splitting = 2 * gamma * expected_bz * (1 / np.sqrt(3))
        frequency_hz = np.linspace(2.860e9, 2.880e9, 161)
        cube = np.empty((frequency_hz.size, 2, 2), dtype=np.float64)
        for y in range(2):
            for x in range(2):
                center = 2.870e9 + (y - 0.5) * 100_000 + (x - 0.5) * 80_000
                cube[:, y, x] = two_lorentzian_odmr_model(
                    frequency_hz,
                    100.0 + y + x,
                    2.5,
                    center - splitting / 2,
                    2.0e6,
                    2.0,
                    center + splitting / 2,
                    2.4e6,
                )
        global_fit = fit_global_odmr_spectrum(cube, frequency_hz)
        pixel_fit = fit_odmr_pixels(cube, global_fit)
        result = reconstruct_bz(pixel_fit)
        self.assertTrue(global_fit.success, global_fit.message)
        self.assertTrue(np.all(pixel_fit.successful_fit_mask))
        np.testing.assert_allclose(result.bz_magnitude, expected_bz, atol=0.02)


if __name__ == "__main__":
    unittest.main()
