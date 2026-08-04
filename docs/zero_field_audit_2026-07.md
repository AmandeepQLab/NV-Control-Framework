ZERO FIELD IMAGING PHYSICS & LOGIC AUDIT
==========================================

STATUS HEADER — added 2026-08-04, do not remove
--------------------------------------------------
**What this file is:** a manual code-and-physics audit of the Zero Field
magnetometry experiment (`experiments/zero_field_experiment.py` and its
GUI), performed by inspection only. No code was changed to produce the
original audit or this re-verification pass.

**Originally written:** 2026-07-19, committed in `3403891` "Add Zero
Field analysis separation and multi-scan averaging" — the same commit
that introduced the multi-scan-averaging rewrite Section 1's walkthrough
now predates (see STALE note below).

**Re-verified against the code:** 2026-08-04. Every numbered finding in
Section 10 has been re-checked against the current source and is tagged
inline as **[FIXED]**, **[PARTIALLY FIXED]**, or **[OPEN]**, with the
resolving file, commit, and date cited. Section 8's "missing metadata"
list is annotated the same way, item by item. Sections 1–9 (the
walkthrough/description) are left as originally written except for the
one staleness note below — they were not re-verified line by line.

**STALE:** Section 1 ("Complete Execution Flow") describes
`ScanExperiment.run()` driving the scan loop directly. As of the current
`experiments/zero_field_experiment.py`, `ZeroFieldExperiment` overrides
`run()` itself to add multi-scan averaging, exclusive camera access, and
raw-scan persistence, calling a private `_run_single_sweep()` helper to
do what Section 1 describes. Section 1 is left unedited below as a
historical description of the original single-scan-only flow — do not
use it as a description of the current control flow. See `CLAUDE.md`
for the current architecture and call chain.
--------------------------------------------------


1. COMPLETE EXECUTION FLOW
--------------------------

1. The user opens Zero Field Imaging from MainWindow.open_zero_field_window().
2. The user presses Start Scan in ZeroFieldWindow.
3. ZeroFieldWindow.start_scan():
   - Reads the GUI configuration: start/stop field, point count, axis,
     settling time, averages, exposure, and binning.
   - Obtains the current display ROI if “Use ROI selected in main camera view”
     is enabled.
   - Applies exposure and binning to the camera.
   - Creates metadata containing scan and camera parameters.
   - Creates a QThread and ZeroFieldWorker.
4. ZeroFieldWorker constructs ZeroFieldExperiment and calls run().
5. ScanExperiment.run():
   - Creates a fresh ImageCube.
   - Calls ZeroFieldExperiment.setup_scan().
   - Iterates scan points in order:
       a. Set the magnetic field.
       b. Wait the settling time.
       c. Acquire and average image frames.
       d. Add the averaged frame to ImageCube.
       e. Compute mean ROI fluorescence.
       f. Emit a live update.
6. The worker forwards live data to the GUI.
7. On completion, the worker emits the populated ImageCube and enables Save.
8. The user presses Save Data, chooses a .npz path, and ImageCube.save()
   writes the data and metadata.


2. SCAN GENERATION
------------------

ZeroFieldExperiment.setup_scan() uses:

    np.linspace(field_start, field_stop, field_points)

This is correct for the requested scan:

- It produces exactly field_points values.
- It includes both start and stop endpoints.
- It preserves ascending or descending order according to the requested
  endpoints.
- Scan-axis values are stored in gauss (G).


3. MAGNET CONTROL AND UNITS
---------------------------

The GUI accepts fields in gauss. For every scan value B_G, the selected-axis
field is converted to millitesla:

    B_mT = B_G * 0.1

This is correct: 1 G = 0.1 mT.

For each point, set_scan_point() copies the magnet's configured vector and
replaces only the selected component:

    fields_mT = dict(self._configured_field_mT)
    fields_mT[selected_axis] = value * 0.1
    magnet.set_vector(bx=..., by=..., bz=...)

Magnet.set_vector():

- Chooses one global polarity.
- Sets the X, Y, and Z coils sequentially.
- Converts requested field to current using each axis calibration:
  (field_mT - field_offset) / field_ratio.

Finding: only the selected axis is changed by the scan, but the other axes are
not guaranteed to be zero. Their values are preserved from magnet.get_vector()
at scan start. They are zero only if the magnet was already configured to zero
on those axes.

This matters because the magnet supports only a global polarity. A negative
scan on one axis with a retained positive field on another axis produces a
mixed-sign vector and Magnet.set_vector() raises an error.

**[OPEN — re-verified 2026-08-04]** Unchanged in the current
`experiments/zero_field_experiment.py`: `setup_scan()` still does
`self._configured_field_mT = self.magnet.get_vector()`, and
`set_scan_point()` still merges the swept axis into that preserved dict.
`Magnet._determine_global_polarity()` in `hardware/magnet/magnet.py`
still raises `ValueError` on mixed signs. No enforcement of a documented
non-swept-axis policy has been added. See Section 10, items 2–3.


4. TIMING SEQUENCE
------------------

For every scan point, ScanExperiment.run() executes:

    set_scan_point(value)
    ↓
    sleep(settling_time_ms / 1000)
    ↓
    acquire_frame()

Therefore, within the scan loop, a scan frame cannot be requested before the
magnetic-field command has been issued and the configured settling delay has
elapsed.

This verifies command order, not physical field confirmation. There is no
power-supply readback, field-sensor readback, or “field settled” acknowledgement.
The settling delay is open-loop.

**[OPEN — re-verified 2026-08-04]** No readback capability exists
anywhere in `hardware/magnet/` or `hardware/power_supply/` as of this
verification. See Section 10, item 4.


5. CAMERA ACQUISITION
---------------------

Zero Field acquisition is implemented as:

    frames = [camera.snap() for _ in range(averages)]
    return np.mean(np.stack(frames), axis=0)

- Number of camera acquisitions per field point: exactly averages.
- Exposure: set once before starting the worker from the GUI value.
- Binning: set once before starting the worker from the GUI value.
- Averaging: pixel-wise average of complete image frames.
- Fluorescence is calculated after image averaging, rather than averaging
  separately processed scalar fluorescence values.
- For averages = 1, one frame is acquired; the one-frame mean is numerically
  unchanged, though NumPy may promote its dtype.

The active HardwareManager constructs AndorNeoAndor3. Its snap() method uses
software triggering: it queues one buffer, starts acquisition, issues a
software trigger, waits up to 10 seconds, then stops and flushes acquisition.

**[NOTE — re-verified 2026-08-04]** `HardwareManager` now also supports
`SimCamera` (selected via `spcm.type: "sim"` in config, or the app's
`--sim` launch flag) and continues to hardcode `AndorNeoAndor3` for the
real path when `spcm.type` is `"Andor"`. The description of
`AndorNeoAndor3.snap()`'s software-trigger sequence is still accurate
for the real driver. This addition postdates the original audit and is
unrelated to any of its findings.


6. ROI PROCESSING
-----------------

Zero Field processing calls:

    mean_fluorescence(frame, roi)

Behavior:

- No ROI: np.mean(full_frame).
- ROI present: np.mean(frame[y0:y1, x0:x1]).
- The reported signal is mean camera counts per pixel.

ROI concerns:

- There is no explicit ROI validation.
- There is no explicit bounds validation or clipping.
- Positive out-of-range slice limits are silently clipped by NumPy.
- Negative coordinates use Python negative-index semantics and may select pixels
  from the opposite edge rather than clipping to zero.
- Reversed, zero-area, or empty ROIs yield an empty array and therefore NaN,
  with a NumPy warning.
- An invalid ROI tuple raises during unpacking or slicing.
- The ROI is taken from the main display but is not transformed after the Zero
  Field window changes binning. A ROI selected at a different binning can refer
  to different physical pixels or become out of range.

**[FIXED — 2026-07-19, `framework/roi.py` + `experiments/zero_field_experiment.py` + `framework/analysis/zero_field.py`, commit `3403891`]**
Fixed by two changes together, not a patch to the old call site:

1. `framework/roi.py` now provides `validate_roi()`/`clip_roi()`/`extract_roi()`,
   which reject non-numeric, non-finite, reversed, and zero-area ROIs with a
   clear `ValueError`, and clip in-range instead of relying on NumPy's silent
   negative-index/out-of-range slicing. `ZeroFieldExperiment.__init__` calls
   `validate_roi(acquisition_roi)` before the scan can start.
2. The "stale ROI after a binning change" bug class is eliminated
   architecturally, not reconciled: `mean_fluorescence_vs_field()` in
   `framework/analysis/zero_field.py` now calls `mean_fluorescence(frame)`
   with **no ROI argument at all** — the acquisition ROI is applied once, in
   hardware, via `camera.set_roi()` in `ZeroFieldExperiment.run()`, and
   analysis deliberately never re-crops. There is no second ROI-application
   site left for a later binning change to invalidate. See the function's own
   docstring: "a hardware acquisition ROI is already applied by the camera
   and must not be cropped again during analysis."

See Section 10, items 5–6.


7. LIVE GUI UPDATES
-------------------

Live updates occur after:

1. The averaged frame is acquired.
2. The averaged frame is added to ImageCube.
3. Fluorescence is processed.

The experiment callback sends the requested scan value, processed fluorescence,
and latest image to the worker. The worker appends the field and signal together,
emits the live update, then emits progress. The GUI updates the plot and labels
from those paired arrays.

The displayed field and fluorescence therefore correspond to the same completed
scan point in normal queued-signal order.

Caveat: the displayed field is the requested scan value, not a hardware readback
value. The UI does not verify the actual coil field.

**[OPEN — re-verified 2026-08-04]** No field readback exists; the caveat
still applies unchanged.


8. IMAGECUBE CONTENTS AND METADATA
----------------------------------

Each completed scan stores:

- data: stack of averaged image frames, one frame per completed point.
- scan_axis_name: “Magnetic Field”.
- scan_axis_unit: “G”.
- scan_axis_values: requested np.linspace field values in gauss.
- metadata["scan_parameters"]:
  - field_start
  - field_stop
  - field_points
  - field_axis
  - settling_time_ms
  - averages
  - exposure_s
  - binning
- metadata["camera_parameters"]:
  - exposure_s
  - binning
  - roi
- axes: empty dictionary.
- experiment_type: “Unknown”.

**[NOTE — re-verified 2026-08-04]** The metadata shape above (a
`scan_parameters`/`camera_parameters` nested structure) no longer
matches the current code exactly — `ZeroFieldExperiment._set_static_metadata()`
now writes a flatter set of top-level keys (see below). Treat this
bullet list as historical; the "Missing metadata" list immediately below
is what was re-verified.

Missing metadata of scientific value, re-verified 2026-08-04 against
`ZeroFieldExperiment._set_static_metadata()`, `_set_averaging_metadata()`,
and `_finalize_image_cube_metadata()` in `experiments/zero_field_experiment.py`
(commit `6beb435`, 2026-08-04, "WIP: zero-field imaging and image inspection"):

- Acquisition timestamp and timezone.
  **[FIXED — 2026-08-04, commit `6beb435`]** `acquisition_started_at_utc`,
  an ISO-8601 UTC timestamp, is now recorded.
- Actual magnet vector sent for every point.
  **[OPEN]** Only the single swept-axis values (`field_values_gauss`) are
  recorded; the full 3-axis vector actually sent per point (including the
  retained non-swept components) is not.
- Initial retained non-swept-axis fields.
  **[OPEN]** `_configured_field_mT` is computed at scan start but never
  written into metadata.
- Global polarity state and polarity transitions.
  **[OPEN]** Not recorded anywhere.
- Magnet calibration values/version.
  **[PARTIALLY FIXED — 2026-08-04, commit `6beb435`]** `magnet_configuration`
  (a deep copy of `magnet.config`, including per-axis `magnetic_field_ratio`/
  `magnetic_field_offset`/`max_current`) is now recorded. There is no
  explicit calibration *version* field.
- Hardware field/current readbacks, if available.
  **[OPEN]** No true hardware readback exists in `hardware/magnet/` —
  `MagnetAxis.get_current()` returns a software-tracked value
  (`self.current_current`, set by `set_field()`), not a supply measurement.
- Confirmation of actual camera exposure and binning readback.
  **[PARTIALLY FIXED — 2026-08-04, commit `6beb435`]** `camera_exposure_s`/
  `camera_binning` are now recorded via `getattr(self.camera, "exposure_time"/"binning")`
  — but this reads the driver object's own cached Python attribute (what it
  believes it set), not an independent SDK-level query of the camera's actual
  state.
- Frame dimensions and dtype as explicit metadata.
  **[FIXED — 2026-08-04, commit `6beb435`]** `image_height_px`,
  `image_width_px`, `image_dtype` are now recorded by
  `_finalize_image_cube_metadata()`.
- ROI coordinate system and binning used when the ROI was selected.
  **[FIXED, indirectly — see Section 6]** The underlying risk (a stale ROI
  reinterpreted after a binning change) is eliminated architecturally, not
  by adding a coordinate-system metadata field. No such field exists, but
  none is needed given the fix described in Section 6.
- Explicit fluorescence-definition metadata.
  **[OPEN]** No metadata field states that fluorescence means "mean counts
  per pixel of the averaged frame" — it remains implicit in code.
- Whether the scan completed normally, stopped early, or failed.
  **[OPEN]** No status/completion flag is recorded in metadata.


9. EXPERIMENT TERMINATION AND SAFE STATE
----------------------------------------

Normal completion:

- ScanExperiment.run() exits normally.
- Its finally block calls ZeroFieldExperiment.cleanup_scan().
- cleanup_scan() calls magnet.disable().
- magnet.disable() sets coil currents to zero, synchronizes positive polarity,
  and turns all three outputs off.
- The worker returns the completed ImageCube.

User presses Stop:

- The GUI calls ZeroFieldWorker.stop().
- The worker sets its cancellation flag and calls experiment.stop().
- BaseExperiment.stop() sets stop_requested=True and running=False.
- The scan loop checks this only between points.
- An in-progress settling delay or camera acquisition is not interrupted; that
  point may still be acquired, added to the cube, processed, and displayed
  before the next loop iteration exits.
- Cleanup then disables the magnet.

Camera failure:

- An exception from camera.snap() propagates through ScanExperiment.run().
- The finally block still attempts magnet.disable().
- The worker emits an error and then failed completion with image_cube=None.

Magnet communication failure:

- Failure during enable, vector setting, or another magnet operation likewise
  propagates.
- The scan finally block attempts to disable the magnet.
- The GUI receives error and failed completion.

Safety conclusion: normal, stopped, camera-error, and magnet-error paths all
attempt to disable the magnet. This is good. Safety is not absolute if
magnet.disable() itself fails; there is no fallback, retry, or independent
emergency shutdown path. Camera acquisition is not explicitly stopped or reset
by Zero Field cleanup.

**[re-verified 2026-08-04]** "Magnet disable has no fallback/retry":
**[OPEN]**, unchanged — `cleanup_scan()` is still a bare
`self.magnet.disable()` call. "Camera acquisition is not explicitly
stopped/reset by cleanup": **[FIXED — see Section 10, item 1]** — this
is now covered by `exclusive_camera_access`, which restores camera and
live-view state on every exit path, including exceptions, as a property
of the context manager `ZeroFieldExperiment.run()` now holds around the
whole scan.


10. PHYSICS AND IMPLEMENTATION CONCERNS
---------------------------------------

1. **[FIXED — 2026-07-19, `framework/camera_ownership.py` + `gui/live_view_controller.py`, commit `3403891`; hardened 2026-07-29, commit `828907b`]**
   HIGH — Concurrent camera streaming is not stopped before the Zero Field scan.
   The main live-view stream can continue calling camera.snap() while the worker
   also calls camera.snap(). The Andor software-acquisition sequence is not
   serialized by a camera-wide acquisition lock. This can cause camera-command
   races, incorrect frame association, failed acquisitions, or uncontrolled
   timing.

   Resolution: `ZeroFieldExperiment.run()` now wraps the entire scan in
   `framework.camera_ownership.exclusive_camera_access(self.camera)`. That
   context manager suspends the registered live-view `QTimer` —
   `LiveViewTimerController.suspend()` (`gui/live_view_controller.py`) calls
   `self._timer.stop()` directly — and stops the camera stream before
   yielding, restoring both afterward including on exception.
   `gui/main_window.py` registers itself via `register_live_view_controller`
   (line 326, 2026-07-19) and `register_camera_state_restorer` (lines 327-329,
   2026-07-29), and its only frame-pull during live view is
   `camera.get_latest_frame()` in `update_image()` — never `.snap()` — so
   there is no bypass of the ownership context. Verified directly: no call to
   `camera.snap()` exists anywhere in `gui/main_window.py`.

2. **[OPEN — re-verified 2026-08-04]**
   HIGH — Non-scanned axes are preserved, not forced to zero.
   The implementation scans one component while retaining any previously
   configured X/Y/Z components. This can alter the physical measurement or
   cause mixed-polarity failures.

3. **[OPEN — re-verified 2026-08-04]**
   HIGH — Negative scans require retained non-scanned fields to be zero or
   sign-compatible. The global-polarity design rejects mixed signs.

4. **[OPEN — re-verified 2026-08-04]**
   MEDIUM — No magnetic-field readback or settling verification.
   The order is correct, but settling is an assumed fixed delay. The displayed
   field is requested, not measured.

5. **[FIXED — see item 1 in this list's citation, plus Section 6]**
   MEDIUM — ROI validity is unchecked.
   Invalid, reversed, empty, negative, or binning-mismatched ROI coordinates
   can produce NaN, unintended pixels, or errors.

6. **[FIXED — see Section 6]**
   MEDIUM — ROI and binning coordinate consistency is unspecified.
   The experiment changes binning after obtaining the display ROI, with no
   coordinate conversion or validation.

7. **[PARTIALLY FIXED — 2026-08-04, commit `6beb435`; see Section 8 for the itemized breakdown]**
   MEDIUM — Metadata omits actual applied conditions.
   Requested parameters are stored, but actual magnet state, polarity, readbacks,
   timestamp, calibration, and completion status are not.

8. **[OPEN — re-verified 2026-08-04]**
   LOW — Stop is cooperative, not immediate.
   A stop request waits until any current sleep/acquisition completes.

9. **[OPEN — re-verified 2026-08-04]**
   LOW — ImageCube stores averaged frames only.
   Individual repeats are discarded, preventing later assessment of frame-level
   noise, drift, or outliers. Note: this is distinct from the newer multi-*scan*
   raw-saving feature (`save_raw_scans`/`raw_scan_saver`), which persists whole
   averaged sweeps, not per-point camera repeats — the two should not be
   conflated.


11. SUGGESTED IMPROVEMENTS, RANKED
----------------------------------

1. Serialize camera ownership during scans: stop or suspend live streaming
   before Zero Field acquisition, and restore it afterward.
   **[DONE — see item 1 above]**
2. Define and enforce the intended non-swept-axis policy: explicitly require
   zero, preserve a documented bias vector, or reject incompatible pre-existing
   fields.
   **[OPEN]**
3. Add ROI bounds/area validation and define ROI behavior across binning changes.
   **[DONE — see Section 6]**
4. Add magnetic-field/current readback where hardware permits; otherwise record
   that settling is open-loop.
   **[OPEN]**
5. Record actual scan vectors, initial vector, polarity, calibration identity,
   timestamp, completion status, and camera readbacks in metadata.
   **[PARTIALLY DONE — see Section 8]**
6. Make Stop semantics explicit in the UI: “stops after current acquisition,”
   or provide hardware-supported cancellation.
   **[OPEN]**
7. Optionally retain repeat-level data or summary statistics for scientific
   quality control.
   **[OPEN]**
