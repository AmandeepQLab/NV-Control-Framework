from pulsestreamer import PulseStreamer, OutputState


class SwabianPulseStreamer:

    def __init__(self, ip_address, channel_map=None):
        self.ip_address = ip_address
        self.channel_map = channel_map or {}
        self.ps = None
        self.sequence = None
        # Manual controls: set_digital_output -> cache -> sync_outputs -> hardware.
        # Experiments: load_sequence -> compile_sequence -> run -> hardware.
        # Keeping these paths separate prevents manual controls from replacing
        # experiment pulse sequences.
        self.persistent_outputs = {
            channel: False
            for channel in self.channel_map.values()
        }

    def connect(self):
        self.ps = PulseStreamer(self.ip_address)
        print(f"[Swabian] Connected to {self.ip_address}")

    def load_sequence(self, sequence):
        sequence.validate()
        self.sequence = sequence

    def compile_sequence(self):
        if self.sequence is None:
            raise RuntimeError("No sequence loaded")

        # Build experiment timing, then merge pulse overrides onto the
        # persistent output state for every affected digital channel.
        events = set([0])

        for p in self.sequence.digital_pulses:
            events.add(p.start_ns)
            events.add(p.start_ns + p.duration_ns)

        # Add a short tail so pulse channels return to their persistent state.
        end_time = max(events)
        events.add(end_time + 1000)

        times = sorted(events)

        channels = set(self.persistent_outputs)
        channels.update(
            p.channel for p in self.sequence.digital_pulses
        )

        compiled = {}

        for ch in sorted(channels):
            pattern = []

            for i in range(len(times) - 1):
                t0 = times[i]
                t1 = times[i + 1]
                duration = t1 - t0

                state = int(self.get_digital_output(ch))

                for p in self.sequence.digital_pulses:
                    if p.channel == ch:
                        if p.start_ns <= t0 < p.start_ns + p.duration_ns:
                            state = p.state

                pattern.append((duration, state))

            compiled[ch] = pattern

        return compiled

    def run(self, n_runs=None, final=None):
        """Stream the loaded sequence to hardware.

        n_runs is passed through to PulseStreamer.stream() only when given
        explicitly; omitting it preserves today's behavior exactly (the
        vendor SDK's own default, REPEAT_INFINITELY -- the sequence loops
        until replaced by the next stream() call). ODMR's single
        triggered-frame call site passes n_runs=1 so exactly one gate
        fires per frame. reset_outputs()/sync_outputs() and any other
        caller are deliberately left on the unchanged default -- they are
        not part of this change.

        final defaults to the *current* persistent_outputs state (not the
        SDK's own OutputState.ZERO() default) regardless of n_runs. Under
        looping this is never reached (the sequence never "finishes" in
        the firmware's sense), so it costs nothing there. It matters once
        a finite n_runs is requested: without it, every digital channel --
        including the magnet polarity relay's flip_channel, which shares
        this same persistent_outputs dict and this same PulseStreamer
        instance -- would be zeroed the instant the sequence completes,
        regardless of what it was actually last commanded to. Mirrors
        exactly what compile_sequence()'s own trailing tail segment
        already holds each channel at while the sequence is playing.
        """
        compiled = self.compile_sequence()

        seq = self.ps.createSequence()

        for ch, pattern in compiled.items():
            seq.setDigital(ch, pattern)

        if final is None:
            final = OutputState(
                digi=[ch for ch, state in self.persistent_outputs.items() if state]
            )

        if n_runs is None:
            self.ps.stream(seq, final=final)
        else:
            self.ps.stream(seq, n_runs=n_runs, final=final)

        print("[Swabian] Sequence streamed to hardware")

    def reset_outputs(self, duration_ns=1000):
        """
        Temporarily reset all known physical digital outputs to LOW.
        """
        channels = set(self.persistent_outputs)

        if self.channel_map:
            channels.update(self.channel_map.values())

        if self.sequence is not None:
            for p in self.sequence.digital_pulses:
                channels.add(p.channel)

        seq = self.ps.createSequence()

        for ch in channels:
            seq.setDigital(ch, [(duration_ns, 0)])

        self.ps.stream(seq)

    def set_digital_output(self, channel, state):
        """
        Store one persistent digital output state.
        """
        self.persistent_outputs[channel] = bool(state)

    def sync_outputs(self):
        """
        Synchronize cached persistent digital outputs with hardware.
        """
        seq = self.ps.createSequence()

        for channel, state in self.persistent_outputs.items():
            seq.setDigital(
                channel,
                [(100_000_000, int(state))]
            )

        self.ps.stream(seq)

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
