from PyQt6.QtCore import QObject, QTimer, pyqtSignal # type: ignore
import numpy as np # type: ignore
import logging
import time
import csv
from pathlib import Path
from datetime import datetime

from experiments.odmr_experiment import ODMRExperiment
from framework.camera_ownership import exclusive_camera_access
from framework.image_cube import ImageCube


LOGGER = logging.getLogger(__name__)


class ODMRWorker(QObject):

    point_signal = pyqtSignal(object, object)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    frequency_signal = pyqtSignal(float)

    def __init__(self, hardware, config, acquisition_roi=None, estimated_s=None):
        super().__init__()

        self.hardware = hardware
        self.config = config
        self.acquisition_roi = acquisition_roi
        self._estimated_s = estimated_s

        coarse = np.linspace(
            config["f_start"],
            config["f_stop"],
            config["steps"]
        )

        freq_list = [coarse]

        if config.get("use_dense_region", False):

            # Dip 1
            center = config["dense1_center"]
            width = config["dense1_width"]
            steps = config["dense1_steps"]

            dense1 = np.linspace(
                center - width / 2,
                center + width / 2,
                steps
            )

            dense1 = dense1[
                (dense1 >= config["f_start"])
                & (dense1 <= config["f_stop"])
            ]

            freq_list.append(dense1)

            # Dip 2
            if config.get("num_dense_dips", 1) == 2:

                center = config["dense2_center"]
                width = config["dense2_width"]
                steps = config["dense2_steps"]

                dense2 = np.linspace(
                    center - width / 2,
                    center + width / 2,
                    steps
                )

                dense2 = dense2[
                    (dense2 >= config["f_start"])
                    & (dense2 <= config["f_stop"])
                ]

                freq_list.append(dense2)

        self.freqs_sorted = np.unique(
            np.concatenate(freq_list)
        )
                        

        self.order = np.random.permutation(len(self.freqs_sorted))
        self.freqs = self.freqs_sorted[self.order]

        self.results = np.full(len(self.freqs_sorted), np.nan)
        self.i_off_results = np.full(len(self.freqs_sorted), np.nan)
        self.i_on_results = np.full(len(self.freqs_sorted), np.nan)

        self.n_avg = config.get("averages", 1)
        self.average_index = 0
        self.completed_averages = 0
        self._stop_requested = False
        self._error_message = None
        self._is_finished = False
        self._scalar_counts = np.zeros(len(self.freqs_sorted), dtype=np.int64)
        self.n_repeats_per_average = []
        self._repeat_counts_for_average = []
        self._invalid_pixel_counts = [
            [None] * len(self.freqs_sorted) for _ in range(self.n_avg)
        ]
        self._repaired_pixel_counts = [
            [None] * len(self.freqs_sorted) for _ in range(self.n_avg)
        ]
        self.pixel_data = None
        self._pixel_shape = None

        self.running = True
        self.freq_index = 0

        self.xdata = []
        self.ydata = []

        self.i_off_data = []
        self.i_on_data = []

        self.experiment = ODMRExperiment(
            self.hardware,
            self.config,
            acquisition_roi=self.acquisition_roi,
        )

        self.timer = None
        self._camera_lease = None

        # Timing diagnostics: mirrors ODMRExperiment's flag so the worker
        # only pays instrumentation cost (dict building, list growth) when
        # the experiment itself has it enabled. See ODMRExperiment.
        self._timing = self.experiment.timing_enabled()
        self._timing_records = []
        self._scan_t0 = None
        self._acquisition_counts = (None, None)

    def start(self):
        try:
            self._camera_lease = exclusive_camera_access(self.hardware["camera"])
            self._camera_lease.__enter__()
            self.experiment.configure_acquisition()
            self.experiment.reset_baseline_warning_state()

            if self._timing:
                self.experiment.reset_timing()
                self.experiment.set_camera_timing(True)
                self._timing_records = []
                self._scan_t0 = time.perf_counter()

            # Arm the camera once for the whole scan (see
            # ODMRExperiment.begin_camera_acquisition). Unconditional --
            # this is the actual speedup, not diagnostic-gated. Counts
            # reset first so get_acquisition_counts() at scan end reports
            # this scan's arms only, not a running total across scans.
            self.experiment.reset_acquisition_counts()
            self.experiment.begin_camera_acquisition()
            self._collect_scan_level_timing()

            self.timer = QTimer()
            self.timer.timeout.connect(self.acquire_next_frequency)
            self.timer.start(10)
        except Exception as error:
            self.error_signal.emit(str(error))
            self._finish()

    def stop(self):

        self._stop_requested = True
        self.running = False
        self.experiment.stop()

        if self.timer is not None:
            self.timer.stop()

        self._finish()

    def _collect_scan_level_timing(self):
        """Drain any experiment-level timing entries not tied to a
        specific point/average (currently: the arm/disarm stages recorded
        by begin_/end_camera_acquisition)."""
        if not self._timing:
            return
        for rec in self.experiment.pop_timing_log():
            row = dict(rec)
            row["point_index"] = None
            row["freq_hz"] = None
            row["avg_index"] = None
            self._timing_records.append(row)

    def acquire_next_frequency(self):

        if not self.running:
            self._finish()
            return

        if self.freq_index >= len(self.freqs):
            self._finish()
            return

        f = self.freqs[self.freq_index]
        sorted_index = self.order[self.freq_index]
        self.progress_signal.emit(self.freq_index + 1, len(self.freqs))
        self.frequency_signal.emit(f / 1e9)

        values = []
        i_off_values = []
        i_on_values = []

        point_t0 = time.perf_counter() if self._timing else None

        try:

            avg_t0 = time.perf_counter() if self._timing else None
            (value, pixel_mean, pixel_sd, repeats_done,
             invalid_count, repaired_count) = (
                self.experiment.acquire_pixel_statistics(f)
            )
            if self._timing:
                self._collect_point_timing(
                    f, self.freq_index, self.average_index, avg_t0
                )
            if self.pixel_data is None:
                self._pixel_shape = pixel_mean.shape
                self.pixel_data = np.full(
                    (self.n_avg, 2, len(self.freqs_sorted), *self._pixel_shape),
                    np.nan, dtype=np.float32,
                )
            elif pixel_mean.shape != self._pixel_shape:
                raise ValueError("ODMR pixel frame dimensions changed during acquisition.")
            sorted_index = self.order[self.freq_index]
            self._record_pixel_repair_counts(
                self.average_index, sorted_index, invalid_count, repaired_count
            )
            self.pixel_data[self.average_index, 0, sorted_index] = pixel_mean
            self.pixel_data[self.average_index, 1, sorted_index] = pixel_sd
            self._repeat_counts_for_average.append(int(repeats_done))
            values.append(value)
            i_off_values.append(self.experiment._last_i_off)
            i_on_values.append(self.experiment._last_i_on)

        except Exception as e:

            msg = (
                f"ODMR acquisition error at "
                f"{f / 1e9:.6f} GHz:\n{e}"
            )

            LOGGER.exception(msg)

            self.running = False
            self._error_message = str(e)
            diagnostics = getattr(
                self.experiment, "last_pixel_repair_diagnostics", None
            )
            if diagnostics is not None:
                self._record_pixel_repair_counts(
                    self.average_index,
                    self.order[self.freq_index],
                    diagnostics.get("invalid_pixels_encountered", 0),
                    diagnostics.get("repaired_pixels", 0),
                )

            self.error_signal.emit(msg)

            self._finish()

            return

        if self._timing:
            self._timing_records.append({
                "point_index": self.freq_index,
                "freq_hz": f,
                "avg_index": None,
                "repeat_index": None,
                "frame": None,
                "stage": "point_total",
                "t_start_rel_s": point_t0 - self._scan_t0,
                "duration_s": time.perf_counter() - point_t0,
            })

        if len(values) == 0:
            self._finish()
            return

        avg_value = float(np.mean(values))
        avg_i_off = float(np.mean(i_off_values))
        avg_i_on = float(np.mean(i_on_values))

        count = int(self._scalar_counts[sorted_index])
        self._scalar_counts[sorted_index] += 1
        self.results[sorted_index] = (
            avg_value if count == 0 else self.results[sorted_index]
            + (avg_value - self.results[sorted_index]) / (count + 1)
        )
        self.i_off_results[sorted_index] = (
            avg_i_off if count == 0 else self.i_off_results[sorted_index]
            + (avg_i_off - self.i_off_results[sorted_index]) / (count + 1)
        )
        self.i_on_results[sorted_index] = (
            avg_i_on if count == 0 else self.i_on_results[sorted_index]
            + (avg_i_on - self.i_on_results[sorted_index]) / (count + 1)
        )

        valid = ~np.isnan(self.results)

        self.xdata = (self.freqs_sorted[valid] / 1e9).tolist()
        self.ydata = self.results[valid].tolist()

        self.i_off_data = self.i_off_results[valid].tolist()
        self.i_on_data = self.i_on_results[valid].tolist()

        self.freq_index += 1
        if self.freq_index >= len(self.freqs):
            if len(set(self._repeat_counts_for_average)) != 1:
                raise RuntimeError(
                    "Repeat count varied within an average; per-average repeat "
                    "metadata cannot represent this sweep."
                )
            self.completed_averages += 1
            self.n_repeats_per_average.append(self._repeat_counts_for_average[0])
            self._repeat_counts_for_average = []
            self.average_index += 1
            self.freq_index = 0
        self.point_signal.emit(self.xdata.copy(), self.ydata.copy())
        if self.average_index >= self.n_avg:
            self._finish()

    def _make_statistics_cube(self):
        if self.pixel_data is None:
            self.statistics_cube = None
            return
        # A partially acquired sweep is intentionally excluded: each leading
        # plane is a completed, independently swept average. If a frame shape
        # is known but no sweep completed, retain a zero-length leading axis
        # so the incomplete status and zero completed count can still persist.
        completed = self.pixel_data[:self.completed_averages]
        camera = self.hardware.get("camera")
        metadata = dict(self.config)
        metadata.update({
            "experiment_name": "ODMR",
            "configured_averages": int(self.n_avg),
            "completed_averages": int(self.completed_averages),
            "n_averages": int(self.completed_averages),
            "configured_repeats": int(self.config.get("repeats", 1)),
            "n_repeats_per_average": list(self.n_repeats_per_average),
            "frequency_values_hz": self.freqs_sorted.tolist(),
            "frequency_acquisition_order_hz": self.freqs.tolist(),
            "frequency_acquisition_order_indices": self.order.tolist(),
            "normalization_method": "100 * (ON - baseline_counts) / (OFF - baseline_counts), per pixel per repeat",
            "experiment_complete": bool(self.completed_averages == self.n_avg),
            "stopped_by_user": bool(self._stop_requested),
            "interrupted": bool(self._error_message is not None or self._stop_requested),
            "error_message": self._error_message,
            "image_height_px": int(self._pixel_shape[0]),
            "image_width_px": int(self._pixel_shape[1]),
            "image_dtype": "float32",
            "acquisition_roi": self.acquisition_roi,
            "camera_binning": getattr(camera, "binning", None),
            "camera_model": type(camera).__name__ if camera is not None else None,
            "camera_exposure_actual_s": getattr(self.experiment, "actual_exposure_s", None),
            "invalid_pixel_counts_per_average_frequency": self._invalid_pixel_counts,
            "repaired_pixel_counts_per_average_frequency": self._repaired_pixel_counts,
            "invalid_pixels_encountered": sum(
                count for row in self._invalid_pixel_counts
                for count in row if count is not None
            ),
            "repaired_pixels": sum(
                count for row in self._repaired_pixel_counts
                for count in row if count is not None
            ),
        })
        axes = {
            "axis_order": ["average", "statistic", "frequency", "y", "x"],
            "average": list(range(self.completed_averages)),
            "statistic": ["normalized_mean", "normalized_sd"],
            "frequency": self.freqs_sorted.tolist(),
            "y": list(range(self._pixel_shape[0])),
            "x": list(range(self._pixel_shape[1])),
        }
        self.statistics_cube = ImageCube(
            data=completed,
            axes=axes,
            metadata=metadata,
            experiment_type="ODMR",
            scan_axis_name="frequency",
            scan_axis_unit="Hz",
            scan_axis_values=self.freqs_sorted,
        )

    def _record_pixel_repair_counts(self, average_index, frequency_index, invalid, repaired):
        self._invalid_pixel_counts[average_index][frequency_index] = int(invalid)
        self._repaired_pixel_counts[average_index][frequency_index] = int(repaired)

    def _collect_point_timing(self, freq_hz, point_index, avg_index, avg_t0):
        """Drain the experiment's per-call timing log, tag, and merge it.

        Draining after every average iteration (rather than once at scan
        end) is what keeps the experiment- and camera-side logs bounded --
        each holds at most one iteration's worth of entries at a time.
        """
        for rec in self.experiment.pop_timing_log():
            row = dict(rec)
            row["point_index"] = point_index
            row["freq_hz"] = freq_hz
            row["avg_index"] = avg_index
            self._timing_records.append(row)

        self._timing_records.append({
            "point_index": point_index,
            "freq_hz": freq_hz,
            "avg_index": avg_index,
            "repeat_index": None,
            "frame": None,
            "stage": "avg_iteration_total",
            "t_start_rel_s": avg_t0 - self._scan_t0,
            "duration_s": time.perf_counter() - avg_t0,
        })

    def _write_timing_file(self):
        """Write the accumulated timing log once, after the scan is over.

        Called only from _finish(), never mid-scan -- file I/O never
        happens inside a timed region.
        """
        if not self._timing_records:
            return

        try:
            base_dir = Path("data")
            base_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = base_dir / f"odmr_timing_{timestamp}.csv"

            scan_total_s = (
                time.perf_counter() - self._scan_t0
                if self._scan_t0 is not None
                else None
            )
            exposure_s = self.config.get("exposure_s", 0.02)

            header_lines = [
                "# ODMR timing diagnostics (temporary instrumentation)",
                f"# generated={datetime.now().isoformat()}",
                f"# f_start_hz={self.config.get('f_start')}",
                f"# f_stop_hz={self.config.get('f_stop')}",
                f"# steps={self.config.get('steps')}",
                f"# averages={self.n_avg}",
                f"# repeats={self.config.get('repeats', 1)}",
                f"# mw_power_dbm={self.config.get('mw_power_dbm', -10)}",
                f"# trigger_delay_s={self.config.get('trigger_delay_s', 0.05)}",
                f"# fire_delay_s={self.config.get('fire_delay_s', 0.010)}",
                f"# reset_delay_s={self.config.get('reset_delay_s', 0.010)}",
                f"# exposure_s={exposure_s}",
                f"# camera_gate_s={self.config.get('camera_gate_s', exposure_s)}",
                f"# mw_settle_s={self.config.get('mw_settle_s', 0.0)}",
                f"# pulse_lead_s={self.config.get('pulse_lead_s', 0.002)}",
                f"# pulse_tail_s={self.config.get('pulse_tail_s', 0.002)}",
                f"# estimated_s={self._estimated_s}",
                f"# measured_scan_total_s={scan_total_s}",
                "# camera.wait_buffer duration includes trigger wait + "
                "exposure + sensor readout; the vendor SDK does not expose "
                "a way to split those further.",
                f"# acquisition_start_count={self._acquisition_counts[0]} "
                "(actual AcquisitionStart calls this scan -- 1 expected "
                "under whole-scan held-open acquisition; a value equal to "
                "the frame count means it silently fell back to arming "
                "per frame)",
                f"# acquisition_stop_count={self._acquisition_counts[1]}",
            ]

            fieldnames = [
                "point_index", "freq_hz", "avg_index", "repeat_index",
                "frame", "stage", "t_start_rel_s", "duration_s",
            ]

            with open(path, "w", newline="") as fh:
                for line in header_lines:
                    fh.write(line + "\n")

                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()

                for row in self._timing_records:
                    writer.writerow({k: row.get(k) for k in fieldnames})

                writer.writerow({
                    "point_index": "",
                    "freq_hz": "",
                    "avg_index": "",
                    "repeat_index": "",
                    "frame": "",
                    "stage": "scan_total",
                    "t_start_rel_s": 0.0,
                    "duration_s": scan_total_s,
                })

            LOGGER.info("ODMR timing diagnostics written to %s", path)

        except Exception:
            LOGGER.exception("Failed to write ODMR timing diagnostics")

    def _finish(self):

        if self._is_finished:
            return
        self._is_finished = True

        if self.timer is not None:
            self.timer.stop()

        self._make_statistics_cube()

        # Disarm before anything else, in particular before the camera
        # lease below is released -- that release is what lets live view
        # or Zero Field touch the camera next, so the held-open
        # acquisition (if any) must already be closed by this point on
        # every exit path (normal completion, an exception mid-scan, or
        # stop() -- all reach _finish()). Never skipped, timing or not.
        try:
            self.experiment.end_camera_acquisition()
        except Exception:
            LOGGER.exception("Failed to close ODMR camera acquisition")

        if self._timing:
            self._collect_scan_level_timing()
            self._acquisition_counts = self.experiment.get_acquisition_counts()
            self.experiment.set_camera_timing(False)
            self._write_timing_file()

        try:
            if self._camera_lease is not None:
                self._camera_lease.__exit__(None, None, None)
                self._camera_lease = None
        finally:
            self.finished_signal.emit()
