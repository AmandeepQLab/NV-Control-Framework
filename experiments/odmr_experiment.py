import numpy as np
import time
import threading

from framework.scan_experiment import ScanExperiment
from framework.fluorescence import mean_fluorescence
from sequencing.pulse_sequence import PulseSequence


class ODMRExperiment(ScanExperiment):

    scan_axis_name = "frequency"
    scan_axis_unit = "Hz"

    def __init__(self, hardware, config):

        super().__init__(hardware, config)

        if all(key in config for key in ("f_start", "f_stop", "steps")):
            self.scan_vector = np.linspace(
                config["f_start"],
                config["f_stop"],
                config["steps"],
            )

    # =====================================================
    # SETUP
    # =====================================================

    def setup(self):

        super().setup()

    def setup_scan(self):
        self.image_cube.scan_axis_name = self.scan_axis_name
        self.image_cube.scan_axis_unit = self.scan_axis_unit
        self.image_cube.scan_axis_values = self.scan_vector

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

        frame_gap_s = self.config.get("frame_gap_s", 0.05)
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

        print(f"multi-frame acquisition: {nframes} frames in {t1 - t0:.3f}s")

        return frames
    # =====================================================
    # FRAME PROCESSING
    # =====================================================

    def _mean_fluorescence(self, frame):
        """Return mean fluorescence per pixel for a frame or selected ROI."""
        return mean_fluorescence(frame, self.config.get("roi"))

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

        for r, pair in enumerate(pairs):
            I_off = self._mean_fluorescence(pair[0])
            I_on = self._mean_fluorescence(pair[1])

            print(
                f"Repeat {r+1}: "
                f"I_off={I_off:.2f}, "
                f"I_on={I_on:.2f}, "
                f"ratio={I_on/I_off:.6f}"
            )

            i_off_list.append(I_off)
            i_on_list.append(I_on)

            if I_on != 0:
                signals.append(100.0 * I_on / I_off)
            else:
                signals.append(0)

        self._last_i_off = np.mean(i_off_list)
        self._last_i_on = np.mean(i_on_list)
        return np.mean(signals)

    # =====================================================
    # TRIGGERED FRAME ACQUISITION
    # =====================================================

    def acquire_triggered_frame(self, pulse, seq):

        camera = self.hw["camera"]

        pulse.load_sequence(seq)

        t0 = time.time()

        pulse.reset_outputs()
        time.sleep(0.005)

        t1 = time.time()

        def fire():
            time.sleep(0.005)
            pulse.run()

        trigger_thread = threading.Thread(target=fire, daemon=True)
        trigger_thread.start()

        t2 = time.time()

        frame = camera.snap_external_trigger()

        t3 = time.time()

        trigger_thread.join()

        print(
            f"reset={t1 - t0:.3f}s, "
            f"thread={t2 - t1:.3f}s, "
            f"camera={t3 - t2:.3f}s, "
            f"total={t3 - t0:.3f}s"
        )

        return frame
    # =====================================================
    # SCAN EXPERIMENT HOOKS
    # =====================================================

    def set_scan_point(self, frequency):
        mw = self.hw["microwave"]

        mw.set_frequency(frequency)

        mw_settle_s = self.config.get("mw_settle_s", 0.0)
        time.sleep(mw_settle_s)

        power_dbm = self.config.get("mw_power_dbm", -10)

        if hasattr(mw, "set_power"):
            mw.set_power(power_dbm)

        if hasattr(mw, "rf_on"):
            mw.rf_on()

    def acquire_frame(self):
        """Acquire the OFF/ON camera-frame pairs for one configured point."""
        mw = self.hw["microwave"]
        pulse = self.hw["pulse_streamer"]

        power_dbm = self.config.get("mw_power_dbm", -10)
        repeats = self.config.get("repeats", 1)
        frames = []

        for r in range(repeats):

            # ==========================================
            # MW OFF FRAME
            # ==========================================

            if hasattr(mw, "set_power"):
                mw.set_power(-100)

            seq_off = self.build_sequence()

            frame_off = self.acquire_triggered_frame(
                pulse,
                seq_off
            )

            # ==========================================
            # MW ON FRAME
            # ==========================================

            if hasattr(mw, "set_power"):
                mw.set_power(power_dbm)

            seq_on = self.build_sequence()

            frame_on = self.acquire_triggered_frame(
                pulse,
                seq_on
            )

            frames.append(np.stack((frame_off, frame_on)))

        return np.stack(frames)

    # =====================================================
    # SINGLE ODMR POINT (backward-compatible convenience API)
    # =====================================================

    def acquire_point(self, frequency, return_raw=False):

        self.set_scan_point(frequency)
        frame = self.acquire_frame()
        signal = self.process_frame(frame)

        if return_raw:
            return signal, self._last_i_off, self._last_i_on

        return signal
