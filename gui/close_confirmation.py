"""Every window asks before it closes (CLAUDE.md "GUI layout rules").

A window mixes in ConfirmCloseMixin (or ConfirmCloseDialogMixin for a
QDialog) *before* its Qt base class and implements close_confirmation(),
which describes -- from the state at the moment of closing -- what closing
does and what is unsaved:

    class MyWindow(ConfirmCloseMixin, QMainWindow):
        def close_confirmation(self):
            return CloseConfirmation(
                "Close My Window", "Close the My window?",
                ["The last result is not saved.", "Settings are kept."])

        def confirmed_close_event(self, event):   # optional
            ...what closing does once confirmed...

The dialog: Yes (labelled e.g. "Close") and Cancel; Cancel is the default,
and Esc or the dialog's own X answer Cancel. There is no "don't ask again".

Closes that need no question:
- a window that is not visible (nothing on screen to lose sight of);
- close_without_confirmation(window): closes the code makes itself, e.g.
  MainWindow closing every window at exit after its own question, or a
  close deferred until a job ends after the user already confirmed.

Tests and unattended runs never block on the dialog: set_auto_answer(True /
False) or the auto_answer() context manager, or the environment variable
NV_CONTROL_AUTO_CONFIRM_CLOSE=yes|no (for the app run in a subprocess).
Every confirmation asked (or auto-answered) is appended to `history`, so a
test can check what the window said.

Modal prompts that themselves ask a question (QMessageBox, file dialogs,
the initial-fit-parameters dialog) are not windows in this sense: closing
them is their own Cancel answer.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field

from PyQt6.QtWidgets import QMessageBox

AUTO_ANSWER_ENV = "NV_CONTROL_AUTO_CONFIRM_CLOSE"

_auto_answer = None
# Every CloseConfirmation asked or auto-answered, oldest first.
history = []


@dataclass(frozen=True)
class CloseConfirmation:
    """What one window says when it is about to close."""

    title: str
    question: str
    facts: tuple = field(default_factory=tuple)
    confirm_text: str = "Close"

    def __post_init__(self):
        object.__setattr__(self, "facts", tuple(f for f in self.facts if f))

    def text(self):
        """Question and facts as one plain text (logs, tests)."""
        return "\n".join([self.question, *(f"• {fact}" for fact in self.facts)])


def set_auto_answer(answer):
    """True: every confirmation is answered "close"; False: "Cancel"; None:
    ask the user (the default)."""
    global _auto_answer
    _auto_answer = None if answer is None else bool(answer)


def auto_answer_setting():
    return _auto_answer


@contextmanager
def auto_answer(answer):
    previous = _auto_answer
    set_auto_answer(answer)
    try:
        yield history
    finally:
        set_auto_answer(previous)


def _effective_auto_answer():
    if _auto_answer is not None:
        return _auto_answer
    value = os.environ.get(AUTO_ANSWER_ENV, "").strip().lower()
    if value in ("yes", "1", "true"):
        return True
    if value in ("no", "0", "false"):
        return False
    return None


def confirm_close(parent, confirmation):
    """Ask; True to close. Cancel is the default; Esc / X mean Cancel."""
    history.append(confirmation)
    answer = _effective_auto_answer()
    if answer is not None:
        return answer
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(confirmation.title)
    box.setText(confirmation.question)
    if confirmation.facts:
        box.setInformativeText(
            "\n".join(f"• {fact}" for fact in confirmation.facts))
    yes = box.addButton(QMessageBox.StandardButton.Yes)
    yes.setText(confirmation.confirm_text)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def close_without_confirmation(window):
    """Close *window* without asking (a close the code makes itself, after
    the user has already confirmed elsewhere). Returns QWidget.close()'s
    result."""
    window._closing_without_confirmation = True
    try:
        return window.close()
    finally:
        try:
            window._closing_without_confirmation = False
        except RuntimeError:  # deleted on close
            pass


class ConfirmCloseMixin:
    """closeEvent(): ask close_confirmation(), then confirmed_close_event().

    Put it before the Qt base class: class W(ConfirmCloseMixin, QMainWindow).
    """

    def close_confirmation(self):
        """The window-specific CloseConfirmation for the current state."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement close_confirmation(): every "
            "window confirms before closing with a window-specific message."
        )

    def confirmed_close_event(self, event):
        """What closing does once confirmed (default: Qt's own close)."""
        super().closeEvent(event)

    def closeEvent(self, event):
        ask = (
            self.isVisible()
            and not getattr(self, "_closing_without_confirmation", False)
        )
        if ask and not confirm_close(self, self.close_confirmation()):
            event.ignore()
            return
        self._close_confirmed = True
        try:
            self.confirmed_close_event(event)
        finally:
            self._close_confirmed = False


class ConfirmCloseDialogMixin(ConfirmCloseMixin):
    """For a non-modal QDialog: Esc (reject()) goes through the confirmation
    too, instead of hiding the dialog directly."""

    def reject(self):
        if getattr(self, "_close_confirmed", False) or not self.isVisible():
            super().reject()
            return
        self.close()


def describe_magnet(magnet):
    """"coils on, model field (x, y, z) G" / "coils off" / None (no magnet).

    Calibration-model values, not measurements."""
    if magnet is None:
        return None
    try:
        enabled = magnet.is_enabled()
    except Exception:
        return "state unknown"
    if not enabled:
        return "coils off (0 A, outputs off)"
    try:
        vector = magnet.get_vector()
        parts = []
        for axis in ("x", "y", "z"):
            value = vector.get(axis)
            parts.append("?" if value is None else f"{value * 10.0:.4g}")
        return f"coils on, model field ({', '.join(parts)}) G"
    except Exception:
        return "coils on"
