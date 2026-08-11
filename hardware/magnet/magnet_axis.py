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

        # Timing diagnostics (off by default; temporary instrumentation --
        # see Magnet.enable_timing_diagnostics/pop_timing_log, which cascade
        # into this). Brackets only the SCPI-write call itself
        # (power_supply.set_current()/safe_shutdown()), not the
        # field<->current calibration conversion above -- that conversion
        # is pure arithmetic with no I/O, so a row here reflects the actual
        # hardware transaction, not conversion overhead.
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

        field_mT = abs(field_mT)

        current = self.field_to_current(field_mT)

        if abs(current) > self.max_current:

            raise ValueError(
                f"{self.name}-coil exceeds maximum current."
            )

        timing = self._timing_enabled

        # -------------------------------------------------
        # Zero field
        # -------------------------------------------------

        if abs(current) < 1e-9:

            if keep_output_enabled:
                t0 = time.perf_counter() if timing else None
                self.power_supply.set_current(0.0)
                if timing:
                    self._timing_log.append(("set_current", time.perf_counter() - t0))
            else:
                t0 = time.perf_counter() if timing else None
                self.power_supply.safe_shutdown()
                if timing:
                    self._timing_log.append(("safe_shutdown", time.perf_counter() - t0))

        else:

            t0 = time.perf_counter() if timing else None
            self.power_supply.set_current(current)
            if timing:
                self._timing_log.append(("set_current", time.perf_counter() - t0))

            current = self.field_to_current(field_mT)

            t0 = time.perf_counter() if timing else None
            self.power_supply.set_current(current)
            if timing:
                self._timing_log.append(("set_current", time.perf_counter() - t0))

            self.current_field = field_mT

        self.current_field = field_mT

        self.current_current = current

    def zero(self):

        self.power_supply.safe_shutdown()

        self.current_field = 0.0
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
