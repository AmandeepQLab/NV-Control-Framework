"""
Test Magnet Enable/Disable
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager


def main():

    cfg = ConfigManager("config/setupInfo.json")
    cfg.load()

    hw_manager = HardwareManager(cfg)
    hw_manager.initialize()

    magnet = hw_manager.get_hardware()["magnet"]

    print(f"Initially : {magnet.is_enabled()}")

    magnet.enable()

    print(f"After enable : {magnet.is_enabled()}")

    magnet.disable()

    print(f"After disable : {magnet.is_enabled()}")


if __name__ == "__main__":
    main()