"""
=====================================================
Magnet Control Window
-----------------------------------------------------
Manual control of Helmholtz coils.

This window communicates with the Magnet backend and
allows manual setting of magnetic fields in Gauss (G).

Author:
    Amandeep + ChatGPT
=====================================================
"""
from hardware.magnet.magnet import POSITIVE, NEGATIVE
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
        QCheckBox,
        QMessageBox,
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
            QCheckBox,
            QMessageBox,
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
            QCheckBox,
            QMessageBox,
        )

# GUI values are Gauss; Magnet/MagnetAxis and calibration remain in mT.
_MT_TO_GAUSS = 10.0
_GAUSS_TO_MT = 0.1

# Post-experiment magnet actions (see on_experiment_finished()).
POST_EXPERIMENT_FIELD_ZERO = "Field zero at diamond"
POST_EXPERIMENT_ELECTRICAL_OFF = "Electrical off"

_SWEPT_OVERRIDE_WARNING = (
    "This experiment is actively sweeping the magnet field. "
    "Manual commands will conflict with it and corrupt your data."
)
_STATIC_OVERRIDE_WARNING = "Changing the field will affect your measurement."


class MagnetControlWindow(QMainWindow):

    _VECTOR_BOX_TITLE = "Commanded / Model Magnet Vector"

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(self, hardware):

        super().__init__()

        self.hardware = hardware
        self.magnet = hardware["magnet"]

        self.setWindowTitle("Magnet Control")
        self.resize(650, 500)

        # Experiment lock state, driven by MainWindow's experiment_started/
        # experiment_finished signals. None means no experiment is running.
        # Set before build_ui(): update_power_button() reads it.
        self._experiment_name = None
        self._magnet_is_swept = False
        self._override_active = False
        # Commanded (x, y, z) mT from a running Zero Field scan, shown in the
        # vector display instead of magnet.get_vector() while it runs.
        self._live_vector_mT = None

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

        power_layout.addWidget(self.power_button)

        main_layout.addLayout(power_layout)

        # =================================================
        # EXPERIMENT LOCK
        # =================================================
        # Outside the tabs so the lock state is visible from either tab.

        lock_box = QGroupBox("Experiment Lock")

        lock_layout = QVBoxLayout(lock_box)

        self.lock_label = QLabel("")
        self.lock_label.setWordWrap(True)
        self.lock_label.setVisible(False)
        lock_layout.addWidget(self.lock_label)

        self.override_check = QCheckBox("Allow manual control during experiment")
        self.override_check.setChecked(False)
        # Only meaningful while an experiment is running.
        self.override_check.setVisible(False)
        lock_layout.addWidget(self.override_check)

        post_layout = QHBoxLayout()
        post_layout.addWidget(QLabel("After experiment:"))
        self.post_experiment_combo = QComboBox()
        self.post_experiment_combo.addItems(
            [POST_EXPERIMENT_FIELD_ZERO, POST_EXPERIMENT_ELECTRICAL_OFF]
        )
        self.post_experiment_combo.setCurrentText(POST_EXPERIMENT_FIELD_ZERO)
        self.post_experiment_combo.setToolTip(
            "Applies to static-field experiments (e.g. ODMR). After a Zero Field "
            "scan the magnet is always turned off by the experiment itself."
        )
        post_layout.addWidget(self.post_experiment_combo)
        post_layout.addStretch()
        lock_layout.addLayout(post_layout)

        main_layout.addWidget(lock_box)

        polarity_layout = QHBoxLayout()
        self.polarity_labels = {}
        for name in ("X", "Y", "Z"):
            label = QLabel(f"{name} coil polarity: Unknown")
            self.polarity_labels[name.lower()] = label
            polarity_layout.addWidget(label)
        main_layout.addLayout(polarity_layout)

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

        field_box = QGroupBox("Target Magnetic Field")

        layout.addWidget(field_box)

        field_layout = QFormLayout(field_box)

        self.field_spin = QDoubleSpinBox()

        self.field_spin.setRange(-1000.0, 1000.0)
        self.field_spin.setDecimals(3)
        self.field_spin.setSingleStep(1.0)
        self.field_spin.setSuffix(" G")

        field_layout.addRow(
            "Field (G)",
            self.field_spin
        )
        # =================================================
        # BUTTONS
        # =================================================

        button_layout = QHBoxLayout()

        self.apply_button = QPushButton("Apply")
        self.read_button = QPushButton("Refresh")
        self.zero_button = QPushButton("Magnetic Zero (all axes)")
        self.zero_button.setToolTip(
            "Command calibrated 0 G on every axis. Compensation currents may be nonzero."
        )


        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.read_button)
        button_layout.addWidget(self.zero_button)
        layout.addLayout(button_layout)

        # =================================================
        # CURRENT STATUS
        # =================================================

        current_box = QGroupBox("Commanded / Model Status")

        layout.addWidget(current_box)

        current_layout = QFormLayout(current_box)

        self.current_field_label = QLabel("0.000 G")

        self.current_current_label = QLabel("0.0000 A")

        self.direction_label = QLabel("Positive")

        self.connection_label = QLabel("Connected")

        current_layout.addRow(
            "Model Field (G)",
            self.current_field_label
        )

        current_layout.addRow(
            "Commanded Current",
            self.current_current_label
        )

        current_layout.addRow(
            "Coil Polarity",
            self.direction_label
        )

        current_layout.addRow(
            "Status",
            self.connection_label
        )

        # =====================================================
        # CURRENT MAGNET VECTOR
        # =====================================================

        self.vector_box = QGroupBox(self._VECTOR_BOX_TITLE)

        layout.addWidget(self.vector_box)

        vector_layout = QFormLayout(self.vector_box)

        self.bx_label = QLabel("0.000 G")
        self.by_label = QLabel("0.000 G")
        self.bz_label = QLabel("0.000 G")

        vector_layout.addRow(
            "Bx (G)",
            self.bx_label
        )

        vector_layout.addRow(
            "By (G)",
            self.by_label
        )

        vector_layout.addRow(
            "Bz (G)",
            self.bz_label
        )

        
        # =================================================
        # STATUS
        # =================================================

        status_box = QGroupBox("Status")

        layout.addWidget(status_box)

        status_layout = QVBoxLayout(status_box)

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)

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

        info_layout.addRow("Ratio (G/A)", self.cfg_ratio_label)

        info_layout.addRow("Offset (G)", self.cfg_offset_label)

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

        self.override_check.toggled.connect(
            self._on_override_toggled
        )

    # =====================================================
    # MAGNET POWER
    # =====================================================

    def toggle_power(self):

        if not self._commands_allowed():
            return

        try:

            if self.magnet.is_enabled():

                self.magnet.disable()

                self.status_label.setText(
                    "Magnet power is OFF; coils de-energized. Model field equals the calibration offset."
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

    # =====================================================
    # UPDATE POWER BUTTON
    # =====================================================

    def update_power_button(self):

        if self.magnet.is_enabled():

            self.power_button.setText("Magnet ON: Turn OFF (de-energize)")

            self.power_button.setStyleSheet(
                "background-color: lightgreen;"
            )

        else:

            self.power_button.setText("Magnet OFF: Turn ON")

            self.power_button.setStyleSheet(
                "background-color: lightgray;"
            )

        self._apply_command_lock()

    # =====================================================
    # EXPERIMENT LOCK
    # =====================================================
    # GUI-layer only: prevents this window from issuing magnet commands
    # that would interleave with a running experiment's own. No lock exists
    # in hardware/magnet/ by design (see CLAUDE.md).

    def _commands_allowed(self):
        return self._experiment_name is None or self._override_active

    def _apply_command_lock(self):
        """Enable command controls per lock state; readouts stay enabled."""
        allowed = self._commands_allowed()
        self.power_button.setEnabled(allowed)
        self.apply_button.setEnabled(allowed)
        # Magnetic Zero additionally requires powered coils, as before.
        self.zero_button.setEnabled(allowed and self.magnet.is_enabled())

    def _set_override_checked(self, checked):
        """Change the checkbox without re-entering _on_override_toggled."""
        self.override_check.blockSignals(True)
        try:
            self.override_check.setChecked(checked)
        finally:
            self.override_check.blockSignals(False)

    def on_experiment_started(self, experiment_name, magnet_is_swept):
        """Lock command controls while an experiment runs.

        Also called when the running set changes (MainWindow re-announces
        the combined state), so it always re-locks and clears any override.
        """
        self._experiment_name = experiment_name
        self._magnet_is_swept = bool(magnet_is_swept)
        self._override_active = False
        self._set_override_checked(False)
        self.override_check.setVisible(True)
        self.lock_label.setText(f"Magnet locked — {experiment_name} running")
        self.lock_label.setVisible(True)
        self._apply_command_lock()

    def on_experiment_finished(self):
        """Unlock unconditionally, then apply the post-experiment action."""
        if self._experiment_name is None:
            return
        self._override_active = False
        self._set_override_checked(False)
        self.override_check.setVisible(False)
        self._experiment_name = None
        self._magnet_is_swept = False
        self.lock_label.setText("")
        self.lock_label.setVisible(False)
        self._live_vector_mT = None
        self.vector_box.setTitle(self._VECTOR_BOX_TITLE)
        self._apply_command_lock()
        self._run_post_experiment_action()
        self.update_display()

    def _on_override_toggled(self, checked):
        if self._experiment_name is None:
            # Not shown when idle; ignore any programmatic toggle.
            self._set_override_checked(False)
            return
        if checked:
            message = (
                _SWEPT_OVERRIDE_WARNING
                if self._magnet_is_swept
                else _STATIC_OVERRIDE_WARNING
            )
            reply = QMessageBox.warning(
                self,
                "Manual Magnet Control",
                message,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                self._set_override_checked(False)
                return
            self._override_active = True
            self.lock_label.setText(
                f"Manual override active — {self._experiment_name} running"
            )
        else:
            self._override_active = False
            self.lock_label.setText(f"Magnet locked — {self._experiment_name} running")
        self._apply_command_lock()

    def _run_post_experiment_action(self):
        """Apply the selected post-experiment state, if the magnet is on.

        Skipped silently when the magnet is not enabled -- including after a
        Zero Field scan, whose own cleanup already disabled it.
        """
        if not self.magnet.is_enabled():
            return
        action = self.post_experiment_combo.currentText()
        try:
            if action == POST_EXPERIMENT_ELECTRICAL_OFF:
                self.magnet.disable()
                self.status_label.setText(
                    "After experiment: magnet de-energized, outputs off."
                )
            else:
                self.magnet.set_vector(bx=0.0, by=0.0, bz=0.0)
                self.status_label.setText(
                    "After experiment: calibrated 0 G commanded on all axes; "
                    "compensation currents may be nonzero."
                )
        except Exception as err:
            self.status_label.setText(f"After-experiment action failed: {err}")

    def update_field_display(self, x_mT, y_mT, z_mT):
        """Show a running Zero Field scan's commanded vector, read-only.

        Ignored when no experiment is running, so the display keeps its
        normal behaviour outside a scan.
        """
        if self._experiment_name is None:
            return
        self._live_vector_mT = {"x": x_mT, "y": y_mT, "z": z_mT}
        self.vector_box.setTitle(
            f"Commanded Magnet Vector (live: {self._experiment_name})"
        )
        self.bx_label.setText(self.field_text(x_mT))
        self.by_label.setText(self.field_text(y_mT))
        self.bz_label.setText(self.field_text(z_mT))

    @staticmethod
    def polarity_text(polarity):
        if polarity == POSITIVE:
            return "+ (Positive)"
        if polarity == NEGATIVE:
            return "- (Negative)"
        return "Unknown"

    @staticmethod
    def field_text(field_mT):
        if field_mT is None:
            return "Unknown G"
        return f"{field_mT * _MT_TO_GAUSS:.3f} G"

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

        self.cfg_axis_label.setText(axis.name)

        self.current_field_label.setText(self.field_text(axis.get_field()))
        current = axis.get_current()
        self.current_current_label.setText(
            "Unknown A" if current is None else f"{current:.4f} A"
        )
        
        self.cfg_ratio_label.setText(
            f"{axis.get_ratio() * _MT_TO_GAUSS:.3f} G/A"
        )

        self.cfg_offset_label.setText(
            f"{axis.get_offset() * _MT_TO_GAUSS:.3f} G"
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

        vector = (
            self._live_vector_mT
            if self._live_vector_mT is not None
            else self.magnet.get_vector()
        )

        self.bx_label.setText(self.field_text(vector['x']))

        self.by_label.setText(self.field_text(vector['y']))

        self.bz_label.setText(self.field_text(vector['z']))

        self.direction_label.setText(self.polarity_text(axis.get_polarity()))
        for name, polarity in self.magnet.get_polarities().items():
            self.polarity_labels[name].setText(
                f"{name.upper()} coil polarity: {self.polarity_text(polarity)}"
            )

        self.update_power_button()


        self.config_file_label.setText(
            "setupInfo.json"
        )

        self.flip_available_label.setText(

            "Yes"

            if axis.flip_available

            else

            "No"

        )

        self.flip_channel_label.setText(

            str(
                axis.flip_channel
            )

        )
    # ===================================================
    # APPLY FIELD
    # =====================================================

    def apply_field(self):

        if not self._commands_allowed():
            return

        if not self.magnet.is_enabled():

            self.status_label.setText(
                "Magnet power is OFF."
            )

            return

        axis = self.get_selected_axis()

        field_gauss = self.field_spin.value()

        try:

            axis.set_field(field_gauss * _GAUSS_TO_MT, keep_output_enabled=True)

            self.status_label.setText(
                f"{axis.name}-axis commanded to {field_gauss:.3f} G"
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

        if not self._commands_allowed():
            return

        if not self.magnet.is_enabled():
            self.status_label.setText("Turn magnet power ON to command calibrated magnetic zero.")
            return

        try:

            self.magnet.set_vector(bx=0.0, by=0.0, bz=0.0)

            self.field_spin.setValue(0.0)

            self.status_label.setText(
                "Calibrated 0 G commanded on all axes; compensation currents may be nonzero."
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
        Refresh commanded/model values; no magnetic-field measurement is made.
        """

        self.status_label.setText(
            "Commanded/model values refreshed; magnetic field is not measured."
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
