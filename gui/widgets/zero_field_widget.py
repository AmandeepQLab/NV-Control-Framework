"""Controls and live plot for a zero-field magnetic scan."""

import pyqtgraph as pg
import numpy as np

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from experiments.zero_field_experiment import VECTOR_AXIS, unit_direction
from gui.image_inspection import line_profile, pixel_value
from gui.widgets import run_button
from gui.widgets.run_button import style_run_button
from gui.widgets.stability_check_group import StabilitySettingsGroup, StabilityWaitBar
from gui.widgets.collapsible_section import CollapsibleSection
from gui import style


class ZeroFieldWidget(QWidget):
    """Widget matching the ODMR control-and-plot layout."""

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout(self)

        # Left column: the run/stop button and the run status fixed at the
        # top, every other control in a vertically scrolling area below
        # (cards and collapsible sections, as in the ODMR window), so the
        # button and progress stay visible at any window height.
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(left_panel, 1)
        self.left_panel = left_panel

        self.run_stop_button = QPushButton()
        self.set_run_button_state(run_button.IDLE)
        left_layout.addWidget(self.run_stop_button)

        # ---- Run status, directly under Start/Stop -----------------------
        self.status_panel = QWidget()
        status_layout = QVBoxLayout(self.status_panel)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(2)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)  # shows its text from the start, as in ODMR
        self.progress_bar.setFormat("Progress: 0 / 0")
        self.progress_bar.setToolTip("Field points acquired in the current scan.")
        status_layout.addWidget(self.progress_bar)
        self.status_label = style.status_label(
            "Status: Ready", "What the window is doing now (ready, running, "
            "waiting for stable counts, stopped, finished).")
        self.current_point_label = style.status_label(
            "Current scan point: --", "Field point acquired last, of the scan's total.")
        self.current_scan_label = style.status_label(
            "Current scan: --", "Scan number within a multi-scan average (and its "
            "sweep direction while alternating).")
        self.current_field_label = style.status_label(
            "Current magnetic field: -- G",
            "Requested field of the last acquired point, in G (along n̂ for a "
            "vector sweep). A commanded value, not a measurement.")
        self.current_signal_label = style.status_label(
            "Mean Fluorescence (counts/pixel): --",
            "Mean of the last acquired point's image (or signal/reference with "
            "reference normalization on).")
        for label in (self.status_label, self.current_point_label,
                      self.current_scan_label, self.current_field_label,
                      self.current_signal_label):
            status_layout.addWidget(label)
        left_layout.addWidget(self.status_panel)

        # Scroll content (attribute name kept: controls_group).
        controls = QWidget()
        self.controls_group = controls
        # Bottom margin and a plainly visible scroll bar (style.control_column).
        self.control_scroll_area, control_layout = style.control_column(controls)
        left_layout.addWidget(self.control_scroll_area, 1)

        # Stability-check wait line, fixed below the scroll area; shown
        # only while waiting for stable counts.
        self.stability_wait_bar = StabilityWaitBar()
        left_layout.addWidget(self.stability_wait_bar)

        # ---- Output (always open) -----------------------------------------
        self.output_group, output_form = style.card("Output")
        control_layout.addWidget(self.output_group)
        output_row = QHBoxLayout()
        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setReadOnly(True)
        self.output_directory_edit.setToolTip(
            "Folder where each run's files are written (one file per run, "
            "named with its start time). Change it with Browse.")
        output_row.addWidget(self.output_directory_edit, 1)
        self.browse_output_button = QPushButton("Browse…")
        self.browse_output_button.setToolTip("Choose the output folder.")
        output_row.addWidget(self.browse_output_button)
        output_form.addRow(output_row)
        save_row = QHBoxLayout()
        self.save_data_check = QCheckBox("Save data")
        self.save_data_check.setChecked(True)
        self.save_data_check.setToolTip(
            "Write each run to the output folder as it is acquired (streamed, "
            "so a crash keeps the points acquired so far). Off: nothing is "
            "written unless you press \"Save Data\" afterwards.")
        save_row.addWidget(self.save_data_check)
        save_row.addStretch(1)
        self.save_button = QPushButton("Save Data")
        self.save_button.setEnabled(False)
        self.save_button.setToolTip(
            "Save the last finished run now (enabled after a run with "
            "\"Save data\" off).")
        save_row.addWidget(self.save_button)
        output_form.addRow(save_row)

        # ---- Field sweep (always open) ------------------------------------
        self.field_group, field_form = style.card("Field sweep")
        control_layout.addWidget(self.field_group)
        self.field_start_spin = self._add_double(
            field_form, "Start field:", -10000, 10000, 4, -2.0, "G",
            "First field of the sweep, in G. With Sweep axis = Vector, the "
            "signed magnitude along the direction n̂.")
        self.field_stop_spin = self._add_double(
            field_form, "Stop field:", -10000, 10000, 4, 2.0, "G",
            "Last field of the sweep, in G (signed magnitude along n̂ for a "
            "vector sweep). Checked against the coils' range before a run.")
        self.field_points_spin = self._add_int(
            field_form, "Number of points:", 1, 100000, 5,
            "Number of evenly spaced field values from start to stop.")

        self.field_axis_combo = QComboBox()
        self.field_axis_combo.addItems(["X", "Y", "Z", self.VECTOR_TEXT])
        self.field_axis_combo.setToolTip(
            "Axis to sweep; the other two axes are zeroed for the duration "
            "of the scan (not preserved). The entry vector -- what the "
            "magnet actually held before the scan touched it -- is "
            "recorded in the saved metadata, so the zeroing is verifiable "
            "after the fact.\n\n"
            "Vector: sweep along the direction (nx, ny, nz) on all three "
            "axes at once. Start/Stop Field are then the signed magnitude "
            "along the normalised direction."
        )
        field_form.addRow(self._tipped("Sweep axis:", self.field_axis_combo),
                          self.field_axis_combo)

        # Direction of a vector sweep; shown only while "Vector" is selected.
        # One row per component (as in the ODMR window), so the column is
        # not widened for a row that is usually hidden.
        self.field_direction_box = QWidget()
        direction_layout = QVBoxLayout(self.field_direction_box)
        direction_layout.setContentsMargins(0, 0, 0, 0)
        direction_form = style.form_layout()
        self.field_direction_spins = []
        for name in ("nx", "ny", "nz"):
            spin = style.double_spin(
                -1000.0, 1000.0, 4, 1.0, step=0.1,
                tooltip=(f"Component {name} of the sweep direction (unitless). "
                         "Any nonzero (nx, ny, nz) works; it is normalised, so "
                         "(1, 1, 0) and (0.707, 0.707, 0) are the same."))
            spin.valueChanged.connect(self._update_field_direction_label)
            direction_form.addRow(self._tipped(f"{name}:", spin), spin)
            self.field_direction_spins.append(spin)
        direction_layout.addLayout(direction_form)
        preset_grid = QGridLayout()
        presets = (
            ("X", (1.0, 0.0, 0.0), "Direction (1, 0, 0): along the X coil axis."),
            ("Y", (0.0, 1.0, 0.0), "Direction (0, 1, 0): along the Y coil axis."),
            ("Z", (0.0, 0.0, 1.0), "Direction (0, 0, 1): along the Z coil axis."),
            ("<110>", (1.0, 1.0, 0.0), "Direction (1, 1, 0): X = Y"),
            ("<1-10>", (1.0, -1.0, 0.0), "Direction (1, -1, 0): X = -Y"),
            ("<111>", (1.0, 1.0, 1.0), "Direction (1, 1, 1): X = Y = Z"),
        )
        self.direction_preset_buttons = {}
        for index, (text, direction, tooltip) in enumerate(presets):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda _checked=False, d=direction: self.set_field_direction(d))
            preset_grid.addWidget(button, index // 3, index % 3)
            self.direction_preset_buttons[text] = button
        self.direction_110_button = self.direction_preset_buttons["<110>"]
        self.direction_111_button = self.direction_preset_buttons["<111>"]
        direction_layout.addLayout(preset_grid)
        self.field_direction_label = QLabel()
        self.field_direction_label.setWordWrap(True)
        self.field_direction_label.setToolTip(
            "The normalised direction n̂ the sweep runs along.")
        direction_layout.addWidget(self.field_direction_label)
        field_form.addRow(self.field_direction_box)
        self.field_axis_combo.currentTextChanged.connect(
            self._update_field_direction_visible
        )
        self._update_field_direction_label()

        self.averages_spin = self._add_int(
            field_form, "Frames per point:", 1, 100000, 1,
            "Camera frames averaged per field point. Each repeat now costs "
            "~16 ms (the held-open software-triggered acquisition -- see "
            "CLAUDE.md), not the ~165 ms it cost before that change, so "
            "increasing this is far cheaper than it used to be.")

        # ---- Settling -----------------------------------------------------
        self.settling_section = CollapsibleSection(
            "Settling", settings_key="zfe/settling",
            tooltip="Show or hide the settling times after each field step.")
        control_layout.addWidget(self.settling_section)
        settling_form = self.settling_section.body_layout()
        self.settling_time_spin = self._add_int(
            settling_form, "Settling time:", 0, 600000, 100,
            "Fixed wait after commanding the field, before acquiring. "
            "Measured to be the largest single per-point cost in a "
            "multi-scan sweep (~36% of total run time) -- there is no "
            "field/current readback (see CLAUDE.md), so the correct value "
            "is empirical: increase if signal looks unsettled at your "
            "coil/sample, decrease if scans are too slow and results look "
            "unaffected.", unit="ms")
        self.post_flip_settling_spin = self._add_int(
            settling_form, "Post-flip extra settling:", 0, 60000, 500,
            "Extra settling time applied after a polarity relay flip on any "
            "axis. Relay flips occur near field zero; the coil current is cut "
            "and restored, requiring longer settling than an incremental step.",
            unit="ms")
        self.settling_section.set_summary_provider(
            lambda: (f"{self.settling_time_spin.value()} ms per point, "
                     f"+{self.post_flip_settling_spin.value()} ms after a relay flip"))

        # ---- Multi-scan averaging (on/off in the header) -----------------
        self.averaging_enabled_check = QCheckBox("On")
        self.averaging_enabled_check.setToolTip(
            "Enable averaging: repeat the whole sweep \"Number of scans\" "
            "times and average the completed scans point by point.")
        self.averaging_section = CollapsibleSection(
            "Multi-scan averaging", checkbox=self.averaging_enabled_check,
            settings_key="zfe/averaging")
        control_layout.addWidget(self.averaging_section)
        averaging_form = self.averaging_section.body_layout()
        self.num_scans_spin = self._add_int(
            averaging_form, "Number of scans:", 1, 100000, 1,
            "How many complete sweeps to acquire and average. Only used with "
            "averaging on.")
        self.save_raw_scans_check = QCheckBox("Save individual scans")
        self.save_raw_scans_check.setToolTip(
            "Also keep each scan as its own file (_scan_NNN), next to the "
            "average. Needs \"Save data\" and averaging on.")
        averaging_form.addRow(self.save_raw_scans_check)
        alternate_row = QHBoxLayout()
        self.alternate_direction_check = QCheckBox("Alternate sweep direction")
        # Scans are numbered from 1 in this window (and in raw file names),
        # so the tooltip names them that way; see
        # ZeroFieldExperiment.scan_is_reversed().
        self.alternate_direction_check.setToolTip(
            "Scans 1, 3, 5, … sweep start→stop; scans 2, 4, 6, … sweep "
            "stop→start. Results are averaged by field value, so linear "
            "drift within a sweep cancels. Use an even number of scans."
        )
        alternate_row.addWidget(self.alternate_direction_check)
        # Non-blocking: an odd scan count still runs, it just can't pair
        # every forward sweep with a reverse one.
        self.alternate_direction_warning = QLabel("⚠ Odd number of scans")
        style.warning_style(self.alternate_direction_warning)
        self.alternate_direction_warning.setToolTip(
            "With an odd number of scans there is one more forward sweep than "
            "reverse sweeps, so linear drift does not fully cancel. The scan "
            "will still run."
        )
        alternate_row.addWidget(self.alternate_direction_warning)
        alternate_row.addStretch()
        averaging_form.addRow(alternate_row)

        # ---- Reference normalization (on/off in the header) ---------------
        # Works with or without multi-scan averaging, so it has its own
        # section rather than sitting under averaging.
        self.reference_enabled_check = QCheckBox("On")
        self.reference_enabled_check.setToolTip(
            "Reference field normalization: each point is also measured at "
            "the reference field and stored as signal/reference, so slow "
            "drift cancels. Roughly doubles acquisition time. Choose a "
            "reference far from zero field, on the same relay branch as most "
            "of the sweep to avoid repeated relay flips (for Z sweeps around "
            "zero, use a negative reference)."
        )
        self.reference_section = CollapsibleSection(
            "Reference normalization", checkbox=self.reference_enabled_check,
            settings_key="zfe/reference")
        control_layout.addWidget(self.reference_section)
        self.reference_field_spin = self._add_double(
            self.reference_section.body_layout(), "Reference field:",
            -10000, 10000, 4, -8.0, "G",
            "The off-resonant reference field, in G (signed magnitude along "
            "n̂ for a vector sweep). Used only with the reference on.")
        self.reference_field_spin.setEnabled(False)
        self.reference_enabled_check.toggled.connect(
            self.reference_field_spin.setEnabled
        )
        self.reference_section.set_summary_provider(
            lambda: (f"On, reference {self.reference_field_spin.value():g} G"
                     if self.reference_enabled_check.isChecked() else "Off"))

        self.averaging_enabled_check.toggled.connect(
            self._set_averaging_controls_enabled
        )
        self.alternate_direction_check.toggled.connect(
            self._update_alternate_direction_warning
        )
        self.num_scans_spin.valueChanged.connect(
            self._update_alternate_direction_warning
        )
        self.averaging_enabled_check.toggled.connect(
            self._update_save_raw_scans_enabled
        )
        self.save_data_check.toggled.connect(self._update_save_raw_scans_enabled)
        self._set_averaging_controls_enabled(False)
        self._update_save_raw_scans_enabled()
        self.averaging_section.set_summary_provider(self._averaging_summary)
        self.save_data_check.toggled.connect(self.averaging_section.refresh_summary)

        # Optional count stability check before a run (gui/stability_gate.py).
        # Its settings are not part of get_config(): they decide when a run
        # starts, not how it scans, and a queue entry snapshots them apart.
        self.stability_group = StabilitySettingsGroup(settings_key="zfe/stability")
        control_layout.addWidget(self.stability_group)

        # ---- Image display (display only; never changes the scan) ---------
        self.display_section = CollapsibleSection(
            "Image display", settings_key="zfe/display",
            tooltip="Show or hide how the image is displayed. Display only: "
                    "none of these change what is acquired or saved.")
        control_layout.addWidget(self.display_section)
        display_form = self.display_section.body_layout()
        note = QLabel("ROI, exposure and binning are set in the main window.")
        note.setWordWrap(True)
        style.muted(note)
        display_form.addRow(note)
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(["Raw Image", "Difference Image"])
        self.display_mode_combo.setToolTip(
            "Raw image of the last point, or its difference from the first "
            "point's image. Display only.")
        display_form.addRow(self._tipped("Image:", self.display_mode_combo),
                            self.display_mode_combo)
        self.display_scaling_combo = QComboBox()
        self.display_scaling_combo.addItems(["Auto", "Manual"])
        self.display_scaling_combo.setToolTip(
            "Auto: intensity range from each image. Manual: the minimum and "
            "maximum below.")
        display_form.addRow(self._tipped("Scaling:", self.display_scaling_combo),
                            self.display_scaling_combo)
        self.display_min_spin = self._add_double(
            display_form, "Minimum intensity:", -1e12, 1e12, 4, 0.0, "",
            "Lowest displayed intensity (counts) with manual scaling.")
        self.display_max_spin = self._add_double(
            display_form, "Maximum intensity:", -1e12, 1e12, 4, 1.0, "",
            "Highest displayed intensity (counts) with manual scaling.")
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Gray", "Viridis", "Plasma", "Inferno", "Magma"])
        self.colormap_combo.setToolTip(
            "Colour map of the Zero Field image. Applied at once and "
            "remembered (setupInfo.json, display.zfe_colormap).")
        display_form.addRow(self._tipped("Colormap:", self.colormap_combo),
                            self.colormap_combo)
        self.display_scaling_combo.currentTextChanged.connect(
            lambda mode: self._set_manual_display_controls_enabled(mode == "Manual")
        )
        self._set_manual_display_controls_enabled(False)
        self.line_profile_check = QCheckBox("Line profile")
        self.line_profile_check.setToolTip(
            "Show a draggable line on the image and plot the intensity along "
            "it below the image.")
        display_form.addRow(self.line_profile_check)
        self.display_section.set_summary_provider(
            lambda: (f"{self.display_mode_combo.currentText()}, "
                     f"{self.display_scaling_combo.currentText().lower()} scaling, "
                     f"{self.colormap_combo.currentText()}"
                     + (", line profile" if self.line_profile_check.isChecked() else "")))

        control_layout.addStretch()
        # Horizontal scrolling is off, so the column must be at least as
        # wide as the controls want (the usually hidden direction rows too).
        style.fit_column_width(self.control_scroll_area, controls,
                               also_measure=(self.field_direction_box,))
        self._update_field_direction_visible()

        self._signal_label = self._COUNTS_LABEL
        plot_group, plot_layout = style.card(
            "Zero Field Mean Fluorescence (counts/pixel)", form=False)
        self.plot_group = plot_group
        main_layout.addWidget(plot_group, 4)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Mean Fluorescence (counts/pixel)")
        self.plot.setLabel("bottom", "Magnetic Field (G)")
        self.plot.showGrid(x=True, y=True)
        self.curve = self.plot.plot([], [], pen="y", symbol="o", symbolSize=6)
        plot_layout.addWidget(self.plot)

        image_group, image_layout = style.card("Zero Field Image", form=False)
        main_layout.addWidget(image_group, 4)
        # Own name, not pyqtgraph's shared default "ImageView" (see
        # gui/pyqtgraph_teardown.py).
        self.image_view = pg.ImageView(name="ZeroFieldImage")
        image_layout.addWidget(self.image_view)
        self.pixel_inspector_label = QLabel("Pixel: --")
        self.pixel_inspector_label.setToolTip(
            "Position and intensity of the image pixel under the mouse.")
        image_layout.addWidget(self.pixel_inspector_label)
        self.line_profile_plot = pg.PlotWidget()
        self.line_profile_plot.setLabel("left", "Intensity")
        self.line_profile_plot.setLabel("bottom", "Distance (pixels)")
        self.line_profile_curve = self.line_profile_plot.plot([], [], pen="c")
        image_layout.addWidget(self.line_profile_plot)

        self._displayed_image = None
        self.line_roi = pg.LineSegmentROI([(10, 10), (100, 100)], pen="c")
        self.image_view.getView().addItem(self.line_roi)
        self.line_roi.setVisible(False)
        self.line_roi.sigRegionChangeFinished.connect(self.update_line_profile)
        self.line_profile_check.toggled.connect(self.set_line_profile_enabled)
        self.image_view.getView().scene().sigMouseMoved.connect(self.update_pixel_inspector)

    def _averaging_summary(self):
        if not self.averaging_enabled_check.isChecked():
            return "Off"
        parts = [f"{self.num_scans_spin.value()} scans"]
        if self.alternate_direction_check.isChecked():
            parts.append("alternating direction")
        if self.save_raw_scans_check.isChecked() and self.save_raw_scans_check.isEnabled():
            parts.append("individual scans saved")
        return "On: " + ", ".join(parts)

    @staticmethod
    def _tipped(text, field):
        label = QLabel(text)
        label.setToolTip(field.toolTip())
        return label

    def _add_double(self, form, label, minimum, maximum, decimals, default,
                    unit="", tooltip=""):
        spin = style.double_spin(minimum, maximum, decimals, default, unit, tooltip)
        form.addRow(self._tipped(label, spin), spin)
        return spin

    def _add_int(self, form, label, minimum, maximum, default, tooltip="", unit=""):
        spin = style.int_spin(minimum, maximum, default, unit, tooltip)
        form.addRow(self._tipped(label, spin), spin)
        return spin

    def set_output_directory(self, path):
        self.output_directory_edit.setText(str(path))

    # ---- vector sweep direction ----------------------------------------

    VECTOR_TEXT = "Vector"

    def is_vector_sweep(self):
        return self.field_axis_combo.currentText() == self.VECTOR_TEXT

    def raw_field_direction(self):
        """(nx, ny, nz) as entered, not normalised."""
        return [spin.value() for spin in self.field_direction_spins]

    def set_field_direction(self, direction):
        for spin, value in zip(self.field_direction_spins, direction):
            spin.setValue(float(value))

    def _update_field_direction_visible(self, _text=None):
        self.field_direction_box.setVisible(self.is_vector_sweep())

    def _update_field_direction_label(self, _value=None):
        try:
            unit = unit_direction(self.raw_field_direction())
        except ValueError:
            self.field_direction_label.setText(
                "⚠ Zero vector: choose a direction. A scan will be refused."
            )
            style.warning_style(self.field_direction_label)
            return
        style.warning_style(self.field_direction_label, on=False)
        self.field_direction_label.setText(
            "n̂ = (" + ", ".join(f"{n:.3f}" for n in unit) + "). "
            "Start/Stop Field (and the reference field) are the signed "
            "magnitude along n̂."
        )

    def get_config(self):
        config = self._single_axis_config()
        if self.is_vector_sweep():
            config["field_axis"] = VECTOR_AXIS
            # Normalised; a zero vector is reported as entered, and refused
            # by the window's pre-scan check (field_range_problem()).
            try:
                config["field_direction"] = unit_direction(self.raw_field_direction())
            except ValueError:
                config["field_direction"] = self.raw_field_direction()
        return config

    def _single_axis_config(self):
        return {
            "field_start": self.field_start_spin.value(),
            "field_stop": self.field_stop_spin.value(),
            "field_points": self.field_points_spin.value(),
            "field_axis": self.field_axis_combo.currentText(),
            "settling_time_ms": self.settling_time_spin.value(),
            "post_flip_settling_ms": self.post_flip_settling_spin.value(),
            "averages": self.averages_spin.value(),
            "averaging_enabled": self.averaging_enabled_check.isChecked(),
            "num_scans": self.num_scans_spin.value(),
            "save_raw_scans": self.save_raw_scans_check.isChecked(),
            # A disabled checkbox keeps its checked state; it must not
            # silently apply while averaging is off.
            "alternate_direction": (
                self.alternate_direction_check.isChecked()
                and self.alternate_direction_check.isEnabled()
            ),
            "reference_enabled": (
                self.reference_enabled_check.isChecked()
                and self.reference_enabled_check.isEnabled()
            ),
            "reference_field_gauss": self.reference_field_spin.value(),
            "save_data": self.save_data_check.isChecked(),
        }

    def set_config(self, config):
        """Put a get_config()-shaped dict back into the controls.

        The enabling checkboxes are set before the controls they enable, so
        get_config() afterwards returns the same values (a False
        alternate/reference flag leaves its box unchecked).
        """
        self.field_start_spin.setValue(config["field_start"])
        self.field_stop_spin.setValue(config["field_stop"])
        self.field_points_spin.setValue(config["field_points"])
        if config["field_axis"] == VECTOR_AXIS:
            self.field_axis_combo.setCurrentText(self.VECTOR_TEXT)
            self.set_field_direction(config["field_direction"])
        else:
            self.field_axis_combo.setCurrentText(config["field_axis"])
        self.settling_time_spin.setValue(config["settling_time_ms"])
        self.post_flip_settling_spin.setValue(config["post_flip_settling_ms"])
        self.averages_spin.setValue(config["averages"])
        self.save_data_check.setChecked(config["save_data"])
        self.averaging_enabled_check.setChecked(config["averaging_enabled"])
        self.num_scans_spin.setValue(config["num_scans"])
        self.save_raw_scans_check.setChecked(config["save_raw_scans"])
        self.alternate_direction_check.setChecked(config["alternate_direction"])
        self.reference_enabled_check.setChecked(config["reference_enabled"])
        self.reference_field_spin.setValue(config["reference_field_gauss"])

    # Live-curve labels: raw counts, or signal/reference with the
    # reference on (see set_signal_mode()).
    _COUNTS_LABEL = "Mean Fluorescence (counts/pixel)"
    _RATIO_LABEL = "Signal / reference"

    def set_signal_mode(self, ratio):
        """Label the live curve as raw counts or as signal/reference."""
        self._signal_label = self._RATIO_LABEL if ratio else self._COUNTS_LABEL
        self.plot.setLabel("left", self._signal_label)
        self.plot_group.setTitle(f"Zero Field {self._signal_label}")
        self.current_signal_label.setText(f"{self._signal_label}: --")

    def reset_scan(self, total, total_scans=1):
        self.curve.setData([], [])
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Progress: 0 / {total}")
        self.current_point_label.setText("Current scan point: --")
        self.current_scan_label.setText(f"Current scan: 0 / {total_scans}")
        self.current_field_label.setText("Current magnetic field: -- G")
        self.current_signal_label.setText(f"{self._signal_label}: --")

    def update_live_data(self, fields, signals, current, total):
        self.update_fluorescence_curve(fields, signals)
        self.update_progress(current, total)

    def update_fluorescence_curve(self, fields, signals):
        """Render the raw mean fluorescence values acquired so far."""
        self.curve.setData(fields, signals)
        self.current_field_label.setText(f"Current magnetic field: {fields[-1]:.6g} G")
        self.current_signal_label.setText(f"{self._signal_label}: {signals[-1]:.6g}")

    def update_progress(self, current, total):
        """Advance scan progress after the newest fluorescence point is displayed."""
        self.progress_bar.setValue(int(100 * current / total) if total else 0)
        self.progress_bar.setFormat(f"Progress: {current} / {total}")
        self.current_point_label.setText(f"Current scan point: {current} / {total}")

    def update_image(self, image, levels, colormap_name):
        """Render the selected display representation of an acquired image."""
        self._displayed_image = np.asarray(image)
        colormap = pg.colormap.get(colormap_name.lower(), source="matplotlib")
        self.image_view.setColorMap(colormap)
        self.image_view.setImage(image.T, autoLevels=False, levels=levels)
        self.update_line_profile()

    def update_pixel_inspector(self, scene_position):
        """Show a value from the cached display image under the mouse pointer."""
        if self._displayed_image is None:
            return
        image_position = self.image_view.getImageItem().mapFromScene(scene_position)
        x, y = int(image_position.x()), int(image_position.y())
        value = pixel_value(self._displayed_image, x, y)
        if value is None:
            self.pixel_inspector_label.setText("Pixel: --")
            return
        self.pixel_inspector_label.setText(f"Pixel: x={x}, y={y}, intensity={value:.6g}")

    def set_line_profile_enabled(self, enabled):
        """Show or hide the interactive line without changing the display image."""
        self.line_roi.setVisible(enabled)
        if enabled:
            self.update_line_profile()

    def update_line_profile(self):
        """Sample and plot the cached displayed image along the interactive line."""
        if self._displayed_image is None or not self.line_profile_check.isChecked():
            return
        start, end = [
            self.image_view.getImageItem().mapFromScene(handle.scenePos())
            for handle in self.line_roi.endpoints
        ]
        distances, values = line_profile(
            self._displayed_image,
            (start.x(), start.y()),
            (end.x(), end.y()),
        )
        self.line_profile_curve.setData(distances, values)

    def update_scan_progress(self, current, total, direction=None):
        text = f"Current scan: {current} / {total}"
        if direction is not None:
            text += f" ({direction})"
        self.current_scan_label.setText(text)

    def _set_averaging_controls_enabled(self, enabled):
        self.num_scans_spin.setEnabled(enabled)
        self.alternate_direction_check.setEnabled(enabled)
        self._update_alternate_direction_warning()

    def _update_alternate_direction_warning(self, _value=None):
        """Show the odd-scan-count warning only when it applies."""
        self.alternate_direction_warning.setVisible(
            self.alternate_direction_check.isChecked()
            and self.alternate_direction_check.isEnabled()
            and self.num_scans_spin.value() % 2 == 1
        )

    def _update_save_raw_scans_enabled(self, _value=None):
        """"Save individual scans" is meaningless with saving off, and
        averaging off means there's only ever one scan to save individually."""
        enabled = (
            self.averaging_enabled_check.isChecked()
            and self.save_data_check.isChecked()
        )
        self.save_raw_scans_check.setEnabled(enabled)

    def _set_manual_display_controls_enabled(self, enabled):
        self.display_min_spin.setEnabled(enabled)
        self.display_max_spin.setEnabled(enabled)

    def set_running_state(self, running):
        """Status line only. The run button follows the window's
        run_started/run_finished signals instead (set_run_button_state())."""
        if running:
            self.status_label.setText("Status: Running")

    # Run/stop button texts and the tooltip shown while a queue owns it.
    RUN_BUTTON_TEXT = {
        run_button.IDLE: "Start",
        run_button.RUNNING: "Stop",
        run_button.BLOCKED: "Queue running",
    }
    QUEUE_RUNNING_TOOLTIP = (
        "A run queue is in progress. Use \"Stop current run\" or \"Stop queue\" "
        "in the Run Queue panel."
    )

    RUN_BUTTON_TOOLTIP = {
        run_button.IDLE: (
            "Start a Zero Field run with the settings below. The field range "
            "is checked first; the magnet is switched off when the run ends."),
        run_button.RUNNING: (
            "Stop the run after the current point. Completed points are kept; "
            "a partly done scan is not added to a multi-scan average."),
        run_button.BLOCKED: QUEUE_RUNNING_TOOLTIP,
    }

    def set_run_button_state(self, state):
        """IDLE: "Start", green. RUNNING: "Stop", red. BLOCKED: disabled,
        grey, "Queue running" (a queue owns the window)."""
        style_run_button(
            self.run_stop_button, state, self.RUN_BUTTON_TEXT[state],
            self.RUN_BUTTON_TOOLTIP[state],
        )

    WAITING_TEXT = "Waiting for stable counts"
    WAITING_TOOLTIP = "Use \"Start anyway\" or \"Cancel\" below the controls."

    def set_run_button_waiting(self):
        """Grey and disabled while a single run's stability check waits."""
        style_run_button(
            self.run_stop_button, run_button.BLOCKED, self.WAITING_TEXT,
            self.WAITING_TOOLTIP,
        )
