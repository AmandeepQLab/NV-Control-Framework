"""Manual initial fit parameters, shared by the ODMR and ODMR analysis windows.

Values are entered in the units the ODMR window has always used: resonance
locations in GHz, contrast in percent, FWHM in MHz. Blank optional fields
mean "use the automatic guess" for that parameter; the baseline is always
automatic.
"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)


SINGLE_LORENTZIAN = "Single Lorentzian"
DOUBLE_LORENTZIAN = "Double Lorentzian"
# Labels and order of the ODMR window's fit-type selector.
FIT_TYPES = (SINGLE_LORENTZIAN, DOUBLE_LORENTZIAN)

CLEAR_BUTTON_TEXT = "Clear / use automatic guesses"


class InitialFitParametersDialog(QDialog):
    """Collect optional manual initial values for an ODMR Lorentzian fit.

    ``allow_clear`` adds a "Clear / use automatic guesses" button. Pressing
    it closes the dialog as accepted with ``cleared`` set to True, without
    validating the fields. The ODMR window does not pass it, so its dialog
    is unchanged.
    """

    def __init__(self, fit_type, initial_values=None, parent=None, *, allow_clear=False):
        super().__init__(parent)
        self.fit_type = fit_type
        self.cleared = False
        self.setWindowTitle("Supply Initial Fit Parameters")
        form = QFormLayout(self)
        self.fields = {}
        self.labels = {}
        if fit_type == SINGLE_LORENTZIAN:
            specs = [("f0", "Resonance location (GHz):", True),
                     ("contrast", "Contrast (%):", False),
                     ("fwhm", "FWHM (MHz):", False)]
        else:
            specs = [("f01", "Resonance location 1 (GHz):", True),
                     ("f02", "Resonance location 2 (GHz):", True),
                     ("contrast1", "Contrast 1 (%):", False),
                     ("fwhm1", "FWHM 1 (MHz):", False),
                     ("contrast2", "Contrast 2 (%):", False),
                     ("fwhm2", "FWHM 2 (MHz):", False)]
        initial_values = initial_values or {}
        for key, label, required in specs:
            field = QLineEdit(self)
            field.setPlaceholderText("Required" if required else "Automatic if blank")
            if initial_values.get(key) is not None:
                field.setText(str(initial_values[key]))
            self.fields[key] = field
            label_widget = QLabel(label, self)
            self.labels[key] = label_widget
            form.addRow(label_widget, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel, self)
        self.clear_button = None
        if allow_clear:
            self.clear_button = buttons.addButton(
                CLEAR_BUTTON_TEXT, QDialogButtonBox.ButtonRole.ResetRole
            )
            self.clear_button.clicked.connect(self.clear_and_accept)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def parsed_values(self):
        required = {"f0"} if self.fit_type == SINGLE_LORENTZIAN else {"f01", "f02"}
        values = {}
        for key, field in self.fields.items():
            raw = field.text().strip()
            if not raw:
                if key in required:
                    raise ValueError(f"{self.labels[key].text().rstrip(':')} is required.")
                values[key] = None
                continue
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"{key} must be a finite numeric value.") from None
            if not np.isfinite(value):
                raise ValueError(f"{key} must be a finite numeric value.")
            if key not in required and value <= 0:
                raise ValueError(f"{key} must be positive.")
            values[key] = value
        if self.fit_type == DOUBLE_LORENTZIAN and values["f01"] == values["f02"]:
            raise ValueError("The two resonance locations must be different.")
        return values

    def accept(self):
        try:
            self.parsed_values()
        except ValueError as error:
            QMessageBox.warning(self, "Invalid Initial Fit Parameters", str(error))
            return
        super().accept()

    def clear_and_accept(self):
        """Discard manual values: the caller returns to automatic guesses."""
        self.cleared = True
        QDialog.accept(self)


def manual_resonance_locations(fit_type, manual):
    """Return the manual resonance locations (GHz) in field order."""
    if fit_type == SINGLE_LORENTZIAN:
        return [manual["f0"]]
    return [manual["f01"], manual["f02"]]


def check_manual_locations_in_range(fit_type, manual, low_ghz, high_ghz):
    """Raise ``ValueError`` if a manual resonance lies outside the measured range."""
    locations = manual_resonance_locations(fit_type, manual)
    if any(location < low_ghz or location > high_ghz for location in locations):
        raise ValueError(
            f"Resonance locations must lie within the measured range ({low_ghz:g}-{high_ghz:g} GHz)."
        )


def manual_initial_guess(fit_type, manual):
    """Convert dialog values to a fit-order ``initial_guess`` list in GHz and %.

    The order is that of ``fit_single_lorentzian`` (baseline, contrast, f0,
    FWHM) or ``fit_double_lorentzian`` (baseline, contrast1, f01, FWHM1,
    contrast2, f02, FWHM2). ``None`` entries mean "use the automatic guess";
    the baseline is always ``None``. FWHM is converted from MHz to GHz.
    """
    if manual is None:
        return None
    if fit_type == SINGLE_LORENTZIAN:
        return [
            None, manual["contrast"], manual["f0"],
            None if manual["fwhm"] is None else manual["fwhm"] / 1000.0,
        ]
    return [
        None, manual["contrast1"], manual["f01"],
        None if manual["fwhm1"] is None else manual["fwhm1"] / 1000.0,
        manual["contrast2"], manual["f02"],
        None if manual["fwhm2"] is None else manual["fwhm2"] / 1000.0,
    ]


_DESCRIPTION_UNITS = {
    "f0": " GHz", "f01": " GHz", "f02": " GHz",
    "contrast": "%", "contrast1": "%", "contrast2": "%",
    "fwhm": " MHz", "fwhm1": " MHz", "fwhm2": " MHz",
}


def describe_initial_parameters(manual):
    """One-line human-readable record of supplied values (or "automatic")."""
    if manual is None:
        return "automatic"
    parts = []
    for key, value in manual.items():
        text = "auto" if value is None else f"{value:g}{_DESCRIPTION_UNITS.get(key, '')}"
        parts.append(f"{key}={text}")
    return "supplied (" + ", ".join(parts) + ")"
