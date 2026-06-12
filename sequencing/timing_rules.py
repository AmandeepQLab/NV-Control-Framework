"""
Timing rules for Swabian-based execution.

This defines constraints that ALL sequences must obey.
"""


MIN_TIME_NS = 0


def validate_sequence(sequence):
    """
    Basic validation before sending to hardware.
    """

    for p in sequence.digital_pulses:

        if p.start_ns < MIN_TIME_NS:
            raise ValueError("Negative time not allowed")

        if p.duration_ns <= 0:
            raise ValueError("Pulse duration must be > 0")

        if p.channel < 0:
            raise ValueError("Invalid channel index")

    return True