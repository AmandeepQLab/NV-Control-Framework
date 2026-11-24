"""Controls and live plot for a zero-field magnetic scan."""

import pyqtgraph as pg
import numpy as np

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from experiments.zero_field_experiment import VECTOR_AXIS, unit_direction
from gui.image_inspection import line_profile, pixel_value
from gui.widgets import run_button
from gui.widgets.run_button import style_run_button


class ZeroFieldWidget(QWidget):
    """Widget matching the ODMR control-and-plot layout."""

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout(self)

        # Left column: the run/stop button fixed at the top, and every
        # other control in a vertically scrolling area below it (the same
        # QScrollArea set-up as the ODMR panel), so the button stays
        # visible at any window height.
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(left_panel, 1)
        self.left_panel = left_panel

        self.run_stop_button = QPushButton()
        self.set_run_button_state(run_button.IDLE)
        left_layout.addWidget(self.run_stop_button)

        controls = QGroupBox("Zero Field Parameters")
        self.controls_group = controls
        control_layout = QVBoxLayout(controls)

        self.control_scroll_area = QScrollArea()
        self.control_scroll_area.setWidgetResizable(True)
        self.control_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.control_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.control_scroll_area.setWidget(controls)
        left_layout.addWidget(self.control_scroll_area, 1)

        output_group = QGroupBox("Output Directory")
        output_group_layout = QVBoxLayout(output_group)
        control_layout.addWidget(output_group)
        output_layout = QHBoxLayout()
        output_group_layout.addLayout(output_layout)
        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setReadOnly(True)
        output_layout.addWidget(self.output_directory_edit)
        self.browse_output_button = QPushButton("Browse…")
        output_layout.addWidget(self.browse_output_button)
        self.save_data_check = QCheckBox("Save data")
        self.save_data_check.setChecked(True)
        output_group_layout.addWidget(self.save_data_check)

        field_group = QGroupBox("Magnetic Field")
        field_layout = QVBoxLayout(field_group)
        control_layout.addWidget(field_group)
        self.field_start_spin = self._add_double(
            field_layout, "Start Field (G):", -10000, 10000, 4, -2.0
        )
        self.field_stop_spin = self._add_double(
            field_layout, "Stop Field (G):", -10000, 10000, 4, 2.0
        )
        self.field_points_spin = self._add_int(
            field_layout, "Number of Points:", 1, 100000, 5
        )

        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel("Sweep Axis:"))
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
        axis_row.addWidget(self.field_axis_combo)
        field_layout.addLayout(axis_row)

        # Direction of a vector sweep; shown only while "Vector" is selected.
        self.field_direction_box = QWidget()
        direction_layout = QVBoxLayout(self.field_direction_box)
        direction_layout.setContentsMargins(0, 0, 0, 0)
        direction_row = QHBoxLayout()
        direction_row.addWidget(QLabel("Direction:"))
        self.field_direction_spins = []
        for name in ("nx", "ny", "nz"):
            direction_row.addWidget(QLabel(f"{name}"))
            spin = QDoubleSpinBox()
            spin.setRange(-1000.0, 1000.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)
            spin.setValue(1.0)
            spin.valueChanged.connect(self._update_field_direction_label)
            direction_row.addWidget(spin)
            self.field_direction_spins.append(spin)
        direction_layout.addLayout(direction_row)
        preset_row = QHBoxLayout()
        self.direction_110_button = QPushButton("<110>")
        self.direction_110_button.setToolTip("Direction (1, 1, 0): X = Y")
        self.direction_110_button.clicked.connect(
            lambda: self.set_field_direction((1.0, 1.0, 0.0))
        )
        self.direction_111_button = QPushButton("<111>")
        self.direction_111_button.setToolTip("Direction (1, 1, 1): X = Y = Z")
        self.direction_111_button.clicked.connect(
            lambda: self.set_field_direction((1.0, 1.0, 1.0))
        )
        preset_row.addWidget(self.direction_110_button)
        preset_row.addWidget(self.direction_111_button)
        preset_row.addStretch()
        direction_layout.addLayout(preset_row)
        self.field_direction_label = QLabel()
        self.field_direction_label.setWordWrap(True)
        direction_layout.addWidget(self.field_direction_label)
        field_layout.addWidget(self.field_direction_box)
        self.field_axis_combo.currentTextChanged.connect(
            self._update_field_direction_visible
        )
        self._update_field_direction_label()
        # Visibility is applied after the column width is computed below, so
        # the width also fits the direction row.

        self.settling_time_spin = self._add_int(
            field_layout, "Settling Time (ms):", 0, 600000, 100
        )
        self.settling_time_spin.setToolTip(
            "Fixed wait after commanding the field, before acquiring. "
            "Measured to be the largest single per-point cost in a "
            "multi-scan sweep (~36% of total run time) -- there is no "
            "field/current readback (see CLAUDE.md), so the correct value "
            "is empirical: increase if signal looks unsettled at your "
            "coil/sample, decrease if scans are too slow and results look "
            "unaffected."
        )
        self.post_flip_settling_spin = self._add_int(
            field_layout, "Post-flip extra settling (ms)", 0, 60000, 500
        )
        self.post_flip_settling_spin.setToolTip(
            "Extra settling time applied after a polarity relay flip on any "
            "axis. Relay flips occur near field zero; the coil current is cut "
            "and restored, requiring longer settling than an incremental step."
        )
        self.averages_spin = self._add_int(
            field_layout, "Number of Averages:", 1, 100000, 1
        )
        self.averages_spin.setToolTip(
            "Camera frames averaged per field point. Each repeat now costs "
            "~16 ms (the held-open software-triggered acquisition -- see "
            "CLAUDE.md), not the ~165 ms it cost before that change, so "
            "increasing this is far cheaper than it used to be."
        )

        averaging_group = QGroupBox("Multi-Scan Averaging")
        averaging_layout = QVBoxLayout(averaging_group)
        control_layout.addWidget(averaging_group)
        self.averaging_enabled_check = QCheckBox("Enable averaging")
        averaging_layout.addWidget(self.averaging_enabled_check)
        self.num_scans_spin = self._add_int(
            averaging_layout, "Number of Scans:", 1, 100000, 1
        )
        self.save_raw_scans_check = QCheckBox("Save individual scans")
        averaging_layout.addWidget(self.save_raw_scans_check)
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
        self.alternate_direction_warning.setStyleSheet("color: #c06000;")
        self.alternate_direction_warning.setToolTip(
            "With an odd number of scans there is one more forward sweep than "
            "reverse sweeps, so linear drift does not fully cancel. The scan "
            "will still run."
        )
        alternate_row.addWidget(self.alternate_direction_warning)
        alternate_row.addStretch()
        averaging_layout.addLayout(alternate_row)
        # Works with or without multi-scan averaging, so it is not tied to
        # the averaging checkbox like the controls above.
        reference_row = QHBoxLayout()
        self.reference_enabled_check = QCheckBox("Reference field normalization")
        self.reference_enabled_check.setToolTip(
            "Each point is also measured at the reference field and stored "
            "as signal/reference, so slow drift cancels. Roughly doubles "
            "acquisition time. Choose a reference far from zero field, on the "
            "same relay branch as most of the sweep to avoid repeated relay "
            "flips (for Z sweeps around zero, use a negative reference)."
        )
        reference_row.addWidget(self.reference_enabled_check)
        reference_row.addStretch()
        averaging_layout.addLayout(reference_row)
        self.reference_field_spin = self._add_double(
            averaging_layout, "Reference field (G)", -10000, 10000, 4, -8.0
        )
        self.reference_field_spin.setEnabled(False)
        self.reference_enabled_check.toggled.connect(
            self.reference_field_spin.setEnabled
        )
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

        camera_group = QGroupBox("Camera Acquisition")
        camera_layout = QVBoxLayout(camera_group)
        control_layout.addWidget(camera_group)
        camera_layout.addWidget(
            QLabel("ROI, exposure, and binning are controlled in the main window.")
        )
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Image Display:"))
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(["Raw Image", "Difference Image"])
        display_row.addWidget(self.display_mode_combo)
        camera_layout.addLayout(display_row)

        scaling_row = QHBoxLayout()
        scaling_row.addWidget(QLabel("Display Scaling:"))
        self.display_scaling_combo = QComboBox()
        self.display_scaling_combo.addItems(["Auto", "Manual"])
        scaling_row.addWidget(self.display_scaling_combo)
        camera_layout.addLayout(scaling_row)

        self.display_min_spin = self._add_double(
            camera_layout, "Minimum Intensity:", -1e12, 1e12, 4, 0.0
        )
        self.display_max_spin = self._add_double(
            camera_layout, "Maximum Intensity:", -1e12, 1e12, 4, 1.0
        )

        colormap_row = QHBoxLayout()
        colormap_row.addWidget(QLabel("Colormap:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Gray", "Viridis", "Plasma", "Inferno", "Magma"])
        colormap_row.addWidget(self.colormap_combo)
        camera_layout.addLayout(colormap_row)
        self.display_scaling_combo.currentTextChanged.connect(
            lambda mode: self._set_manual_display_controls_enabled(mode == "Manual")
        )
        self._set_manual_display_controls_enabled(False)

        self.line_profile_check = QCheckBox("Enable Line Profile")
        camera_layout.addWidget(self.line_profile_check)

        self.save_button = QPushButton("Save Data")
        self.save_button.setEnabled(False)
        control_layout.addWidget(self.save_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("Progress: 0 / 0")
        control_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Status: Ready")
        self.current_point_label = QLabel("Current scan point: --")
        self.current_scan_label = QLabel("Current scan: --")
        self.current_field_label = QLabel("Current magnetic field: -- G")
        self.current_signal_label = QLabel("Mean Fluorescence (counts/pixel): --")
        control_layout.addWidget(self.status_label)
        control_layout.addWidget(self.current_point_label)
        control_layout.addWidget(self.current_scan_label)
        control_layout.addWidget(self.current_field_label)
        control_layout.addWidget(self.current_signal_label)
        control_layout.addStretch()
        # Horizontal scrolling is off, so the column must be at least as
        # wide as the controls want, plus room for the vertical scrollbar.
        self.control_scroll_area.setMinimumWidth(
            controls.sizeHint().width()
            + self.control_scroll_area.verticalScrollBar().sizeHint().width()
            + 2 * self.control_scroll_area.frameWidth()
        )
        self._update_field_direction_visible()

        self._signal_label = self._COUNTS_LABEL
        plot_group = QGroupBox("Zero Field Mean Fluorescence (counts/pixel)")
        self.plot_group = plot_group
        main_layout.addWidget(plot_group, 4)
        plot_layout = QVBoxLayout(plot_group)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Mean Fluorescence (counts/pixel)")
        self.plot.setLabel("bottom", "Magnetic Field (G)")
        self.plot.showGrid(x=True, y=True)
        self.curve = self.plot.plot([], [], pen="y", symbol="o", symbolSize=6)
        plot_layout.addWidget(self.plot)

        image_group = QGroupBox("Zero Field Image")
        main_layout.addWidget(image_group, 4)
        image_layout = QVBoxLayout(image_group)
        # Own name, not pyqtgraph's shared default "ImageView" (see
        # gui/pyqtgraph_teardown.py).
        self.image_view = pg.ImageView(name="ZeroFieldImage")
        image_layout.addWidget(self.image_view)
        self.pixel_inspector_label = QLabel("Pixel: --")
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

    def _add_double(self, layout, label, minimum, maximum, decimals, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(default)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def _add_int(self, layout, label, minimum, maximum, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        row.addWidget(spin)
        layout.addLayout(row)
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
            self.field_direction_label.setStyleSheet("color: #c06000;")
            return
        self.field_direction_label.setStyleSheet("")
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

    def set_run_button_state(self, state):
        """IDLE: "Start", green. RUNNING: "Stop", red. BLOCKED: disabled,
        grey, "Queue running" (a queue owns the window)."""
        style_run_button(
            self.run_stop_button, state, self.RUN_BUTTON_TEXT[state],
            self.QUEUE_RUNNING_TOOLTIP if state == run_button.BLOCKED else "",
        )
