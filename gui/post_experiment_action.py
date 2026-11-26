"""What the magnet does when an experiment (or a whole run queue) ends.

One app-wide setting: "Field zero at diamond" (calibrated 0 G, the
default) or "Electrical off". MainWindow owns one PostExperimentMagnetAction
and applies it on its experiment_finished signal, so it runs after every
experiment and queue whether or not the Magnet window was ever opened. The
Magnet window's "After experiment:" combo only shows and changes this
setting.

The action is applied only while the magnet is enabled. After a Zero Field
run it is therefore skipped: the experiment's own cleanup has already
switched the coils off.
"""

import logging

from PyQt6.QtCore import QObject, pyqtSignal

LOGGER = logging.getLogger(__name__)

POST_EXPERIMENT_FIELD_ZERO = "Field zero at diamond"
POST_EXPERIMENT_ELECTRICAL_OFF = "Electrical off"
POST_EXPERIMENT_CHOICES = (POST_EXPERIMENT_FIELD_ZERO, POST_EXPERIMENT_ELECTRICAL_OFF)

FIELD_ZERO_MESSAGE = (
    "After experiment: calibrated 0 G commanded on all axes; "
    "compensation currents may be nonzero."
)
ELECTRICAL_OFF_MESSAGE = "After experiment: magnet de-energized, outputs off."


class PostExperimentMagnetAction(QObject):
    """The post-experiment magnet setting and the code that applies it."""

    action_changed = pyqtSignal(str)
    # Status text after apply() did something (or failed).
    applied = pyqtSignal(str)

    def __init__(self, magnet, action=POST_EXPERIMENT_FIELD_ZERO, parent=None):
        super().__init__(parent)
        self.magnet = magnet
        self._action = POST_EXPERIMENT_FIELD_ZERO
        self.set_action(action)

    def action(self):
        return self._action

    def set_action(self, action):
        if action not in POST_EXPERIMENT_CHOICES:
            raise ValueError(f"Unknown post-experiment action: {action!r}")
        if action == self._action:
            return
        self._action = action
        self.action_changed.emit(action)

    def apply(self):
        """Apply the selected state if the magnet is on. Returns the status
        text, or None when nothing was done (no magnet, or magnet off)."""
        magnet = self.magnet
        if magnet is None or not magnet.is_enabled():
            return None
        try:
            if self._action == POST_EXPERIMENT_ELECTRICAL_OFF:
                magnet.disable()
                message = ELECTRICAL_OFF_MESSAGE
            else:
                magnet.set_vector(bx=0.0, by=0.0, bz=0.0)
                message = FIELD_ZERO_MESSAGE
        except Exception as err:
            LOGGER.exception("Post-experiment magnet action failed")
            message = f"After-experiment action failed: {err}"
        self.applied.emit(message)
        return message
