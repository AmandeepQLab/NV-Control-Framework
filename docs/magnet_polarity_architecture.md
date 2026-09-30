# Independent magnet polarity relays

## Architecture and calibration

The former `Magnet` implementation selected one global polarity, rejected mixed-sign vectors, and passed absolute field values to all three `MagnetAxis` objects. The configured channel 3 relay consequently represented all coils in software.

Each `MagnetAxis` now receives its own `Helmholtz.<axis>.flip_direction` configuration and the shared Pulse Streamer. `Magnet.set_vector(bx, by, bz)` passes signed total-field targets independently to the axes.

| Axis | Supply | Polarity relay | Channel name |
| --- | --- | --- | --- |
| X | E3632A, COM13 | Digital 6 | `flipMF_X` |
| Y | E3631A, COM12, P6V | Digital 7 | `flipMF_Y` |
| Z | E3632A, COM11 | Digital 3 | `flipMF_Z` |

Each relay direction has its own measured calibration:

```text
positive: B_target = B0_positive + k_positive * I
negative: B_target = B0_negative - k_negative * I
```

Each directional `magnetic_field_ratio` is a positive magnitude `k` in mT/A, and each directional `magnetic_field_offset` is its zero-current intercept `B0` in mT. The relay selects which intercept and slope apply. Supply current is a non-negative magnitude, bounded by `max_current`. A target must be compared with the chosen branch's intercept. In the overlap where both branches can reach the target, the existing polarity is retained. If the branch intercepts leave a narrow unreachable interval, the current branch is held at zero current while the target lies in that interval; its modeled field is the selected branch's intercept. No calibration values are averaged.

`negative_state: true` explicitly preserves the established electrical convention for every axis: HIGH means reversed/negative coil contribution, LOW means original/positive coil contribution. No axis-specific inversion is inferred.

GUI field values and Zero Field scan values remain in Gauss. Their existing conversion to backend mT is exactly once (`value_G * 0.1`); calibration values remain in mT/A and mT.

## Channel audit before modification

The audit inspected configuration, runtime sequence construction, generic Pulse Streamer methods, GUI consumers, and all Python digital-output/pulse call sites without connecting to hardware.

| Channel | Uses found before the change | Disposition |
| --- | --- | --- |
| 3 | `Helmholtz.flip_direction.switch`; `Magnet._sync_positive_polarity()` and `_apply_global_polarity()`; relay fixture maps and assertions in `tests/test_magnet_scpi_traffic.py`, `tests/test_zero_field_timing.py`, and `tests/test_pulse_streamer_single_shot.py`; historical manual scripts listed below | Becomes Z's independent relay. The global configuration and control assumption are retired. |
| 6 | `pulseGenerator` named `detector2`; root-level orphan EventCamera `classname`/`type`/`address`/`Switch` fields; unused fixture entry in `tests/test_mw_switch_gating.py`, `tests/test_odmr_external_acquisition.py`, `tests/test_zero_field_software_acquisition.py`, and `tests/test_pulse_streamer_single_shot.py`; README table and Pulse Streamer reset docstring | Stale camera assignment removed, including the orphan root fields. The active Andor `spcm` configuration remains intact. Channel 6 belongs to X. |
| 7 | No digital configuration, pulse construction, or output call site | Assigned to Y. |

`HardwareManager.initialize()` reads the active camera configuration only from `spcm`; nothing consumes the orphan root EventCamera keys, and no EventCamera driver exists. The fixture `detector2` entries were never used to construct pulses. `ODMRExperiment.build_sequence()` and `build_repeated_off_on_sequence()` use only `greenLaser`, `MW`, and `detector` (channels 1, 2, and 5). The standalone sequencer demonstration uses channels 0, 1, and 2. No active non-magnet owner of channels 3, 6, or 7 was found.

The final active digital map has distinct owners: green laser 1, microwave switch 2, camera detector 5, X relay 6, Y relay 7, and Z relay 3.

Historical manual hardware scripts still contain direct channel 3 operations:

- `tests/test_swabian_relay.py`: direct `Sequence.setDigital(3, ...)` HIGH/LOW writes (pre-change lines 8 and 14).
- `tests/test_swabian_ttl3.py`: direct LOW/HIGH/LOW writes (lines 7, 13, and 19).
- `tests/test_persistent_outputs.py`: `set_digital_output(3, True/False)` (lines 44 and 58).
- `tests/test_odmr_backend.py`: `set_digital_output(3, True)` and corresponding readback assertion (lines 55 and 112).

These files are excluded by `tests/conftest.py`'s hardware-free allowlist and were not executed or modified. Their channel 3 operations now refer only to Z. They bypass safe per-axis magnet control; review and establish zero coil current before any deliberate manual use. `pytest --hardware`, including collection alone, imports scripts that can command hardware.

The generic real Pulse Streamer driver merges cached persistent outputs into compiled sequences, final states for finite runs, and normal reset states. `sync_outputs()` transmits the full persistent map while preserving the other relays' commanded values. Shutdown must de-energize coils before any operation that forces relay channels LOW. `SimPulseStreamer` already stores states by digital channel and supports all three independently; it adds no synthetic magnetic-field or Zero Field physics.

## Safe axis-local switching and state

Automatic polarity selection is part of calibrated field control. An actual polarity transition zeroes the affected axis's current, disables its output, waits for current decay, changes only that axis's relay state, waits for relay settling, configures the positive target current, and restores output as required by the existing enabled state. The other axes retain their current settings and relay states.

At startup, relay states and supply setpoints are unknown. `Magnet.enable()` first zeroes and disables **every axis with an unknown relay**, waits for discharge, synchronizes those relays to their positive branch in one shared output update, waits for settling, and enables outputs. This coordination matters because Pulse Streamer synchronization sends the entire cached output map. Initial nonzero coil commands through `set_vector()` or a magnet-owned axis require `Magnet.enable()` first. After initialization, re-enabling preserves known polarity, and individual field transitions remain axis-local. Fields display Unknown before any successful command or after a command failure.

The normal reversal sequence is `set_current(0)` → SCPI pacing → `output_off()` → discharge wait (at least the SCPI pacing interval) → selected relay cache update plus `sync_outputs()` → relay settling → `set_current(I)` → SCPI pacing → `output_on()` if the output was previously enabled. Vector control also retains its post-write pacing; repeated cached points do not sleep. On a transition error the affected axis attempts output-off and invalidates its command cache. Shutdown attempts every axis even if one fails; failed enable attempts roll back to shutdown. A transport error cannot prove that a physical output switched off, so errors are surfaced to the caller.

Each axis has separate configured `discharge_delay_s` and `settle_delay_s`, initially 0.05 seconds each. These are short, open-loop starting values, not measured guarantees that coil current has decayed or contacts have settled. Verify both physically at the rig and adjust them before relying on repeated reversals. Existing `Helmholtz.scpi_write_delay_s` remains a separate serial-command pacing control.

Current-write suppression is retained for unchanged confirmed commands on the same polarity branch. Target field, current magnitude, and polarity are distinct state. Equal magnitudes on opposite branches require a real transition. Relay/output changes invalidate relevant current assumptions, and command failures must not be cached as success. Cached/commanded values are not measurements of magnetic field or coil current.

## Electrical zero and calibrated magnetic zero

`Magnet.zero()` preserves its electrical-zero meaning: command 0 A through the coils. With nonzero intercepts this corresponds to the configured background field, not a calibrated total field of zero. De-energizing coils must not label the cached field as measured or commanded magnetic zero.

`Magnet.set_vector(0, 0, 0)` requests calibrated magnetic zero through the ordinary field algorithm. With the current directional calibration, the calculated commands are:

| Axis | Selected negative ratio (mT/A) | Selected negative offset (mT) | Compensation current (A, rounded) | Polarity |
| --- | --- | --- | --- | --- |
| X | 0.886743 | 0.012819 | 0.0144562743 | Reversed, HIGH |
| Y | 0.740600 | 0.006233 | 0.0084161491 | Reversed, HIGH |
| Z | 0.800057 | 0.117748 | 0.1471745138 | Reversed, HIGH |

These currents are illustrative calculations from `(B0_negative - 0) / k_negative`, not constants used by control code.

For a Zero Field scan that requests X and Y at 0 G while sweeping Z, X and Y remain on their independent compensation branches. Crossing Z's calibration intercept only switches Z. The same rule applies to scans along X or Y. Camera acquisition ownership, held-open acquisition, and the choice of whether to preserve or zero non-swept targets are unchanged.

## GUI and implementation files

The Magnet window replaces the global manual flip button with read-only X/Y/Z coil-polarity labels. Its selected-axis configuration displays that axis's relay channel. Manual fields, vectors, offsets, and Zero Field scans remain in G (ratio display remains G/A). Magnetic Zero calls `set_vector(0,0,0)` while enabled; OFF calls `disable()`. Refresh explicitly identifies commanded/model values, and unknown state displays Unknown. No measured magnetic field is claimed.

| File | Purpose |
| --- | --- |
| `config/setupInfo.json` | Independent relay configuration and unique digital owners; calibration numbers preserved. |
| `hardware/magnet/magnet.py` | Signed vectors, coordinated safe startup, electrical zero, axis-local orchestration, channel conflict checks, retired global control. |
| `hardware/magnet/magnet_axis.py` | Background-intercept calibration, positive supply current, independent relay/cache state, safe transitions and error handling. |
| `gui/magnet_window.py` | Automatic polarity UI, magnetic-zero command, G units, commanded-state display. |
| `experiments/zero_field_experiment.py` | Update obsolete comments; scan conversion and acquisition logic unchanged. |
| `hardware/pulse_streamer/swabian_pulse_streamer.py` | Documentation names all three persistent relays; driver behavior unchanged. |
| `README.md` | Current channel map and automatic-polarity description. |
| `CLAUDE.md` | Current architecture and cache guidance. |
| `docs/magnet_polarity_architecture.md` | Audit, semantics, file inventory, and validation record. |
| `tests/conftest.py` | Allowlist the new hardware-free polarity suite. |
| `tests/test_magnet_independent_polarity.py` | Calibration, every sign combination, all-axis locality, exact switching sequence, startup, failures, and scan-unit tests. |
| `tests/test_magnetic_field_gui_units.py` | Preserve existing G-unit tests; update signed polarity, magnetic zero, OFF, and unknown-state assertions. |
| `tests/test_magnet_scpi_traffic.py` | Preserve cache/pacing/error-queue regressions with local reversal counts. |
| `tests/test_pulse_streamer_single_shot.py` | Verify independent relay persistence through real-driver mocks, finite runs, reset, and ODMR playback. |
| `tests/test_zero_field_timing.py` | Per-axis relay timing and updated configuration fixtures. |
| `tests/test_mw_switch_gating.py` | Replace stale detector2 fixture with explicit relay owners. |
| `tests/test_odmr_external_acquisition.py` | Replace stale detector2 fixture with explicit relay owners. |
| `tests/test_zero_field_software_acquisition.py` | Replace stale detector2 fixture with explicit relay owners. |
| `tests/test_zero_field_window_save_toggle.py` | Mock its existing modal warning so unattended regression validation can complete; production behavior unchanged. |

Power-supply drivers and simulator implementations did not require functional changes. Existing working-copy calibration and Gauss-unit edits were preserved; unrelated working files were not edited.

## Hardware-free validation

Run from the workspace with `QT_QPA_PLATFORM=offscreen`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider -o faulthandler_timeout=30
```

The final full allowlisted run after the directional calibration update completed with **322 passed, 1 skipped, and 18 subtests passed** (19.63 seconds, exit code 0). The skip is the existing POSIX permission test on Windows. The focused magnet, GUI, scan-timing, and Pulse Streamer suites passed 122 tests and 18 subtests. Pyqtgraph emitted deleted-QComboBox cleanup tracebacks during interpreter shutdown after the passing test result; pytest exited successfully. Real-hardware scripts were never collected or executed.

## Manual validation after code review

- Confirm physical coil/relay wiring matches X=6, Y=7, Z=3 and HIGH reverses each axis relative to its calibration branch.
- Measure current decay and relay settling at representative currents; validate or adjust the initial 50 ms discharge/settle delays.
- Check that reversals interrupt only the selected axis and that the supplies report no SCPI errors.
- Measure the compensated field and a scan crossing each intercept; simulation verifies control flow only.
