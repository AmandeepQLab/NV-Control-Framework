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


class ImageCubeHDF5Tests(unittest.TestCase):
    """HDF5 persistence: round-trip, legacy .npz compatibility, plane reads."""

    def setUp(self):
        self.fields = np.array([-1.0, 0.0, 1.0])
        self.data = np.array(
            [
                [[1.0, 2.0], [3.0, 4.0]],
                [[10.0, 20.0], [30.0, 40.0]],
                [[100.0, 200.0], [300.0, 400.0]],
            ]
        )
        self.metadata = {
            "acquisition_roi": (0, 0, 2, 2),
            "scan_entry_magnet_vectors_mT": [
                {"x": 0.0, "y": 0.0, "z": 0.0},
                {"x": 0.05, "y": 0.0, "z": 0.0},
            ],
            "magnet_configuration": {
                "X": {"type": "E3631A", "port": "COM3"},
                "Y": {"type": "E3631A", "port": "COM4"},
            },
            "nested": {"deeper": {"deepest": [1, 2, 3]}},
        }
        self.cube = ImageCube(
            data=self.data,
            metadata=self.metadata,
            experiment_type="Zero Field",
            scan_axis_name="Magnetic Field",
            scan_axis_unit="G",
            scan_axis_values=self.fields,
        )

    def test_hdf5_round_trip_preserves_data_and_scan_axis_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field.h5"
            self.cube.save(path)
            loaded_cube = ImageCube.load(path)

        np.testing.assert_array_equal(loaded_cube.data, self.data)
        self.assertEqual(loaded_cube.experiment_type, "Zero Field")
        self.assertEqual(loaded_cube.scan_axis_name, "Magnetic Field")
        self.assertEqual(loaded_cube.scan_axis_unit, "G")
        np.testing.assert_array_equal(loaded_cube.scan_axis_values, self.fields)

    def test_hdf5_round_trip_preserves_none_scan_axis_fields(self):
        cube = ImageCube(data=self.data, experiment_type=None)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "no_scan_axis.h5"
            cube.save(path)
            loaded_cube = ImageCube.load(path)

        self.assertIsNone(loaded_cube.scan_axis_name)
        self.assertIsNone(loaded_cube.scan_axis_unit)
        self.assertIsNone(loaded_cube.experiment_type)

    def test_hdf5_save_handles_none_scan_axis_values_without_corrupting_data(self):
        # ImageCube.__init__ normalizes a constructor scan_axis_values=None
        # to [] unconditionally, so None is only reachable by mutating the
        # attribute directly post-construction, as done here. Without a
        # save-time guard, np.asarray(None, dtype=np.float64) silently
        # produces array(nan) instead of raising or preserving None.
        cube = ImageCube(data=self.data)
        cube.scan_axis_values = None

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "none_scan_axis_values.h5"
            cube.save(path)
            loaded_cube = ImageCube.load(path)

        # load() reconstructs via ImageCube(...), so __init__'s own
        # normalization means this comes back [] rather than None -- the
        # on-disk sentinel records the distinction even though the public
        # load() API cannot currently surface it as None.
        self.assertEqual(loaded_cube.scan_axis_values, [])

    def test_hdf5_round_trip_preserves_nested_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field.h5"
            self.cube.save(path)
            loaded_cube = ImageCube.load(path)

        self.assertEqual(
            loaded_cube.metadata["scan_entry_magnet_vectors_mT"],
            self.metadata["scan_entry_magnet_vectors_mT"],
        )
        self.assertEqual(
            loaded_cube.metadata["magnet_configuration"],
            self.metadata["magnet_configuration"],
        )
        self.assertEqual(loaded_cube.metadata["nested"], self.metadata["nested"])
        # JSON has no tuple type: acquisition_roi round-trips as a list, not
        # a tuple, unlike the legacy .npz path (documented, not a bug).
        self.assertEqual(
            loaded_cube.metadata["acquisition_roi"],
            list(self.metadata["acquisition_roi"]),
        )

    def test_existing_npz_file_still_loads_correctly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.npz"
            self.cube.save(path)
            loaded_cube = ImageCube.load(path)

        np.testing.assert_array_equal(loaded_cube.data, self.data)
        self.assertEqual(loaded_cube.metadata, self.metadata)
        self.assertEqual(loaded_cube.experiment_type, "Zero Field")
        self.assertEqual(loaded_cube.scan_axis_name, "Magnetic Field")
        self.assertEqual(loaded_cube.scan_axis_unit, "G")
        np.testing.assert_array_equal(loaded_cube.scan_axis_values, self.fields)
        # The legacy path is untouched: acquisition_roi survives as the
        # exact tuple it was saved as, unlike the new HDF5/JSON path.
        self.assertIsInstance(loaded_cube.metadata["acquisition_roi"], tuple)

    def test_plane_level_read_matches_full_cube_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field.h5"
            self.cube.save(path)
            loaded_cube = ImageCube.load(path)

            for index in range(self.data.shape[0]):
                np.testing.assert_array_equal(
                    ImageCube.load_plane(path, index), self.data[index]
                )

        np.testing.assert_array_equal(loaded_cube.data, self.data)

    def test_peek_shape_matches_data_shape_for_hdf5_and_npz(self):
        with tempfile.TemporaryDirectory() as directory:
            hdf5_path = Path(directory) / "zero_field.h5"
            npz_path = Path(directory) / "zero_field.npz"
            self.cube.save(hdf5_path)
            self.cube.save(npz_path)

            self.assertEqual(ImageCube.peek_shape(hdf5_path), self.data.shape)
            self.assertEqual(ImageCube.peek_shape(npz_path), self.data.shape)

    def test_load_plane_rejects_npz(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_field.npz"
            self.cube.save(path)

            with self.assertRaises(NotImplementedError):
                ImageCube.load_plane(path, 0)

    def test_save_rejects_data_less_cube_for_both_formats(self):
        cube = ImageCube(data=None)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                cube.save(Path(directory) / "empty.h5")
            with self.assertRaises(ValueError):
                cube.save(Path(directory) / "empty.npz")


if __name__ == "__main__":
    unittest.main()
