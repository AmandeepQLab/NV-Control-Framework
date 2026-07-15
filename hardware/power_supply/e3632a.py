"""
=====================================================
Keysight / HP E3632A Power Supply
-----------------------------------------------------

Author:
    Amandeep + ChatGPT
=====================================================
"""

from .base_scpi_power_supply import BaseSCPIPowerSupply


class E3632A(BaseSCPIPowerSupply):

    # =================================================
    # OUTPUT
    # =================================================

    def output_on(self):

        self.write("OUTP ON")

        self.output_enabled = True

    def output_off(self):

        self.write("OUTP OFF")

        self.output_enabled = False

    # =================================================
    # CURRENT
    # =================================================

    def set_current(self, current):

        self.write(f"APPL MAX,{current}")

        self.current = current

    def get_current(self):

        return self.current

    # =================================================
    # VOLTAGE
    # =================================================

    def set_voltage(self, voltage):

        self.write(f"APPL {voltage},MAX")

        self.voltage = voltage

    def get_voltage(self):

        return self.voltage

    # =================================================
    # RANGE
    # =================================================

    def set_range(self, range_name):

        self.write(f"VOLT:RANG {range_name}")

    def get_range(self):

        return self.query("VOLT:RANG?").strip()