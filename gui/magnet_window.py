"""
=====================================================
Magnet Control Window
-----------------------------------------------------
Manual control of Helmholtz coils.

This window communicates with the Magnet backend and
allows manual setting of magnetic fields.

Author:
    Amandeep + ChatGPT
=====================================================
"""
from hardware.magnet.magnet import (POSITIVE,NEGATIVE)
try:
    from PyQt6.QtWidgets import (  # type: ignore[import]
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QFormLayout,
        QLabel,
        QPushButton,
        QDoubleSpinBox,
        QRadioButton,
        QButtonGroup,
        QTabWidget,
        QGroupBox,
        QComboBox,
    )
except ImportError:
    try:
        from PySide6.QtWidgets import (  # type: ignore[import]
            QMainWindow,
            QWidget,
            QVBoxLayout,
            QHBoxLayout,
            QFormLayout,
            QLabel,
            QPushButton,
            QDoubleSpinBox,
            QRadioButton,
            QButtonGroup,
            QTabWidget,
            QGroupBox,
            QComboBox,
        )
    except ImportError:
        from PyQt5.QtWidgets import (  # type: ignore[import]
            QMainWindow,
            QWidget,
            QVBoxLayout,
            QHBoxLayout,
            QFormLayout,
            QLabel,
            QPushButton,
            QDoubleSpinBox,
            QRadioButton,
            QButtonGroup,
            QTabWidget,
            QGroupBox,
            QComboBox,
        )

class MagnetControlWindow(QMainWindow):

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(self, hardware):

        super().__init__()

        self.hardware = hardware
        self.magnet = hardware["magnet"]

        self.setWindowTitle("Magnet Control")
        self.resize(650, 500)

        self.build_ui()
        self.connect_signals()
        self.update_display()
        self.update_power_button()

    # =====================================================
    # BUILD GUI
    # =====================================================

    def build_ui(self):

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # =================================================
        # MAGNET POWER
        # =================================================

        power_layout = QHBoxLayout()

        self.power_button = QPushButton()

        self.power_button.setMinimumHeight(42)

        self.polarity_button = QPushButton()
        self.polarity_button.setCheckable(True)
        self.polarity_button.setMinimumHeight(42)

        power_layout.addWidget(self.power_button)
        power_layout.addWidget(self.polarity_button)

        main_layout.addLayout(power_layout)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # =================================================
        # MANUAL CONTROL TAB
        # =================================================

        manual_tab = QWidget()

        self.tabs.addTab(
            manual_tab,
            "Manual Control"
        )

        layout = QVBoxLayout(manual_tab)

        # =================================================
        # AXIS
        # =================================================

        axis_box = QGroupBox("Axis")

        layout.addWidget(axis_box)

        axis_layout = QHBoxLayout(axis_box)

        self.axis_combo = QComboBox()

        self.axis_combo.addItems(

            [

                "X",

                "Y",

                "Z",

            ]

        )

        self.axis_combo.setCurrentText("Z")

        axis_layout.addWidget(self.axis_combo)

        # =================================================
        # MAGNETIC FIELD
        # =================================================

        field_box = QGroupBox("Magnetic Field")

        layout.addWidget(field_box)

        field_layout = QFormLayout(field_box)

        self.field_spin = QDoubleSpinBox()

        self.field_spin.setRange(-100.0, 100.0)
        self.field_spin.setDecimals(3)
        self.field_spin.setSingleStep(0.1)
        self.field_spin.setSuffix(" mT")

        field_layout.addRow(
            "Field",
            self.field_spin
        )
        # =================================================
        # BUTTONS
        # =================================================

        button_layout = QHBoxLayout()

        self.apply_button = QPushButton("Apply")
        self.read_button = QPushButton("Read")
        self.zero_button = QPushButton("Zero")


        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.read_button)
        button_layout.addWidget(self.zero_button)
        layout.addLayout(button_layout)

        # =================================================
        # CURRENT STATUS
        # =================================================

        current_box = QGroupBox("Current Status")

        layout.addWidget(current_box)

        current_layout = QFormLayout(current_box)

        self.current_field_label = QLabel("0.000 mT")

        self.current_current_label = QLabel("0.0000 A")

        self.direction_label = QLabel("Positive")

        self.connection_label = QLabel("Connected")

        current_layout.addRow(
            "Field",
            self.current_field_label
        )

        current_layout.addRow(
            "Current",
            self.current_current_label
        )

        current_layout.addRow(
            "Direction",
            self.direction_label
        )

        current_layout.addRow(
            "Status",
            self.connection_label
        )

        # =====================================================
        # CURRENT MAGNET VECTOR
        # =====================================================

        vector_box = QGroupBox("Current Magnet Vector")

        layout.addWidget(vector_box)

        vector_layout = QFormLayout(vector_box)

        self.bx_label = QLabel("0.000 mT")
        self.by_label = QLabel("0.000 mT")
        self.bz_label = QLabel("0.000 mT")

        vector_layout.addRow(
            "Bx",
            self.bx_label
        )

        vector_layout.addRow(
            "By",
            self.by_label
        )

        vector_layout.addRow(
            "Bz",
            self.bz_label
        )

        
        # =================================================
        # STATUS
        # =================================================

        status_box = QGroupBox("Status")

        layout.addWidget(status_box)

        status_layout = QVBoxLayout(status_box)

        self.status_label = QLabel("Ready")

        status_layout.addWidget(self.status_label)

        layout.addStretch()

        # =====================================================
        # CONFIGURATION TAB
        # =====================================================

        configuration_tab = QWidget()

        self.tabs.addTab(
            configuration_tab,
            "Configuration"
        )

        cfg_layout = QVBoxLayout(configuration_tab)

        # -----------------------------------------------------
        # Configuration Information
        # -----------------------------------------------------

        info_box = QGroupBox("Configuration Information")

        cfg_layout.addWidget(info_box)

        info_layout = QFormLayout(info_box)

        self.cfg_axis_label = QLabel("Z")

        self.cfg_ratio_label = QLabel("--")

        self.cfg_offset_label = QLabel("--")

        self.cfg_max_current_label = QLabel("--")

        self.cfg_supply_label = QLabel("--")

        self.cfg_address_label = QLabel("--")

        self.cfg_range_label = QLabel("--")

        info_layout.addRow("Axis", self.cfg_axis_label)

        info_layout.addRow("Ratio", self.cfg_ratio_label)

        info_layout.addRow("Offset", self.cfg_offset_label)

        info_layout.addRow("Max Current", self.cfg_max_current_label)

        info_layout.addRow("Power Supply", self.cfg_supply_label)

        info_layout.addRow("Address", self.cfg_address_label)

        info_layout.addRow("Range", self.cfg_range_label)

        # -----------------------------------------------------
        # Buttons
        # -----------------------------------------------------

        config_box = QGroupBox("Configuration")

        config_layout = QFormLayout(config_box)

        cfg_layout.addWidget(config_box)

        self.config_file_label = QLabel("setupInfo.json")

        config_layout.addRow(
            "Configuration File",
            self.config_file_label
        )

        self.flip_available_label = QLabel("--")

        config_layout.addRow(
            "Flip Available",
            self.flip_available_label
        )

        self.flip_channel_label = QLabel("--")

        config_layout.addRow(
            "Flip Channel",
            self.flip_channel_label
        )
    
    # =====================================================
    # CONNECT SIGNALS
    # =====================================================

    def connect_signals(self):

        self.apply_button.clicked.connect(
            self.apply_field
        )

        self.zero_button.clicked.connect(
            self.zero_field
        )

        self.axis_combo.currentTextChanged.connect(
            self.update_display
        )

        self.read_button.clicked.connect(
            self.read_field
        )

        self.power_button.clicked.connect(
            self.toggle_power
        )

        self.polarity_button.toggled.connect(
            self.toggle_polarity
        )

    # =====================================================
    # MAGNET POWER
    # =====================================================

    def toggle_power(self):

        try:

            if self.magnet.is_enabled():

                self.magnet.disable()

                self.status_label.setText(
                    "Magnet power is OFF."
                )

            else:

                self.magnet.enable()

                self.status_label.setText(
                    "Magnet power is ON."
                )

        except Exception as err:

            self.status_label.setText(
                str(err)
            )

        self.update_power_button()
        self.update_display()

    def toggle_polarity(self, negative):

        try:

            self.magnet.set_polarity(
                NEGATIVE if negative else POSITIVE
            )

        except Exception as err:

            self.status_label.setText(
                str(err)
            )

        self.update_display()

    # =====================================================
    # UPDATE POWER BUTTON
    # =====================================================

    def update_power_button(self):

        if self.magnet.is_enabled():

            self.power_button.setText("Magnet ON")

            self.power_button.setStyleSheet(
                "background-color: lightgreen;"
            )

        else:

            self.power_button.setText("Magnet OFF")

            self.power_button.setStyleSheet(
                "background-color: lightgray;"
            )

        self.update_polarity_button()

    def update_polarity_button(self):

        polarity = self.magnet.get_polarity()

        self.polarity_button.blockSignals(True)
        self.polarity_button.setChecked(polarity == NEGATIVE)
        self.polarity_button.blockSignals(False)

        self.polarity_button.setText(
            "Polarity: NEGATIVE"
            if polarity == NEGATIVE
            else "Polarity: POSITIVE"
        )

        self.polarity_button.setEnabled(
            self.magnet.is_enabled()
        )

    # =====================================================
    # GET SELECTED AXIS
    # =====================================================

    def get_selected_axis(self):

        axis = self.axis_combo.currentText()

        if axis == "X":
            return self.magnet.x

        if axis == "Y":
            return self.magnet.y

        return self.magnet.z

    # =====================================================
    # UPDATE DISPLAY
    # =====================================================

    def update_display(self):

        axis = self.get_selected_axis()

        self.current_field_label.setText(f"{axis.get_field():.3f} mT")
        self.current_current_label.setText(f"{axis.get_current():.4f} A")
        
        self.cfg_ratio_label.setText(
            f"{axis.get_ratio():.3f} mT/A"
        )

        self.cfg_offset_label.setText(
            f"{axis.get_offset():.3f} mT"
        )

        self.cfg_max_current_label.setText(
            f"{axis.get_max_current():.2f} A"
        )

        self.cfg_supply_label.setText(
            axis.get_supply_type()
        )

        self.cfg_address_label.setText(
            axis.get_supply_address()
        )

        self.cfg_range_label.setText(
            axis.get_supply_range()
        )

        vector = self.magnet.get_vector()

        self.bx_label.setText(f"{vector['x']:.3f} mT")

        self.by_label.setText(f"{vector['y']:.3f} mT")

        self.bz_label.setText(f"{vector['z']:.3f} mT")

        direction = self.magnet.get_polarity()

        if direction == POSITIVE:
            self.direction_label.setText("Positive")
        else:
            self.direction_label.setText("Negative")

        self.update_polarity_button()


        self.config_file_label.setText(
            "setupInfo.json"
        )

        self.flip_available_label.setText(

            "Yes"

            if self.magnet.flip_available

            else

            "No"

        )

        self.flip_channel_label.setText(

            str(
                self.magnet.flip_channel
            )

        )
    # ===================================================
    # APPLY FIELD
    # =====================================================

    def apply_field(self):

        if not self.magnet.is_enabled():

            self.status_label.setText(
                "Magnet power is OFF."
            )

            return

        axis = self.get_selected_axis()

        field = self.field_spin.value()

        try:

            axis.set_field(field)

            self.status_label.setText(
                f"{axis.name}-axis set to {field:.3f} mT"
            )

        except Exception as err:

            self.status_label.setText(
                str(err)
            )

        self.update_display()

    # =====================================================
    # ZERO FIELD
    # =====================================================

    def zero_field(self):

        try:

            self.magnet.zero()

            self.field_spin.setValue(0.0)

            self.status_label.setText(
                "Magnet field zeroed"
            )

        except Exception as err:

            self.status_label.setText(
                str(err)
            )

        self.update_display()
    # =====================================================
    # READ FIELD
    # =====================================================

    def read_field(self):

        """
        Refresh values from the selected axis.

        In the future this will query the actual
        power supply (and optional Hall probe).
        """

        self.status_label.setText(
            "Field read."
        )

        self.update_display()

    # =====================================================
    # REFRESH
    # =====================================================

    def refresh(self):

        """
        Refresh all displayed values.
        """

        self.update_display()

    # =====================================================
    # SHOW EVENT
    # =====================================================

    def showEvent(self, event):

        super().showEvent(event)

        self.update_display()
        self.update_power_button()
