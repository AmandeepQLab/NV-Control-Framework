# Exposure-minimum fix — session report

Date: 2026-09-04
Branch: `dev`

## Request

The main-window exposure spinbox could not be set below 1 ms, even though
the Andor Neo 5.5 supports much shorter exposures. Task: investigate
read-only first (report findings), then propose and — on approval —
implement a fix, verified in sim mode only (real-rig verification left to
the user).

## Investigation findings

**a) Root cause — confirmed.** [gui/main_window.py](../gui/main_window.py)'s
exposure spinbox had a hardcoded floor:
```python
self.exposure_spin.setRange(0.001, 10)   # seconds
self.exposure_spin.setDecimals(4)
```
The 1 ms floor came from the range's own minimum, not the decimals (4
decimals would already resolve to 100 µs).

**b) Nothing downstream clamped it.** `AcquisitionState` (frozen dataclass,
no validation), `AndorNeoAndor3.set_exposure()`/`configure_camera()`, and
`SimCamera.set_exposure()` all passed the value through unclamped. The GUI
spinbox was the only floor anywhere in the chain.

**c) SDK support exists but was unused.** The installed `andor3` wrapper
(`.venv/Lib/site-packages/andor3/andor3.py`) exposes `getFloatMin()` /
`getFloatMax()`, mapped to the real SDK3 calls `AT_GetFloatMin`/
`AT_GetFloatMax`. A repo-wide grep for `FloatMin|FloatMax` found zero
call sites — the driver only ever read back what the SDK *had just
accepted* via `getFloat()`, never queried the legal range up front.
Caveat recorded for the rig: exposure is set (in `AcquisitionState.apply_to()`)
*before* ROI/binning, so a queried min/max reflects whichever AOI was
active before the most recent ROI change, not necessarily the one about
to be used — relevant because sCMOS exposure minimums can be row-time
(ROI-height) dependent, though `ElectronicShutteringMode = "Global"` may
reduce that coupling; unverified without hardware.

**d) Drift-epsilon check no longer made sense at sub-ms exposures.**
`experiments/odmr_experiment.py`'s `_EXPOSURE_DRIFT_EPSILON_S = 100e-6`
(fixed absolute 100 µs) becomes comparable to the exposure itself at
~100 µs, making the check nearly blind.

**e) camera_gate_s and the Zero-Field stale-buffer guard were already fine.**
`camera_gate_s` is computed in integer nanoseconds with no ms-granularity
rounding. The ZFE stale-buffer guard (`min_plausible_s = 0.5 *
self.exposure_time`, in `andor_neo_andor3.py`/`sim_camera.py`) is already
proportional to exposure, not a fixed epsilon. Neither needed a change.

## Decisions (user-confirmed)

- Exposure spinbox display switched from seconds to **milliseconds**
  (`exposure_s` stays in seconds everywhere else — conversion happens only
  at the GUI boundary).
- Drift epsilon made a **proportional cap**:
  `max(20µs, min(100µs, 0.2 × requested_exposure_s))` — the floor was
  raised from an initially-proposed 10µs to 20µs on user feedback, since
  the measured ~3µs quantization noise is fixed, not proportional, so the
  floor must stay comfortably above it at *any* exposure, not just small
  ones.
- `get_exposure_limits()` must be **non-fatal**: it's queried in
  `MainWindow.__init__` while building the spinbox, and the camera has
  been observed in states where an SDK call can raise — a raise there must
  not prevent the app from starting.

## Implementation

| File | Change |
|---|---|
| [hardware/camera/andor_neo_andor3.py](../hardware/camera/andor_neo_andor3.py) | Added `get_exposure_limits()` — returns `(cam.getFloatMin("ExposureTime"), cam.getFloatMax("ExposureTime"))`. Raises on SDK failure (caller's responsibility to catch). |
| [hardware/camera/sim_camera.py](../hardware/camera/sim_camera.py) | Added matching `get_exposure_limits()` stub returning a fixed `(1e-6, 10.0)` s (no real hardware floor to query). |
| [gui/main_window.py](../gui/main_window.py) | Spinbox range now queried from `self.camera.get_exposure_limits()` at construction, wrapped in `try/except` — falls back to the old `(0.001, 10)` s range and prints a named warning on failure. Label changed to "Exposure (ms):", decimals raised to 3 (1 µs resolution). `exposure_s = spinbox_value_ms / 1000.0` at both conversion points (`__init__` and the `set_exposure` slot). |
| [experiments/odmr_experiment.py](../experiments/odmr_experiment.py) | `_EXPOSURE_DRIFT_EPSILON_S` (100 µs) is now the cap, not the epsilon itself; new `_drift_epsilon_s(requested_exposure_s)` classmethod implements `max(20e-6, min(100e-6, 0.2 * requested_exposure_s))`. Unchanged (100 µs) at the validated ms-scale working point. |
| [tests/test_baseline_correction.py](../tests/test_baseline_correction.py) | Updated `test_set_exposure_preserves_baseline_counts` for the new unit contract — `MainWindow.set_exposure()`'s parameter is now milliseconds, not seconds. |

## Sanity check (sim mode)

```
SimCamera.get_exposure_limits() reports (s): (1e-06, 10.0)
Spinbox range shown (ms):                    (0.001, 10000.0)
Default spinbox value: 10.0 ms  ->  exposure_s stored = 0.01
```
Matches the pre-change default (`0.01` s) exactly, just in ms display units.

## Test results

- `pytest tests/` (no `--hardware`, per this repo's hardware-safety gate):
  **235 passed, 1 skipped**, including `test_exposure_propagation.py`'s
  `ExposureDriftWarningTests` (unchanged pass/fail behavior confirmed at
  the ms-scale working point) and the `MainWindow.__init__`
  source-inspection tests.
- No `--hardware` tests were run.

## Outstanding — needs rig confirmation, not done here

- What `getFloatMin("ExposureTime")` actually returns on the real Andor
  Neo 5.5 (expected near the ~10 µs nominal spec — unverified).
- Whether that minimum shifts with ROI height given
  `ElectronicShutteringMode = "Global"`. The current query reflects
  whichever AOI is active at spinbox-construction time (typically full
  sensor); re-querying after ROI changes was intentionally left as a
  follow-up, not implemented.

## Unrelated finding — flagged, not acted on

After implementation, the working tree for this change was found already
committed **and pushed to `origin/dev`** as commit `f6479dd`
("Query exposure limits from SDK; display exposure in ms; scale drift
epsilon with exposure"), authored as `unknown <Bargill.lab@Setup2>` — no
`Co-Authored-By: Claude Sonnet 5` attribution trailer, and it swept in
unrelated untracked files that were already sitting in the working tree
at session start (`PROJECT_STATUS_REPORT.md` and 9 `*.csv` result files:
`odmr_0G.csv`, `odmr_10G.csv`, `odmr_20G.csv`, `odmr_30G.csv`,
`sweep_fwd.csv`, `sweep_null_1.csv`, `sweep_null_2.csv`,
`sweep_null_3.csv`, `sweep_rev.csv`).

This commit/push was **not performed by this assistant in this session** —
no `git commit`/`git push` tool call was made. The author identity and
missing attribution suggest either an IDE autosave/auto-commit feature, or
an action taken in an earlier, now-summarized turn. No corrective action
(amend, force-push, or history rewrite) has been taken — that would be
irreversible-adjacent and wasn't clearly authorized. Options if you want
it cleaned up: leave as-is; add a follow-up commit that untracks the
incidental files (`git rm --cached`, no history rewrite); or amend +
force-push for proper attribution (only safe if nobody else has fetched
`origin/dev` since).
