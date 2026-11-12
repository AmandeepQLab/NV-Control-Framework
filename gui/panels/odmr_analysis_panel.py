"""Widgets for offline ODMR spectrum and spatial analysis."""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ODMRAnalysisPanel(QWidget):
    """Controls and displays for global and pixel-wise ODMR analysis."""

    open_file_requested = pyqtSignal()
    fit_global_requested = pyqtSignal()
    fit_pixels_requested = pyqtSignal()
    reconstruct_bz_requested = pyqtSignal()
    map_selection_requested = pyqtSignal(str)
    map_pixel_clicked = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        main_layout = QHBoxLayout(self)
        control_layout = QVBoxLayout()
        main_layout.addLayout(control_layout, 1)

        controls = QGroupBox("Analysis")
        controls_layout = QVBoxLayout(controls)
        self.open_button = QPushButton("Open ODMR HDF5")
        self.fit_button = QPushButton("Fit Global Spectrum")
        self.fit_pixels_button = QPushButton("Fit Pixels")
        self.reconstruct_bz_button = QPushButton("Reconstruct Bz")
        self.fit_button.setEnabled(False)
        self.fit_pixels_button.setEnabled(False)
        self.reconstruct_bz_button.setEnabled(False)
        for button in (
            self.open_button,
            self.fit_button,
            self.fit_pixels_button,
            self.reconstruct_bz_button,
        ):
            controls_layout.addWidget(button)
        self.status_label = QLabel("Open an ODMR statistics HDF5 file to begin.")
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)
        control_layout.addWidget(controls)

        metadata_box = QGroupBox("Acquisition Metadata")
        metadata_layout = QVBoxLayout(metadata_box)
        self.metadata_label = QLabel("No dataset loaded.")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        metadata_layout.addWidget(self.metadata_label)
        control_layout.addWidget(metadata_box)

        fit_box = QGroupBox("Global Two-Lorentzian Fit")
        fit_layout = QVBoxLayout(fit_box)
        self.fit_info_label = QLabel("No global fit available.")
        self.fit_info_label.setWordWrap(True)
        fit_layout.addWidget(self.fit_info_label)
        control_layout.addWidget(fit_box)

        pixel_box = QGroupBox("Pixel Fit")
        pixel_layout = QVBoxLayout(pixel_box)
        self.pixel_fit_summary_label = QLabel("No pixel fit available.")
        self.pixel_fit_summary_label.setWordWrap(True)
        pixel_layout.addWidget(self.pixel_fit_summary_label)
        self.selected_pixel_label = QLabel("Pixel: --")
        self.selected_pixel_label.setWordWrap(True)
        pixel_layout.addWidget(self.selected_pixel_label)
        control_layout.addWidget(pixel_box)
        control_layout.addStretch(1)

        result_layout = QVBoxLayout()
        main_layout.addLayout(result_layout, 4)

        plot_box = QGroupBox("Global ODMR Spectrum")
        plot_layout = QVBoxLayout(plot_box)
        self.plot = pg.PlotWidget()
        self.plot.getViewBox().unregister()
        self.plot.setLabel("bottom", "Frequency (GHz)")
        self.plot.setLabel("left", "Normalized fluorescence (%)")
        self.plot.showGrid(x=True, y=True)
        plot_layout.addWidget(self.plot)
        self.measured_curve = self.plot.plot(
            [], [], pen=None, symbol="o", symbolSize=6, name="Global mean"
        )
        self.fitted_curve = self.plot.plot(
            [], [], pen=pg.mkPen("r", width=2), name="Global fit"
        )
        result_layout.addWidget(plot_box, 3)

        spatial_layout = QHBoxLayout()
        result_layout.addLayout(spatial_layout, 2)

        map_box = QGroupBox("Pixel Result Map")
        map_layout = QVBoxLayout(map_box)
        map_controls = QHBoxLayout()
        map_controls.addWidget(QLabel("Map:"))
        self.map_combo = QComboBox()
        self.map_combo.addItems(
            ["f_minus", "f_plus", "splitting", "RMSE", "fit_status", "Bz magnitude"]
        )
        self.map_combo.setEnabled(False)
        map_controls.addWidget(self.map_combo, 1)
        map_layout.addLayout(map_controls)

        color_controls = QHBoxLayout()
        color_controls.addWidget(QLabel("Colormap:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(
            ["Viridis", "Plasma", "Inferno", "Magma", "Cividis"]
        )
        color_controls.addWidget(self.colormap_combo)
        color_controls.addWidget(QLabel("Min:"))
        self.scale_min_spin = self._make_scale_spinbox()
        color_controls.addWidget(self.scale_min_spin)
        color_controls.addWidget(QLabel("Max:"))
        self.scale_max_spin = self._make_scale_spinbox()
        color_controls.addWidget(self.scale_max_spin)
        self.auto_scale_button = QPushButton("Auto")
        self.auto_scale_button.setToolTip("Restore automatic color-scale limits")
        color_controls.addWidget(self.auto_scale_button)
        map_layout.addLayout(color_controls)

        self.map_title_label = QLabel("No pixel-fit map available.")
        map_layout.addWidget(self.map_title_label)
        # A bare ImageItem in a ViewBox provides map navigation without
        # ImageView's unrelated ROI/time controls and named global link entries.
        self.image_view = pg.GraphicsLayoutWidget()
        self.image_view_box = self.image_view.addViewBox(enableMenu=False)
        self.image_view_box.invertY(True)
        self.image_view_box.unregister()
        self.image_item = pg.ImageItem()
        self.image_view_box.addItem(self.image_item)
        map_layout.addWidget(self.image_view, 1)
        self.color_bar = pg.ColorBarItem(
            values=(0.0, 1.0),
            colorMap=pg.colormap.get("viridis", source="matplotlib"),
            label="",
            interactive=False,
            colorMapMenu=False,
        )
        self.color_bar.setImageItem(self.image_item)
        self.image_view.addItem(self.color_bar, row=0, col=1)
        self.color_bar.hide()
        self.status_legend_label = QLabel("")
        self.status_legend_label.setWordWrap(True)
        map_layout.addWidget(self.status_legend_label)
        spatial_layout.addWidget(map_box, 3)

        pixel_plot_box = QGroupBox("Selected Pixel Spectrum")
        pixel_plot_layout = QVBoxLayout(pixel_plot_box)
        self.pixel_plot = pg.PlotWidget()
        self.pixel_plot.getViewBox().unregister()
        self.pixel_plot.setLabel("bottom", "Frequency (GHz)")
        self.pixel_plot.setLabel("left", "Normalized fluorescence (%)")
        self.pixel_plot.showGrid(x=True, y=True)
        pixel_plot_layout.addWidget(self.pixel_plot)
        self.pixel_measured_curve = self.pixel_plot.plot(
            [], [], pen=None, symbol="o", symbolSize=5, name="Pixel data"
        )
        self.pixel_fitted_curve = self.pixel_plot.plot(
            [], [], pen=pg.mkPen("r", width=2), name="Pixel fit"
        )
        spatial_layout.addWidget(pixel_plot_box, 2)

        self._displayed_map = None
        self.selection_marker = pg.ScatterPlotItem(
            size=12, symbol="+", pen=pg.mkPen("y", width=2)
        )
        self.image_view_box.addItem(self.selection_marker)
        self.image_view.scene().sigMouseClicked.connect(self._on_map_clicked)

        self.open_button.clicked.connect(self.open_file_requested.emit)
        self.fit_button.clicked.connect(self.fit_global_requested.emit)
        self.fit_pixels_button.clicked.connect(self.fit_pixels_requested.emit)
        self.reconstruct_bz_button.clicked.connect(self.reconstruct_bz_requested.emit)
        self.map_combo.currentTextChanged.connect(self.map_selection_requested.emit)
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.scale_min_spin.valueChanged.connect(self._apply_manual_scale)
        self.scale_max_spin.valueChanged.connect(self._apply_manual_scale)
        self.auto_scale_button.clicked.connect(self.restore_auto_scale)
        self.clear_pixel_results()

    @staticmethod
    def _make_scale_spinbox():
        spinbox = QDoubleSpinBox()
        spinbox.setRange(-1.0e12, 1.0e12)
        spinbox.setDecimals(9)
        spinbox.setSingleStep(0.001)
        spinbox.setKeyboardTracking(False)
        return spinbox

    def _on_map_clicked(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._displayed_map is None:
            return
        image_position = self.image_item.mapFromScene(event.scenePos())
        self.map_pixel_clicked.emit(
            float(image_position.x()), float(image_position.y())
        )

    def set_status(self, message):
        self.status_label.setText(str(message))

    def set_actions(
        self,
        *,
        open_enabled,
        global_fit_enabled,
        pixel_fit_enabled,
        reconstruct_enabled,
    ):
        self.open_button.setEnabled(bool(open_enabled))
        self.fit_button.setEnabled(bool(global_fit_enabled))
        self.fit_pixels_button.setEnabled(bool(pixel_fit_enabled))
        self.reconstruct_bz_button.setEnabled(bool(reconstruct_enabled))

    def set_fit_enabled(self, enabled):
        """Keep the Stage 1 controller helper available."""
        self.fit_button.setEnabled(bool(enabled))

    def set_metadata(self, text):
        self.metadata_label.setText(str(text))

    def set_spectrum(self, frequency_hz, spectrum):
        frequency_ghz = frequency_hz / 1.0e9
        self.measured_curve.setData(frequency_ghz, spectrum)

    def clear_fit(self, message="No global fit available."):
        self.fitted_curve.setData([], [])
        self.fit_info_label.setText(message)

    def set_fit_result(self, result):
        if not result.success:
            self.clear_fit(f"Fit status: {result.status}\n{result.message}")
            return

        self.fitted_curve.setData(
            result.frequency_hz / 1.0e9, result.fitted_spectrum
        )
        splitting_ghz = (
            result.frequency_plus_hz - result.frequency_minus_hz
        ) / 1.0e9
        self.fit_info_label.setText(
            f"Fit status: {result.status}\n"
            f"f_low: {result.frequency_minus_hz / 1.0e9:.9g} GHz\n"
            f"f_high: {result.frequency_plus_hz / 1.0e9:.9g} GHz\n"
            f"Splitting: {splitting_ghz:.9g} GHz\n"
            f"Low linewidth: {result.linewidth_minus_hz / 1.0e9:.6g} GHz\n"
            f"High linewidth: {result.linewidth_plus_hz / 1.0e9:.6g} GHz\n"
            f"Low amplitude: {result.amplitude_minus:.6g}%\n"
            f"High amplitude: {result.amplitude_plus:.6g}%\n"
            f"RMSE: {result.rmse:.6g}%\n"
            f"R²: {result.r2:.6g}"
        )

    def set_pixel_fit_summary(self, result):
        fraction = 100.0 * result.success_fraction
        self.pixel_fit_summary_label.setText(
            f"Valid spectra: {result.valid_pixel_count}\n"
            f"Successful fits: {result.successful_fit_count}\n"
            f"Failed fits: {result.failed_fit_count}\n"
            f"Success fraction: {fraction:.1f}%"
        )

    def set_selected_pixel_info(self, text):
        self.selected_pixel_label.setText(str(text))

    def set_selected_pixel_spectrum(self, frequency_hz, spectrum, fitted=None):
        self.pixel_measured_curve.setData(frequency_hz / 1.0e9, spectrum)
        if fitted is None:
            self.pixel_fitted_curve.setData([], [])
        else:
            self.pixel_fitted_curve.setData(frequency_hz / 1.0e9, fitted)

    def show_result_map(
        self,
        map_yx,
        title,
        units,
        *,
        categorical=False,
        status_legend="",
    ):
        """Render ``(y, x)`` data transposed so screen coordinates are ``(x, y)``."""
        values = np.asarray(map_yx)
        if values.ndim != 2:
            self.clear_result_map("Map data must have (y, x) shape.")
            return
        self._displayed_map = values
        self.map_title_label.setText(f"{title} [{units}]")
        self.status_legend_label.setText(status_legend)
        display_image = values.T

        if categorical:
            levels = (-0.5, 4.5)
            colors = [
                (40, 160, 70),
                (235, 180, 45),
                (120, 120, 120),
                (220, 75, 55),
                (150, 70, 190),
            ]
            epsilon = 1.0e-6
            positions = [0.0]
            categorical_colors = [colors[0]]
            for index in range(1, len(colors)):
                boundary = index / len(colors)
                positions.extend((boundary - epsilon, boundary + epsilon))
                categorical_colors.extend((colors[index - 1], colors[index]))
            positions.append(1.0)
            categorical_colors.append(colors[-1])
            color_map = pg.ColorMap(
                positions, categorical_colors
            )
        else:
            finite = np.isfinite(values)
            if not finite.any():
                self.clear_result_map(f"{title} has no valid pixels.")
                return
            low = float(np.min(values[finite]))
            high = float(np.max(values[finite]))
            if low == high:
                padding = max(abs(low) * 1e-6, 1e-12)
                low -= padding
                high += padding
            levels = (low, high)

            color_map = self._selected_color_map()

        self._is_categorical_map = bool(categorical)
        self._automatic_levels = levels
        self._set_scale_controls_enabled(not categorical)
        self._set_scale_control_values(levels)
        self.color_bar.setLabel("left", units)
        self.color_bar.setColorMap(color_map)
        self.color_bar.setLevels(levels)
        self.color_bar.show()

        self.image_item.setImage(
            display_image, autoLevels=False, levels=levels
        )
        self.image_view_box.autoRange()

    def _selected_color_map(self):
        return pg.colormap.get(
            self.colormap_combo.currentText().lower(), source="matplotlib"
        )

    def _set_scale_controls_enabled(self, enabled):
        for widget in (
            self.scale_min_spin,
            self.scale_max_spin,
            self.auto_scale_button,
            self.colormap_combo,
        ):
            widget.setEnabled(bool(enabled))

    def _set_scale_control_values(self, levels):
        spins = (self.scale_min_spin, self.scale_max_spin)
        blocked = [spin.blockSignals(True) for spin in spins]
        try:
            self.scale_min_spin.setValue(float(levels[0]))
            self.scale_max_spin.setValue(float(levels[1]))
        finally:
            for spin, was_blocked in zip(spins, blocked):
                spin.blockSignals(was_blocked)

    def _apply_manual_scale(self, *_args):
        if self._displayed_map is None or self._is_categorical_map:
            return
        low = self.scale_min_spin.value()
        high = self.scale_max_spin.value()
        if not low < high:
            return
        self.color_bar.setLevels((low, high))

    def restore_auto_scale(self):
        if self._displayed_map is None or self._is_categorical_map:
            return
        self._set_scale_control_values(self._automatic_levels)
        self.color_bar.setLevels(self._automatic_levels)

    def _on_colormap_changed(self, _name):
        if self._displayed_map is None or self._is_categorical_map:
            return
        self.color_bar.setColorMap(self._selected_color_map())

    def clear_result_map(self, message="No pixel-fit map available."):
        self._displayed_map = None
        self.image_item.clear()
        self.selection_marker.setData([])
        self.color_bar.hide()
        self.color_bar.setLabel("left", "")
        self._set_scale_controls_enabled(False)
        self.map_title_label.setText(message)
        self.status_legend_label.setText("")

    def set_map_enabled(self, enabled):
        self.map_combo.setEnabled(bool(enabled))

    def set_selected_map(self, name):
        index = self.map_combo.findText(name)
        if index >= 0 and index != self.map_combo.currentIndex():
            self.map_combo.setCurrentIndex(index)

    def mark_selected_pixel(self, x, y):
        if self._displayed_map is None or x is None or y is None:
            self.selection_marker.setData([])
            return
        # ImageItem pixel cells span [index, index + 1); mark their centers.
        self.selection_marker.setData([{"pos": (x + 0.5, y + 0.5)}])

    def clear_pixel_results(self):
        self.pixel_fit_summary_label.setText("No pixel fit available.")
        self.set_selected_pixel_info("Pixel: --")
        self.set_selected_pixel_spectrum(np.array([]), np.array([]))
        self.clear_result_map()
        self.set_map_enabled(False)
        self.set_selected_map("f_minus")

    def clear_dataset(self):
        self.set_metadata("No dataset loaded.")
        self.measured_curve.setData([], [])
        self.clear_fit()
        self.clear_pixel_results()
        self.set_actions(
            open_enabled=True,
            global_fit_enabled=False,
            pixel_fit_enabled=False,
            reconstruct_enabled=False,
        )

