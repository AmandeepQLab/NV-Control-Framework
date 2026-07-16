import time


class SimPulseStreamer:

    def __init__(self):
        self.sequence = None
        self.digital_outputs = {}

    def load_sequence(self, sequence):
        self.sequence = sequence

    def run(self):

        if self.sequence is None:
            raise RuntimeError("No sequence loaded")

        print("\n[Sim Pulse Streamer START]")

        # flatten all pulses
        events = []

        for p in self.sequence.digital_pulses:
            events.append((p.start_ns, "ON", p.channel))
            events.append((p.start_ns + p.duration_ns, "OFF", p.channel))

        # sort by time
        events.sort(key=lambda x: x[0])

        t0 = events[0][0] if events else 0

        for t, state, ch in events:

            dt = t - t0
            time.sleep(0.01)  # slow simulation for visibility

            print(f"t={dt:6d} ns | CH {ch} {state}")

        print("[Sim Pulse Streamer END]\n")

    def set_digital_output(self, channel, state):

        self.digital_outputs[channel] = bool(state)
