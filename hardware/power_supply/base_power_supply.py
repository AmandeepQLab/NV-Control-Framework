"""
=====================================================
Base Power Supply
-----------------------------------------------------
Abstract base class for programmable power supplies.

All concrete power supply drivers should inherit from
this class.

Author:
    Amandeep + ChatGPT
=====================================================
"""

from abc import ABC, abstractmethod


class BasePowerSupply(ABC):

    def __init__(self, config):

        self.config = config

        self.name = config.get("name", "Power Supply")

        self.connected = False

    # =================================================
    # CONNECTION
    # =================================================

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    # =================================================
    # OUTPUT
    # =================================================

    @abstractmethod
    def output_on(self):
        pass

    @abstractmethod
    def output_off(self):
        pass

    # =================================================
    # PROGRAMMING
    # =================================================

    @abstractmethod
    def set_voltage(
        self,
        voltage,
    ):
        pass

    @abstractmethod
    def set_current(
        self,
        current,
    ):
        pass

    # =================================================
    # MEASUREMENTS
    # =================================================

    @abstractmethod
    def measure_voltage(self):
        pass

    @abstractmethod
    def measure_current(self):
        pass

    # =================================================
    # IDENTIFICATION
    # =================================================

    @abstractmethod
    def identify(self):
        pass