from PyQt6.QtWidgets import ( # type: ignore
    QDialog,
    QVBoxLayout,
)

from gui.widgets.pulse_sequence_viewer import (
    PulseSequenceViewer
)
from gui.close_confirmation import CloseConfirmation, ConfirmCloseDialogMixin


class PulseSequenceWindow(ConfirmCloseDialogMixin, QDialog):

    """
    Generic Pulse Sequence Inspector.

    Displays the actual PulseSequence object
    that will be sent to the Pulse Streamer.
    """

    # =====================================================
    # INIT
    # =====================================================

    def __init__(
        self,
        sequence,
        channel_map,
        parent=None,
    ):

        super().__init__(parent)

        self.setWindowTitle(
            "Pulse Sequence Inspector"
        )

        self.resize(1200, 650)

        layout = QVBoxLayout(self)

        self.viewer = PulseSequenceViewer()

        layout.addWidget(self.viewer)

        self.viewer.set_sequence(
            sequence,
            channel_map
        )

    def close_confirmation(self):
        return CloseConfirmation(
            "Close Pulse Sequence Inspector",
            "Close the pulse sequence inspector?",
            [
                "It only displays the sequence; nothing is lost and no "
                "hardware is affected.",
                "Reopen it with \"Show Pulse Sequence\" in the ODMR window.",
            ],
        )
