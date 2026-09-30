"""Hardware-free calibration, relay sequencing, and failure-path checks.

Only in-memory supply/streamer subclasses are instantiated. No connections,
serial interfaces, camera SDKs, or network devices are accessed.
"""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from experiments.zero_field_experiment import ZeroFieldExperiment
from hardware.camera.sim_camera import SimCamera
from hardware.magnet.magnet import Magnet, POSITIVE, NEGATIVE
from hardware.magnet.magnet_axis import MagnetAxis
from hardware.power_supply.sim_power_supply import SimPowerSupply
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


SETUP = json.loads((Path(__file__).resolve().parents[1] / "config/setupInfo.json").read_text())
CHANNELS = {"X": 6, "Y": 7, "Z": 3}


class Supply(SimPowerSupply):
    def __init__(self, name, events):
        super().__init__({"name": name})
        self.events = events

    def set_current(self, value):
        assert value >= 0, "A calibrated command sent negative supply current"
        self.events.append(("current", self.name, value))
        self.current = value

    def output_on(self):
        self.events.append(("on", self.name))
        self.output_enabled = True

    def output_off(self):
        self.events.append(("off", self.name))
        self.output_enabled = False


class Streamer(SimPulseStreamer):
    def __init__(self, supplies, events):
        super().__init__(channel_map={f"flipMF_{name}": ch for name, ch in CHANNELS.items()})
        self.events = events
        self.supplies = supplies
        # Physical simulator state is separate from the software cache, to
        # expose unsafe initial synchronization of a relay left HIGH at boot.
        self.physical = {channel: True for channel in CHANNELS.values()}

    def set_digital_output(self, channel, state):
        self.events.append(("relay", channel, state))
        super().set_digital_output(channel, state)

    def sync_outputs(self):
        self.events.append(("sync",))
        for name, channel in CHANNELS.items():
            state = self.persistent_outputs[channel]
            if self.physical[channel] != state:
                supply = self.supplies[name]
                assert supply.current == 0.0 and not supply.output_enabled
            self.physical[channel] = state


def make_magnet(enable=True, config=None):
    config = deepcopy(SETUP["Helmholtz"] if config is None else config)
    config["scpi_write_delay_s"] = 0.0
    for name in CHANNELS:
        flip = config[name]["flip_direction"]
        flip["discharge_delay_s"] = 0.0
        flip["settle_delay_s"] = 0.0
    events = []
    supplies = {name: Supply(name, events) for name in CHANNELS}
    streamer = Streamer(supplies, events)
    magnet = Magnet(config, supplies, streamer)
    if enable:
        magnet.enable()
        events.clear()
    return magnet, supplies, streamer, events


def test_verified_configuration_has_unique_relay_owners_and_directional_calibrations():
    pg = SETUP["pulseGenerator"]
    mapping = dict(zip(pg["channelNames"], pg["channelValues"]))
    assert len(mapping) == len(set(mapping.values()))
    assert "detector2" not in mapping
    assert "flip_direction" not in SETUP["Helmholtz"]
    assert "Switch" not in SETUP  # Removed unused root EventCamera fragment.
    expected = {
        "X": {"positive": (0.887629, 0.012076), "negative": (0.886743, 0.012819)},
        "Y": {"positive": (0.741657, 0.006619), "negative": (0.740600, 0.006233)},
        "Z": {"positive": (0.799629, 0.117176), "negative": (0.800057, 0.117748)},
    }
    for name, channel in CHANNELS.items():
        cfg = SETUP["Helmholtz"][name]
        flip = cfg["flip_direction"]
        assert flip["switch"]["switchChannel"] == mapping[f"flipMF_{name}"] == channel
        assert flip["negative_state"] is True
        for branch, calibration in expected[name].items():
            assert (
                cfg["magnetic_field_calibration"][branch]["magnetic_field_ratio"],
                cfg["magnetic_field_calibration"][branch]["magnetic_field_offset"],
            ) == calibration


def test_startup_quiesces_all_unknown_axes_before_shared_relay_sync():
    magnet, supplies, streamer, events = make_magnet(enable=False)
    assert magnet.get_vector() == {"x": None, "y": None, "z": None}
    for supply in supplies.values():
        supply.current = 0.5
        supply.output_enabled = True  # Simulate supply left energized at restart.
    magnet.enable()
    first_sync = events.index(("sync",))
    first_relay = next(i for i, e in enumerate(events) if e[0] == "relay")
    for name in CHANNELS:
        assert events.index(("current", name, 0.0)) < first_relay
        assert events.index(("off", name)) < first_relay
        assert events.index(("on", name)) > first_sync
    assert magnet.get_polarities() == dict.fromkeys(("x", "y", "z"), POSITIVE)
    assert not any(streamer.physical.values())


def test_uninitialized_direct_command_cannot_sync_energized_peers():
    magnet, _, _, events = make_magnet(enable=False)
    with pytest.raises(RuntimeError, match="Enable the magnet"):
        magnet.x.set_field(0.4)
    with pytest.raises(RuntimeError, match="Enable the magnet"):
        magnet.set_vector(0, 0, 0)
    assert events == []


@pytest.mark.parametrize("signs", list(product((-1, 1), repeat=3)))
def test_all_field_sign_combinations_are_accepted_and_use_background_intercept(signs):
    magnet, supplies, _, _ = make_magnet()
    targets = tuple(sign * 0.3 for sign in signs)
    magnet.set_vector(*targets)
    assert tuple(magnet.get_vector().values()) == targets
    for axis, target in zip(magnet.axes, targets):
        direction = axis.get_polarity()
        ratio, offset = axis._branch_values(direction)
        delta = target - offset if direction == POSITIVE else offset - target
        assert supplies[axis.name].current == pytest.approx(abs(delta) / ratio)
        assert direction == (POSITIVE if target > 0 else NEGATIVE)
        assert axis.current_to_field(axis.get_current()) == pytest.approx(target)


@pytest.mark.parametrize("name", CHANNELS)
@pytest.mark.parametrize("direction", [POSITIVE, NEGATIVE])
def test_each_axis_uses_its_measured_directional_slope_and_offset(name, direction):
    magnet, supplies, _, _ = make_magnet()
    axis = getattr(magnet, name.lower())
    branch = "positive" if direction == POSITIVE else "negative"
    calibration = axis.calibration[branch]
    ratio = calibration["magnetic_field_ratio"]
    offset = calibration["magnetic_field_offset"]
    requested_current = 0.25
    target = offset + direction.value * ratio * requested_current

    axis.set_field(target)

    assert axis.get_polarity() == direction
    assert supplies[name].current == pytest.approx(requested_current)
    assert axis.current_to_field(requested_current) == pytest.approx(target)


@pytest.mark.parametrize("name", CHANNELS)
def test_positive_target_below_offset_needs_reversed_coil(name):
    magnet, _, streamer, _ = make_magnet()
    axis = getattr(magnet, name.lower())
    target = axis.field_offset / 2
    axis.set_field(target)
    assert target > 0 and axis.get_polarity() == NEGATIVE
    assert streamer.get_digital_output(CHANNELS[name])
    negative = axis.calibration["negative"]
    assert axis.get_current() == pytest.approx(
        (negative["magnetic_field_offset"] - target)
        / negative["magnetic_field_ratio"]
    )


def test_magnetic_zero_is_compensation_electrical_zero_is_background():
    magnet, supplies, streamer, events = make_magnet()
    magnet.set_vector(0, 0, 0)
    assert magnet.get_vector() == dict.fromkeys(("x", "y", "z"), 0.0)
    for axis in magnet.axes:
        assert axis.get_polarity() == NEGATIVE
        assert axis.get_current() == pytest.approx(axis.field_offset / axis.field_ratio)
        assert supplies[axis.name].current > 0
    relays = dict(streamer.persistent_outputs)
    events.clear()
    magnet.zero()
    assert all(s.current == 0 for s in supplies.values())
    assert all(s.output_enabled for s in supplies.values())
    assert magnet.get_vector() == {a.name.lower(): a.field_offset for a in magnet.axes}
    assert streamer.persistent_outputs == relays
    assert not any(e[0] == "relay" for e in events)
    magnet.disable()
    assert not any(s.output_enabled for s in supplies.values())


@pytest.mark.parametrize("name", CHANNELS)
def test_equal_magnitude_reversal_has_exact_local_safe_sequence(name):
    magnet, supplies, streamer, events = make_magnet()
    magnet.set_vector(0.4, 0.4, 0.4)
    axis = getattr(magnet, name.lower())
    axis.set_field(axis.field_offset + axis.field_ratio * 0.2)
    before = {n: (s.current, s.output_enabled, streamer.get_digital_output(CHANNELS[n]))
              for n, s in supplies.items() if n != name}
    axis.scpi_write_delay_s = 0.005
    axis.discharge_delay_s = 0.012
    axis.settle_delay_s = 0.023
    events.clear()
    with patch("hardware.magnet.magnet_axis.time.sleep", side_effect=lambda s: events.append(("wait", s))):
        negative = axis.calibration["negative"]
        axis.set_field(
            negative["magnetic_field_offset"]
            - negative["magnetic_field_ratio"] * 0.2
        )
    assert events[:7] == [
        ("current", name, 0.0), ("wait", 0.005), ("off", name), ("wait", 0.012),
        ("relay", CHANNELS[name], True), ("sync",), ("wait", 0.023),
    ]
    assert events[7][0:2] == ("current", name)
    assert events[7][2] == pytest.approx(0.2)
    assert events[8:] == [("wait", 0.005), ("on", name)]
    assert before == {n: (s.current, s.output_enabled, streamer.get_digital_output(CHANNELS[n]))
                      for n, s in supplies.items() if n != name}


@pytest.mark.parametrize("name", CHANNELS)
def test_intercept_and_repeated_same_branch_points_do_not_toggle(name):
    magnet, _, _, events = make_magnet()
    axis = getattr(magnet, name.lower())
    axis.set_field(axis.field_offset - 0.1)
    events.clear()
    for target in (axis.field_offset - 0.1, axis.field_offset - 0.2,
                   axis.field_offset, axis.field_offset + 5e-10):
        axis.set_field(target, keep_output_enabled=True)
    assert not any(e[0] == "relay" for e in events)
    assert axis.get_current() == 0
    assert axis.get_polarity() == NEGATIVE
    events.clear()
    assert axis.set_field(axis.field_offset, keep_output_enabled=True) is False
    assert events == []


def test_offset_gap_holds_current_branch_at_zero_current_without_negative_commands():
    # Y's measured negative offset is below its positive offset, leaving a
    # narrow unreachable interval. A request there holds the relay branch,
    # commands exactly zero current, and reports that branch's modeled field.
    magnet, supplies, streamer, events = make_magnet()
    axis = magnet.y
    low = axis.calibration["negative"]["magnetic_field_offset"]
    high = axis.calibration["positive"]["magnetic_field_offset"]
    target = (low + high) / 2

    axis.set_field(axis.calibration["positive"]["magnetic_field_offset"] + 0.01)
    assert axis.get_polarity() == POSITIVE
    events.clear()
    axis.set_field(target)
    assert axis.get_polarity() == POSITIVE
    assert supplies["Y"].current == 0
    assert axis.get_field() == high
    assert not any(event[0] == "relay" for event in events)
    assert all(s.current >= 0 for s in supplies.values())

    # At the next target below the negative intercept, switch branches safely.
    axis.set_field(low - 0.001)
    assert axis.get_polarity() == NEGATIVE
    assert supplies["Y"].current > 0
    assert streamer.get_digital_output(CHANNELS["Y"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1000])
def test_invalid_last_axis_target_prevalidates_without_partial_vector(value):
    magnet, _, _, events = make_magnet()
    with pytest.raises(ValueError):
        magnet.set_vector(0.4, -0.4, value)
    assert events == []


@pytest.mark.parametrize("ratio", [-1, 0, float("nan"), float("inf")])
def test_invalid_calibration_ratio_is_rejected(ratio):
    cfg = deepcopy(SETUP["Helmholtz"]["X"])
    cfg["magnetic_field_calibration"]["positive"]["magnetic_field_ratio"] = ratio
    with pytest.raises(ValueError):
        MagnetAxis(cfg, Supply("X", []))


def test_missing_or_unavailable_relay_fails_before_current_write():
    magnet, _, _, events = make_magnet()
    magnet.x.flip_available = False
    with pytest.raises(RuntimeError, match="available polarity relay"):
        magnet.set_vector(0, 0, 0)
    assert events == []
    magnet.x.flip_available = True
    magnet.x.pulse_streamer = None
    with pytest.raises(RuntimeError, match="Pulse Streamer"):
        magnet.set_vector(0, 0, 0)
    assert events == []


def test_negative_calculated_current_is_a_software_error_not_a_supply_command():
    magnet, _, _, events = make_magnet()
    with patch.object(magnet.x, "field_to_current", return_value=-0.01):
        with pytest.raises(ValueError, match="non-negative"):
            magnet.set_vector(0.4, 0.4, 0.4)
    assert events == []


def test_duplicate_or_conflicting_channel_owner_is_rejected():
    cfg = deepcopy(SETUP["Helmholtz"])
    cfg["Y"]["flip_direction"]["switch"]["switchChannel"] = 6
    with pytest.raises(ValueError, match="distinct digital channel"):
        make_magnet(enable=False, config=cfg)
    magnet, supplies, streamer, _ = make_magnet(enable=False)
    streamer.channel_map["detector2"] = 6
    with pytest.raises(ValueError, match="conflicts"):
        Magnet(magnet.config, supplies, streamer)


@pytest.mark.parametrize("failure", ["zero", "off", "sync", "target", "on"])
def test_failed_transition_does_not_cache_success_or_continue_unsafely(failure):
    magnet, supplies, streamer, events = make_magnet()
    magnet.set_vector(0.4, 0.4, 0.4)
    axis = magnet.x
    ps = supplies["X"]
    original = ps.set_current

    def set_current(value):
        if (failure == "zero" and value == 0) or (failure == "target" and value > 0):
            raise RuntimeError("injected")
        return original(value)

    method, target = {"zero": ("set_current", ps), "target": ("set_current", ps),
                      "off": ("output_off", ps), "on": ("output_on", ps),
                      "sync": ("sync_outputs", streamer)}[failure]
    events.clear()
    replacement = set_current if failure in ("zero", "target") else None
    with patch.object(target, method, side_effect=replacement or RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="injected"):
            axis.set_field(-0.4)
    assert axis._last_confirmed_current is None
    assert axis.get_field() is None
    assert not any(e[0] in ("current", "off", "on") and e[1] in ("Y", "Z") for e in events)
    if failure in ("zero", "off"):
        assert not any(e[0] == "relay" for e in events)
    if failure == "sync":
        assert axis.get_polarity() is None
    if failure != "off":
        assert not ps.output_enabled


def test_disable_attempts_other_axes_and_off_even_when_zero_fails():
    magnet, supplies, _, events = make_magnet()
    magnet.set_vector(0.4, 0.4, 0.4)
    events.clear()
    with patch.object(supplies["X"], "set_current", side_effect=RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="X"):
            magnet.disable()
    assert all(("off", name) in events for name in CHANNELS)
    assert not any(s.output_enabled for s in supplies.values())


def test_enable_failure_rolls_back_other_axes():
    magnet, supplies, _, events = make_magnet(enable=False)
    with patch.object(supplies["Y"], "output_on", side_effect=RuntimeError("injected")):
        with pytest.raises(RuntimeError, match="injected"):
            magnet.enable()
    assert not any(s.output_enabled for s in supplies.values())
    assert not magnet.is_enabled()


@pytest.mark.parametrize("name", CHANNELS)
def test_zero_field_scan_converts_gauss_once_and_preserves_compensated_peers(name):
    magnet, supplies, streamer, events = make_magnet()
    experiment = ZeroFieldExperiment(
        hardware_manager=None, camera=SimCamera(image_shape=(2, 2)), magnet=magnet,
        field_start=-1, field_stop=3, field_points=3, field_axis=name,
        settling_time_ms=0, averages=1,
    )
    experiment.setup_scan()
    try:
        peers = {n: (s.current, streamer.get_digital_output(CHANNELS[n]))
                 for n, s in supplies.items() if n != name}
        for gauss in (-1.0, 0.0, 3.0):
            events.clear()
            experiment.set_scan_point(gauss)
            assert getattr(magnet, name.lower()).get_field() == pytest.approx(gauss * 0.1)
            assert not any(e[0] in ("current", "off", "on") and e[1] != name for e in events)
            assert peers == {n: (s.current, streamer.get_digital_output(CHANNELS[n]))
                             for n, s in supplies.items() if n != name}
    finally:
        experiment.cleanup_scan()
