"""Background worker for the ZeroFieldExperiment."""

import logging

import numpy as np

from PyQt6.QtCore import QObject, pyqtSignal

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.fluorescence import mean_fluorescence


LOGGER = logging.getLogger(__name__)


class ZeroFieldWorker(QObject):
    """Run the existing experiment and forward its live-update callback."""

    live_update_signal = pyqtSignal(float, float, object)
    progress_signal = pyqtSignal(int, int)
    scan_started_signal = pyqtSignal(int, int)
    scan_completed_signal = pyqtSignal(object, int, int)
    finished_signal = pyqtSignal(object, object, object, bool)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        hardware_manager,
        camera,
        magnet,
        config,
        acquisition_roi,
        metadata,
        output_path,
    ):
        super().__init__()
        self.hardware_manager = hardware_manager
        self.camera = camera
        self.magnet = magnet
        self.config = config
        self.acquisition_roi = acquisition_roi
        self.metadata = metadata
        self.output_path = output_path
        self.experiment = None
        self._stop_requested = False
        self._fields = []
        self._signals = []

    def start(self):
        try:
            self.experiment = ZeroFieldExperiment(
                hardware_manager=self.hardware_manager,
                camera=self.camera,
                magnet=self.magnet,
                field_start=self.config["field_start"],
                field_stop=self.config["field_stop"],
                field_points=self.config["field_points"],
                field_axis=self.config["field_axis"],
                settling_time_ms=self.config["settling_time_ms"],
                averages=self.config["averages"],
                acquisition_roi=self.acquisition_roi,
                metadata=self.metadata,
                live_update_callback=self._on_live_update,
                scan_started_callback=self._on_scan_started,
                scan_completed_callback=self._on_scan_completed,
                averaging_enabled=self.config.get("averaging_enabled", False),
                num_scans=self.config.get("num_scans", 1),
                save_raw_scans=self.config.get("save_raw_scans", False),
                raw_scan_saver=self._save_raw_scan,
            )
            if self._stop_requested:
                self.experiment.stop()
            cube = self.experiment.run()
            if cube.data is not None:
                cube.save(self.output_path)
                LOGGER.info("Average ImageCube saved: %s", self.output_path)
            self.finished_signal.emit(
                cube,
                np.asarray(self._fields),
                np.asarray(self._signals),
                self._stop_requested,
            )
        except Exception as error:
            self.error_signal.emit(str(error))
            self.finished_signal.emit(
                None, np.asarray(self._fields), np.asarray(self._signals), True
            )

    def stop(self):
        """Thread-safe cancellation flag; no hardware calls occur in the GUI thread."""
        self._stop_requested = True
        if self.experiment is not None:
            self.experiment.stop()

    def _on_live_update(self, field, image):
        """Calculate the transient live-monitor signal outside acquisition."""
        # The camera has already applied the acquisition ROI.  Live analysis
        # therefore operates on the received image without software cropping.
        signal = mean_fluorescence(image)
        self._fields.append(field)
        self._signals.append(signal)
        self.live_update_signal.emit(float(field), float(signal), image)
        self.progress_signal.emit(len(self._fields), self.config["field_points"])

    def _on_scan_started(self, current, total):
        self._fields = []
        self._signals = []
        self.scan_started_signal.emit(current, total)

    def _on_scan_completed(self, image_cube, current, total):
        self.scan_completed_signal.emit(image_cube, current, total)

    def _save_raw_scan(self, scan_index, image_cube):
        raw_path = self.output_path.with_name(
            f"{self.output_path.stem.rsplit('_average', 1)[0]}"
            f"_scan_{scan_index:03d}{self.output_path.suffix}"
        )
        image_cube.save(raw_path)
        return raw_path.name
