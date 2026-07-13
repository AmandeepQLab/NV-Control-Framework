"""
Test Magnet Control GUI
"""

import sys

from PyQt6.QtWidgets import QApplication

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from gui.magnet_window import MagnetControlWindow


def main():

    app = QApplication(sys.argv)

    # --------------------------------------------
    # Load configuration
    # --------------------------------------------

    cfg = ConfigManager("config/setupInfo.json")
    cfg.load()

    # --------------------------------------------
    # Initialize hardware
    # --------------------------------------------

    hw_manager = HardwareManager(cfg)

    hw_manager.initialize()

    hardware = hw_manager.get_hardware()

    # --------------------------------------------
    # Launch window
    # --------------------------------------------

    window = MagnetControlWindow(hardware)

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":

    main()