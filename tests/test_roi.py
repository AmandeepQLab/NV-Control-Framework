"""Unit tests for reusable image ROI validation and extraction."""

import unittest

import numpy as np

from framework.roi import clip_roi, extract_roi, prepare_roi, validate_roi


class RoiTests(unittest.TestCase):
    def setUp(self):
        self.image = np.arange(20).reshape(4, 5)

    def test_none_returns_full_frame(self):
        validated, clipped, region = prepare_roi(self.image, None)

        self.assertIsNone(validated)
        self.assertIsNone(clipped)
        np.testing.assert_array_equal(region, self.image)

    def test_valid_roi(self):
        roi = (1, 1, 4, 3)

        self.assertEqual(validate_roi(roi), roi)
        self.assertEqual(clip_roi(roi, self.image.shape), roi)
        np.testing.assert_array_equal(
            extract_roi(self.image, roi), self.image[1:3, 1:4]
        )

    def test_wrong_length_and_non_numeric_coordinates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly four"):
            validate_roi((0, 0, 2))
        with self.assertRaisesRegex(ValueError, "numeric"):
            validate_roi((0, "top", 2, 3))

    def test_partially_outside_roi_is_clipped(self):
        roi = (-2, 1, 3, 8)

        self.assertEqual(clip_roi(roi, self.image.shape), (0, 1, 3, 4))
        np.testing.assert_array_equal(
            extract_roi(self.image, roi), self.image[1:4, 0:3]
        )

    def test_completely_outside_roi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "completely outside"):
            extract_roi(self.image, (6, 0, 8, 2))

    def test_negative_coordinates_with_no_overlap_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "completely outside"):
            extract_roi(self.image, (-4, -3, -1, -1))

    def test_reversed_coordinates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "x0 < x1"):
            validate_roi((3, 1, 2, 2))
        with self.assertRaisesRegex(ValueError, "y0 < y1"):
            validate_roi((1, 3, 2, 2))

    def test_empty_roi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "x0 < x1"):
            validate_roi((2, 1, 2, 3))

    def test_single_pixel_roi_is_valid(self):
        region = extract_roi(self.image, (2, 1, 3, 2))

        self.assertEqual(region.shape, (1, 1))
        self.assertEqual(region[0, 0], self.image[1, 2])

    def test_zero_width_roi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "zero-width"):
            validate_roi((1, 0, 1, 3))

    def test_zero_height_roi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "zero-height"):
            validate_roi((1, 2, 3, 2))


if __name__ == "__main__":
    unittest.main()
