from PyQt6.QtCore import QObject, QTimer, pyqtSignal # type: ignore
import numpy as np # type: ignore
import logging
import time
import csv
from pathlib import Path
from datetime import datetime

from experiments.odmr_experiment import ODMRExperiment
from framework.camera_ownership import exclusive_camera_access


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
            self.experiment.check_mw_power_settling_margin()

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

            for avg_index in range(self.n_avg):

                if not self.running:
                    break

                avg_t0 = time.perf_counter() if self._timing else None

                value, i_off, i_on = self.experiment.acquire_configured_point(
                    f,
                    return_raw=True
                )

                if self._timing:
                    self._collect_point_timing(f, self.freq_index, avg_index, avg_t0)

                values.append(value)
                i_off_values.append(i_off)
                i_on_values.append(i_on)

        except Exception as e:

            msg = (
                f"ODMR acquisition error at "
                f"{f / 1e9:.6f} GHz:\n{e}"
            )

            LOGGER.exception(msg)

            self.running = False

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

        self.results[sorted_index] = avg_value
        self.i_off_results[sorted_index] = avg_i_off
        self.i_on_results[sorted_index] = avg_i_on

        valid = ~np.isnan(self.results)

        self.xdata = (self.freqs_sorted[valid] / 1e9).tolist()
        self.ydata = self.results[valid].tolist()

        self.i_off_data = self.i_off_results[valid].tolist()
        self.i_on_data = self.i_on_results[valid].tolist()

        self.point_signal.emit(
            self.xdata.copy(),
            self.ydata.copy()
        )

        self.freq_index += 1

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
                f"# fire_delay_s={self.config.get('fire_delay_s', 0.005)}",
                f"# reset_delay_s={self.config.get('reset_delay_s', 0.005)}",
                f"# exposure_s={exposure_s}",
                f"# camera_gate_s={self.config.get('camera_gate_s', exposure_s)}",
                f"# mw_settle_s={self.config.get('mw_settle_s', 0.0)}",
                f"# mw_power_settle_s={self.config.get('mw_power_settle_s', 0.0)}",
                f"# pulse_lead_s={self.config.get('pulse_lead_s', 0.002)}",
                f"# pulse_tail_s={self.config.get('pulse_tail_s', 0.002)}",
                f"# camera_overhead_s={self.config.get('camera_overhead_s', 0.35)} "
                "(GUI time-estimate fudge factor, not read by the "
                "acquisition path)",
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

        if self.timer is not None:
            self.timer.stop()

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
