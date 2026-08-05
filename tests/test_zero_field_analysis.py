"""Unit tests for reusable Zero Field ImageCube analysis."""

import shutil
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


class ImageCubeStreamingTests(unittest.TestCase):
    """ImageCube.open_streaming_write(): incremental, crash-safe HDF5 writes."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.path = self.directory / "stream.h5"
        self.planes = [
            np.full((2, 3), 1.0),
            np.full((2, 3), 2.0),
            np.full((2, 3), 3.0),
        ]

    def test_open_streaming_write_rejects_npz(self):
        with self.assertRaises(NotImplementedError):
            ImageCube.open_streaming_write(self.directory / "stream.npz", 3)

    def test_interrupted_write_is_loadable_and_marked_incomplete(self):
        # Simulates a crash or forced quit partway through a scan: the
        # writer is closed directly, without ever calling finalize().
        writer = ImageCube.open_streaming_write(
            self.path,
            num_planes=3,
            scan_axis_name="Magnetic Field",
            scan_axis_unit="G",
            scan_axis_values=[-1.0, 0.0, 1.0],
            metadata={"experiment_name": "Zero Field"},
        )
        writer.write_plane(0, self.planes[0])
        writer.write_plane(1, self.planes[1])
        writer.close()

        cube = ImageCube.load(self.path)

        self.assertFalse(cube.metadata["scan_complete"])
        self.assertEqual(cube.metadata["planes_written"], 2)
        self.assertEqual(cube.metadata["experiment_name"], "Zero Field")
        # close() truncates data and scan_axis_values to the planes actually
        # written -- a partial file must contain only real data, never an
        # HDF5 fill-value plane indistinguishable from a genuine frame.
        self.assertEqual(cube.data.shape, (2, 2, 3))
        np.testing.assert_array_equal(cube.data[0], self.planes[0])
        np.testing.assert_array_equal(cube.data[1], self.planes[1])
        self.assertEqual(list(cube.scan_axis_values), [-1.0, 0.0])

    def test_finalize_rejects_a_writer_that_never_reached_num_planes(self):
        writer = ImageCube.open_streaming_write(self.path, num_planes=3)
        writer.write_plane(0, self.planes[0])

        with self.assertRaises(ValueError):
            writer.finalize()

        writer.close()
        cube = ImageCube.load(self.path)
        self.assertFalse(cube.metadata["scan_complete"])
        self.assertEqual(cube.data.shape, (1, 2, 3))

    def test_finalize_marks_complete_and_merges_final_metadata(self):
        writer = ImageCube.open_streaming_write(
            self.path, num_planes=3, metadata={"completed_scans": 0}
        )
        for index, plane in enumerate(self.planes):
            writer.write_plane(index, plane)
        writer.finalize({"completed_scans": 1, "experiment_complete": True})

        cube = ImageCube.load(self.path)

        self.assertTrue(cube.metadata["scan_complete"])
        self.assertEqual(cube.metadata["planes_written"], 3)
        self.assertEqual(cube.metadata["completed_scans"], 1)
        self.assertTrue(cube.metadata["experiment_complete"])
        for index, plane in enumerate(self.planes):
            np.testing.assert_array_equal(cube.data[index], plane)

    def test_write_plane_rejects_inconsistent_shape(self):
        writer = ImageCube.open_streaming_write(self.path, num_planes=2)
        writer.write_plane(0, np.zeros((2, 3)))

        with self.assertRaises(ValueError):
            writer.write_plane(1, np.zeros((4, 5)))

        writer.close()

    def test_write_plane_rejects_out_of_range_index(self):
        writer = ImageCube.open_streaming_write(self.path, num_planes=2)

        with self.assertRaises(ValueError):
            writer.write_plane(5, np.zeros((2, 3)))

        writer.close()

    def test_context_manager_leaves_scan_complete_false_without_finalize(self):
        with ImageCube.open_streaming_write(self.path, num_planes=2) as writer:
            writer.write_plane(0, np.zeros((2, 3)))
            # Exiting the with-block does not imply completion -- only an
            # explicit finalize() call does.

        cube = ImageCube.load(self.path)
        self.assertFalse(cube.metadata["scan_complete"])
        self.assertEqual(cube.metadata["planes_written"], 1)


if __name__ == "__main__":
    unittest.main()
