import logging
import threading

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


def call_with_timeout(function, timeout_s, label="call"):
    """Run function() on a daemon thread and wait at most *timeout_s*.

    True if it returned (an exception is logged, not raised), False if it
    was still running when the wait ended. A call that never returns is
    left behind on its daemon thread and does not keep the process alive.
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
        print(f"[HardwareManager] {label} did not finish within {timeout_s:g} s; continuing.")
        return False
    if "error" in outcome:
        LOGGER.error("%s failed: %r", label, outcome["error"])
        print(f"[HardwareManager] {label} failed: {outcome['error']!r}")
    return True


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
        # =================================================
        # CHANNEL MAP
        # =================================================

        ps_cfg = self.cfg.get("pulseGenerator")

        channel_names = ps_cfg["channelNames"]

        channel_values = ps_cfg["channelValues"]

        self.hardware["channels"] = dict(
            zip(channel_names, channel_values)
        )

        print("\nLoaded channel map:\n")

        for k, v in self.hardware["channels"].items():

            print(f"{k} -> {v}")

        # =================================================
        # MICROWAVE SOURCE
        # =================================================

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
        self.switch_rf_off_at_startup()

        # =================================================
        # CAMERA
        # =================================================

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
        
        # =================================================
        # MAGNET
        # =================================================

        magnet = Magnet(

            config=helmholtz_cfg,

            power_supplies=power_supplies,

            pulse_streamer=self.hardware["pulse_streamer"]

        )

        self.hardware["magnet"] = magnet
        # =================================================
        # HARDWARE SUMMARY (TEMPORARY)
        # =================================================

        print("\nAvailable hardware:\n")

        for name in self.hardware:

            print(f"  {name}")
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
            print(f"[HardwareManager] WARNING: SG386 RF off at startup failed: {error!r}")
            return False
        LOGGER.info("SG386 RF output switched off at startup")
        print("[HardwareManager] SG386 RF output switched off at startup.")
        return True

    def _release_device(self, name, before_close, timeout_s):
        """Run *before_close* methods, then the driver's close() (or
        disconnect() if it has no close()), on a bounded daemon thread.
        Every failure is logged, and a step failing does not skip the next
        (an RF-off error still closes the connection). Returns True if the
        device was closed."""
        device = self.hardware.get(name)
        if device is None:
            return False
        closer = next((m for m in ("close", "disconnect")
                       if callable(getattr(device, m, None))), None)
        outcome = {"closed": False}

        def release():
            for method in before_close:
                function = getattr(device, method, None)
                if not callable(function):
                    continue
                try:
                    function()
                except Exception as error:
                    LOGGER.exception("%s %s() failed at shutdown", name, method)
                    print(f"[HardwareManager] {name} {method}() failed: {error!r}")
            if closer is None:
                LOGGER.error("%s (%s) has no close() or disconnect(); not closed",
                             name, type(device).__name__)
                print(f"[HardwareManager] {name} has no close()/disconnect(); not closed.")
                return
            try:
                getattr(device, closer)()
                outcome["closed"] = True
            except Exception as error:
                LOGGER.exception("%s %s() failed at shutdown", name, closer)
                print(f"[HardwareManager] {name} {closer}() failed: {error!r}")

        call_with_timeout(release, timeout_s, f"{name} release")
        return outcome["closed"]

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
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        print("\nShutting down hardware...")

        # --------------------------------------------
        # Magnet
        # --------------------------------------------

        magnet_safe = True
        if "magnet" in self.hardware:
            try:
                self.hardware["magnet"].disable()
            except Exception as error:
                magnet_safe = False
                LOGGER.exception("Magnet shutdown failed")
                print(f"[HardwareManager] magnet shutdown failed: {error!r}")

        # --------------------------------------------
        # Power supplies
        # --------------------------------------------

        for ps in self.hardware.get("power_supplies", {}).values():
            try:
                ps.disconnect()
            except Exception as error:
                LOGGER.exception("Power supply disconnect failed")
                print(f"[HardwareManager] power supply disconnect failed: {error!r}")

        # --------------------------------------------
        # Pulse Streamer: stop any sequence, outputs LOW, release it.
        # Bounded, so an unreachable device can't hang the exit.
        # --------------------------------------------

        pulse = self.hardware.get("pulse_streamer")
        if pulse is not None:
            if magnet_safe:
                call_with_timeout(
                    pulse.close, PULSE_STREAMER_CLOSE_TIMEOUT_S,
                    "Pulse Streamer close",
                )
            else:
                LOGGER.warning(
                    "Pulse Streamer outputs left as they are: the magnet "
                    "could not be switched off, so the polarity relays are "
                    "not switched under possible coil current."
                )
                print("[HardwareManager] Pulse Streamer left as is "
                      "(magnet shutdown failed; relays not switched).")

        # --------------------------------------------
        # Microwave (SG386): RF output off, then close the connection.
        # Camera: stop acquisition and close (the driver's close() does
        # both). Each bounded and independent of the other devices.
        # --------------------------------------------

        self._release_device(
            "microwave", ("rf_off",), MICROWAVE_CLOSE_TIMEOUT_S)
        self._release_device("camera", (), CAMERA_CLOSE_TIMEOUT_S)

        print("Hardware shutdown complete.")
