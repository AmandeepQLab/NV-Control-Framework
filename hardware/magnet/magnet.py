"""Helmholtz vector control with independent calibrated X/Y/Z polarity relays."""
import logging
import time

# Re-export the established enum/constants for existing callers.
from .magnet_axis import MagnetAxis, MagnetDirection, POSITIVE, NEGATIVE

LOGGER = logging.getLogger(__name__)


class Magnet:
    def __init__(self, config, power_supplies, pulse_streamer=None,
                 scpi_write_delay_s=None):
        self.config = config
        self.power_supplies = power_supplies
        self.pulse_streamer = pulse_streamer
        self.scpi_write_delay_s = MagnetAxis._delay(
            config.get("scpi_write_delay_s", 0.05)
            if scpi_write_delay_s is None else scpi_write_delay_s
        )
        if "flip_direction" in config:
            raise ValueError(
                "Global Helmholtz.flip_direction is obsolete; configure "
                "Helmholtz.X/Y/Z.flip_direction independently."
            )
        for name in ("X", "Y", "Z"):
            setattr(self, name.lower(), MagnetAxis(
                config[name], power_supplies[name], pulse_streamer,
                scpi_write_delay_s=self.scpi_write_delay_s,
            ))
        for axis in self.axes:
            axis.requires_coordinated_initialization = True
        relay_channels = [axis.flip_channel for axis in self.axes if axis.flip_available]
        if len(set(relay_channels)) != len(relay_channels):
            raise ValueError("Each magnet polarity relay must own a distinct digital channel")
        # Refuse a relay assigned to another configured digital owner.
        channel_map = getattr(pulse_streamer, "channel_map", {})
        if isinstance(channel_map, dict):
            for axis in self.axes:
                if not axis.flip_available:
                    continue
                owners = [name for name, channel in channel_map.items()
                          if channel == axis.flip_channel]
                if owners and owners != [axis.flip_channel_name]:
                    raise ValueError(f"{axis.name}: relay channel {axis.flip_channel} "
                                     f"conflicts with configured owners {owners}")
                if (axis.flip_channel_name in channel_map
                        and channel_map[axis.flip_channel_name] != axis.flip_channel):
                    raise ValueError(f"{axis.name}: relay channel does not match channel map")
        self.enabled = False
        self._timing_enabled = False
        self._timing_log = []

    @property
    def axes(self):
        return (self.x, self.y, self.z)

    def enable_timing_diagnostics(self, enabled):
        self._timing_enabled = bool(enabled)
        self._timing_log = []
        for axis in self.axes:
            axis.enable_timing_diagnostics(enabled)

    def pop_timing_log(self):
        log, self._timing_log = self._timing_log, []
        return log

    def electrical_zero(self):
        """Set all coil currents to zero, retaining outputs and relay states.

        The resulting model vector is the calibration background, not B=0.
        Use set_vector(0, 0, 0) for calibrated magnetic zero.
        """
        for axis in self.axes:
            axis.electrical_zero()
            self._pace_after_write(True, axis.name, False)

    def zero(self):
        """Backward-compatible electrical zero; does not apply B=0 compensation."""
        self.electrical_zero()

    def set_vector(self, bx=0.0, by=0.0, bz=0.0):
        """Command signed total fields in mT independently on all three axes."""
        values = (bx, by, bz)
        timing = self._timing_enabled
        start = time.perf_counter() if timing else None
        # A bad target must not cause a partially applied vector through validation.
        for axis, value in zip(self.axes, values):
            axis.plan_field(value)
        if timing:
            self._timing_log.append(("validate_vector", time.perf_counter() - start))
        for axis, value in zip(self.axes, values):
            # enable()/zero() diagnostics do not belong to this vector command.
            axis.pop_timing_log()
            wrote = axis.set_field(value, keep_output_enabled=True)
            if timing:
                for stage, duration in axis.pop_timing_log():
                    self._timing_log.append((f"axis_{axis.name}_{stage}", duration))
            self._pace_after_write(wrote, axis.name, timing)

    def _pace_after_write(self, wrote, axis_name, timing):
        """Preserve pacing after real writes; a cached point never sleeps."""
        if not wrote or self.scpi_write_delay_s <= 0:
            return
        start = time.perf_counter() if timing else None
        time.sleep(self.scpi_write_delay_s)
        if timing:
            self._timing_log.append(
                (f"axis_{axis_name}_write_delay_sleep", time.perf_counter() - start)
            )

    def get_vector(self):
        """Commanded/model fields in mT, not measured fields."""
        return {axis.name.lower(): axis.get_field() for axis in self.axes}

    def get_polarities(self):
        """Per-axis coil polarity; None denotes unsynchronized state."""
        return {axis.name.lower(): axis.get_polarity() for axis in self.axes}

    def enable(self):
        try:
            self._initialize_relays()
            # Configured axes retain their signed field when re-enabled.
            for axis in self.axes:
                axis.enable()
                self._pace_after_write(True, axis.name, False)
        except Exception:
            try:
                self.disable()
            except Exception:
                LOGGER.exception("Magnet enable failed and shutdown also reported an error")
            raise
        self.enabled = True

    def _initialize_relays(self):
        """Quiesce every unknown coil before syncing the shared output bank.

        The Pulse Streamer sends ALL cached outputs, whose initial LOW values
        are not readbacks. Sequential per-axis startup could otherwise flip
        an energized peer. Normal reversals never use this startup path.
        """
        unknown = [axis for axis in self.axes
                   if axis.flip_available and axis.direction is None]
        if not unknown:
            return
        if self.pulse_streamer is None:
            raise RuntimeError("Polarity relays require a Pulse Streamer")
        for axis in unknown:
            axis.electrical_zero(disable_output=True)
        # Start after the last output-off, so every unknown coil gets at
        # least its configured discharge time before any relay can move.
        delay = max(max(axis.discharge_delay_s, self.scpi_write_delay_s)
                    for axis in unknown)
        if delay:
            time.sleep(delay)
        for axis in unknown:
            self.pulse_streamer.set_digital_output(
                axis.flip_channel, not axis.negative_state)
        self.pulse_streamer.sync_outputs()
        delay = max(axis.settle_delay_s for axis in unknown)
        if delay:
            time.sleep(delay)
        for axis in unknown:
            axis.direction = POSITIVE
            axis.invalidate_cache()

    def disable(self):
        # Do not reset relays: even shutdown relay motion requires local
        # discharge/settle ordering. Zero/off alone de-energizes every coil.
        errors = []
        for axis in self.axes:
            try:
                axis.electrical_zero(disable_output=True)
                self._pace_after_write(True, axis.name, False)
            except Exception as error:
                errors.append((axis.name, error))
        self.enabled = False
        if errors:
            names = ", ".join(name for name, _ in errors)
            raise RuntimeError(f"Failed to de-energize magnet axis(es): {names}") from errors[0][1]

    def is_enabled(self):
        return self.enabled

    def check_error_queues(self, context=""):
        """Optional scan-boundary SCPI diagnostics, never a per-write query."""
        found = {}
        for axis in self.axes:
            if not hasattr(axis.power_supply, "check_error_queue"):
                continue
            errors = axis.power_supply.check_error_queue()
            if errors:
                found[axis.name] = errors
                LOGGER.error(
                    "%s-axis power supply reported %d pending SCPI error(s) at %s: %s",
                    axis.name, len(errors), context or "error check", errors,
                )
        return found

    def is_connected(self):
        return all(axis.power_supply.connected for axis in self.axes)

    def set_polarity(self, direction):
        """Retired global control: signed field requests select each relay safely."""
        raise RuntimeError("Global polarity control is retired; use axis.set_field() or set_vector()")

    def flip_polarity(self):
        self.set_polarity(None)

    # Keep old mutating entry points as explicit migration errors, never a flip.
    set_direction = set_polarity
    flip_direction = flip_polarity
