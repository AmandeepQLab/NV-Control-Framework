"""Microwave-free widefield fluorescence scan over a magnetic-field axis."""

import copy

import numpy as np

from framework.scan_experiment import ScanExperiment
from framework.camera_ownership import exclusive_camera_access
from framework.image_cube import ImageCube


class ZeroFieldExperiment(ScanExperiment):
    """Acquire fluorescence images while sweeping one magnetic-field axis.

    Field inputs and the ImageCube scan axis are expressed in gauss.  The
    existing :class:`Magnet` API uses millitesla internally, so values are
    converted only at the hardware boundary.
    """

    scan_axis_name = "Magnetic Field"
    scan_axis_unit = "G"

    def __init__(
        self,
        hardware_manager,
        camera,
        magnet,
        field_start,
        field_stop,
        field_points,
        field_axis,
        settling_time_ms=0,
        averages=1,
        roi=None,
        metadata=None,
        live_update_callback=None,
        scan_started_callback=None,
        scan_completed_callback=None,
        averaging_enabled=False,
        num_scans=1,
        save_raw_scans=False,
    ):
        config = {
            "settling_time_ms": settling_time_ms,
        }
        hardware = {
            "camera": camera,
            "magnet": magnet,
        }
        super().__init__(hardware, config)

        axis = field_axis.upper()
        if axis not in ("X", "Y", "Z"):
            raise ValueError("field_axis must be 'X', 'Y', or 'Z'.")
        if field_points < 1:
            raise ValueError("field_points must be at least 1.")
        if averages < 1:
            raise ValueError("averages must be at least 1.")
        if not isinstance(averaging_enabled, bool):
            raise ValueError("averaging_enabled must be a boolean.")
        if not isinstance(num_scans, int) or isinstance(num_scans, bool) or num_scans < 1:
            raise ValueError("num_scans must be an integer greater than or equal to 1.")
        if not isinstance(save_raw_scans, bool):
            raise ValueError("save_raw_scans must be a boolean.")

        self.hardware_manager = hardware_manager
        self.camera = camera
        self.magnet = magnet
        self.field_start = field_start
        self.field_stop = field_stop
        self.field_points = field_points
        self.field_axis = axis
        self.averages = averages
        self.metadata = {} if metadata is None else dict(metadata)
        self.live_update_callback = live_update_callback
        self.scan_started_callback = scan_started_callback
        self.scan_completed_callback = scan_completed_callback
        self.averaging_enabled = averaging_enabled
        self.num_scans = num_scans
        self.save_raw_scans = save_raw_scans
        self.raw_scans = []
        self._configured_field_mT = None
        self.latest_field = None
        self.latest_image = None

    def setup_scan(self):
        # Keep all supplies enabled for the full sweep.  In particular, the
        # two non-swept axes remain at zero current but must not be repeatedly
        # power-cycled by their per-axis zero-field safety path.
        self.magnet.enable()

        self.scan_vector = np.linspace(
            self.field_start,
            self.field_stop,
            self.field_points,
        )
        self.image_cube.scan_axis_name = self.scan_axis_name
        self.image_cube.scan_axis_unit = self.scan_axis_unit
        self.image_cube.scan_axis_values = self.scan_vector
        self.image_cube.metadata = dict(self.metadata)

        # Preserve the currently configured values on the two axes outside
        # the scan.  Magnet.get_vector() returns values in millitesla.
        self._configured_field_mT = self.magnet.get_vector()

    def cleanup_scan(self):
        """Return the magnet to its existing safe disabled state after a scan."""
        self.magnet.disable()

    def set_scan_point(self, value):
        fields_mT = dict(self._configured_field_mT)
        fields_mT[self.field_axis.lower()] = value * 0.1
        self.magnet.set_vector(
            bx=fields_mT["x"],
            by=fields_mT["y"],
            bz=fields_mT["z"],
        )

    def acquire_frame(self):
        frames = [self.camera.snap() for _ in range(self.averages)]
        return np.mean(np.stack(frames), axis=0)

    def process_frame(self, frame):
        """Retain the frame for the live GUI; final analysis is external."""
        self.latest_image = frame
        return None

    def emit_live_update(self, value, _signal):
        self.latest_field = value

        if self.live_update_callback is not None:
            self.live_update_callback(value, self.latest_image)

    def run(self):
        """Execute one or more sweeps and return their averaged ImageCube."""
        with exclusive_camera_access(self.camera):
            scan_count = self.num_scans if self.averaging_enabled else 1
            averaged_cube = None
            completed_scans = 0

            for scan_index in range(1, scan_count + 1):
                if self.stop_requested or not self.running:
                    break

                if self.scan_started_callback is not None:
                    self.scan_started_callback(scan_index, scan_count)

                super().run()
                acquired_cube = self.image_cube

                if acquired_cube.data is None:
                    if averaged_cube is None:
                        averaged_cube = acquired_cube
                    break

                completed_scans += 1
                if self.save_raw_scans:
                    self.raw_scans.append(acquired_cube)

                if averaged_cube is None:
                    averaged_cube = (
                        acquired_cube
                        if scan_count == 1
                        else self._new_averaged_cube(acquired_cube)
                    )
                else:
                    self._update_running_average(
                        averaged_cube.data, acquired_cube.data, completed_scans
                    )

                self._set_averaging_metadata(averaged_cube, completed_scans)
                if self.scan_completed_callback is not None:
                    self.scan_completed_callback(
                        averaged_cube, scan_index, scan_count
                    )

            if averaged_cube is not None:
                self._set_averaging_metadata(averaged_cube, completed_scans)
                self.image_cube = averaged_cube
        return self.image_cube

    def _new_averaged_cube(self, acquired_cube):
        return ImageCube(
            data=np.array(acquired_cube.data, dtype=np.float64, copy=True),
            axes=copy.deepcopy(acquired_cube.axes),
            metadata=copy.deepcopy(acquired_cube.metadata),
            experiment_type=acquired_cube.experiment_type,
            scan_axis_name=acquired_cube.scan_axis_name,
            scan_axis_unit=acquired_cube.scan_axis_unit,
            scan_axis_values=copy.deepcopy(acquired_cube.scan_axis_values),
        )

    @staticmethod
    def _update_running_average(average, current, scan_count):
        """Update ``average`` in place without retaining prior scan images."""
        average += (np.asarray(current, dtype=np.float64) - average) / scan_count

    def _set_averaging_metadata(self, image_cube, completed_scans):
        metadata = dict(image_cube.metadata)
        metadata.update(
            {
                "averaging_enabled": self.averaging_enabled,
                "num_scans": self.num_scans,
                "save_raw_scans": self.save_raw_scans,
                "completed_scans": completed_scans,
            }
        )
        if self.save_raw_scans:
            metadata.setdefault("raw_scan_filenames", [])
        image_cube.metadata = metadata
