"""
=====================================================
Simulated Power Supply
-----------------------------------------------------
Software-only implementation of BasePowerSupply.

Used for development and testing without laboratory
hardware.

Author:
    Amandeep + ChatGPT
=====================================================
"""

from hardware.power_supply.base_power_supply import BasePowerSupply


class SimPowerSupply(BasePowerSupply):

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, config):

        super().__init__(config)

        self.output_enabled = False

        self.voltage = 0.0

        self.current = 0.0

    # =================================================
    # CONNECTION
    # =================================================

    def connect(self):

        self.connected = True

        print(f"{self.name}: connected")

    def disconnect(self):

        self.connected = False

        print(f"{self.name}: disconnected")

    # =================================================
    # OUTPUT
    # =================================================

    def output_on(self):

        self.output_enabled = True

        print(f"{self.name}: output ON")

    def output_off(self):

        self.output_enabled = False

        print(f"{self.name}: output OFF")

    # =================================================
    # PROGRAMMING
    # =================================================

    def set_voltage(
        self,
        voltage,
    ):

        self.voltage = voltage

        print(

            f"{self.name}: voltage = {voltage:.3f} V"

        )

    def set_current(
        self,
        current,
    ):

        self.current = current

        print(

            f"{self.name}: current = {current:.3f} A"

        )

    # =================================================
    # MEASUREMENTS
    # =================================================

    def measure_voltage(self):

        return self.voltage

    def measure_current(self):

        return self.current

    # =================================================
    # IDENTIFICATION
    # =================================================

    def identify(self):

        return "Simulated Power Supply"