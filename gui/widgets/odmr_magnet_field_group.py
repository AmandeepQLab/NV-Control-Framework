"""ODMR panel group: the magnetic field held at the diamond during a scan.

Display and form only: what the modes do is in
experiments/odmr_experiment.py::ODMRExperiment.setup_magnet_field().
"""

from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
)

from experiments.odmr_experiment import (
    FIELD_MODE_CUSTOM, FIELD_MODE_LEAVE, FIELD_MODE_ZERO,
)

# Combo text -> config value, in display order (default first).
MODE_TEXTS = {
    "Field zero at diamond": FIELD_MODE_ZERO,
    "Custom vector": FIELD_MODE_CUSTOM,
    "Leave magnet as is": FIELD_MODE_LEAVE,
}

GROUP_TOOLTIP = (
    "Magnetic field at the diamond for the whole ODMR scan. It is set once, "
    "before the first frequency point, and not changed during the scan. "
    "The mode, the commanded and modelled field are saved with the data."
)
MODE_TOOLTIP = (
    "Field zero at diamond enables the magnet and cancels the lab background "
    "(calibrated zero) for the whole scan. Use it for zero-field ESR: with the "
    "coils off, the ~1.2 G lab background splits and broadens the lines.\n"
    "Custom vector enables the magnet and holds the X, Y, Z field below for "
    "the whole scan, e.g. a bias field.\n"
    "Leave magnet as is keeps whatever the magnet window set, including coils "
    "off, and sends no magnet command.\n"
    "An unreachable field is refused before the scan starts. After the scan "
    "the magnet window's \"After experiment\" action applies as usual."
)
UNAVAILABLE_TOOLTIP = (
    "No magnet in the hardware configuration, so this is disabled: the scan "
    "sends no magnet command, as with \"Leave magnet as is\"."
)


def _component_tooltip(axis):
    return (
        f"{axis} component of the total field at the diamond during the scan, "
        "in gauss (G). Calibrated: 0 G cancels the lab background on this "
        "axis. Used only with \"Custom vector\". Checked against the axis's "
        "reachable range before the scan starts; positive and negative "
        "values select the coil's relay branch automatically."
    )


class MagnetFieldGroup(QGroupBox):
    """"Magnetic field during scan": mode plus a custom X/Y/Z vector in G."""

    def __init__(self, parent=None):
        super().__init__("Magnetic field during scan", parent)
        self.setToolTip(GROUP_TOOLTIP)
        layout = QFormLayout(self)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(MODE_TEXTS))
        self.mode_combo.setToolTip(MODE_TOOLTIP)
        layout.addRow(QLabel("Mode:"), self.mode_combo)

        self.component_spins = {}
        for axis in ("X", "Y", "Z"):
            spin = QDoubleSpinBox()
            spin.setRange(-10000.0, 10000.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)
            spin.setValue(0.0)
            spin.setSuffix(" G")
            spin.setToolTip(_component_tooltip(axis))
            layout.addRow(QLabel(f"{axis} (G):"), spin)
            self.component_spins[axis] = spin
        self.x_spin = self.component_spins["X"]
        self.y_spin = self.component_spins["Y"]
        self.z_spin = self.component_spins["Z"]

        self._available = True
        self.mode_combo.currentTextChanged.connect(self._update_enabled)
        self._update_enabled()

    def mode(self):
        """The selected mode's config value."""
        return MODE_TEXTS[self.mode_combo.currentText()]

    def set_mode(self, mode):
        for text, value in MODE_TEXTS.items():
            if value == mode:
                self.mode_combo.setCurrentText(text)
                return
        raise ValueError(f"Unknown magnet field mode {mode!r}")

    def set_custom_vector(self, x, y, z):
        for spin, value in zip((self.x_spin, self.y_spin, self.z_spin), (x, y, z)):
            spin.setValue(value)

    def set_available(self, available):
        """Disable the group (with a reason) when there is no magnet."""
        self._available = bool(available)
        self.setEnabled(self._available)
        self.setToolTip(GROUP_TOOLTIP if self._available else UNAVAILABLE_TOOLTIP)
        self.mode_combo.setToolTip(
            MODE_TOOLTIP if self._available else UNAVAILABLE_TOOLTIP
        )

    def is_available(self):
        return self._available

    def _update_enabled(self):
        custom = self.mode() == FIELD_MODE_CUSTOM
        for spin in self.component_spins.values():
            spin.setEnabled(custom)

    def get_config(self):
        """Config keys. Without a magnet the mode is reported as
        "leave_as_is", which is what the scan then does."""
        return {
            "magnet_field_mode": self.mode() if self._available else FIELD_MODE_LEAVE,
            "magnet_custom_vector_gauss": [
                self.x_spin.value(), self.y_spin.value(), self.z_spin.value(),
            ],
        }
