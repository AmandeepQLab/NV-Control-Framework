"""One calibrated Helmholtz axis, its supply, and its polarity relay.

Fields are commanded/model values in mT, never magnetic measurements.
Each relay branch has its own measured zero-current offset and positive
current ratio. The relay selects the branch; current remains non-negative.
"""
from enum import Enum
import math
import time


class MagnetDirection(Enum):
    POSITIVE = 1
    NEGATIVE = -1


POSITIVE = MagnetDirection.POSITIVE
NEGATIVE = MagnetDirection.NEGATIVE
FIELD_ZERO_TOLERANCE_MT = 1e-9
CURRENT_TOLERANCE_A = 1e-9


class MagnetAxis:
    def __init__(self, config, power_supply, pulse_streamer=None,
                 scpi_write_delay_s=0.0):
        self.config = config
        self.power_supply = power_supply
        self.pulse_streamer = pulse_streamer
        self.name = config["name"]
        self.max_current = float(config["max_current"])
        calibration = config["magnetic_field_calibration"]
        self.calibration = {}
        for branch in ("positive", "negative"):
            branch_config = calibration[branch]
            ratio = float(branch_config["magnetic_field_ratio"])
            offset = float(branch_config["magnetic_field_offset"])
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError(f"{self.name}: {branch} magnetic_field_ratio must be positive and finite")
            if not math.isfinite(offset):
                raise ValueError(f"{self.name}: {branch} magnetic_field_offset must be finite")
            self.calibration[branch] = {
                "magnetic_field_ratio": ratio,
                "magnetic_field_offset": offset,
            }
        if not math.isfinite(self.max_current) or self.max_current < 0:
            raise ValueError(f"{self.name}: max_current must be non-negative and finite")

        flip = config.get("flip_direction", {})
        self.flip_available = bool(flip.get("available", False))
        switch = flip.get("switch", {})
        self.flip_channel = switch.get("switchChannel")
        self.flip_channel_name = switch.get("switchChannelName")
        # Verified rig convention: HIGH reverses the calibrated coil branch.
        self.negative_state = flip.get("negative_state", True)
        if not isinstance(self.negative_state, bool):
            raise ValueError(f"{self.name}: negative_state must be a boolean")
        if self.flip_available and (
            type(self.flip_channel) is not int or not 0 <= self.flip_channel <= 7
        ):
            raise ValueError(f"{self.name}: relay requires a digital channel from 0 to 7")
        # Open-loop starting values; verify coil decay and relay motion in lab.
        self.discharge_delay_s = self._delay(flip.get("discharge_delay_s", 0.05))
        self.settle_delay_s = self._delay(flip.get("settle_delay_s", 0.05))
        self.scpi_write_delay_s = self._delay(scpi_write_delay_s)
        # Do not infer physical relay state from a newly created PS cache.
        self.direction = None if self.flip_available else POSITIVE
        self.requires_coordinated_initialization = False
        self.current_field = None
        self.current_current = None
        self._last_confirmed_current = None
        self._last_confirmed_polarity = None
        self._timing_enabled = False
        self._timing_log = []

    @staticmethod
    def _delay(value):
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Magnet delays must be non-negative and finite")
        return value

    def field_to_current(self, field_mT, direction=None):
        """Return non-negative current using the chosen directional fit."""
        target = float(field_mT)
        if not math.isfinite(target):
            raise ValueError(f"{self.name}: requested field must be finite")
        direction = self._select_branch(target) if direction is None else direction
        ratio, offset = self._branch_values(direction)
        delta = target - offset if direction == POSITIVE else offset - target
        return 0.0 if delta <= FIELD_ZERO_TOLERANCE_MT else delta / ratio

    def _branch_values(self, direction):
        branch = "positive" if direction != NEGATIVE else "negative"
        calibration = self.calibration[branch]
        return calibration["magnetic_field_ratio"], calibration["magnetic_field_offset"]

    def _select_branch(self, target_mT):
        """Pick a reachable branch, retaining polarity through the overlap.

        The positive branch can reach targets at/above its intercept; the
        negative branch can reach targets at/below its intercept. If offsets
        are ordered to leave a small unreachable interval, hold the current
        branch at zero current through that gap. With no prior branch, choose
        the closest zero-current intercept (negative wins an exact tie).
        """
        positive_offset = self.calibration["positive"]["magnetic_field_offset"]
        negative_offset = self.calibration["negative"]["magnetic_field_offset"]
        can_positive = target_mT >= positive_offset - FIELD_ZERO_TOLERANCE_MT
        can_negative = target_mT <= negative_offset + FIELD_ZERO_TOLERANCE_MT
        if can_positive and can_negative:
            direction = self.direction or NEGATIVE
        elif can_positive:
            direction = POSITIVE
        elif can_negative:
            direction = NEGATIVE
        elif self.direction is not None:
            direction = self.direction
        else:
            direction = (
                POSITIVE
                if abs(target_mT - positive_offset) < abs(target_mT - negative_offset)
                else NEGATIVE
            )
        return direction

    def plan_field(self, field_mT):
        """Validate without I/O; used to preflight all axes of a vector."""
        target = float(field_mT)
        if not math.isfinite(target):
            raise ValueError(f"{self.name}: requested field must be finite")
        direction = self._select_branch(target)
        current = self.field_to_current(target, direction)
        _, selected_offset = self._branch_values(direction)
        branch_reaches_target = (
            target >= selected_offset - FIELD_ZERO_TOLERANCE_MT
            if direction == POSITIVE
            else target <= selected_offset + FIELD_ZERO_TOLERANCE_MT
        )
        # If branch offsets leave an unachievable gap, use zero current on
        # the retained/nearest branch; never ask the supply for negative I.
        # Preserve the exact target for a reachable inverse calculation;
        # in the small branch gap report the nearest modeled intercept.
        model_field = target if branch_reaches_target else selected_offset
        if math.isfinite(current) and current < 0:
            raise ValueError(f"{self.name}: calibrated supply current must be non-negative")
        if not math.isfinite(current) or current > self.max_current:
            raise ValueError(f"{self.name}-coil exceeds maximum current or has invalid current")
        if direction == NEGATIVE and not self.flip_available:
            raise RuntimeError(f"{self.name}: reversed coil field requires an available polarity relay")
        if self.flip_available and self.pulse_streamer is None:
            raise RuntimeError(f"{self.name}: polarity relay requires a Pulse Streamer")
        if (current != 0 and self.direction is None
                and self.requires_coordinated_initialization):
            raise RuntimeError("Enable the magnet before commanding an unsynchronized polarity relay")
        return current, direction, model_field

    def current_to_field(self, current, polarity=None):
        """Model field for a non-negative current and known coil polarity."""
        if not math.isfinite(current) or current < 0:
            raise ValueError("Supply current must be non-negative and finite")
        direction = self.direction if polarity is None else polarity
        if direction not in (POSITIVE, NEGATIVE):
            direction = POSITIVE
        ratio, offset = self._branch_values(direction)
        return offset + direction.value * ratio * current

    def enable_timing_diagnostics(self, enabled):
        self._timing_enabled = bool(enabled)
        self._timing_log = []

    def pop_timing_log(self):
        log, self._timing_log = self._timing_log, []
        return log

    def _call(self, stage, function, *args):
        start = time.perf_counter() if self._timing_enabled else None
        result = function(*args)
        if self._timing_enabled:
            self._timing_log.append((stage, time.perf_counter() - start))
        return result

    def _wait(self, seconds, stage):
        if seconds > 0:
            self._call(stage, time.sleep, seconds)

    def _write_current(self, current):
        # Never keep a success cache after a possibly partially delivered write.
        self.invalidate_cache()
        self.current_field = None
        self.current_current = None
        self._call("set_current", self.power_supply.set_current, current)
        self.current_current = current
        self.current_field = self.current_to_field(current)
        self._last_confirmed_current = current
        self._last_confirmed_polarity = self.direction

    def _output(self, enabled):
        self.invalidate_cache()
        self._call("output_on" if enabled else "output_off",
                   self.power_supply.output_on if enabled else self.power_supply.output_off)

    def _switch_polarity(self, direction, current, resume_output):
        """Zero/off/discharge/switch/settle/program/on, for this axis only.

        E3631A/E3632A APPL programs current separately from OUTP. Pace the
        extra serial commands as well as the normal set_vector writes.
        On any error, do not re-enable or claim the requested field succeeded.
        """
        self._write_current(0.0)
        self._wait(self.scpi_write_delay_s, "switch_write_delay_sleep")
        self._output(False)
        self._wait(max(self.discharge_delay_s, self.scpi_write_delay_s),
                   "discharge_sleep")
        self.direction = None
        state = self.negative_state if direction == NEGATIVE else not self.negative_state

        def switch():
            self.pulse_streamer.set_digital_output(self.flip_channel, state)
            self.pulse_streamer.sync_outputs()

        self._call("apply_polarity_flip", switch)
        self._wait(self.settle_delay_s, "relay_settle_sleep")
        self.direction = direction
        self._write_current(current)
        if resume_output:
            self._wait(self.scpi_write_delay_s, "switch_write_delay_sleep")
            self._output(True)
            # OUTP succeeded; the current just programmed remains commanded.
            self._last_confirmed_current = current
            self._last_confirmed_polarity = direction

    def set_field(self, field_mT, keep_output_enabled=False):
        """Program a signed total field in mT, choosing this axis's relay.

        Return whether a supply write occurred. An off supply stays off;
        enable() grants output power. At the intercept, keep the existing
        polarity and retain the historical optional zero-current shutdown.
        """
        current, direction, model_field = self.plan_field(field_mT)
        # At the exact intercept no relay motion is needed, even if unknown.
        flip_needed = direction is not None and direction != self.direction
        wrote = False
        try:
            if flip_needed:
                self._switch_polarity(direction, current,
                                      bool(self.power_supply.output_enabled))
                wrote = True
            else:
                if self._timing_enabled:
                    self._timing_log.append(("apply_polarity_noop", 0.0))
                if not self._cache_matches(current):
                    self._write_current(current)
                    wrote = True
            if current == 0 and not keep_output_enabled:
                # Explicitly zero before output-off, even on a cache hit.
                if not wrote:
                    self._write_current(0.0)
                self._wait(self.scpi_write_delay_s, "switch_write_delay_sleep")
                self._output(False)
                wrote = True
        except Exception:
            self.invalidate_cache()
            self.current_field = None
            self.current_current = None
            # A transport error may follow a partially delivered command.
            # Best-effort OFF is safe even when the initial zero write failed.
            try:
                self._output(False)
            except Exception:
                pass  # Preserve the original failure; never continue switching.
            raise
        self.current_field = model_field
        self.current_current = current
        return wrote

    def _cache_matches(self, current):
        return (self._last_confirmed_current is not None
                and self._last_confirmed_polarity == self.direction
                and abs(self._last_confirmed_current - current) < CURRENT_TOLERANCE_A)

    def invalidate_cache(self):
        """Invalidate commanded-state cache after external supply changes.

        This is transport success caching, not a measured current or field.
        SCPI error queues are checked separately at optional scan boundaries.
        """
        self._last_confirmed_current = None
        self._last_confirmed_polarity = None

    def enable(self):
        if self.flip_available and self.direction is None:
            if self.requires_coordinated_initialization:
                raise RuntimeError("Enable the magnet to initialize the shared relay outputs safely")
            if self.pulse_streamer is None:
                raise RuntimeError(f"{self.name}: polarity relay requires a Pulse Streamer")
            # Establish LOW/positive only after zero/off, including on startup.
            self._switch_polarity(POSITIVE, 0.0, resume_output=True)
        else:
            if self.current_current is None:
                self._write_current(0.0)
                self._wait(self.scpi_write_delay_s, "switch_write_delay_sleep")
            self._output(True)
        self.invalidate_cache()

    def electrical_zero(self, disable_output=False):
        """De-energize the coil; its model field is B0, not magnetic zero."""
        try:
            self._write_current(0.0)
        finally:
            try:
                if disable_output:
                    self._wait(self.scpi_write_delay_s, "switch_write_delay_sleep")
                    self._output(False)
            finally:
                self.invalidate_cache()

    def zero(self):
        """Compatibility: electrical zero/output off. For B=0 use set_field(0)."""
        self.electrical_zero(disable_output=True)

    def get_polarity(self):
        """Last synchronized coil branch, or None when unknown."""
        return self.direction

    def get_field(self):
        """Last commanded/model total field (mT), or None after a failed write."""
        return self.current_field

    def get_current(self):
        return self.current_current

    @property
    def field_ratio(self):
        return self._branch_values(self.direction or POSITIVE)[0]

    @property
    def field_offset(self):
        return self._branch_values(self.direction or POSITIVE)[1]

    def get_ratio(self):
        """Active (or positive before synchronization) calibration in mT/A."""
        return self.field_ratio

    def get_offset(self):
        """Active (or positive before synchronization) zero-current offset in mT."""
        return self.field_offset

    def get_max_current(self):
        return self.max_current

    def get_supply_type(self):
        return self.config.get("type", "--")

    def get_supply_address(self):
        return self.config.get("address", "--")

    def get_supply_range(self):
        return self.config.get("range", self.config.get("channel", "--"))
