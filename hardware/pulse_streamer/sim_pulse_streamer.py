import logging
import time



LOGGER = logging.getLogger(__name__)

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
        self.last_reset_channels = {}

    def connect(self):
        self.connected = True
        LOGGER.debug("Connected (simulated)%s", f" to {self.ip_address}" if self.ip_address else "")

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

        t0 = events[0][0] if events else 0
        lines = []

        for t, state, ch in events:

            dt = t - t0
            time.sleep(0.01)  # slow simulation for visibility

            lines.append(f"t={dt:6d} ns | CH {ch} {state}")

        # Once per triggered frame: session log only.
        LOGGER.debug("Sequence played (simulated):\n%s", "\n".join(lines))

    def reset_outputs(self, duration_ns=1000, preserve_persistent=True):
        """
        Force a clean baseline on every known output (simulated).

        Mirrors SwabianPulseStreamer.reset_outputs() -- see that method's
        docstring for the preserve_persistent distinction. Tracks the
        resulting per-channel state on self.last_reset_channels (there's
        no real device to hold it) so sim-mode tests can verify parity.
        """
        channels = set(self.persistent_outputs)

        if self.channel_map:
            channels.update(self.channel_map.values())

        if self.sequence is not None:
            for p in self.sequence.digital_pulses:
                channels.add(p.channel)

        self.last_reset_channels = {
            ch: (int(self.get_digital_output(ch)) if preserve_persistent else 0)
            for ch in channels
        }

        LOGGER.debug("reset_outputs(duration_ns=%s, preserve_persistent=%s) -> %s (simulated)",
                     duration_ns, preserve_persistent, self.last_reset_channels)

    def set_digital_output(self, channel, state):
        """
        Store one persistent digital output state.
        """
        self.persistent_outputs[channel] = bool(state)

    def sync_outputs(self):
        """
        Synchronize cached persistent digital outputs with hardware (simulated).
        """
        LOGGER.debug("sync_outputs -> %s (simulated)", self.persistent_outputs)

    def get_digital_output(self, channel):
        """
        Return the persistent state stored for one digital output.
        """
        return self.persistent_outputs.get(channel, False)

    def close(self):
        """Same steps as SwabianPulseStreamer.close(): outputs LOW, stop
        streaming, disconnect. Safe to call twice."""
        if getattr(self, "closed", False):
            return
        try:
            self.reset_outputs(preserve_persistent=False)
        except Exception:
            pass
        self.sequence = None
        self.connected = False
        self.closed = True
