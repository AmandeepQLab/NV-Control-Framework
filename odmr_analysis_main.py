"""Standalone entry point for offline ODMR analysis."""

import sys

from PyQt6.QtWidgets import QApplication

from gui import style
from PyQt6.QtCore import qInstallMessageHandler

from framework.logging_setup import log_qt_message, setup_logging
from gui.odmr_analysis_window import ODMRAnalysisWindow
from gui.widgets.collapsible_section import enable_state_persistence


def main():
    setup_logging(verbose="--verbose" in sys.argv)
    qInstallMessageHandler(log_qt_message)  # Qt's own messages into the log
    app = QApplication.instance() or QApplication(sys.argv)
    style.apply_app_style(app)
    enable_state_persistence()
    window = ODMRAnalysisWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

