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
    # ERROR QUEUE (diagnostic instrumentation, not called per write)
    # =================================================

    def check_error_queue(self, max_errors=10):
        """Drain SYST:ERR? until the instrument reports no error, or
        max_errors is reached (a safety bound in case a fault leaves the
        queue returning errors indefinitely). Returns the list of
        non-empty error strings found, in the order reported.

        This is the only place any power-supply driver reads a response
        after a command in normal operation -- see
        Magnet.check_error_queues() for why it's deliberately not part of
        set_current()/output_on()/output_off()'s own call path.
        """

        errors = []

        for _ in range(max_errors):

            response = self.query("SYST:ERR?").strip()

            if not response:
                break

            code = response.split(",", 1)[0].strip().lstrip("+")

            if code in ("0", ""):
                break

            errors.append(response)

        return errors

    # =================================================
    # DISCONNECT
    # =================================================

    def disconnect(self):

        if self.connected:

            self.interface.disconnect()

            self.connected = False

            print(f"[{self.name}] Disconnected")