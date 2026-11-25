"""CollapsibleSection: a titled block of controls that can be folded away.

    [v] Title                                   [x] On
        one-line summary (only while collapsed)
        body: the section's controls

- The arrow/title button only shows or hides the body. It never changes a
  setting: controls in a collapsed body keep their values and are read by
  get_config() exactly as when shown.
- An optional header checkbox is for features that are on/off settings
  (dense points, stability check, averaging, ...). It is the feature's own
  existing QCheckBox, moved into the header, so its signals and the config
  key it drives are unchanged. Ticking it also expands the section;
  unticking leaves the section as it is.
- The summary line tells what the folded controls are set to. It is
  refreshed from a provider callable whenever a control in the body (or
  the header checkbox) changes, or when refresh_summary() is called.
- The section reports its body's width even while collapsed, so a control
  column sized from it never clips when the section is opened.
- Open/closed state can be remembered across restarts (QSettings). It is
  off unless the app's entry point calls enable_state_persistence(), so
  tests, scripts and harnesses always get the documented defaults.
"""

import os

from PyQt6.QtCore import QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from gui import style

SETTINGS_ORGANIZATION = "NV Control"
SETTINGS_APPLICATION = "nv_control"
# Points QSettings at an INI file instead of the registry (checks, scratch runs).
SETTINGS_FILE_ENV = "NV_CONTROL_UI_STATE_FILE"

_state_store = None


def enable_state_persistence(settings=None):
    """Remember open/closed sections from now on. Called by the app's main().

    Without *settings*: the registry (HKCU\\Software\\NV Control\\nv_control),
    or the INI file named by the NV_CONTROL_UI_STATE_FILE environment
    variable. Only section open/closed state is stored -- never a setting.
    """
    global _state_store
    if settings is None:
        path = os.environ.get(SETTINGS_FILE_ENV)
        settings = (
            QSettings(path, QSettings.Format.IniFormat) if path
            else QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        )
    _state_store = settings
    return settings


def disable_state_persistence():
    global _state_store
    _state_store = None


def state_persistence_enabled():
    return _state_store is not None


def _to_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


_CHANGE_SIGNALS = (
    (QCheckBox, "toggled"),
    (QAbstractButton, None),
    (QAbstractSpinBox, "valueChanged"),
    (QComboBox, "currentTextChanged"),
    (QLineEdit, "textChanged"),
)


class CollapsibleSection(QFrame):
    expanded_changed = pyqtSignal(bool)

    def __init__(self, title, *, expanded=False, checkbox=None,
                 settings_key=None, tooltip="", parent=None):
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        self._title = title
        self._settings_key = settings_key
        self._summary_provider = None
        self._summary_text = ""

        outer = QVBoxLayout(self)
        left, top, right, bottom = style.CARD_MARGINS
        outer.setContentsMargins(left - 4, 2, right, bottom - 4)
        outer.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("sectionToggle")
        self.toggle_button.setText(title.replace("&", "&&"))  # no mnemonic
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setToolTip(
            tooltip or f"Show or hide the \"{title}\" settings. Only hides "
            "them: values are kept and used as set."
        )
        header.addWidget(self.toggle_button)
        header.addStretch(1)

        self.header_checkbox = checkbox
        if checkbox is not None:
            header.addWidget(checkbox)
            checkbox.toggled.connect(self._on_header_checkbox)
        outer.addLayout(header)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("sectionSummary")
        self.summary_label.setWordWrap(True)
        # Never widen the column for a long summary: wrap to the width given.
        self.summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.summary_label.setContentsMargins(style.SECTION_INDENT + 14, 0, 0, 2)
        style.muted(self.summary_label)
        outer.addWidget(self.summary_label)

        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self._body_layout = style.form_layout(self.body)
        self._body_layout.setContentsMargins(style.SECTION_INDENT, 2, 0, 2)
        outer.addWidget(self.body)

        if _state_store is not None and settings_key:
            stored = _state_store.value(self._state_key())
            if stored is not None:
                expanded = _to_bool(stored)
        self.toggle_button.setChecked(bool(expanded))
        self._apply_expanded(bool(expanded))
        self.toggle_button.toggled.connect(self._on_toggled)

    # ---- public API ------------------------------------------------------

    def title(self):
        return self._title

    def body_layout(self):
        """The body's QFormLayout: addRow(label, field) or addRow(widget)."""
        return self._body_layout

    def is_expanded(self):
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded):
        self.toggle_button.setChecked(bool(expanded))

    def set_summary(self, text):
        """Static summary text (used while collapsed)."""
        self._summary_text = text or ""
        self._update_summary_visibility()

    def set_summary_provider(self, provider):
        """provider() -> summary text. Re-evaluated when any control now in
        the body, or the header checkbox, changes."""
        self._summary_provider = provider
        for widget in self.body.findChildren(QWidget):
            self.watch(widget)
        if self.header_checkbox is not None:
            self.header_checkbox.toggled.connect(self.refresh_summary)
        self.refresh_summary()

    def watch(self, widget):
        """Refresh the summary when *widget*'s value changes."""
        for kind, signal in _CHANGE_SIGNALS:
            if isinstance(widget, kind):
                if signal is None:
                    if isinstance(widget, QAbstractButton) and widget.isCheckable():
                        widget.toggled.connect(self.refresh_summary)
                else:
                    getattr(widget, signal).connect(self.refresh_summary)
                return

    def refresh_summary(self, *_args):
        if self._summary_provider is not None:
            self._summary_text = self._summary_provider() or ""
        self._update_summary_visibility()

    def summary(self):
        return self._summary_text

    # ---- size: report the body's width even when collapsed ---------------

    def _with_body_width(self, hint, body_hint):
        margins = self.layout().contentsMargins()
        width = body_hint.width() + margins.left() + margins.right()
        return QSize(max(hint.width(), width), hint.height())

    def sizeHint(self):
        return self._with_body_width(super().sizeHint(), self.body.sizeHint())

    def minimumSizeHint(self):
        return self._with_body_width(
            super().minimumSizeHint(), self.body.minimumSizeHint()
        )

    # ---- internals ---------------------------------------------------------

    def _state_key(self):
        return f"sections/{self._settings_key}/expanded"

    def _on_toggled(self, expanded):
        self._apply_expanded(expanded)
        if _state_store is not None and self._settings_key:
            _state_store.setValue(self._state_key(), bool(expanded))
            _state_store.sync()
        self.expanded_changed.emit(expanded)

    def _apply_expanded(self, expanded):
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        self._update_summary_visibility()

    def _update_summary_visibility(self):
        self.summary_label.setText(self._summary_text)
        self.summary_label.setVisible(
            bool(self._summary_text) and not self.toggle_button.isChecked()
        )

    def _on_header_checkbox(self, checked):
        if checked:
            self.set_expanded(True)
