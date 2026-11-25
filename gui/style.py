"""One place for the GUI's look: spacing, colours, cards and helpers.

Every window builds its control column from these helpers and from
gui/widgets/collapsible_section.py::CollapsibleSection, so a new feature
reuses the same look instead of hand-rolling margins, colours or
column-width measurement (see CLAUDE.md "GUI layout rules").

The app stylesheet (apply_app_style) only matches widgets this module marks:
group boxes with the dynamic property card="true", and the collapsible
section's own named parts. Everything else -- pyqtgraph's PlotWidget,
ImageView and their internal buttons and group boxes -- keeps the native
look. Nothing here changes any value a control holds.
"""

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QSpinBox,
)

# ---- layout constants (pixels) --------------------------------------------

COLUMN_SPACING = 8          # between cards/sections in a control column
CARD_MARGINS = (10, 6, 10, 8)  # inside a card or section body
ROW_SPACING = 6             # between rows of a form
SECTION_INDENT = 6          # body indent under a section header

# ---- colours ----------------------------------------------------------------

WARNING_COLOR = "#c06000"   # warnings in labels (e.g. zero direction)

# Run/stop buttons (gui/widgets/run_button.py): named colours, with the text
# colour explicit so the button reads the same in light and dark themes.
RUN_IDLE_BACKGROUND = "green"
RUN_RUNNING_BACKGROUND = "red"
RUN_BLOCKED_BACKGROUND = "#707070"
RUN_TEXT = "white"
RUN_BLOCKED_TEXT = "#f0f0f0"

CARD_PROPERTY = "card"

# The card title is bold through the card's own font; "> QWidget" resets the
# card's direct children (and so everything inside them) to normal weight.
# QGroupBox::title ignores font-weight. Don't nest cards: a card directly
# inside a card would get the normal weight.
APP_STYLESHEET = """
QGroupBox[card="true"] {
    border: 1px solid palette(mid);
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: bold;
}
QGroupBox[card="true"] > QWidget {
    font-weight: normal;
}
QGroupBox[card="true"]::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0px 3px;
}
QFrame#collapsibleSection {
    border: 1px solid palette(mid);
    border-radius: 6px;
}
QToolButton#sectionToggle {
    border: none;
    font-weight: bold;
    padding: 2px 0px;
}
"""


def apply_app_style(app):
    """Install the app stylesheet once, from the app's entry point."""
    app.setStyleSheet(APP_STYLESHEET)


# ---- building blocks --------------------------------------------------------

def mark_card(group_box):
    """Give an existing QGroupBox the card look (border, radius, title)."""
    group_box.setProperty(CARD_PROPERTY, True)
    return group_box


def card(title, parent=None, form=True):
    """A card: QGroupBox(title) with the card look and a form (or vertical)
    layout using the shared margins. Returns (group_box, layout)."""
    from PyQt6.QtWidgets import QVBoxLayout

    box = mark_card(QGroupBox(title, parent))
    layout = form_layout(box) if form else QVBoxLayout(box)
    layout.setContentsMargins(*CARD_MARGINS)
    layout.setSpacing(ROW_SPACING)
    return box, layout


def form_layout(parent=None):
    """Label/field rows: labels left, fields stretching to the column edge,
    so fields line up within a card and fill its width."""
    layout = QFormLayout(parent) if parent is not None else QFormLayout()
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setVerticalSpacing(ROW_SPACING)
    return layout


def button_row(*buttons):
    """Related buttons side by side, sharing the width equally."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    for button in buttons:
        row.addWidget(button, 1)
    return row


def double_spin(minimum, maximum, decimals, value, suffix="", tooltip="", step=None):
    """QDoubleSpinBox with the unit in its suffix (" GHz", " s", " G", ...)."""
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    if step is not None:
        spin.setSingleStep(step)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix if suffix.startswith(" ") else " " + suffix)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


def int_spin(minimum, maximum, value, suffix="", tooltip=""):
    """QSpinBox with an optional unit suffix."""
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix if suffix.startswith(" ") else " " + suffix)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


def set_unit_suffix(spin, unit):
    """Move a unit into an existing spin box's suffix (value unchanged)."""
    spin.setSuffix(" " + unit if unit else "")
    return spin


def muted(label):
    """Secondary text (status, summaries): the palette's placeholder colour,
    so it follows light and dark themes."""
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def warning_style(label, on=True):
    label.setStyleSheet(f"color: {WARNING_COLOR};" if on else "")
    return label


def status_label(text="", tooltip=""):
    """A word-wrapped status line for the fixed area under a run button."""
    label = QLabel(text)
    label.setWordWrap(True)
    if tooltip:
        label.setToolTip(tooltip)
    return label


def fit_column_width(scroll_area, content, also_measure=()):
    """Set *scroll_area*'s minimum width to *content*'s width plus the
    vertical scrollbar, so horizontal scrolling (off) is never needed.

    CollapsibleSection already reports its body's width while collapsed.
    *also_measure*: other widgets that are hidden now but may be shown
    later; they are shown just for the measurement.
    """
    hidden = [w for w in also_measure if w.isHidden()]
    for widget in hidden:
        widget.setVisible(True)
    width = content.sizeHint().width()
    for widget in hidden:
        widget.setVisible(False)
    scroll_area.setMinimumWidth(
        width
        + scroll_area.verticalScrollBar().sizeHint().width()
        + 2 * scroll_area.frameWidth()
    )
    return scroll_area.minimumWidth()
