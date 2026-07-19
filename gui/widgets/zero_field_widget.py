"""Controls and live plot for a zero-field magnetic scan."""

import pyqtgraph as pg

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ZeroFieldWidget(QWidget):
    """Widget matching the ODMR control-and-plot layout."""

    def __init__(self, exposure_s=0.02, binning=1):
        super().__init__()

        main_layout = QHBoxLayout(self)

        controls = QGroupBox("Zero Field Parameters")
        main_layout.addWidget(controls, 1)
        control_layout = QVBoxLayout(controls)

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

        camera_group = QGroupBox("Camera")
        camera_layout = QVBoxLayout(camera_group)
        control_layout.addWidget(camera_group)
        self.exposure_spin = self._add_double(
            camera_layout, "Exposure Time (s):", 0.001, 10.0, 4, exposure_s
        )
        self.binning_spin = self._add_int(
            camera_layout, "Binning:", 1, 8, binning
        )
        self.use_roi_check = QCheckBox("Use ROI selected in main camera view")
        self.use_roi_check.setChecked(True)
        camera_layout.addWidget(self.use_roi_check)

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

    def get_config(self):
        return {
            "field_start": self.field_start_spin.value(),
            "field_stop": self.field_stop_spin.value(),
            "field_points": self.field_points_spin.value(),
            "field_axis": self.field_axis_combo.currentText(),
            "settling_time_ms": self.settling_time_spin.value(),
            "averages": self.averages_spin.value(),
            "exposure_s": self.exposure_spin.value(),
            "binning": self.binning_spin.value(),
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
        self.curve.setData(fields, signals)
        self.progress_bar.setValue(int(100 * current / total) if total else 0)
        self.progress_bar.setFormat(f"Progress: {current} / {total}")
        self.current_point_label.setText(f"Current scan point: {current} / {total}")
        self.current_field_label.setText(f"Current magnetic field: {fields[-1]:.6g} G")
        self.current_signal_label.setText(
            f"Mean Fluorescence (counts/pixel): {signals[-1]:.6g}"
        )

    def update_scan_progress(self, current, total):
        self.current_scan_label.setText(f"Current scan: {current} / {total}")

    def _set_averaging_controls_enabled(self, enabled):
        self.num_scans_spin.setEnabled(enabled)
        self.save_raw_scans_check.setEnabled(enabled)

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
