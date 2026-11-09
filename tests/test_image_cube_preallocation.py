"""Hardware-free tests for ImageCube pre-allocation (expected_planes).

Without pre-allocation, add() re-concatenates the whole cube per frame --
O(N^2 * frame size) in copies. With expected_planes, the buffer is
allocated once on the first frame and filled in place.
"""

import unittest

import numpy as np

from framework.image_cube import ImageCube
from tests.test_zero_field_timing import make_experiment


def _frame(value, shape=(2, 3)):
    return np.full(shape, float(value))


def _buffer_address(cube):
    return cube._data.__array_interface__["data"][0]


class ImageCubePreallocationTests(unittest.TestCase):
    def test_buffer_is_allocated_once_and_filled_in_place(self):
        cube = ImageCube(data=None, expected_planes=3)
        self.assertIsNone(cube.data)  # shape unknown until the first frame

        cube.add(_frame(1))
        buffer, address = cube._data, _buffer_address(cube)
        self.assertEqual(buffer.shape, (3, 2, 3))
        self.assertEqual(buffer.dtype, np.float64)

        for value in (2, 3):
            cube.add(_frame(value))
            self.assertIs(cube._data, buffer)
            self.assertEqual(_buffer_address(cube), address)

        self.assertEqual(cube._count, 3)
        np.testing.assert_array_equal(
            cube.data, np.stack([_frame(1), _frame(2), _frame(3)])
        )

    def test_data_exposes_only_filled_planes(self):
        cube = ImageCube(data=None, expected_planes=5)
        cube.add(_frame(1))
        cube.add(_frame(2))
        self.assertEqual(cube.data.shape, (2, 2, 3))
        self.assertEqual(cube.shape, (2, 2, 3))
        self.assertTrue(np.shares_memory(cube.data, cube._data))  # view, not copy

    def test_truncate_releases_unused_planes(self):
        cube = ImageCube(data=None, expected_planes=5)
        cube.add(_frame(1))
        cube.add(_frame(2))
        cube.truncate(2)
        self.assertEqual(cube._data.shape, (2, 2, 3))
        self.assertIsNone(cube._data.base)  # owns its memory; buffer released
        np.testing.assert_array_equal(cube.data, np.stack([_frame(1), _frame(2)]))

    def test_truncate_is_noop_when_full_or_not_preallocated(self):
        full = ImageCube(data=None, expected_planes=2)
        full.add(_frame(1))
        full.add(_frame(2))
        buffer = full._data
        full.truncate(2)
        self.assertIs(full._data, buffer)

        plain = ImageCube(data=None)
        plain.add(_frame(1))
        plain.add(_frame(2))
        data = plain.data
        plain.truncate(1)
        self.assertIs(plain.data, data)

    def test_overflow_warns_and_keeps_growing(self):
        cube = ImageCube(data=None, expected_planes=2)
        cube.add(_frame(1))
        cube.add(_frame(2))
        with self.assertLogs("framework.image_cube", level="WARNING"):
            cube.add(_frame(3))
        cube.add(_frame(4))
        np.testing.assert_array_equal(
            cube.data, np.stack([_frame(v) for v in (1, 2, 3, 4)])
        )

    def test_shape_mismatch_still_rejected(self):
        cube = ImageCube(data=None, expected_planes=3)
        cube.add(_frame(1))
        with self.assertRaises(ValueError):
            cube.add(_frame(2, shape=(3, 3)))

    def test_without_expected_planes_behaviour_is_unchanged(self):
        cube = ImageCube(data=None)
        cube.add(_frame(1, shape=(2, 2)))
        cube.add(_frame(2, shape=(2, 2)))
        self.assertIsNone(cube._count)
        self.assertEqual(cube.data.dtype, np.float64)
        self.assertEqual(cube.data.shape, (2, 2, 2))

        uint_cube = ImageCube(data=None)
        uint_cube.add(np.ones((2, 2), dtype=np.uint16))
        self.assertEqual(uint_cube.data.dtype, np.uint16)  # no float64 promotion


class ZeroFieldPreallocationTests(unittest.TestCase):
    def test_complete_sweep_uses_one_preallocated_buffer(self):
        experiment = make_experiment(field_points=4)
        cube = experiment.run()
        self.assertEqual(cube._count, 4)
        self.assertEqual(cube._data.shape[0], 4)
        self.assertEqual(cube.data.shape[0], 4)

    def test_stopped_sweep_is_truncated_to_acquired_frames(self):
        holder = {}

        def stop_after_two(*_):
            if len(holder["experiment"].image_cube.data) == 2:
                holder["experiment"].stop()

        experiment = make_experiment(field_points=6, live_update_callback=stop_after_two)
        holder["experiment"] = experiment
        cube = experiment.run()

        self.assertEqual(cube.data.shape[0], 2)
        self.assertEqual(cube._data.shape[0], 2)  # unused planes released
        self.assertEqual(len(cube.scan_axis_values), 2)
        self.assertEqual(len(cube.metadata["field_values_gauss"]), 2)


if __name__ == "__main__":
    unittest.main()
