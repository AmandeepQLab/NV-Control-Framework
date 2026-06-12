from pulsestreamer import PulseStreamer


class SwabianPulseStreamer:

    def __init__(self, ip_address, channel_map=None):
        self.ip_address = ip_address
        self.channel_map = channel_map or {}
        self.ps = None
        self.sequence = None

    def connect(self):
        self.ps = PulseStreamer(self.ip_address)
        print(f"[Swabian] Connected to {self.ip_address}")

    def load_sequence(self, sequence):
        sequence.validate()
        self.sequence = sequence

    def compile_sequence(self):
        if self.sequence is None:
            raise RuntimeError("No sequence loaded")

        events = set([0])

        for p in self.sequence.digital_pulses:
            events.add(p.start_ns)
            events.add(p.start_ns + p.duration_ns)

        # Add short low tail so outputs return low
        end_time = max(events)
        events.add(end_time + 1000)

        times = sorted(events)

        channels = sorted(set(p.channel for p in self.sequence.digital_pulses))

        compiled = {}

        for ch in channels:
            pattern = []

            for i in range(len(times) - 1):
                t0 = times[i]
                t1 = times[i + 1]
                duration = t1 - t0

                state = 0

                for p in self.sequence.digital_pulses:
                    if p.channel == ch:
                        if p.start_ns <= t0 < p.start_ns + p.duration_ns:
                            state = p.state

                pattern.append((duration, state))

            compiled[ch] = pattern

        return compiled

    def run(self):
        compiled = self.compile_sequence()

        seq = self.ps.createSequence()

        for ch, pattern in compiled.items():
            seq.setDigital(ch, pattern)

        self.ps.stream(seq)

        print("[Swabian] Sequence streamed to hardware")

    def reset_outputs(self, duration_ns=1000):
        """
        Force all known digital output channels LOW.
        """

        seq = self.ps.createSequence()

        channels = set()

        if self.channel_map:
            channels.update(self.channel_map.values())

        if self.sequence is not None:
            for p in self.sequence.digital_pulses:
                channels.add(p.channel)

        for ch in channels:
            seq.setDigital(ch, [(duration_ns, 0)])

        self.ps.stream(seq)

        print("[Swabian] Outputs reset LOW")

    def close(self):
        try:
            self.reset_outputs()
        except Exception:
            pass
