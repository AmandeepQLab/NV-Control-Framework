from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QGroupBox, QCheckBox,
    QComboBox, QProgressBar, QTextEdit, QDialog, QScrollArea
)
from PyQt6.QtCore import Qt

import pyqtgraph as pg
import numpy as np
from PyQt6.QtGui import QFontDatabase


class ODMRStatisticsExplanationDialog(QDialog):
    """Readable explanation of the ODMR statistics hierarchy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ODMR Statistics — How are these calculated?")
        self.resize(720, 680)

        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        text.setPlainText(_ODMR_STATISTICS_EXPLANATION)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(text)


_ODMR_STATISTICS_EXPLANATION = """What are we measuring?

Each ODMR frequency point is measured repeatedly. A complete frequency sweep is then repeated independently several times:

    Repeats within an average
            -> mean + SD for that average
            -> independent averages
            -> final ODMR mean

Fast noise

Repeat-to-repeat fluctuations within one average represent fast noise. For average a:

    s_a = sample SD of repeats
    N_a = number of repeats
    v_fast,a = s_a^2 / N_a

Slow noise

Average-to-average variation contains both genuine slower fluctuations and residual fast sampling noise in each average mean. For equal-weight averages, the slow variance estimate is:

    slow_variance = max(observed_between_average_variance
                        - mean(v_fast,a), 0)

This is a transparent method-of-moments estimator and can be noisy when only a few independent averages are available.

Final mean

The primary scientific result is the equal-weight combination of completed average means:

    final_mean = mean(mean_a)

This is intentionally not inverse-variance weighted.

Combined uncertainty

For A usable independent averages:

    fast_sem = sqrt(sum(v_fast,a) / A^2)
    slow_sem = slow_sd / sqrt(A)
    combined_sem = sqrt(fast_sem^2 + slow_sem^2)

Numerical example

Consider 5 independent averages with 20 repeats per average, average means 0.95, 1.05, 0.97, 1.04, and 0.99, and within-average SD approximately 0.10. The equal-weight final mean is approximately 1.000. Within-average fluctuations illustrate fast noise; variation between the five average means contains the slower component after accounting for fast sampling noise. This example is conceptual, not a precision claim.

Why do we calculate this?

The final mean ODMR dataset is the primary result used for subsequent ODMR fitting and magnetic-image extraction.

Fast/slow SD and SEM quantities are quality and uncertainty diagnostics. They help determine whether additional repeats or additional independent averages are likely to be useful.

The magnetic image will ultimately be obtained from the spatially resolved final ODMR mean, not from the uncertainty maps."""


class ODMRPanel(QWidget):

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        self.control_box = QGroupBox("ODMR Parameters")

        odmr_layout = QVBoxLayout()
        self.control_box.setLayout(odmr_layout)

        self.control_scroll_area = QScrollArea()
        self.control_scroll_area.setWidgetResizable(True)
        self.control_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.control_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.control_scroll_area.setWidget(self.control_box)
        main_layout.addWidget(self.control_scroll_area, 1)

        self.fstart_spin = self._add_double(odmr_layout, "Start (GHz):", 0, 10, 6, 2.77)
        self.fstop_spin = self._add_double(odmr_layout, "Stop (GHz):", 0, 10, 6, 2.97)
        self.steps_spin = self._add_int(odmr_layout, "Steps:", 2, 100000, 20)

        self.dense_check = QCheckBox("Add dense points near ESR dip(s)")
        self.dense_check.setChecked(False)
        self.dense_check.toggled.connect(self.toggle_dense_section)
        odmr_layout.addWidget(self.dense_check)

        self.dense_group = QGroupBox("Dense ESR Region")
        dense_layout = QVBoxLayout()
        self.dense_group.setLayout(dense_layout)
        odmr_layout.addWidget(self.dense_group)

        dip_layout = QHBoxLayout()
        dip_layout.addWidget(QLabel("Number of dips:"))

        self.num_dips_combo = QComboBox()
        self.num_dips_combo.addItems(["1", "2"])
        self.num_dips_combo.currentTextChanged.connect(self.toggle_dip2)

        dip_layout.addWidget(self.num_dips_combo)
        dense_layout.addLayout(dip_layout)

        self.dip1_group = QGroupBox("Dip 1")
        dip1_layout = QVBoxLayout()
        self.dip1_group.setLayout(dip1_layout)
        dense_layout.addWidget(self.dip1_group)

        self.dense1_center_spin = self._add_double(dip1_layout, "Dip 1 center (GHz):", 0, 10, 6, 2.87)
        self.dense1_width_spin = self._add_double(dip1_layout, "Dip 1 width (MHz):", 0.1, 500, 2, 20)
        self.dense1_steps_spin = self._add_int(dip1_layout, "Dip 1 steps:", 2, 100000, 30)

        self.dip2_group = QGroupBox("Dip 2")
        dip2_layout = QVBoxLayout()
        self.dip2_group.setLayout(dip2_layout)
        dense_layout.addWidget(self.dip2_group)

        self.dense2_center_spin = self._add_double(dip2_layout, "Dip 2 center (GHz):", 0, 10, 6, 2.90)
        self.dense2_width_spin = self._add_double(dip2_layout, "Dip 2 width (MHz):", 0.1, 500, 2, 20)
        self.dense2_steps_spin = self._add_int(dip2_layout, "Dip 2 steps:", 2, 100000, 30)

        self.dense_group.setVisible(False)
        self.dip2_group.setVisible(False)

        self.avg_spin = self._add_int(odmr_layout, "Averages:", 1, 100000, 1)
        self.avg_spin.setToolTip(
            "Number of full point re-acquisitions per frequency, each "
            "producing one contrast value; these are averaged together.\n"
            "Outer loop -- see Repeats for the averaging that happens "
            "inside each one."
        )
        self.repeat_spin = self._add_int(odmr_layout, "Repeats:", 1, 100000, 1)
        self.repeat_spin.setToolTip(
            "Number of OFF/ON frame pairs acquired and averaged into one "
            "contrast value per point, before Averages (above) repeats the "
            "whole thing.\n"
            "Inner loop."
        )
        self.mw_power_spin = self._add_double(odmr_layout, "MW Power (dBm):", -100, 20, 1, -10)

        # =====================================================
        # DELAYS SECTION
        # =====================================================

        self.delays_check = QCheckBox("Show Delays")
        self.delays_check.setChecked(False)
        self.delays_check.toggled.connect(self.toggle_delays_section)
        odmr_layout.addWidget(self.delays_check)

        self.delays_group = QGroupBox("Delays")
        delays_layout = QVBoxLayout()
        self.delays_group.setLayout(delays_layout)
        odmr_layout.addWidget(self.delays_group)

        self.trigger_delay_spin = self._add_double(
            delays_layout, "Camera Trigger Delay (s):", 0.0, 10.0, 4, 0.02
        )
        self.trigger_delay_spin.setToolTip(
            "Delay before the first camera trigger pulse.\n"
            "Recommended: 0.02 s.\n"
            "Below ~0.02 s, ~1 frame in 9 stalls ~400 ms (root cause "
            "unconfirmed). Rig testing at 0/5/20/150 ms found no "
            "measurable contrast difference -- this delay doesn't affect "
            "ESR contrast, only scan-time predictability."
        )

        self.fire_delay_spin = self._add_double(
            delays_layout, "Fire Delay (s):", 0.0, 10.0, 4, 0.010
        )
        self.fire_delay_spin.setToolTip(
            "Delay before Pulse Streamer fires after reset.\n"
            "Recommended: 0.010 s. Measured: below 0.010 s, pulse.run() "
            "intermittently blocks 300-1000 ms (a Swabian RPC stall, not "
            "camera arming -- arming now happens once per scan, not per "
            "frame). 0.010 s eliminated the stalls entirely in testing and "
            "was faster overall than 0.005 s once the stall tail is counted."
        )

        self.reset_delay_spin = self._add_double(
            delays_layout, "Reset Delay (s):", 0.0, 10.0, 4, 0.010
        )
        self.reset_delay_spin.setToolTip(
            "Delay after resetting Swabian digital outputs to a clean "
            "baseline before acquisition (the magnet polarity relay is "
            "preserved at its commanded state, not zeroed).\n"
            "Recommended: 0.010 s. Measured: below 0.010 s, pulse.run() "
            "intermittently blocks 300-1000 ms (the Swabian needs recovery "
            "time before accepting a new streamed sequence). 0.010 s "
            "eliminated the stalls entirely in testing and was faster "
            "overall than 0.005 s once the stall tail is counted."
        )

        self.mw_settle_spin = self._add_double(
            delays_layout, "MW Settle (s):", 0.0, 10.0, 4, 0.0
        )
        self.mw_settle_spin.setToolTip(
            "Delay after setting the SG386's frequency, before acquisition "
            "-- frequency settling only. Unrelated to MW on/off gating, "
            "which is done by the fast switch (Pulse Streamer channel 2), "
            "not this delay.\n"
            "Recommended: 0.0 s currently. Use 0.05–0.1 s only if ESR disappears after frequency jumps."
        )

        self.pulse_lead_spin = self._add_double(
            delays_layout,
            "Pulse Lead (s):", 0.0, 1.0, 4, 0.002
        )
        self.pulse_lead_spin.setToolTip(
            "Laser and MW switch on this long before the camera trigger "
            "fires.\n"
            "Recommended: 0.002 s.\n"
            "A sweep at 2/1/0.5/0.2/0 ms found no measurable contrast "
            "dependence, though the lower values overlap the trigger-delay "
            "stall regime (see Camera Trigger Delay), so that comparison "
            "isn't fully clean."
        )

        self.pulse_tail_spin = self._add_double(
            delays_layout,
            "Pulse Tail (s):", 0.0, 1.0, 4, 0.002
        )
        self.pulse_tail_spin.setToolTip(
            "Laser and MW remain ON this long after the camera exposure "
            "ends, within the pulse sequence itself.\n"
            "Recommended: 0.002 s.\n"
            "Costs no wall-clock scan time -- nothing waits on it; it only "
            "extends how long the sequence holds these channels on "
            "hardware."
        )
        self.delays_group.setVisible(False)

        self.save_check = QCheckBox("Save data")
        self.save_check.setChecked(False)
        odmr_layout.addWidget(self.save_check)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Plot mode:"))

        self.plot_mode_combo = QComboBox()
        self.plot_mode_combo.addItems(["Normalized ESR", "Mean I_on / I_off"])

        mode_layout.addWidget(self.plot_mode_combo)
        odmr_layout.addLayout(mode_layout)

        fit_layout = QHBoxLayout()
        fit_layout.addWidget(QLabel("Fit type:"))

        self.fit_type_combo = QComboBox()
        self.fit_type_combo.addItems(["Single Lorentzian", "Double Lorentzian"])

        fit_layout.addWidget(self.fit_type_combo)
        odmr_layout.addLayout(fit_layout)

        self.fit_button = QPushButton("Fit Lorentzian")
        self.fit_button.setEnabled(False)
        odmr_layout.addWidget(self.fit_button)

        self.fit_group = QGroupBox("Fit Results")
        fit_result_layout = QVBoxLayout()
        self.fit_group.setLayout(fit_result_layout)

        self.fit_label = QLabel(
            "f0 = --\n"
            "FWHM = --\n"
            "Contrast = --\n"
            "SNR = --\n"
            "R² = --"
        )

        fit_result_layout.addWidget(self.fit_label)
        odmr_layout.addWidget(self.fit_group)

        self.statistics_group = QGroupBox("ODMR Statistics")
        statistics_layout = QVBoxLayout(self.statistics_group)
        self.statistics_summary_label = QLabel("Statistics: No completed acquisition")
        self.statistics_summary_label.setWordWrap(True)
        statistics_layout.addWidget(self.statistics_summary_label)
        self.statistics_details_button = QPushButton("How are these calculated?")
        statistics_layout.addWidget(self.statistics_details_button)
        odmr_layout.addWidget(self.statistics_group)
        self.statistics_dialog = None
        self.statistics_details_button.clicked.connect(self.show_statistics_explanation)

        self.run_stop_button = QPushButton("Run ODMR")
        self.run_stop_button.setStyleSheet(
            "background-color: green; color: white; font-weight: bold;"
        )
        odmr_layout.addWidget(self.run_stop_button)

        self.sequence_button = QPushButton("Show Pulse Sequence")
        odmr_layout.addWidget(self.sequence_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Progress: 0 / 0")
        self.progress_bar.setTextVisible(True)
        odmr_layout.addWidget(self.progress_bar)

        self.freq_label = QLabel("Current frequency: -- GHz")
        odmr_layout.addWidget(self.freq_label)

        self.time_label = QLabel("Time: estimated -- | actual --")
        odmr_layout.addWidget(self.time_label)

        odmr_layout.addStretch()

        plot_box = QGroupBox("ODMR Plot")
        main_layout.addWidget(plot_box, 4)

        plot_layout = QVBoxLayout()
        plot_box.setLayout(plot_layout)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Normalized Fluorescence (%)")
        self.plot.setLabel("bottom", "Microwave Frequency (GHz)")
        self.plot.showGrid(x=True, y=True)
        plot_layout.addWidget(self.plot)

        self.curve = self.plot.plot([], [], pen="y", symbol="o", symbolSize=6)

    def set_statistics_status(self, message):
        self.statistics_summary_label.setText(message)

    def update_statistics(self, result):
        """Display processor-provided availability and final-mean validity."""
        n_averages = result.n_averages
        repeats = result.n_repeats_per_average
        if repeats and len(set(repeats)) == 1:
            repeat_text = str(repeats[0])
        else:
            repeat_text = ", ".join(str(value) for value in repeats) or "--"

        final_mean_available = bool(np.isfinite(result.final_mean).any())
        fast_available = bool(np.isfinite(result.fast_sd).any()) and bool(
            np.isfinite(result.fast_sem).any()
        )
        slow_available = bool(np.isfinite(result.slow_sd).any()) and bool(
            np.isfinite(result.slow_sem).any()
        )
        combined_available = bool(np.isfinite(result.combined_sem).any())
        total = int(result.final_mean.size)
        valid = int(np.isfinite(result.final_mean).sum())
        valid_percent = 100.0 * valid / total if total else 0.0
        slow_estimable = bool(result.slow_noise_estimable) and bool(
            np.asarray(result.slow_noise_estimable_per_pixel).any()
        )
        slow_status = (
            "Estimable" if slow_estimable
            else "Not estimable (<2 usable averages)"
        )
        self.statistics_summary_label.setText(
            f"Completed averages: {n_averages}\n"
            f"Repeats / average: {repeat_text}\n"
            f"Final mean: {'Available' if final_mean_available else 'Unavailable'}\n"
            f"Fast noise: {'Available' if fast_available else 'Unavailable'}\n"
            f"Slow noise: {slow_status}\n"
            f"Combined uncertainty: {'Available' if combined_available else 'Unavailable'}\n"
            f"Valid data: {valid_percent:.1f}% ({valid}/{total})"
        )

    def show_statistics_explanation(self):
        if self.statistics_dialog is None:
            self.statistics_dialog = ODMRStatisticsExplanationDialog(self)
        self.statistics_dialog.show()
        self.statistics_dialog.raise_()
        self.statistics_dialog.activateWindow()

    def set_running_state(self, running):
        if running:
            self.run_stop_button.setText("Stop ODMR")
            self.run_stop_button.setStyleSheet(
                "background-color: red; color: white; font-weight: bold;"
            )
        else:
            self.run_stop_button.setText("Run ODMR")
            self.run_stop_button.setStyleSheet(
                "background-color: green; color: white; font-weight: bold;"
            )

    def toggle_dense_section(self, checked):
        self.dense_group.setVisible(checked)

    def toggle_delays_section(self, checked):
        self.delays_group.setVisible(checked)

    def toggle_dip2(self, text):
        self.dip2_group.setVisible(text == "2")

    def _add_double(self, layout, label, min_val, max_val, decimals, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))

        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(decimals)
        spin.setValue(default)

        row.addWidget(spin)
        layout.addLayout(row)

        return spin

    def _add_int(self, layout, label, min_val, max_val, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))

        spin = QSpinBox()
        spin.setRange(min_val, max_val)
        spin.setValue(default)

        row.addWidget(spin)
        layout.addLayout(row)

        return spin

    def get_config(self):
        return {
            "f_start": self.fstart_spin.value() * 1e9,
            "f_stop": self.fstop_spin.value() * 1e9,
            "steps": self.steps_spin.value(),
            "averages": self.avg_spin.value(),
            "mw_power_dbm": self.mw_power_spin.value(),
            "trigger_delay_s": self.trigger_delay_spin.value(),
            "save_data": self.save_check.isChecked(),
            "repeats": self.repeat_spin.value(),

            "use_dense_region": self.dense_check.isChecked(),
            "num_dense_dips": int(self.num_dips_combo.currentText()),
            "dense1_center": self.dense1_center_spin.value() * 1e9,
            "dense1_width": self.dense1_width_spin.value() * 1e6,
            "dense1_steps": self.dense1_steps_spin.value(),
            "dense2_center": self.dense2_center_spin.value() * 1e9,
            "dense2_width": self.dense2_width_spin.value() * 1e6,
            "dense2_steps": self.dense2_steps_spin.value(),

            "fire_delay_s": self.fire_delay_spin.value(),
            "reset_delay_s": self.reset_delay_spin.value(),
            "mw_settle_s": self.mw_settle_spin.value(),
            "pulse_lead_s": self.pulse_lead_spin.value(),
            "pulse_tail_s": self.pulse_tail_spin.value(),
        }

    def get_plot_mode(self):
        return self.plot_mode_combo.currentText()

    def get_fit_type(self):
        return self.fit_type_combo.currentText()

    def update_time_label(self, estimated_s=None, actual_s=None):
        est_text = "--" if estimated_s is None else self._format_time(estimated_s)
        act_text = "--" if actual_s is None else self._format_time(actual_s)

        self.time_label.setText(
            f"Time: estimated {est_text} | actual {act_text}"
        )

    def _format_time(self, seconds):
        seconds = int(round(seconds))

        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60

        if h > 0:
            return f"{h} h {m} min {s} s"

        if m > 0:
            return f"{m} min {s} s"

        return f"{s} s"

    def update_fit_results(self, fit):
        if fit["fit_type"] == "single":
            self.fit_label.setText(
                f"Single Lorentzian\n"
                f"f0 = {fit['f0']:.6f} GHz\n"
                f"FWHM = {1000 * fit['fwhm']:.3f} MHz\n"
                f"Contrast = {fit['contrast']:.3f} %\n"
                f"SNR = {fit['snr']:.2f}\n"
                f"R² = {fit['r2']:.5f}\n"
                f"RMSE = {fit['rmse']:.4f}"
            )
        else:
            self.fit_label.setText(
                f"Double Lorentzian\n"
                f"f01 = {fit['f01']:.6f} GHz\n"
                f"FWHM1 = {1000 * fit['fwhm1']:.3f} MHz\n"
                f"Contrast1 = {fit['contrast1']:.3f} %\n"
                f"SNR1 = {fit['snr1']:.2f}\n\n"
                f"f02 = {fit['f02']:.6f} GHz\n"
                f"FWHM2 = {1000 * fit['fwhm2']:.3f} MHz\n"
                f"Contrast2 = {fit['contrast2']:.3f} %\n"
                f"SNR2 = {fit['snr2']:.2f}\n\n"
                f"R² = {fit['r2']:.5f}\n"
                f"RMSE = {fit['rmse']:.4f}"
            )

    def update_plot_normalized(self, x, y):
        self.plot.clear()
        self.plot.setLabel("left", "Normalized Fluorescence (%)")
        self.plot.setLabel("bottom", "Microwave Frequency (GHz)")
        self.plot.showGrid(x=True, y=True)
        self.plot.plot(x, y, pen="y", symbol="o", symbolSize=6)

    def update_plot_raw(self, x, i_on, i_off):
        self.plot.clear()
        self.plot.setLabel("left", "Mean Fluorescence (counts/pixel)")
        self.plot.setLabel("bottom", "Microwave Frequency (GHz)")
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        self.plot.plot(x, i_on, pen="g", symbol="o", symbolSize=6, name="Mean I_on")
        self.plot.plot(x, i_off, pen="r", symbol="o", symbolSize=6, name="Mean I_off")

    def update_plot(self, x, y):
        self.update_plot_normalized(x, y)

    def reset_plot(self):
        self.plot.clear()

    def update_progress(self, current, total):
        percent = 0 if total <= 0 else int(100 * current / total)
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"Progress: {current} / {total}")

    def update_frequency(self, freq_ghz):
        self.freq_label.setText(f"Current frequency: {freq_ghz:.6f} GHz")
