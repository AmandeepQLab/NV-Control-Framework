"""
=====================================================
Simulated Microwave Source
-----------------------------------------------------
Software-only stand-in for SG386, matching its public
surface so experiment code cannot tell the difference.

Used for development and testing away from the lab.

Author:
    Amandeep + ChatGPT
=====================================================
"""


class SimMicrowave:

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(self, address=None, port=None):

        self.address = address
        self.port = port

        self.connected = False

        # Defaults chosen so a SimCamera reading these before any
        # set_frequency()/set_power() call sees a sane "RF off, at
        # resonance" state rather than None.
        self.frequency = 2.87e9
        self.power = 0

        self.rf_enabled = False

    # =================================================
    # CONNECTION
    # =================================================

    def connect(self):

        self.connected = True

        print(f"[SimMicrowave] Connected (simulated){f' at {self.address}' if self.address else ''}")

    def close(self):

        self.connected = False

    # =================================================
    # FREQUENCY
    # =================================================

    def set_frequency(self, frequency_hz):

        self.frequency = frequency_hz

    def get_frequency(self):

        return self.frequency

    # =================================================
    # POWER
    # =================================================

    def set_power(self, power_dbm):

        self.power = power_dbm

    def get_power(self):

        return self.power

    # =================================================
    # RF OUTPUT
    # =================================================

    def rf_on(self):

        self.rf_enabled = True

    def rf_off(self):

        self.rf_enabled = False
