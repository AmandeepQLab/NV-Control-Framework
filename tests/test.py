
"""
=====================================================
Test Magnet Field Sweep
-----------------------------------------------------
Manual verification of global polarity switching.

Author:
    Amandeep + ChatGPT
=====================================================
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager


def main():

    # =================================================
    # Initialize Hardware
    # =================================================

    cfg = ConfigManager("config/setupInfo.json")

    hw_manager = HardwareManager(cfg)

    hw_manager.initialize()

    hardware = hw_manager.get_hardware()

    magnet = hardware["magnet"]

    # =================================================
    # Test
    # =================================================

    magnet.enable()

    try:

        for B in [-5, -2, 0, 2, 5]:

            print(f"\nSetting Bx = {B:.1f} mT")

            magnet.set_vector(
                bx=B,
                by=0.0,
                bz=0.0,
            )

            input("Press Enter for next field...")

    finally:

        print("\nDisabling magnet...")

        magnet.disable()

        hw_manager.shutdown()


if __name__ == "__main__":
    main()