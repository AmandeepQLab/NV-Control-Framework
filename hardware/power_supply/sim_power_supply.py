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

import logging
from hardware.power_supply.base_power_supply import (
    BasePowerSupply,
)



LOGGER = logging.getLogger(__name__)

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

        LOGGER.debug("%s: connected (simulated)", self.name)

    def disconnect(self):

        self.connected = False

        LOGGER.debug("%s: disconnected (simulated)", self.name)

    # =================================================
    # OUTPUT
    # =================================================

    def output_on(self):

        self.output_enabled = True

        LOGGER.debug("%s: output ON", self.name)

    def output_off(self):

        self.output_enabled = False

        LOGGER.debug("%s: output OFF", self.name)

    # =================================================
    # PROGRAMMING
    # =================================================

    def set_voltage(self, voltage):

        self.voltage = voltage

        LOGGER.debug("%s: voltage = %.3f V", self.name, voltage)

    def set_current(self, current):

        self.current = current

        LOGGER.debug("%s: current = %.3f A", self.name, current)

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