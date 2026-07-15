"""
Test complete Magnet backend
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager


def main():

    cfg = ConfigManager("config/setupInfo.json")
    cfg.load()

    hw = HardwareManager(cfg)
    hw.initialize()

    magnet = hw.get_hardware()["magnet"]

    print("\nEnabling magnet...")
    magnet.enable()

    print("\nSetting fields...")
    magnet.set_vector(
        bx=1.0,
        by=1.0,
        bz=1.0,
    )

    input("\nVerify currents. Press Enter to continue...")

    print("\nZeroing fields...")
    magnet.zero()

    input("\nVerify zero current. Press Enter to continue...")

    print("\nDisabling magnet...")
    magnet.disable()

    print("\nDone.")


if __name__ == "__main__":
    main()