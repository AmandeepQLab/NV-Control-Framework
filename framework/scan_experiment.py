"""Reusable acquisition engine for experiments that sweep a scan axis."""

import time

import numpy as np

from framework.base_experiment import BaseExperiment
from framework.image_cube import ImageCube


class ScanExperiment(BaseExperiment):
    """Base class for experiments that acquire one frame at each scan point.

    Subclasses provide the hardware-specific hooks.  :meth:`run` owns the
    common lifecycle and returns ``(scan_vector, signal)`` for compatibility
    with the existing experiment runner.
    """

    scan_axis_name = "scan"
    scan_axis_unit = ""

    def __init__(self, hardware, config=None):
        super().__init__(hardware, config)
        self.config = {} if config is None else config
        self.scan_vector = np.asarray([])
        self.settling_time_ms = self.config.get("settling_time_ms", 0)
        self.image_cube = ImageCube(
            data=None,
            scan_axis_name=self.scan_axis_name,
            scan_axis_unit=self.scan_axis_unit,
            scan_axis_values=[],
        )
        self.scan_signal = []

    def setup_scan(self):
        """Prepare scan hardware.  Override when preparation is required."""

    def set_scan_point(self, value):
        """Set hardware to *value*.  Override in scan implementations."""
        raise NotImplementedError

    def acquire_frame(self):
        """Acquire one frame at the current scan point.  Override in subclasses."""
        raise NotImplementedError

    def process_frame(self, frame):
        """Convert a frame to its live signal; by default return the frame sum."""
        return np.asarray(frame).sum()

    def cleanup_scan(self):
        """Release scan-specific resources.  Override when required."""

    def emit_live_update(self, value, signal):
        """Hook for UI integrations; intentionally a no-op by default."""

    def emit_progress(self):
        """Hook for UI integrations; intentionally a no-op by default."""

    def run(self):
        self.state = self.RUNNING
        self.start_time = time.time()
        self.scan_signal = []
        self.image_cube = ImageCube(
            data=None,
            scan_axis_name=self.scan_axis_name,
            scan_axis_unit=self.scan_axis_unit,
            scan_axis_values=[],
        )

        try:
            self.setup_scan()

            for value in self.scan_vector:
                if self.stop_requested or not self.running:
                    break

                self.set_scan_point(value)

                if self.settling_time_ms > 0:
                    time.sleep(self.settling_time_ms / 1000.0)

                frame = self.acquire_frame()
                self.image_cube.add(frame)

                signal = self.process_frame(frame)
                self.scan_signal.append(signal)
                self.emit_live_update(value, signal)
                self.emit_progress()

            return self.scan_vector, np.asarray(self.scan_signal)

        except Exception:
            self.state = self.ERROR
            raise

        finally:
            self.end_time = time.time()
            try:
                self.cleanup_scan()
            finally:
                self.cleanup()
