import time


class SimPulseStreamer:

    def __init__(self, ip_address=None, channel_map=None):
        self.ip_address = ip_address
        self.channel_map = channel_map or {}
        self.connected = False
        self.sequence = None
        # Mirrors SwabianPulseStreamer's persistent_outputs cache so
        # set_digital_output/sync_outputs/get_digital_output behave the
        # same way here as against the real relay-toggling driver.
        self.persistent_outputs = {
            channel: False
            for channel in self.channel_map.values()
        }

    def connect(self):
        self.connected = True
        print(f"[Sim Pulse Streamer] Connected (simulated){f' to {self.ip_address}' if self.ip_address else ''}")

    def load_sequence(self, sequence):
        sequence.validate()
        self.sequence = sequence

    def compile_sequence(self):
        if self.sequence is None:
            raise RuntimeError("No sequence loaded")

        # flatten all pulses
        events = []

        for p in self.sequence.digital_pulses:
            events.append((p.start_ns, "ON", p.channel))
            events.append((p.start_ns + p.duration_ns, "OFF", p.channel))

        # sort by time
        events.sort(key=lambda x: x[0])

        return events

    def run(self, n_runs=None, final=None):
        """Accepts the same n_runs/final keywords as SwabianPulseStreamer.run()
        so the live ODMR call site (which now passes n_runs=1) works
        unchanged in sim mode. Sim has no real device-side looping to
        control, so both are accepted and ignored -- run() always just
        plays the sequence once, printed, as it always has."""

        events = self.compile_sequence()

        print("\n[Sim Pulse Streamer START]")

        t0 = events[0][0] if events else 0

        for t, state, ch in events:

            dt = t - t0
            time.sleep(0.01)  # slow simulation for visibility

            print(f"t={dt:6d} ns | CH {ch} {state}")

        print("[Sim Pulse Streamer END]\n")

    def reset_outputs(self, duration_ns=1000):
        """
        Temporarily reset all known outputs to LOW (simulated).
        """
        print(f"[Sim Pulse Streamer] reset_outputs(duration_ns={duration_ns}) (simulated)")

    def set_digital_output(self, channel, state):
        """
        Store one persistent digital output state.
        """
        self.persistent_outputs[channel] = bool(state)

    def sync_outputs(self):
        """
        Synchronize cached persistent digital outputs with hardware (simulated).
        """
        print(f"[Sim Pulse Streamer] sync_outputs -> {self.persistent_outputs} (simulated)")

    def get_digital_output(self, channel):
        """
        Return the persistent state stored for one digital output.
        """
        return self.persistent_outputs.get(channel, False)

    def close(self):
        try:
            self.reset_outputs()
        except Exception:
            pass
