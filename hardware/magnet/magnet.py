"""
=====================================================
Magnet Hardware
-----------------------------------------------------
Abstraction layer for Helmholtz coils.

Experiments should NEVER communicate directly with
power supplies.

Instead they should simply request

    magnet.z.set_field(1.5)

or

    magnet.set_vector(...)

Author:
    Amandeep + ChatGPT
=====================================================
"""

from .magnet_axis import MagnetAxis
from enum import Enum
import logging
import time

LOGGER = logging.getLogger(__name__)
# =====================================================
# MAGNET DIRECTION
# =====================================================

class MagnetDirection(Enum):
    POSITIVE = 1
    NEGATIVE = -1

# =====================================================
# Backward compatibility
# These aliases will eventually be removed once all
# modules use MagnetDirection directly.
# =====================================================

POSITIVE = MagnetDirection.POSITIVE
NEGATIVE = MagnetDirection.NEGATIVE


class Magnet:

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(
        self,
        config,
        power_supplies,
        pulse_streamer=None,
        scpi_write_delay_s=None,
    ):

        self.config = config

        self.power_supplies = power_supplies

        self.pulse_streamer = pulse_streamer

        # -------------------------------------------------
        # SCPI WRITE PACING
        # -------------------------------------------------

        # Fixed pause applied after each axis write inside set_vector()
        # that actually reaches a power supply (never after a
        # change-detection skip -- see set_vector()). This is the
        # rate-limiting backstop for the 521 (input buffer overrun) /
        # -113 (undefined header) errors seen at the rig when
        # set_vector()'s per-axis writes landed back-to-back, with no
        # flow control, across three serial links.
        #
        # 0.05 s is a starting guess, not a measured value: transmitting
        # one APPL command at the default 9600 baud takes roughly
        # 25-30 ms (see CLAUDE.md / the ZFE SCPI-overrun investigation),
        # so this budgets about that much again for the instrument to
        # actually parse and apply it. Tune at the rig the same way
        # fire_delay_s/reset_delay_s were tuned for the Pulse Streamer --
        # configurable via Helmholtz.scpi_write_delay_s in
        # config/setupInfo.json, or pass this constructor argument
        # directly (e.g. 0.0 in tests, where no real instrument is being
        # paced for).
        if scpi_write_delay_s is not None:
            self.scpi_write_delay_s = max(0.0, scpi_write_delay_s)
        else:
            self.scpi_write_delay_s = max(
                0.0, config.get("scpi_write_delay_s", 0.05)
            )

        # -------------------------------------------------
        # CURRENT DIRECTION
        # -------------------------------------------------

        # +1 = Positive
        # -1 = Negative

        self.direction = POSITIVE

        # -------------------------------------------------
        # AXES
        # -------------------------------------------------

        self.x = MagnetAxis(
            config["X"],
            power_supplies["X"]
        )

        self.y = MagnetAxis(
            config["Y"],
            power_supplies["Y"]
        )

        self.z = MagnetAxis(
            config["Z"],
            power_supplies["Z"]
        )

        # -------------------------------------------------
        # FIELD REVERSAL
        # -------------------------------------------------

        flip = config["flip_direction"]

        self.flip_available = flip["available"]

        self.flip_channel = (
            flip["switch"]["switchChannel"]
        )

        self.flip_channel_name = (
            flip["switch"]["switchChannelName"]
        )
    
    # =================================================
    # MAGNET STATE
    # =================================================

        self.enabled = False

    # =================================================
    # TIMING DIAGNOSTICS STATE
    # =================================================
    # Off by default; temporary instrumentation. See
    # enable_timing_diagnostics()/pop_timing_log() below.

        self._timing_enabled = False
        self._timing_log = []

    # =====================================================
    # TIMING DIAGNOSTICS (off by default; temporary instrumentation)
    # =====================================================

    def enable_timing_diagnostics(self, enabled):
        """Turn per-call timing of set_vector()'s sub-steps on/off.

        Cascades to each axis so MagnetAxis.set_field()'s own SCPI-write
        timing turns on/off together with this. Always clears every log
        involved, whether enabling or disabling.
        """
        self._timing_enabled = bool(enabled)
        self._timing_log = []
        for axis in (self.x, self.y, self.z):
            axis.enable_timing_diagnostics(enabled)

    def pop_timing_log(self):
        """Return and clear the accumulated (stage, duration_s) log.

        Axis-level entries (set_current/safe_shutdown) are already merged
        in by set_vector() itself, tagged as axis_<X/Y/Z>_<stage> -- see
        set_vector() for why that has to happen inline rather than here.
        """
        log = self._timing_log
        self._timing_log = []
        return log

    # =====================================================
    # ZERO MAGNET
    # =====================================================

    def zero(self):

        for axis in (self.x, self.y, self.z):

            axis.power_supply.set_current(0.0)

            axis.current_field = 0.0

            axis.current_current = 0.0

            # Bypasses set_field()'s change-detection cache entirely (this
            # writes power_supply.set_current() directly), so the cache
            # must be told the confirmed value explicitly rather than left
            # stale. Invalidate rather than cache 0.0: disable() calls
            # zero() immediately before turning outputs off, and the
            # invalidate-on-output-change rule (see MagnetAxis.
            # invalidate_cache()) already means that combination should
            # leave the cache unknown, not confirmed-at-zero.
            axis.invalidate_cache()

    # =====================================================
    # VECTOR CONTROL
    # =====================================================

    def set_vector(self, bx=0.0, by=0.0, bz=0.0,):

        values = [bx, by, bz]

        timing = self._timing_enabled

        t0 = time.perf_counter() if timing else None
        direction = self._determine_global_polarity(
            values
        )
        if timing:
            self._timing_log.append(
                ("determine_polarity", time.perf_counter() - t0)
            )

        # Read before calling set_polarity() -- this is the exact condition
        # _apply_global_polarity() itself checks (an early return when the
        # requested direction already matches) -- so the stage name below
        # reports a real flip vs. the common no-op case as a greppable tag,
        # not just an implied difference in duration.
        flip_needed = direction != self.direction

        t0 = time.perf_counter() if timing else None
        self.set_polarity(direction)
        if timing:
            stage = (
                "apply_global_polarity_flip"
                if flip_needed
                else "apply_global_polarity_noop"
            )
            self._timing_log.append((stage, time.perf_counter() - t0))

        # -------------------------------------------------
        # Set positive magnitudes
        # -------------------------------------------------

        # A vector update is commonly used inside a scan.  Zero-current axes
        # must remain enabled while the scan is active; shutdown remains the
        # responsibility of ``disable()`` or an explicit axis zero operation.
        #
        # Each axis's set_field() reports whether it actually wrote
        # anything (False on a change-detection cache hit -- see
        # MagnetAxis.set_field()). scpi_write_delay_s only ever follows a
        # real write: sleeping after a skipped one would burn wall-clock
        # time pacing a command that was never sent, undoing most of the
        # benefit change-detection exists to provide.
        wrote_x = self.x.set_field(abs(bx), keep_output_enabled=True)
        if timing:
            for stage, duration in self.x.pop_timing_log():
                self._timing_log.append((f"axis_X_{stage}", duration))
        self._pace_after_write(wrote_x, "X", timing)

        wrote_y = self.y.set_field(abs(by), keep_output_enabled=True)
        if timing:
            for stage, duration in self.y.pop_timing_log():
                self._timing_log.append((f"axis_Y_{stage}", duration))
        self._pace_after_write(wrote_y, "Y", timing)

        wrote_z = self.z.set_field(abs(bz), keep_output_enabled=True)
        if timing:
            for stage, duration in self.z.pop_timing_log():
                self._timing_log.append((f"axis_Z_{stage}", duration))
        self._pace_after_write(wrote_z, "Z", timing)

    def _pace_after_write(self, wrote, axis_name, timing):
        """Sleep scpi_write_delay_s after a real SCPI write, never after a
        change-detection skip. See set_vector()'s inter-command-delay
        comment and __init__'s scpi_write_delay_s docstring."""
        if not wrote or self.scpi_write_delay_s <= 0:
            return
        t0 = time.perf_counter() if timing else None
        time.sleep(self.scpi_write_delay_s)
        if timing:
            self._timing_log.append(
                (f"axis_{axis_name}_write_delay_sleep", time.perf_counter() - t0)
            )

    # =====================================================
    # GET VECTOR
    # =====================================================

    def get_vector(self):

       return {

            "x": self.direction.value * self.x.get_field(),

            "y": self.direction.value * self.y.get_field(),

            "z": self.direction.value * self.z.get_field(),

        }
        
    # =================================================
    # ENABLE MAGNET
    # =================================================

    def enable(self):

        self._sync_positive_polarity()

        self.x.power_supply.output_on()
        self.x.invalidate_cache()

        self.y.power_supply.output_on()
        self.y.invalidate_cache()

        self.z.power_supply.output_on()
        self.z.invalidate_cache()

        self.enabled = True

    # =================================================
    # DISABLE MAGNET
    # =================================================

    def disable(self):

        self.zero()

        self._sync_positive_polarity()

        self.x.power_supply.output_off()
        self.x.invalidate_cache()

        self.y.power_supply.output_off()
        self.y.invalidate_cache()

        self.z.power_supply.output_off()
        self.z.invalidate_cache()

        self.enabled = False

    # =================================================
    # MAGNET STATE
    # =================================================

    def is_enabled(self):

        return self.enabled

    # =====================================================
    # POWER SUPPLY ERROR QUEUES (off by default; diagnostic instrumentation)
    # =====================================================

    def check_error_queues(self, context=""):
        """Poll each axis's power supply SCPI error queue and log loudly
        if any reports a pending error.

        Deliberately NOT called per set_current()/set_vector() call --
        that would add a query/response round trip (subject to each
        SerialDevice's own read timeout) to every write on every axis,
        which is the "*OPC?-style pacing" the SCPI-overrun investigation
        concluded was the wrong tool here (see CLAUDE.md). This exists so
        the driver is no longer *blind* to the 521/-113-style errors that
        motivated it -- call it at scan boundaries only (see
        ZeroFieldExperiment's check_power_supply_errors flag, off by
        default), where the cost is paid at most twice per run instead of
        six times per point.

        Returns {axis_name: [error strings]} for any axis with a
        non-empty queue; axes with nothing pending, and any power supply
        that doesn't implement check_error_queue() at all (e.g. a driver
        with no SCPI error-queue concept), are omitted.
        """
        found = {}

        for axis in (self.x, self.y, self.z):

            power_supply = axis.power_supply

            if not hasattr(power_supply, "check_error_queue"):
                continue

            errors = power_supply.check_error_queue()

            if errors:
                found[axis.name] = errors
                LOGGER.error(
                    "%s-axis power supply reported %d pending SCPI "
                    "error(s) at %s: %s",
                    axis.name, len(errors), context or "error check", errors,
                )

        return found

    # =====================================================
    # MAGNET STATUS
    # =====================================================

    def is_connected(self):

        return (

            self.x.power_supply.connected
            and
            self.y.power_supply.connected
            and
            self.z.power_supply.connected

        )
    # =====================================================
    # DIRECTION
    # =====================================================

    def set_polarity(self, direction):

        if direction not in (POSITIVE, NEGATIVE):

            raise ValueError(
                "Polarity must be POSITIVE or NEGATIVE."
            )

        self._apply_global_polarity(direction)

    def flip_polarity(self):

        if self.direction == POSITIVE:

            self.set_polarity(NEGATIVE)

        else:

            self.set_polarity(POSITIVE)

    def get_polarity(self):

        return self.direction

    def _sync_positive_polarity(self):
        # The polarity relay is a latched hardware device whose state survives
        # application restarts. Therefore the framework explicitly synchronizes
        # the relay to POSITIVE whenever the magnet subsystem is enabled or disabled.
        if self.flip_available and self.pulse_streamer is not None:
            self.pulse_streamer.set_digital_output(
                self.flip_channel,
                False
            )
            self.pulse_streamer.sync_outputs()

        self.direction = POSITIVE

    def _determine_global_polarity(self, values):

        signs = set()

        for value in values:

            if value > 0:

                signs.add(POSITIVE)

            elif value < 0:

                signs.add(NEGATIVE)

        if len(signs) > 1:

            raise ValueError(
                "Mixed-sign magnetic fields cannot be "
                "generated because the polarity relay "
                "is global."
            )

        if NEGATIVE in signs:

            return NEGATIVE

        if POSITIVE in signs:

            return POSITIVE

        return self.direction

    # =====================================================
    # APPLY GLOBAL POLARITY
    # =====================================================

    def _apply_global_polarity(self, direction):

        if (
            direction == NEGATIVE
            and not self.flip_available
        ):

            raise RuntimeError(
                "Negative magnetic fields are not supported "
                "because the global polarity relay is unavailable."
            )

        if direction == self.direction:

            return

        if self.pulse_streamer is not None:

           self.pulse_streamer.set_digital_output(
                self.flip_channel,
                direction == NEGATIVE
            )
           self.pulse_streamer.sync_outputs()

        self.direction = direction

        # A real flip only (this line is never reached on the early-return
        # no-op above, which is the common case within one sweep -- see
        # set_vector()'s flip_needed comment). Each axis's set_field()
        # commands a magnitude via abs(), unaffected by polarity, but the
        # cache's promise is "the supply is still where set_field() last
        # left it" -- a relay operation is exactly the kind of state
        # change outside set_field() that promise can't cover without
        # hardware readback, so treat it the same as output on/off.
        #
        # getattr(..., None)-guarded: some tests exercise only the
        # polarity-relay slice of Magnet via Magnet.__new__(Magnet),
        # bypassing __init__ (and therefore self.x/y/z) entirely -- see
        # tests/test_pulse_streamer_single_shot.py's _make_bare_magnet().
        for axis in (
            getattr(self, "x", None),
            getattr(self, "y", None),
            getattr(self, "z", None),
        ):
            if axis is not None:
                axis.invalidate_cache()

    def get_direction(self):

        return self.get_polarity()


    def set_direction(self, direction):

        self.set_polarity(direction)


    def flip_direction(self):

        self.flip_polarity()
