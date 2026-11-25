"""ODMR panel group: the magnetic field held at the diamond during a scan.

Display and form only: what the modes do is in
experiments/odmr_experiment.py::ODMRExperiment.setup_magnet_field().
"""

from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QLabel,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from experiments.odmr_experiment import (
    FIELD_MODE_CUSTOM, FIELD_MODE_LEAVE, FIELD_MODE_ZERO,
    VECTOR_ENTRY_COMPONENTS, VECTOR_ENTRY_DIRECTION,
)
from framework.field_direction import unit_direction, vector_along

# Combo text -> config value, in display order (default first).
MODE_TEXTS = {
    "Field zero at diamond": FIELD_MODE_ZERO,
    "Custom vector": FIELD_MODE_CUSTOM,
    "Leave magnet as is": FIELD_MODE_LEAVE,
}
ENTRY_TEXTS = {
    "Components (X, Y, Z)": VECTOR_ENTRY_COMPONENTS,
    "Direction + magnitude": VECTOR_ENTRY_DIRECTION,
}
# Quick-pick directions (coil axes; the sample's <100> edges lie along them).
DIRECTION_PRESETS = (
    ("X", (1.0, 0.0, 0.0), "Direction (1, 0, 0): along the X coil axis."),
    ("Y", (0.0, 1.0, 0.0), "Direction (0, 1, 0): along the Y coil axis."),
    ("Z", (0.0, 0.0, 1.0), "Direction (0, 0, 1): along the Z coil axis."),
    ("<110>", (1.0, 1.0, 0.0), "Direction (1, 1, 0): X = Y, Z = 0."),
    ("<1-10>", (1.0, -1.0, 0.0), "Direction (1, -1, 0): X = -Y, Z = 0."),
    ("<111>", (1.0, 1.0, 1.0), "Direction (1, 1, 1): X = Y = Z."),
)
DEFAULT_DIRECTION = (1.0, 1.0, 1.0)  # as the Zero Field window

GROUP_TOOLTIP = (
    "Magnetic field at the diamond for the whole ODMR scan. It is set once, "
    "before the first frequency point, and not changed during the scan. "
    "The mode, the commanded and modelled field are saved with the data."
)
MODE_TOOLTIP = (
    "Field zero at diamond enables the magnet and cancels the lab background "
    "(calibrated zero) for the whole scan. Use it for zero-field ESR: with the "
    "coils off, the ~1.2 G lab background splits and broadens the lines.\n"
    "Custom vector enables the magnet and holds the field entered below for "
    "the whole scan, e.g. a bias field.\n"
    "Leave magnet as is keeps whatever the magnet window set, including coils "
    "off, and sends no magnet command.\n"
    "An unreachable field is refused before the scan starts. After the scan "
    "the magnet window's \"After experiment\" action applies as usual."
)
ENTRY_TOOLTIP = (
    "How the custom vector is entered. Used only with \"Custom vector\".\n"
    "Components (X, Y, Z): type each axis's field in gauss.\n"
    "Direction + magnitude: pick a direction (nx, ny, nz) and one signed "
    "magnitude in gauss; X, Y, Z = magnitude x normalised direction. Use it "
    "to put a field along a crystal direction such as <110> or <111>.\n"
    "Both are applied the same way: the X, Y, Z components are commanded "
    "through the same magnet path, with the same relay and settling handling."
)
DIRECTION_TOOLTIP = (
    "Component {name} of the field direction (unitless). Any nonzero "
    "(nx, ny, nz) works: it is normalised automatically, so (1, 1, 0) and "
    "(0.707, 0.707, 0) are the same direction. Used only with \"Direction + "
    "magnitude\". A zero vector has no direction and the scan is refused."
)
MAGNITUDE_TOOLTIP = (
    "Field magnitude along the direction, in gauss (G). Negative values point "
    "the field the opposite way. Calibrated: 0 G is calibrated zero at the "
    "diamond on all axes. The resulting X, Y, Z are shown below and checked "
    "against each axis's reachable range before the scan starts."
)
APPLIED_TOOLTIP = (
    "Read-only: the X, Y, Z field (G) this entry will command, magnitude x "
    "normalised direction. It is exactly what \"Components (X, Y, Z)\" would "
    "command with these values, and is saved as the commanded vector."
)
NORMALISED_TOOLTIP = "Read-only: the normalised direction n̂ the magnitude is applied along."
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


def _gauss_spin(tooltip):
    spin = QDoubleSpinBox()
    spin.setRange(-10000.0, 10000.0)
    spin.setDecimals(4)
    spin.setSingleStep(0.1)
    spin.setValue(0.0)
    spin.setSuffix(" G")
    spin.setToolTip(tooltip)
    return spin


class MagnetFieldGroup(QGroupBox):
    """"Magnetic field during scan": mode plus a custom vector in G, entered
    as X/Y/Z components or as a magnitude along a direction."""

    def __init__(self, parent=None):
        super().__init__("Magnetic field during scan", parent)
        self.setToolTip(GROUP_TOOLTIP)
        layout = QFormLayout(self)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(MODE_TEXTS))
        self.mode_combo.setToolTip(MODE_TOOLTIP)
        layout.addRow(QLabel("Mode:"), self.mode_combo)

        self.entry_combo = QComboBox()
        self.entry_combo.addItems(list(ENTRY_TEXTS))
        self.entry_combo.setToolTip(ENTRY_TOOLTIP)
        layout.addRow(QLabel("Enter as:"), self.entry_combo)

        # One page per entry. A stacked widget's size hint covers both pages,
        # so the panel's column width fits the wider direction page too.
        self.entry_stack = QStackedWidget()
        layout.addRow(self.entry_stack)

        components_page = QWidget()
        components_layout = QFormLayout(components_page)
        components_layout.setContentsMargins(0, 0, 0, 0)
        self.component_spins = {}
        for axis in ("X", "Y", "Z"):
            spin = _gauss_spin(_component_tooltip(axis))
            components_layout.addRow(QLabel(f"{axis} (G):"), spin)
            self.component_spins[axis] = spin
        self.x_spin = self.component_spins["X"]
        self.y_spin = self.component_spins["Y"]
        self.z_spin = self.component_spins["Z"]
        self.entry_stack.addWidget(components_page)

        direction_page = QWidget()
        direction_layout = QVBoxLayout(direction_page)
        direction_layout.setContentsMargins(0, 0, 0, 0)
        # One row per component, like X/Y/Z, so the column stays as narrow
        # as before this entry existed.
        direction_form = QFormLayout()
        direction_form.setContentsMargins(0, 0, 0, 0)
        self.direction_spins = []
        for name in ("nx", "ny", "nz"):
            spin = QDoubleSpinBox()
            spin.setRange(-1000.0, 1000.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)
            spin.setToolTip(DIRECTION_TOOLTIP.format(name=name))
            direction_form.addRow(QLabel(f"{name}:"), spin)
            self.direction_spins.append(spin)
        direction_layout.addLayout(direction_form)

        # Presets in a 2 x 3 grid: coil axes, then crystal directions.
        preset_grid = QGridLayout()
        self.direction_preset_buttons = {}
        for index, (text, direction, tooltip) in enumerate(DIRECTION_PRESETS):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda _checked=False, d=direction: self.set_field_direction(d)
            )
            preset_grid.addWidget(button, index // 3, index % 3)
            self.direction_preset_buttons[text] = button
        direction_layout.addLayout(preset_grid)

        self.normalised_label = QLabel()
        self.normalised_label.setToolTip(NORMALISED_TOOLTIP)
        direction_layout.addWidget(self.normalised_label)

        magnitude_form = QFormLayout()
        magnitude_form.setContentsMargins(0, 0, 0, 0)
        self.magnitude_spin = _gauss_spin(MAGNITUDE_TOOLTIP)
        magnitude_form.addRow(QLabel("Magnitude:"), self.magnitude_spin)
        direction_layout.addLayout(magnitude_form)

        self.applied_label = QLabel()
        self.applied_label.setWordWrap(True)
        self.applied_label.setToolTip(APPLIED_TOOLTIP)
        direction_layout.addWidget(self.applied_label)
        self.entry_stack.addWidget(direction_page)

        self.set_field_direction(DEFAULT_DIRECTION)

        self._available = True
        self.mode_combo.currentTextChanged.connect(self._update_enabled)
        self.entry_combo.currentTextChanged.connect(self._update_entry_page)
        for spin in self.direction_spins:
            spin.valueChanged.connect(self._update_direction_labels)
        self.magnitude_spin.valueChanged.connect(self._update_direction_labels)
        self._update_entry_page()
        self._update_direction_labels()
        self._update_enabled()

    # ---- mode -----------------------------------------------------------

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

    # ---- custom-vector entry --------------------------------------------

    def vector_entry(self):
        return ENTRY_TEXTS[self.entry_combo.currentText()]

    def set_vector_entry(self, entry):
        for text, value in ENTRY_TEXTS.items():
            if value == entry:
                self.entry_combo.setCurrentText(text)
                return
        raise ValueError(f"Unknown magnet vector entry {entry!r}")

    def raw_field_direction(self):
        """(nx, ny, nz) as entered, not normalised."""
        return [spin.value() for spin in self.direction_spins]

    def set_field_direction(self, direction):
        for spin, value in zip(self.direction_spins, direction):
            spin.setValue(float(value))

    def set_field_magnitude(self, magnitude):
        self.magnitude_spin.setValue(float(magnitude))

    def direction_components(self):
        """[x, y, z] G of the direction entry, or None for a zero direction."""
        try:
            return vector_along(self.magnitude_spin.value(), self.raw_field_direction())
        except ValueError:
            return None

    # ---- availability / state -------------------------------------------

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
        self.entry_combo.setEnabled(custom)
        self.entry_stack.setEnabled(custom)

    def _update_entry_page(self, _text=None):
        current = 0 if self.vector_entry() == VECTOR_ENTRY_COMPONENTS else 1
        # Width covers both pages (so the column never clips); height follows
        # the shown page only, so the components page has no blank space.
        for index in range(self.entry_stack.count()):
            self.entry_stack.widget(index).setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred if index == current
                else QSizePolicy.Policy.Ignored,
            )
        self.entry_stack.setCurrentIndex(current)
        self.entry_stack.adjustSize()

    def _update_direction_labels(self, _value=None):
        try:
            unit = unit_direction(self.raw_field_direction())
        except ValueError:
            self.normalised_label.setText("⚠ Zero vector: choose a direction.")
            self.normalised_label.setStyleSheet("color: #c06000;")
            self.applied_label.setText("Applied: none (a scan will be refused)")
            return
        self.normalised_label.setStyleSheet("")
        self.normalised_label.setText(
            "n̂ = (" + ", ".join(f"{n:.3f}" for n in unit) + ")"
        )
        x, y, z = self.direction_components()
        self.applied_label.setText(
            f"Applied: X {x:.4f}, Y {y:.4f}, Z {z:.4f} G"
        )

    def get_config(self):
        """Config keys. Without a magnet the mode is reported as
        "leave_as_is", which is what the scan then does.

        magnet_custom_vector_gauss is always the X, Y, Z a custom scan
        commands: the typed components, or (direction entry) magnitude x
        normalised direction -- None for a zero direction, which the window
        refuses before a scan starts.
        """
        entry = self.vector_entry()
        if entry == VECTOR_ENTRY_DIRECTION:
            vector = self.direction_components()
        else:
            vector = [self.x_spin.value(), self.y_spin.value(), self.z_spin.value()]
        try:
            direction = unit_direction(self.raw_field_direction())
        except ValueError:
            direction = self.raw_field_direction()
        return {
            "magnet_field_mode": self.mode() if self._available else FIELD_MODE_LEAVE,
            "magnet_vector_entry": entry,
            "magnet_custom_vector_gauss": vector,
            "magnet_field_direction": direction,
            "magnet_field_magnitude_gauss": self.magnitude_spin.value(),
        }
