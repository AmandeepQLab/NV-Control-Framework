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

from hardware.power_supply.base_power_supply import (
    BasePowerSupply,
)


class SimPowerSupply(BasePowerSupply):

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, config):

        super().__init__(config)

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

    def set_voltage(self, voltage):

        self.voltage = voltage

        print(
            f"{self.name}: voltage = "
            f"{voltage:.3f} V"
        )

    def set_current(self, current):

        self.current = current

        print(
            f"{self.name}: current = "
            f"{current:.3f} A"
        )

    # =================================================
    # MEASUREMENTS
    # =================================================

    def get_voltage(self):

        return self.voltage

    def get_current(self):

        return self.current

    # =================================================
    # IDENTIFICATION
    # =================================================

    def identify(self):

        return "Simulated Power Supply"

    # =================================================
    # ERROR QUEUE
    # =================================================

    def check_error_queue(self, max_errors=10):
        """No real SCPI error queue exists in sim mode; always empty.

        Present for method parity with BaseSCPIPowerSupply (see
        CLAUDE.md's simulated-hardware rule) so Magnet.check_error_queues()
        doesn't need to special-case sim vs. real power supplies, and so
        sim-mode tests can monkeypatch this to exercise
        check_error_queues()'s aggregation/logging without a real
        instrument.
        """

        return []