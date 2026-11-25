from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QGroupBox, QCheckBox,
    QComboBox, QProgressBar, QTextEdit, QDialog, QScrollArea, QLineEdit,
    QFormLayout, QDialogButtonBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal

import pyqtgraph as pg
import numpy as np
from PyQt6.QtGui import QFontDatabase

from gui.widgets import run_button
from gui.widgets.run_button import style_run_button
from gui.widgets.stability_check_group import StabilitySettingsGroup, StabilityWaitBar
from gui.widgets.odmr_magnet_field_group import MagnetFieldGroup
from gui.widgets.collapsible_section import CollapsibleSection
from gui import style

# Moved to a shared module (also used by the ODMR analysis window);
# re-exported here so existing imports keep working.
from gui.widgets.initial_fit_parameters_dialog import InitialFitParametersDialog  # noqa: F401


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

    analysis_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # Left column, as in the Zero Field window: the run/stop button
        # fixed at the top, and every other control in a vertically
        # scrolling area below it, so the button stays visible at any
        # window height.
        self.left_panel = QWidget()
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.left_panel, 1)

        self.run_stop_button = QPushButton()
        self.set_run_button_state(run_button.IDLE)
        left_layout.addWidget(self.run_stop_button)

        # Progress bar, current-frequency and time labels fixed directly
        # below the run/stop button, outside the scroll area, so they stay
        # visible next to the button.
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Progress: 0 / 0")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setToolTip(
            "Acquired points out of the scan's total (frequencies x averages).")
        left_layout.addWidget(self.progress_bar)

        self.freq_label = QLabel("Current frequency: -- GHz")
        self.freq_label.setToolTip("Microwave frequency being measured now, in GHz.")
        left_layout.addWidget(self.freq_label)

        self.time_label = QLabel("Time: estimated -- | actual --")
        self.time_label.setToolTip(
            "Estimated scan duration from the settings, and the time actually "
            "taken (shown when the scan ends).")
        left_layout.addWidget(self.time_label)

        # The field held during the current/last scan (set at run start).
        self.field_label = QLabel("Field: --")
        self.field_label.setWordWrap(True)
        self.field_label.setToolTip(
            "Magnetic field held at the diamond during the current or last "
            "scan, from \"Magnetic field during scan\". Commanded or model "
            "values in gauss, not a measurement."
        )
        left_layout.addWidget(self.field_label)

        # Scroll content: cards and collapsible sections (gui/style.py,
        # gui/widgets/collapsible_section.py). Folding a section only hides
        # its controls; get_config() reads them as before.
        self.control_box = QWidget()
        odmr_layout = QVBoxLayout(self.control_box)
        odmr_layout.setContentsMargins(0, 0, 4, 0)
        odmr_layout.setSpacing(style.COLUMN_SPACING)

        self.control_scroll_area = QScrollArea()
        self.control_scroll_area.setWidgetResizable(True)
        self.control_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.control_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.control_scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.control_scroll_area.setWidget(self.control_box)
        left_layout.addWidget(self.control_scroll_area, 1)

        # Stability-check wait line, fixed below the scroll area; shown
        # only while waiting for stable counts.
        self.stability_wait_bar = StabilityWaitBar()
        left_layout.addWidget(self.stability_wait_bar)

        # ---- Scan (always open) -------------------------------------------
        self.scan_group, scan_form = style.card("Scan")
        odmr_layout.addWidget(self.scan_group)
        self.fstart_spin = self._add_double(
            scan_form, "Start:", 0, 10, 6, 2.77, "GHz",
            "First microwave frequency of the sweep, in GHz.")
        self.fstop_spin = self._add_double(
            scan_form, "Stop:", 0, 10, 6, 2.97, "GHz",
            "Last microwave frequency of the sweep, in GHz.")
        self.steps_spin = self._add_int(
            scan_form, "Steps:", 2, 100000, 20,
            "Number of evenly spaced frequencies from Start to Stop. Dense "
            "points (if on) are added to these.")
        self.avg_spin = self._add_int(
            scan_form, "Averages:", 1, 100000, 1,
            "Number of independent full sweeps over every frequency. The "
            "result is their equal-weight mean; with 2 or more, the slow "
            "(sweep-to-sweep) noise can be estimated, and the plot shows the "
            "running mean after each completed sweep. Outer loop -- see "
            "Repeats for the averaging inside each point.")
        self.repeat_spin = self._add_int(
            scan_form, "Repeats:", 1, 100000, 1,
            "Number of OFF/ON frame pairs acquired and averaged at each "
            "frequency within one sweep (inner loop). More repeats lower the "
            "fast noise of each point.")
        self.mw_power_spin = self._add_double(
            scan_form, "MW power:", -100, 20, 1, -10, "dBm",
            "SG386 output power for the whole scan, in dBm. MW on/off is "
            "gated by the fast switch, not by this power.")
        self.save_check = QCheckBox("Save data")
        self.save_check.setChecked(False)
        self.save_check.setToolTip(
            "Save the scan (config.json, the spectrum and odmr_statistics.h5) "
            "in a new data/odmr_<date>_<time> folder when it finishes.")
        scan_form.addRow(self.save_check)

        # ---- Dense points near ESR dips (on/off in the header) -----------
        self.dense_check = QCheckBox("On")
        self.dense_check.setChecked(False)
        self.dense_check.setToolTip(
            "Add dense points near ESR dip(s): extra frequencies concentrated "
            "around one or two dips, merged with the evenly spaced ones. "
            "Use it to resolve narrow lines without a long scan.")
        self.dense_section = CollapsibleSection(
            "Dense points near ESR dips", checkbox=self.dense_check,
            settings_key="odmr/dense")
        odmr_layout.addWidget(self.dense_section)
        dense_form = self.dense_section.body_layout()
        self.num_dips_combo = QComboBox()
        self.num_dips_combo.addItems(["1", "2"])
        self.num_dips_combo.setToolTip("Number of dips to add dense points around.")
        dense_form.addRow(self._tipped("Number of dips:", self.num_dips_combo),
                          self.num_dips_combo)
        self.dense1_center_spin = self._add_double(
            dense_form, "Dip 1 centre:", 0, 10, 6, 2.87, "GHz",
            "Centre frequency of the first dense region, in GHz.")
        self.dense1_width_spin = self._add_double(
            dense_form, "Dip 1 width:", 0.1, 500, 2, 20, "MHz",
            "Full width of the first dense region around its centre, in MHz.")
        self.dense1_steps_spin = self._add_int(
            dense_form, "Dip 1 steps:", 2, 100000, 30,
            "Number of dense frequencies across the first region.")
        self.dense2_center_spin = self._add_double(
            dense_form, "Dip 2 centre:", 0, 10, 6, 2.90, "GHz",
            "Centre frequency of the second dense region, in GHz. Used only "
            "with 2 dips.")
        self.dense2_width_spin = self._add_double(
            dense_form, "Dip 2 width:", 0.1, 500, 2, 20, "MHz",
            "Full width of the second dense region, in MHz. Used only with "
            "2 dips.")
        self.dense2_steps_spin = self._add_int(
            dense_form, "Dip 2 steps:", 2, 100000, 30,
            "Number of dense frequencies across the second region. Used only "
            "with 2 dips.")
        self._dense_fields = (
            self.num_dips_combo, self.dense1_center_spin, self.dense1_width_spin,
            self.dense1_steps_spin, self.dense2_center_spin,
            self.dense2_width_spin, self.dense2_steps_spin,
        )
        self.num_dips_combo.currentTextChanged.connect(self.toggle_dip2)
        self.dense_check.toggled.connect(self._update_dense_enabled)
        self.toggle_dip2(self.num_dips_combo.currentText())
        self._update_dense_enabled()
        self.dense_section.set_summary_provider(self._dense_summary)

        # ---- Magnetic field during scan (folded; summary spells it out) ---
        self.magnet_field_group = MagnetFieldGroup()
        odmr_layout.addWidget(self.magnet_field_group)

        # ---- Delays -------------------------------------------------------
        self.delays_section = CollapsibleSection(
            "Delays", settings_key="odmr/delays",
            tooltip="Show or hide the timing delays. The defaults are the "
                    "measured recommendations; change them only for a reason.")
        odmr_layout.addWidget(self.delays_section)
        delays_form = self.delays_section.body_layout()
        self.trigger_delay_spin = self._add_double(
            delays_form, "Camera trigger delay:", 0.0, 10.0, 4, 0.02, "s",
            "Delay before the first camera trigger pulse.\n"
            "Recommended: 0.02 s.\n"
            "Below ~0.02 s, ~1 frame in 9 stalls ~400 ms (root cause "
            "unconfirmed). Rig testing at 0/5/20/150 ms found no "
            "measurable contrast difference -- this delay doesn't affect "
            "ESR contrast, only scan-time predictability.")
        self.fire_delay_spin = self._add_double(
            delays_form, "Fire delay:", 0.0, 10.0, 4, 0.010, "s",
            "Delay before Pulse Streamer fires after reset.\n"
            "Recommended: 0.010 s. Measured: below 0.010 s, pulse.run() "
            "intermittently blocks 300-1000 ms (a Swabian RPC stall, not "
            "camera arming -- arming now happens once per scan, not per "
            "frame). 0.010 s eliminated the stalls entirely in testing and "
            "was faster overall than 0.005 s once the stall tail is counted.")
        self.reset_delay_spin = self._add_double(
            delays_form, "Reset delay:", 0.0, 10.0, 4, 0.010, "s",
            "Delay after resetting Swabian digital outputs to a clean "
            "baseline before acquisition (the magnet polarity relay is "
            "preserved at its commanded state, not zeroed).\n"
            "Recommended: 0.010 s. Measured: below 0.010 s, pulse.run() "
            "intermittently blocks 300-1000 ms (the Swabian needs recovery "
            "time before accepting a new streamed sequence). 0.010 s "
            "eliminated the stalls entirely in testing and was faster "
            "overall than 0.005 s once the stall tail is counted.")
        self.mw_settle_spin = self._add_double(
            delays_form, "MW settle:", 0.0, 10.0, 4, 0.0, "s",
            "Delay after setting the SG386's frequency, before acquisition "
            "-- frequency settling only. Unrelated to MW on/off gating, "
            "which is done by the fast switch (Pulse Streamer channel 2), "
            "not this delay.\n"
            "Recommended: 0.0 s currently. Use 0.05–0.1 s only if ESR "
            "disappears after frequency jumps.")
        self.pulse_lead_spin = self._add_double(
            delays_form, "Pulse lead:", 0.0, 1.0, 4, 0.002, "s",
            "Laser and MW switch on this long before the camera trigger "
            "fires.\n"
            "Recommended: 0.002 s.\n"
            "A sweep at 2/1/0.5/0.2/0 ms found no measurable contrast "
            "dependence, though the lower values overlap the trigger-delay "
            "stall regime (see Camera Trigger Delay), so that comparison "
            "isn't fully clean.")
        self.pulse_tail_spin = self._add_double(
            delays_form, "Pulse tail:", 0.0, 1.0, 4, 0.002, "s",
            "Laser and MW remain ON this long after the camera exposure "
            "ends, within the pulse sequence itself.\n"
            "Recommended: 0.002 s.\n"
            "Costs no wall-clock scan time -- nothing waits on it; it only "
            "extends how long the sequence holds these channels on "
            "hardware.")
        self.delays_section.set_summary_provider(self._delays_summary)

        # ---- Stability check (on/off in the header) -----------------------
        self.stability_group = StabilitySettingsGroup(settings_key="odmr/stability")
        odmr_layout.addWidget(self.stability_group)

        # ---- Plot & fit (open by default) ---------------------------------
        self.fit_section = CollapsibleSection(
            "Plot & fit", expanded=True, settings_key="odmr/fit")
        odmr_layout.addWidget(self.fit_section)
        fit_form = self.fit_section.body_layout()
        self.plot_mode_combo = QComboBox()
        self.plot_mode_combo.addItems(["Normalized ESR", "Mean I_on / I_off"])
        self.plot_mode_combo.setToolTip(
            "What the plot shows: the normalized ESR spectrum (ON/OFF, %), or "
            "the mean ON and OFF counts per pixel. Display only.")
        fit_form.addRow(self._tipped("Plot mode:", self.plot_mode_combo),
                        self.plot_mode_combo)
        self.fit_type_combo = QComboBox()
        self.fit_type_combo.addItems(["Single Lorentzian", "Double Lorentzian"])
        self.fit_type_combo.setToolTip(
            "Model for \"Fit Lorentzian\": one dip, or two dips (e.g. a "
            "strain- or field-split pair).")
        fit_form.addRow(self._tipped("Fit type:", self.fit_type_combo),
                        self.fit_type_combo)
        self.fit_button = QPushButton("Fit Lorentzian")
        self.fit_button.setEnabled(False)
        self.fit_button.setToolTip(
            "Fit the spectrum currently shown with the selected fit type. "
            "Enabled once data is available; results appear below.")
        fit_form.addRow(self.fit_button)
        self.initial_fit_parameters_button = QPushButton("Supply Initial Fit Parameters")
        self.initial_fit_parameters_button.setToolTip(
            "Give starting values for the fit (dip locations in GHz; "
            "optionally contrast and FWHM). Use it when the automatic guess "
            "misses a dip.")
        fit_form.addRow(self.initial_fit_parameters_button)
        self.fit_group = QWidget()
        fit_result_layout = QVBoxLayout(self.fit_group)
        fit_result_layout.setContentsMargins(0, 4, 0, 0)
        self.fit_label = QLabel(
            "f0 = --\n"
            "FWHM = --\n"
            "Contrast = --\n"
            "SNR = --\n"
            "R² = --"
        )
        self.fit_label.setToolTip("Result of the last fit.")
        fit_result_layout.addWidget(self.fit_label)
        fit_form.addRow(self.fit_group)
        self.fit_section.set_summary_provider(self._fit_summary)

        # ---- ODMR statistics (folded) -------------------------------------
        self.statistics_group = CollapsibleSection(
            "ODMR Statistics", settings_key="odmr/statistics")
        odmr_layout.addWidget(self.statistics_group)
        statistics_form = self.statistics_group.body_layout()
        self.statistics_summary_label = QLabel("Statistics: No completed acquisition")
        self.statistics_summary_label.setWordWrap(True)
        self.statistics_summary_label.setToolTip(
            "Availability of the per-pixel statistics of the last completed "
            "scan (saved in odmr_statistics.h5).")
        statistics_form.addRow(self.statistics_summary_label)
        self.statistics_details_button = QPushButton("How are these calculated?")
        self.statistics_details_button.setToolTip(
            "Explain how the final mean, fast/slow noise and combined "
            "uncertainty are calculated.")
        statistics_form.addRow(self.statistics_details_button)
        self.statistics_dialog = None
        self.statistics_details_button.clicked.connect(self.show_statistics_explanation)
        self.statistics_group.set_summary_provider(
            lambda: self.statistics_summary_label.text().splitlines()[0])

        # ---- Tools --------------------------------------------------------
        self.analysis_button = QPushButton("ODMR Analysis")
        self.analysis_button.setToolTip(
            "Open the offline ODMR analysis window (load a saved "
            "odmr_statistics.h5, fit per pixel, reconstruct Bz). Touches no "
            "hardware.")
        self.analysis_button.clicked.connect(self.analysis_requested.emit)
        self.sequence_button = QPushButton("Show Pulse Sequence")
        self.sequence_button.setToolTip(
            "Show the pulse sequence (laser, MW switch, camera trigger) that "
            "the current delay settings produce.")
        self.tools_row = QWidget()
        self.tools_row.setLayout(style.button_row(self.analysis_button,
                                                  self.sequence_button))
        odmr_layout.addWidget(self.tools_row)

        odmr_layout.addStretch()

        # Horizontal scrolling is off, so the column must be at least as
        # wide as the controls want; sections report their folded bodies.
        style.fit_column_width(self.control_scroll_area, self.control_box)

        plot_box, plot_layout = style.card("ODMR Plot", form=False)
        main_layout.addWidget(plot_box, 4)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Normalized Fluorescence (%)")
        self.plot.setLabel("bottom", "Microwave Frequency (GHz)")
        self.plot.showGrid(x=True, y=True)
        plot_layout.addWidget(self.plot)

        self.curve = self.plot.plot([], [], pen="y", symbol="o", symbolSize=6)

    # ---- section summaries ---------------------------------------------------

    def _dense_summary(self):
        if not self.dense_check.isChecked():
            return "Off"
        dips = [(self.dense1_center_spin, self.dense1_width_spin, self.dense1_steps_spin)]
        if self.num_dips_combo.currentText() == "2":
            dips.append((self.dense2_center_spin, self.dense2_width_spin,
                         self.dense2_steps_spin))
        parts = [f"{c.value():.6f} GHz, {w.value():g} MHz wide, {n.value()} pts"
                 for c, w, n in dips]
        return f"{len(dips)} dip{'s' if len(dips) > 1 else ''}: " + "; ".join(parts)

    def _delays_summary(self):
        def ms(spin):
            return f"{spin.value() * 1000:g} ms"
        return (f"trigger {ms(self.trigger_delay_spin)}, fire {ms(self.fire_delay_spin)}, "
                f"reset {ms(self.reset_delay_spin)}, settle {ms(self.mw_settle_spin)}, "
                f"lead {ms(self.pulse_lead_spin)}, tail {ms(self.pulse_tail_spin)}")

    def _fit_summary(self):
        lines = [line for line in self.fit_label.text().splitlines() if line.strip()]
        if not lines or lines[0].startswith("f0 = --"):
            return f"{self.plot_mode_combo.currentText()}; no fit yet"
        return ", ".join(lines[:3])

    def _update_dense_enabled(self, _checked=None):
        for field in self._dense_fields:
            field.setEnabled(self.dense_check.isChecked())

    def set_statistics_status(self, message):
        self.statistics_summary_label.setText(message)
        self.statistics_group.refresh_summary()

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
        self.statistics_group.refresh_summary()

    def show_statistics_explanation(self):
        if self.statistics_dialog is None:
            self.statistics_dialog = ODMRStatisticsExplanationDialog(self)
        self.statistics_dialog.show()
        self.statistics_dialog.raise_()
        self.statistics_dialog.activateWindow()

    # Same texts and shared styling as the Zero Field window's button.
    RUN_BUTTON_TEXT = {
        run_button.IDLE: "Start",
        run_button.RUNNING: "Stop",
        run_button.BLOCKED: "Waiting for stable counts",
    }
    WAITING_TOOLTIP = "Use \"Start anyway\" or \"Cancel\" below the controls."
    RUN_BUTTON_TOOLTIP = {
        run_button.IDLE: (
            "Start an ODMR scan with the settings below. The magnetic field "
            "is set first, then every frequency is measured; the plot shows "
            "the mean of the completed sweeps."),
        run_button.RUNNING: (
            "Stop the scan. Completed sweeps are kept (and saved if \"Save "
            "data\" is on); a partly done sweep is dropped."),
        run_button.BLOCKED: WAITING_TOOLTIP,
    }

    def set_run_button_state(self, state):
        """IDLE: "Start", green. RUNNING: "Stop", red. Driven by the
        window's run_started/run_finished signals, not the click. BLOCKED:
        grey and disabled while the stability check waits."""
        style_run_button(
            self.run_stop_button, state, self.RUN_BUTTON_TEXT[state],
            self.RUN_BUTTON_TOOLTIP[state],
        )

    def toggle_dip2(self, text):
        """Show the Dip 2 rows only with 2 dips (values are kept)."""
        form = self.dense_section.body_layout()
        for spin in (self.dense2_center_spin, self.dense2_width_spin,
                     self.dense2_steps_spin):
            form.setRowVisible(spin, text == "2")

    @staticmethod
    def _tipped(text, field):
        label = QLabel(text)
        label.setToolTip(field.toolTip())
        return label

    def _add_double(self, form, label, min_val, max_val, decimals, default,
                    unit="", tooltip=""):
        spin = style.double_spin(min_val, max_val, decimals, default, unit, tooltip)
        form.addRow(self._tipped(label, spin), spin)
        return spin

    def _add_int(self, form, label, min_val, max_val, default, tooltip=""):
        spin = style.int_spin(min_val, max_val, default, tooltip=tooltip)
        form.addRow(self._tipped(label, spin), spin)
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

            **self.magnet_field_group.get_config(),
        }

    def set_field_status(self, text):
        self.field_label.setText(text)

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
        self.fit_section.refresh_summary()

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
