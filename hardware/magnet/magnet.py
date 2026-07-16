"""
=====================================================
Magnet Hardware
-----------------------------------------------------
Abstraction layer for Helmholtz coils.

Experiments should NEVER communicate directly with
power supplies.

Instead they should simply request

    magnet.z.set_field(1.5)

or

    magnet.set_vector(...)

Author:
    Amandeep + ChatGPT
=====================================================
"""

from .magnet_axis import MagnetAxis
from enum import Enum
# =====================================================
# MAGNET DIRECTION
# =====================================================

class MagnetDirection(Enum):
    POSITIVE = 1
    NEGATIVE = -1

# =====================================================
# Backward compatibility
# These aliases will eventually be removed once all
# modules use MagnetDirection directly.
# =====================================================

POSITIVE = MagnetDirection.POSITIVE
NEGATIVE = MagnetDirection.NEGATIVE


class Magnet:

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(
        self,
        config,
        power_supplies,
        pulse_streamer=None,
    ):

        self.config = config

        self.power_supplies = power_supplies

        self.pulse_streamer = pulse_streamer

        # -------------------------------------------------
        # CURRENT DIRECTION
        # -------------------------------------------------

        # +1 = Positive
        # -1 = Negative

        self.direction = POSITIVE

        # -------------------------------------------------
        # AXES
        # -------------------------------------------------

        self.x = MagnetAxis(
            config["X"],
            power_supplies["X"]
        )

        self.y = MagnetAxis(
            config["Y"],
            power_supplies["Y"]
        )

        self.z = MagnetAxis(
            config["Z"],
            power_supplies["Z"]
        )

        # -------------------------------------------------
        # FIELD REVERSAL
        # -------------------------------------------------

        flip = config["flip_direction"]

        self.flip_available = flip["available"]

        self.flip_channel = (
            flip["switch"]["switchChannel"]
        )

        self.flip_channel_name = (
            flip["switch"]["switchChannelName"]
        )
    
    # =================================================
    # MAGNET STATE
    # =================================================

        self.enabled = False

    # =====================================================
    # ZERO MAGNET
    # =====================================================

    def zero(self):

        for axis in (self.x, self.y, self.z):

            axis.power_supply.set_current(0.0)

            axis.current_field = 0.0

            axis.current_current = 0.0

    # =====================================================
    # VECTOR CONTROL
    # =====================================================

    def set_vector(self, bx=0.0, by=0.0, bz=0.0,):

        values = [bx, by, bz]

        direction = self._determine_global_polarity(
            values
        )

        self.set_polarity(direction)

        # -------------------------------------------------
        # Set positive magnitudes
        # -------------------------------------------------

        self.x.set_field(abs(bx))

        self.y.set_field(abs(by))

        self.z.set_field(abs(bz))

    # =====================================================
    # GET VECTOR
    # =====================================================

    def get_vector(self):

       return {

            "x": self.direction.value * self.x.get_field(),

            "y": self.direction.value * self.y.get_field(),

            "z": self.direction.value * self.z.get_field(),

        }
        
    # =================================================
    # ENABLE MAGNET
    # =================================================

    def enable(self):

        self._sync_positive_polarity()

        self.x.power_supply.output_on()

        self.y.power_supply.output_on()

        self.z.power_supply.output_on()

        self.enabled = True

    # =================================================
    # DISABLE MAGNET
    # =================================================

    def disable(self):

        self.zero()

        self._sync_positive_polarity()

        self.x.power_supply.output_off()

        self.y.power_supply.output_off()

        self.z.power_supply.output_off()

        self.enabled = False

    # =================================================
    # MAGNET STATE
    # =================================================

    def is_enabled(self):

        return self.enabled

    # =====================================================
    # MAGNET STATUS
    # =====================================================

    def is_connected(self):

        return (

            self.x.power_supply.connected
            and
            self.y.power_supply.connected
            and
            self.z.power_supply.connected

        )
    # =====================================================
    # DIRECTION
    # =====================================================

    def set_polarity(self, direction):

        if direction not in (POSITIVE, NEGATIVE):

            raise ValueError(
                "Polarity must be POSITIVE or NEGATIVE."
            )

        self._apply_global_polarity(direction)

    def flip_polarity(self):

        if self.direction == POSITIVE:

            self.set_polarity(NEGATIVE)

        else:

            self.set_polarity(POSITIVE)

    def get_polarity(self):

        return self.direction

    def _sync_positive_polarity(self):
        # The polarity relay is a latched hardware device whose state survives
        # application restarts. Therefore the framework explicitly synchronizes
        # the relay to POSITIVE whenever the magnet subsystem is enabled or disabled.
        if self.flip_available and self.pulse_streamer is not None:
            self.pulse_streamer.set_digital_output(
                self.flip_channel,
                False
            )
            self.pulse_streamer.sync_outputs()

        self.direction = POSITIVE

    def _determine_global_polarity(self, values):

        signs = set()

        for value in values:

            if value > 0:

                signs.add(POSITIVE)

            elif value < 0:

                signs.add(NEGATIVE)

        if len(signs) > 1:

            raise ValueError(
                "Mixed-sign magnetic fields cannot be "
                "generated because the polarity relay "
                "is global."
            )

        if NEGATIVE in signs:

            return NEGATIVE

        if POSITIVE in signs:

            return POSITIVE

        return self.direction

    # =====================================================
    # APPLY GLOBAL POLARITY
    # =====================================================

    def _apply_global_polarity(self, direction):

        if (
            direction == NEGATIVE
            and not self.flip_available
        ):

            raise RuntimeError(
                "Negative magnetic fields are not supported "
                "because the global polarity relay is unavailable."
            )

        if direction == self.direction:

            return

        if self.pulse_streamer is not None:

           self.pulse_streamer.set_digital_output(
                self.flip_channel,
                direction == NEGATIVE
            )
           self.pulse_streamer.sync_outputs()

        self.direction = direction

    def get_direction(self):

        return self.get_polarity()


    def set_direction(self, direction):

        self.set_polarity(direction)


    def flip_direction(self):

        self.flip_polarity()
