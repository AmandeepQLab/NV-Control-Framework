"""Unit tests for reusable Zero Field ImageCube analysis."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from framework.analysis.zero_field import mean_fluorescence_vs_field
from framework.image_cube import ImageCube


class ZeroFieldAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.fields = np.array([-1.0, 0.0, 1.0])
        self.data = np.array(
            [
                [[1.0, 2.0], [3.0, 4.0]],
                [[10.0, 20.0], [30.0, 40.0]],
                [[100.0, 200.0], [300.0, 400.0]],
            ]
        )
        self.cube = ImageCube(
            data=self.data,
            scan_axis_name="Magnetic Field",
            scan_axis_unit="G",
            scan_axis_values=self.fields,
        )

    def test_returns_expected_field_vector_and_full_frame_signal(self):
        fields, signals = mean_fluorescence_vs_field(self.cube)

        np.testing.assert_array_equal(fields, self.fields)
        np.testing.assert_allclose(signals, [2.5, 25.0, 250.0])

    def test_acquisition_roi_metadata_does_not_crop_images_again(self):
        self.cube.metadata["acquisition_roi"] = (100, 200, 102, 202)

        _, signals = mean_fluorescence_vs_field(self.cube)

        np.testing.assert_allclose(signals, [2.5, 25.0, 250.0])

    def test_loaded_image_cube_can_be_reanalyzed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field.npz"
            self.cube.save(path)
            loaded_cube = ImageCube.load(path)

        fields, signals = mean_fluorescence_vs_field(loaded_cube)

        np.testing.assert_array_equal(fields, self.fields)
        np.testing.assert_allclose(signals, [2.5, 25.0, 250.0])


if __name__ == "__main__":
    unittest.main()
