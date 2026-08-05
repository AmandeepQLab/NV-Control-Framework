"""Tests for analysis.zero_field_averaging: format-agnostic streaming
averaging (.npz and .h5 scan sources), checkpointed resumability, and
packaging into a final cube."""

import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from analysis.zero_field_averaging import (
    _atomic_save_npy,
    average_plane_range,
    average_scans,
    package_averaged_planes,
)
from framework.image_cube import ImageCube


def _make_scan_cube(data, roi=(0, 0, 2, 2), field_axis="Y"):
    metadata = {
        "acquisition_roi": roi,
        "scan_parameters": {"field_axis": field_axis},
    }
    return ImageCube(
        data=data,
        metadata=metadata,
        experiment_type="Zero Field",
        scan_axis_name="Magnetic Field",
        scan_axis_unit="G",
        scan_axis_values=[-1.0, 0.0, 1.0],
    )


class AverageScansTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        rng = np.random.default_rng(0)
        self.scan_data = [rng.random((3, 4, 4)) for _ in range(3)]
        self.expected = np.mean(np.stack(self.scan_data), axis=0)

    def _save_scans(self, suffix):
        paths = []
        for index, data in enumerate(self.scan_data, start=1):
            path = self.directory / f"scan_{index:03d}{suffix}"
            _make_scan_cube(data).save(path)
            paths.append(path)
        return paths

    def test_average_matches_direct_mean_for_npz_scans(self):
        paths = self._save_scans(".npz")
        output_path = self.directory / "average.npz"
        report_path = self.directory / "report.txt"

        average_scans(paths, output_path, report_path)

        loaded = ImageCube.load(output_path)
        np.testing.assert_allclose(loaded.data, self.expected)
        self.assertEqual(loaded.metadata["num_scans"], 3)
        self.assertTrue(report_path.exists())

    def test_average_matches_direct_mean_for_hdf5_scans(self):
        paths = self._save_scans(".h5")
        output_path = self.directory / "average.npz"
        report_path = self.directory / "report.txt"

        average_scans(paths, output_path, report_path)

        loaded = ImageCube.load(output_path)
        np.testing.assert_allclose(loaded.data, self.expected)

    def test_average_handles_mixed_npz_and_hdf5_scans(self):
        paths = []
        for index, data in enumerate(self.scan_data, start=1):
            suffix = ".npz" if index % 2 else ".h5"
            path = self.directory / f"scan_{index:03d}{suffix}"
            _make_scan_cube(data).save(path)
            paths.append(path)
        output_path = self.directory / "average.npz"
        report_path = self.directory / "report.txt"

        average_scans(paths, output_path, report_path)

        loaded = ImageCube.load(output_path)
        np.testing.assert_allclose(loaded.data, self.expected)

    def test_uses_acquisition_roi_metadata_key(self):
        # Regression test: the original script looked up metadata["roi"],
        # which real ZeroFieldExperiment metadata never sets (only
        # "acquisition_roi"), so it always raised against real data.
        paths = self._save_scans(".npz")
        report_path = self.directory / "report.txt"

        average_scans(paths, self.directory / "average.npz", report_path)  # must not raise

        text = report_path.read_text(encoding="utf-8")
        self.assertIn("ROI used for fluorescence calculation: (0, 0, 2, 2)", text)

    def test_missing_roi_metadata_raises(self):
        paths = []
        for index, data in enumerate(self.scan_data, start=1):
            path = self.directory / f"scan_{index:03d}.npz"
            cube = _make_scan_cube(data)
            del cube.metadata["acquisition_roi"]
            cube.save(path)
            paths.append(path)

        with self.assertRaises(ValueError):
            average_scans(paths, self.directory / "average.npz", self.directory / "report.txt")

    def test_explicit_roi_overrides_metadata(self):
        paths = self._save_scans(".npz")
        report_path = self.directory / "report.txt"

        average_scans(
            paths, self.directory / "average.npz", report_path, roi=(1, 1, 3, 3)
        )

        text = report_path.read_text(encoding="utf-8")
        self.assertIn("ROI used for fluorescence calculation: (1, 1, 3, 3)", text)


class AtomicSaveNpyTests(unittest.TestCase):
    def test_failure_never_leaves_a_file_at_the_final_path(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        final_path = directory / "plane_000.npy"

        class ExplodingArray:
            """Raises inside np.save, simulating a kill mid-write."""

            def __array__(self, dtype=None):
                raise RuntimeError("simulated failure mid-write")

        with self.assertRaises(RuntimeError):
            _atomic_save_npy(final_path, ExplodingArray())

        self.assertFalse(final_path.exists())

    def test_successful_write_is_loadable_at_the_final_path(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        final_path = directory / "plane_000.npy"
        array = np.arange(9, dtype=np.float64).reshape(3, 3)

        _atomic_save_npy(final_path, array)

        np.testing.assert_array_equal(np.load(final_path), array)
        self.assertEqual(list(directory.glob("*.partial")), [])


class AveragePlaneRangeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        rng = np.random.default_rng(1)
        self.scan_data = [rng.random((4, 3, 3)) for _ in range(2)]
        self.paths = []
        for index, data in enumerate(self.scan_data, start=1):
            path = self.directory / f"scan_{index:03d}.npz"
            _make_scan_cube(data, roi=(0, 0, 2, 2)).save(path)
            self.paths.append(path)
        self.expected = np.mean(np.stack(self.scan_data), axis=0)
        self.planes_dir = self.directory / "planes"

    def test_resumable_across_two_invocations(self):
        average_plane_range(self.paths, self.planes_dir, start=0, count=2)
        self.assertTrue((self.planes_dir / "plane_000.npy").exists())
        self.assertTrue((self.planes_dir / "plane_001.npy").exists())
        self.assertFalse((self.planes_dir / "plane_002.npy").exists())

        average_plane_range(self.paths, self.planes_dir, start=0, count=10)

        for index in range(4):
            plane = np.load(self.planes_dir / f"plane_{index:03d}.npy")
            np.testing.assert_allclose(plane, self.expected[index])

    def test_existing_plane_is_not_recomputed(self):
        average_plane_range(self.paths, self.planes_dir, start=0, count=10)
        plane_path = self.planes_dir / "plane_000.npy"
        sentinel = np.full((3, 3), -999.0)
        np.save(plane_path, sentinel)

        average_plane_range(self.paths, self.planes_dir, start=0, count=10)

        np.testing.assert_array_equal(np.load(plane_path), sentinel)


class PackageAveragedPlanesTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        rng = np.random.default_rng(2)
        self.scan_data = [rng.random((3, 4, 4)) for _ in range(2)]
        self.paths = []
        for index, data in enumerate(self.scan_data, start=1):
            path = self.directory / f"scan_{index:03d}.npz"
            _make_scan_cube(data).save(path)
            self.paths.append(path)
        self.planes_dir = self.directory / "planes"
        average_plane_range(self.paths, self.planes_dir, start=0, count=10)
        self.expected = np.mean(np.stack(self.scan_data), axis=0)

    def test_packaged_cube_matches_averaged_planes(self):
        output_path = self.directory / "packaged.npz"

        package_averaged_planes(self.paths, self.planes_dir, output_path)

        loaded = ImageCube.load(output_path)
        np.testing.assert_allclose(loaded.data, self.expected)
        self.assertEqual(loaded.metadata["num_scans"], 2)
        self.assertEqual(
            loaded.metadata["raw_scan_filenames"], [p.name for p in self.paths]
        )

    def test_missing_plane_raises(self):
        (self.planes_dir / "plane_001.npy").unlink()

        with self.assertRaises(RuntimeError):
            package_averaged_planes(
                self.paths, self.planes_dir, self.directory / "packaged.npz"
            )

    def test_report_dir_renders_expected_files(self):
        output_path = self.directory / "packaged.npz"
        report_dir = self.directory / "report"

        package_averaged_planes(self.paths, self.planes_dir, output_path, report_dir)

        self.assertTrue((report_dir / "analysis_report.md").exists())
        self.assertTrue((report_dir / "mean_fluorescence_vs_field.png").exists())
        self.assertTrue((report_dir / "field_images_and_differences.png").exists())

    def test_roi_overrides_full_frame_mean_in_report(self):
        output_path = self.directory / "packaged.npz"
        report_dir = self.directory / "report"

        package_averaged_planes(
            self.paths, self.planes_dir, output_path, report_dir, roi=(0, 0, 2, 2)
        )

        text = (report_dir / "analysis_report.md").read_text(encoding="utf-8")
        self.assertIn(
            "Mean fluorescence was computed over the ROI (0, 0, 2, 2)", text
        )


if __name__ == "__main__":
    unittest.main()
