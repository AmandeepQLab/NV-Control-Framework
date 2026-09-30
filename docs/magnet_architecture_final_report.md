# NV Control Framework — Magnet Architecture Final Report

Date: 2026-09-29

The magnet architecture supports three independent X/Y/Z polarity relays and calibrated signed field requests, with a separate measured calibration on each axis's positive and negative branch. Implementation and validation accessed no physical hardware. The final hardware-free suite completed with **322 passed, 1 skipped, and 18 subtests passed**.

The current calibration numbers were preserved exactly. Power-supply drivers, calibration units, GUI and scan units, and the held-open camera/acquisition architecture were preserved.

## Architecture Before

`Magnet` selected one global polarity using digital channel 3, rejected mixed-sign vectors, and passed absolute field values to all three `MagnetAxis` objects before calibration. This prevented independent coil reversal and could produce negative calculated supply currents when the requested field magnitude was below a positive calibration offset.

## Architecture After

```text
Magnet
  ├─ MagnetAxis X → X supply → X polarity relay ch 6 → X coil
  ├─ MagnetAxis Y → Y supply → Y polarity relay ch 7 → Y coil
  └─ MagnetAxis Z → Z supply → Z polarity relay ch 3 → Z coil
```

The axes share the Pulse Streamer but own independent relay configuration and cached polarity state.

| Axis | Supply | Relay channel | Channel-map name | Configuration |
| --- | --- | --- | --- | --- |
| X | E3632A, COM13 | 6 | `flipMF_X` | `Helmholtz.X.flip_direction` |
| Y | E3631A, COM12, P6V | 7 | `flipMF_Y` | `Helmholtz.Y.flip_direction` |
| Z | E3632A, COM11 | 3 | `flipMF_Z` | `Helmholtz.Z.flip_direction` |

`Magnet.set_vector(bx, by, bz)` forwards signed total-field targets to the axes. Each `MagnetAxis` independently selects its relay branch and non-negative supply current. Mixed-sign vectors are accepted. The old global polarity mutators are retired and raise an explanatory error instead of flipping all axes.

## Field Equation

Each axis uses the calibration selected by its polarity relay:

```text
positive: B_target = B0_positive + k_positive * I
negative: B_target = B0_negative - k_negative * I
```

- `B_target` and each branch's `B0` are in mT.
- Each branch's `k` is a positive calibration magnitude in mT/A.
- Supply current must satisfy `0 <= I <= max_current`.
- The requested target is compared with the selected branch's intercept, not by the target's sign.
- When both branches can reach a target, the existing relay branch is retained.
- Y's measured offsets leave a narrow unreachable interval. In that interval, the current relay branch is held at zero current and its intercept is reported as the modeled field.
- Within `1e-9 mT` of a branch intercept, current is zero.
- Invalid calibration, non-finite targets, and negative calculated current are rejected.

The exact `setupInfo.json` calibration structures are:

```json
"X": {
  "magnetic_field_calibration": {
    "positive": {"magnetic_field_ratio": 0.887629, "magnetic_field_offset": 0.012076},
    "negative": {"magnetic_field_ratio": 0.886743, "magnetic_field_offset": 0.012819}
  }
},
"Y": {
  "magnetic_field_calibration": {
    "positive": {"magnetic_field_ratio": 0.741657, "magnetic_field_offset": 0.006619},
    "negative": {"magnetic_field_ratio": 0.740600, "magnetic_field_offset": 0.006233}
  }
},
"Z": {
  "magnetic_field_calibration": {
    "positive": {"magnetic_field_ratio": 0.799629, "magnetic_field_offset": 0.117176},
    "negative": {"magnetic_field_ratio": 0.800057, "magnetic_field_offset": 0.117748}
  }
}
```

Each vector is validated before its axis writes begin. An equal current magnitude requiring the opposite relay polarity is treated as a physical transition, not a cache hit. Unchanged commands on the same branch retain current-write suppression and avoid unnecessary pacing sleeps.

`negative_state: true` explicitly preserves the verified convention: **LOW selects the positive/original coil branch; HIGH selects the reversed branch**.

## Pulse Streamer Channel Audit

The audit inspected configuration and digital-output/pulse call sites before implementation. No active conflicting owner remains.

| Channel | Previous uses and findings | Final disposition |
| --- | --- | --- |
| 3 | Global magnet relay configuration and global switching methods; hardware-free relay tests; direct operations in the four manual scripts listed below | Z polarity relay; global control assumption removed |
| 6 | `detector2` channel-map entry; unused root-level EventCamera configuration; unused detector2 entries in test fixtures; stale documentation | Stale declarations removed; X polarity relay |
| 7 | No prior digital assignment or output call site found | Y polarity relay |

The active camera configuration is read from `spcm`. No EventCamera driver or consumer of the orphan root fields exists. Removing the unused EventCamera declaration leaves the Andor configuration intact.

Runtime ODMR sequence builders use `greenLaser`, `MW`, and `detector` on channels 1, 2, and 5. The standalone sequencer demonstration uses channels 0, 1, and 2. The final active digital map has one owner per channel:

| Channel | Owner |
| --- | --- |
| 1 | Green laser |
| 2 | Microwave switch |
| 3 | Z polarity relay |
| 5 | Camera detector |
| 6 | X polarity relay |
| 7 | Y polarity relay |

Direct channel-3 operations remain in historical manual hardware scripts:

- `tests/test_swabian_relay.py`: direct HIGH/LOW sequence writes.
- `tests/test_swabian_ttl3.py`: direct LOW/HIGH/LOW sequence writes.
- `tests/test_persistent_outputs.py`: persistent channel-3 updates.
- `tests/test_odmr_backend.py`: channel-3 relay update and assertion.

These scripts were not executed or modified. Their channel-3 operations now affect Z only and bypass calibrated safe switching. They remain excluded by the hardware-free pytest allowlist.

Stale detector2 fixtures were replaced in the microwave-switch, ODMR external-acquisition, Zero Field software-acquisition, and Pulse Streamer single-shot tests. Generic Pulse Streamer compilation, finite-run final states, and normal resets preserve all three independently cached relay outputs.

## Safe Switching

A normal polarity reversal performs the following sequence on the affected axis:

1. Command `set_current(0.0)`.
2. Wait for SCPI pacing.
3. Command `output_off()`.
4. Wait for discharge, for at least the configured SCPI pacing interval.
5. Update only the selected relay's cached state and call `sync_outputs()`.
6. Wait for relay settling.
7. Program the required non-negative current magnitude.
8. Wait for SCPI pacing.
9. Restore output if it was previously enabled.

Other axes retain their current settings, outputs, relay states, and valid current caches. `sync_outputs()` sends the shared cached map, preserving the other relay values. Vector control retains its post-write pacing.

Each axis has configurable `discharge_delay_s` and `settle_delay_s`, both initially **0.05 seconds**. These are open-loop starting values requiring physical verification.

Startup requires additional coordination because a new software cache is not a physical relay readback. `Magnet.enable()` zeros and disables every axis with unknown relay state before any shared relay synchronization, waits for discharge, initializes those relays, waits for settling, and then enables outputs. Initial nonzero coil commands require this initialization first.

Transition failures invalidate the affected command cache and attempt output-off. Shutdown attempts every axis even if one fails; failed enable attempts roll back to shutdown. Transport errors are surfaced and cannot establish whether a physical command actually completed.

## Magnetic Zero

`Magnet.set_vector(0, 0, 0)` requests calibrated magnetic zero through the ordinary field-setting algorithm. With the preserved setup calibration:

| Axis | Selected negative ratio (mT/A) | Selected negative offset (mT) | Compensation current (A) | Polarity |
| --- | --- | --- | --- | --- |
| X | 0.886743 | 0.012819 | 0.0144562743 | Reversed / HIGH |
| Y | 0.740600 | 0.006233 | 0.0084161491 | Reversed / HIGH |
| Z | 0.800057 | 0.117748 | 0.1471745138 | Reversed / HIGH |

These magnitudes are calculated from `(B0_negative - 0) / k_negative`; they are not hard-coded into control logic.

`Magnet.zero()` remains backward-compatible electrical zero: all coil currents become 0 A, while cached model fields become their background offsets. `electrical_zero()` exposes that meaning explicitly. `disable()` also switches outputs off. Electrical zero and calibrated magnetic zero are distinct.

Cached fields and currents are commanded/model state, not measurements. Uncommanded or failed state displays Unknown.

## GUI

The Magnet window replaces the unsafe global flip control with read-only X/Y/Z polarity status. The selected axis displays its own relay configuration.

All field inputs, vectors, offsets, and Zero Field scan values remain in Gauss; the displayed calibration ratio remains G/A. Manual input converts once with `value_G * 0.1` before calling the backend. Backend calibration remains mT/A and mT.

The Magnetic Zero button requests `set_vector(0,0,0)` while enabled. The power OFF control de-energizes the coils. Refresh labels identify commanded/model values rather than measured field.

## Zero Field

The existing Gauss-to-mT scan conversion remains exactly once. When Z is swept with X and Y requested at 0 G, X and Y retain independently calculated compensation currents. Crossing Z's calibration intercept changes only Z's relay and output sequence. The same behavior applies to X or Y scans.

Acquisition ownership, held-open acquisition, and the existing choice to preserve or zero non-swept targets are unchanged. Simulator implementations already support independent persistent relay states; no synthetic magnetic-field or Zero Field physics was added.

## Files Changed

| File | Purpose |
| --- | --- |
| `config/setupInfo.json` | Independent relay configuration; unique channel ownership; remove stale EventCamera declaration; preserve calibration |
| `hardware/magnet/magnet.py` | Signed vector orchestration, safe startup, electrical zero/shutdown, channel-conflict validation, retirement of global control |
| `hardware/magnet/magnet_axis.py` | Intercept calibration, positive current magnitudes, independent relay/cache state, safe transitions and error handling |
| `gui/magnet_window.py` | Automatic polarity UI, magnetic zero, Gauss units, commanded-state display |
| `experiments/zero_field_experiment.py` | Correct obsolete comments only; conversion and acquisition logic unchanged |
| `hardware/pulse_streamer/swabian_pulse_streamer.py` | Update relay documentation only; driver behavior unchanged |
| `README.md` | Update channel map and architecture description |
| `CLAUDE.md` | Update architecture, startup, caching, and simulator guidance |
| `docs/magnet_polarity_architecture.md` | Detailed audit, implementation semantics, and validation record |
| `docs/magnet_architecture_final_report.md` | This standalone final report |
| `tests/conftest.py` | Allowlist the new hardware-free polarity suite |
| `tests/test_magnet_independent_polarity.py` | Calibration, sign combinations, relay locality, sequencing, startup, failures, and scan-unit tests |
| `tests/test_magnetic_field_gui_units.py` | Preserve Gauss checks and update polarity, zero, OFF, and unknown-state assertions |
| `tests/test_magnet_scpi_traffic.py` | Cache, pacing, error-queue, and local-reversal regressions |
| `tests/test_pulse_streamer_single_shot.py` | Independent relay persistence through finite runs, resets, and ODMR playback |
| `tests/test_zero_field_timing.py` | Per-axis timing assertions and configuration fixtures |
| `tests/test_mw_switch_gating.py` | Replace stale detector2 fixture with explicit relay owners |
| `tests/test_odmr_external_acquisition.py` | Replace stale detector2 fixture with explicit relay owners |
| `tests/test_zero_field_software_acquisition.py` | Replace stale detector2 fixture with explicit relay owners |
| `tests/test_zero_field_window_save_toggle.py` | Mock an existing modal warning that blocked unattended regression runs; no production change |

Existing working-copy calibration and Gauss-unit edits were preserved. Unrelated working files were not edited. Power-supply drivers and simulator implementations required no functional changes.

## Tests

The focused suites include hardware-free checks for both measured calibration branches on each axis, every sign combination, relay isolation, calibrated zero, the exact switching order, unchanged-current suppression, invalid-current rejection, startup ordering, injected failures, and scan-unit conversion.

The final complete hardware-free run used:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider -o faulthandler_timeout=30
```

Result: **322 passed, 1 skipped, 18 subtests passed in 19.63 seconds; exit code 0.**

The skip is an existing POSIX permission test that does not apply to Windows. Pyqtgraph emitted deleted-QComboBox cleanup tracebacks during interpreter shutdown after the passing test result; pytest exited successfully. `git diff --check` passed.

No `--hardware` collection or physical hardware tests were run. The results above are from implementation validation; creating this report did not rerun tests or access hardware.

## Remaining Hardware Validation

After reviewing the code, perform these physical checks manually:

1. Confirm X=6, Y=7, Z=3 wiring and HIGH=reversed on every axis.
2. Measure coil-current decay and relay settling at representative currents; validate or adjust the 50 ms defaults.
3. Verify reversals leave other axes unchanged and that supplies report no SCPI errors.
4. Measure compensated magnetic zero and scans crossing each calibration intercept.
