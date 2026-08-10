from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QGroupBox, QCheckBox,
    QComboBox, QProgressBar
)

import pyqtgraph as pg


class ODMRPanel(QWidget):

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        control_box = QGroupBox("ODMR Parameters")
        main_layout.addWidget(control_box, 1)

        odmr_layout = QVBoxLayout()
        control_box.setLayout(odmr_layout)

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
        self.repeat_spin = self._add_int(odmr_layout, "Repeats:", 1, 100000, 1)
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
            "Reducing below 0.02 s may decrease in ESR contrast."
        )

        self.fire_delay_spin = self._add_double(
            delays_layout, "Fire Delay (s):", 0.0, 10.0, 4, 0.005
        )
        self.fire_delay_spin.setToolTip(
            "Delay after camera arming before Pulse Streamer fires.\n"
            "Recommended: 0.005 s. Ensures the camera is ready before trigger."
        )

        self.reset_delay_spin = self._add_double(
            delays_layout, "Reset Delay (s):", 0.0, 10.0, 4, 0.005
        )
        self.reset_delay_spin.setToolTip(
            "Delay after forcing Swabian outputs LOW before acquisition.\n"
            "Recommended: 0.005 s. Prevents false camera triggers."
        )

        self.mw_settle_spin = self._add_double(
            delays_layout, "MW Settle (s):", 0.0, 10.0, 4, 0.0
        )
        self.mw_settle_spin.setToolTip(
            "Delay after setting MW frequency before acquisition.\n"
            "Recommended: 0.0 s currently. Use 0.05–0.1 s only if ESR disappears after frequency jumps."
        )

        self.pulse_lead_spin = self._add_double(
            delays_layout,
            "Pulse Lead (s):", 0.0, 1.0, 4, 0.002
        )
        self.pulse_lead_spin.setToolTip(
            "Laser and MW start before the camera trigger.\n"
            "Recommended: 0.002 s.\n"
            "This fixed ESR loss at 10 ms exposure."
        )

        self.pulse_tail_spin = self._add_double(
            delays_layout,
            "Pulse Tail (s):", 0.0, 1.0, 4, 0.002
        )
        self.pulse_tail_spin.setToolTip(
            "Laser and MW remain ON after the camera exposure.\n"
            "Recommended: 0.002 s.\n"
            "Provides safe overlap between illumination/MW and camera exposure."
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
