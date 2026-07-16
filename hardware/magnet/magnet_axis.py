"""
=====================================================
Magnet Axis
-----------------------------------------------------
Represents a single Helmholtz coil axis.

Responsible for converting magnetic field to current
using calibration parameters and communicating with
the associated power supply.

Author:
    Amandeep + ChatGPT
=====================================================
"""


class MagnetAxis:

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(
        self,
        config,
        power_supply,
    ):

        self.config = config
        self.power_supply = power_supply

        self.name = config["name"]

        self.max_current = config["max_current"]

        self.field_ratio = config["magnetic_field_ratio"]

        self.field_offset = config["magnetic_field_offset"]

        self.current_field = 0.0
        self.current_current = 0.0

    # =====================================================
    # CONVERSION
    # =====================================================

    def field_to_current(
        self,
        field_mT,
    ):

        return (

            field_mT
            - self.field_offset

        ) / self.field_ratio

    def current_to_field(
        self,
        current,
    ):

        return (

            self.field_ratio
            * current

            + self.field_offset

        )

    # =====================================================
    # CONTROL
    # =====================================================

    def set_field(self,field_mT):

        field_mT = abs(field_mT)

        current = self.field_to_current(field_mT)

        if abs(current) > self.max_current:

            raise ValueError(
                f"{self.name}-coil exceeds maximum current."
            )

        # -------------------------------------------------
        # Zero field
        # -------------------------------------------------

        if abs(current) < 1e-9:

            self.power_supply.safe_shutdown()

        else:

            self.power_supply.set_current(current)

            current = self.field_to_current(field_mT)

            self.power_supply.set_current(current)

            self.current_field = field_mT

        self.current_field = field_mT

        self.current_current = current

    def zero(self):

        self.power_supply.safe_shutdown()

        self.current_field = 0.0
    # =====================================================
    # GETTERS
    # =====================================================

    def get_field(self):

        return self.current_field

    def get_current(self):

        return self.current_current

    def get_ratio(self):

        return self.field_ratio

    def get_offset(self):

        return self.field_offset

    def get_max_current(self):

        return self.max_current

    # =====================================================
    # POWER SUPPLY
    # =====================================================

    def get_supply_type(self):

        return self.config.get(
            "type",
            "--"
        )

    def get_supply_address(self):

        return self.config.get(
            "address",
            "--"
        )

    def get_supply_range(self):

        return self.config.get(
            "range",
            self.config.get(
                "channel",
                "--"
            )
        )
