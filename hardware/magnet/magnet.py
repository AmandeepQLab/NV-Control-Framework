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

    # =====================================================
    # ZERO MAGNET
    # =====================================================

    def zero(self):

        self.x.zero()

        self.y.zero()

        self.z.zero()

    # =====================================================
    # VECTOR CONTROL
    # =====================================================

    def set_vector(self, bx=0.0, by=0.0, bz=0.0,):

        #
        # Determine requested direction
        #

        values = [bx, by, bz]

        signs = set()

        for value in values:

            if value > 0:

                signs.add(POSITIVE)

            elif value < 0:

                signs.add(NEGATIVE)

        if len(signs) > 1:

            raise ValueError(

                "Mixed-sign magnetic fields "
                "are not supported."

            )

        #
        # Select direction
        #

        if NEGATIVE in signs:

            self.set_direction(NEGATIVE)

        else:

            self.set_direction(POSITIVE)

        #
        # Set positive magnitudes
        #

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

    def get_direction(self):

        return self.direction


    def set_direction(self, direction):

        if direction not in (POSITIVE, NEGATIVE):

            raise ValueError(
                "Direction must be +1 or -1."
            )

        #
        # TODO:
        # Send TTL to Pulse Streamer
        #

        self.direction = direction


    def flip_direction(self):

        if self.direction == POSITIVE:

            self.set_direction(NEGATIVE)

        else:

            self.set_direction(POSITIVE)