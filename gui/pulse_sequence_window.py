from PyQt6.QtWidgets import ( # type: ignore
    QDialog,
    QVBoxLayout,
)

from gui.widgets.pulse_sequence_viewer import (
    PulseSequenceViewer
)


class PulseSequenceWindow(QDialog):

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