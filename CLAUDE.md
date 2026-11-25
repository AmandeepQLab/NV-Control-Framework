# CLAUDE.md

Assistant-facing guide to this repository. Last verified against the code: 2026-08-10.

This file documents how the code actually works, not how it was originally intended to work — every claim below was checked against the current source, not carried forward from comments, commit messages, or prior documentation. If you change something this file describes, update this file in the same change.

## Conventions

- **Every user-facing control must have a tooltip explaining what it does, its units, and when to use it.** (Standing convention, from 2026-11-24. Controls written before then are not all compliant; new and changed controls must be. `tests/test_stability_check.py` checks it for the stability-check controls.)

## What this is

NV Control Framework — Python/PyQt6 control software for nitrogen-vacancy (NV) center quantum sensing experiments in diamond. Currently drives: an SRS SG386 microwave source (VISA/TCP), a Swabian Pulse Streamer 8/2 (vendor `pulsestreamer` SDK), an Andor Neo 5.5 sCMOS camera (`andor3` SDK3 wrapper), and a Helmholtz electromagnet built from Keysight/HP E3631A/E3632A power supplies over serial (SCPI). Real ODMR and Zero Field magnetometry experiments run against this hardware today.

## Architecture and call chain

The actual chain, end to end:

```
GUI Window  →  QThread Worker  →  Experiment (ScanExperiment/BaseExperiment)  →  injected hardware object  →  device SDK
```

Concretely: `gui/odmr_window.py` → `gui/odmr_worker.py` (`QThread`) → `experiments/odmr_experiment.py::ODMRExperiment` → `hw["camera"]`/`hw["microwave"]`/`hw["pulse_streamer"]` (whichever concrete objects `HardwareManager` built) → the vendor SDK. Same shape for `gui/zero_field_window.py` → `gui/zero_field_worker.py` → `experiments/zero_field_experiment.py::ZeroFieldExperiment`.

**`controller/experiment_runner.py` is dead code.** It is imported by exactly one file in the whole repo — `tests/test_odmr.py` — and by nothing in the GUI. Do not route new code through it and do not treat it as a real architectural layer; the GUI workers construct and run experiments directly.

**Two places the GUI reaches hardware directly, and this is intentional, not a layering violation:**
- `gui/magnet_window.py` (`MagnetControlWindow`) — manual coil jogging has no "experiment" concept to route through, so it imports `hardware.magnet.magnet.POSITIVE`/`NEGATIVE` and calls `Magnet`/`MagnetAxis` methods directly. These direct commands are locked while any experiment runs — see "Magnet control is locked during experiments" below.
- `gui/main_window.py`'s live view — `self.camera.get_latest_frame()`/`stop_stream()` are called directly on the driver object for the live preview, since there's no experiment wrapping a live display either. ROI/exposure/binning changes for live view *do* go through `framework.acquisition_state.AcquisitionState` and `framework.camera_ownership`, not raw SDK calls. The count-statistics panel under the live view is **not** a third such place: it only receives the frames `update_image()` already fetched (see "Live count statistics").

**Invariant: experiments never import a concrete `hardware.*` driver class.** `experiments/odmr_experiment.py` and `experiments/zero_field_experiment.py` only call methods on hardware objects handed to them through their constructors (a `hardware` dict for ODMR, including `hardware["magnet"]` when present; explicit `camera`/`magnet` args for Zero Field). Neither has an `import hardware.camera...` or `import hardware.microwave...` line. If you're adding experiment logic and find yourself importing a concrete driver, that's a sign the object should be injected instead — this is what keeps `--sim` and real hardware interchangeable without touching experiment code.

**Camera contention is handled, not ignored.** Any code acquiring images through an experiment must hold `framework.camera_ownership.exclusive_camera_access(camera)` around the whole acquisition (both `ODMRExperiment.run()` and `ZeroFieldExperiment.run()` already do this). It suspends the registered live-view `QTimer` and stops streaming for the duration, then restores both — including on exception. `gui/main_window.py` registers itself via `register_live_view_controller()`/`register_camera_state_restorer()` at startup; don't add a second, unregistered code path that calls `camera.snap()` or `start_stream()` directly.

**MW on/off is gated by a fast switch, not by changing SG386 amplitude.** `ODMRExperiment` gates MW via the ZASWA-2-50DRA+ switch on Pulse Streamer channel 2 (`build_sequence()`'s `mw_on` argument), not by writing the SG386's power per frame — the SG386 stays at one power for the whole scan point. Measured at the rig: the switch's off-state isolation is better than the SG386's own at −100 dBm, and switching to it improved measured ODMR contrast by ~10%. **Do not reintroduce per-frame `set_power()` calls** to gate MW — that regime was deliberately removed (see `experiments/odmr_experiment.py::acquire_frame()`).

**Camera acquisition is held open across a whole ODMR scan, not re-armed per frame.** `ODMRExperiment.begin_camera_acquisition()`/`end_camera_acquisition()` arm/disarm the camera once per scan; `acquire_triggered_frame()` then just waits on the already-armed camera (`grab_external_frame()`) instead of each frame doing its own arm/disarm. The timing-diagnostics CSV header records `acquisition_start_count` specifically so a silent regression back to per-frame arming is visible — it should read 1 per scan; a value equal to the frame count means the held-open path silently fell back.

**Pulse sequences fire single-shot, and persistent digital outputs (including all three magnet polarity relays) survive both firing and reset.** `SwabianPulseStreamer.run()` is called with `n_runs=1` for triggered frames so exactly one gate fires per frame — looping sequences (the vendor SDK's own default) previously let the camera capture stray repetitions. `run()`'s `final` state defaults to the *current* `persistent_outputs` dict, not the SDK's all-zero default, preserving the independently commanded X/Y/Z relay states on channels 6/7/3. `reset_outputs()` likewise holds each channel at its persistent value. `close()` (`preserve_persistent=False`) forces every channel LOW; coil current must already be zero and supply outputs disabled before closing the Pulse Streamer.

**Magnet polarity and calibration are independent per axis and direction.** `Magnet.set_vector()` sends signed total-field targets to `MagnetAxis` X/Y/Z; each axis owns `Helmholtz.<axis>.flip_direction` (X relay 6, Y 7, Z 3) and `magnetic_field_calibration.positive/negative`. Each branch has its own mT/A slope and mT intercept: `B = B0_positive + k_positive*I` or `B = B0_negative - k_negative*I`; supply current remains nonnegative. Branch selection checks the target against the selected branch intercept. Where both branches reach a target, the current relay branch is retained. Inside an unreachable intercept gap, current stays zero on the current branch and the model reports that branch's intercept. No calibration values are averaged. GUI/Zero Field values remain Gauss with one conversion to mT. `negative_state: true` preserves HIGH=reversed and LOW=original. Mixed-sign vectors are valid. `set_vector(0,0,0)` commands negative-branch compensation; `zero()` commands electrical 0 A. Cached fields are commanded/model values, never measurements. `set_vector()` returns a `frozenset` of the axis names whose relay flipped during the call (`frozenset()` if none). It is detected by comparing each axis's `direction` before and after `set_field()` — during `set_vector()` only `MagnetAxis._switch_polarity()` changes it — so `MagnetAxis` itself is unchanged. Callers that ignore the return value are unaffected. Safe relay transitions remain axis-local with 50 ms open-loop discharge and settling defaults requiring physical verification. See `docs/magnet_polarity_architecture.md` for the full channel audit.

**Exposure has a single source of truth: the Main Window spinbox.** `MainWindow.acquisition_state` is built from `self.exposure_spin.value()` (not `AcquisitionState`'s own dataclass default) and pushed to the camera once, explicitly, at the end of `__init__` — before any user interaction or experiment can run. `AndorNeoAndor3.set_exposure()`/`configure_camera()` then store what `cam.getFloat("ExposureTime")` reads back from the SDK, not the requested value. Before this, four independent exposure defaults existed (the `AcquisitionState` dataclass default, `AndorNeoAndor3`'s own `__init__` default, `SimCamera`'s `__init__` default, and the spinbox's initial value) and nothing pushed the GUI's displayed value to the camera until the user touched the spinbox — a scan run immediately after launch could silently use whatever the driver's own default was, while the GUI displayed something else entirely.

**Magnet control is locked during experiments — in the GUI layer only.** Neither `hardware/magnet/` nor `SerialDevice` has any lock, so a manual command from `MagnetControlWindow` mid-scan would interleave unpaced SCPI writes with the experiment's own on the same 9600-baud lines (the same class of bug as the 2026-09-13 overrun). The fix prevents the GUI from issuing them; **do not add locks, mutexes, or state flags to `hardware/magnet/` for this.** How it works:
- Each experiment class declares `MAGNET_IS_SWEPT`: `ZeroFieldExperiment.MAGNET_IS_SWEPT = True` (it commands the magnet every point), `ODMRExperiment.MAGNET_IS_SWEPT = False` (it sets the field at most once, at scan setup, then holds it; see "ODMR magnetic field during scan"). A new experiment class must declare it.
- `ODMRWindow` and `ZeroFieldWindow` emit `run_started` just before starting their worker thread and `run_finished` first thing in their worker-finished slot (`odmr_finished()`/`scan_finished()`), which the worker's finished signal reaches on every exit path (completion, stop, error).
- `MainWindow` tracks running experiments keyed by window and emits `experiment_started(name, magnet_is_swept)` / `experiment_finished()`. Overlapping runs (e.g. ODMR + Zero Field) re-emit `experiment_started` with the combined name and `any()` of the swept flags; `experiment_finished` fires only when none remain. A magnet window opened mid-run starts locked.
- `MagnetControlWindow.on_experiment_started()` disables the command buttons (power, Apply, Magnetic Zero); the handlers also refuse while locked. Readouts, Refresh, and the axis selector stay usable. An "Allow manual control during experiment" checkbox (visible only during a run) re-enables commands after a warning dialog whose text depends on `magnet_is_swept`; Cancel unchecks it. Every `experiment_started` re-locks and clears the override.
- `on_experiment_finished()` always unlocks, clears and hides the override, then runs the **post-experiment action** chosen in "After experiment:" (always enabled): "Field zero at diamond" → `magnet.set_vector(0, 0, 0)` (calibrated compensation, not 0 A) or "Electrical off" → `magnet.disable()`. It runs regardless of the override, but **only if the magnet is still enabled** — otherwise it is skipped silently. Consequences: after a Zero Field run the action never fires, because `ZeroFieldExperiment.cleanup_scan()` has already called `magnet.disable()`. After ODMR with the magnet on, the default action **replaces any manually set bias field with calibrated 0 G**. ODMR's own "Field zero at diamond" and "Custom vector" modes leave the magnet enabled, so this action always applies after them (a custom field is replaced by calibrated 0 G, or the coils are switched off with "Electrical off"). The action lives in `MagnetControlWindow`, so it only runs if that window has been opened (closing it only hides it, so a closed-but-created window still applies it).
  - The selector therefore applies only to static-field experiments (e.g. ODMR); after a Zero Field scan the magnet is always turned off by the experiment itself. Its tooltip says the same.
- During a Zero Field run, `ZeroFieldWorker.field_updated(x_mT, y_mT, z_mT)` emits per stored point: the swept axis's requested value plus `ZeroFieldExperiment._configured_field_mT` for the others (for a vector sweep, s·n̂ on all three axes) — commanded targets, not measurements or model fields. `ZeroFieldWindow.field_updated` relays it; `MainWindow._relay_zero_field_vector()` forwards it to `MagnetControlWindow.update_field_display()`, which shows it read-only in the vector box (retitled "live") and ignores it when no experiment is running.

**Closing the app never shuts hardware down under a live worker thread.** `MainWindow` is the close gatekeeper:
- **Worker registry.** `MainWindow._worker_threads` maps each live worker `QThread` to its experiment window. A thread is registered in `_experiment_window_started()` (the same `run_started` path that drives the magnet lock) via the window's `worker_thread()`, and removed by `_on_worker_thread_finished()` on `QThread.finished` — the thread really exiting, which is later than `run_finished` (the ZFE worker still writes its timing file after its finished signal). Every experiment window must implement `worker_thread()` and `request_stop()` (both `ODMRWindow` and `ZeroFieldWindow` do); a new experiment window that omits them fails loudly rather than escaping close safety. The finished slots are bound `MainWindow` methods, not lambdas, so they run on the GUI thread although `QThread.finished` is emitted from the worker thread.
- **`closeEvent()`.** With no live worker it asks the existing "Exit NV Control" confirmation and shuts down as before (`_shutdown_hardware()`). With one or more live workers it asks "Scan in progress" — "Stop and Close" (default) / "Cancel". Cancel ignores the close; the scan continues. "Stop and Close" ignores this close, calls `request_stop()` on every running window, shows "Stopping scan — please wait…" in the status bar, and connects each worker's `QThread.finished` to `_on_worker_finished_for_close()`, which calls `close()` again once the registry is empty; that second close skips the Exit confirmation (already confirmed) and shuts down. Nothing blocks the GUI thread.
- **10-second timeout** (`_CLOSE_WAIT_TIMEOUT_MS`). If workers haven't all exited by then, `_on_close_timeout()` logs a warning and forces the close (`_force_close`) — hardware shutdown then **can** race a still-running worker, which is the accepted cost of not hanging forever. A worker finishing after shutdown does not trigger a second shutdown (`_shut_down`).
- **Tool windows are closed on exit** (`_close_tool_windows()`), after the experiment windows and before `hw_manager.shutdown()`. Qt quits only when the last *visible* top-level window closes, and a visible magnet window used to keep the process running after Exit. Hardware shutdown had already completed by then; only the process stayed alive.
  - The magnet window is closed and `deleteLater()`'d, not just hidden. That also disconnects its `experiment_started`/`experiment_finished` slots, so a worker that outlives a forced close can't trigger the post-experiment magnet command on a magnet being shut down.
  - The ODMR analysis window is closed. While a pixel fit runs, its own `closeEvent()` defers the close until the fit ends, and the process quits then.
  - Reopening: closing the magnet window only hides it (it is kept so the post-experiment action still applies), so `open_magnet_window()` calls `show()` on the existing instance before raising it.
  - Tested by `tests/test_app_exit.py`, which runs the real app with `--sim` in a subprocess and requires it to exit by itself with the magnet window open, hidden, or never opened.
- **pyqtgraph registry cleared last.** `_shutdown_hardware()` ends with `gui/pyqtgraph_teardown.py::clear_view_registry()`, after experiment windows are closed and hardware is shut down. It empties pyqtgraph's process-wide `ViewBox.AllViews`/`NamedViews`. Otherwise a named ViewBox destroyed at Qt teardown makes pyqtgraph rebuild other views' context-menu combo boxes, which may already be deleted, and it prints `RuntimeError: wrapped C/C++ object of type QComboBox has been deleted`. Each `ImageView` has its own name (`"LiveView"`, `"ZeroFieldImage"`), not pyqtgraph's shared default `"ImageView"`. Measured: the test suite reproduced the error without the clear; app exit in `--sim` (Zero Field window open) was clean with and without it.
- **Zero Field run queue between runs.** No worker thread exists during a queue's pause, so `closeEvent()` also treats any window in `_running_experiments` whose `queue_active()` is True as busy (`_active_queue_windows()`) and shows the same "Scan in progress" dialog. "Stop and Close" calls `request_stop()` on such a window right away, which ends its queue synchronously; a queue with a run in progress is stopped through the normal worker path. Windows without `queue_active()` (ODMR) are unaffected. See "Zero Field run queue".
- **`ZeroFieldWindow.closeEvent()` / `ODMRWindow.closeEvent()`.** While a scan runs: request stop and ignore the close (the window stays open until the scan finishes). A Zero Field queue between runs is ended, then the close is accepted. Idle: accept, with no dialog — ODMR's former "Are you sure?" question on an idle window was removed, since `_shutdown_hardware()` closes these windows and a modal there would stall shutdown.

Magnet startup coordinates the shared Pulse Streamer output bank: every axis with unknown relay state is first zeroed and disabled before any relay synchronization. `Magnet.enable()` is required before initial nonzero coil commands. Ordinary later reversals remain local to one axis. Uncommanded or failed field state displays Unknown; transport failures invalidate the affected command cache, shutdown attempts all axes, and failed enable attempts roll back to shutdown.

## Hardware device selection

`hardware/hardware_manager.py::HardwareManager.initialize()` builds camera, microwave, and pulse-streamer objects by reading a `type` field from `config/setupInfo.json` (`spcm.type`, `frequencyGenerators[0].type`, `pulseGenerator.type`), mirroring the pattern power supplies already used (`Helmholtz.{X,Y,Z}.type`, dispatched through `create_power_supply()`). Every category accepts `"sim"` as a value alongside its real type name, dispatching to the matching `Sim*` class. Unrecognized type values raise `ValueError` — there is no silent fallback either direction.

`HardwareManager(config_manager, force_sim=False)` — passing `force_sim=True` overrides every device to its simulated class in memory, without touching `config/setupInfo.json`. The app's `--sim` CLI flag (`gui/main_window.py::main()`, parsed with `argparse.parse_known_args()` so Qt's own CLI args are untouched) sets this. Default behavior (`force_sim=False`, no `--sim`) is byte-identical to before this mechanism existed — same classes, same order, same `.connect()` calls.

Simulated classes have full method parity with their real counterparts, not just the methods currently called: `hardware/camera/sim_camera.py::SimCamera`, `hardware/microwave/sim_microwave.py::SimMicrowave`, `hardware/pulse_streamer/sim_pulse_streamer.py::SimPulseStreamer`, `hardware/power_supply/sim_power_supply.py::SimPowerSupply`. `SimCamera` can optionally be wired to the constructed microwave object (`SimCamera(microwave=...)`) so a simulated ODMR sweep produces a real Lorentzian dip — `HardwareManager` does this automatically in sim mode. `SimPulseStreamer` stores all three magnet relay states independently by channel; magnet simulation verifies control flow and contains no synthetic magnetic-field or Zero Field physics.

## Display settings (`display` section of setupInfo.json)

`config/setupInfo.json` has a `display` section with one colormap per image display, stored lowercase as the pyqtgraph/matplotlib name:

```json
"display": { "live_view_colormap": "gray", "zfe_colormap": "gray" }
```

- `live_view_colormap` — the main-window live view. The "Colormap:" combo sits directly above the image (`MainWindow.colormap_combo`).
- `zfe_colormap` — the Zero Field image. The combo is `ZeroFieldWidget.colormap_combo` (in "Camera Acquisition"), which is the reference implementation.
- There is **no `odmr_colormap`**: the ODMR window shows only a spectrum plot, with no image to colour. The separate ODMR analysis window's map has its own colormap list and is not persisted.

Both selectors offer the same list: `gui/display_settings.py::COLORMAP_NAMES` (Gray, Viridis, Plasma, Inferno, Magma). A test keeps it equal to the ZFE widget's hard-coded items.

- **Applies immediately**, with no scan or stream running. The live view sets the colormap on the `ImageView` LUT, which recolours the frame on screen and persists across `setImage()`. ZFE re-renders its retained display image, as it already did.
- **Loaded at window construction**, without emitting a change signal, so startup neither renders nor writes. A missing or unknown value means Gray; a window built without a config (tests: `hardware_manager=None`) defaults to Gray and never saves.
- **Saved on every change** via `save_colormap()`. It updates the in-memory `ConfigManager.config`, so a ZFE window opened later sees the latest choice.
- **How the file is written.** Only the `display` block's text is rewritten (an atomic temp-file replace), so the rest of the file, formatting included, is byte-for-byte unchanged. If that targeted edit can't be verified to parse to the expected content, the file is re-serialised in full (same data, normalised formatting) and a warning is logged.
- **Save failures** are logged, never raised.
- Because the file is tracked, changing a colormap at the rig shows up as a working-tree change to `config/setupInfo.json`.

## ODMR per-pixel statistics

**GUI ODMR scans go through `ODMRExperiment.acquire_pixel_statistics()`, not `acquire_configured_point()`/`process_frame()`.** `ODMRWorker` calls it once per frequency point. It sets the scan point and acquires `repeats` OFF/ON pairs via `acquire_repeat_pair()`, which is the same two triggered frames with the same timing-diagnostics stages as before.

- **Per-pixel normalisation.** Each pair is normalised per pixel by `normalize_odmr_pair_per_pixel()`: `100 * (ON - baseline_counts) / (OFF - baseline_counts)`. It is then folded into a running per-pixel mean and sample SD (Welford's algorithm), and the frames are discarded.
- **Invalid pixels.** OFF pixels at or below the baseline are replaced by the mean of their valid normalised 3×3 neighbours. An invalid pixel with no valid neighbour raises `InvalidPixelNeighborhoodError`. Invalid and repaired counts are recorded per (average, frequency).
- **The scalar contrast for the live plot keeps the old convention:** the mean of per-repeat ROI-mean ratios (see "per-repeat-ratio averaging" below). `process_frame()`/`acquire_configured_point()` remain for other callers.

**`averages` now means independent full sweeps, not repeats of one point.** Before, `ODMRWorker` looped `averages` times inside each frequency point. Now it walks the whole frequency list once per average.
- The live curve is the running mean over sweeps acquired so far.
- **The ODMR plot is deliberately not live-plotted per point; do not change that.** A half-finished sweep must never be mixed into the displayed running mean. As the code stands, `ODMRWindow` only stores each `point_signal` (whose per-point mean already includes the in-progress sweep) and redraws when the scan finishes (`odmr_finished()`), on Fit, or on a plot-mode change — there is no redraw after each completed sweep, and a plot-mode change mid-scan does draw the partial sweep.
- `n_points * averages` acquisitions, so `estimate_odmr_time()` is unchanged in form.
- Every average must have the same repeat count, or the worker raises.

**Statistics cube.**
- `ODMRWorker` pre-allocates `pixel_data` as float32 `(averages, 2, n_freq, H, W)` at the first point. Statistic index 0 holds the normalised mean and index 1 the repeat SD.
- At `_finish()` it builds `statistics_cube`, an `ImageCube` with `axis_order ["average", "statistic", "frequency", "y", "x"]`. Only **completed** sweeps are included; a partial sweep after stop/error is dropped. The metadata records completion, stop, and error status plus pixel-repair counts.
- `ODMRWindow.odmr_finished()` validates it with `framework/analysis/odmr_statistics.py`: `load_odmr_statistics()` and `calculate_odmr_statistics()`.
- The panel's "ODMR Statistics" box shows availability. Its "How are these calculated?" dialog explains the maths.
- When saving is on, the cube is saved as `odmr_statistics.h5` in the scan's output folder.

**The maths** is in `calculate_odmr_statistics()`:
- `final_mean` is the equal-weight mean of per-sweep means, **not** inverse-variance weighted. It is the primary result.
- `fast_*` comes from repeat-to-repeat SDs within each sweep.
- `slow_*` is a method-of-moments estimate: between-sweep variance minus the mean fast variance, floored at 0. It needs at least 2 usable averages.
- `combined_sem` combines the fast and slow SEMs in quadrature.
- The SD and SEM maps are diagnostics, not the result.

**HDF5 chunking** (`ImageCube._save_hdf5()`): one chunk per 2-D image plane for any number of leading dimensions, `(1, …, 1, H, W)`. Before, it was one chunk per leading index, which made the 5-D statistics cube chunk whole `(2, n_freq, H, W)` blocks.

## ODMR magnetic field during scan

**Rule: zero-field ESR is taken at calibrated zero, not with coils off.** With the coils off, the lab background (~1.2 G, mostly Z) splits each strain line into sub-components and inflates the apparent splitting. Measured at the rig: 2E = 6.87 MHz and FWHM 5.3 MHz with the coils off, against 2E = 5.52 MHz and FWHM 3.6 MHz at calibrated zero. ODMR files saved before this setting existed don't record the field, so their ESRs can't be interpreted reliably.

**GUI:** the "Magnetic field during scan" group in the ODMR panel (`gui/widgets/odmr_magnet_field_group.py::MagnetFieldGroup`).
- Mode: "Field zero at diamond" (default) / "Custom vector" / "Leave magnet as is". Every control has a tooltip.
- "Enter as:" (enabled only for "Custom vector") picks how the custom vector is given, on a `QStackedWidget` page each:
  - "Components (X, Y, Z)" (default): X, Y, Z spinboxes in G (default 0, ±10000).
  - "Direction + magnitude": nx/ny/nz (default 1, 1, 1 as in Zero Field; ±1000, normalised automatically), presets X, Y, Z, <110>, <1-10>, <111>, the normalised n̂, one signed "Magnitude" in G (negative = opposite direction), and a read-only "Applied: X …, Y …, Z … G" line. A zero direction is flagged there and refused by the window ("Invalid field direction") before anything starts.
  - The stack's width covers both pages, so the panel column did not widen (303 px, as before); inactive pages have a vertical `Ignored` size policy, so the height follows the shown page.
- `get_config()` adds `magnet_field_mode` (`"field_zero"`, `"custom_vector"`, `"leave_as_is"`), `magnet_vector_entry` (`"components"`/`"direction"`), `magnet_custom_vector_gauss` `[x, y, z]` (with a direction entry: the computed components, or None for a zero direction), `magnet_field_direction` (normalised) and `magnet_field_magnitude_gauss`. Like the other ODMR settings they live in the form for the window's lifetime (the ODMR window has no saved settings); nothing goes to `setupInfo.json`.
- **Both entries command the same way.** `commanded_vector_gauss()` in `experiments/odmr_experiment.py` turns a direction entry into `[x, y, z]` once, with `framework/field_direction.py::vector_along()` (magnitude × `unit_direction()`), ignoring `magnet_custom_vector_gauss`. From there it is the components path: same range check, `enable()`, one `set_vector()`. The window's preflight and status line use the same function. A config without `magnet_vector_entry` means components.
- No `hardware["magnet"]`: the group is disabled with a tooltip saying why, `get_config()` reports `"leave_as_is"`, and the scan sends no magnet command.

**Experiment** (`ODMRExperiment.setup_magnet_field()`, called first in `ODMRWorker.start()`, before the camera lease and any MW/camera action; also from `setup_scan()`):
- Field zero / custom vector: `magnet_field_problem()` runs `plan_field()` on all three axes (pure calculation, no I/O; the Zero Field rule: only `ValueError` refuses, a relay-state `RuntimeError` before the first `enable()` does not). Unreachable → `MagnetFieldRangeError`, with no magnet command sent. Otherwise `magnet.enable()` if it is off, then **one** `set_vector()`. Nothing changes the field again during the scan.
- Leave as is: no magnet command; the magnet's state is only read.
- **A config without `magnet_field_mode` means "leave_as_is"**, so direct callers, scripts and older tests behave exactly as before. Only the GUI defaults to field zero.
- Only the injected magnet object is used (`is_enabled`, `enable`, `set_vector`, `get_vector`, `get_polarities`, per-axis `plan_field`/`current_to_field`); no concrete class import.

**Window:** `ODMRWindow._preflight_magnet_field()` runs the same check before a run starts, and before a stability wait. On failure it shows "Magnetic field out of range" with the axis's approximate reachable range. Nothing starts and `run_started` is not emitted. The "Field:" status line shows e.g. "Field: calibrated zero (0, 0, 0) G", "Field: custom (2, -1, 3) G", "Field: custom 4 G along [0.707, 0.707, 0] = (2.8284, 2.8284, 0) G", "Field: left as is, coils on, model (…) G", "Field: coils off (background)" or "Field: no magnet available (not controlled)".

**Metadata** (`magnet_field_record`, in `config.json` and the statistics cube, in every mode):
- `magnet_field_mode` (effective), `magnet_available`, `magnet_enabled`, `magnet_enabled_by_scan`.
- `magnet_commanded_vector_gauss` (null for leave as is) and `magnet_relay_flip_axes` (`"X,Z"`, `""`; null for leave as is).
- `magnet_vector_entry` (custom vector only, else null), `magnet_field_direction` (unit vector) and `magnet_field_magnitude_gauss` (direction entry only, else null), and with a direction entry `magnet_field_description`, e.g. "4 G along [0.707, 0.707, 0]" (key absent otherwise).
- `magnet_model_vector_gauss` and `magnet_model_polarity` (`"+"`/`"-"`/`"?"`), read right after setup. These are calibration-model values, not measurements.
- Coils off only: `magnet_calibration_background_gauss`, the zero-current intercept of each axis's retained branch (positive before the relays are synchronized), labelled a calibration estimate, not a measurement. Also `magnet_field_note`: "coils off: diamond sees the lab background field".
- `magnet_field_definitions` spells all of these out.
- With "Leave magnet as is" the scan itself is unchanged; the only differences in the files are these keys and the two config keys.

**After the scan:** the magnet window's post-experiment action applies as before. `MAGNET_IS_SWEPT` stays False.

**Tests:** `tests/test_odmr_magnet_field.py`.

## ODMR fitting

**Live ODMR window fits** use `fit_single_lorentzian()` / `fit_double_lorentzian()` in `analysis/odmr_fit.py`, in GHz and percent. Both take an optional `initial_guess`: a parameter list in fit order where a `None` entry means "use the automatic guess".

- "Supply Initial Fit Parameters" (`InitialFitParametersDialog`, in `gui/widgets/initial_fit_parameters_dialog.py`, shared with the analysis window; `gui/panels/odmr_panel.py` re-exports it) collects values for the currently selected fit type:
  - resonance location(s) in GHz — required;
  - contrast (%) and FWHM (MHz) — optional; blank means automatic.
- The baseline is always automatic.
- `ODMRWindow.fit_esr()` converts FWHM MHz→GHz (`manual_initial_guess()`) and rejects resonance locations outside the measured range (`check_manual_locations_in_range()`). Both helpers live in the shared dialog module and are also used by the analysis window.
- Guesses are kept per fit type in `ODMRWindow.initial_fit_guesses` for that window's lifetime only; they are not persisted.

**Two-Lorentzian spectrum fit (Hz units throughout).** These are used by the offline analysis, not the live window's "Fit Lorentzian" button.
- `fit_two_lorentzian_spectrum()` fits one spectrum with ordered dips.
  - Centres are reparameterised through a logit, so f₋ stays inside the measured range and f₊ stays between f₋ and the top of the range.
  - It needs at least 8 strictly increasing frequencies and finite samples. Non-finite samples are skipped.
  - Invalid input raises `ValueError`. Optimizer failure returns an unsuccessful `TwoLorentzianFitResult`, never made-up values.
- `fit_global_odmr_spectrum()` fits the finite-pixel spatial mean of a `(frequency, y, x)` final-mean cube; `global_mean_odmr_spectrum()` computes that mean. `TwoLorentzianInitialGuess` holds reusable starting values.

## Offline ODMR analysis window

**What it is.** `gui/odmr_analysis_window.py::ODMRAnalysisWindow` analyses a saved `odmr_statistics.h5`. It touches **no hardware** and is not an experiment window, so it has no `run_started`/`worker_thread()`/`request_stop()` and is not in `MainWindow`'s worker registry.

**How it is opened.**
- From the ODMR window's "ODMR Analysis" button: `ODMRPanel.analysis_requested` → `ODMRWindow.analysis_requested` → `MainWindow.open_odmr_analysis_window()`. There is one instance, parented to `MainWindow`, re-shown if already open.
- Standalone: `python odmr_analysis_main.py`.

**Workflow.** Each step is enabled by the window's `ODMRAnalysisState`.
1. **Open ODMR HDF5.** Runs `load_odmr_statistics()` + `calculate_odmr_statistics()`.
2. **Fit Global Spectrum.** Uses the "Fit type:" selector. It has the ODMR window's labels and order (Single, Double) and **defaults to Double**, which was the window's only fit before. See "Global fit options" below.
3. **Fit Pixels.** Runs `analysis/odmr_pixel_fit.py::fit_odmr_pixels()`. It needs a successful **Double** global fit (`global_fit_result`), which seeds every pixel (`_local_initial_guess()`):
   - centres and linewidths come from the global fit;
   - baseline (90th percentile) and amplitudes are estimated per pixel.
   - It runs on a `QThread` (`gui/odmr_analysis_worker.py::ODMRPixelFitWorker`).
   - Each pixel needs at least 8 finite samples.
   - Per-pixel outcomes go into a `PixelFitStatus` map, and one pixel's failure never aborts the rest.
4. **Reconstruct Bz.** `analysis/odmr_magnetic.py::reconstruct_bz()` computes `(f₊ − f₋) / (2γ)` with γ = 2.8024951 MHz/G, then divides by cos θ with θ = 54.7356°. It reports a **magnitude** in Gauss with propagated uncertainty: no field sign, no background subtraction.

**Display.**
- Maps: f₋, f₊, splitting, RMSE, fit status, and Bz magnitude. Clicking a pixel shows its spectrum and fit.
- The colormap list is the window's own (Viridis…Cividis) and is not persisted; see "Display settings".

**Global fit options.**
- **Double Lorentzian** runs `fit_global_odmr_spectrum()` (bounded, ordered, Hz). Its result is `global_fit_result` and seeds pixel fitting.
- **Single Lorentzian** runs the ODMR window's `fit_single_lorentzian()` on the finite points of the global mean, in GHz. Its result is `single_fit_result`; `global_fit_result` stays None.
  - So Fit Pixels and Reconstruct Bz stay disabled after a Single fit, and the status line says so.
  - The state stays `LOADED`.
- **"Supply Initial Fit Parameters"** (next to "Fit Global Spectrum") opens the same `InitialFitParametersDialog` as the ODMR window, with the same fields, units and validation.
  - The analysis window passes `allow_clear=True`, which adds a "Clear / use automatic guesses" button. It accepts without validating, and the window then stores None for that fit type. The ODMR window doesn't pass it, so its dialog has no such button.
  - Values are kept per fit type in `ODMRAnalysisWindow.initial_fit_guesses` for the window's lifetime. Loading another file does not clear them, and the dialog is pre-filled with them.
  - The label under the buttons shows what the selected type will use. If only the other type has values, it says they are not used with this type.
  - Locations outside the measured range are rejected before fitting, as in the ODMR window.
- **How supplied Double values reach the fitter.** Blank values and the baseline are filled from the automatic guess by `analysis/odmr_fit.py::two_lorentzian_guess_with_overrides()`. Units are converted (GHz/MHz → Hz), and the two dips are ordered by centre, so they may be entered in either order.
- **Why this exists.** The automatic double guess (`_default_two_lorentzian_guess()`) picks the deepest sample, then the lowest sample at least `max(2, N // 20)` *indices* away. With "dense points near dips" sampling, that is a few MHz, on the same dip. The fit then converges with both Lorentzians on the deeper dip and reports `success`, missing the second dip. Supplied locations avoid this. The automatic guess itself is unchanged.
- **Failures are always reported** in the status line and a message box: an unsuccessful result, an exception, or an out-of-range location.
- **Record of what was used.** `global_fit_settings` (`fit_type`, `initial_parameters`) is shown at the top of the "Global Fit" box, including on failure. The pixel-fit summary states the seeding and the global fit's initial parameters. The window has no file export.

**Closing and reloading during a pixel fit.** Closing is deferred until the fit's thread finishes. Loading another file is refused while it runs.

## Dead code — do not build on these

Confirmed unused by the running application; each is either an abandoned duplicate or a stub with no subclasses:

- `controller/experiment_runner.py` — only referenced by `tests/test_odmr.py`.
- `hardware/camera/andor_neo.py` (`AndorNeo`, pylablib-based) — a second, older Andor driver. `HardwareManager` only ever constructs `AndorNeoAndor3` (`hardware/camera/andor_neo_andor3.py`).
- `hardware/sim_hardware.py` (`SimMicrowave`, `SimPulseStreamer`, `build_sim_hardware()`) — an earlier, less complete simulator module. `HardwareManager` now wires `hardware/microwave/sim_microwave.py` and `hardware/pulse_streamer/sim_pulse_streamer.py` instead (co-located with their real counterparts, matching the rest of the package layout). This file was deliberately left alone rather than deleted or refactored — treat it as legacy, not a second implementation to keep in sync.
- `experiments/pulsed_experiment.py` (`PulsedExperiment`) — abstract stub, no subclasses anywhere in the repo. `ODMRExperiment` builds its own pulse sequences without inheriting from it.
- `hardware/camera/base_camera.py` (`BaseCamera` ABC) — no camera class actually subclasses it; `AndorNeo`, `AndorNeoAndor3`, and `SimCamera` are independently duck-typed.

## Not implemented — disabled placeholders only

Rabi, Ramsey, T1, and T2 experiments do not exist as code. `gui/main_window.py` has four buttons for them, all `.setEnabled(False)` with no click handler wired up. One of them is misspelled in the source — the button is labeled/named "Ramsy", not "Ramsey". **Do not silently fix this spelling** if you're working nearby; it's recorded here deliberately, not accidentally missed. SmarAct XYZ stages, an Event Camera, and a C4 Lock-In Camera are documented-as-planned only — no driver code exists for any of them. The unused `stages`/`MCS2` configuration remains. The unused orphan EventCamera fields and its stale `detector2=6` assignment were removed after auditing every digital call site; channel 6 now belongs exclusively to the X polarity relay.

## Known open issues (Zero Field experiment)

See `docs/zero_field_audit_2026-07.md` for the full physics/logic audit with per-finding status. These are **known and deliberate-for-now, not bugs to spontaneously fix** if you're working in `experiments/zero_field_experiment.py`:

- Non-swept magnet axes are preserved from whatever was configured before a scan started, not forced to zero — a documented design tradeoff, not an oversight (audit §3, §10.2–10.3).
- Stop is cooperative: an in-flight settling sleep or camera acquisition is not interrupted, only the point loop's next iteration boundary checks the stop flag (audit §10.8).
- No magnetic-field or camera-exposure hardware readback exists anywhere in `hardware/magnet/` or `hardware/power_supply/` — settling is open-loop, and recorded exposure/binning metadata reflects the driver's own cached value, not an independent SDK query (audit §4, §8).
- Per-point repeat frames (the `averages` loop inside `acquire_frame()`) are discarded after averaging — only the mean is kept. This is separate from the multi-*scan* raw-saving feature (`save_raw_scans`), which persists whole sweeps, not individual repeats (audit §10.9).

Camera-contention and ROI-validation findings from the same audit are fixed — see "Architecture and call chain" above.

**Field range is validated before a Zero Field scan starts.** `ZeroFieldWindow._start_run()` (called by `start_scan()` and by the run queue) calls `_preflight_field_range()` first, before any state change, thread, or worker exists; the check itself is `field_range_problem()`. It runs each axis's existing `MagnetAxis.plan_field()` (pure calculation, no I/O) for the vector at both sweep endpoints: the swept axis at `field_start`/`field_stop` × 0.1 mT/G, the other axes at 0.0 mT (the GUI always zeroes them). Endpoints suffice because required current on a branch is |target − intercept| / ratio, maximal at an endpoint of a linear sweep. With reference normalization on, the reference vector (swept axis at `reference_field_gauss`, others 0.0 mT) is checked the same way, and an unreachable reference is refused with the same dialog. A `ValueError` (out of range / non-finite) shows "Field range out of bounds" with the axis's approximate reachable range in Gauss, derived from `max_current` and both calibration branches, and aborts the start. `RuntimeError`s from `plan_field()` deliberately do **not** block: before the magnet's first `enable()` every relay is unsynchronized and `plan_field()` raises for any nonzero current, a state the scan's own `setup_scan()` resolves — blocking on it would refuse every first scan after launch. Previously an unreachable target raised mid-scan, after data had been acquired. For a vector sweep every axis is checked at s·n̂ for both endpoints and the reference (each axis is linear in s, so endpoints still suffice); the message names the failing axis and the direction, and a zero or invalid direction is refused first, as "Invalid field direction".

**Relay flips get extra settling and are marked in the data.** A flip (`_switch_polarity()`: zero/off/discharge/switch/settle/on) is a far larger transient than an incremental step, and flips happen near field zero — X ~0.12 G, Z ~1.17 G, Y's gap ~0.063 G on this rig — exactly where ZFE resolves. `ZeroFieldExperiment(post_flip_settling_ms=...)` (constructor default 0.0; GUI "Post-flip extra settling (ms)", default 500, range 0–60000, passed via `ZeroFieldWorker`) sleeps that much **after** the normal `settling_time_ms` sleep, only at points whose field command flipped a relay; like the normal settling sleep it is not interruptible by stop. Flips from `setup_scan()`'s own `set_vector(0, 0, 0)` (e.g. X/Z moving to their negative branch; every later scan of a multi-scan run, whose previous sweep ended positive) are attributed to point 0, since they occur immediately before it with no settling in between. Per acquired point the experiment records `relay_flip_occurred` (bool) and `relay_flip_axes` (sorted comma-joined names: `"X"`, `"X,Z"`, `""`), truncated with `field_values_gauss` on an early stop. They are stored as **ImageCube metadata lists**, not separate HDF5 datasets — the same mechanism as `field_values_gauss`, so they reach the single-scan streamed file (writer `update_metadata()` + `merge_hdf5_metadata`), raw per-scan files, and the atomic-saved average without any writer or file-format change. They are written in `_run_single_sweep()`'s `finally`, so a hard process kill mid-scan leaves a streamed file without them. In the multi-scan average a point is flagged if a flip preceded it in any averaged scan, with the union of axes. `post_flip_settling_ms` is recorded in metadata.

**Requested vs modelled field are both recorded per Zero Field point.** `field_values_gauss` (and `scan_axis_values`) stay exactly what they were: the **requested** linspace, in G — unchanged for backward compatibility. Alongside them, `_run_single_sweep()` snapshots the magnet right after each point's `set_vector()` (`_model_snapshot()`) and records three more per-point metadata lists, truncated with the others on an early stop and carried through the same streaming/average/raw-file paths as the relay-flip lists:
- `model_field_values_gauss` — the swept axis's **modelled** field, in **G** (`MagnetAxis.get_field()` × 10).
- `model_field_vector_mT` — the full modelled `[x, y, z]` vector, in **mT**, shape `(n_points, 3)` (from `Magnet.get_vector()`, which returns a dict).
- `model_polarity` — the relay branch per axis as `[x, y, z]`: `"+"`, `"-"`, or `"?"` (unknown).

All three are calibration-model values, **not measurements** (there is no field readback). Where they differ from the request: in a **branch gap** (rig: Y between ~0.0623 and ~0.0662 G) the supply holds 0 A and the model field is the retained branch's intercept, e.g. requested 0.0640 G → modelled 0.0623 G. In a **branch overlap** (rig: X ~0.121–0.128 G, Z ~1.172–1.177 G) both branches reach the request exactly, so the model *field* equals the request; only `model_polarity` shows which branch sweep history selected. In a multi-scan average the model fields are per-point means across scans and an axis whose polarity differed between scans is `"mixed"`; raw per-scan files keep exact values. `field_value_definitions` in the metadata spells these out.

**Zero Field cubes are pre-allocated, not grown per point.** `ZeroFieldExperiment._run_single_sweep()` builds its `ImageCube` with `expected_planes=self.field_points`; the first `add()` allocates one float64 `(field_points, H, W)` buffer and later frames are written in place. (Plain `ImageCube` without `expected_planes` still concatenates per frame — O(N²·frame) copies — and keeps the frame's dtype.) Consequences to keep in mind: `ImageCube.data` is now a **property** returning a view of only the filled planes, so `data.shape[0]` is still the acquired-frame count after an early stop; assigning `cube.data = ...` replaces the buffer and drops pre-allocation. `_run_single_sweep()`'s `finally` calls `image_cube.truncate(frame_count)`, which copies the acquired planes out and frees the unused remainder after an early stop (a no-op for a complete sweep). Because the whole cube is allocated at the first point, a sweep whose full cube can't fit in RAM now fails at the first point instead of partway through. More frames than declared logs a warning and falls back to concatenation. The multi-scan average cube is unchanged (one copy of a completed cube).

## Zero Field bidirectional sweeps (`alternate_direction`)

**Why it exists.** At the rig, slow fluorescence drift within one sweep (0.4–0.6%) is larger than the ~0.3% zero-field dip. If every scan sweeps the same way, drift that repeats per scan survives averaging and looks like field dependence. Pairing a forward and a reverse sweep and averaging by field cancels drift that is linear in time.

**How it is switched on.**
- GUI: "Alternate sweep direction" in "Multi-Scan Averaging" (`ZeroFieldWidget.alternate_direction_check`, default off).
  - It is enabled only while "Enable averaging" is checked.
  - `get_config()["alternate_direction"]` is `isChecked() and isEnabled()`, so a disabled-but-checked box reports False.
  - With an odd "Number of Scans" a non-blocking "⚠ Odd number of scans" label appears; the scan still runs.
- Experiment: `ZeroFieldExperiment(alternate_direction=False)`, passed through by `ZeroFieldWorker`. It only takes effect together with `averaging_enabled`; `_alternating` is the effective flag.

**Which scans reverse.** `ZeroFieldExperiment.scan_is_reversed(scan_index, alternate)`, with the same 1-based `scan_index` as the GUI's "Current scan" label and the `_scan_NNN` raw file names. Scan 1 sweeps start→stop; scans 2, 4, 6, … sweep stop→start. So a single scan, and the first scan of every average, is always forward. The "Current scan" label adds "(start→stop)"/"(stop→start)" while alternating.

**What a reverse sweep does.** `setup_scan()` sets `scan_vector` to the reversed linspace. Everything per point runs unchanged in sweep order: flip detection, post-flip settling, model snapshot, live updates (plotted at their real field values, so the live curve fills in from the stop end), and streaming.

**Averaging is by field, not by acquisition order.** Before a completed scan is folded in, `_field_ordered()` flips a reverse scan's image planes (a reversed view, no copy) and every per-point list in `_FIELD_ORDERED_KEYS`: `field_values_gauss`, `relay_flip_occurred`, `relay_flip_axes`, `model_field_values_gauss`, `model_field_vector_mT`, `model_polarity`. The existing merge rules then apply unchanged: running mean for images and model fields, union for flips, "mixed" for differing polarity.
- The averaged cube, `field_values_gauss` and `scan_axis_values` are therefore always in start→stop order.
- Consequence for flips: a flip is flagged on the point after it in sweep order, so one physical relay crossing is flagged on adjacent field indices in the two directions. The average's union shows both.
- Consequence for polarity: in a branch overlap the relay branch depends on sweep direction, so overlap points can legitimately become "mixed".

**Per-scan raw files** (streamed or `save_raw_scans`) keep acquisition order: a reverse file's `scan_axis_values`/`field_values_gauss` run stop→start. They record `sweep_direction` (`"forward"`/`"reverse"`), only while alternating. `analysis/zero_field_averaging.py` averages plane *i* of every file, so it **refuses** a raw `.h5` with `sweep_direction == "reverse"` (`_reject_reverse_sweep()`). Use the run's own average file instead.

**New data in the average file** (multi-scan runs only; with Number of Scans = 1 there is no average, just the single forward scan's file).
- **Datasets, while alternating:** `forward_average` and `reverse_average` — running means of forward-only and reverse-only completed scans, start→stop order, same shape as `data`. A direction with no completed scan has no dataset. They let analysis see the drift directly: forward and reverse are tilted opposite ways. With equal counts, `data` = their mean.
  - They are stored through `ImageCube.extra_datasets`, a generic dict of named arrays. `_save_hdf5()` writes each as a top-level dataset, and `load()` returns every non-reserved top-level dataset in it. Names may not be `data`, `scan_axis_values`, `metadata_json`, `axes_json` or contain `/`. `.npz` stores them as `extra_dataset__<name>`. Streamed files never have any.
- **Metadata, while alternating:** `scan_directions` (one `"forward"`/`"reverse"` per completed scan), `forward_scan_count`, `reverse_scan_count`.
- **Metadata, every multi-scan average:** `scan_point_times_s` — one list per completed scan, each in that scan's acquisition order.
- `point_time_s` and `sweep_direction` are per-scan only (`_PER_SWEEP_ONLY_KEYS`) and are dropped from a multi-scan average.

**Per-point timestamps.** Every sweep records `point_time_s`: the midpoint of each point's frame acquisition, in s since that sweep began (just before `setup_scan()`), in acquisition order, truncated on an early stop like the other per-point lists. Single-scan files and raw scan files carry it.

**Other metadata.**
- `alternate_direction` (the effective flag) is in the static metadata of every run.
- `field_value_definitions` gained `point_time_s` and `field_order`: a multi-scan average is start→stop regardless of sweep direction; single scans and raw files are in acquisition order.

**With the mode off, output is unchanged.** It was checked file-by-file against the previous commit with a deterministic run (single scan, multi-scan average, and raw files). Data, field axis, flips and polarity were identical. The only additions are `alternate_direction`, `point_time_s`, `scan_point_times_s` (multi-scan average) and the two new `field_value_definitions` entries.

**Unchanged.**
- A partial scan (stop or error) is never folded into any average, including the direction averages.
- Pre-scan field-range validation checks the same two endpoints.
- No magnet or hardware file is involved.

## Zero Field reference field normalization (`reference_enabled`)

**Why it exists.** At the rig, slow environmental drift is ~1% peak-to-peak over minutes, against ~0.06% point-to-point noise and a ~0.15–0.3% zero-field dip. Bidirectional sweeps cancel only drift that is linear in time, and need many scans. Like ODMR's ON/OFF pairing, this mode measures every point twice: at its field (signal, S) and at a fixed off-resonant reference field (R). It stores the per-pixel ratio, so slow multiplicative drift common to both halves divides out.

**How it is switched on.**
- GUI: "Reference field normalization" checkbox and "Reference field (G)" spinbox (default −8.0, ±10000, 4 decimals), in "Multi-Scan Averaging" next to "Alternate sweep direction".
  - It works with or without multi-scan averaging, so it is not tied to the averaging checkbox.
  - The spinbox is enabled only while the box is checked.
  - `get_config()["reference_enabled"]` is `isChecked() and isEnabled()`. `reference_field_gauss` is always reported.
- Experiment: `ZeroFieldExperiment(reference_enabled=False, reference_field_gauss=-8.0)`, passed through by `ZeroFieldWorker`.

**Reference vector.** It is the point's vector with the swept axis replaced by `reference_field_gauss`; the non-swept axes keep their usual values (calibrated zero, or the retained vector with `zero_other_axes=False`).

**Acquisition order within a point** (`_run_single_sweep()` → `_acquire_half()` per half).
- Point index i in **sweep order**: even i measures S then R (`"SR"`); odd i measures R then S (`"RS"`).
  - Alternation cancels linear drift between halves over neighbouring points.
  - Consecutive points share a reference half: R at the end of point i, R at the start of point i+1.
- Each half runs: `set_vector`, normal settling, extra post-flip settling if any relay flipped, then `averages` frames.
- **Repeated identical command.** When a half commands the same vector as the previous half of the sweep (the R,R pair), `set_vector()` is still called, but the magnet's change-detection cache skips the write. No settling sleep is added, because nothing moved.
  - This is decided by comparing the commanded vectors in the experiment, since `set_vector()` doesn't report whether it wrote.
  - S→S between points is a different field, so it settles as usual.
- Flip detection and post-flip settling apply to every command, signal or reference. Setup-scan flips still fold into the sweep's first command, point 0's S half.
- With the reference off, `_acquire_half()` runs only the S half, with the same command/snapshot/settle/acquire sequence and timing stage names as before.
  - The reference half's timing stages are prefixed `reference_`, and include `reference_acquire`.

**Relay-flip cost of a reference on the opposite branch.** A reference whose sign puts the swept axis on the other relay branch from a point's field flips the relay on both halves that change field: R after S, and S after R. Each flip pays the full zero/off/discharge/switch/settle/on sequence plus `post_flip_settling_ms` (GUI default 500 ms).
- In sim, a symmetric −2…+2 G X sweep with a +8 G reference took 0.71 s per point, against 0.26 s with the reference off (2.7×). The negative half of the sweep flipped on almost every half.
- A 0.5…2.5 G sweep, on the reference's branch, took 0.27 s against 0.17 s (1.6×). It is under 2× because the R,R repeat neither writes nor settles.
- So pick the reference's sign on the branch most of the sweep uses. The checkbox tooltip says the same, and recommends a negative reference for Z sweeps around zero. The default is −8.0 G for that reason. The sim timings above used +8 G.

**Data.**
- `data` is unchanged in meaning: the signal planes.
- Extra datasets (`ImageCube.extra_datasets`), in the **same plane order as `data`** (acquisition order for single scans and raw files, start→stop for a multi-scan average):
  - `reference` — per-point reference planes.
  - `ratio` — per-pixel `data / reference`, float64. Where the reference is 0 it is NaN, and the count is logged as a warning per sweep.
- **Multi-scan average.** `reference` and `ratio` are averaged per field point like `data`. `ratio` is the **mean of per-scan ratios**, the same convention as ODMR's mean(I_on/I_off), so it is not `data / reference` of the averaged planes.
  - While alternating, also `ratio_forward_average` and `ratio_reverse_average`, next to `forward_average`/`reverse_average`.
  - Field alignment applies unchanged: `_field_ordered()` reverses `reference`/`ratio` (`_PER_POINT_EXTRA_KEYS`) with the image planes.
- **Single-scan streaming.** `ImageCubeStreamWriter.write_extra_plane()` writes each point's `reference` and `ratio` planes **before** its data plane, so `planes_written` never counts a point whose extras are missing. `close()` truncates extras to `planes_written` like `data`, so a crash keeps every completed point's three planes. Raw per-scan streamed files carry them too.
- **Live display.** With the reference on, the fluorescence curve plots the mean ratio per point over finite pixels (`zero_field_worker.mean_ratio()`/`ratio_curve()`; y-axis "Signal / reference"). The completed-scan and final curves come from the cube's `ratio` dataset. The live image still shows the signal frame.

**Metadata.**
- Static: `reference_enabled` (always written), plus `reference_field_gauss` and the extra `field_value_definitions` entries (only when on).
- Per point, only when on:
  - `reference_order` (`"SR"`/`"RS"`)
  - `reference_point_time_s` (midpoint of the reference acquisition, same clock as `point_time_s`)
  - `reference_relay_flip_occurred` / `reference_relay_flip_axes`
  - `reference_model_field_vector_mT` / `reference_model_polarity`
  - `relay_flip_*` and `model_*` keep describing the S half.
- All of these except `reference_point_time_s` are field-ordered (`_FIELD_ORDERED_KEYS`).
- **Merge rules in a multi-scan average** (`_merge_reference_metadata()`): union for flips, running mean for the model vector, "mixed" for polarity, and "mixed" for `reference_order` where scans differed.
  - With alternate direction and an even point count, every field's order differs between the two directions, so `reference_order` is all "mixed". With an odd count it matches.
- `reference_point_time_s` is per-scan only (`_PER_SWEEP_ONLY_KEYS`); a multi-scan average stores `scan_reference_point_times_s` instead.

**With the reference off, output is unchanged.** Checked file by file against the previous commit with a deterministic run (single scan, multi-scan average, raw files). Data, field axis, every metadata value and the dataset list were identical; the only addition is `reference_enabled: false`.

**Unchanged.**
- A stop is still checked only between points, so a point is always complete (both halves) or absent. A partial scan is never folded into an average.
- No magnet or hardware file is involved.

## Zero Field vector sweeps (`field_axis = "vector"`)

**Why it exists.** The sample is a {100}-faced plate with <100> edges, so the coil axes are <100> crystal directions. A field along <100> keeps all four NV orientations degenerate (confirmed by ODMR at the rig: X alone or Z alone gives one ESR pair, X = Y gives two), so a single-axis sweep cannot show the zero-field cross-relaxation feature. Sweeping along <110> or <111> can.

**How it is switched on.**
- GUI: "Vector" is the fourth "Sweep Axis" item (`ZeroFieldWidget.VECTOR_TEXT`).
  - It shows `field_direction_box`: nx/ny/nz spinboxes (default 1, 1, 1; ±1000, 4 decimals), quick-pick buttons "<110>" (1,1,0) and "<111>" (1,1,1), and a label with the normalised n̂. A zero vector is flagged there.
  - Start/Stop Field and the reference field are then the **signed magnitude s along n̂**. The labels are unchanged.
  - `get_config()` adds `field_axis: "vector"` and `field_direction` (normalised). With X/Y/Z it is exactly as before, with no `field_direction` key. `set_config()` accepts both.
  - The direction box's column width is computed before it is hidden, so showing it never clips the column.
- Experiment: `ZeroFieldExperiment(field_axis="vector", field_direction=[nx, ny, nz])`, passed through by `ZeroFieldWorker`.
  - `field_direction` is normalised once by `unit_direction()` (`framework/field_direction.py`, shared with ODMR's direction entry; still importable from `experiments/zero_field_experiment.py`). It is required for "vector" and refused for X/Y/Z.
  - `unit_direction()` rejects a zero, non-finite or non-3-number direction, and returns an already-unit vector unchanged, so normalising again (queue file, experiment) never changes the last digit.

**What a point commands.** `_point_vector_mT(s)` = s × 0.1 × n̂ on all three axes, through the same `set_scan_point()` → `Magnet.set_vector()` path (`+ 0.0` keeps a zero component from becoming −0.0).
- `setup_scan()` still commands `set_vector(0, 0, 0)` first; no axis is "non-swept", so `non_swept_axis_field_mT` is `{}`.
- Flip detection, post-flip settling, model snapshots, timestamps, alternate direction, reference and streaming are unchanged. Axes flip at different s, since each crosses its own branch intercept at its own s.
- Reference: R = `reference_field_gauss` × n̂.
- The magnet window's live vector is s·n̂ (`ZeroFieldWorker._on_live_update()`).

**Metadata.**
- Every Zero Field file now has `field_direction`: the unit vector, or `null` for X/Y/Z. `field_axis` is "vector".
- `field_values_gauss` / `scan_axis_values` are the requested s. `model_field_vector_mT` is per point as before. `model_field_values_gauss` is the model vector's component along n̂, in G.
- `field_value_definitions` gains `field_direction` and vector wordings of `field_values_gauss`, `model_field_values_gauss` and (with the reference) `reference`, only for vector sweeps.
- The timing CSV header gets a `field_direction` line only for vector sweeps.

**Queue.**
- `validate_config()` accepts `field_axis` "vector" and then requires `field_direction` (stored normalised). For X/Y/Z a stray `field_direction` is dropped. Queue file format and version are unchanged.
- `summarize()` shows the direction: "vec(0.58,0.58,0.58) -2→2 G, …".

**Single-axis output is unchanged.** Checked file by file against the previous commit with a deterministic run (single scan; 2-scan average with alternate direction and reference; raw files). Data, field axis, datasets and every metadata value were identical; the only addition is `field_direction: null`.

**Tests:** `tests/test_zero_field_vector.py`.

## Zero Field run queue

**What it is.** Several Zero Field runs with different settings, executed one after another unattended. The "Run Queue" panel is a non-closable bottom dock in `ZeroFieldWindow`. The code is in three places:
- `gui/zero_field_queue.py` — the GUI-free model: `QueueEntry`, `summarize()`, `validate_config()`, queue files, `QueueLog`;
- `gui/widgets/zero_field_queue_widget.py` — display and buttons only;
- the "RUN QUEUE" section of `gui/zero_field_window.py` — editing and execution.

No experiment, worker, magnet or hardware file was changed for it.

**Entries.**
- "Add to queue" snapshots `ZeroFieldWidget.get_config()` (a deep copy) plus the window's `output_directory`, so later form edits never change an entry.
- The add runs the same `_preflight_field_range()` as the Run button, with its dialog, and an unreachable entry is not added.
- Other buttons: Remove (not the running entry), Move up/down, Duplicate, "Clear pending" (pending entries only), and "Load entry into form" (`ZeroFieldWidget.set_config()` plus the output directory).
- Statuses: pending / running / done / stopped / failed / skipped (unstable). The last comes from the count stability check (see "Count stability check before experiments"); it is not a failure, so "On failure" does not apply and the queue continues.
- Each entry also snapshots the form's stability-check settings (`QueueEntry.stability`, a `StabilitySettings`), kept apart from `config` so `config` stays exactly `get_config()`. The summary gains ", stability ≤x% ≤y%/min over w s" only when the check is on.
- There is **no duration estimate**. The config does not determine it: exposure lives in the main window, and relay-flip count and SCPI pacing depend on the sweep path.

**Queue files.**
- "Save queue…" writes JSON: `format: "nv_control.zero_field_queue"`, `version: 1`, `pause_between_runs_s`, `on_failure`, and each entry's `config` + `output_directory` + `stability` (the `StabilitySettings` as an object). Run status is not saved. `stability` is optional on load (validated by `StabilitySettings.from_dict()`); files written before it existed load with the check off, and the version stays 1.
- "Load queue…" replaces the list with all-pending entries. It is refused as a whole (nothing partially loaded) if the file is invalid, if any entry is missing a `get_config()` key or has the wrong type (`CONFIG_TYPES`), or if any entry fails the field-range check.

**Execution: one start path.**
- `start_scan()` is now `_start_run(widget.get_config(), self.output_directory)`, and the queue calls the same `_start_run(entry.config, entry.output_directory, interactive=False)`. The validation, worker, file naming, metadata, signals and cleanup are therefore identical.
- `interactive=False` changes only one thing: a refusal (field range, unusable directory) is returned as a reason string instead of shown as a modal dialog.
- The field-range check itself is the module function `field_range_problem()`, which `_preflight_field_range()` wraps with its dialog.
- The range is checked when an entry is added and again immediately before it starts.
- Pending entries run in list order; the next one is picked at start time, so reordering pending entries mid-queue changes what runs next.
- The next entry starts after the previous worker's `QThread.finished` (`_queue_run_thread_finished()`), plus "Pause between runs (s)" (default 0) on a single-shot `QTimer`.
- File names keep their one-second timestamp. If the next start would reuse the previous run's second, it waits for the next second, so names never collide.

**Outcomes.**
- **Failed:** the worker raised, or the start was refused.
  - During a queue, `show_error()` records the message on the entry instead of opening a dialog, so a failure never blocks the queue.
  - "On failure": "Stop queue" (default) ends the queue; "Skip to next" continues.
- **Stopped is not a failure.** "Stop current run" stops the worker, marks the entry stopped ("stopped by user") and continues. "Stop queue" stops the run (message "queue stopped") and starts nothing more; remaining entries stay pending. Stopped during a pause, the queue ends at once.
- `request_stop()` (MainWindow's close path) stops the whole queue while one is active.
- The normal Run button and "Run queue" are disabled while a queue runs; "Run queue" is also disabled during a single run. While the queue runs, the Run button is grey and reads "Queue running", with a tooltip pointing to the queue's stop buttons (see "Zero Field window layout").

**Magnet lock across the queue.**
- `run_started` is emitted per run, so `MainWindow` registers every worker thread for close safety.
- `run_finished` is emitted **once**, when the queue ends: `scan_finished()` calls `_queue_record_finished_run()`, and skips `run_finished` if the queue continues.
- So `experiment_finished` never fires between queued runs, and the magnet stays locked through runs and pauses.
- Each run's `run_started` re-emits `experiment_started`, which (as for any start) clears a manual-control override in `MagnetControlWindow`.
- The post-experiment action runs once, at the queue's end. After Zero Field it is skipped anyway, because the magnet is already disabled.
- A single run outside a queue emits `run_finished` exactly as before.

**Queue log.**
- File: `zero_field_queue_<timestamp>.csv`, created when the queue starts, in the first pending entry's output directory. It is UTF-8 with a BOM, so Excel shows "→".
- Columns (`LOG_COLUMNS`): index (1-based list position when the entry started), status, start_time, end_time, duration_s, output_file, first_frame_mean_counts, summary, message, then the stability fields: stability_check_enabled, stability_outcome ("not used" when off), stability_std_percent, stability_drift_percent_per_min, stability_wait_s, and the limits (stability_window_s, stability_max_std_percent, stability_max_drift_percent_per_min, stability_timeout_s, stability_on_timeout; blank when off).
- One row per finished entry (done/stopped/failed, including a start refused by validation), appended and flushed as it finishes, so a crash keeps completed rows.
- `first_frame_mean_counts` is `mean_fluorescence()` of the first live-update image: scan 1, point 1, the signal frame averaged over that point's repeats.
- If the log can't be created, the queue still runs and the error is logged.

**Status.** "Queue: run k of N" (N = entries started in this execution plus those still pending), "Queue: pausing …", and a finished summary. The running row is highlighted in bold.

**Tests:** `tests/test_zero_field_queue.py`, which runs real queues on SimCamera and a real `Magnet` over simulated supplies.

## Zero Field window layout

- **Left column** (`ZeroFieldWidget.left_panel`):
  - The run/stop button is fixed at the top.
  - Every other control is below it in `control_scroll_area`, a `QScrollArea` set up exactly like the ODMR panel's: resizable, vertical scrollbar as needed, horizontal always off. Its widget is the "Zero Field Parameters" group (`controls_group`), with the groups in their original order.
  - The scroll area's minimum width is the controls' size hint plus the scrollbar, so nothing is clipped horizontally.
  - The plot, the image and the bottom "Run Queue" dock are outside it.
  - Below the scroll area, the stability-check wait bar (hidden unless waiting; see "Count stability check before experiments").
- **Run/stop button.** It is one toggle driving `toggle_scan()`. Its styling is shared with the ODMR run button: `gui/widgets/run_button.py::style_run_button()`, where IDLE is green, RUNNING red and BLOCKED grey and disabled. Text and background colours are both explicit, so it reads the same in light and dark themes. The ODMR button now uses the same texts and colours (see "ODMR window layout").
  - Idle: "Start", green. Running: "Stop", red. A running queue: "Queue running", grey, disabled.
  - It follows the window's `run_started`/`run_finished` signals (`_update_run_button()`), not the click, so completion, stop and error all return it to "Start".
  - `ZeroFieldWidget.set_running_state()` now only sets the status line.
- **Default size** is 1100×960. That fits 1920×1080 with the taskbar visible (1032 px available, 991 px with the title bar), and it is clamped to a smaller screen. The window's minimum height is about 540 px, set by the plot and queue areas; below the default height the controls scroll.

## Live count statistics (main window)

A "Count statistics" panel under the live view, like a power meter's statistics screen, for watching focus/laser drift. `gui/widgets/count_statistics_panel.py::CountStatisticsPanel` (Qt) on top of `framework/count_statistics.py` (GUI-free: `frame_sample()`, `RunningStatistics`, `CountLog`).

- **Source of samples.** `MainWindow.update_image()` calls `count_statistics_panel.add_frame(frame)` with each frame it already got from `get_latest_frame()`. One sample = the mean counts over the whole frame, which *is* the acquisition ROI (the camera AOI). **The panel never calls the camera.** Raw counts: `baseline_counts` is not subtracted, and a baseline change does not reset.
- **Repeated frames are dropped.** The 30 ms timer can poll faster than the camera delivers, and `get_latest_frame()` returns a copy of the same frame again; the drivers keep no frame counter. `frame_sample()` returns a fingerprint (shape, exact sum, 64 strided pixels) and an identical one is skipped, so a frame is never counted twice.
- **Values** (refreshed at 4 Hz by the panel's own `QTimer`, not per frame): Actual (latest accepted frame, shown even when not accumulating), Mean, Min, Max, Std deviation as counts and % of mean (**sample SD, ddof=1**, Welford — matches `numpy.std(ddof=1)`), Ratio max/min, Samples, Elapsed (time spent accumulating; Stop pauses it).
- **Buttons:** Start/Stop accumulation (shared run-button styling), Reset (clears statistics, trend and elapsed; keeps running). Trend plot: the samples over the last 5 minutes, scrolling; a pause breaks the line.
- **Experiments.** `framework.camera_ownership.experiment_owns_camera(camera)` (new; deliberately lock-free, because `exclusive_camera_access()` holds `state_lock` while waiting on the GUI thread) is checked per frame and per refresh. While it is True the panel shows "Paused — experiment running" and ignores frames; accumulation continues by itself when the live view resumes.
- **Settings change resets.** A change of the main window's ROI, exposure or binning (`AcquisitionState`) resets the statistics and says which, e.g. "Statistics reset: exposure 10 → 20 ms at 15:12:10". The first new frame after a change is also skipped, since `get_latest_frame()` may still hold one taken with the old settings.
- **"Log to CSV".** While accumulating: `data/count_log_<YYYY-MM-DD_HH-MM-SS>.csv` (gitignored), columns `timestamp, mean_counts, exposure_ms, binning, roi` (`roi` "x0 y0 x1 y1" in sensor pixels or "full"), one row per accepted sample, flushed per row so a crash keeps them. Stop or unticking closes the file; the next Start opens a new one. A write error stops logging and is shown in the panel, never raised into the live view. Closed in `_shutdown_hardware()`.
- **Cost, measured with `--sim` (2026-11-24):** `update_image()` median 0.297 ms with the panel vs 0.280 ms without (64×64 sim frames). `frame_sample()` on uint16: 2.9 ms for a full 2560×2160 Neo frame, 0.23 ms for 800×600 (an exact int64 sum; float64 was 3.7 ms). For comparison, `ImageView.setImage` + paint of a full frame took 14 ms.
- **Signals for other consumers:** `sample_accepted(t, value)` for every accepted frame (accumulating or not; `t` on the panel's `clock`) and `samples_invalidated(reason)` on a settings change. The pre-experiment stability check uses them instead of computing its own samples.
- **Tests:** `tests/test_count_statistics.py`, including the real `MainWindow(sim=True)` with `exclusive_camera_access()`.

## Count stability check before experiments

**Why.** Fluorescence wanders by ~1 % with 25–40 s oscillations when focus or air flow is unsettled, enough to spoil a long run. Optionally, a run waits until the live counts are stable, at the cost of time.

**Where the code is.**
- `framework/stability.py` (GUI-free): `StabilitySettings`, `window_statistics()`, `evaluate()`, `record()`, `status_text()`.
- `gui/stability_gate.py`: `StabilityMonitor` (one per app), `StabilityCheck` (one per wait), `StabilityWaitController` (per window).
- `gui/widgets/stability_check_group.py`: `StabilitySettingsGroup` ("Stability check" group) and `StabilityWaitBar`.

**Samples.** The same per-frame mean counts as the count-statistics panel: `CountStatisticsPanel.sample_accepted(t, value)` fires for every accepted live frame (repeats already dropped), and `samples_invalidated` on an ROI/exposure/binning change, which restarts the check's window. Nothing here touches the camera.

**Live view.** `StabilityMonitor` is built by `MainWindow` with `start_stream()`/`stop_stream()`/`timer.isActive()`. If the live view is off when a check begins, it is started; when the last check ends it is stopped again — **before** the experiment is started, which then takes the camera as usual. A running live view is left running.

**Rule** (`evaluate()`), over samples with `now − window_s < t ≤ now`:
- relative std = sample SD (ddof=1) / mean × 100 ≤ "Max std (%)";
- |drift| = |least-squares slope| × 60 / mean × 100 ≤ "Max drift (%/min)" (limits inclusive);
- and the window is really covered: `window_s` since the check (or last settings reset) began, ≥ 10 samples, first sample ≤ 2 s after the window start, newest ≤ 2 s old.
- Evaluated at 4 Hz. Timeout from the check's start: "Start anyway" (default) starts, "Cancel this run" does not.

**Controls** (ODMR panel after "Delays"; Zero Field after "Multi-Scan Averaging"; all with tooltips): "Check count stability before start" (default off), "Window (s)" 60, "Max std (%)" 0.30, "Max drift (%/min)" 0.50, "Timeout (s)" 600, "On timeout". The fields are enabled only with the box checked.
- **Not part of `get_config()`**: they decide when a run starts, not how it scans. Like the windows' other settings they live in the form for the window's lifetime; nothing is written to `setupInfo.json`.
- A window built without a monitor (tests, `hardware_manager=None` harnesses) disables the group.

**While waiting** the run button is grey and disabled, "Waiting for stable counts". The wait bar below the scroll area (outside it, always visible) shows e.g. "Waiting for stable counts: std 0.62% (limit 0.30%), drift −0.80%/min (limit 0.50%/min), window 45/60 s, 45 s elapsed (timeout 600 s)", with "Start anyway" and "Cancel".
- Zero Field also mirrors the line in its status label. The form is snapshotted at the click.
- Zero Field checks the field range **before** waiting.
- Closing the window, or `request_stop()`, cancels a wait. No `run_started` is emitted until the run really starts, so the magnet lock and `MainWindow`'s worker registry are unaffected.

**Start paths.**
- ODMR: `toggle_odmr()` → `request_start()` → wait → `run_odmr(stability_record=...)`.
- Zero Field: `start_scan()` → wait → `_start_run(..., stability_record=...)`.
- With the box off both call exactly what they called before (`run_odmr()`, `_start_run(config, output_directory)`); a queue entry without the check calls `_start_run(..., interactive=False)` with no extra argument.

**Metadata.** `record()`'s keys: `stability_check_enabled`, `stability_window_s`, `stability_max_std_percent`, `stability_max_drift_percent_per_min`, `stability_timeout_s`, `stability_on_timeout`, `stability_outcome` ("passed", "timed out, started anyway", "started anyway by user"), `stability_std_percent` / `stability_drift_percent_per_min` (over the window at the moment of the decision; null if not computable), `stability_wait_s`.
- ODMR: added to the run's config, so they are in `config.json` and the statistics cube's metadata.
- Zero Field: top-level keys of every saved file's metadata, not inside `scan_parameters`.
- **With the check off the only addition is `stability_check_enabled: false`** (that is how "not used" is recorded in files).

**Queue.**
- Each entry is checked with its own settings just before it starts, after the pause. The wait line goes to the queue status, the window status and the wait bar; no dialogs.
- Timeout with "Cancel this run" → "skipped (unstable)", message "not stable within T s (std …, drift …)", and the queue continues.
- "Cancel" or "Stop current run" during a wait → stopped ("cancelled during stability check"), and the queue continues. "Stop queue" during a wait → stopped ("queue stopped during stability check"), and the rest stay pending.
- The finished summary adds ", N skipped (unstable)" only when N > 0.

**Tests:** `tests/test_stability_check.py`. Two existing layout tests that list every control group (`test_odmr_window_layout.py`, `test_zero_field_queue.py`) now include "Stability check".

## ODMR window layout

Same shape as the Zero Field window.
- **Left column** (`ODMRPanel.left_panel`): the run/stop button fixed at the top, the progress bar, current-frequency label, time label and "Field:" status line (`field_label`) fixed directly under it (outside the scroll area), and below them `control_scroll_area` (vertical scrollbar as needed, horizontal always off) holding the "ODMR Parameters" group (`control_box`) with every other control in its original order (plus the "Magnetic field during scan" group after the dense-region section and the "Stability check" group after Delays). The plot is outside it. Below the scroll area is the stability-check wait bar, hidden unless waiting.
  - The scroll area's minimum width is the controls' size hint **measured with the Dense ESR Region, Dip 2 and Delays sections shown**, plus the scrollbar, so expanding them never clips horizontally.
- **Run/stop button.** One toggle driving `ODMRWindow.toggle_odmr()`; Stop goes through the unchanged `stop_odmr()` path. Styled by `ODMRPanel.set_run_button_state()` with the shared `style_run_button()`: idle "Start" on green, running "Stop" on red (previously "Run ODMR"/"Stop ODMR").
  - It follows the window's own `run_started`/`run_finished` signals, not the click. `run_finished` is emitted from `odmr_finished()`, which the worker's `finished_signal` reaches on completion, stop and error, so all three return it to "Start".
  - `ODMRPanel.set_running_state()` was removed; `run_odmr()`/`odmr_finished()` no longer style the button directly.
- **Default size** is 1200×900, clamped to the screen's available area (minus 40 px for the title bar). It fits 1920×1080 with the taskbar. The controls now scroll even when collapsed: the control box's size hint is about 1148 px collapsed and 1716 px with Dense, Dip 2 and Delays expanded (measured 2026-11-25; it was about 734 px before the "Magnetic field during scan" and "Stability check" groups were added).
- The layout change left controls, defaults and `get_config()` unchanged; the later "Magnetic field during scan" group added two `get_config()` keys (see "ODMR magnetic field during scan"). **Tests:** `tests/test_odmr_window_layout.py` (real scans on sim hardware).

## Known hardware quirks (ODMR timing)

**`trigger_delay_s` below ~0.02s triggers a reproducible ~400ms per-frame stall, cause unknown.** Confirmed from `data/odmr_timing_*.csv` timing-diagnostics recordings: at `trigger_delay_s = 0.0`, roughly 1 frame in 9 (10.4-10.8% in two independent recorded runs) takes ~400-430ms instead of the normal ~90ms — a distinct second mode in the `repeat_total` distribution, not scattered jitter. At `trigger_delay_s >= 0.02s` the second mode disappears entirely (tightest recorded spread: ~130-133ms, no bimodality). This has not been root-caused — candidates include the Andor SDK3 buffer/trigger-arming not having settled from the previous frame, but this is speculation, not confirmed. **Do not push `trigger_delay_s` toward 0 to save time even though MW switch-gating contrast is indifferent to it** — the stall more than cancels any savings and makes scan duration unpredictable. `gui/odmr_window.py::estimate_odmr_time()` does not model this (see the comment at its `trigger_delay_s`-dependent term) — its estimate for `trigger_delay_s < 0.02s` will run 15-30% low for this reason, not because the formula itself is wrong.

**`fire_delay_s` below ~0.010s triggers intermittent 300-1000ms stalls in `pulse.run()`, not a network issue.** Confirmed from four paired recordings (500 frames each, 50 points/repeats=5/trigger_delay_s=20ms/pulse_lead_s=0/exposure=10ms): the count of `pulse_run` calls taking >100ms matched the count of >300ms frames exactly in all four runs (13 at 5/5, 0 at 10/10, 7 at 10/5, 2 at 20/20). `reset_delay_s` alone does not fix it — the 10/5 run (`reset_delay_s`=10ms, `fire_delay_s`=5ms) still stalled — so the delay that matters is `fire_delay_s` specifically, the wait before `pulse.run()` is dispatched. 200 pings to the Swabian during a stalled run showed 0% loss and max 0ms, ruling out the network; the Swabian itself appears to need recovery time before accepting a new streamed sequence. `fire_delay_s = 0.010s` eliminated the stalls entirely across the tested runs and was faster overall than `0.005s` once the stall tail is counted (32.16s vs 33.66s scan total). Both `fire_delay_s` and `reset_delay_s` GUI/code-fallback defaults were moved from 0.005s to 0.010s for this reason. The stalls also degrade data quality, which is a stronger argument for the 10ms default than the timing alone: six consecutive identical scans after the fix gave contrast repeatability of ±5.8% relative SD (range 2.591–3.040%, mean 2.861%), while six comparable scans taken while the stalls were occurring gave ±12.3% (range 2.081–2.938%, mean 2.591%) — the stalls roughly doubled measurement scatter, consistent with a frame blocking 300-1000ms breaking the OFF/ON common-mode pairing the repeats loop depends on.

**Andor Neo exposure quantises to a fixed ~3µs offset, not a proportional one.** Measured at the rig: requested 10.000ms → camera held 9.997ms (−3µs); requested 20.000ms → camera held 20.003ms (+3µs). The sensor rounds to the nearest whole row period, so the offset's *magnitude* doesn't scale with the requested exposure. This is why `ODMRExperiment._EXPOSURE_DRIFT_EPSILON_S` (the requested-vs-actual-exposure warning in `configure_acquisition()`) uses an absolute 100µs threshold rather than a relative one — a relative threshold would be the wrong shape even where it happened to pass at these two points.

**Sensor readout ≈ 7.3ms, measured at the ROI currently in use.** Derived from `camera.wait_buffer`'s duration minus the configured trigger delay and exposure; consistent across recorded runs at that ROI. Readout scales with ROI/row count on an sCMOS sensor, so this number is conditional on ROI — re-measure if the acquisition ROI changes materially.

**Camera arm + disarm ≈ 160ms total, paid once per scan.** See "Camera acquisition is held open across a whole ODMR scan" above — this is the `CAMERA_ARM_DISARM_S` constant in `gui/odmr_window.py::estimate_odmr_time()`, derived the same way from recorded timing-diagnostics data.

## Known hardware quirks (Zero Field magnet / power-supply SCPI traffic)

**A ZFE run could command zero field at the diamond regardless of sign — root-caused to an SCPI input-buffer overrun on the E363x power supplies, fixed 2026-09-13.** Rig symptoms: the supplies beeped continuously during ZFE runs and accumulated errors; after clearing them, a single-axis run produced Error 521 (input buffer overrun) and Error −113 (undefined header). Root cause was two compounding sources of unpaced traffic, both now fixed: (1) `MagnetAxis.set_field()`'s non-zero branch called `power_supply.set_current()` twice with a provably identical value (a pure recompute of the same inputs) — removed; (2) `Magnet.set_vector()` commanded all three axes on every scan point with zero inter-command delay and no flow control on `SerialDevice` (9600 baud, no `write_timeout`), across three serial links — six real SCPI writes per point before the fix. Because every axis's `magnetic_field_offset` in `config/setupInfo.json` is nonzero, `MagnetAxis.set_field()`'s true-zero branch (`abs(current) < 1e-9`) was already unreachable on this rig's calibration, so even the two axes nominally "held at zero" under `zero_other_axes` were hitting the double-write branch on every point.

Fixed by, in `hardware/magnet/`: removing the double-write; adding a per-axis change-detection cache (`MagnetAxis._last_confirmed_current`) that skips a write only when the requested value already matches what a previous write *confirmed* (assigned after `power_supply.set_current()` returns without raising, never before — a dropped/failed write must not be cached as success, or every later identical request would be wrongly skipped forever); invalidating that axis's current assumptions when its output state or independently owned polarity relay changes; and adding `Magnet.scpi_write_delay_s`, a fixed pause after a write that actually reaches a supply, never after a change-detection skip. A matching magnitude on the opposite polarity branch is not an unchanged physical command. Target field, current magnitude, and polarity are cached separately; nominal 0 G means calibrated background compensation, not 0 A. Pacing remains configurable via `Helmholtz.scpi_write_delay_s` in `config/setupInfo.json` (default 0.05s — **a starting guess, not a measured value**; tune at the rig the same way `fire_delay_s`/`reset_delay_s` were tuned for the Pulse Streamer, see below).

**The per-write cache update only guards against a write that raises a Python exception — it cannot, by itself, detect a SCPI-level error queued on the instrument** (pyserial's `write()` does not raise for a 521/-113 condition; that would require reading the error queue after every write, which is exactly the round-trip cost avoided below). `Magnet.check_error_queues()` — off by default, wired into `ZeroFieldExperiment` via `check_power_supply_errors` (constructor flag or `NV_ZFE_CHECK_PS_ERRORS=1`, same shape as `timing_diagnostics`/`NV_ZFE_TIMING`) — is the backstop for that residual gap: it polls each axis's `SYST:ERR?` (via `BaseSCPIPowerSupply.check_error_queue()`) and logs loudly if non-empty, called only at scan start (`setup_scan()`, right after `enable()`) and scan end (`cleanup_scan()`, right before `disable()`) — never per point or per write — so it costs nothing in the hot path even when enabled. Before this, the drivers had no way to detect, surface, or recover from the error state they were causing, which is why this went undiagnosed across weeks of Zero Field measurements.

## Measured acquisition parameters (ODMR)

These are characterized properties of the current rig/sample, not code facts — re-measure before relying on them if the sample, ROI, or laser power changes.

**10ms and 20ms exposure gave statistically equivalent contrast (2.794% vs 2.814%) at ~20,000 counts on a bright ensemble, repeats=5.** 10ms is preferred for scan speed at this signal level. **This is conditional, not universal**: on a dimmer sample the measurement becomes photon-limited and a longer exposure would win instead. Don't treat 10ms as a correct default independent of signal level.

**Camera baseline (dark counts) ≈ 120, against ~20,000 counts of signal.** Baseline subtraction (`baseline_counts`, see `ODMRExperiment.process_frame()` and `normalize_odmr_pair_per_pixel()`) changes contrast by ~0.6% at this signal level — below the measurement floor documented below (so it's not distinguishable from noise here), but becomes material once the signal drops below roughly 1000 counts, where 120 is no longer a small correction.

**Measurement floor: contrast repeatability is ±5% relative at repeats=5.** Any change claimed to have a smaller effect than that needs repeated runs in a single session, same ROI and laser power, to be credible against this dataset's own noise — a single before/after comparison at this repeats setting cannot distinguish a real few-percent effect from run-to-run noise.

**The per-repeat-ratio averaging in `ODMRExperiment.process_frame()` was analysed and deliberately left unchanged.** It computes contrast as mean(I_on/I_off) across repeats, not the ratio of pooled sums ΣI_on/ΣI_off — even though the pooled-sum ratio is the formally unbiased estimator, since E[A/B] ≠ E[A]/E[B]. The bias is driven only by the component of noise uncorrelated between the OFF and ON frames of a pair: common-mode fluctuation (laser drift slower than the ~100ms pair separation) divides out of each ratio exactly and contributes zero bias to either estimator, and the residual bias scales as the square of the uncorrelated relative noise on I_off. At ~20,000 counts over the working ROI, simulated with 7% slow common-mode drift plus 1% fast per-frame wobble, the bias is +0.006%, against the measured ±5.8% contrast repeatability above. **Do not change this without first establishing that the uncorrelated noise fraction has grown** — e.g. much lower signal, a much longer OFF→ON separation, or a laser noisy at the pair-separation timescale. Pooling also does not reduce variance: to first order both estimators have identical variance, and the only difference is bias.

## Testing safety — read before running pytest with `--hardware`

`tests/` contains 103 test files (excluding `__init__.py`/`conftest.py`). Only 46 are real, hardware-free pytest tests — see `tests/conftest.py::ALLOWED_FILES` for the current, authoritative list rather than a copy here that will drift the next time a file is added to it (as just happened for this change). **The other ~57 are ad-hoc scripts that command real lab hardware — the Andor camera, the SG386 microwave source over VISA, the Swabian pulse streamer, the Helmholtz power supplies — unconditionally at module import time, not just inside a test function.** Several have no `if __name__ == "__main__":` guard at all: importing them fires a relay, streams a pulse, or turns on a power-supply output as a side effect of Python loading the file.

`pytest.ini` (`testpaths = tests`) plus `tests/conftest.py`'s `pytest_ignore_collect` hook gate this: a bare `pytest` run only collects the files named in `ALLOWED_FILES`. The gate is an allowlist, not a denylist — anything not explicitly named there is excluded from collection entirely, so it is never imported. Passing `--hardware` lifts the restriction and collects everything, including every hardware-commanding script.

**Never run `pytest --hardware` (or `python` on any of the gated files directly) without first confirming real instruments are powered, connected, and safe to command** — this includes verifying no one is near the magnet coils before anything that calls `magnet.enable()`/`set_vector()`, and that the camera/pulse-streamer/microwave are in a state where an unattended trigger or relay toggle is safe. `--collect-only --hardware` is **not** safe either — collection still fully imports every file, which is enough to trigger the unconditional module-level hardware calls in most of them.

**GUI tests and pyqtgraph teardown.** `tests/conftest.py::pytest_sessionfinish` calls `clear_view_registry()` (only if pyqtgraph was imported), for the same reason as the app's close path. Without it, a full run printed that `QComboBox has been deleted` RuntimeError after the summary. The minimal trigger was three files together: `test_zero_field_reference.py`, `test_zero_field_software_acquisition.py` and `test_zero_field_window_save_toggle.py`. The error never failed a test or changed the exit code.
- Before this fix, more than two `ZeroFieldWindow`s per process segfaulted at shutdown. So `test_zero_field_window_save_toggle.py` shared one window and `test_colormap_selectors.py` kept its windows alive. Both now build and destroy a window per test, which was clean in 5 consecutive full runs.
- `test_zero_field_reference.py` and `test_zero_field_bidirectional.py` still keep their widgets alive (`_KEEP_ALIVE`). Several tests still use stand-ins instead of extra windows. These were not re-tested without the workaround.
- A shutdown crash or teardown traceback reappearing should be reported, not worked around.

## Repository hygiene

data/__init__.py and data/data_manager.py are tracked source files. The .gitignore uses data/* with negation rules to keep them tracked while excluding HDF5/CSV output files. The neighbouring `Data/` rule is written as `Data/*` for the same reason: this repo runs with `core.ignorecase=true` on Windows, where a bare `Data/` also excludes the `data/` directory itself, and git cannot re-include a file whose parent directory is excluded. Do not revert either rule to a bare directory pattern; check with `git check-ignore -v --no-index data/data_manager.py` (it should report the `!` negation line).
