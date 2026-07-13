"""
=====================================================
Zero-Field Imaging Window
-----------------------------------------------------
GUI for zero-field widefield NV imaging.

This experiment exploits the zero-field fluorescence
feature of high-density NV ensembles without applying
microwave excitation.

Author:
    Amandeep + ChatGPT
=====================================================
"""

import importlib

# Dynamically import a Qt binding that is available in the environment.
# Try common bindings in order and import the QtWidgets symbols we need.
_qt_candidates = [
    "PyQt5.QtWidgets",
    "PySide6.QtWidgets",
    "PySide2.QtWidgets",
]

for _mod in _qt_candidates:
    try:
        _qtwidgets = importlib.import_module(_mod)
        QMainWindow = getattr(_qtwidgets, "QMainWindow")
        QLabel = getattr(_qtwidgets, "QLabel")
        QWidget = getattr(_qtwidgets, "QWidget")
        QVBoxLayout = getattr(_qtwidgets, "QVBoxLayout")
        break
    except (ModuleNotFoundError, ImportError, AttributeError):
        _qtwidgets = None

if _qtwidgets is None:
    raise ImportError(
        "No supported Qt bindings found. Install PyQt5, PySide6 or PySide2."
    )


class ZeroFieldWindow(QMainWindow):

    def __init__(
        self,
        hardware,
        camera,
        roi_getter,
        exposure_getter,
    ):

        super().__init__()

        self.hardware = hardware
        self.camera = camera

        self.roi_getter = roi_getter
        self.exposure_getter = exposure_getter

        self.setWindowTitle(
            "Zero-Field Imaging"
        )

        self.resize(1200, 700)

        central = QWidget()

        self.setCentralWidget(
            central
        )

        layout = QVBoxLayout()

        central.setLayout(layout)

        title = QLabel(

            "<h2>Zero-Field Imaging</h2>"

        )

        layout.addWidget(title)

        layout.addWidget(

            QLabel(

                "GUI framework created successfully.\n\n"
                "Experiment implementation coming next."

            )

        )