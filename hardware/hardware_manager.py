import logging
import threading
import time

from framework.logging_setup import format_block, log_block, log_path_text

from hardware.microwave.sg386 import SG386
from hardware.microwave.sim_microwave import SimMicrowave

from hardware.camera.sim_camera import SimCamera

from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer

from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer

from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.power_supply import (
    SimPowerSupply,
    E3631A,
    E3632A,
)
from hardware.magnet import Magnet

LOGGER = logging.getLogger(__name__)

# Upper bound on releasing the Pulse Streamer at shutdown. The vendor SDK's
# JSON-RPC calls each time out only after 10 s; an unreachable device must
# not hold the app's exit longer than this.
PULSE_STREAMER_CLOSE_TIMEOUT_S = 5.0
# Same bound for the SG386 (VISA timeout 5 s per call) and the camera.
MICROWAVE_CLOSE_TIMEOUT_S = 5.0
CAMERA_CLOSE_TIMEOUT_S = 5.0


def run_bounded(function, timeout_s, label="call"):
    """Run function() on a daemon thread and wait at most *timeout_s*.

    Returns (status, error): ("ok", None), ("error", exception) -- logged
    here -- or ("timeout", None) if it was still running when the wait
    ended. A call that never returns is left behind on its daemon thread
    and does not keep the process alive.
    """
    outcome = {}

    def target():
        try:
            function()
        except Exception as error:  # reported below, on the caller's thread
            outcome["error"] = error

    thread = threading.Thread(target=target, name=f"bounded {label}", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        LOGGER.warning("%s did not finish within %.1f s; continuing", label, timeout_s)
        return "timeout", None
    if "error" in outcome:
        LOGGER.error("%s failed: %r", label, outcome["error"])
        return "error", outcome["error"]
    return "ok", None


def call_with_timeout(function, timeout_s, label="call"):
    """True if function() returned (an exception is logged, not raised),
    False if it was still running after *timeout_s* (see run_bounded())."""
    return run_bounded(function, timeout_s, label)[0] != "timeout"


def short_reason(error, limit=70):
    """A one-line reason for a summary line (full detail is in the log)."""
    if error is None:
        return "unknown error"
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sg386_identity(idn):
    """"s/n 002306, fw 1.23.26" from "Stanford ...,SG386,s/n002306,ver1.23.26"."""
    parts = [part.strip() for part in (idn or "").split(",")]
    serial = next((p[3:].strip() for p in parts if p.lower().startswith("s/n")), None)
    version = next((p[3:].strip() for p in parts if p.lower().startswith("ver")), None)
    found = [text for text in (serial and f"s/n {serial}", version and f"fw {version}") if text]
    return ", ".join(found) if found else (idn or "identity unknown")


class HardwareManager:

    def __init__(self, config_manager, force_sim=False):

        self.cfg = config_manager

        self.hardware = {}

        # When True, every device type is overridden to its simulated
        # class regardless of config/setupInfo.json, without editing that
        # file. Selected via the app's --sim launch flag.
        self.force_sim = force_sim

    # =====================================================
    # CREATE POWER SUPPLY
    # =====================================================

    def create_power_supply(self, config):

        ps_type = config["type"].upper()

        # ---------------------------------------------
        # Simulated Power Supply
        # ---------------------------------------------

        if ps_type == "SIM":

            return SimPowerSupply(config)

        # ---------------------------------------------
        # Keysight E3631A
        # ---------------------------------------------

        elif ps_type == "E3631A":

            return E3631A(config)

        # ---------------------------------------------
        # Keysight E3632A
        # ---------------------------------------------

        elif ps_type == "E3632A":

            return E3632A(config)

        else:

            raise ValueError(
                f"Unknown power supply type: {ps_type}"
            )
    # =====================================================
    # INITIALIZE ALL HARDWARE
    # =====================================================
    
    def initialize(self):
        """Connect every device (same order as always), then log the
        startup summary. A device that fails stops startup as before; the
        summary then shows it FAILED and the later devices not started."""
        self._init_step = None
        self._startup_rf_off = None
        error = None
        try:
            self._initialize_devices()
        except Exception as exc:
            error = exc
            LOGGER.exception("Hardware initialization failed at %s", self._init_step)
            raise
        finally:
            try:
                self.log_startup_summary(error)
            except Exception:
                LOGGER.exception("Could not write the startup summary")

    def _initialize_devices(self):
        # =================================================
        # CHANNEL MAP
        # =================================================

        ps_cfg = self.cfg.get("pulseGenerator")

        channel_names = ps_cfg["channelNames"]

        channel_values = ps_cfg["channelValues"]

        self.hardware["channels"] = dict(
            zip(channel_names, channel_values)
        )

        LOGGER.debug("Channel map: %s", self.hardware["channels"])

        # =================================================
        # MICROWAVE SOURCE
        # =================================================

        self._init_step = "SG386"
        fg = self.cfg.get(
            "frequencyGenerators"
        )[0]

        fg_type = "sim" if self.force_sim else fg["type"].lower()

        # -------------------------------------------------
        # SRS SG386
        # -------------------------------------------------

        if fg_type == "srs":

            address = fg["address"]

            port = fg.get("port", None)

            mw = SG386(address, port)

            mw.connect()

            self.hardware["microwave"] = mw

        # -------------------------------------------------
        # Simulated microwave source
        # -------------------------------------------------

        elif fg_type == "sim":

            mw = SimMicrowave(
                fg.get("address"),
                fg.get("port"),
            )

            mw.connect()

            self.hardware["microwave"] = mw

        else:

            raise ValueError(
                f"Unknown microwave type: {fg_type}"
            )

        # RF output off from the start: it is on only during ODMR scans
        # (CLAUDE.md). A failure is a warning, never a startup blocker.
        self._startup_rf_off = self.switch_rf_off_at_startup()

        # =================================================
        # CAMERA
        # =================================================

        self._init_step = "Camera"
        camera_cfg = self.cfg.get("spcm")

        camera_type = "sim" if self.force_sim else camera_cfg["type"].lower()

        if camera_type == "andor":

            camera = AndorNeoAndor3()

            camera.connect()

        elif camera_type == "sim":

            camera = SimCamera(
                microwave=self.hardware.get("microwave")
            )

            camera.connect()

        else:

            raise ValueError(
                f"Unknown camera type: {camera_cfg['type']}"
            )

        self.hardware["camera"] = camera

        # =================================================
        # PULSE STREAMER
        # =================================================

        self._init_step = "Pulse Streamer"
        ps_cfg = self.cfg.get("pulseGenerator")

        pulse_gen_type = "sim" if self.force_sim else ps_cfg["type"].lower()

        if pulse_gen_type == "pulsestreamer":

            ip = ps_cfg["ipAddress"]

            pulse = SwabianPulseStreamer(
                ip_address=ip,
                channel_map=self.hardware["channels"],
            )
            pulse.connect()

            self.hardware["pulse_streamer"] = pulse

        elif pulse_gen_type == "sim":

            pulse = SimPulseStreamer(
                ip_address=ps_cfg.get("ipAddress"),
                channel_map=self.hardware["channels"],
            )
            pulse.connect()

            self.hardware["pulse_streamer"] = pulse

            # Lets SimCamera.odmr_contrast() read MW on/off from the
            # loaded sequence's channel-2 content instead of the source's
            # own power -- wired explicitly here (not discovered by
            # either object) because the camera block above runs before
            # this one, so it can't be a constructor argument without
            # reordering real-hardware construction too.
            if camera_type == "sim":
                camera.pulse_streamer = pulse

        else:
            raise ValueError(
                f"Unknown pulse generator type: {ps_cfg['type']}"
            )
        # =================================================
        # POWER SUPPLIES
        # =================================================

        helmholtz_cfg = self.cfg.get("Helmholtz")

        power_supplies = {}

        for axis in ["X", "Y", "Z"]:

            self._init_step = f"Power supply {axis}"
            axis_cfg = helmholtz_cfg[axis]

            if self.force_sim:
                # Shallow copy: override the type without mutating the
                # loaded config dict that self.cfg still holds.
                axis_cfg = dict(axis_cfg)
                axis_cfg["type"] = "SIM"

            ps = self.create_power_supply(
                axis_cfg
            )

            ps.connect()

            power_supplies[axis] = ps
            self.hardware["power_supplies"] = power_supplies

        self.hardware["power_supplies"] = power_supplies
        
        # =================================================
        # MAGNET
        # =================================================

        self._init_step = "Magnet"
        magnet = Magnet(

            config=helmholtz_cfg,

            power_supplies=power_supplies,

            pulse_streamer=self.hardware["pulse_streamer"]

        )

        self.hardware["magnet"] = magnet
        self._init_step = None
        LOGGER.debug("Available hardware: %s", ", ".join(self.hardware))
    # =====================================================
    # GET HARDWARE
    # =====================================================

    def get_hardware(self):

        return self.hardware
    # =====================================================
    # SHUTDOWN
    # =====================================================

    def switch_rf_off_at_startup(self):
        """Switch the connected SG386's RF output off and log it. Returns
        True on success; a failure is logged as a warning only."""
        mw = self.hardware.get("microwave")
        if mw is None:
            return False
        try:
            mw.rf_off()
        except Exception as error:
            LOGGER.warning("Could not switch the SG386 RF output off at startup: %r", error)
            return False
        # INFO, but shown in the startup summary rather than on its own line.
        LOGGER.debug("SG386 RF output switched off at startup")
        return True

    # =====================================================
    # STARTUP SUMMARY
    # =====================================================

    STARTUP_ORDER = ("SG386", "Camera", "Pulse Streamer", "Power supply X",
                     "Power supply Y", "Power supply Z", "Magnet")

    def _startup_device(self, label):
        if label == "SG386":
            return self.hardware.get("microwave")
        if label == "Camera":
            return self.hardware.get("camera")
        if label == "Pulse Streamer":
            return self.hardware.get("pulse_streamer")
        if label.startswith("Power supply"):
            return self.hardware.get("power_supplies", {}).get(label[-1])
        return self.hardware.get("magnet")

    def _startup_detail(self, label, device):
        """(state, detail) of a connected device for the startup summary."""
        simulated = isinstance(
            device, (SimMicrowave, SimCamera, SimPulseStreamer, SimPowerSupply)
        ) or (label == "Magnet" and self.force_sim)
        state = "SIM" if simulated else "OK"
        if label == "SG386":
            identity = "simulated" if simulated else _sg386_identity(getattr(device, "idn", None))
            if self._startup_rf_off:
                return state, f"{identity}, RF off"
            return "WARN", f"{identity}, RF off failed (see log)"
        if label == "Camera":
            return state, "simulated" if simulated else "Andor Neo (andor3)"
        if label == "Pulse Streamer":
            if simulated:
                return state, "simulated"
            detail = f"{getattr(device, 'ip_address', '?')}"
            firmware = getattr(device, "firmware_version", None)
            client = getattr(device, "client_version", None)
            note = getattr(device, "version_note", None)
            if firmware:
                detail += f", fw v{firmware}"
            extra = "; ".join(t for t in (client and f"client v{client}", note) if t)
            return state, detail + (f" ({extra})" if extra else "")
        if label.startswith("Power supply"):
            return state, "simulated" if simulated else type(device).__name__
        relays = ", ".join(
            f"{axis.name} ch{axis.flip_channel}" for axis in getattr(device, "axes", ()))
        return state, f"relays {relays}" if relays else ""

    def startup_rows(self, error=None):
        rows = []
        failed = self._init_step if error is not None else None
        for label in self.STARTUP_ORDER:
            device = self._startup_device(label)
            if device is not None:
                rows.append((label, *self._startup_detail(label, device)))
            elif label == failed:
                rows.append((label, "FAILED", f"{short_reason(error)} (see log)"))
            else:
                rows.append((label, "-", "not started" if failed else "not configured"))
        channels = self.hardware.get("channels") or {}
        named = [(name, channels[key]) for key, name in
                 (("greenLaser", "laser"), ("MW", "MW"), ("detector", "detector"))
                 if key in channels]
        if named:
            rows.append(("Channels", "", ", ".join(f"{n} {c}" for n, c in named)))
        return rows

    def startup_title(self):
        config = getattr(self.cfg, "config_path", None) or "?"
        mode = "  [--sim: simulated hardware]" if self.force_sim else ""
        return f"NV Control{mode}  (config: {config}, log: {log_path_text()})"

    def log_startup_summary(self, error=None):
        rows = self.startup_rows(error)
        problem = any(state in ("FAILED", "WARN") for _l, state, _d in rows)
        log_block(format_block(self.startup_title(), rows),
                  logging.WARNING if problem else logging.INFO)

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def _release_device(self, name, before_close, timeout_s):
        """Run *before_close* methods, then the driver's close() (or
        disconnect() if it has no close()), on a bounded daemon thread.
        Every failure is logged, and a step failing does not skip the next
        (an RF-off error still closes the connection). Returns a dict:
        closed (bool), status ("ok"/"error"/"timeout"), problems (list of
        short reasons), rf ("read back"/"commanded"/... for the SG386)."""
        device = self.hardware.get(name)
        report = {"closed": False, "status": "ok", "problems": [], "rf": None}
        if device is None:
            return report
        closer = next((m for m in ("close", "disconnect")
                       if callable(getattr(device, m, None))), None)

        def release():
            for method in before_close:
                function = getattr(device, method, None)
                if not callable(function):
                    continue
                try:
                    function()
                except Exception as error:
                    LOGGER.exception("%s %s() failed at shutdown", name, method)
                    report["problems"].append(f"{method}() failed: {short_reason(error, 40)}")
                    continue
                if method == "rf_off":
                    report["rf"] = "commanded"
                    read_back = getattr(device, "is_rf_on", None)
                    if callable(read_back):
                        try:
                            if read_back():
                                LOGGER.error("%s RF output still reads ON after rf_off()", name)
                                report["problems"].append("RF still ON (read back)")
                                report["rf"] = None
                            else:
                                report["rf"] = "read back"
                        except Exception:
                            LOGGER.exception("%s RF read-back failed at shutdown", name)
                            report["rf"] = "not read back"
            if closer is None:
                LOGGER.error("%s (%s) has no close() or disconnect(); not closed",
                             name, type(device).__name__)
                report["problems"].append("no close() or disconnect()")
                return
            try:
                getattr(device, closer)()
                report["closed"] = True
            except Exception as error:
                LOGGER.exception("%s %s() failed at shutdown", name, closer)
                report["problems"].append(f"{closer}() failed: {short_reason(error, 40)}")

        report["status"], _error = run_bounded(release, timeout_s, f"{name} release")
        return report

    @staticmethod
    def _release_row(label, report, ok_detail, timeout_s):
        if report["status"] == "timeout":
            return label, "TIMEOUT", f"no answer within {timeout_s:g} s (see log)"
        if report["problems"]:
            return label, "FAILED", "; ".join(report["problems"])
        return label, "OK", ok_detail

    def shutdown(self):
        """Release every device. Safe to call more than once.

        Order: magnet (0 A, outputs off) -> power supplies -> Pulse
        Streamer -> microwave (RF off, then close) -> camera (stop and
        close). Microwave, camera and Pulse Streamer are each bounded to 5 s
        and every failure is logged. Each step is isolated, so one
        device failing never skips the others -- except that the Pulse
        Streamer's outputs (which include the three magnet polarity relays)
        are not touched if switching the magnet off failed, since the coils
        may then still carry current (see CLAUDE.md: coil current must be
        zero before closing the Pulse Streamer).

        Ends with one shutdown summary (log_shutdown_summary()); the steps
        themselves only log details.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        started = time.monotonic()
        rows = []
        LOGGER.debug("Shutting down hardware...")

        # --------------------------------------------
        # Magnet
        # --------------------------------------------

        magnet_safe = True
        if "magnet" in self.hardware:
            try:
                self.hardware["magnet"].disable()
                rows.append(("Magnet", "OK", "coils 0 A, outputs off"))
            except Exception as error:
                magnet_safe = False
                LOGGER.exception("Magnet shutdown failed")
                rows.append(("Magnet", "FAILED", short_reason(error)))

        # --------------------------------------------
        # Power supplies
        # --------------------------------------------

        for axis, ps in self.hardware.get("power_supplies", {}).items():
            label = f"Power supply {axis}"
            try:
                ps.disconnect()
                rows.append((label, "OK", "disconnected"))
            except Exception as error:
                LOGGER.exception("Power supply disconnect failed")
                rows.append((label, "FAILED", short_reason(error)))

        # --------------------------------------------
        # Pulse Streamer: stop any sequence, outputs LOW, release it.
        # Bounded, so an unreachable device can't hang the exit.
        # --------------------------------------------

        pulse = self.hardware.get("pulse_streamer")
        if pulse is not None:
            if magnet_safe:
                status, error = run_bounded(
                    pulse.close, PULSE_STREAMER_CLOSE_TIMEOUT_S,
                    "Pulse Streamer close",
                )
                problems = list(getattr(pulse, "close_problems", None) or [])
                if status == "timeout":
                    rows.append(("Pulse Streamer", "TIMEOUT",
                                 f"no answer within {PULSE_STREAMER_CLOSE_TIMEOUT_S:g} s (see log)"))
                elif status == "error" or problems:
                    rows.append(("Pulse Streamer", "FAILED",
                                 "; ".join(problems) or short_reason(error)))
                else:
                    rows.append(("Pulse Streamer", "OK",
                                 "outputs LOW, sequence stopped, closed"))
            else:
                LOGGER.warning(
                    "Pulse Streamer outputs left as they are: the magnet "
                    "could not be switched off, so the polarity relays are "
                    "not switched under possible coil current."
                )
                rows.append(("Pulse Streamer", "SKIPPED",
                             "left as is: magnet not off, relays not switched"))

        # --------------------------------------------
        # Microwave (SG386): RF output off, then close the connection.
        # Camera: stop acquisition and close (the driver's close() does
        # both). Each bounded and independent of the other devices.
        # --------------------------------------------

        if self.hardware.get("microwave") is not None:
            report = self._release_device(
                "microwave", ("rf_off",), MICROWAVE_CLOSE_TIMEOUT_S)
            rf = report["rf"] or "commanded"
            rows.append(self._release_row(
                "SG386", report, f"RF off ({rf}), closed", MICROWAVE_CLOSE_TIMEOUT_S))
        if self.hardware.get("camera") is not None:
            report = self._release_device("camera", (), CAMERA_CLOSE_TIMEOUT_S)
            rows.append(self._release_row(
                "Camera", report, "acquisition stopped, closed", CAMERA_CLOSE_TIMEOUT_S))

        self.shutdown_rows = rows
        self.log_shutdown_summary(rows, time.monotonic() - started)

    @staticmethod
    def shutdown_footer(elapsed_s):
        return f"Shutdown complete in {elapsed_s:.1f} s   (log: {log_path_text()})"

    def log_shutdown_summary(self, rows, elapsed_s):
        problem = any(state not in ("OK", "SIM") for _l, state, _d in rows)
        log_block(format_block("NV Control shutdown", rows, self.shutdown_footer(elapsed_s)),
                  logging.WARNING if problem else logging.INFO)
