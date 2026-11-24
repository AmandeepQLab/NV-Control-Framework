"""Stability-check controls shared by the ODMR and Zero Field windows.

StabilitySettingsGroup: the "Stability check" group in each window's
scrollable panel. StabilityWaitBar: the wait line with "Start anyway" and
"Cancel", fixed under the scroll area and shown only while waiting.
See gui/stability_gate.py and framework/stability.py.
"""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from framework.stability import ON_TIMEOUT_CHOICES, StabilitySettings

UNAVAILABLE_TOOLTIP = (
    "Not available: this window was opened without the main window's live "
    "view, which supplies the counts."
)


class StabilitySettingsGroup(QGroupBox):
    """Checkbox plus window/limits/timeout; fields enabled only when on."""

    def __init__(self, parent=None):
        super().__init__("Stability check", parent)
        layout = QVBoxLayout(self)
        defaults = StabilitySettings()

        self.enabled_check = QCheckBox("Check count stability before start")
        self.enabled_check.setToolTip(
            "Before starting, wait until the live counts are stable. Costs "
            "time; useful when focus or air flow drifts. The counts are the "
            "main window's live-view mean over the acquisition ROI; the live "
            "view is started for the check if it is off."
        )
        layout.addWidget(self.enabled_check)

        self.window_spin = self._add_spin(
            layout, "Window (s):", 5.0, 3600.0, 0, defaults.window_s,
            "How long the counts must stay within the limits before the run "
            "starts (seconds). Longer windows catch slow oscillations "
            "(25–40 s at the rig) but take longer to pass.",
        )
        self.max_std_spin = self._add_spin(
            layout, "Max std (%):", 0.01, 100.0, 2, defaults.max_std_percent,
            "Largest allowed standard deviation of the counts over the "
            "window, as % of the mean. Lower is stricter; it must sit above "
            "the counts' own shot noise or the check never passes.",
        )
        self.max_drift_spin = self._add_spin(
            layout, "Max drift (%/min):", 0.01, 100.0, 2,
            defaults.max_drift_percent_per_min,
            "Largest allowed steady rise or fall of the counts, in % of the "
            "mean per minute (straight-line fit over the window). Lower is "
            "stricter.",
        )
        self.timeout_spin = self._add_spin(
            layout, "Timeout (s):", 1.0, 86400.0, 0, defaults.timeout_s,
            "Longest time to wait for stable counts (seconds). After it, "
            "\"On timeout\" decides whether the run starts anyway or is "
            "cancelled. Should be longer than the window.",
        )

        row = QHBoxLayout()
        on_timeout_label = QLabel("On timeout:")
        row.addWidget(on_timeout_label)
        self.on_timeout_combo = QComboBox()
        self.on_timeout_combo.addItems(ON_TIMEOUT_CHOICES)
        self.on_timeout_combo.setToolTip(
            "What happens if the counts are not stable within the timeout: "
            "\"Start anyway\" starts the run (the file records that it timed "
            "out); \"Cancel this run\" does not start it (in a queue the "
            "entry is marked \"skipped (unstable)\" and the queue continues)."
        )
        on_timeout_label.setToolTip(self.on_timeout_combo.toolTip())
        row.addWidget(self.on_timeout_combo)
        layout.addLayout(row)

        self.fields = (
            self.window_spin, self.max_std_spin, self.max_drift_spin,
            self.timeout_spin, self.on_timeout_combo,
        )
        self.enabled_check.toggled.connect(self._update_enabled)
        self._available = True
        self._update_enabled()

    @staticmethod
    def _add_spin(layout, label, minimum, maximum, decimals, default, tooltip):
        row = QHBoxLayout()
        text = QLabel(label)
        text.setToolTip(tooltip)
        row.addWidget(text)
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(default)
        spin.setToolTip(tooltip)
        row.addWidget(spin)
        layout.addLayout(row)
        return spin

    def controls(self):
        """Every user-facing control in the group (for tooltip checks)."""
        return (self.enabled_check,) + self.fields

    def _update_enabled(self, _checked=None):
        on = self._available and self.enabled_check.isChecked()
        for field in self.fields:
            field.setEnabled(on)

    def set_available(self, available):
        """Disable the whole check when no live view can supply counts."""
        self._available = bool(available)
        self.enabled_check.setEnabled(self._available)
        if not self._available:
            self.enabled_check.setChecked(False)
            self.setToolTip(UNAVAILABLE_TOOLTIP)
        self._update_enabled()

    def get_settings(self):
        return StabilitySettings(
            enabled=self._available and self.enabled_check.isChecked(),
            window_s=self.window_spin.value(),
            max_std_percent=self.max_std_spin.value(),
            max_drift_percent_per_min=self.max_drift_spin.value(),
            timeout_s=self.timeout_spin.value(),
            on_timeout=self.on_timeout_combo.currentText(),
        )

    def set_settings(self, settings):
        self.window_spin.setValue(settings.window_s)
        self.max_std_spin.setValue(settings.max_std_percent)
        self.max_drift_spin.setValue(settings.max_drift_percent_per_min)
        self.timeout_spin.setValue(settings.timeout_s)
        self.on_timeout_combo.setCurrentText(settings.on_timeout)
        self.enabled_check.setChecked(settings.enabled and self._available)


class StabilityWaitBar(QWidget):
    """Wait line plus "Start anyway" / "Cancel"; hidden unless waiting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setToolTip(
            "Stability check in progress: std and drift of the live counts "
            "over the window, against their limits."
        )
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        self.start_anyway_button = QPushButton("Start anyway")
        self.start_anyway_button.setToolTip(
            "Stop waiting and start the run now. The file records "
            "\"started anyway by user\" and the std/drift reached so far."
        )
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip(
            "Stop waiting and do not start this run. In a queue the entry is "
            "marked stopped and the queue continues."
        )
        row.addWidget(self.start_anyway_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.setVisible(False)

    def controls(self):
        return (self.status_label, self.start_anyway_button, self.cancel_button)

    def show_waiting(self):
        self.status_label.setText("Waiting for stable counts…")
        self.setVisible(True)

    def set_status(self, text):
        self.status_label.setText(text)

    def hide_waiting(self):
        self.setVisible(False)
