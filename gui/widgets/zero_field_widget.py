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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.image_inspection import line_profile, pixel_value


class ZeroFieldWidget(QWidget):
    """Widget matching the ODMR control-and-plot layout."""

    def __init__(self):
        super().__init__()

        main_layout = QHBoxLayout(self)

        controls = QGroupBox("Zero Field Parameters")
        main_layout.addWidget(controls, 1)
        control_layout = QVBoxLayout(controls)

        output_group = QGroupBox("Output Directory")
        output_layout = QHBoxLayout(output_group)
        control_layout.addWidget(output_group)
        self.output_directory_edit = QLineEdit()
        self.output_directory_edit.setReadOnly(True)
        output_layout.addWidget(self.output_directory_edit)
        self.browse_output_button = QPushButton("Browse…")
        output_layout.addWidget(self.browse_output_button)

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
        self.field_axis_combo.addItems(["X", "Y", "Z"])
        axis_row.addWidget(self.field_axis_combo)
        field_layout.addLayout(axis_row)

        self.settling_time_spin = self._add_int(
            field_layout, "Settling Time (ms):", 0, 600000, 500
        )
        self.averages_spin = self._add_int(
            field_layout, "Number of Averages:", 1, 100000, 1
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
        self.averaging_enabled_check.toggled.connect(
            self._set_averaging_controls_enabled
        )
        self._set_averaging_controls_enabled(False)

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

        self.run_stop_button = QPushButton("Start Scan")
        self.run_stop_button.setStyleSheet(
            "background-color: green; color: white; font-weight: bold;"
        )
        control_layout.addWidget(self.run_stop_button)

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

        plot_group = QGroupBox("Zero Field Mean Fluorescence (counts/pixel)")
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
        self.image_view = pg.ImageView()
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

    def get_config(self):
        return {
            "field_start": self.field_start_spin.value(),
            "field_stop": self.field_stop_spin.value(),
            "field_points": self.field_points_spin.value(),
            "field_axis": self.field_axis_combo.currentText(),
            "settling_time_ms": self.settling_time_spin.value(),
            "averages": self.averages_spin.value(),
            "averaging_enabled": self.averaging_enabled_check.isChecked(),
            "num_scans": self.num_scans_spin.value(),
            "save_raw_scans": self.save_raw_scans_check.isChecked(),
        }

    def reset_scan(self, total, total_scans=1):
        self.curve.setData([], [])
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Progress: 0 / {total}")
        self.current_point_label.setText("Current scan point: --")
        self.current_scan_label.setText(f"Current scan: 0 / {total_scans}")
        self.current_field_label.setText("Current magnetic field: -- G")
        self.current_signal_label.setText("Mean Fluorescence (counts/pixel): --")

    def update_live_data(self, fields, signals, current, total):
        self.update_fluorescence_curve(fields, signals)
        self.update_progress(current, total)

    def update_fluorescence_curve(self, fields, signals):
        """Render the raw mean fluorescence values acquired so far."""
        self.curve.setData(fields, signals)
        self.current_field_label.setText(f"Current magnetic field: {fields[-1]:.6g} G")
        self.current_signal_label.setText(
            f"Mean Fluorescence (counts/pixel): {signals[-1]:.6g}"
        )

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

    def update_scan_progress(self, current, total):
        self.current_scan_label.setText(f"Current scan: {current} / {total}")

    def _set_averaging_controls_enabled(self, enabled):
        self.num_scans_spin.setEnabled(enabled)
        self.save_raw_scans_check.setEnabled(enabled)

    def _set_manual_display_controls_enabled(self, enabled):
        self.display_min_spin.setEnabled(enabled)
        self.display_max_spin.setEnabled(enabled)

    def set_running_state(self, running):
        if running:
            self.run_stop_button.setText("Stop Scan")
            self.run_stop_button.setStyleSheet(
                "background-color: red; color: white; font-weight: bold;"
            )
            self.status_label.setText("Status: Running")
        else:
            self.run_stop_button.setText("Start Scan")
            self.run_stop_button.setStyleSheet(
                "background-color: green; color: white; font-weight: bold;"
            )
