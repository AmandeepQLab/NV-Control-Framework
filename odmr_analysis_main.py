"""Standalone entry point for offline ODMR analysis."""

import sys

from PyQt6.QtWidgets import QApplication

from gui.odmr_analysis_window import ODMRAnalysisWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = ODMRAnalysisWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

