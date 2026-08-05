"""Background worker for the ZeroFieldExperiment."""

import logging
from pathlib import Path

import numpy as np

from PyQt6.QtCore import QObject, pyqtSignal

from experiments.zero_field_experiment import ZeroFieldExperiment
from framework.fluorescence import mean_fluorescence
from framework.image_cube import merge_hdf5_metadata


LOGGER = logging.getLogger(__name__)


def _is_hdf5_path(filename):
    return Path(filename).suffix.lower() in (".h5", ".hdf5")


def _atomic_save_hdf5(cube, path):
    """Save *cube* to *path* via a temp-file-then-rename so a crash exactly
    mid-write can't corrupt whatever was already durably there before.

    The temp name inserts ``.partial`` before the suffix (``avg.partial.h5``,
    not ``avg.h5.partial``) so it still ends in ``.h5`` -- ImageCube.save()
    dispatches on suffix, and a name ending in ``.partial`` would silently
    route through the .npz writer instead.
    """
    partial_path = path.with_name(f"{path.stem}.partial{path.suffix}")
    cube.save(partial_path)
    partial_path.replace(path)


class ZeroFieldWorker(QObject):
    """Run the existing experiment and forward its live-update callback."""

    live_update_signal = pyqtSignal(float, float, object)
    fluorescence_update_signal = pyqtSignal(object, object)
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
        self._single_scan_direct = False
        self._fields = []
        self._signals = []

    def start(self):
        try:
            streaming = _is_hdf5_path(self.output_path)
            scan_count = (
                self.config.get("num_scans", 1)
                if self.config.get("averaging_enabled", False)
                else 1
            )
            # A single-scan run has nothing to average -- stream straight
            # into the final output path instead of a disposable per-scan
            # file, so there's no redundant second whole-cube write and no
            # duplicate file left on disk.
            self._single_scan_direct = streaming and scan_count == 1
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
                # HDF5 only -- streaming needs random-access writes .npz
                # can't do (see ImageCube.open_streaming_write). A non-.h5
                # destination falls back to exactly today's behavior: no
                # incremental writes anywhere, one save at the very end.
                stream_scan_path=(
                    self._output_path_as_stream_target
                    if self._single_scan_direct
                    else self._raw_scan_path if streaming else None
                ),
                stream_path_is_output=self._single_scan_direct,
            )
            if self._stop_requested:
                self.experiment.stop()
            cube = self.experiment.run()
            if cube.data is not None:
                if self._single_scan_direct:
                    # Pixel data (and scan_complete/planes_written/
                    # stopped_by_user) already reached output_path
                    # incrementally during acquisition; only the metadata
                    # computed after the writer closed (image dimensions,
                    # averaging_enabled, experiment_complete, ...) still
                    # needs to land on disk, so patch it in rather than
                    # rewriting the whole file a second time.
                    merge_hdf5_metadata(self.output_path, dict(cube.metadata))
                elif streaming:
                    _atomic_save_hdf5(cube, self.output_path)
                else:
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
        """Publish the newest stored image's raw fluorescence measurement."""
        # The camera has already applied the acquisition ROI.  Live analysis
        # therefore operates on the received image without software cropping.
        signal = self._compute_live_fluorescence(image)
        self._fields.append(field)
        self._signals.append(signal)
        self.live_update_signal.emit(float(field), float(signal), image)
        self.fluorescence_update_signal.emit(
            np.asarray(self._fields), np.asarray(self._signals)
        )
        self.progress_signal.emit(len(self._fields), self.config["field_points"])

    @staticmethod
    def _compute_live_fluorescence(averaged_image):
        """Return the raw mean fluorescence for one completed field point."""
        return mean_fluorescence(averaged_image)

    def _on_scan_started(self, current, total):
        self._fields = []
        self._signals = []
        self.scan_started_signal.emit(current, total)

    def _on_scan_completed(self, image_cube, current, total):
        if _is_hdf5_path(self.output_path) and not self._single_scan_direct:
            # Persist the running average after every completed scan, not
            # just once at the very end -- closes the gap where several
            # scans finish, then a crash loses all of them because nothing
            # had reached disk yet. Not needed when streaming directly to
            # the final path: that data is already durable on disk
            # incrementally, and this scan is the whole run anyway.
            _atomic_save_hdf5(image_cube, self.output_path)
            LOGGER.info("Running average persisted: %s", self.output_path)
        self.scan_completed_signal.emit(image_cube, current, total)

    def _output_path_as_stream_target(self, scan_index):
        """Stream a single-scan run straight into its final output path."""
        return self.output_path

    def _raw_scan_path(self, scan_index):
        return self.output_path.with_name(
            f"{self.output_path.stem.rsplit('_average', 1)[0]}"
            f"_scan_{scan_index:03d}{self.output_path.suffix}"
        )

    def _save_raw_scan(self, scan_index, image_cube):
        raw_path = self._raw_scan_path(scan_index)
        image_cube.save(raw_path)
        return raw_path.name
