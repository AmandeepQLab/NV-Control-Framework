import config
from experiments.odmr_experiment import ODMRExperiment
import numpy as np # type: ignore
import time

from PyQt6.QtWidgets import QMainWindow, QMessageBox # type: ignore
from PyQt6.QtCore import QThread, pyqtSignal # type: ignore

from gui.panels.odmr_panel import ODMRPanel
from gui.odmr_worker import ODMRWorker
from data.data_manager import DataManager
from gui.pulse_sequence_window import PulseSequenceWindow

from analysis.odmr_fit import fit_single_lorentzian, fit_double_lorentzian


# =====================================================
# ESTIMATE-ONLY CAMERA/VISA CONSTANTS
# =====================================================
# Measured facts about this rig's camera/VISA hardware, not user-tunable
# ODMR parameters -- no spinbox, no config key. Derived as medians pooled
# across all 23 data/odmr_timing_*.csv timing-diagnostics recordings
# (2026-08-06 through 2026-08-09; see estimate_odmr_time() below for how
# each is used).
#
# Calibration caveat: all 23 source recordings predate the MW
# switch-gating change (2026-08-10) and so include a per-frame VISA
# set_power() write (~1.5-1.6ms) that acquire_triggered_frame() no longer
# performs. CAMERA_PER_FRAME_OVERHEAD_S below is therefore ~1.5ms/frame
# higher than current-code reality -- estimates will run slightly high
# (roughly 2 * repeats * n_points * averages * 1.5ms per scan, well under
# 1% of a typical scan's total) until constants are re-derived from a
# post-switch-gating recording.

# camera.{trigger_mode_set,flush_pre,queue_buffer,acquisition_start} (arm)
# + camera.{acquisition_stop,flush_post} (disarm), once per scan.
CAMERA_ARM_DISARM_S = 0.160

# visa_set_frequency + visa_rf_on + visa_set_power_initial (SG386 VISA
# calls in ODMRExperiment.set_scan_point()), once per (point, average).
VISA_POINT_OVERHEAD_S = 0.0102

# load_sequence + reset_outputs (Pulse Streamer RPCs) + pulse.run() RPC +
# sensor readout (wait_buffer minus exposure/trigger_delay/fire_delay) +
# ~1.2ms unaccounted host/thread scheduling overhead (thread creation,
# Python call overhead), once per triggered frame (2 * repeats per point).
CAMERA_PER_FRAME_OVERHEAD_S = 0.0132


class ODMRWindow(QMainWindow):

    stop_requested = pyqtSignal()

    def __init__(self, hardware, camera, acquisition_state_getter):
        super().__init__()

        self.setWindowTitle("ODMR Experiment")
        self.resize(900, 600)

        self.hardware = hardware
        self.camera = camera
        self.acquisition_state_getter = acquisition_state_getter

        self.data_manager = DataManager()

        self.latest_x = None
        self.latest_y = None
        self.current_config = None
        self.scan_start_time = None

        self.odmr_worker = None
        self.odmr_thread = None
        self.odmr_running = False

        self.panel = ODMRPanel()
        self.setCentralWidget(self.panel)

        self.panel.run_stop_button.clicked.connect(self.toggle_odmr)
        self.panel.sequence_button.clicked.connect(self.show_pulse_sequence)
        self.panel.plot_mode_combo.currentTextChanged.connect(self.refresh_plot)
        self.panel.fit_button.clicked.connect(self.fit_esr)

    # =====================================================
    # RUN / STOP TOGGLE
    # =====================================================

    def toggle_odmr(self):

        if self.odmr_running:
            self.stop_odmr()
        else:
            self.run_odmr()

    # =====================================================
    # DISPLAY PULSE SEQUENCE
    # =====================================================
    def show_pulse_sequence(self):

        config = self.panel.get_config()
        acquisition_state = self.acquisition_state_getter()
        config["exposure_s"] = acquisition_state.exposure_s

        from experiments.odmr_experiment import ODMRExperiment

        experiment = ODMRExperiment(
            self.hardware,
            config,
            acquisition_roi=acquisition_state.acquisition_roi,
        )

        repeats = config.get(
            "repeats",
            1
        )

        sequence = experiment.build_repeated_off_on_sequence(
            repeats
        )

        self.sequence_window = PulseSequenceWindow(
            sequence=sequence,
            channel_map=self.hardware["channels"],
            parent=self
        )

        self.sequence_window.show()

    # =====================================================
    # RUN ODMR
    # =====================================================

    def run_odmr(self):

        acquisition_state = self.acquisition_state_getter()
        acquisition_roi = acquisition_state.acquisition_roi

        config = self.panel.get_config()
        config["exposure_s"] = acquisition_state.exposure_s
        config["baseline_counts"] = acquisition_state.baseline_counts

        self.current_config = config.copy()

        self.latest_x = None
        self.latest_y = None

        self.scan_start_time = time.time()

        estimated_s = self.estimate_odmr_time(config)
        self.panel.update_time_label(
            estimated_s=estimated_s,
            actual_s=None
        )

        self.odmr_running = True
        self.panel.set_running_state(True)

        self.panel.reset_plot()
        self.panel.fit_button.setEnabled(False)
        self.panel.update_progress(0, config["steps"])
        self.panel.update_frequency(config["f_start"] / 1e9)

        self.panel.plot.enableAutoRange(False)
        self.panel.plot.setXRange(
            config["f_start"] / 1e9,
            config["f_stop"] / 1e9,
            padding=0
        )
        self.panel.plot.enableAutoRange(axis="y", enable=True)

        self.odmr_thread = QThread()

        self.odmr_worker = ODMRWorker(
            self.hardware,
            config,
            acquisition_roi=acquisition_roi,
            estimated_s=estimated_s,
        )

        self.odmr_worker.moveToThread(self.odmr_thread)

        self.stop_requested.connect(self.odmr_worker.stop)

        self.odmr_thread.started.connect(self.odmr_worker.start)

        self.odmr_worker.point_signal.connect(self.store_latest_data)
        self.odmr_worker.finished_signal.connect(self.odmr_finished)
        self.odmr_worker.finished_signal.connect(self.odmr_thread.quit)

        self.odmr_worker.error_signal.connect(self.show_error)
        self.odmr_worker.progress_signal.connect(self.panel.update_progress)
        self.odmr_worker.frequency_signal.connect(self.panel.update_frequency)

        self.odmr_thread.start()

    # =====================================================
    # STOP ODMR
    # =====================================================

    def stop_odmr(self):

        if self.odmr_worker is not None:
            self.stop_requested.emit()

    # =====================================================
    # STORE DATA DURING SCAN
    # =====================================================

    def store_latest_data(self, x, y):

        self.latest_x = x
        self.latest_y = y

    # =====================================================
    # ESTIMATE ODMR TIME
    # =====================================================

    def estimate_odmr_time(self, config):

        coarse = np.linspace(
            config["f_start"],
            config["f_stop"],
            config["steps"]
        )

        freq_list = [coarse]

        if config.get("use_dense_region", False):

            dense1 = np.linspace(
                config["dense1_center"] - config["dense1_width"] / 2,
                config["dense1_center"] + config["dense1_width"] / 2,
                config["dense1_steps"]
            )

            dense1 = dense1[
                (dense1 >= config["f_start"])
                & (dense1 <= config["f_stop"])
            ]

            freq_list.append(dense1)

            if config.get("num_dense_dips", 1) == 2:

                dense2 = np.linspace(
                    config["dense2_center"] - config["dense2_width"] / 2,
                    config["dense2_center"] + config["dense2_width"] / 2,
                    config["dense2_steps"]
                )

                dense2 = dense2[
                    (dense2 >= config["f_start"])
                    & (dense2 <= config["f_stop"])
                ]

                freq_list.append(dense2)

        n_points = len(
            np.unique(
                np.concatenate(freq_list)
            )
        )

        averages = config.get("averages", 1)
        repeats = config.get("repeats", 1)

        exposure_s = config.get("exposure_s", 0.02)
        trigger_delay_s = config.get("trigger_delay_s", 0.05)
        fire_delay_s = config.get("fire_delay_s", 0.005)
        reset_delay_s = config.get("reset_delay_s", 0.005)
        mw_settle_s = config.get("mw_settle_s", 0.0)
        pulse_lead_s = config.get("pulse_lead_s", 0.002)
        # pulse_tail_s deliberately not read here -- nothing in the
        # acquisition path waits for it (see build_sequence()/
        # acquire_triggered_frame() in ODMRExperiment).

        # Each triggered frame (acquire_triggered_frame()) pays
        # reset_delay_s + fire_delay_s once, then blocks on
        # grab_external_frame(), which itself waits out the sequence's own
        # baked-in trigger_delay_s + pulse_lead_s wait (build_sequence()'s
        # camera_t = trigger_delay_ns + pulse_lead_ns), then exposure_s,
        # then sensor readout -- all once per frame, and there are
        # 2 * repeats frames (OFF + ON) per point-average, not one.
        #
        # NOTE: at trigger_delay_s < 0.02s, roughly 1 frame in 9 hits a
        # reproducible ~400ms stall not modeled here at all -- see
        # "Known hardware quirks (ODMR timing)" in CLAUDE.md. This
        # formula is a best-estimate for the typical (non-stalled) case;
        # it will run 15-30% low for scans configured that way.
        frame_wall_s = (
            reset_delay_s + fire_delay_s + trigger_delay_s + pulse_lead_s
            + exposure_s + CAMERA_PER_FRAME_OVERHEAD_S
        )

        # set_scan_point() pays its VISA overhead + mw_settle_s once per
        # (point, average) -- not once per frame.
        time_per_point_avg = (
            VISA_POINT_OVERHEAD_S + mw_settle_s + 2 * repeats * frame_wall_s
        )

        return CAMERA_ARM_DISARM_S + n_points * averages * time_per_point_avg

    # =====================================================
    # REFRESH PLOT
    # =====================================================

    def refresh_plot(self):

        if self.latest_x is None or self.latest_y is None:
            return

        mode = self.panel.get_plot_mode()

        if mode == "Mean I_on / I_off":

            if self.odmr_worker is None:
                return

            i_off = self.odmr_worker.i_off_data
            i_on = self.odmr_worker.i_on_data

            self.panel.update_plot_raw(
                self.latest_x,
                i_on,
                i_off
            )

        else:

            self.panel.update_plot_normalized(
                self.latest_x,
                self.latest_y
            )

    # =====================================================
    # FIT ESR
    # =====================================================

    def fit_esr(self):

        if self.latest_x is None or self.latest_y is None:
            QMessageBox.warning(
                self,
                "Fit Error",
                "No ODMR data available."
            )
            return

        try:

            fit_type = self.panel.get_fit_type()

            if fit_type == "Single Lorentzian":
                fit = fit_single_lorentzian(
                    self.latest_x,
                    self.latest_y
                )
            else:
                fit = fit_double_lorentzian(
                    self.latest_x,
                    self.latest_y
                )

            self.panel.update_fit_results(fit)

            self.refresh_plot()

            self.panel.plot.plot(
                self.latest_x,
                fit["fit_y"],
                pen="c",
                width=3
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Fit Error",
                str(e)
            )

    # =====================================================
    # FINISHED
    # =====================================================

    def odmr_finished(self):

        self.odmr_running = False
        self.panel.set_running_state(False)

        self.refresh_plot()
        self.panel.fit_button.setEnabled(True)

        if self.scan_start_time is not None:
            actual_s = time.time() - self.scan_start_time
        else:
            actual_s = None

        if self.current_config is not None:
            estimated_s = self.estimate_odmr_time(self.current_config)
        else:
            estimated_s = None

        self.panel.update_time_label(
            estimated_s=estimated_s,
            actual_s=actual_s
        )

        if (
            self.current_config is not None
            and self.current_config.get("save_data", True)
            and self.latest_x is not None
            and self.latest_y is not None
        ):

            freqs_hz = np.array(self.latest_x) * 1e9
            signal = np.array(self.latest_y)

            i_off = np.array(self.odmr_worker.i_off_data)
            i_on = np.array(self.odmr_worker.i_on_data)

            # Camera's genuinely read-back exposure at scan start (see
            # ODMRExperiment.configure_acquisition()), alongside the
            # requested exposure_s already in current_config -- lets the
            # real quantisation/drift magnitude be seen from saved data
            # rather than assumed.
            actual_exposure_s = getattr(
                self.odmr_worker.experiment, "actual_exposure_s", None
            )
            if actual_exposure_s is not None:
                self.current_config["camera_exposure_actual_s"] = actual_exposure_s

            self.data_manager.save_odmr(
                freqs_hz,
                signal,
                self.current_config,
                i_off=i_off,
                i_on=i_on
            )

    # =====================================================
    # ERROR MESSAGE
    # =====================================================

    def show_error(self, message):

        QMessageBox.critical(
            self,
            "ODMR Error",
            message
        )

    # =====================================================
    # CLOSE GUI WARNING
    # =====================================================

    def closeEvent(self, event):

        running = (
            self.odmr_worker is not None
            and getattr(self.odmr_worker, "running", False)
        )

        if running:

            msg = (
                "An ODMR scan is currently running.\n\n"
                "Are you sure you want to stop the scan and close the ODMR GUI?"
            )

        else:

            msg = (
                "Are you sure you want to close the ODMR GUI?"
            )

        reply = QMessageBox.question(
            self,
            "Close ODMR",
            msg,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:

            try:
                if self.odmr_worker is not None:
                    self.stop_requested.emit()
            except Exception:
                pass

            event.accept()

        else:

            event.ignore()
