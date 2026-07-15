"""
=====================================================
Base SCPI Power Supply
-----------------------------------------------------
Common functionality for SCPI-compatible power
supplies.

Handles communication and generic SCPI commands.

Author:
    Amandeep + ChatGPT
=====================================================
"""

from hardware.interfaces import SerialDevice

from .base_power_supply import BasePowerSupply


class BaseSCPIPowerSupply(BasePowerSupply):

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, config):

        super().__init__(config)

        self.interface = SerialDevice(
            self.address
        )

        self.idn = ""

    # =================================================
    # IDENTIFICATION
    # =================================================

    def get_identification(self):

        return self.idn

    # =================================================
    # CONNECTION
    # =================================================

    def connect(self):

        self.interface.connect()

        self.interface.flush()

        self.connected = True

        self.idn = self.identify()

        print(
            f"[{self.name}] Connected: {self.idn}"
        )

    # =================================================
    # COMMUNICATION
    # =================================================

    def write(self, command):

        self.interface.write(command)

    def query(self, command):

        return self.interface.query(command)

    # =================================================
    # IDENTIFICATION
    # =================================================

    def identify(self):

        return self.query("*IDN?")

    # =================================================
    # RESET
    # =================================================

    def reset(self):

        self.write("*RST")

    # =================================================
    # DISCONNECT
    # =================================================

    def disconnect(self):

        if self.connected:

            self.interface.disconnect()

            self.connected = False

            print(f"[{self.name}] Disconnected")