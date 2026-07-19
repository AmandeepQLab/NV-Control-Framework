"""
Integration test for the Zero Field backend.

This test validates

- ConfigManager
- HardwareManager
- Magnet
- Camera
- ScanExperiment
- ZeroFieldExperiment

No microwave or Pulse Streamer sequence is used.

The experiment acquires fluorescence images while sweeping
the magnetic field.
"""

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from experiments.zero_field_experiment import ZeroFieldExperiment


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

        camera = hw["camera"]
        magnet = hw["magnet"]

        print("\n========================================")
        print(" Zero Field Backend Test")
        print("========================================\n")

        # =================================================
        # CREATE EXPERIMENT
        # =================================================

        exp = ZeroFieldExperiment(

            hardware_manager=hw_manager,

            camera=camera,

            magnet=magnet,

            field_start=-2,

            field_stop=2,

            field_points=5,

            field_axis="X",

            settling_time_ms=500,

            averages=1,

            roi=None,

        )

        print("Experiment created successfully.\n")

        # =================================================
        # BUILD SCAN VECTOR
        # =================================================

        exp.setup_scan()

        print("Scan vector:")

        print(exp.scan_vector)

        print()

        print("Initial magnet vector:")

        print(magnet.get_vector())

        print()

        input(
            "Press ENTER to begin the magnetic field sweep "
            "or CTRL+C to abort..."
        )

        # =================================================
        # RUN EXPERIMENT
        # =================================================

        cube = exp.run()

        print("\nExperiment completed.\n")

        # =================================================
        # VERIFY RESULT
        # =================================================

        print("ImageCube information")

        print("---------------------")

        print("Scan Axis Name :", cube.scan_axis_name)

        print("Scan Axis Unit :", cube.scan_axis_unit)

        print("Scan Axis Values :")

        print(cube.scan_axis_values)

        print()

        print("Image Stack Shape:")

        print(cube.data.shape)

        print()

        print("Final Magnet Vector:")

        print(magnet.get_vector())

        print("\nZero Field backend test PASSED.")

    finally:

        print("\nShutting down hardware...")

        hw_manager.shutdown()


if __name__ == "__main__":

    main()
