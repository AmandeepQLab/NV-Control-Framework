"""
Test Pulse Streamer Persistent Outputs
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

        ps = hardware["pulse_streamer"]

        # =================================================
        # INITIAL STATE
        # =================================================

        print("\nInitial persistent outputs:")
        print(ps.persistent_outputs)

        # =================================================
        # SET TTL3 HIGH
        # =================================================

        print("\nSetting TTL3 HIGH...")
        ps.set_digital_output(3, True)

        print("Stored state:", ps.get_digital_output(3))
        print("Persistent outputs:", ps.persistent_outputs)

        assert ps.get_digital_output(3) is True

        input("\nRelay should NOT click. Press Enter...")

        # =================================================
        # SET TTL3 LOW
        # =================================================

        print("\nSetting TTL3 LOW...")
        ps.set_digital_output(3, False)

        print("Stored state:", ps.get_digital_output(3))
        print("Persistent outputs:", ps.persistent_outputs)

        assert ps.get_digital_output(3) is False

        input("\nRelay should still NOT click. Press Enter...")

        print("\nPersistent output cache test PASSED.")

    finally:

        print("\nShutting down hardware...")
        hw_manager.shutdown()


if __name__ == "__main__":
    main()