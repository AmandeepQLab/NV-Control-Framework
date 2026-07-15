"""
=====================================================
Keysight / HP E3631A Power Supply
-----------------------------------------------------
Triple-output programmable DC power supply.

Author:
    Amandeep + ChatGPT
=====================================================
"""

from .base_scpi_power_supply import BaseSCPIPowerSupply


class E3631A(BaseSCPIPowerSupply):

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, config):

        super().__init__(config)

        self.channel = config["channel"]

    # =================================================
    # OUTPUT
    # =================================================

    def output_on(self):

        self.write(f"INST {self.channel}")

        self.write("OUTP ON")

        self.output_enabled = True

    def output_off(self):

        self.write(f"INST {self.channel}")

        self.write("OUTP OFF")

        self.output_enabled = False

    # =================================================
    # CURRENT
    # =================================================

    def set_current(self, current):

        self.write(
            f"APPL {self.channel}, MAX, {current:.6f}"
        )

        self.current = current

    def get_current(self):

        return self.current

    # =================================================
    # VOLTAGE
    # =================================================

    def set_voltage(self, voltage):

        self.write(
            f"APPL {self.channel}, {voltage:.6f}, MAX"
        )

        self.voltage = voltage

    def get_voltage(self):

        return self.voltage