"""
=====================================================
Magnet Axis
-----------------------------------------------------
Represents a single Helmholtz coil axis.

Responsible for converting magnetic field to current
using calibration parameters and communicating with
the associated power supply.

Author:
    Amandeep + ChatGPT
=====================================================
"""

import time


class MagnetAxis:

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(
        self,
        config,
        power_supply,
    ):

        self.config = config
        self.power_supply = power_supply

        self.name = config["name"]

        self.max_current = config["max_current"]

        self.field_ratio = config["magnetic_field_ratio"]

        self.field_offset = config["magnetic_field_offset"]

        self.current_field = 0.0
        self.current_current = 0.0

        # Change-detection cache: the current this axis's power supply was
        # last CONFIRMED to hold -- i.e. the value passed to a
        # power_supply.set_current() call that returned without raising.
        # None means "unknown" and forces the next set_field() call to
        # write unconditionally, regardless of the requested value.
        #
        # This must reflect what was actually written, not what was
        # requested: setting it *before* the call would make a dropped or
        # failed write permanently invisible, since every later identical
        # request would then be wrongly skipped forever. Placing the
        # assignment on the line immediately after the power_supply call
        # (see set_field() below) gets this for free from ordinary Python
        # control flow -- if set_current() raises, the assignment simply
        # never executes and the cache is left exactly as it was.
        #
        # Note this only guards against a write that raises a Python
        # exception (e.g. a transport-level failure). It cannot detect a
        # SCPI-level error queued on the instrument itself (e.g. the 521
        # input-buffer-overrun / -113 undefined-header errors that
        # motivated this cache) -- pyserial's write() does not raise for
        # those, and checking would require reading the instrument's error
        # queue after every write, which is exactly the per-write
        # round-trip cost Magnet.check_error_queues() is deliberately kept
        # out of the hot path to avoid (see its docstring). The scan-
        # boundary error-queue check is the intended backstop for that
        # residual gap, not this cache.
        #
        # Invalidated (set back to None) by anything that changes the
        # supply's state other than a successful set_field() call --
        # output on/off, enable()/disable(), or a global polarity flip --
        # via invalidate_cache(), called from Magnet. See Magnet.enable(),
        # Magnet.disable(), Magnet.zero(), and
        # Magnet._apply_global_polarity().
        self._last_confirmed_current = None

        # Timing diagnostics (off by default; temporary instrumentation --
        # see Magnet.enable_timing_diagnostics/pop_timing_log, which cascade
        # into this). Brackets only the SCPI-write call itself
        # (power_supply.set_current()/safe_shutdown()), not the
        # field<->current calibration conversion above -- that conversion
        # is pure arithmetic with no I/O, so a row here reflects the actual
        # hardware transaction, not conversion overhead. A skipped write
        # (change-detection cache hit) records no row at all -- there is no
        # hardware transaction to time.
        self._timing_enabled = False
        self._timing_log = []

    # =====================================================
    # CONVERSION
    # =====================================================

    def field_to_current(
        self,
        field_mT,
    ):

        return (

            field_mT
            - self.field_offset

        ) / self.field_ratio

    def current_to_field(
        self,
        current,
    ):

        return (

            self.field_ratio
            * current

            + self.field_offset

        )

    # =====================================================
    # TIMING DIAGNOSTICS (off by default; temporary instrumentation)
    # =====================================================

    def enable_timing_diagnostics(self, enabled):
        """Turn per-call SCPI-write timing on/off. Always clears the log."""
        self._timing_enabled = bool(enabled)
        self._timing_log = []

    def pop_timing_log(self):
        """Return and clear the accumulated (stage, duration_s) log."""
        log = self._timing_log
        self._timing_log = []
        return log

    # =====================================================
    # CONTROL
    # =====================================================

    def set_field(self, field_mT, keep_output_enabled=False):
        """Command this axis's power supply to field_mT (sign ignored --
        polarity is Magnet's responsibility, not this method's).

        Returns True if a power_supply call was actually made, False if a
        change-detection cache hit skipped it entirely -- callers that
        need to pace real writes (see Magnet.set_vector()'s inter-command
        delay) use this to avoid sleeping after a no-op.
        """

        field_mT = abs(field_mT)

        current = self.field_to_current(field_mT)

        if abs(current) > self.max_current:

            raise ValueError(
                f"{self.name}-coil exceeds maximum current."
            )

        timing = self._timing_enabled
        wrote = False

        # -------------------------------------------------
        # Zero field
        # -------------------------------------------------

        if abs(current) < 1e-9:

            if keep_output_enabled:
                if not self._cache_matches(0.0):
                    t0 = time.perf_counter() if timing else None
                    self.power_supply.set_current(0.0)
                    # Only reached if the line above didn't raise -- see
                    # _last_confirmed_current's docstring in __init__.
                    self._last_confirmed_current = 0.0
                    wrote = True
                    if timing:
                        self._timing_log.append(("set_current", time.perf_counter() - t0))
            else:
                # safe_shutdown() also turns the output off -- a state
                # change beyond "what current is set", so this always
                # invalidates rather than caching 0.0 (matches
                # invalidate_cache()'s other callers: anything that
                # touches output on/off leaves the cache unknown).
                t0 = time.perf_counter() if timing else None
                self.power_supply.safe_shutdown()
                self._last_confirmed_current = None
                wrote = True
                if timing:
                    self._timing_log.append(("safe_shutdown", time.perf_counter() - t0))

        else:

            if not self._cache_matches(current):
                t0 = time.perf_counter() if timing else None
                self.power_supply.set_current(current)
                # Only reached if the line above didn't raise. No
                # recompute-and-resend here -- field_to_current(field_mT)
                # is a pure function of two values set_field() never
                # mutates, so a second call would be guaranteed to
                # compute and send the exact same current again. That
                # redundant second write (previously issued
                # unconditionally here) is exactly the kind of traffic
                # that overran the E363x input buffer under ZFE's
                # automated per-point sweep -- see CLAUDE.md / the
                # ZFE SCPI-overrun investigation.
                self._last_confirmed_current = current
                wrote = True
                if timing:
                    self._timing_log.append(("set_current", time.perf_counter() - t0))

        self.current_field = field_mT

        self.current_current = current

        return wrote

    def _cache_matches(self, current):
        """True if power_supply is already confirmed to hold `current`.

        False (never skip) when the cache is unknown -- either never
        written, or invalidated by invalidate_cache() -- so a lost
        command is never permanently masked by treating "unknown" as "no
        need to write".
        """
        return (
            self._last_confirmed_current is not None
            and abs(self._last_confirmed_current - current) < 1e-9
        )

    def invalidate_cache(self):
        """Forget the last confirmed commanded current.

        Call this whenever something other than a successful set_field()
        call changes what the power supply is actually doing -- output
        on/off, enable()/disable(), or a global polarity flip -- so the
        next set_field() call writes unconditionally instead of trusting
        a value the cache can no longer vouch for. See Magnet.enable(),
        Magnet.disable(), Magnet.zero(), and Magnet._apply_global_polarity().
        """
        self._last_confirmed_current = None

    def zero(self):

        self.power_supply.safe_shutdown()

        self.current_field = 0.0

        self.invalidate_cache()
    # =====================================================
    # GETTERS
    # =====================================================

    def get_field(self):

        return self.current_field

    def get_current(self):

        return self.current_current

    def get_ratio(self):

        return self.field_ratio

    def get_offset(self):

        return self.field_offset

    def get_max_current(self):

        return self.max_current

    # =====================================================
    # POWER SUPPLY
    # =====================================================

    def get_supply_type(self):

        return self.config.get(
            "type",
            "--"
        )

    def get_supply_address(self):

        return self.config.get(
            "address",
            "--"
        )

    def get_supply_range(self):

        return self.config.get(
            "range",
            self.config.get(
                "channel",
                "--"
            )
        )
