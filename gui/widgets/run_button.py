"""Shared styling for the experiment windows' run/stop toggle buttons.

One definition for the ODMR and Zero Field windows: green when idle, red
while running, grey when blocked (disabled). The background and text
colours are both set explicitly, so the text stays readable whatever the
system light/dark theme would otherwise give the button.
"""

IDLE = "idle"
RUNNING = "running"
BLOCKED = "blocked"

# Same named colours the ODMR button has always used; grey with a light
# text for the disabled ("blocked") state.
_BACKGROUND = {IDLE: "green", RUNNING: "red", BLOCKED: "#707070"}
_TEXT = {IDLE: "white", RUNNING: "white", BLOCKED: "#f0f0f0"}


def run_button_stylesheet(state):
    """Stylesheet for a run button in *state* (IDLE, RUNNING or BLOCKED).

    The :disabled rule applies whenever the button is disabled, so a
    disabled button is greyed out even if its state says otherwise.
    """
    return (
        f"QPushButton {{ background-color: {_BACKGROUND[state]}; "
        f"color: {_TEXT[state]}; font-weight: bold; }}"
        f" QPushButton:disabled {{ background-color: {_BACKGROUND[BLOCKED]}; "
        f"color: {_TEXT[BLOCKED]}; }}"
    )


def style_run_button(button, state, text, tooltip=""):
    """Set a run button's text, colours, enabled state and tooltip.

    BLOCKED disables the button; IDLE and RUNNING enable it.
    """
    button.setText(text)
    button.setStyleSheet(run_button_stylesheet(state))
    button.setEnabled(state != BLOCKED)
    button.setToolTip(tooltip)
