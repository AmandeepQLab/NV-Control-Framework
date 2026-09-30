"""Sim-mode tests for the ZFE SCPI-overrun fix: the MagnetAxis double-write
removal, per-axis change-detection (cached only after a confirmed write,
invalidated by anything else that changes supply state), the configurable
inter-command delay, and the off-by-default power-supply error-queue check.

No real hardware is touched anywhere in this file -- SimPowerSupply and
SimPulseStreamer stand in for the serial/TCP links, and a real
Magnet/MagnetAxis stack is built on top of them so the logic under test
(hardware/magnet/magnet.py, hardware/magnet/magnet_axis.py) runs for real,
not through a test double.
"""

import unittest
from unittest import mock

from experiments.zero_field_experiment import ZeroFieldExperiment
from hardware.camera.sim_camera import SimCamera
from hardware.magnet.magnet import Magnet, POSITIVE, NEGATIVE
from hardware.power_supply.sim_power_supply import SimPowerSupply
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


# Nonzero intercepts exercise compensation-current writes. The negative
# Y offset deliberately covers the opposite compensation branch as well.
_RELAY_CHANNELS = {"X": 6, "Y": 7, "Z": 3}


def _calibration(ratio, offset):
    return {
        branch: {"magnetic_field_ratio": ratio, "magnetic_field_offset": offset}
        for branch in ("positive", "negative")
    }


_HELMHOLTZ_CFG = {
    "X": {"name": "X", "max_current": 5.0,
          "magnetic_field_calibration": _calibration(5.383, 0.006)},
    "Y": {"name": "Y", "max_current": 5.0,
          "magnetic_field_calibration": _calibration(6.600, -0.027)},
    "Z": {"name": "Z", "max_current": 5.0,
          "magnetic_field_calibration": _calibration(8.165, 0.304)},
}
for _axis, _channel in _RELAY_CHANNELS.items():
    _HELMHOLTZ_CFG[_axis]["flip_direction"] = {
        "available": True,
        "negative_state": True,
        "switch": {"switchChannel": _channel, "switchChannelName": f"flipMF_{_axis}"},
        "discharge_delay_s": 0.0,
        "settle_delay_s": 0.0,
    }


def _instrument(power_supply):
    """Wrap set_current/output_on/output_off with a call log, in place."""
    power_supply.calls = []
    for method_name in ("set_current", "output_on", "output_off"):
        original = getattr(power_supply, method_name)

        def wrapped(*args, _name=method_name, _original=original, **kwargs):
            power_supply.calls.append((_name, args, kwargs))
            return _original(*args, **kwargs)

        setattr(power_supply, method_name, wrapped)
    return power_supply


def make_magnet(scpi_write_delay_s=0.0, offsets=True):
    cfg = _HELMHOLTZ_CFG if offsets else {
        axis: {**_HELMHOLTZ_CFG[axis],
               "magnetic_field_calibration": _calibration(
                   _HELMHOLTZ_CFG[axis]["magnetic_field_calibration"]["positive"]["magnetic_field_ratio"],
                   0.0,
               )}
        for axis in ("X", "Y", "Z")
    }
    power_supplies = {}
    for axis in ("X", "Y", "Z"):
        ps = SimPowerSupply({"name": axis})
        ps.connect()
        _instrument(ps)
        power_supplies[axis] = ps
    pulse_streamer = SimPulseStreamer(
        channel_map={f"flipMF_{axis}": channel for axis, channel in _RELAY_CHANNELS.items()}
    )
    pulse_streamer.connect()
    return Magnet(
        config=cfg, power_supplies=power_supplies,
        pulse_streamer=pulse_streamer, scpi_write_delay_s=scpi_write_delay_s,
    )


def set_current_calls(power_supply):
    return [c for c in power_supply.calls if c[0] == "set_current"]


class DoubleWriteRemovedTests(unittest.TestCase):
    def test_nonzero_field_writes_current_exactly_once(self):
        magnet = make_magnet()
        magnet.enable()
        for ps in magnet.power_supplies.values():
            ps.calls.clear()

        magnet.x.set_field(1.5, keep_output_enabled=True)

        self.assertEqual(len(set_current_calls(magnet.power_supplies["X"])), 1)


class ChangeDetectionCacheTests(unittest.TestCase):
    def test_repeated_identical_value_is_skipped(self):
        magnet = make_magnet()
        magnet.enable()
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        wrote_first = magnet.x.set_field(1.5, keep_output_enabled=True)
        wrote_second = magnet.x.set_field(1.5, keep_output_enabled=True)

        self.assertTrue(wrote_first)
        self.assertFalse(wrote_second)
        self.assertEqual(len(set_current_calls(ps)), 1)

    def test_changed_value_is_not_skipped(self):
        magnet = make_magnet()
        magnet.enable()
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        magnet.x.set_field(1.5, keep_output_enabled=True)
        wrote = magnet.x.set_field(2.0, keep_output_enabled=True)

        self.assertTrue(wrote)
        self.assertEqual(len(set_current_calls(ps)), 2)

    def test_cache_is_not_updated_when_write_raises(self):
        # This is the requirement that matters most here: a write that
        # fails or is dropped must not be recorded as confirmed, or every
        # later identical request would be wrongly skipped forever --
        # exactly turning a lost command permanently invisible, the class
        # of bug this whole fix targets.
        magnet = make_magnet()
        magnet.enable()
        ps = magnet.power_supplies["X"]

        with mock.patch.object(ps, "set_current", side_effect=RuntimeError("dropped")):
            with self.assertRaises(RuntimeError):
                magnet.x.set_field(1.5, keep_output_enabled=True)

        # The failed write must not have been cached -- the next call
        # with the SAME value must still go out over the wire.
        ps.calls.clear()
        wrote = magnet.x.set_field(1.5, keep_output_enabled=True)
        self.assertTrue(wrote)
        self.assertEqual(len(set_current_calls(ps)), 1)

    def test_cache_invalidated_by_enable(self):
        magnet = make_magnet()
        magnet.enable()
        magnet.x.set_field(1.5, keep_output_enabled=True)

        magnet.enable()  # e.g. re-armed at the start of a second scan
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        wrote = magnet.x.set_field(1.5, keep_output_enabled=True)
        self.assertTrue(wrote, "enable() must invalidate the cache")

    def test_cache_invalidated_by_disable(self):
        magnet = make_magnet()
        magnet.enable()
        magnet.x.set_field(1.5, keep_output_enabled=True)

        magnet.disable()
        magnet.enable()
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        wrote = magnet.x.set_field(1.5, keep_output_enabled=True)
        self.assertTrue(wrote, "disable() must invalidate the cache")

    def test_cache_invalidated_by_direct_output_on_off(self):
        magnet = make_magnet()
        magnet.enable()
        magnet.x.set_field(1.5, keep_output_enabled=True)

        magnet.x.power_supply.output_off()
        magnet.x.invalidate_cache()
        magnet.x.power_supply.output_on()
        magnet.x.invalidate_cache()
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        wrote = magnet.x.set_field(1.5, keep_output_enabled=True)
        self.assertTrue(wrote)

    def test_cache_invalidated_by_polarity_flip(self):
        magnet = make_magnet(offsets=False)
        magnet.enable()
        magnet.set_vector(bx=1.5, by=0.0, bz=0.0)

        ps = magnet.power_supplies["X"]
        ps.calls.clear()
        magnet.set_vector(bx=-1.5, by=0.0, bz=0.0)

        # Equal current magnitude on opposite branches is a physical change:
        # discharge before reversing, then restore the requested magnitude.
        self.assertEqual([call[1][0] for call in set_current_calls(ps)], [0.0, 1.5 / 5.383])
        self.assertEqual(magnet.x.get_polarity(), NEGATIVE)

    def test_single_axis_flip_preserves_other_axes_current_caches(self):
        for changed_axis in ("X", "Y", "Z"):
            with self.subTest(axis=changed_axis):
                magnet = make_magnet(offsets=False)
                magnet.enable()
                fields = {"bx": 1.0, "by": 1.0, "bz": 1.0}
                magnet.set_vector(**fields)
                for ps in magnet.power_supplies.values():
                    ps.calls.clear()

                fields[f"b{changed_axis.lower()}"] = -1.0
                magnet.set_vector(**fields)

                for axis, ps in magnet.power_supplies.items():
                    if axis == changed_axis:
                        self.assertEqual(len(set_current_calls(ps)), 2)
                    else:
                        self.assertEqual(ps.calls, [], axis)
                        self.assertEqual(getattr(magnet, axis.lower()).get_polarity(), POSITIVE)

    def test_no_flip_still_skips_unchanged_axes(self):
        # The optimisation must survive: a same-direction point-to-point
        # move (the common case within one sweep) still skips axes whose
        # value didn't change.
        magnet = make_magnet()
        magnet.enable()
        magnet.set_vector(bx=1.5, by=0.0, bz=0.0)

        for ps in magnet.power_supplies.values():
            ps.calls.clear()
        magnet.set_vector(bx=1.5, by=0.0, bz=0.0)  # identical point again

        for axis in ("X", "Y", "Z"):
            self.assertEqual(
                len(set_current_calls(magnet.power_supplies[axis])), 0, axis
            )

    def test_zero_intercept_output_policy_is_preserved(self):
        magnet = make_magnet(offsets=False)
        magnet.enable()
        ps = magnet.power_supplies["X"]
        ps.calls.clear()

        magnet.x.set_field(0.0, keep_output_enabled=True)
        self.assertEqual([c[0] for c in ps.calls], ["set_current"])

        ps.calls.clear()
        magnet.x.set_field(0.0, keep_output_enabled=False)
        self.assertEqual([c[0] for c in ps.calls], ["set_current", "output_off"])


class SweepTrafficTests(unittest.TestCase):
    """Reproduce the rig scenario end to end and count real SCPI writes."""

    def _run_sweep(self, field_start, field_stop, field_points, field_axis="X"):
        camera = SimCamera(image_shape=(2, 2))
        camera.connect()
        magnet = make_magnet()
        experiment = ZeroFieldExperiment(
            hardware_manager=None,
            camera=camera,
            magnet=magnet,
            field_start=field_start,
            field_stop=field_stop,
            field_points=field_points,
            field_axis=field_axis,
            settling_time_ms=0,
            averages=1,
        )
        experiment.run()
        return magnet

    def test_degenerate_repro_writes_only_initialization_first_point_and_cleanup(self):
        magnet = self._run_sweep(-15, -15, 61, field_axis="X")

        # Every axis gets one initialization zero write, compensation, cleanup zero.
        # X/Z also discharge once when setup selects negative compensation.
        # Only X gets one changed scan-point write; repeated points are cached.
        self.assertEqual(len(set_current_calls(magnet.power_supplies["X"])), 5)
        self.assertEqual(len(set_current_calls(magnet.power_supplies["Y"])), 3)
        self.assertEqual(len(set_current_calls(magnet.power_supplies["Z"])), 4)

    def test_non_degenerate_sweep_writes_once_per_changed_point(self):
        magnet = self._run_sweep(-15, 15, 61, field_axis="X")

        # X: initialization(1), setup discharge+compensation(2), points(61),
        # crossing the intercept discharge(1), cleanup(1). The crossing
        # preserves Y/Z compensation and does not invalidate their caches.
        self.assertEqual(len(set_current_calls(magnet.power_supplies["X"])), 66)
        self.assertEqual(len(set_current_calls(magnet.power_supplies["Y"])), 3)
        self.assertEqual(len(set_current_calls(magnet.power_supplies["Z"])), 4)
        for ps in magnet.power_supplies.values():
            self.assertTrue(all(call[1][0] >= 0.0 for call in set_current_calls(ps)))


class InterCommandDelayTests(unittest.TestCase):
    def test_delay_applied_only_after_real_writes(self):
        sleep_calls = []
        magnet = make_magnet(scpi_write_delay_s=0.02)

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        magnet.enable()
        with mock.patch("hardware.magnet.magnet.time.sleep", side_effect=fake_sleep):
            magnet.set_vector(bx=1.5, by=0.0, bz=0.0)  # 3 real writes
            sleep_calls.clear()
            magnet.set_vector(bx=1.5, by=0.0, bz=0.0)  # 0 real writes (all cached)

        self.assertEqual(sleep_calls, [])

    def test_delay_value_is_configurable_and_fires_once_per_real_write(self):
        sleep_calls = []
        magnet = make_magnet(scpi_write_delay_s=0.02)

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        magnet.enable()
        with mock.patch("hardware.magnet.magnet.time.sleep", side_effect=fake_sleep):
            magnet.set_vector(bx=1.5, by=0.3, bz=0.5)  # 3 writes, no relay changes

        self.assertEqual(sleep_calls, [0.02, 0.02, 0.02])

    def test_zero_delay_sleeps_not_at_all(self):
        sleep_calls = []
        magnet = make_magnet(scpi_write_delay_s=0.0)

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        magnet.enable()
        with mock.patch("hardware.magnet.magnet.time.sleep", side_effect=fake_sleep):
            magnet.set_vector(bx=1.5, by=0.3, bz=0.2)

        self.assertEqual(sleep_calls, [])

    def test_config_key_sets_default_delay(self):
        magnet = Magnet(
            config={**_HELMHOLTZ_CFG, "scpi_write_delay_s": 0.123},
            power_supplies={
                axis: SimPowerSupply({"name": axis}) for axis in ("X", "Y", "Z")
            },
        )
        self.assertEqual(magnet.scpi_write_delay_s, 0.123)

    def test_missing_config_key_falls_back_to_default(self):
        magnet = Magnet(
            config=_HELMHOLTZ_CFG,
            power_supplies={
                axis: SimPowerSupply({"name": axis}) for axis in ("X", "Y", "Z")
            },
        )
        self.assertEqual(magnet.scpi_write_delay_s, 0.05)

    def test_constructor_argument_overrides_config(self):
        magnet = Magnet(
            config={**_HELMHOLTZ_CFG, "scpi_write_delay_s": 0.123},
            power_supplies={
                axis: SimPowerSupply({"name": axis}) for axis in ("X", "Y", "Z")
            },
            scpi_write_delay_s=0.0,
        )
        self.assertEqual(magnet.scpi_write_delay_s, 0.0)


class ErrorQueueCheckTests(unittest.TestCase):
    def test_sim_power_supply_reports_no_errors_by_default(self):
        ps = SimPowerSupply({"name": "X"})
        ps.connect()
        self.assertEqual(ps.check_error_queue(), [])

    def test_check_error_queues_aggregates_and_omits_clean_axes(self):
        magnet = make_magnet()
        magnet.power_supplies["X"].check_error_queue = lambda: [
            '+521,"Input buffer overrun"'
        ]

        with self.assertLogs("hardware.magnet.magnet", level="ERROR") as log:
            found = magnet.check_error_queues(context="test")

        self.assertEqual(list(found.keys()), ["X"])
        self.assertEqual(found["X"], ['+521,"Input buffer overrun"'])
        self.assertTrue(any("X-axis" in message for message in log.output))

    def test_check_error_queues_returns_empty_when_all_clean(self):
        magnet = make_magnet()
        self.assertEqual(magnet.check_error_queues(), {})

    def test_zfe_flag_off_by_default_never_checks(self):
        camera = SimCamera(image_shape=(2, 2))
        camera.connect()
        magnet = make_magnet()
        magnet.check_error_queues = mock.Mock(wraps=magnet.check_error_queues)

        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=camera, magnet=magnet,
            field_start=0.0, field_stop=1.0, field_points=2,
            field_axis="X", settling_time_ms=0,
        )
        experiment.run()

        magnet.check_error_queues.assert_not_called()

    def test_zfe_flag_on_checks_exactly_at_scan_start_and_end(self):
        camera = SimCamera(image_shape=(2, 2))
        camera.connect()
        magnet = make_magnet()
        magnet.check_error_queues = mock.Mock(wraps=magnet.check_error_queues)

        experiment = ZeroFieldExperiment(
            hardware_manager=None, camera=camera, magnet=magnet,
            field_start=0.0, field_stop=1.0, field_points=5,
            field_axis="X", settling_time_ms=0,
            check_power_supply_errors=True,
        )
        experiment.run()

        # Exactly twice for the whole run -- never per point -- regardless
        # of field_points, which is the entire point of checking only at
        # scan boundaries.
        self.assertEqual(magnet.check_error_queues.call_count, 2)


if __name__ == "__main__":
    unittest.main()
