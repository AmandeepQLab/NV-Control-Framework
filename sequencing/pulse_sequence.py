from dataclasses import dataclass
from sequencing.timing_rules import validate_sequence


@dataclass
class DigitalPulse:
    channel: int
    start_ns: int
    duration_ns: int
    state: int = 1


class PulseSequence:
    def add_pulse(self, channel, start_ns, duration_ns, state=1):
        self.add_digital_pulse(channel, start_ns, duration_ns, state)

    def __init__(self):
        self.digital_pulses = []

    def add_digital_pulse(
        self,
        channel: int,
        start_ns: int,
        duration_ns: int,
        state: int = 1
    ):

        pulse = DigitalPulse(
            channel=channel,
            start_ns=start_ns,
            duration_ns=duration_ns,
            state=state
        )

        self.digital_pulses.append(pulse)

    def clear(self):
        self.digital_pulses = []

    def print_sequence(self):

        print("\nPulse Sequence:")
        print("-" * 40)

        for pulse in self.digital_pulses:

            end_ns = pulse.start_ns + pulse.duration_ns

            print(
                f"CH {pulse.channel} | "
                f"{pulse.start_ns} ns -> {end_ns} ns | "
                f"STATE = {pulse.state}"
            )

        print("-" * 40)

    # -----------------------------
    # NEW: validation hook
    # -----------------------------
    def validate(self):
        return validate_sequence(self)
