"""
=====================================================
Base Power Supply
-----------------------------------------------------
Abstract interface for programmable power supplies.

Every power supply driver should inherit from this
class.

Author:
    Amandeep + ChatGPT
=====================================================
"""


class BasePowerSupply:

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, config):

        self.config = config

        self.name = config.get(
            "name",
            "Power Supply"
        )

        self.address = config.get(
            "address",
            ""
        )

        self.connected = False

        self.output_enabled = False

    # =================================================
    # CONNECTION
    # =================================================

    def connect(self):
        raise NotImplementedError()

    def disconnect(self):
        raise NotImplementedError()

    def is_connected(self):

        return self.connected

    # =================================================
    # OUTPUT
    # =================================================

    def output_on(self):
        raise NotImplementedError()

    def output_off(self):
        raise NotImplementedError()

    def is_output_on(self):

        return self.output_enabled

    # =================================================
    # PROGRAMMING
    # =================================================

    def set_voltage(self, voltage):
        raise NotImplementedError()

    def set_current(self, current):
        raise NotImplementedError()

    # =================================================
    # MEASUREMENTS
    # =================================================

    def get_voltage(self):
        raise NotImplementedError()

    def get_current(self):
        raise NotImplementedError()

    # =================================================
    # IDENTIFICATION
    # =================================================

    def identify(self):
        raise NotImplementedError()
    # =================================================
    # SAFE SHUTDOWN
    # =================================================

    def safe_shutdown(self):

        self.set_current(0.0)

        self.output_off()

        self.current = 0.0