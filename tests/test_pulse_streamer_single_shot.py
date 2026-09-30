"""Tests for single-shot pulse-streamer sequences and the stale-buffer
guard they make necessary.

No real hardware. Uses the real (network-free) pulsestreamer.Sequence/
OutputState classes for realistic argument shapes -- both are pure
in-memory data builders, confirmed by reading their source, no socket/DLL
calls -- plus fake doubles for everything that would otherwise touch a
socket or an SDK DLL.
"""

import unittest
from unittest import mock

import numpy as np
from pulsestreamer import Sequence, OutputState
from PyQt6.QtCore import QCoreApplication, QTimer  # type: ignore

from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.camera.sim_camera import SimCamera
from hardware.microwave.sim_microwave import SimMicrowave
from hardware.magnet.magnet import Magnet, POSITIVE, NEGATIVE
from hardware.power_supply.sim_power_supply import SimPowerSupply
from sequencing.pulse_sequence import PulseSequence
from experiments.odmr_experiment import ODMRExperiment
from gui.odmr_worker import ODMRWorker


def _ensure_qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


CHANNEL_MAP = {
    "greenLaser": 1, "MW": 2, "detector": 5,
    "flipMF_X": 6, "flipMF_Y": 7, "flipMF_Z": 3,
}
RELAY_CHANNELS = {"X": 6, "Y": 7, "Z": 3}


# =====================================================
# Fake PulseStreamer double -- no network I/O
# =====================================================

class FakePS:
    """Mimics just the pulsestreamer.PulseStreamer surface
    SwabianPulseStreamer calls."""

    def __init__(self):
        self.stream_calls = []
        # (seq, kwargs) for every stream() call, in chronological order --
        # additive alongside stream_calls so existing kwargs-only
        # assertions are unaffected. Lets a test walk everything ever
        # dispatched to the device and inspect each sequence's own
        # per-channel content, not just the top-level stream() arguments.
        self.streamed = []

    def createSequence(self):
        return Sequence()

    def stream(self, seq, **kwargs):
        self.stream_calls.append(kwargs)
        self.streamed.append((seq, kwargs))


def _make_streamer():
    streamer = SwabianPulseStreamer(ip_address="fake", channel_map=dict(CHANNEL_MAP))
    streamer.ps = FakePS()
    return streamer


def _make_loaded_streamer():
    streamer = _make_streamer()
    seq = PulseSequence()
    seq.add_pulse(channel=CHANNEL_MAP["greenLaser"], start_ns=0, duration_ns=1000)
    streamer.load_sequence(seq)
    return streamer


# =====================================================
# run() argument-passing: the core regression this task exists to prevent
# =====================================================

class RunArgumentPassingTests(unittest.TestCase):
    def test_run_without_n_runs_preserves_current_behavior(self):
        streamer = _make_loaded_streamer()
        streamer.run()
        call = streamer.ps.stream_calls[-1]
        self.assertNotIn("n_runs", call)

    def test_run_with_n_runs_1_passes_it_through(self):
        streamer = _make_loaded_streamer()
        streamer.run(n_runs=1)
        call = streamer.ps.stream_calls[-1]
        self.assertEqual(call["n_runs"], 1)

    def test_final_defaults_to_zero_when_nothing_persistent_is_set(self):
        streamer = _make_loaded_streamer()
        streamer.run(n_runs=1)
        digi_mask, _, _ = streamer.ps.stream_calls[-1]["final"].getData()
        self.assertEqual(digi_mask, 0)

    def test_final_keeps_relay_channel_high_under_negative_polarity(self):
        """The regression this task exists to prevent: naively adding
        n_runs=1 with the SDK's own default final=OutputState.ZERO()
        would zero the relay line every single ODMR frame."""
        streamer = _make_loaded_streamer()
        for channel in RELAY_CHANNELS.values():
            with self.subTest(channel=channel):
                streamer.set_digital_output(channel, True)
                streamer.run(n_runs=1)
                digi_mask, _, _ = streamer.ps.stream_calls[-1]["final"].getData()
                self.assertTrue(digi_mask & (1 << channel))

    def test_final_still_computed_when_looping(self):
        """final is built unconditionally, not just when n_runs=1 is
        requested -- harmless under looping (never reached), but must not
        silently depend on the caller asking for single-shot."""
        streamer = _make_loaded_streamer()
        streamer.set_digital_output(CHANNEL_MAP["flipMF_Y"], True)
        streamer.run()  # no n_runs -- still looping
        call = streamer.ps.stream_calls[-1]
        self.assertIn("final", call)
        digi_mask, _, _ = call["final"].getData()
        self.assertTrue(digi_mask & (1 << CHANNEL_MAP["flipMF_Y"]))

    def test_explicit_final_override_is_respected(self):
        streamer = _make_loaded_streamer()
        custom = OutputState(digi=[CHANNEL_MAP["MW"]])
        streamer.run(n_runs=1, final=custom)
        self.assertIs(streamer.ps.stream_calls[-1]["final"], custom)


# =====================================================
# reset_outputs() preserves persistent_outputs (the relay) instead of
# unconditionally zeroing every known channel -- the bug this task fixes
# =====================================================

def _channel_pattern(seq, channel):
    """The exact (duration, state) list passed to Sequence.setDigital()
    for one channel -- read back via the real vendor Sequence.getDigital(),
    not the merged/unioned timeline, so each channel's own segments are
    unambiguous."""
    return seq.getDigital()[channel][0]


class ResetOutputsPreservesRelayTests(unittest.TestCase):
    def test_reset_outputs_preserves_set_relay_channel(self):
        streamer = _make_streamer()
        active_channels = {RELAY_CHANNELS["X"], RELAY_CHANNELS["Z"]}
        for channel in active_channels:
            streamer.set_digital_output(channel, True)

        streamer.reset_outputs()

        seq, kwargs = streamer.ps.streamed[-1]
        self.assertEqual(kwargs, {})  # n_runs/final still untouched

        for name, ch in CHANNEL_MAP.items():
            pattern = _channel_pattern(seq, ch)
            self.assertTrue(
                all(state == int(ch in active_channels) for _, state in pattern),
                f"{name} (channel {ch}) did not preserve its independent state",
            )

    def test_reset_outputs_matches_old_behavior_when_nothing_is_set(self):
        streamer = _make_streamer()  # persistent_outputs all False

        streamer.reset_outputs()

        seq, _ = streamer.ps.streamed[-1]
        for ch in CHANNEL_MAP.values():
            pattern = _channel_pattern(seq, ch)
            self.assertTrue(all(state == 0 for _, state in pattern))

    def test_close_de_energizes_relay_regardless_of_persistent_state(self):
        streamer = _make_streamer()
        for channel in RELAY_CHANNELS.values():
            streamer.set_digital_output(channel, True)

        streamer.close()

        seq, kwargs = streamer.ps.streamed[-1]
        self.assertEqual(kwargs, {})
        for ch in CHANNEL_MAP.values():
            pattern = _channel_pattern(seq, ch)
            self.assertTrue(
                all(state == 0 for _, state in pattern),
                f"channel {ch} should be de-energized at shutdown",
            )

    def test_sim_pulse_streamer_reset_outputs_preserves_relay(self):
        """SimPulseStreamer parity -- see CLAUDE.md: sim classes must have
        full method parity, not just currently-called methods."""
        streamer = SimPulseStreamer(channel_map=dict(CHANNEL_MAP))
        active_channels = {RELAY_CHANNELS["X"], RELAY_CHANNELS["Z"]}
        for channel in active_channels:
            streamer.set_digital_output(channel, True)

        streamer.reset_outputs()

        for name, ch in CHANNEL_MAP.items():
            self.assertEqual(streamer.last_reset_channels[ch], int(ch in active_channels), name)

    def test_sim_pulse_streamer_close_de_energizes_relay(self):
        streamer = SimPulseStreamer(channel_map=dict(CHANNEL_MAP))
        for channel in RELAY_CHANNELS.values():
            streamer.set_digital_output(channel, True)

        streamer.close()

        self.assertTrue(
            all(state == 0 for state in streamer.last_reset_channels.values())
        )


# =====================================================
# reset_outputs() / sync_outputs() / close() -- confirm untouched
# =====================================================

class UntouchedMethodsTests(unittest.TestCase):
    def test_reset_outputs_passes_no_n_runs_or_final(self):
        streamer = _make_streamer()
        streamer.reset_outputs()
        self.assertEqual(streamer.ps.stream_calls[-1], {})

    def test_sync_outputs_passes_no_n_runs_or_final(self):
        streamer = _make_streamer()
        streamer.sync_outputs()
        self.assertEqual(streamer.ps.stream_calls[-1], {})

    def test_close_only_calls_reset_outputs(self):
        streamer = _make_streamer()
        streamer.close()
        self.assertEqual(len(streamer.ps.stream_calls), 1)
        self.assertEqual(streamer.ps.stream_calls[-1], {})


# =====================================================
# Magnet relay path: confirmed to never call run()/reset_outputs(), and
# safe on the shared persistent_outputs/PulseStreamer instance ODMR uses
# =====================================================

def _make_magnet(streamer):
    """Full magnet stack: simulated supplies and a network-free driver double."""
    config = {}
    supplies = {}
    for axis, channel in RELAY_CHANNELS.items():
        config[axis] = {
            "name": axis,
            "max_current": 5.0,
            "magnetic_field_calibration": {
                "positive": {"magnetic_field_ratio": 5.0, "magnetic_field_offset": 0.0},
                "negative": {"magnetic_field_ratio": 5.0, "magnetic_field_offset": 0.0},
            },
            "flip_direction": {
                "available": True,
                "negative_state": True,
                "switch": {"switchChannel": channel, "switchChannelName": f"flipMF_{axis}"},
                "discharge_delay_s": 0.0,
                "settle_delay_s": 0.0,
            },
        }
        supplies[axis] = SimPowerSupply({"name": axis})
        supplies[axis].connect()
    magnet = Magnet(
        config=config, power_supplies=supplies, pulse_streamer=streamer,
        scpi_write_delay_s=0.0,
    )
    magnet.enable()
    streamer.ps.stream_calls.clear()
    streamer.ps.streamed.clear()
    return magnet


class MagnetSharedStreamerTests(unittest.TestCase):
    def test_axis_reversal_only_syncs_its_own_relay(self):
        for changed_axis, channel in RELAY_CHANNELS.items():
            with self.subTest(axis=changed_axis):
                streamer = _make_streamer()
                magnet = _make_magnet(streamer)
                with mock.patch.object(streamer, "run", wraps=streamer.run) as run, \
                     mock.patch.object(streamer, "reset_outputs", wraps=streamer.reset_outputs) as reset:
                    getattr(magnet, changed_axis.lower()).set_field(-1.0, keep_output_enabled=True)

                run.assert_not_called()
                reset.assert_not_called()
                self.assertEqual(streamer.ps.stream_calls, [{}])  # sync_outputs only
                for relay_channel in RELAY_CHANNELS.values():
                    self.assertEqual(streamer.get_digital_output(relay_channel), relay_channel == channel)

    def test_odmr_run_after_relay_set_keeps_relay_high(self):
        """The integration case that actually matters: the relay and
        ODMR's frame sequences share one PulseStreamer instance. Setting
        the relay via the magnet's API, then running an ODMR-style
        single-shot sequence on the SAME instance, must not clobber it."""
        streamer = _make_loaded_streamer()
        magnet = _make_magnet(streamer)

        magnet.set_vector(bx=-1.0, by=1.0, bz=-1.0)

        streamer.run(n_runs=1)

        # persistent_outputs itself is untouched by run()...
        for axis, channel in RELAY_CHANNELS.items():
            self.assertEqual(streamer.get_digital_output(channel), axis != "Y")
        # ...and the final state ODMR's single-shot sequence leaves the
        # device in still asserts the relay channel.
        digi_mask, _, _ = streamer.ps.stream_calls[-1]["final"].getData()
        for axis, channel in RELAY_CHANNELS.items():
            self.assertEqual(bool(digi_mask & (1 << channel)), axis != "Y")

    def test_mixed_relay_states_preserved_in_every_streamed_sequence_during_scan(self):
        """All three relay lines keep their selected branch during playback,
        between OFF/ON frames, and in each single-shot final state."""
        streamer = _make_streamer()
        magnet = _make_magnet(streamer)
        magnet.set_vector(bx=-1.0, by=1.0, bz=-1.0)
        streamer.ps.stream_calls.clear()
        streamer.ps.streamed.clear()

        mw = SimMicrowave()
        mw.connect()
        camera = SimCamera(image_shape=(4, 4))
        hardware = {
            "camera": camera,
            "microwave": mw,
            "pulse_streamer": streamer,
            "channels": CHANNEL_MAP,
        }
        config = {
            "mw_power_dbm": -10,
            "repeats": 3,
            "exposure_s": 0.001,
            "trigger_delay_s": 0.001,
            "fire_delay_s": 0.001,
            "reset_delay_s": 0.001,
            "pulse_lead_s": 0.0005,
            "pulse_tail_s": 0.0005,
        }
        experiment = ODMRExperiment(hardware, config)

        experiment.acquire_frame()

        # repeats * 2 frames * (reset_outputs + run)
        self.assertEqual(len(streamer.ps.streamed), 3 * 2 * 2)

        for i, (seq, kwargs) in enumerate(streamer.ps.streamed):
            for axis, channel in RELAY_CHANNELS.items():
                expected_state = int(axis != "Y")
                pattern = _channel_pattern(seq, channel)
                self.assertTrue(
                    all(state == expected_state for _, state in pattern),
                    f"streamed call #{i} changed {axis} relay during playback: {pattern}",
                )
                if "final" in kwargs:
                    digi_mask, _, _ = kwargs["final"].getData()
                    self.assertEqual(
                        bool(digi_mask & (1 << channel)), bool(expected_state),
                        f"streamed call #{i} changed {axis} relay in final state",
                    )

    def test_disable_and_reenable_preserve_independent_relay_states(self):
        streamer = _make_streamer()
        magnet = _make_magnet(streamer)
        magnet.set_vector(bx=1.0, by=-1.0, bz=-1.0)
        streamer.ps.stream_calls.clear()

        magnet.disable()
        magnet.enable()

        self.assertEqual(streamer.ps.stream_calls, [])
        for axis, channel in RELAY_CHANNELS.items():
            expected_negative = axis != "X"
            self.assertEqual(streamer.get_digital_output(channel), expected_negative)
            self.assertEqual(
                getattr(magnet, axis.lower()).get_polarity(),
                NEGATIVE if expected_negative else POSITIVE,
            )


# =====================================================
# Stale/pre-filled buffer cannot be returned as a fresh frame
# =====================================================

class FakeSDKCamFastReturn:
    """Mimics just enough of the andor3.Andor3 surface to exercise
    grab_external_frame()'s stale-buffer plausibility guard. waitBuffer()
    returns immediately by default -- simulating a buffer a stray gate
    already filled before grab_external_frame() started waiting."""

    def __init__(self, width=2, height=2, wait_sleep_s=0.0):
        self._width = width
        self._height = height
        self._wait_sleep_s = wait_sleep_s

    def setEnumIndex(self, feature, index):
        pass

    def command(self, feature):
        pass

    def queueBuffer(self, count):
        pass

    def flush(self):
        pass

    def getInt(self, feature):
        return {
            "AOIWidth": self._width,
            "AOIHeight": self._height,
            "AOIStride": self._width * 2,
        }[feature]

    def waitBuffer(self, timeout_ms, copy=False, requeue=False):
        if self._wait_sleep_s:
            import time
            time.sleep(self._wait_sleep_s)
        return np.zeros(self._width * self._height * 2, dtype=np.uint8)


class StaleBufferGuardTests(unittest.TestCase):
    def test_implausibly_fast_return_is_rejected(self):
        camera = AndorNeoAndor3()
        camera.cam = FakeSDKCamFastReturn()  # returns instantly
        camera.exposure_time = 0.05
        camera.begin_external_acquisition()
        with self.assertRaisesRegex(RuntimeError, "stray camera gate"):
            camera.grab_external_frame()

    def test_return_at_or_above_exposure_time_is_accepted(self):
        camera = AndorNeoAndor3()
        camera.cam = FakeSDKCamFastReturn(wait_sleep_s=0.01)
        camera.exposure_time = 0.005  # floor = 2.5 ms; 10 ms sleep clears it
        camera.begin_external_acquisition()
        frame = camera.grab_external_frame()
        self.assertIsNotNone(frame)

    def test_sim_camera_rejects_implausibly_fast_grab(self):
        class InstantSimCamera(SimCamera):
            def _acquire_and_store_frame(self):
                frame = np.ones(self.image_shape)
                self.latest_frame = frame
                return frame

        camera = InstantSimCamera(image_shape=(4, 4))
        camera.exposure_time = 0.05
        camera.begin_external_acquisition()
        with self.assertRaisesRegex(RuntimeError, "stray camera gate"):
            camera.grab_external_frame()


# =====================================================
# ODMR-scan-level regression: every run() call during a scan is n_runs=1
# =====================================================

class RecordingFakePulseStreamer:
    """Records every run() call's n_runs -- no device I/O, no looping
    concept, just an argument recorder wired into a real ODMR scan."""

    def __init__(self):
        self.run_calls = []
        self.reset_outputs_calls = 0

    def load_sequence(self, sequence):
        sequence.validate()

    def reset_outputs(self, duration_ns=1000):
        self.reset_outputs_calls += 1

    def run(self, n_runs=None, final=None):
        self.run_calls.append(n_runs)


def _run_worker_to_completion(worker, timeout_s=15.0):
    app = _ensure_qapp()
    state = {"finished": False}

    def on_finished():
        state["finished"] = True
        app.quit()

    worker.finished_signal.connect(on_finished)

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(app.quit)
    watchdog.start(int(timeout_s * 1000))

    QTimer.singleShot(0, worker.start)
    app.exec()

    return state["finished"]


class ODMRScanSingleShotRegressionTests(unittest.TestCase):
    def test_every_run_call_during_a_scan_is_single_shot(self):
        camera = SimCamera(image_shape=(4, 4))
        mw = SimMicrowave()
        mw.connect()
        pulse = RecordingFakePulseStreamer()

        hardware = {
            "camera": camera,
            "microwave": mw,
            "pulse_streamer": pulse,
            "channels": dict(CHANNEL_MAP),
        }
        config = {
            "f_start": 2.80e9, "f_stop": 2.94e9, "steps": 3,
            "averages": 1, "repeats": 2,
            "mw_power_dbm": -10, "exposure_s": 0.001,
            "trigger_delay_s": 0.001, "fire_delay_s": 0.001,
            "reset_delay_s": 0.001, "save_data": False,
        }
        worker = ODMRWorker(hardware, config, acquisition_roi=None)

        finished = _run_worker_to_completion(worker)

        self.assertTrue(finished)
        # 3 points x 1 average x 2 repeats x 2 frames (off/on) = 12 calls.
        self.assertEqual(len(pulse.run_calls), 12)
        self.assertTrue(all(n == 1 for n in pulse.run_calls))


if __name__ == "__main__":
    unittest.main()
