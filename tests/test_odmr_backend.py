"""
Integration test for the ODMR backend.

This test verifies that the complete ODMR backend functions correctly after
the Pulse Streamer persistent-output refactor.

Validated components:

- ConfigManager
- HardwareManager
- Pulse Streamer
- Persistent output cache
- ODMRExperiment
- Microwave source
- Camera
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from experiments.odmr_experiment import ODMRExperiment


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

        hw = hw_manager.get_hardware()

        ps = hw["pulse_streamer"]

        # =================================================
        # CONFIGURE PERSISTENT OUTPUTS
        # =================================================

        #
        # These outputs should remain persistent and must
        # not interfere with experiment pulse sequences.
        #

        ps.set_digital_output(3, True)      # Magnet polarity relay
        ps.set_digital_output(1, False)     # Laser default state

        print("\nPersistent outputs before experiment:")
        print(ps.persistent_outputs)

        # =================================================
        # ODMR CONFIGURATION
        # =================================================

        config = {

            "roi": None,

            "exposure_s": 0.02,

            "trigger_delay_s": 0.1,

            "camera_gate_s": 0.5,

            "mw_power_dbm": -10,
        }

        exp = ODMRExperiment(hw, config)

        # =================================================
        # BUILD SEQUENCE
        # =================================================

        sequence = exp.build_sequence()

        print("\n========== ODMR Sequence ==========\n")

        sequence.print_sequence()

        ps.load_sequence(sequence)

        # =================================================
        # ACQUIRE ONE ODMR POINT
        # =================================================

        frequency = 2.87e9

        print(f"\nAcquiring ODMR point at {frequency/1e9:.6f} GHz\n")

        signal = exp.acquire_point(frequency)

        print(f"\nNormalized ODMR signal : {signal}")

        # =================================================
        # VERIFY PERSISTENT OUTPUTS
        # =================================================

        print("\nPersistent outputs after experiment:")

        print(ps.persistent_outputs)

        assert ps.get_digital_output(3) is True

        assert ps.get_digital_output(1) is False

        print("\nODMR backend test PASSED.")

    finally:

        print("\nShutting down hardware...")

        hw_manager.shutdown()


if __name__ == "__main__":

    main()