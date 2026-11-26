"""The live view's "Normalization" controls as a collapsible section.

pyqtgraph's ImageView ships its own "Normalization" group box (opened from
the image's Menu button), laid out as one wide grid under the image. This
section takes over those same widgets -- the radio buttons, check boxes and
spin boxes pyqtgraph itself reads in ImageView.normalize() -- and puts them
in label/field rows in the shared section style. Their signals stay
connected to ImageView, so what they do is unchanged:

- Divide / Subtract / Off and the ROI / Frame / Time range boxes are read
  by ImageView.normalize(). The three sources only apply to image *stacks*
  (3-D data with a time axis); the live view shows single 2-D frames, so
  on it they change nothing visible.
- The blur spin boxes are not read anywhere in pyqtgraph 0.14; they are
  kept (and shown) as they were.

Folding only hides the controls. The Menu button's "Normalization" entry
opens and closes this section instead of pyqtgraph's own box, which stays
hidden.
"""

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QFormLayout, QHBoxLayout, QLabel

from gui.widgets.collapsible_section import CollapsibleSection

_NO_EFFECT_NOTE = (
    "pyqtgraph applies the ROI / Frame / Time range sources only to image "
    "stacks with a time axis; the live view shows single frames, so on it "
    "they have no visible effect."
)

OPERATION_TOOLTIP = (
    "How pyqtgraph normalizes the displayed image by the selected mean(s): "
    "Divide (image / mean), Subtract (image - mean) or Off. Display only: "
    "saved data and experiments never see it. " + _NO_EFFECT_NOTE
)
DIVIDE_TOOLTIP = "Divide the displayed image by the selected mean(s). Display only."
SUBTRACT_TOOLTIP = "Subtract the selected mean(s) from the displayed image. Display only."
OFF_TOOLTIP = "No normalization: the image is shown as acquired (default)."
SOURCES_TOOLTIP = (
    "Which mean to normalize by: the mean inside the yellow normalization "
    "ROI, each frame's own mean, or the mean over a time range. "
    + _NO_EFFECT_NOTE
)
ROI_TOOLTIP = (
    "Normalize by the mean inside the yellow normalization ROI (drawn on the "
    "image while this section is open). " + _NO_EFFECT_NOTE
)
FRAME_TOOLTIP = "Normalize by each frame's own mean. " + _NO_EFFECT_NOTE
TIME_RANGE_TOOLTIP = (
    "Normalize by the mean over a time range of the stack. " + _NO_EFFECT_NOTE
)
BLUR_TOOLTIP = (
    "pyqtgraph's {axis} blur setting ({unit}). Not used by pyqtgraph 0.14: "
    "changing it has no effect on the image."
)

OFF_SUMMARY = "Off"


def _tipped(text, tooltip):
    label = QLabel(text)
    label.setToolTip(tooltip)
    return label


def _row(*widgets):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return row


class ImageNormalizationSection(CollapsibleSection):
    """pyqtgraph ImageView's normalization controls, in a section."""

    def __init__(self, image_view, *, settings_key=None, parent=None):
        super().__init__(
            "Normalization", settings_key=settings_key, parent=parent,
            tooltip="Show or hide the live view's display normalization "
                    "(pyqtgraph). Only hides the controls; the line below "
                    "the title always shows what is set.")
        self.image_view = image_view
        ui = image_view.ui
        self.ui = ui

        self.divide_radio = ui.normDivideRadio
        self.subtract_radio = ui.normSubtractRadio
        self.off_radio = ui.normOffRadio
        self.roi_check = ui.normROICheck
        self.frame_check = ui.normFrameCheck
        self.time_range_check = ui.normTimeRangeCheck
        self.blur_spins = {
            "X": ui.normXBlurSpin,
            "Y": ui.normYBlurSpin,
            "T": ui.normTBlurSpin,
        }

        for widget, tooltip in (
            (self.divide_radio, DIVIDE_TOOLTIP),
            (self.subtract_radio, SUBTRACT_TOOLTIP),
            (self.off_radio, OFF_TOOLTIP),
            (self.roi_check, ROI_TOOLTIP),
            (self.frame_check, FRAME_TOOLTIP),
            (self.time_range_check, TIME_RANGE_TOOLTIP),
        ):
            widget.setToolTip(tooltip)
        for axis, spin in self.blur_spins.items():
            unit = "frames" if axis == "T" else "px"
            # Unit in the suffix (value and decimals unchanged).
            spin.setSuffix(f" {unit}")
            axis_name = "time" if axis == "T" else axis
            spin.setToolTip(BLUR_TOOLTIP.format(axis=axis_name, unit=unit))

        # Label/field rows that keep their natural width instead of
        # spreading across the whole live-view card.
        form = self.body_layout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.addRow(
            _tipped("Operation:", OPERATION_TOOLTIP),
            _row(self.divide_radio, self.subtract_radio, self.off_radio),
        )
        form.addRow(
            _tipped("Mean of:", SOURCES_TOOLTIP),
            _row(self.roi_check, self.frame_check, self.time_range_check),
        )
        for axis, spin in self.blur_spins.items():
            form.addRow(_tipped(f"Blur {axis}:", spin.toolTip()), spin)

        # pyqtgraph's own box is now empty; keep it hidden for good, and
        # let its Menu entry open/close this section instead.
        ui.normGroup.hide()
        if image_view.menu is None:
            image_view.buildMenu()
        self.menu_action = image_view.normAction
        try:
            self.menu_action.toggled.disconnect(image_view.normToggled)
        except TypeError:
            pass
        self.menu_action.toggled.connect(self.set_expanded)
        self.expanded_changed.connect(self._on_expanded)
        self._on_expanded(self.is_expanded())

        self.set_summary_provider(self.summary_text)

    def summary_text(self):
        if self.off_radio.isChecked():
            return OFF_SUMMARY
        operation = "Divide" if self.divide_radio.isChecked() else "Subtract"
        sources = [
            name for name, check in (
                ("ROI", self.roi_check),
                ("frame", self.frame_check),
                ("time range", self.time_range_check),
            ) if check.isChecked()
        ]
        target = (
            f" by {' + '.join(sources)} mean" if sources
            else " (no mean selected)"
        )
        blur = " / ".join(
            f"{axis} {spin.value():g}" for axis, spin in self.blur_spins.items()
        )
        return f"{operation}{target}, blur {blur}"

    def _on_expanded(self, expanded):
        # What pyqtgraph's normToggled() did, minus showing its own box:
        # the normalization ROI / time region are drawn only while the
        # controls are shown.
        with QSignalBlocker(self.menu_action):
            self.menu_action.setChecked(expanded)
        view = self.image_view
        view.normRoi.setVisible(expanded and self.roi_check.isChecked())
        view.normRgn.setVisible(expanded and self.time_range_check.isChecked())
