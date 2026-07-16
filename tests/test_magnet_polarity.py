"""
Test Magnet Polarity
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager


def main():

    # =====================================================
    # LOAD CONFIGURATION
    # =====================================================

    cfg = ConfigManager("config/setupInfo.json")
    cfg.load()

    # =====================================================
    # INITIALIZE HARDWARE
    # =====================================================

    hw_manager = HardwareManager(cfg)

    try:

        hw_manager.initialize()

        hardware = hw_manager.get_hardware()

        magnet = hardware["magnet"]

        # =================================================
        # ENABLE MAGNET
        # =================================================

        print("\nEnabling magnet...")
        magnet.enable()

        # =================================================
        # POSITIVE FIELD
        # =================================================

        print("\nPositive field")
        magnet.set_vector(
            bx=0.0,
            by=2.0,
            bz=0.0,
        )

        input("Verify field. Press Enter to continue...")

        # =================================================
        # NEGATIVE FIELD
        # =================================================

        print("\nNegative field")
        magnet.set_vector(
            bx=0.0,
            by=-2.0,
            bz=0.0,
        )

        input("Verify relay switched. Press Enter to continue...")

        # =================================================
        # ZERO FIELD
        # =================================================

        print("\nZero field")
        magnet.zero()

        input("Verify current is zero. Press Enter to continue...")

        # =================================================
        # DISABLE MAGNET
        # =================================================

        print("\nDisabling magnet...")
        magnet.disable()

    finally:

        hw_manager.shutdown()


if __name__ == "__main__":
    main()
