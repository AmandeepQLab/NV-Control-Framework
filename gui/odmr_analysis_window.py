"""Hardware-independent window for offline ODMR statistics analysis."""

from enum import Enum
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QDialog, QFileDialog, QMainWindow, QMessageBox

from analysis.odmr_fit import (
    fit_global_odmr_spectrum,
    fit_single_lorentzian,
    global_mean_odmr_spectrum,
    two_lorentzian_guess_with_overrides,
    two_lorentzian_odmr_model,
)
from analysis.odmr_magnetic import reconstruct_bz
from analysis.odmr_pixel_fit import PIXEL_FIT_STATUS_LABELS, PixelFitStatus
from framework.analysis.odmr_statistics import (
    calculate_odmr_statistics,
    load_odmr_statistics,
)
from gui.odmr_analysis_worker import ODMRPixelFitWorker
from gui.close_confirmation import (
    CloseConfirmation,
    ConfirmCloseMixin,
    close_without_confirmation,
)
from gui.panels.odmr_analysis_panel import ODMRAnalysisPanel
from gui.widgets.initial_fit_parameters_dialog import (
    DOUBLE_LORENTZIAN,
    FIT_TYPES,
    SINGLE_LORENTZIAN,
    InitialFitParametersDialog,
    check_manual_locations_in_range,
    describe_initial_parameters,
    manual_initial_guess,
)


class ODMRAnalysisState(str, Enum):
    EMPTY = "EMPTY"
    LOADED = "LOADED"
    GLOBAL_FIT_AVAILABLE = "GLOBAL_FIT_AVAILABLE"
    PIXEL_FIT_AVAILABLE = "PIXEL_FIT_AVAILABLE"
    MAGNETIC_MAP_AVAILABLE = "MAGNETIC_MAP_AVAILABLE"


def display_position_to_pixel(image_x, image_y, map_shape):
    """Convert ImageItem coordinates to source ``(x, y)`` for a ``(y, x)`` map.

    Pixel cells occupy ``[index, index + 1)`` in ImageItem coordinates. Bounds
    are checked before integer conversion so a slightly negative coordinate
    cannot truncate to zero and select a border pixel.
    """
    height, width = map_shape
    if not np.isfinite(image_x) or not np.isfinite(image_y):
        return None
    if not (0.0 <= image_x < width and 0.0 <= image_y < height):
        return None
    return int(np.floor(image_x)), int(np.floor(image_y))


class ODMRAnalysisWindow(ConfirmCloseMixin, QMainWindow):
    """Load an ODMR statistics file and inspect global and pixel-wise results."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ODMR Analysis")
        self.resize(1400, 950)

        self.panel = ODMRAnalysisPanel(self)
        self.setCentralWidget(self.panel)
        self.panel.open_file_requested.connect(self.open_file)
        self.panel.fit_global_requested.connect(self.fit_global_spectrum)
        self.panel.initial_fit_parameters_requested.connect(
            self.supply_initial_fit_parameters
        )
        self.panel.fit_type_changed.connect(self._update_initial_parameter_text)
        self.panel.fit_pixels_requested.connect(self.fit_pixels)
        self.panel.reconstruct_bz_requested.connect(self.reconstruct_magnetic_map)
        self.panel.map_selection_requested.connect(self.show_selected_map)
        self.panel.map_pixel_clicked.connect(self.select_pixel_from_image)

        self.selected_source_path = None
        self.loaded_odmr_cube = None
        self.statistics_result = None
        self.global_spectrum = None
        # Two-Lorentzian (Double) result only: it seeds pixel fitting. A
        # Single Lorentzian result is kept in single_fit_result instead.
        self.global_fit_result = None
        self.single_fit_result = None
        # What the last global fit used: {"fit_type", "initial_parameters"}.
        self.global_fit_settings = None
        # Manual initial values per fit type, kept for this window's lifetime
        # (not cleared by loading another file), as in the ODMR window.
        self.initial_fit_guesses = {fit_type: None for fit_type in FIT_TYPES}
        self.pixel_fit_result = None
        self.magnetic_result = None
        self.selected_pixel = None
        self.state = ODMRAnalysisState.EMPTY

        self.pixel_worker = None
        self.pixel_thread = None
        self._close_after_pixel_fit = False
        self._update_initial_parameter_text()
        self._update_actions()

    # ------------------------------------------------------------------
    # Initial fit parameters
    # ------------------------------------------------------------------

    def supply_initial_fit_parameters(self):
        """Open the shared dialog for the selected fit type."""
        fit_type = self.panel.get_fit_type()
        dialog = InitialFitParametersDialog(
            fit_type, self.initial_fit_guesses[fit_type], self, allow_clear=True
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.set_initial_fit_parameters(
            fit_type, None if dialog.cleared else dialog.parsed_values()
        )

    def set_initial_fit_parameters(self, fit_type, values):
        """Store dialog values for ``fit_type``; ``None`` means automatic."""
        if fit_type not in self.initial_fit_guesses:
            raise ValueError(f"Unknown fit type: {fit_type}")
        self.initial_fit_guesses[fit_type] = None if values is None else dict(values)
        self._update_initial_parameter_text()

    def _initial_parameter_text(self, fit_type):
        manual = self.initial_fit_guesses[fit_type]
        text = f"Initial parameters ({fit_type}): {describe_initial_parameters(manual)}."
        if manual is None:
            for other in FIT_TYPES:
                if other != fit_type and self.initial_fit_guesses[other] is not None:
                    text += f" Values supplied for {other} are not used with {fit_type}."
        return text

    def _update_initial_parameter_text(self, *_args):
        self.panel.set_initial_parameters_text(
            self._initial_parameter_text(self.panel.get_fit_type())
        )

    def _settings_text(self):
        settings = self.global_fit_settings
        if settings is None:
            return ""
        return (
            f"Fit type: {settings['fit_type']}\n"
            "Initial parameters: "
            f"{describe_initial_parameters(settings['initial_parameters'])}"
        )

    def open_file(self):
        if self._pixel_fit_active():
            return
        start_directory = (
            str(Path(self.selected_source_path).parent)
            if self.selected_source_path
            else ""
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open ODMR Statistics HDF5",
            start_directory,
            "HDF5 files (*.h5 *.hdf5);;All files (*)",
        )
        if not path:
            return
        self.load_file(path)

    def load_file(self, source_path):
        """Load and process one file; clear prior results before attempting it."""
        if self._pixel_fit_active():
            self.panel.set_status("Wait for pixel fitting to finish before loading another file.")
            return False
        self._clear_session()
        self.selected_source_path = str(source_path)
        self.panel.set_status(f"Loading {self.selected_source_path} …")
        try:
            cube = load_odmr_statistics(self.selected_source_path)
            statistics = calculate_odmr_statistics(cube)
            spectrum = global_mean_odmr_spectrum(statistics.final_mean)
            if not np.isfinite(spectrum).any():
                raise ValueError("The loaded ODMR data contain no usable finite pixels.")

            self.loaded_odmr_cube = cube
            self.statistics_result = statistics
            self.global_spectrum = spectrum
            self.state = ODMRAnalysisState.LOADED
            self.panel.set_metadata(self._format_metadata(cube, statistics))
            self.panel.set_spectrum(statistics.frequency_values, spectrum)
            self.panel.clear_fit()
            self.panel.set_status("ODMR statistics loaded. The global spectrum is ready.")
            self._update_actions()
            return True
        except Exception as error:
            self.panel.clear_dataset()
            self.panel.set_status(f"Load failed: {error}")
            self._update_actions()
            QMessageBox.warning(self, "ODMR Load Error", str(error))
            return False

    def fit_global_spectrum(self):
        """Fit the loaded final mean with the selected fit type.

        Double Lorentzian uses the canonical bounded fitter
        (``fit_global_odmr_spectrum``), whose result seeds pixel fitting.
        Single Lorentzian uses the ODMR window's ``fit_single_lorentzian``
        on the global mean spectrum; pixel fitting and Bz need Double.
        Manual initial parameters for the selected type are used when set.
        Every failure is reported in the status line and a message box.
        """
        if self.statistics_result is None or self._pixel_fit_active():
            return None
        fit_type = self.panel.get_fit_type()
        manual = self.initial_fit_guesses.get(fit_type)
        self._invalidate_pixel_and_magnetic_results()
        self.global_fit_result = None
        self.single_fit_result = None
        self.global_fit_settings = {
            "fit_type": fit_type,
            "initial_parameters": None if manual is None else dict(manual),
        }
        self.state = ODMRAnalysisState.LOADED
        self.panel.clear_fit("Global fit is running…")
        self.panel.set_status(f"Fitting the global ODMR spectrum ({fit_type})…")
        self._update_actions()
        try:
            frequency_hz = self.statistics_result.frequency_values
            if manual is not None:
                finite = np.isfinite(self.global_spectrum)
                measured_ghz = frequency_hz[finite] / 1.0e9
                check_manual_locations_in_range(
                    fit_type, manual,
                    float(np.min(measured_ghz)), float(np.max(measured_ghz)),
                )
            if fit_type == SINGLE_LORENTZIAN:
                return self._fit_global_single(manual)

            kwargs = {}
            if manual is not None:
                kwargs["initial_guess"] = two_lorentzian_guess_with_overrides(
                    frequency_hz,
                    self.global_spectrum,
                    _ghz_guess_to_hz(manual_initial_guess(DOUBLE_LORENTZIAN, manual)),
                )
            result = fit_global_odmr_spectrum(
                self.statistics_result.final_mean,
                frequency_hz,
                **kwargs,
            )
            self.global_fit_result = result
            self.panel.set_fit_result(result, self._settings_text())
            if not result.success:
                self.panel.set_status(f"Global fit failed: {result.message}")
                QMessageBox.warning(self, "ODMR Fit Error", result.message)
                self._update_actions()
                return result

            self.state = ODMRAnalysisState.GLOBAL_FIT_AVAILABLE
            self.panel.set_status(
                f"Global {fit_type} fit completed "
                f"(initial parameters: {'supplied' if manual else 'automatic'})."
            )
            self._update_actions()
            return result
        except Exception as error:
            self.global_fit_result = None
            self.single_fit_result = None
            settings = self._settings_text()
            prefix = f"{settings}\n" if settings else ""
            self.panel.clear_fit(f"{prefix}Fit failed: {error}")
            self.panel.set_status(f"Global fit failed: {error}")
            self._update_actions()
            QMessageBox.warning(self, "ODMR Fit Error", str(error))
            return None

    def _fit_global_single(self, manual):
        """Single Lorentzian on the global mean, via the ODMR window's fitter."""
        finite = np.isfinite(self.global_spectrum)
        frequency_ghz = self.statistics_result.frequency_values[finite] / 1.0e9
        spectrum = self.global_spectrum[finite]
        initial_guess = manual_initial_guess(SINGLE_LORENTZIAN, manual)
        fit = fit_single_lorentzian(
            frequency_ghz,
            spectrum,
            **({} if initial_guess is None else {"initial_guess": initial_guess}),
        )
        if not np.all(np.isfinite(np.asarray(fit["params"], dtype=float))):
            raise RuntimeError("Single Lorentzian fit returned non-finite parameters.")
        self.single_fit_result = fit
        self.panel.set_single_fit_result(frequency_ghz, fit, self._settings_text())
        self.panel.set_status(
            f"Global {SINGLE_LORENTZIAN} fit completed "
            f"(initial parameters: {'supplied' if manual else 'automatic'}). "
            "Pixel fitting and Bz need a Double Lorentzian global fit."
        )
        self._update_actions()
        return fit

    def fit_pixels(self):
        """Start per-pixel fitting on a dedicated worker thread."""
        if (
            self.global_fit_result is None
            or not self.global_fit_result.success
            or self._pixel_fit_active()
        ):
            return False

        self._invalidate_pixel_and_magnetic_results()
        self.state = ODMRAnalysisState.GLOBAL_FIT_AVAILABLE
        self.pixel_worker = ODMRPixelFitWorker(
            self.statistics_result.final_mean,
            self.global_fit_result,
        )
        self.pixel_thread = QThread(self)
        self.pixel_worker.moveToThread(self.pixel_thread)

        self.pixel_thread.started.connect(self.pixel_worker.run)
        self.pixel_worker.started.connect(self._pixel_fit_started)
        self.pixel_worker.finished.connect(self._pixel_fit_finished)
        self.pixel_worker.failed.connect(self._pixel_fit_failed)
        self.pixel_worker.finished.connect(self.pixel_thread.quit)
        self.pixel_worker.failed.connect(self.pixel_thread.quit)
        self.pixel_thread.finished.connect(self.pixel_worker.deleteLater)
        self.pixel_thread.finished.connect(self._pixel_fit_thread_finished)
        self.pixel_thread.finished.connect(self.pixel_thread.deleteLater)

        self.panel.set_status("Fitting pixels…")
        self._update_actions()
        self.pixel_thread.start()
        return True

    def _pixel_fit_started(self):
        self.panel.set_status("Fitting pixels…")

    def _pixel_fit_finished(self, result):
        self.pixel_fit_result = result
        self.state = ODMRAnalysisState.PIXEL_FIT_AVAILABLE
        self.panel.set_pixel_fit_summary(result, self._pixel_seed_text())
        if result.successful_fit_count == 0:
            self.panel.set_selected_map("fit_status")
            self.panel.set_status(
                "Pixel fitting completed: no pixels fitted successfully. "
                "Inspect the status map for diagnostics."
            )
        else:
            self.panel.set_selected_map("f_minus")
            self.panel.set_status(
                f"Pixel fitting completed: {result.successful_fit_count} successful, "
                f"{result.failed_fit_count} failed."
            )
        self.panel.set_map_enabled(True)
        self.show_selected_map(self.panel.map_combo.currentText())
        self._update_selected_pixel_display()

    def _pixel_seed_text(self):
        """Record how pixel fits were seeded (always the global Double fit)."""
        settings = self.global_fit_settings
        if settings is None:
            return "Seeded from the global fit."
        return (
            f"Seeded from the global {settings['fit_type']} fit "
            "(centres and linewidths; baseline and amplitudes per pixel). "
            "Global fit initial parameters: "
            f"{describe_initial_parameters(settings['initial_parameters'])}."
        )

    def _pixel_fit_failed(self, error_message):
        self.pixel_fit_result = None
        self.magnetic_result = None
        self.selected_pixel = None
        self.state = ODMRAnalysisState.GLOBAL_FIT_AVAILABLE
        self.panel.clear_pixel_results()
        self.panel.set_status(f"Pixel fitting failed: {error_message}")
        QMessageBox.warning(self, "ODMR Pixel Fit Error", error_message)

    def _pixel_fit_thread_finished(self):
        self.pixel_worker = None
        self.pixel_thread = None
        self._update_actions()
        if self._close_after_pixel_fit:
            # The user already confirmed the close; don't ask again.
            self._close_after_pixel_fit = False
            close_without_confirmation(self)

    def reconstruct_magnetic_map(self):
        """Run the existing Bz reconstruction on completed pixel-fit maps."""
        if (
            self.pixel_fit_result is None
            or self.pixel_fit_result.successful_fit_count == 0
            or self._pixel_fit_active()
        ):
            return None
        try:
            result = reconstruct_bz(self.pixel_fit_result)
        except Exception as error:
            self.magnetic_result = None
            self.state = ODMRAnalysisState.PIXEL_FIT_AVAILABLE
            self.panel.set_status(f"Bz reconstruction failed: {error}")
            self._update_selected_pixel_display()
            self._update_actions()
            QMessageBox.warning(self, "Bz Reconstruction Error", str(error))
            return None

        if not np.any(result.valid_mask & np.isfinite(result.bz_magnitude)):
            self.magnetic_result = None
            self.state = ODMRAnalysisState.PIXEL_FIT_AVAILABLE
            self.panel.set_status("Bz reconstruction returned no valid finite pixels.")
            self._update_actions()
            return result

        self.magnetic_result = result
        self.state = ODMRAnalysisState.MAGNETIC_MAP_AVAILABLE
        self.panel.set_status("Bz magnitude map reconstructed (non-negative, in Gauss).")
        self.panel.set_selected_map("Bz magnitude")
        self.show_selected_map(self.panel.map_combo.currentText())
        self._update_selected_pixel_display()
        self._update_actions()
        return result

    def show_selected_map(self, name):
        """Display the selected pixel-fit or magnetic-result map."""
        result = self.pixel_fit_result
        if result is None:
            self.panel.clear_result_map()
            return

        map_data = {
            "f_minus": (result.f_minus_hz / 1.0e9, "f_minus", "GHz"),
            "f_plus": (result.f_plus_hz / 1.0e9, "f_plus", "GHz"),
            "splitting": (result.splitting_hz / 1.0e9, "splitting", "GHz"),
            "RMSE": (result.rmse, "RMSE", "%"),
            "fit_status": (result.status, "Fit status", "category"),
        }
        if name == "Bz magnitude":
            if self.magnetic_result is None:
                self.panel.clear_result_map("Reconstruct Bz to display this map.")
                return
            values, title, units = (
                self.magnetic_result.bz_magnitude,
                "Bz magnitude (non-negative)",
                "G",
            )
            categorical = False
            legend = ""
        else:
            entry = map_data.get(name)
            if entry is None:
                self.panel.clear_result_map("Unknown result map.")
                return
            values, title, units = entry
            categorical = name == "fit_status"
            legend = ""
            if categorical:
                legend = "Status codes: " + " | ".join(
                    f"{int(status)}={label}"
                    for status, label in PIXEL_FIT_STATUS_LABELS.items()
                )

        self.panel.show_result_map(
            values,
            title,
            units,
            categorical=categorical,
            status_legend=legend,
        )
        if self.selected_pixel is None:
            self.panel.mark_selected_pixel(None, None)
        else:
            self.panel.mark_selected_pixel(*self.selected_pixel)

    def select_pixel_from_image(self, image_x, image_y):
        if self.pixel_fit_result is None or self.panel._displayed_map is None:
            return None
        pixel = display_position_to_pixel(
            image_x, image_y, self.pixel_fit_result.status.shape
        )
        if pixel is None:
            return None
        x, y = pixel
        self.selected_pixel = (x, y)
        self.panel.mark_selected_pixel(x, y)
        self._update_selected_pixel_display()
        return pixel

    def _update_selected_pixel_display(self):
        if self.selected_pixel is None or self.pixel_fit_result is None:
            self.panel.set_selected_pixel_info("Pixel: --")
            self.panel.set_selected_pixel_spectrum(np.array([]), np.array([]))
            return

        x, y = self.selected_pixel
        result = self.pixel_fit_result
        stats = self.statistics_result
        status_code = int(result.status[y, x])
        status = None
        try:
            status = PixelFitStatus(status_code)
            status_label = PIXEL_FIT_STATUS_LABELS[status]
        except (ValueError, KeyError):
            status_label = f"unknown status ({status_code})"

        f_minus = result.f_minus_hz[y, x]
        f_plus = result.f_plus_hz[y, x]
        splitting = result.splitting_hz[y, x]
        rmse = result.rmse[y, x]
        r2 = result.r2[y, x]
        bz_text = "--"
        if self.magnetic_result is not None and self.magnetic_result.valid_mask[y, x]:
            bz = self.magnetic_result.bz_magnitude[y, x]
            if np.isfinite(bz):
                bz_text = f"{bz:.7g} G"

        def frequency_text(value):
            return f"{value / 1.0e9:.9g} GHz" if np.isfinite(value) else "--"

        def number_text(value, unit=""):
            return f"{value:.6g}{unit}" if np.isfinite(value) else "--"

        self.panel.set_selected_pixel_info(
            f"Pixel: x={x}, y={y}\n"
            f"f_low: {frequency_text(f_minus)}\n"
            f"f_high: {frequency_text(f_plus)}\n"
            f"Splitting: {frequency_text(splitting)}\n"
            f"Bz magnitude: {bz_text}\n"
            f"RMSE: {number_text(rmse, '%')}\n"
            f"R²: {number_text(r2)}\n"
            f"Fit status: {status_label}"
        )

        frequency_hz = stats.frequency_values
        spectrum = stats.final_mean[:, y, x]
        fitted = None
        if status == PixelFitStatus.SUCCESS:
            fitted = two_lorentzian_odmr_model(
                frequency_hz,
                result.baseline[y, x],
                result.amplitude_minus[y, x],
                result.f_minus_hz[y, x],
                result.linewidth_minus_hz[y, x],
                result.amplitude_plus[y, x],
                result.f_plus_hz[y, x],
                result.linewidth_plus_hz[y, x],
            )
        self.panel.set_selected_pixel_spectrum(frequency_hz, spectrum, fitted)

    def _invalidate_pixel_and_magnetic_results(self):
        self.pixel_fit_result = None
        self.magnetic_result = None
        self.selected_pixel = None
        self.panel.clear_pixel_results()

    def _clear_session(self):
        self.selected_source_path = None
        self.loaded_odmr_cube = None
        self.statistics_result = None
        self.global_spectrum = None
        self.global_fit_result = None
        self.single_fit_result = None
        self.global_fit_settings = None
        self._invalidate_pixel_and_magnetic_results()
        self.state = ODMRAnalysisState.EMPTY
        self.panel.clear_dataset()

    def _pixel_fit_active(self):
        return self.pixel_thread is not None

    def _update_actions(self):
        busy = self._pixel_fit_active()
        has_data = self.statistics_result is not None
        has_global_fit = (
            self.global_fit_result is not None and self.global_fit_result.success
        )
        has_pixel_result = self.pixel_fit_result is not None
        can_reconstruct = (
            has_pixel_result
            and self.pixel_fit_result.successful_fit_count > 0
            and self.magnetic_result is None
        )
        self.panel.set_actions(
            open_enabled=not busy,
            global_fit_enabled=has_data and not busy,
            pixel_fit_enabled=has_global_fit and not busy,
            reconstruct_enabled=can_reconstruct and not busy,
        )
        self.panel.set_map_enabled(has_pixel_result and not busy)

    def _format_metadata(self, cube, statistics):
        metadata = cube.metadata
        configured_averages = metadata.get("configured_averages", "—")
        configured_repeats = metadata.get("configured_repeats", "—")
        repeats = ", ".join(str(value) for value in statistics.n_repeats_per_average)
        if metadata.get("experiment_complete") is True:
            completion = "Complete"
        elif metadata.get("interrupted") or metadata.get("stopped_by_user"):
            completion = "Interrupted"
        else:
            completion = "Incomplete / status unavailable"

        height, width = statistics.spatial_shape
        return (
            f"File: {self.selected_source_path}\n"
            f"Configured averages: {configured_averages}\n"
            f"Completed averages: {statistics.n_averages}\n"
            f"Configured repeats: {configured_repeats}\n"
            f"Repeats per average: {repeats or '—'}\n"
            f"Image dimensions: {width} × {height} (x × y)\n"
            f"Frequency points: {statistics.frequency_values.size}\n"
            f"Acquisition status: {completion}"
        )

    def results_text(self):
        """What has been computed from the loaded file, or None."""
        done = []
        if self.global_fit_result is not None:
            done.append("global double-Lorentzian fit")
        elif self.single_fit_result is not None:
            done.append("global single-Lorentzian fit")
        if self.pixel_fit_result is not None:
            done.append("pixel fit maps")
        if self.magnetic_result is not None:
            done.append("Bz map")
        return ", ".join(done) if done else None

    def unsaved_results(self):
        results = self.results_text()
        return [f"analysis results not saved ({results})"] if results else []

    def pixel_fit_running(self):
        return self.pixel_thread is not None and self.pixel_thread.isRunning()

    def close_confirmation(self):
        standalone = self.parent() is None
        facts = []
        if self.selected_source_path:
            facts.append(f"Loaded file: {self.selected_source_path}.")
        else:
            facts.append("No file loaded.")
        results = self.results_text()
        if results:
            facts.append(f"Results: {results}. They are not saved to any file "
                         "(this window has no export).")
        if self.pixel_fit_running():
            facts.append("A pixel fit is running: the window closes when it finishes.")
        facts.append(
            "Closing ends the analysis program; everything shown here is lost."
            if standalone else
            "Closing only hides this window; the results stay until the app "
            "exits (reopen it with ODMR Analysis in the ODMR window).")
        return CloseConfirmation(
            "Close ODMR Analysis", "Close the ODMR Analysis window?", facts)

    def confirmed_close_event(self, event):
        if self.pixel_fit_running():
            self._close_after_pixel_fit = True
            self.panel.set_status("Pixel fitting is still running; the window will close when it finishes.")
            event.ignore()
            return
        super().confirmed_close_event(event)


def _ghz_guess_to_hz(guess):
    """Convert a double-Lorentzian GHz/% guess list to Hz, keeping ``None``.

    Indices 2/5 are centres and 3/6 FWHMs (GHz); amplitudes are unchanged.
    """
    return [
        None if value is None else (value * 1.0e9 if index in (2, 3, 5, 6) else value)
        for index, value in enumerate(guess)
    ]
