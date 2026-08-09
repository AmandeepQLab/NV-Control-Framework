import numpy as np
import time
import threading
import logging
import os

from framework.scan_experiment import ScanExperiment
from framework.camera_ownership import exclusive_camera_access
from framework.fluorescence import mean_fluorescence
from framework.roi import validate_roi
from sequencing.pulse_sequence import PulseSequence


LOGGER = logging.getLogger(__name__)

# SG386 manual: amplitude switching settles to within 1 ppm in under this
# long. Used only to decide whether to warn that the accumulated delay
# between an MW power write and the camera gate opening (see
# check_mw_power_settling_margin) has fallen below spec -- not itself a
# delay applied anywhere.
SG386_AMPLITUDE_SETTLE_S = 0.008


class ODMRExperiment(ScanExperiment):

    scan_axis_name = "frequency"
    scan_axis_unit = "Hz"

    def __init__(self, hardware, config, acquisition_roi=None):

        super().__init__(hardware, config)
        self.acquisition_roi = validate_roi(acquisition_roi)

        if all(key in config for key in ("f_start", "f_stop", "steps")):
            self.scan_vector = np.linspace(
                config["f_start"],
                config["f_stop"],
                config["steps"],
            )

        # =====================================================
        # TIMING DIAGNOSTICS (off by default; temporary instrumentation)
        # =====================================================
        # Enabled via config["timing_diagnostics"] or the NV_ODMR_TIMING=1
        # environment variable. Never set by the GUI panel itself.
        self._timing = bool(config.get("timing_diagnostics", False)) or (
            os.environ.get("NV_ODMR_TIMING") == "1"
        )
        self._timing_log = []
        self._timing_epoch = time.perf_counter()

        # Whether a scan-wide held-open camera acquisition is currently
        # active (see begin_camera_acquisition/end_camera_acquisition
        # below). acquire_triggered_frame() checks this to decide whether
        # to pull a frame from the already-armed camera or fall back to
        # the camera's own self-contained per-call snap_external_trigger().
        self._acquisition_open = False

        # Whether the once-per-scan baseline-margin warning (see
        # process_frame) has already fired for the current scan.
        self._baseline_margin_warned = False

    # =====================================================
    # SETUP
    # =====================================================

    def setup(self):

        super().setup()

    def check_mw_power_settling_margin(self):
        """Warn if the accumulated delay before the MW/laser gate opens has
        fallen below the SG386's amplitude-settling spec.

        Sums every delay that elapses between an mw.set_power() write in
        acquire_frame() and the gate actually opening (see acquire_frame/
        acquire_triggered_frame/build_sequence for the derivation of this
        chain): reset_delay_s, fire_delay_s, trigger_delay_s, pulse_lead_s,
        and mw_power_settle_s itself. At today's defaults this margin is
        accidental (none of the first four exist for MW-settling reasons),
        so this check -- not a larger default -- is what catches it
        shrinking below spec if any of those are tuned down later.
        """
        reset_delay_s = self.config.get("reset_delay_s", 0.005)
        fire_delay_s = self.config.get("fire_delay_s", 0.005)
        trigger_delay_s = self.config.get("trigger_delay_s", 0.05)
        pulse_lead_s = self.config.get("pulse_lead_s", 0.002)
        mw_power_settle_s = self.config.get("mw_power_settle_s", 0.0)

        margin_s = (
            reset_delay_s + fire_delay_s + trigger_delay_s
            + pulse_lead_s + mw_power_settle_s
        )

        if margin_s < SG386_AMPLITUDE_SETTLE_S:
            LOGGER.warning(
                "MW power settling margin is %.3f ms, below the SG386's "
                "%.1f ms amplitude-settling spec (<1 ppm). Contributing "
                "delays: reset_delay_s=%.3f ms, fire_delay_s=%.3f ms, "
                "trigger_delay_s=%.3f ms, pulse_lead_s=%.3f ms, "
                "mw_power_settle_s=%.3f ms. ODMR contrast may include an "
                "RF settling transient. Raise mw_power_settle_s (or one of "
                "the other contributing delays) to restore margin.",
                margin_s * 1000,
                SG386_AMPLITUDE_SETTLE_S * 1000,
                reset_delay_s * 1000,
                fire_delay_s * 1000,
                trigger_delay_s * 1000,
                pulse_lead_s * 1000,
                mw_power_settle_s * 1000,
            )

    def reset_baseline_warning_state(self):
        """Call once at scan start so the once-per-scan baseline-margin
        warning (see process_frame) can fire again for a new scan, rather
        than staying silenced from a previous one."""
        self._baseline_margin_warned = False

    def setup_scan(self):
        self.configure_acquisition()
        self.image_cube.scan_axis_name = self.scan_axis_name
        self.image_cube.scan_axis_unit = self.scan_axis_unit
        self.image_cube.scan_axis_values = self.scan_vector

    def configure_acquisition(self):
        """Apply this experiment's temporary camera AOI while it is borrowed."""
        self.hw["camera"].set_roi(self.acquisition_roi)

    # =====================================================
    # TIMING DIAGNOSTICS
    # =====================================================
    # Off by default (see __init__). When disabled, every call site below
    # is a single cached-bool check -- no perf_counter() call, no list
    # append, no allocation. Temporary instrumentation; not a permanent
    # feature.

    def timing_enabled(self):
        return self._timing

    def reset_timing(self):
        """Start a fresh timing epoch and log. Call once at scan start."""
        self._timing_log = []
        self._timing_epoch = time.perf_counter()

    def pop_timing_log(self):
        """Return and clear accumulated timing records (bounds growth)."""
        log = self._timing_log
        self._timing_log = []
        return log

    def set_camera_timing(self, enabled):
        """Enable/disable the camera driver's own stage-level timing.

        Only ODMR calls camera.snap_external_frames(); live view and Zero
        Field use camera.snap(), which this never touches. Disabling here
        also clears the camera-side log (see AndorNeoAndor3.
        enable_timing_diagnostics), so nothing lingers after a scan ends.
        """
        camera = self.hw["camera"]
        if hasattr(camera, "enable_timing_diagnostics"):
            camera.enable_timing_diagnostics(enabled)

    def pop_camera_timing_log(self):
        camera = self.hw["camera"]
        if hasattr(camera, "pop_timing_log"):
            return camera.pop_timing_log()
        return []

    def _record_timing(self, repeat_index, frame_label, stage, t0):
        if not self._timing:
            return
        self._timing_log.append({
            "repeat_index": repeat_index,
            "frame": frame_label,
            "stage": stage,
            "t_start_rel_s": t0 - self._timing_epoch,
            "duration_s": time.perf_counter() - t0,
        })

    def _merge_camera_log(self, repeat_index, frame_label, start_rel):
        """Pop the camera's own per-stage log and fold it into ours.

        Camera-side entries only carry a duration, not an absolute
        timestamp, so their t_start_rel_s is reconstructed by walking
        forward from start_rel in call order -- correct as long as the
        caller pops right after the call whose stages it wants to
        attribute (both acquire_triggered_frame and begin_/end_
        camera_acquisition below do this immediately).
        """
        if not self._timing:
            return
        running_t = start_rel
        for stage, frame_idx, duration in self.pop_camera_timing_log():
            label = f"camera.{stage}" if frame_idx is None else f"camera.{stage}[{frame_idx}]"
            self._timing_log.append({
                "repeat_index": repeat_index,
                "frame": frame_label,
                "stage": label,
                "t_start_rel_s": running_t,
                "duration_s": duration,
            })
            running_t += duration

    # =====================================================
    # HELD-OPEN CAMERA ACQUISITION (scan-wide arm/disarm)
    # =====================================================

    def begin_camera_acquisition(self):
        """Arm the camera once for the whole scan, if it supports it.

        hasattr-guarded so a camera/test-double lacking the new driver
        API (e.g. an older sim, or a fake used only for set_roi()) simply
        never gets the held-open path -- acquire_triggered_frame() then
        falls back to the camera's own self-contained per-frame method.
        """
        camera = self.hw["camera"]
        if not hasattr(camera, "begin_external_acquisition"):
            return
        t0 = time.perf_counter() if self._timing else None
        camera.begin_external_acquisition()
        self._acquisition_open = True
        if self._timing:
            self._merge_camera_log(None, None, t0 - self._timing_epoch)

    def end_camera_acquisition(self):
        """Disarm the camera. Safe to call even if begin_ was never
        called, or if the camera lacks the new API -- always leaves
        self._acquisition_open False."""
        camera = self.hw["camera"]
        self._acquisition_open = False
        if not hasattr(camera, "end_external_acquisition"):
            return
        t0 = time.perf_counter() if self._timing else None
        camera.end_external_acquisition()
        if self._timing:
            self._merge_camera_log(None, None, t0 - self._timing_epoch)

    def reset_acquisition_counts(self):
        camera = self.hw["camera"]
        if hasattr(camera, "reset_acquisition_counts"):
            camera.reset_acquisition_counts()

    def get_acquisition_counts(self):
        camera = self.hw["camera"]
        if hasattr(camera, "get_acquisition_counts"):
            return camera.get_acquisition_counts()
        return (None, None)

    def run(self):
        """Run a scan under one exclusive camera lease."""
        with exclusive_camera_access(self.hw["camera"]):
            return super().run()

    # =====================================================
    # CLEANUP
    # =====================================================

    def cleanup(self):

        super().cleanup()

    # =====================================================
    # BUILD PULSE SEQUENCE
    # =====================================================

    def build_sequence(self, mw_on=True):

        seq = PulseSequence()

        channels = self.hw["channels"]

        laser_ch = channels["greenLaser"]
        mw_ch = channels["MW"]
        det_ch = channels["detector"]

        exposure_s = self.config.get("exposure_s", 0.02)
        exposure_ns = int(exposure_s * 1e9)

        trigger_delay_s = self.config.get("trigger_delay_s", 0.05)
        trigger_delay_ns = int(trigger_delay_s * 1e9)

        camera_gate_s = self.config.get("camera_gate_s", exposure_s)
        camera_gate_ns = int(camera_gate_s * 1e9)

        pulse_lead_s = self.config.get("pulse_lead_s", 0.002)   # 2 ms before camera
        pulse_tail_s = self.config.get("pulse_tail_s", 0.002)   # 2 ms after camera

        pulse_lead_ns = int(pulse_lead_s * 1e9)
        pulse_tail_ns = int(pulse_tail_s * 1e9)

        camera_t = trigger_delay_ns + pulse_lead_ns
        pulse_t = camera_t - pulse_lead_ns
        pulse_duration_ns = camera_gate_ns + pulse_lead_ns + pulse_tail_ns

        # Laser ON slightly before camera and after camera
        seq.add_pulse(
            channel=laser_ch,
            start_ns=pulse_t,
            duration_ns=pulse_duration_ns
        )

        # MW switch ON only for MW ON measurement
        if mw_on:
            seq.add_pulse(
                channel=mw_ch,
                start_ns=pulse_t,
                duration_ns=pulse_duration_ns
            )

        # Camera trigger
        seq.add_pulse(
            channel=det_ch,
            start_ns=camera_t,
            duration_ns=camera_gate_ns
        )

        return seq
    # ==================

    def build_repeated_off_on_sequence(self, repeats):

        seq = PulseSequence()

        channels = self.hw["channels"]

        laser_ch = channels["greenLaser"]
        mw_ch = channels["MW"]
        det_ch = channels["detector"]

        exposure_s = self.config.get("exposure_s", 0.02)
        exposure_ns = int(exposure_s * 1e9)

        trigger_delay_s = self.config.get("trigger_delay_s", 0.05)
        trigger_delay_ns = int(trigger_delay_s * 1e9)

        camera_gate_s = self.config.get("camera_gate_s", exposure_s)
        camera_gate_ns = int(camera_gate_s * 1e9)

        # frame_gap_s doesn't exist in the real acquisition path -- each
        # frame there is fired by its own acquire_triggered_frame() call,
        # separated by reset_delay_s + fire_delay_s (the real dead time
        # between one frame's gate and the next one's dispatch beginning).
        # Reused here purely as this preview's inter-frame spacing so
        # frames render visibly apart; this is still a fabricated combined
        # sequence the hardware never actually runs (see acquire_frame(),
        # which fires two independent single-shot sequences instead).
        frame_gap_s = (
            self.config.get("reset_delay_s", 0.005)
            + self.config.get("fire_delay_s", 0.005)
        )
        frame_gap_ns = int(frame_gap_s * 1e9)

        pulse_lead_s = self.config.get("pulse_lead_s", 0.002)
        pulse_tail_s = self.config.get("pulse_tail_s", 0.002)

        pulse_lead_ns = int(pulse_lead_s * 1e9)
        pulse_tail_ns = int(pulse_tail_s * 1e9)

        camera_t = trigger_delay_ns + pulse_lead_ns
        pulse_duration_ns = camera_gate_ns + pulse_lead_ns + pulse_tail_ns

        for _ in range(repeats):

            # ==========================
            # MW OFF frame
            # ==========================

            pulse_t = camera_t - pulse_lead_ns

            seq.add_pulse(
                channel=laser_ch,
                start_ns=pulse_t,
                duration_ns=pulse_duration_ns
            )

            seq.add_pulse(
                channel=det_ch,
                start_ns=camera_t,
                duration_ns=camera_gate_ns
            )

            camera_t += camera_gate_ns + frame_gap_ns

            # ==========================
            # MW ON frame
            # ==========================

            pulse_t = camera_t - pulse_lead_ns

            seq.add_pulse(
                channel=laser_ch,
                start_ns=pulse_t,
                duration_ns=pulse_duration_ns
            )

            seq.add_pulse(
                channel=mw_ch,
                start_ns=pulse_t,
                duration_ns=pulse_duration_ns
            )

            seq.add_pulse(
                channel=det_ch,
                start_ns=camera_t,
                duration_ns=camera_gate_ns
            )

            camera_t += camera_gate_ns + frame_gap_ns

        return seq
    
    # ==================
    def acquire_triggered_frames(self, pulse, seq, nframes):

        camera = self.hw["camera"]

        pulse.load_sequence(seq)

        pulse.reset_outputs()
        #time.sleep(0.005)
        reset_delay_s = self.config.get("reset_delay_s", 0.005)
        time.sleep(reset_delay_s)

        def fire():
            #time.sleep(0.005)
            fire_delay_s = self.config.get("fire_delay_s", 0.005)
            time.sleep(fire_delay_s)
            pulse.run()

        trigger_thread = threading.Thread(target=fire, daemon=True)
        trigger_thread.start()

        t0 = time.time()

        frames = camera.snap_external_frames(
            nframes=nframes,
            timeout_ms=10000
        )

        t1 = time.time()

        trigger_thread.join()

        LOGGER.debug(
            "Multi-frame acquisition: %d frames in %.3fs", nframes, t1 - t0
        )

        return frames
    # =====================================================
    # FRAME PROCESSING
    # =====================================================

    def _mean_fluorescence(self, frame):
        """Return mean fluorescence per pixel from the received frame."""
        return mean_fluorescence(frame)

    def _integrate_frame(self, frame):
        """Backward-compatible alias for the former frame integration hook."""
        return self._mean_fluorescence(frame)

    def process_frame(self, frame):
        """Process a raw camera frame or one ODMR OFF/ON acquisition."""
        frame = np.asarray(frame)

        # A single camera image remains supported for callers that used this
        # method directly before ODMR became a ScanExperiment.
        if frame.ndim == 2:
            return self._mean_fluorescence(frame)

        pairs = frame[np.newaxis, ...] if frame.ndim == 3 else frame
        i_off_list = []
        i_on_list = []
        signals = []

        baseline = self.config.get("baseline_counts", 0.0)

        for r, pair in enumerate(pairs):
            I_off = self._mean_fluorescence(pair[0])
            I_on = self._mean_fluorescence(pair[1])

            LOGGER.debug(
                "Repeat %d: I_off=%.2f, I_on=%.2f, ratio=%.6f",
                r + 1, I_off, I_on, I_on / I_off if I_off else float("nan"),
            )

            i_off_list.append(I_off)
            i_on_list.append(I_on)

            S_off = I_off - baseline
            S_on = I_on - baseline

            if S_off <= 0:
                raise RuntimeError(
                    f"Repeat {r + 1}: off-resonance signal ({I_off:.2f} counts) "
                    f"is at or below the configured camera baseline "
                    f"({baseline:.2f} counts) -- contrast cannot be computed. "
                    "Check the baseline value (Main Window > Camera Baseline) "
                    "against a fresh dark measurement, and confirm the "
                    "laser/MW state is what this point expects."
                )

            # Below this, the correction subtracts a value comparable to or
            # larger than what's left -- the corrected signal's reliability
            # is then dominated by how well the baseline itself was
            # measured, not by the real optical signal. Once per scan, not
            # per point, so a whole scan under marginal conditions logs
            # once rather than flooding the log.
            if S_off < baseline and not self._baseline_margin_warned:
                self._baseline_margin_warned = True
                LOGGER.warning(
                    "Baseline-corrected off-resonance signal (%.2f counts) is "
                    "below the baseline itself (%.2f counts) at repeat %d -- "
                    "the correction's result is only as reliable as the "
                    "baseline measurement.",
                    S_off, baseline, r + 1,
                )

            if S_on <= 0:
                LOGGER.warning(
                    "Repeat %d: on-resonance signal (%.2f counts) at or below "
                    "the camera baseline (%.2f counts) after correction; "
                    "contrast for this repeat may be negative or unreliable.",
                    r + 1, I_on, baseline,
                )

            signals.append(100.0 * S_on / S_off)

        # Raw means, deliberately NOT baseline-corrected -- unlike the
        # contrast in `signals` above. Kept raw because return_raw=True
        # callers, the live "Mean I_on / I_off" plot mode, and saved
        # metadata are meant to show what the camera actually measured.
        # This means the plotted I_on/I_off traces and the plotted
        # contrast are computed from different quantities once a baseline
        # is set -- by design, not an oversight.
        self._last_i_off = np.mean(i_off_list)
        self._last_i_on = np.mean(i_on_list)
        return np.mean(signals)

    # =====================================================
    # TRIGGERED FRAME ACQUISITION
    # =====================================================

    def acquire_triggered_frame(self, pulse, seq, repeat_index=None, frame_label=None):

        camera = self.hw["camera"]
        timing = self._timing

        t0 = time.time()

        tt0 = time.perf_counter() if timing else None
        pulse.load_sequence(seq)
        self._record_timing(repeat_index, frame_label, "load_sequence", tt0)

        tt0 = time.perf_counter() if timing else None
        pulse.reset_outputs()
        self._record_timing(repeat_index, frame_label, "reset_outputs", tt0)

        reset_delay_s = self.config.get("reset_delay_s", 0.005)
        tt0 = time.perf_counter() if timing else None
        time.sleep(reset_delay_s)
        self._record_timing(repeat_index, frame_label, "reset_delay_sleep", tt0)

        t1 = time.time()

        fire_timing = {} if timing else None

        def fire():
            fire_delay_s = self.config.get("fire_delay_s", 0.005)
            ft0 = time.perf_counter() if timing else None
            time.sleep(fire_delay_s)
            if timing:
                fire_timing["fire_delay_sleep"] = (ft0, time.perf_counter() - ft0)
            ft0 = time.perf_counter() if timing else None
            # Exactly one gate per triggered frame -- see
            # SwabianPulseStreamer.run() for why n_runs=1 is required now
            # that the camera is armed for the whole scan (a looping
            # sequence's stray repetition can otherwise fill an
            # already-armed buffer between frames).
            pulse.run(n_runs=1)
            if timing:
                fire_timing["pulse_run"] = (ft0, time.perf_counter() - ft0)

        trigger_thread = threading.Thread(target=fire, daemon=True)
        trigger_thread.start()

        t2 = time.time()

        tt0 = time.perf_counter() if timing else None
        if self._acquisition_open and hasattr(camera, "grab_external_frame"):
            # Camera already armed for the whole scan (see
            # begin_camera_acquisition) -- just wait for the next
            # trigger, no per-frame arm/disarm.
            frame = camera.grab_external_frame(timeout_ms=10000)
            self._record_timing(repeat_index, frame_label, "grab_external_frame", tt0)
        else:
            frame = camera.snap_external_trigger()
            self._record_timing(repeat_index, frame_label, "snap_external_trigger", tt0)
        snap_t_start_rel = (tt0 - self._timing_epoch) if timing else None

        t3 = time.time()

        tt0 = time.perf_counter() if timing else None
        trigger_thread.join()
        self._record_timing(repeat_index, frame_label, "trigger_thread_join", tt0)

        if timing:
            for stage, (fstart, fdur) in fire_timing.items():
                self._timing_log.append({
                    "repeat_index": repeat_index,
                    "frame": frame_label,
                    "stage": stage,
                    "t_start_rel_s": fstart - self._timing_epoch,
                    "duration_s": fdur,
                })

            self._merge_camera_log(repeat_index, frame_label, snap_t_start_rel)

        LOGGER.debug(
            "Triggered frame timings: reset=%.3fs, thread=%.3fs, "
            "camera=%.3fs, total=%.3fs",
            t1 - t0,
            t2 - t1,
            t3 - t2,
            t3 - t0,
        )

        return frame
    # =====================================================
    # SCAN EXPERIMENT HOOKS
    # =====================================================

    def set_scan_point(self, frequency):
        mw = self.hw["microwave"]
        timing = self._timing

        t0 = time.perf_counter() if timing else None
        mw.set_frequency(frequency)
        self._record_timing(None, None, "visa_set_frequency", t0)

        mw_settle_s = self.config.get("mw_settle_s", 0.0)
        time.sleep(mw_settle_s)

        power_dbm = self.config.get("mw_power_dbm", -10)

        if hasattr(mw, "set_power"):
            t0 = time.perf_counter() if timing else None
            mw.set_power(power_dbm)
            self._record_timing(None, None, "visa_set_power_initial", t0)

        if hasattr(mw, "rf_on"):
            t0 = time.perf_counter() if timing else None
            mw.rf_on()
            self._record_timing(None, None, "visa_rf_on", t0)

    def acquire_frame(self):
        """Acquire the OFF/ON camera-frame pairs for one configured point."""
        mw = self.hw["microwave"]
        pulse = self.hw["pulse_streamer"]
        timing = self._timing

        power_dbm = self.config.get("mw_power_dbm", -10)
        repeats = self.config.get("repeats", 1)
        mw_power_settle_s = self.config.get("mw_power_settle_s", 0.0)
        frames = []

        for r in range(repeats):

            repeat_t0 = time.perf_counter() if timing else None

            # ==========================================
            # MW OFF FRAME
            # ==========================================

            if hasattr(mw, "set_power"):
                t0 = time.perf_counter() if timing else None
                mw.set_power(-100)
                self._record_timing(r, "off", "visa_set_power_off", t0)

                # Settles the SG386's amplitude step before the gate opens.
                # Zero by default -- see check_mw_power_settling_margin,
                # which warns if the delays that already elapse before the
                # gate (independent of this one) fall below the spec this
                # exists to cover.
                t0 = time.perf_counter() if timing else None
                time.sleep(mw_power_settle_s)
                self._record_timing(r, "off", "mw_power_settle_sleep", t0)

            seq_off = self.build_sequence()

            off_t0 = time.perf_counter() if timing else None
            frame_off = self.acquire_triggered_frame(
                pulse,
                seq_off,
                repeat_index=r,
                frame_label="off",
            )

            # ==========================================
            # MW ON FRAME
            # ==========================================

            if hasattr(mw, "set_power"):
                t0 = time.perf_counter() if timing else None
                mw.set_power(power_dbm)
                self._record_timing(r, "on", "visa_set_power_on", t0)

                t0 = time.perf_counter() if timing else None
                time.sleep(mw_power_settle_s)
                self._record_timing(r, "on", "mw_power_settle_sleep", t0)

            seq_on = self.build_sequence()

            on_t0 = time.perf_counter() if timing else None
            frame_on = self.acquire_triggered_frame(
                pulse,
                seq_on,
                repeat_index=r,
                frame_label="on",
            )

            if timing:
                self._timing_log.append({
                    "repeat_index": r,
                    "frame": None,
                    "stage": "off_on_gap",
                    "t_start_rel_s": off_t0 - self._timing_epoch,
                    "duration_s": on_t0 - off_t0,
                })
                self._timing_log.append({
                    "repeat_index": r,
                    "frame": None,
                    "stage": "repeat_total",
                    "t_start_rel_s": repeat_t0 - self._timing_epoch,
                    "duration_s": time.perf_counter() - repeat_t0,
                })

            frames.append(np.stack((frame_off, frame_on)))

        return np.stack(frames)

    # =====================================================
    # SINGLE ODMR POINT (backward-compatible convenience API)
    # =====================================================

    def acquire_configured_point(self, frequency, return_raw=False):
        """Acquire one point with camera settings configured by the caller."""
        self.set_scan_point(frequency)
        frame = self.acquire_frame()
        signal = self.process_frame(frame)

        if return_raw:
            return signal, self._last_i_off, self._last_i_on

        return signal

    def acquire_point(self, frequency, return_raw=False):
        """Backward-compatible one-point acquisition with exclusive access."""
        with exclusive_camera_access(self.hw["camera"]):
            self.configure_acquisition()
            return self.acquire_configured_point(frequency, return_raw=return_raw)
