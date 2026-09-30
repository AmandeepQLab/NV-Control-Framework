# NV Control Framework - Project Status Report

Review date: 2026-09-21 (Asia/Jerusalem). Repository: `C:\Lab_Py\nv_control`. Branch: `dev`. Inspected HEAD: `fa4acea323f055d832bd8444d6dbc8e59a265ea6`.

This baseline describes the working tree, including its uncommitted calibration changes and untracked analysis scripts. Review activity was read-only except replacement of this requested report. No application was launched against instruments; no hardware was contacted; no code, configuration, tests, or commits were changed. Test execution and its isolation are documented in section 20.

Evidence convention: `path:line` refers to the current source, not an older report. **Implemented** means a working code path exists; it does not mean this review validated an instrument. **Partial** means a restricted or incomplete path exists. **Placeholder** means a visible stub without execution. **Absent/planned** means no implementation was found. Architectural intent is explicitly identified when inferred. Claims about previous rig measurements are repository documentation, not new measurements.

## 1. Executive Summary

The framework implements a Python/PyQt6 laboratory application with live Andor camera viewing, manual Helmholtz magnet control, microwave-frequency ODMR acquisition, and microwave-free Zero Field image acquisition. Real drivers exist for the Andor Neo SDK3 camera, SRS SG386 microwave source, Swabian Pulse Streamer, and Keysight/HP E3631A/E3632A supplies. `HardwareManager` constructs shared device instances; experiment windows create workers on Qt threads and inject those instances into experiment objects. The old `ExperimentRunner` is not the application's execution layer. See `main.py:1`, `gui/main_window.py:42`, `hardware/hardware_manager.py`, `gui/zero_field_window.py:185`, and `gui/odmr_worker.py`.

README and CLAUDE document real ODMR and Zero Field use, and the code contains substantive device-specific acquisition and timing work. That is evidence of a developed real-hardware path, not certification that this checkout and its current local calibration will produce correct measurements. This review deliberately did not connect hardware. In particular, the calibration in the working tree differs from HEAD on all three axes; the software does not validate those physical calibrations. The report distinguishes implemented commands, simulated tests, and historical rig claims throughout.

Zero Field is the stronger image-data workflow. The window takes Main Window acquisition settings, selects an axis and field vector in gauss, then the experiment obtains exclusive camera access, arms software-triggered acquisition once for the entire run, enables the magnet for each sweep, applies each field, waits, acquires and averages the requested number of frames, and stores one mean image per field point. A worker calculates raw mean fluorescence from the full acquired image and signals the GUI once per point. Optional multi-scan averaging retains a running average. A cooperative stop returns meaningful partial data or the average of completed sweeps. See `experiments/zero_field_experiment.py:253`, `:360`, `:399`, `:577`; `gui/zero_field_worker.py:237`.

Saving now defaults to HDF5. Single scans stream directly to their final file; repeated scans stream to per-scan files and save the running average after completed scans. Completion metadata distinguishes a complete sweep, a complete requested experiment, and a user stop. Saving can be turned off, leaving the result in memory for later Save Data. These are implemented behaviors, with limits: file flushing is not a transaction or a guarantee against power loss, and some error paths can bypass magnet cleanup. See `framework/image_cube.py:423`, `gui/zero_field_worker.py:76`, `gui/zero_field_window.py:410`, and section 22.

Several older assumptions would be wrong today. Zero Field **zeros non-swept axes by default**, rather than retaining manual settings. Its panel has no fixed-X/Y/Z compensation fields or exposure/binning controls; camera settings come from Main Window. There is **no custom reusable ImageViewer class**: Zero Field owns a `pyqtgraph.ImageView`, display caches, and shared pixel/line helpers. Difference display subtracts a copied first acquired image for the entire run and never converts the acquired cube into differences. The GUI's fluorescence is raw counts/pixel. Offline modules additionally support scan averaging, mean normalization, descriptive contrast, derivatives, and report figures; they do not implement an automated zero-field optimizer. See `experiments/zero_field_experiment.py:278`, `gui/widgets/zero_field_widget.py:119`, `gui/zero_field_window.py:216`, `analysis/zero_field_averaging.py:451`.

ODMR uses externally triggered OFF/ON image pairs with microwave switch gating on Pulse Streamer channel 2, held-open camera acquisition, configurable camera-baseline correction, and single/double Lorentzian fitting. Its GUI worker retains scalar frequency/signal/OFF/ON arrays, not the raw image cube; the separate programmatic `ODMRExperiment.run()` path uses the generic scan container. The GUI receives point results but does not redraw the spectrum from its point handler; refresh occurs on mode changes or scan completion. These distinctions matter when reusing ODMR for Zero Field development. See `experiments/odmr_experiment.py:484`, `:678`, `gui/odmr_worker.py:202`, `gui/odmr_window.py:208`.

Simulation is selectable globally with `--sim` or device by device through configuration types. The current camera simulator models shape, ROI/binning state, random frames, exposure waits, and an optional microwave-dependent Lorentzian intensity. It has no magnetic-field-dependent Zero Field physics and is not a physical interpretation tool. Its current switch-gating integration also limits the realism of simulated ODMR contrast. Rabi, Ramsey (button spelled Ramsy), T1, and T2 remain disabled placeholders; SmarAct, event/lock-in cameras, and Keithley have no supported driver path. See `hardware/camera/sim_camera.py`, `hardware/hardware_manager.py`, `gui/main_window.py:333`.

The principal immediate risks are concrete: GUI/worker lock inversion around camera ownership; uncoordinated manual magnet access and shutdown; a disk-error cleanup gap; local calibration values that can produce negative supply-current commands; and incomplete validation in offline analysis and generic data containers. The architecture already has useful boundaries, injection, ownership, and persistence mechanisms. Development should continue from these mechanisms, address the specific failure paths, and then improve the real Zero Field analysis workflow rather than restarting the framework.

## 2. Git / Repository State

| Item | Observed state before this report was written |
|---|---|
| Root | `C:/Lab_Py/nv_control` |
| Branch | `dev` |
| HEAD | `fa4acea323f055d832bd8444d6dbc8e59a265ea6` |
| Latest commit | 2026-09-13, fix magnet SCPI overrun: duplicate-write removal, change detection, write pacing |
| Staged changes | None; `git diff --cached --stat` empty |
| Unstaged tracked changes | `config/setupInfo.json`: 6 additions/6 removals, all X/Y/Z calibration ratio/offset values |
| Untracked files | `field_ratio.py`, `focus_vs_field.py` |
| Existing report | Tracked `PROJECT_STATUS_REPORT.md`, dated 2026-08-24, describing revision `9439c12`; replaced by this report |
| Inspection warning | Git/rg could not enumerate `.pytest_cache` because of permissions; source inspection and cache-disabled tests did not depend on it |

The report is **tracked**, despite an entry for it in `.gitignore` and recent commit messages mentioning removal. `git ls-files --stage -- PROJECT_STATUS_REPORT.md` returned an indexed file; ignore rules do not remove tracked files. Measurement files in `data/`, root CSVs, HDF5 files, and caches are not implementation evidence. However, ignored `data/data_manager.py` is an active local-only source dependency and was reviewed (section 17). No physical-data interpretation was performed.

Recent relevant commits:

| Commit | Date | Capability/change |
|---|---|---|
| `fa4acea` | 2026-09-13 | Magnet current-write deduplication/cache invalidation, per-write pacing, optional supply-error diagnostics |
| `f6479dd` | 2026-09-04 | SDK exposure limits, millisecond camera control, exposure-dependent drift tolerance |
| `2d6e03e` | 2026-09-01 | Zero Field Save data toggle and later in-memory saving |
| `9439c12` | 2026-08-11 | Tooltips describing measured acquisition behavior; exact revision named in previous report |
| `07de473` | 2026-08-11 | Held-open software-triggered Zero Field acquisition |
| `d37e1a4` | 2026-08-11 | Optional Zero Field timing instrumentation |
| `ff4b064` / `53e8545` | 2026-08-10 | Pulse delays increased to 10 ms; switch-based microwave gating |
| `4505dc7` / `a9a7c2d` / `837ef8b` | 2026-08-05 | Direct single-scan streaming, partial-scan correctness, incremental HDF5 persistence |
| `d0f81cd` | 2026-08-04 | Config-driven device selection and global simulation mode |
| `828907b` | 2026-07-29 | Major architectural milestone: acquisition state and camera ownership |
| `3403891` | 2026-07-19 | Zero Field analysis separation and multi-scan averaging |

Two comparisons are appropriate: **`9439c12..HEAD`** for changes since the identifiable previous report, and **`828907b..HEAD`** for the broader architectural evolution. The former contains 23 changed files and 1,970 insertions/90 deletions, including an added previous report; these statistics are not a measure of functionality. Section 21 groups the actual capabilities. Local calibration edits are outside both committed comparisons.

## 3. Current Project Structure

```text
nv_control/
  main.py                         -> gui.main_window.main
  config/                         config_manager.py, setupInfo.json
  hardware/
    hardware_manager.py           active construction/lifecycle
    camera/                       andor_neo_andor3.py, sim_camera.py,
                                  streaming.py; legacy andor_neo.py/base_camera.py
    microwave/                    sg386.py, sim_microwave.py
    pulse_streamer/               swabian_pulse_streamer.py, sim_pulse_streamer.py
    power_supply/                 base_power_supply.py, base_scpi_power_supply.py,
                                  e3631a.py, e3632a.py, sim_power_supply.py
    magnet/                       magnet.py, magnet_axis.py
    interfaces/                   serial_device.py
    sim_hardware.py               legacy simulation assembly
  framework/
    acquisition_state.py          user camera settings
    camera_ownership.py           camera lease/live-state coordination
    roi.py, fluorescence.py        shared image utilities
    base_experiment.py            state/stop API
    scan_experiment.py            generic point-sweep lifecycle
    image_cube.py                 NPZ/HDF5 and incremental writer
    paths.py                      output-directory validation
    analysis/zero_field.py        mean fluorescence versus field
  gui/
    main_window.py, live_view_controller.py
    zero_field_window.py, zero_field_worker.py, widgets/zero_field_widget.py
    odmr_window.py, odmr_worker.py, panels/odmr_panel.py
    magnet_window.py
    pulse_sequence_window.py, widgets/pulse_sequence_viewer.py
    image_inspection.py           generic pixel/line mathematics
  experiments/                    zero_field_experiment.py, odmr_experiment.py,
                                  pulsed_experiment.py (stub)
  sequencing/                     pulse_sequence.py, timing_rules.py,
                                  test_sequence.py (demo)
  controller/experiment_runner.py legacy synchronous runner
  analysis/                       odmr_fit.py, zero_field_averaging.py,
                                  average_zero_field_scans.py,
                                  average_zero_field_planes.py,
                                  package_zero_field_average.py,
                                  run_zero_field_15_scan_average.cmd
  utils/camera_diagnostics.py      lazy JSON-line file logger
  data/data_manager.py             active ODMR exporter; local-only/ignored
  data/__init__.py                 local-only package initializer
  tests/                          allowlisted safe tests, gated lab scripts,
                                  fixtures/odmr_timing_*.csv
  docs/                           July audits; September exposure note
  README.md, CLAUDE.md             narrative guidance, with stale details
  pytest.ini, requirements.txt, .gitignore
  field_ratio.py, focus_vs_field.py (untracked local analysis scripts)
  PROJECT_STATUS_REPORT.md        this generated baseline
  test_cube.npz, data/, *.csv      sample/generated measurement artifacts
```

The review covered active modules, legacy modules, standalone analysis, sequencing, tests and safety gate, docs, configuration, and history. Empty package initializers are packaging rather than additional subsystems. `.venv`, caches, and instrument-result arrays were not treated as software implementation. Source searches found no additional implemented experiment or hardware family beyond those described below.

## 4. Architecture

```text
main.py -> gui.main_window.main (--sim parsed with parse_known_args)
  -> QApplication -> MainWindow
       |-> ConfigManager("config/setupInfo.json")
       |-> HardwareManager(force_sim=...) -> shared hardware dict
       |      |-> microwave: SG386 / SimMicrowave
       |      |-> camera: AndorNeoAndor3 / SimCamera
       |      |-> Pulse Streamer: real / sim
       |      `-> X/Y/Z supplies -> MagnetAxis -> Magnet
       |
       |-> frozen AcquisitionState; manual/graphical ROI controls
       |-> live-view QTimer + camera's streaming thread -> main ImageView
       |        ^ registered timer controller and state-restoration callback
       |
       |-> ZeroFieldWindow -> ZeroFieldWidget (controls/rendering)
       |       `-> QObject ZeroFieldWorker moved to QThread
       |             -> ZeroFieldExperiment (ScanExperiment subclass)
       |                 -> exclusive_camera_access
       |                 -> Magnet + held-open software-trigger camera
       |                 -> ImageCube + optional HDF5 stream writer
       |             -> fluorescence signals -> window/display copies
       |             -> final/average persistence and timing CSV
       |
       |-> ODMRWindow -> ODMRPanel; worker on QThread
       |       -> ODMRExperiment.acquire_configured_point (GUI path)
       |       -> exclusive camera lease + external trigger + pulse/MW
       |       -> scalar arrays -> fit/plots; DataManager NPY/CSV/JSON export
       |
       `-> MagnetControlWindow -> same Magnet directly (no experiment lease)

Offline: ImageCube.load / plane readers -> framework.analysis.zero_field
         or analysis.zero_field_averaging / analysis.odmr_fit -> derived output
```

| Responsibility | Actual owner/boundary |
|---|---|
| Device instances/connections | `HardwareManager`; drivers own SDK/serial/VISA objects; references are shared with windows/workers |
| User acquisition settings | Main Window's frozen `AcquisitionState`; hardware drivers additionally cache applied/SDK-read-back values |
| Camera arbitration | Process-wide per-camera state in `framework/camera_ownership.py`; weak-keyed locks, registered GUI timer controller/restorer |
| Experiment state | `BaseExperiment` flags and state plus window running flags and worker stop flags; these are separate state representations |
| Raw image dataset | Zero Field experiment's mutable `ImageCube`; returned to window; driver owns temporary capture buffers |
| Visualization state | Main Window live image/ROI; Zero Field window copied image/reference/display caches; widget levels/map/line overlay |
| Analysis | Worker live fluorescence; `framework.analysis.zero_field` completed-cube fluorescence; `analysis/` offline summaries and ODMR fitting |
| Disk output | `ImageCube` serialization/stream writer; Zero Field worker orchestrates streaming/atomic averages; windows handle user saves; ODMR window exports scalar results |

The generic `ScanExperiment` implements setup -> set point -> settle -> acquire -> append -> process -> callbacks -> cleanup (`framework/scan_experiment.py:59`). Zero Field overrides the run/sweep lifecycle to add multi-scan averaging and persistence. ODMR's GUI worker drives point acquisition itself. Treating every experiment as one identical generic-run path would be inaccurate. The `BaseCamera` ABC and `PulsedExperiment` express architectural intent, but active cameras do not subclass the former and no concrete experiment subclasses the latter.

Logging is distributed: Python module loggers in experiments/workers/drivers, direct console prints in startup/hardware, GUI status/message boxes, and lazy JSON-line camera diagnostics at repository `data/camera_diagnostics.log`. No application-wide rotating log setup is installed by `main()`. `utils/camera_diagnostics.py:40` suppresses diagnostic failures; camera snap logging can perform stack inspection and file I/O. ODMR/Zero Field timing logs are separately opt-in and written after acquisition. They are not a central telemetry service.

## 5. Hardware Support

The table describes actual code paths. Real-device readiness means an implemented driver and application integration, with historical rig use documented where stated; it does not mean hardware was tested in this review.

| Device/model | Python class; interface | Connection / configuration | Implemented controls and maturity | Simulator / limitations |
|---|---|---|---|---|
| Andor Neo 5.5 sCMOS | `AndorNeoAndor3`; duck-typed camera API | `andor3.Andor3().open(0)`; `spcm.type="Andor"` | Active SDK3 driver: exposure/read-back/limits, hardware AOI, binning, software snap, live stream, held-open software/external acquisition, buffers/timing/close | `SimCamera`; SDK camera index/settings hard-coded; model not verified from configuration |
| Legacy Andor alternative | `AndorNeo`; independent pylablib implementation | `AndorSDK3Camera(0)`; not selected by manager | Legacy snap/external-frame path | ROI/binning setters only cache attributes; no current held-open parity; not the active driver |
| SRS SG386 microwave | `SG386`; no shared ABC | PyVISA `TCPIP0::<address>::inst0::INSTR`; first `frequencyGenerators` entry | Frequency/power set/query, RF on/off, close; used by ODMR | `SimMicrowave`; configured port is unused by VISA resource, configured limits not enforced |
| Swabian Pulse Streamer 8/2 | `SwabianPulseStreamer`; duck-typed | Vendor SDK/IP; `pulseGenerator` | Digital sequence validation/compile/load/run; finite-shot count; persistent outputs; reset/sync/close | `SimPulseStreamer`; no implemented analog-output control despite analog config |
| X field supply, Keysight/HP E3632A | `E3632A` -> `BaseSCPIPowerSupply` -> `BasePowerSupply`; `SerialDevice` transport | `Helmholtz.X`, COM13, configured P15V | SCPI current/voltage, output, ID/reset/error queue, range API | `SimPowerSupply`; configured range not applied automatically; getters cache commands |
| Y field supply, Keysight/HP E3631A | `E3631A` -> same bases | `Helmholtz.Y`, COM12, P6V | Channel selection, current/voltage, output, ID/reset/error queue | Same simulator; no independent measured-current/field feedback |
| Z field supply, Keysight/HP E3632A | `E3632A` -> same bases | `Helmholtz.Z`, COM11, configured P15V | Same as X | Same limitations as X |
| Three-axis Helmholtz magnet | `Magnet` + three `MagnetAxis` | Shared supplies and Pulse Streamer polarity relay; `Helmholtz` | Calibrated request conversion, vector/axis cached state, enable/disable/zero, common polarity, current-write caching/pacing/error diagnostics | Same abstraction over sim supplies; cannot generate arbitrary mixed-sign vectors |
| Green laser / microwave RF switch / detector TTL | Digital sequence channels, no independent instrument classes | Active map: greenLaser 1, MW 2, detector 5, detector2 6, flipMF 3 | Digital gating and camera trigger patterns | No laser power or analog AOM driver; presence of a TTL name is not a complete detector/laser subsystem |
| SmarAct MCS2 stages | Absent | `stages` config stub only | Planned/unimplemented | None |
| Event camera | Absent | Orphaned top-level EventCamera config fields | Planned/unimplemented; active `spcm` still selects Andor | None |
| Keithley devices | Absent | No supported selector/driver found | Unsupported | None |
| NI DAQ / photon counter / C4 lock-in camera | Absent | Historical `niDaq`/`spcm` names or documentation only | No respective acquisition path; `spcm` actually selects ordinary camera | None |

Evidence: `hardware/hardware_manager.py:77`, `:97`, `:144`, `:174`, `:225`; `hardware/camera/andor_neo_andor3.py:424`; `hardware/camera/andor_neo.py:48`; `hardware/microwave/sg386.py:6`; `hardware/power_supply/e3631a.py:31`; `hardware/power_supply/e3632a.py:64`; `config/setupInfo.json:12`.

`BaseCamera` is not inherited by either active camera. Compatibility is by method convention and `hasattr` fallbacks. Power supplies do use their base classes. `SerialDevice` defaults to 9600 baud, 1 s timeout, newline-terminated ASCII, without transaction locking, flow control, write timeout, or command-completion verification (`hardware/interfaces/serial_device.py:24`). The error queue reader is available but not equivalent to per-command acceptance checking.

The active camera configures cooling, global shutter, 280 MHz readout, overlap off, spurious-noise filtering, gain enum 2, Mono16, and continuous acquisition in code (`andor_neo_andor3.py:439`). Frame decoding respects stride and produces copied uint16 images. Held-open acquisition uses copy-before-requeue behavior. Exposure is read back from SDK after setting; field/current getters elsewhere are cached values. SDK stop/flush failures are suppressed during held-open teardown, a lifecycle limitation rather than proof of successful disarming (`:232`).

## 6. Simulation Layer

`python main.py --sim` sets `HardwareManager(force_sim=True)` and replaces camera, microwave, pulse streamer and all supplies in memory. `Magnet` remains the normal abstraction over simulated supplies/relay. No JSON is rewritten. Per-device `type="sim"` also permits mixed real/simulated operation; all other selected devices still connect normally. `debugMode` and `dummy` flags do not select simulation. Unknown types raise rather than silently falling back. See `gui/main_window.py:853`, `hardware/hardware_manager.py:97`.

| Component | Actual simulation behavior |
|---|---|
| `hardware/camera/sim_camera.py::SimCamera` | Default 64x64 sensor, exposure 0.02 s, binning 1, ROI None; SDK-like held-open software/external modes, acquisition counters/timing APIs, stream/snap/latest-frame methods |
| `hardware/microwave/sim_microwave.py::SimMicrowave` | Cached frequency, power and RF state, matching control methods |
| `hardware/pulse_streamer/sim_pulse_streamer.py::SimPulseStreamer` | Loaded sequence and persistent-output state, prints simulated events; accepts real-facing method arguments |
| `hardware/power_supply/sim_power_supply.py::SimPowerSupply` | Cached connection/output/current/voltage; error queue always empty; accepts signed values |
| `hardware/sim_hardware.py` | Legacy separate factory/classes; not manager's active path; omits current magnet/channel assembly and microwave-camera wiring |

Camera ROI first crops `(x0,y0,x1,y1)` using exclusive upper bounds. Binning drops incomplete edge blocks and takes block **means**, not charge sums. Exposure introduces waits but does not scale brightness or noise. Held-open acquisition avoids simulated per-frame arming; timeouts and genuine external-trigger synchronization are not modeled. Live latest-frame reads return a defensive copy. Simulator setters do not reproduce all SDK range/AOI constraints. Source: `sim_camera.py:114`, `:287`, `:390`, `:493`.

Synthetic fluorescence is spatially uniform base intensity 1.0 plus independent normal noise with sigma 0.01. When microwave gating is asserted, the camera applies `contrast/(1+((f-f0)/gamma)^2)`, with `f0=2.87 GHz`, `gamma=5 MHz`, contrast 0.03. Manager wires the simulated camera to microwave and, when simulated, pulse streamer. Gating checks whether the currently loaded sequence contains an asserted MW pulse, not the instantaneous waveform. Without that pulse reference it uses microwave power > -50 dBm; RF-enabled state is not part of the test. Thus a synthetic ODMR dip is implemented, but timing and instrument fidelity are limited (`sim_camera.py:311`).

There is **no synthetic magnetic-field-dependent Zero Field response**: the camera has no magnet input. A Zero Field run exercises commands, images, averaging, GUI, and persistence, not zero-field physics. Noise uses global unseeded NumPy RNG; deterministic tests seed/mock separately. No simulator-seed or synthetic-field model configuration is provided.

SimPulseStreamer sleeps 10 ms per compiled event, ignores actual nanosecond spacing and accepted `n_runs/final` arguments, and its compiler does not fully reproduce pulse state/persistent-output merging (`sim_pulse_streamer.py:28`). It cannot validate real pulse timing, physical finite-shot execution, coil settling, transport overruns, or readout stalls.

A clean simulation installation still has an import dependency issue: HardwareManager eagerly imports real drivers and their `andor3`, `pulsestreamer`, `serial`, and PyVISA dependencies before choosing simulators. `requirements.txt` omits the first three. README's statement that those packages are unnecessary for simulation is therefore not true of current startup imports (`hardware/hardware_manager.py:1`; `README.md`, Installation). This review used the existing environment, not a fresh installation.

## 7. Acquisition Architecture

`AcquisitionState` is a frozen dataclass with `acquisition_roi`, `exposure_s`, `binning`, and `baseline_counts`; `apply_to` calls exposure -> binning -> ROI (`framework/acquisition_state.py:6`). It separates full-sensor acquisition ROI from optional image-local analysis ROI. The dataclass itself has no range validation. Main Window builds its initial state from controls: 10 ms exposure, binning 1, baseline 0, full sensor; the standalone dataclass's 20 ms default is not the GUI startup setting (`gui/main_window.py:135`, `:205`, `:399`).

Exposure is displayed in milliseconds and converted to seconds. Bounds come from `camera.get_exposure_limits()`; failure falls back to 1 ms through 10 s. Real setter caches SDK read-back exposure. Main Window's requested state and actual driver exposure can differ through SDK quantization. Binning UI allows integers 1-8, while active Andor supports only 1,2,3,4,8; 5-7 can be selected but rejected. Baseline is software-only ODMR input, not camera correction or Zero Field subtraction.

ROI has manual X/Y/Width/Height editors and a graphical `pg.RectROI`. The shared setter validates nonempty integer coordinates and sensor bounds, applies candidate acquisition state, then commits state or restores after camera failure. `None` resets to full sensor. Manual edits and graphical selection are synchronized with signal blockers (`gui/main_window.py:542`, `:578`, `:674`). Graphical ROI coordinates are taken from the view, while cropped/binned images are rendered without a full-sensor transform; alignment after cropping/binning remains a concrete issue.

`framework/roi.py:8` rejects malformed length, nonnumeric/bool/nonfinite/fractional coordinates, reversed or empty extents. Software `clip_roi` permits clipping but rejects no overlap and invalid image dimensions. Main camera ROI additionally rejects sensor-bound violations. These are different purposes, not identical validation duplicates. Camera SDK conversion changes zero-based API origins to one-based AOI origins; active Andor ROI is implemented despite a stale placeholder heading (`andor_neo_andor3.py:532`).

Camera access is cooperative and process-wide per object. `exclusive_camera_access` serializes experiments, marks ownership, suspends the registered GUI polling timer, stops an active stream, runs acquisition, restores Main Window settings, restarts the old stream, and resumes its timer (`framework/camera_ownership.py:54`). Main live view polls every 30 ms; driver streaming uses `StreamController`'s thread lifecycle/acquisition gates (`gui/main_window.py:399`; `hardware/camera/streaming.py:16`). Experiment data is not taken from the live-view cached frame.

Zero Field holds the lease across every requested sweep and arms software acquisition once for that whole run. ODMR's GUI worker holds a lease and arms external acquisition once for its scan. Cameras lacking the held-open API use the per-frame fallback. Experiment cleanup restores camera user settings and prior streaming, **not** the pre-scan magnet field: Zero Field disables the magnet after each sweep. A backend opt-out of non-swept-axis zeroing captures that sweep's entry vector; cleanup between repeated scans means a retained bias is not automatically reapplied to later sweeps.

The implementation prevents ordinary concurrent live/experiment acquisition, but does not guarantee contention-free GUI operation. `apply_live_acquisition_state` blocks acquiring the experiment lock before checking ownership (`camera_ownership.py:120`), while lease teardown can wait for GUI-thread timer resumption (`gui/live_view_controller.py:43`). A GUI settings action during a scan can deadlock this pair. Live Stop bypasses ownership, manual magnet controls remain active, and shutdown does not join workers. Section 22 records these risks with consequences.

Averaging and axes are experiment-specific: Zero Field averages camera frames at each linearly spaced field point, then optionally averages complete scans on the same field grid. ODMR averages OFF/ON-derived ratios across repeats and worker-level averages on a sorted frequency grid acquired in random order. A generic `ScanExperiment` provides neither those averaging policies nor robust stopped-axis truncation automatically.

## 8. Data Model / ImageCube

`ImageCube(data, axes, metadata, experiment_type, scan_axis_name, scan_axis_unit, scan_axis_values)` stores these objects directly (`framework/image_cube.py:48`). `axes` is a generic dictionary; the scan-axis attributes are separate compatibility fields. There is no required named spatial-axis schema.

| Context | Actual data representation |
|---|---|
| Zero Field | `(acquired_field_points, image_y, image_x)`; one per-point averaged raw image per plane; active uint16 camera input becomes float64 through NumPy mean |
| Multi-scan Zero Field | Same shape, float64 running mean of completed sweeps; no repeat or scan dimension retained in the final average |
| Programmatic generic ODMR run | `(frequency, repeats, off_on=2, image_y, image_x)` |
| ODMR GUI | Scalar frequency, normalized-signal, raw OFF/ON-mean arrays; does not fill/save that ImageCube |
| Live view | Latest camera image/cache; no experiment cube |

Zero Field currently calls `self.image_cube.add(averaged_image)` at `experiments/zero_field_experiment.py:641`. `ImageCube.add` takes a view for the first frame and concatenates subsequent frames (`image_cube.py:75`); it is not a preallocated in-memory acquisition buffer. Streaming saves incrementally **in addition to** retaining the growing in-memory cube. Per-point repeat images are discarded after their mean. Multi-scan running averaging reduces retention across sweeps, not the memory needed for one full cube.

Serialization:

- NPZ stores data plus axes/metadata as object arrays and all experiment/scan fields; load uses `allow_pickle=True` and supports missing legacy scan-axis fields. `save` uses NPZ for any suffix other than `.h5/.hdf5` (`image_cube.py:138`), so arbitrary extensions do not select TIFF/CSV.
- HDF5 stores `data`, `scan_axis_values`, `metadata_json`, `axes_json`; string attributes and None flags describe experiment and axis names/units. Pixel dtype is preserved; field values are serialized float64; metadata must be JSON serializable. Data uses chunks, gzip level 4, shuffle (`:160`).
- `peek_shape` returns shape without loading full pixels for HDF5/NPZ. `load_plane` and `open_streaming_write` are HDF5-only; NPZ attempts raise `NotImplementedError` (`:301`, `:341`, `:364`).
- HDF5 stream writer initializes metadata immediately and allocates the full intended stack when the first frame arrives. Each plane and its metadata are flushed. Orderly incomplete close shrinks pixel/axis datasets; a hard crash can leave unwritten fill planes in the declared shape. `ImageCube.load` does not itself reject/trim all such incomplete files; consumers must honor completion metadata.

Current validation, exhaustively separated by layer:

| Layer | Checks / failure behavior |
|---|---|
| `ImageCube.__init__` | No data-shape, dtype, finite-value, axis-length, ROI, or metadata schema validation |
| `ImageCube.add` | Incoming ndarray conversion; reject differing trailing frame shape with `ValueError("All ImageCube frames must have the same shape.")`; does not explicitly reject dtype changes (NumPy may promote) |
| `ImageCube.show` | Only 2D or 3D data; other dimensions raise ValueError |
| `ImageCube.save` | Reject `data is None`; backend serialization requirements apply; no general scientific validation |
| `peek_shape` | NPZ header reader rejects unsupported NPY versions |
| Streaming constructor | `num_planes >= 1`; optional dtype converted by NumPy |
| `write_plane` | Writer open; index in range; subsequent shapes equal; dtype converted to chosen/first-plane dtype |
| Streaming metadata/finalize | Reject calls after close; finalize requires `planes_written == num_planes`; no bitmap validation for holes or duplicate writes |
| Stream close | Idempotent; incomplete clean close truncates data/axis to highest written index+1; assumes sequential writing, which Zero Field satisfies |
| Zero Field dataset | Its additional exact structural/metadata checks are listed in section 13 |
| Runtime fluorescence analysis | Data present, ndim at least 3, one-dimensional field vector with one value per frame (`framework/analysis/zero_field.py:8`) |
| Offline scan averaging | Nonempty sources, compatible shapes/dtypes, supported plane reader, float64 for `average_scans`, incomplete HDF5 rejection by default; does not compare full scan axes/acquisition metadata (`analysis/zero_field_averaging.py:81`, `:192`, `:331`) |

Reviewed Zero Field display operations copy images/reference and subtract into a new float array; fluorescence analysis reads images. They do not overwrite measured planes. Nevertheless ImageCube is **mutable**, its constructor and first add can alias caller data, and callbacks can receive mutable cubes. No global read-only buffer guarantee exists. ROI extraction also returns views. Store “raw” here means unnormalized averaged camera counts; it does not mean every original camera exposure has been retained.

## 9. Visualization Architecture

There is no repository-defined `ImageViewer` or `image_viewer.py`. Main Window and ZeroFieldWidget independently instantiate `pg.ImageView`; advanced image behavior is tied to ZeroFieldWindow/Widget. Generic reusable pieces are `gui/image_inspection.py::pixel_value/line_profile`, `framework.fluorescence`, `framework.roi`, and the third-party image widget. A separate `PulseSequenceViewer` really is reusable for digital pulse visualization; it is not an image viewer.

| Capability | Current implementation |
|---|---|
| Live camera image | Main Window polls driver cache, transposes for display; autoLevels=False; acquisition ROI overlay |
| Experiment image | Zero Field receives one per-point averaged image through worker signals |
| Raw/difference | Window uses latest copied raw mean or `float64(latest)-float64(first_reference)` |
| Auto scaling | Finite min/max; all-nonfinite fallback `(0,1)`; constant image padded by max(1,1% of absolute value) |
| Manual scaling | Explicit minimum/maximum, rejects min >= max; no pixel modification |
| Colormaps | Gray, Viridis, Plasma, Inferno, Magma via widget rendering |
| Pixel inspector | Current displayed image's pixel, including signed difference values; outside image returns None |
| Line profile | Interactive LineSegmentROI; nearest-neighbor rounded sample coordinates, clipped at image edges; pixel-distance axis |
| Area ROI | Acquisition overlay in Main Window; no separate Zero Field area-analysis ROI control |
| Cached refresh | `_latest_averaged_image`, `_reference_image`, `_current_display_image`, widget `_displayed_image`; settings redraw cached image without reacquisition |
| Reusable APIs | `pixel_value`, `line_profile`, `update_image`, display-parameter helpers; no unified independent image-viewing component |

References: `gui/zero_field_window.py:216`, `:256`, `:297`; `gui/widgets/zero_field_widget.py:125`, `:197`, `:283`; `gui/image_inspection.py:6`.

Difference reference is a **copy of the first averaged image of the run**, reset at Start only, and retained across multi-scan repeats. It is not a near-zero reference unless the first field happens to be zero. Offline report images use a different reference: the sampled field nearest numeric zero. Neither display operation changes the raw cube. At multi-scan completion the curve is recomputed from the averaged cube, but the displayed image remains the latest individual scan's image rather than being replaced by a final average plane.

## 10. Experiment Status

| Experiment/tool | Class / worker / GUI | Acquisition and analysis | Visualization / saving | Coverage and real readiness |
|---|---|---|---|---|
| Live Camera | Camera driver/StreamController; MainWindow and LiveViewTimerController | Driver live loop; acquisition state; no experiment class or scan analysis | Main ImageView + ROI; no dedicated live dataset export | State/ownership/ROI/stream tests; active real camera path |
| Zero Field | `ZeroFieldExperiment`; `ZeroFieldWorker(QObject)` on QThread; `ZeroFieldWindow` + `ZeroFieldWidget` | Single-axis field sweep, per-point frames, optional multi-scan mean; raw fluorescence | Live image/difference/profile/curve; HDF5 streaming/average, NPZ API fallback; timing CSV | Extensive safe tests; real driver chain implemented and rig use documented, qualified by section 22 |
| ODMR / ESR-labelled action | `ODMRExperiment`; `ODMRWorker` on QThread; `ODMRWindow` + `ODMRPanel` | MW frequency sweep, OFF/ON image pairs, baseline-corrected ratio; Lorentzian fits | Scalar plot/fit and pulse preview; DataManager scalar NPY/CSV/JSON; no GUI raw-image cube save | Safe gating/trigger/baseline/ROI/timing tests; documented real workflow; point plot refresh and cleanup gaps |
| Manual magnet control | `MagnetControlWindow`, `Magnet`, `MagnetAxis`; no experiment worker | Manual enable/disable/zero/vector/polarity, cached readout | Controls/status, not a scan dataset | Real supplies implemented; no experiment exclusion lock |
| Pulse sequence preview | `PulseSequenceWindow`, `PulseSequenceViewer` | Draw existing digital pulses, no separate experiment | Reusable visual inspector | Preview support; does not implement new pulse experiment families |
| Pulsed base | `PulsedExperiment` | `build_sequence()` raises NotImplementedError; no concrete subclasses | None | Stub |
| Rabi, Ramsy/Ramsey, T1, T2 | Disabled Main Window buttons only | None | Placeholder labels only | Absent experiment/worker/tests |
| Magnetic-field reconstruction / other scans | No additional experiment class found | Fluorescence images and manual fields do not constitute calibrated field-map inversion | Offline descriptive images only | Absent as a dedicated experiment/reconstruction workflow |

Source inventory: `experiments/`, `gui/main_window.py:328`, `experiments/pulsed_experiment.py:4`. ESR is an ODMR UI name here, not evidence of a second independent ESR implementation.

## 11. Zero Field Experiment - Current Implementation

### GUI and acquisition inputs

`ZeroFieldWindow.start_scan()` collects widget settings, checks output-directory writability when saving is enabled, takes a Main Window acquisition snapshot, resets result/reference state, constructs output names and metadata, then creates the worker/QThread (`gui/zero_field_window.py:122`). The worker constructs the experiment directly; it does not use ExperimentRunner or reimplement camera hardware. The acquisition ROI is frozen at construction. Exposure/binning come from the already-applied Main Window state, while the window records requested values and the experiment records cached driver values.

| GUI control | Default / range / actual role |
|---|---|
| Sweep axis | X; choices X/Y/Z |
| Start/stop field | -2 / +2 G; each -10000 to +10000 G, four decimals |
| Number of points | 5; 1-100000; inclusive linear sweep |
| Settling time | 100 ms; 0-600000 ms |
| Number of averages | 1; 1-100000 camera frames per point |
| Enable averaging | Off; enables repeated full sweeps |
| Number of scans | 1; 1-100000 |
| Save individual scans | Off; enabled with multi-scan averaging and Save data |
| Output directory | Configured/default path, read-only field and Browse |
| Save data | On; disabling gives a one-time memory-loss warning |
| Start/Stop Scan | Starts captured config or requests cooperative stop |
| Save Data | Enabled when a cube with data is available; copy saved file or serialize memory |
| Point/scan progress | Point count/percentage; current scan; current field and fluorescence |
| Camera ROI/exposure/binning | Informational label only; edited in Main Window |
| Fixed X/Y/Z fields | **No controls**; ordinary GUI uses backend zero-other-axes default |
| Image display | Raw Image / Difference Image |
| Scaling | Auto / Manual; manual bounds -1e12 to +1e12, defaults 0/1 |
| Colormap | Gray default; Viridis, Plasma, Inferno, Magma |
| Inspection | Pixel readout and Enable Line Profile toggle/line overlay |

Source: `gui/widgets/zero_field_widget.py:36`, `:242`. Running-state styling changes the Start/Stop button and status, but does not freeze every parameter control; the worker continues with its captured dictionary rather than later edits.

### Actual sequence

```text
MainWindow AcquisitionState snapshot
  -> ZeroFieldWindow config + metadata + optional output path
  -> ZeroFieldWorker on QThread
  -> ZeroFieldExperiment.run
       acquire exclusive camera lease; stop live view
       apply frozen acquisition ROI
       optional camera/magnet timing enable
       reset camera acquisition counters
       begin_software_acquisition once, when supported
       for each requested sweep:
         callback scan_started (worker clears live scalar lists)
         magnet.enable; optional boundary error-queue check
         record cached entry vector
         zero other axes by default / resolve retained vector for opt-out
         build np.linspace(start, stop, points); establish metadata
         open HDF5 stream writer if selected
         for each field point, unless stop already requested:
           convert G -> mT; magnet.set_vector
           wait configured settling delay
           acquire N software-trigger frames (or N snap fallbacks)
           np.mean(np.stack(frames), axis=0)
           append ONE image to in-memory ImageCube
           write/flush ONE HDF5 plane if saving
           process_frame records latest_image (returns None)
           worker live callback computes raw mean fluorescence
           emit image -> cumulative fluorescence -> progress signals
         close/finalize stream and disable magnet
         finalize/validate acquired cube
         fold COMPLETE sweep into running average
         retain requested raw-scan file or remove temporary per-scan file
         persist/report running average through completion callback
       finalize/validate result
       end software acquisition; disable timing flags
       restore MainWindow camera settings/live stream/timer via lease exit
  -> worker final save/metadata merge -> finished signal -> window result
  -> timing CSV written in worker finally, if enabled
```

The field command, settling, frames, averaging, storage and worker signal order are confirmed in `experiments/zero_field_experiment.py:617` and `gui/zero_field_worker.py:237`. Actual GUI rendering is queued on the GUI thread; emission order is not a synchronous guarantee that the screen painted before the next point. The backend's `emit_progress()` is the inherited no-op; the worker emits the real progress signal inside the live callback.

Zero Field does not itself turn on illumination, configure laser power, or force the microwave RF source off: its injected hardware use is camera plus magnet. The microwave-free acquisition sequence therefore relies on the surrounding rig/output state; it does not verify that physical MW is absent. In particular, a preceding ODMR scan's source RF enable is not explicitly undone by the ODMR cleanup path. This is a state-management limitation, not a claim about illumination or MW at the sample during any recorded measurement.

For `P` completed points, `A` frames per point, and `S` completed sweeps, the normal camera frame count is `P*A*S`. On active Andor/SimCamera paths these are `grab_software_frame(timeout_ms=10000)` calls, with one begin/end pair over the whole run, **not P*A*S standalone snaps**. Older/fake cameras without the begin API use `snap()` A times per point. There is exactly one stored image per completed field point in each sweep; repeats within a point are not persisted separately (`zero_field_experiment.py:360`). The ordinary mean does not reject NaN, saturation, or varying exposure and does not provide uncertainty estimates.

### Live fluorescence and repeated scans

`ZeroFieldWorker._compute_live_fluorescence()` calls `framework.fluorescence.mean_fluorescence()` on the whole received per-point averaged image (`zero_field_worker.py:251`). This is raw mean camera counts/pixel after camera ROI/binning. There is no analysis-area recrop, display-difference subtraction, camera-baseline correction, or normalization. The worker appends a new scalar to `_signals` and field to `_fields`; previous points are not recalculated per point. It emits `live_update_signal(float,float,object)`, `fluorescence_update_signal(object,object)`, then `progress_signal(int,int)`.

Per-scan scalar lists reset at scan start. On scan completion and final completion, the window recomputes `mean_fluorescence_vs_field(image_cube)` across the returned running-average cube to show the accumulated result. Thus earlier points **are** recomputed at those boundaries, not during each live point. Scalars are recoverable from the cube but are not separately stored as a dedicated Zero Field curve dataset (`zero_field_window.py:313`, `:325`). Backend `scan_signal` contains None values, not fluorescence measurements; it also serves as a point-completion count.

Across complete sweeps, the running update is `average += (current-average)/completed_scans`. The first multi-scan accumulator is a float64 deep copy of data/axes/metadata. Prior raw sweep cubes are released once folded in; saved individual sweeps are distinct from discarded per-point repeat frames (`zero_field_experiment.py:489`, `:711`).

### Stop, errors, cleanup, and saving

Stop sets worker and experiment flags without hardware calls; the point loop checks at the next boundary. An in-flight settling sleep or all A acquisitions at a point finish before stopping. Partial first-sweep data can be returned; a partial later sweep is excluded from an already-completed running average. The scan axis and in-memory `field_values_gauss` are truncated to acquired planes. Completed scans, experiment completion and stop intent are separate metadata values (`zero_field_experiment.py:474`, `:670`, `:727`).

Ordinary exceptions propagate through experiment cleanup to worker error text and `finished_signal(None,...,True)`. The GUI does not retain an in-memory partial cube on that error path; a streamed file may provide recovery. Base cleanup overwrites ERROR with FINISHED, so flags alone are not a reliable failure record. A specific stream-metadata/close failure can occur before `cleanup_scan()` is entered and skip magnet disable; timing enable/camera arm also precede the inner acquisition-cleanup try. These are limitations of error paths, not claims that all cleanup is absent.

Output names are `zero_field_YYYY-MM-DD_HH-MM-SS.h5` for one sweep and `zero_field_YYYY-MM-DD_HH-MM-SS_average.h5` for multiple; individual files use `_scan_001.h5`, etc. Directory comes from `dataOutput.zeroFieldDirectory`, resolved relative to repository root, or repository `data`. Single-scan HDF5 goes directly to final file and later merges final metadata. Multi-scan output is saved through `<stem>.partial.h5` then `Path.replace`, on each completed sweep and final return. Temporary per-scan files are removed after incorporation if individual scans were not requested. Non-HDF5 destinations use final conventional save (`zero_field_worker.py:24`, `:76`, `:260`).

With Save data off, there is no cube/raw-scan output during acquisition; later Save Data serializes the in-memory result. When saving was on, Save Data copies only the final cube, not companion scans or timing CSV. Opt-in timing CSV is independent of the Save data toggle. Output timestamps have one-second resolution and no collision guard. Acquisition still keeps full image cubes in memory even with streaming enabled.

Visualization specifics and reference lifetime are in section 9. Raw Image means averaged acquired counts; Difference Image is display-only first-run-frame subtraction. At completion the last image and final averaged curve can represent different aggregation levels.

## 12. Zero Field Magnetic-Field Calibration Path

```text
config/setupInfo.json -> ConfigManager.load -> HardwareManager.initialize
  -> Helmholtz config -> Magnet -> MagnetAxis(X/Y/Z)
  -> requested Zero Field value in G
  -> set_scan_point: field_mT = value_G * 0.1
  -> Magnet.set_vector(bx,by,bz): signed target per axis
  -> MagnetAxis selects that axis's relay branch and directional fit
  -> positive: I_A = (B_target - B0_positive) / k_positive
  -> negative: I_A = (B0_negative - B_target) / k_negative
  -> E3631A/E3632A.set_current(I_A) -> non-negative serial SCPI command
```

Current directional calibration in `config/setupInfo.json`:

| Axis | Positive ratio / offset (mT/A, mT) | Negative ratio / offset (mT/A, mT) | Relay | Supply selection |
|---|---|---|---|---|
| X | 0.887629 / 0.012076 | 0.886743 / 0.012819 | ch 6, `flipMF_X` | `type=E3632A`, `address=COM13`, `range=P15V`, `max_current=5` A |
| Y | 0.741657 / 0.006619 | 0.740600 / 0.006233 | ch 7, `flipMF_Y` | `type=E3631A`, `address=COM12`, `channel=P6V`, `max_current=5` A |
| Z | 0.799629 / 0.117176 | 0.800057 / 0.117748 | ch 3, `flipMF_Z` | `type=E3632A`, `address=COM11`, `range=P15V`, `max_current=5` A |

The field equations are `B=B0_positive+k_positive*I` and `B=B0_negative-k_negative*I`. Current remains non-negative. When both branches can reach a target, the existing relay branch is retained. Y has a narrow interval between its zero-current intercepts that neither fit reaches: current remains zero on the current branch and the cached model field reports that branch's intercept. Calibration values are not averaged.

Zero field selects the negative branch on every axis and commands positive compensation magnitudes computed from `B0_negative/k_negative`: X 0.0144562743 A, Y 0.0084161491 A, Z 0.1471745138 A. `Magnet.zero()` retains electrical-zero semantics and commands 0 A; its model fields are the selected branch offsets. `get_vector()` returns commanded/model fields, not magnetic measurements.

GUI and Zero Field values remain in G; only `set_scan_point` converts them once using `value_G * 0.1`. E3631A's current setter sends channel-specific `APPL P6V, MAX, <current>`; E3632A sends its current command through the SCPI base/serial transport. Configured E3632A range is displayed/read as configuration but is not automatically applied by initialization. Supply getters report cached setters, not independent current measurements.

The axis skips unchanged currents within 1e-9 A only after a prior setter returned without Python exception; output changes and that axis's real polarity changes invalidate its current cache. `Magnet.set_vector()` sleeps `Helmholtz.scpi_write_delay_s=0.05` after each actual write, not a skip. Optional `NV_ZFE_CHECK_PS_ERRORS=1` polls/logs supply errors at scan start/end, without automatic abort. Independent relay transitions retain the axis-local current-zero, output-off, relay, settling, current-restore sequence. The 50 ms discharge and settling values remain open-loop starting values for physical verification.

## 13. Zero Field Metadata

The following lists all explicitly generated keys on the current GUI/backend paths. Caller-supplied metadata can add arbitrary keys. Source locations: `gui/widgets/zero_field_widget.py:242`; `gui/zero_field_window.py:176`; `experiments/zero_field_experiment.py:727`, `:765`, `:800`; `framework/image_cube.py:454`.

| Group | Fields and meaning |
|---|---|
| Experiment identity/time | `experiment_name="Zero Field"`; `acquisition_started_at_utc` (timezone-aware ISO time initialized at first sweep setup) |
| User scan snapshot | `scan_parameters`: `field_start`, `field_stop`, `field_points`, `field_axis`, `settling_time_ms`, `averages`, `averaging_enabled`, `num_scans`, `save_raw_scans`, `save_data` |
| Requested camera snapshot | `camera_parameters`: `exposure_s`, `binning`, `acquisition_roi` from Main Window |
| Applied/cached acquisition | Top-level `acquisition_roi`, `camera_exposure_s`, `camera_binning`; `averages_per_point` |
| Sweep description | `field_axis`, `field_values_gauss`, `field_point_count`, `field_start_gauss`, `field_stop_gauss`, `settling_time_ms` |
| Non-swept-axis policy/state | `zero_other_axes`; `initial_magnet_vector_mT` from first sweep entry; `non_swept_axis_field_mT` for the two resolved fixed axes; `scan_entry_magnet_vectors_mT` accumulated per sweep |
| Hardware | `camera_model` = Python class name; `power_supply_models` = axis->class names when mapping exists; `magnet_configuration` = deep copy of available magnet config, including calibration/addresses/relay/delay |
| Multi-scan/completion | `averaging_enabled`, `num_scans`, `save_raw_scans`, `completed_scans`, `experiment_complete`, `stopped_by_user` |
| Raw-scan provenance | `raw_scan_filenames` when save_raw_scans and not direct single-scan final streaming; omitted where concept does not apply |
| Image descriptors | `image_height_px`, `image_width_px`, `image_dtype` after data dimensions are known |
| Streaming file bookkeeping | `planes_written`, `scan_complete`; `stopped_by_user` merged on orderly sweep close |

Outside `metadata`, ImageCube carries `scan_axis_name="Magnetic Field"`, `scan_axis_unit="G"`, `scan_axis_values`, `axes` (normally empty), and `experiment_type`. Zero Field does not set `experiment_type` in its cube constructors, so it remains **"Unknown"** despite the descriptive metadata field. Data itself has field/image dimensions and dtype.

Metadata is not identical in every artifact. Single-scan final HDF5 receives the post-run metadata merge. A multi-scan averaged file is serialized from the finalized returned cube. Individually streamed raw-scan files are opened with static sweep metadata and stream bookkeeping; the later image descriptors/averaging metadata are not universally merged into those already-closed files. Orderly partial stream close truncates the HDF5 axis, but its original static `field_values_gauss` metadata can remain the full planned vector in raw files; the in-memory cube is explicitly truncated. A hard crash can precede even those clean-close adjustments.

Recorded fields/vectors are requests/caches, not magnetic-field measurements. Exposure in top-level metadata comes from the driver's attribute, which the active driver updates through SDK read-back; this code does not query SDK independently at metadata-write time. No per-frame timestamps, measured currents/fields, per-repeat images, statistical uncertainties, fitted center, software commit, simulator seed, display reference/scaling/map, or Zero Field baseline value are automatically included. A timing file header is separate from cube metadata.

Entry-vector capture occurs **after** `magnet.enable()` (`zero_field_experiment.py:257`, `:269`), and enable synchronizes positive polarity (`magnet.py:307`). Thus `initial_magnet_vector_mT` is not an untouched physical pre-run snapshot; a prior negative cached vector can lose its sign before recording. The name and comments should not be read as a measured pre-enable state.

Zero Field validates every completed/returned acquired cube after image metadata finalization (`zero_field_experiment.py:814`):

1. If stop was requested and data is None, return without further dataset checks.
2. Otherwise require a NumPy ndarray with **exactly 3 dimensions**, else ValueError: `Zero Field ImageCube must contain a 3-D stack of acquired frames.`
3. Unless stop requested, frame count must equal configured field_points; mismatch raises ValueError with both counts.
4. Height and width must each be >=1, else `Zero Field ImageCube frames must have non-zero dimensions.`
5. `len(metadata['field_values_gauss'])` must equal stored frame count; missing defaults empty and fails for nonempty data.
6. `image_height_px` and `image_width_px` must match actual shape.
7. `image_dtype` must equal `str(data.dtype)`.

The dense stack supplies common shape/dtype; validation does not independently test original repeat-frame dtype, finite values, saturation, monotonic/unique fields, equality of separate scan_axis_values with metadata, ROI-size/binning consistency, current limits for the whole planned sweep, or physical field calibration. Runtime fluorescence separately checks one scan-axis value per image. The stream writer's structural guards are in section 8. These checks run after a sweep, while incremental disk writes have already occurred; saving is not preceded by one comprehensive whole-dataset validation pass.

## 14. Zero Field Timing Diagnostics

Enable with experiment constructor/config `timing_diagnostics=True` or `NV_ZFE_TIMING=1`; no GUI checkbox exists. This is separate from `NV_ODMR_TIMING` and from `NV_ZFE_CHECK_PS_ERRORS`. Timing uses `time.perf_counter`, accumulates records in memory, and drains camera/magnet sublogs into experiment records (`zero_field_experiment.py:182`).

CSV columns, exactly:

```csv
scan_index,point_index,field_gauss,avg_index,stage,t_start_rel_s,duration_s
```

`scan_index` is 1-based for sweeps; point and per-point averaging indexes are zero-based; nonapplicable indexes/field are empty. Fields are gauss, start offsets and durations seconds. Nested records overlap aggregate records and must not be summed as independent costs.

| Stage/quantity | Recorded behavior |
|---|---|
| `determine_polarity` | Magnet common-sign selection |
| `apply_global_polarity_flip` / `apply_global_polarity_noop` | Relay path decision/work |
| `axis_X_set_current`, Y/Z equivalents | Supply current call when actually issued |
| `axis_X_write_delay_sleep`, Y/Z equivalents | Configured pacing after actual writes; absent on skipped current |
| Axis `safe_shutdown` | Possible axis API diagnostic; normal set_vector uses keep_output_enabled and does not take that zero-current shutdown path |
| `settling_sleep` | Only when settling_time_ms > 0 |
| `grab_software_frame` or `snap` | Each repeat camera call, labeled by avg_index |
| `camera.trigger_mode_set`, `camera.flush_pre`, `camera.queue_buffer`, `camera.acquisition_start` | Held-open setup stages when supplied by driver |
| `camera.software_trigger`, `camera.wait_buffer`, `camera.buffer_to_image` | Per software-triggered frame |
| `camera.acquisition_stop`, `camera.flush_post` | Held-open teardown |
| Standalone snap camera stages | trigger_mode_set, queue_buffer, acquisition_start, software_trigger, wait_buffer, buffer_to_image, acquisition_stop, flush, each with camera prefix |
| `write_plane` | HDF5 plane write/flush only when streaming |
| `process_frame` | Backend latest-image assignment; **not** fluorescence calculation |
| `emit_live_update` | Synchronous worker callback including fluorescence and signal emission; not queued GUI painting time |
| `point_total` | Whole point, including field/pacing/settling/frame/mean/store/callback work |
| `scan_total` | Sweep lifecycle, including sweep cleanup; not subsequent running-average persistence callback |
| `run_total` | Experiment.run through camera lease cleanup, before worker final save/CSV writing |

There is **no dedicated per-point `averaging_time` stage**, no separate full `set_scan_point` aggregate, and no independent per-point NumPy append or GUI rendering measurement. Their time is included in broader totals where applicable. Substage relative starts are reconstructed cumulatively from durations; they are diagnostic approximations rather than independent hardware timestamps. Evidence: `zero_field_experiment.py:196`, `:209`, `:360`, `:617`; `hardware/magnet/magnet.py:214`; `andor_neo_andor3.py:284`.

Worker writes `data/zero_field_timing_YYYYMMDD_HHMMSS.csv`, with `data` relative to **process working directory**, independent of the selected cube-output directory (`gui/zero_field_worker.py:166`). Header comments contain generated time; start/stop gauss; field_points/axis; settling_time_ms; averages; averaging_enabled/num_scans/save_raw_scans; zero_other_axes; acquisition_roi; camera_exposure_s; camera_binning; measured_run_total_s. Exposure/binning are read from camera attributes after run, not queried afresh by the CSV writer. No file is created if disabled/no records. Failure logs an exception without failing acquisition.

Timing adds clock/list/merge overhead and post-run file I/O but does not add a different point/frame sequence. Supply pacing and held-open acquisition are active independent of timing. CSV can be written when cube saving is off, and after the finished signal has already reached the GUI. Simulation stage durations do not establish real hardware latency. Tests verify stages/counts and timing on/off acquisition parity (section 20).

## 15. Zero Field Analysis Capability

This table distinguishes live/reusable framework functionality from offline scripts and ad hoc local diagnostics. A descriptive fluorescence image is not a calibrated field map.

| Feature | Status | File/Class | Notes |
|---|---|---|---|
| Raw mean fluorescence | Implemented | `framework/fluorescence.py:8`; `framework/analysis/zero_field.py:8`; worker `_compute_live_fluorescence` | Full acquired image mean; counts/pixel; completed-cube curve supported |
| Difference image / image delta-F | Implemented | `gui/zero_field_window.py:304`; `analysis/zero_field_averaging.py:534` | GUI reference is first run image; offline report reference is nearest numeric-zero field; raw cube unchanged |
| Normalization | Implemented offline; absent live | `analysis/zero_field_averaging.py:525` | `signals / signals.mean()`; not endpoint normalization |
| Delta-F curve | Partial | Offline difference/symmetry reporting at `:543` | Matched +B/-B differences and image subtraction; no general reference-selectable live delta-F curve |
| Delta-F/F | Partial/ad hoc | Untracked `field_ratio.py:8` | Near-zero/wing ratio image and percent change; no reusable reference-defined delta-F/F analysis API or live mode |
| Percentage contrast | Implemented descriptive offline | `analysis/zero_field_averaging.py:578` | `100*(max-min)/mean`; also signed relative +B/-B differences; not an inferred physical contrast model |
| Baseline subtraction | Absent for Zero Field | No Zero Field correction path; compare `experiments/odmr_experiment.py:498` | Main Window baseline applies only to ODMR |
| Zero-field center detection | Absent | `analysis/zero_field_averaging.py:527` merely chooses argmin(abs(fields)) | Nearest commanded zero is not fluorescence-derived center estimation |
| Polynomial fitting | Absent | No Zero Field fitter found | None |
| Lorentzian fitting | Absent for Zero Field | `analysis/odmr_fit.py` is used by ODMR only | Existing ODMR fitter is not wired to Zero Field |
| Peak/dip detection | Partial | `analysis/zero_field_averaging.py:395` | Sample min/max and their fields; no robust peak finder or center uncertainty |
| First derivative / symmetry | Implemented offline | `analysis/zero_field_averaging.py:526`, `:543` | Numerical gradient, matched +/- field intensity comparisons |
| Drift removal | Absent | Untracked `focus_vs_field.py:13` only measures focus/centroids | No registration, temporal correction, or drift compensation |
| X/Y/Z compensation | Partial manual command capability; absent analysis-driven workflow | `Magnet`, `MagnetControlWindow`; backend zero_other_axes=False | Global-sign restriction; GUI scan defaults zero other axes; no fitted offsets/controller |
| Automated iterative zeroing | Absent | No optimization loop/controller found | None |
| Magnetic-field maps | Absent | Image/ratio maps only | No fluorescence-to-field inversion/calibrated per-pixel B reconstruction |

Offline `analysis/zero_field_averaging.py` provides three substantive workflows: `average_scans` streams plane-wise averages to compressed NPZ and writes a fluorescence text report; `average_plane_range` writes resumable `plane_###.npy` checkpoints; `package_averaged_planes` creates NPZ and optional plots/Markdown report. Inputs can be HDF5 or NPZ; HDF5 output is explicitly rejected by these offline functions. The report includes fluorescence, mean-normalized fluorescence, derivative, sampled histogram, first/near-zero/last images, differences, extrema/contrast, and symmetry. CLI wrappers expose the functions; the `.cmd` wrapper remains tied to a specific July dataset. See `analysis/zero_field_averaging.py:312`, `:413`, `:451`.

Offline limitations matter for real data: `average_scans` defaults to the **acquisition ROI** from metadata and slices it again as image-local coordinates; equal-shaped sources are not checked for equal field axes/settings; checkpoint filenames have no source identity; some generated integrity “Pass” lines are literals, not actual finite-data validation. Section 22 records those issues. Existing analysis code should therefore not be treated as a fully validated measurement pipeline.

The two untracked scripts are part of this local baseline, not committed framework APIs. `field_ratio.py` compares |B|<=1.5 G with |B|>=13 G, reports ratio-map statistics and radial summaries. `focus_vs_field.py` reports normalized gradient-energy sharpness and intensity centroids, then compares the same windows. They load full cubes, use hard-coded thresholds and unguarded empty/zero divisions, and do not correct focus/drift or estimate a magnetic field. They were inspected, not executed on measurement data.

## 16. Zero Field Real-Hardware Readiness

The software path exists end to end: real serial supplies/relay -> calibrated field commands -> real Andor software-triggered images -> averaging -> live fluorescence/display -> HDF5 output. README/CLAUDE document actual rig use and timing/SCPI observations. The safe tests verify controlled software behavior and fake-driver contracts. This review supplies no fresh hardware, field-calibration, image-physics, or instrument-health validation.

| Requested real workflow | Current support / remaining manual work |
|---|---|
| Hold two axes fixed | Ordinary GUI holds their **requested fields at zero**. Backend opt-out preserves per-sweep entry values; no GUI fixed-bias controls, no arbitrary mixed-sign vector, no automatic bias restoration across repeated sweeps |
| Sweep one axis | Implemented X/Y/Z inclusive linear sweep in G, converted to mT at magnet boundary |
| Collect averaged images | Implemented per-point frame average plus optional completed-sweep running average; individual repeat images not saved |
| Fluorescence versus field | Implemented live raw mean and completed-average curve; offline descriptive normalization/plots available |
| Save timing | Implemented opt-in environment/config flag; CSV separate from chosen cube folder and Save Data copy |
| Repeat scans | Implemented multi-scan averaging and optional per-scan files; manual new runs also available |
| Compare X/Y/Z | Acquire each axis separately; no integrated multi-dataset comparison or automatic three-axis sequence; external/offline processing required |

Before interpreting a current real scan as a verified requested field, the unresolved command/readback and calibration-domain limitations must be understood. The local calibration can generate signed current requests, cached state is not measured field, and the most recent pacing value has no calibration validation. These are software-readiness observations, not an evaluation of physical experiment results.

Automated center finding, uncertainty estimates, robust reference normalization, drift correction, fitted compensation and iterative zeroing still require development. Users currently select scan ranges/settling/averages, inspect fluorescence and offline plots, and compare axes manually. Full unattended readiness is limited by the specific ownership, shutdown and disk-error cases in section 22.

## 17. ODMR Status

The GUI defaults to a 2.77-2.97 GHz coarse sweep with 20 points and -10 dBm; optional one/two dense frequency windows augment the coarse grid. Worker builds a sorted unique grid but acquires a random permutation, storing results at sorted indexes. It applies requested microwave frequency, configurable settling, power and RF enable, then takes OFF/ON frames through the fast switch, not source-power modulation (`gui/odmr_worker.py:32`; `experiments/odmr_experiment.py:655`).

Each acquisition repeat produces two camera images. At a frequency, GUI camera count is `2 * repeats * averages`; repeats are averaged within backend processing and averages are outer worker re-acquisitions. The GUI arms external camera acquisition once for its scan; each trigger uses a single-shot sequence (`n_runs=1`) and preserves persistent relay outputs. Older camera API fallback remains. Standalone `ODMRExperiment.run()` gets a lease and uses generic ScanExperiment, but does not itself invoke the GUI worker's scan-wide arm, so that path normally uses per-frame fallback (`odmr_experiment.py:231`, `:272`, `:566`).

For each pair, processing computes raw means I_off and I_on; subtracts configured baseline b; returns `100*(I_on-b)/(I_off-b)` averaged over repeats. This is a percent ratio around 100, not `100*(I_off-I_on)/I_off`. Nonpositive corrected OFF signal raises RuntimeError; small corrected OFF margin and nonpositive ON signal warn. Raw OFF/ON means remain uncorrected for display/export (`odmr_experiment.py:484`). Requested exposure is injected from Main Window; driver read-back exposure is checked with tolerance `max(20 microseconds, min(100 microseconds, 0.2*requested_exposure))` and recorded in saved config.

The GUI offers normalized or raw-mean plots, pulse preview, progress and single/double Lorentzian fit. **Live plotting is partial:** the point signal only updates latest arrays; `refresh_plot()` is called on mode change/completion rather than every point (`gui/odmr_window.py:185`, `:208`, `:306`, `:386`). No ODMR experiment-image view exists. Fitting uses unconstrained SciPy curve_fit and returns center(s), absolute FWHM, amplitude/baseline, fit curve, RMSE, R-squared and residual-noise SNR. Fit results are displayed but not automatically exported with data; bounds, robust failure diagnostics and uncertainty estimates are limited (`analysis/odmr_fit.py:27`, `:68`).

Saving is controlled by a GUI checkbox, unchecked by default. `data/data_manager.py::DataManager.save_odmr` writes `data/odmr_YYYYMMDD_HHMMSS/` with `frequencies_hz.npy`, `signal.npy`, optional `i_off.npy`/`i_on.npy`, `odmr.csv` and `config.json`. CSV columns with both raw means are `frequency_hz,I_off,I_on,normalized_signal`. These are scalar means, **not raw image frames**. GUI never fills the experiment ImageCube. The programmatic generic run can create a 5-D raw OFF/ON stack, but it is not the GUI export route.

**Local-only dependency:** `gui/odmr_window.py:11` imports `data.data_manager`, but `git ls-files -- data` and `git ls-tree -r HEAD -- data` are empty; `.gitignore` excludes `data/`. Both `data/data_manager.py` and `data/__init__.py` exist locally and were inspected, but are not part of the tracked checkout. Consequently this workspace can import a module a fresh tracked checkout lacks, and MainWindow imports ODMRWindow during startup even when the user intends Zero Field or simulation. This is a high-priority reproducibility defect, separate from measurement export quality.

Optional ODMR timing writes CWD `data/odmr_timing_<timestamp>.csv`; columns are `point_index,freq_hz,avg_index,repeat_index,frame,stage,t_start_rel_s,duration_s`; headers include camera acquisition start/stop counts (`gui/odmr_worker.py:295`). The time estimate incorporates measured stage costs recorded in test fixtures, but does not model all low-delay stall behavior. Historical delays/readout numbers in CLAUDE are rig/ROI-specific observations, not performance guarantees for this checkout.

Simulator provides a gated Lorentzian intensity dip and noisy OFF/ON frames, with waveform limitations described in section 6. Safe tests cover gating, baseline arithmetic, ROI, external acquisition, single-shot/persistent outputs, exposure and estimate fixtures. They do not validate real timing/physics or robust fit recovery. Cleanup disarms/releases camera but does not explicitly RF-off the microwave or close the streamer; queued stop takes effect at worker event-loop boundaries, potentially after a full point.

## 18. Other Experiments

No additional implemented Rabi, Ramsey, T1, T2, pulsed ESR family, confocal stage scan, or magnetic-map experiment was found. The disabled main buttons are placeholders. `PulsedExperiment.build_sequence()` is a stub; listing Rabi/Ramsey in PulseSequenceViewer comments describes intended reuse, not execution code.

`PulseSequence` stores digital pulses with start/duration/state/channel. Current validation rejects negative start, nonpositive duration and negative channel (`sequencing/timing_rules.py:11`); it does not validate every device channel limit, overlap, pulse-state value or instrument timing quantum. `sequencing/test_sequence.py` is a print-on-import demonstration excluded by testpaths, not a hardware experiment or automated test suite.

Manual magnet control and live camera are genuine operational tools. The offline averaging/report scripts and untracked ratio/focus scripts are analysis utilities, not new acquisition experiments. The older `analysis/__init__.py` fitting function coexists with the GUI's active `analysis.odmr_fit` functions; that duplication does not create another experiment.

## 19. Configuration Status

`ConfigManager` loads JSON and traverses nested keys without schema validation (`config/config_manager.py:16`). MainWindow uses a CWD-relative config path, so normal startup assumes the repository working directory. Configuration is valid JSON; the EventCamera fragment is structurally misplaced top-level data, not a JSON parsing error.

| Section/keys | Current use |
|---|---|
| `spcm.type="Andor"` | Active camera selector; device index 0 hard-coded; `address`, `classname`, nested Switch are not active camera selection |
| `frequencyGenerators[0].type/address/port` | SG386 selection/address, port forwarded but unused in VISA resource; only first generator used |
| `pulseGenerator.type/ipAddress/channelNames/channelValues` | Active streamer and digital map; address 132.64.56.138 |
| `Helmholtz.X/Y/Z` | Supply types/COM addresses/channel, current limits and calibration; values in section 12 |
| `Helmholtz.flip_direction.available/switch.*` | Relay availability/channel/name; global sign behavior |
| `Helmholtz.scpi_write_delay_s` | 0.05 s after actual vector current writes |
| `dataOutput.zeroFieldDirectory` | `data`, resolved from repository for Zero Field |
| `--sim` / per-device `type=sim` | Actual simulator selection; not debugMode/dummy |
| ROI, exposure, binning, baseline | Runtime AcquisitionState/UI; not a persisted configuration section |
| Experiment parameters | Widget-generated dictionaries captured on Start; metadata snapshots; no active JSON experiment templates |
| Diagnostics | `NV_ZFE_TIMING`, `NV_ODMR_TIMING`, `NV_ZFE_CHECK_PS_ERRORS` or applicable constructor/config dictionaries; no ordinary GUI toggles |

Unused/stale keys include `debugMode`, `showEvents`, `setupNumber`, `niDaq`, `dummy`, `pulseGenerator.trigger`, analog channel names/values, top-level EventCamera fields, `stages`, laser direct/AOM/delay configuration, detector block, microwave.MW switch/delays, generator serialNumber/default/min/max specifications, and relay type/classname/delays. E3632A `range` is retained/displayed but not applied. Extra frequency-generator entries are ignored. Digital detector/MW/laser channel values recur in stale blocks and the active channel map; only checking one copy can mislead. Source selection/control call sites are in `hardware/hardware_manager.py`, `hardware/microwave/sg386.py`, `hardware/magnet/`.

Important code-fixed parameters include camera SDK index/readout/cooling/gain/encoding, serial baud/timeout, simulator dimensions/noise/resonance, exposure/acquisition timeouts, pulse tail and some relay synchronization behavior, GUI defaults, HDF5 compression, and one-second filename timestamps. These are possible future configuration choices, not modifications made by this review. The relevant immediate issue is understanding which values actually drive the current instruments.

Other active configuration files are `pytest.ini` (testpaths/hardware marker), `tests/conftest.py` (safe collection policy), pinned `requirements.txt` plus pytest>=9, and `.gitignore`. Generated `data/odmr_*/config.json` files are saved run snapshots, not application startup configuration. README/CLAUDE and dated docs are guidance, not executable configuration, and contain stale statements about non-swept axes, test counts, exposure tolerance, simulator dependency availability and cleanup assumptions.

## 20. Test Status

### Collection scope and actual result

`tests/` contains **78 Python files**: 76 `test*.py` scripts plus conftest and package initializer. The default safe allowlist contains **19 test modules**. Fifty-seven other scripts are excluded before import; many directly command hardware during import, while others are manual/guarded/simulation utilities. They are excluded, not counted as skipped test cases. `sequencing/test_sequence.py` is outside configured testpaths. The allowlist, not historical documentation, is authoritative (`pytest.ini:5`; `tests/conftest.py:15`, `:60`). No `--hardware` collection/execution occurred.

**Completed safe run: 260 collected; 259 passed; 0 failed; 0 errors; 1 skipped; no pytest warnings; 17.86 s; exit 0.** JUnit XML independently confirms 260/0/0/1 and 17.858 s. The skip is the POSIX chmod directory-permission test on Windows (`tests/test_paths.py:36`).

| Allowlisted module under tests/ | Collected | Main coverage |
|---|---:|---|
| `test_acquisition_state.py` | 2 | Complete camera setting application |
| `test_baseline_correction.py` | 17 | ODMR baseline arithmetic/errors/warnings |
| `test_camera_ownership.py` | 7 | Lease/live suspend/restore and exceptions |
| `test_exposure_propagation.py` | 15 | Startup, exposure limits/read-back/drift |
| `test_magnet_scpi_traffic.py` | 23 | Write suppression/cache/pacing/error queues |
| `test_mw_switch_gating.py` | 14 | MW pulse-gate behavior, simulator gating |
| `test_odmr_acquisition_roi.py` | 2 | Hardware ROI versus analysis |
| `test_odmr_external_acquisition.py` | 11 | Held-open external camera workflow |
| `test_odmr_time_estimate.py` | 11 | Time model and fixture data |
| `test_paths.py` | 4 | Output paths; includes the one Windows skip |
| `test_pulse_streamer_single_shot.py` | 22 | Finite run/final/persistent-output behavior |
| `test_roi.py` | 11 | Shared ROI validity/extraction |
| `test_streaming_controller.py` | 3 | Streaming thread lifecycle |
| `test_zero_field_analysis.py` | 19 | Fluorescence, NPZ/HDF5/stream structure |
| `test_zero_field_averaging.py` | 36 | Frame/sweep averages, worker, display, metadata, save/stop |
| `test_zero_field_averaging_module.py` | 20 | Offline plane averaging/readers/packaging |
| `test_zero_field_software_acquisition.py` | 22 | Software-trigger mode, buffers, fallback/cleanup |
| `test_zero_field_timing.py` | 14 | Timing stages/counts/parity/CSV |
| `test_zero_field_window_save_toggle.py` | 7 | Save toggle, disk/memory fallback |
| **Total** | **260** | **259 pass, 1 skip** |

### Executed command and isolation

The successful run used existing `.venv\Scripts\python.exe`, not a dependency install. It ran the entire default allowlist with real devices replaced by the suite's fakes/simulators. Real driver modules/vendor packages are import dependencies but constructors/connections are controlled by these tests. Bytecode, pytest cache and external plugin autoload were disabled; Qt was offscreen. Process working directory, pytest temp files, camera diagnostic log and JUnit XML were redirected to OS temp so tests did not write generated files into the repository.

PowerShell environment and interpreter invocation:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
# The Python payload below was passed on stdin using a single-quoted here-string:
# <payload> | & '.\.venv\Scripts\python.exe' -B -
```

Exact successful Python payload:

```python
import os, pathlib, sys, tempfile
root = pathlib.Path(r'C:\Lab_Py\nv_control')
temp = pathlib.Path(tempfile.mkdtemp(prefix='nv_control_review_'))
sys.path.insert(0, str(root))
os.chdir(temp)
import utils.camera_diagnostics as diagnostics
diagnostics.LOG_PATH = temp / 'camera_diagnostics.log'
print('REVIEW_TEMP=' + str(temp), flush=True)
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
app = QApplication.instance() or QApplication([])
acknowledged = []
def acknowledge_save_warning():
    for widget in app.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible() and widget.windowTitle() == 'Zero Field' and widget.text().startswith('Saving is off:'):
            acknowledged.append(widget.text())
            widget.accept()
timer = QTimer()
timer.timeout.connect(acknowledge_save_warning)
timer.start(100)
import pytest
result = pytest.main(['-c', str(root / 'pytest.ini'), str(root / 'tests'), '-p', 'no:cacheprovider', '-q', '-ra', '--basetemp', str(temp / 'pytest'), '--junitxml', str(temp / 'results.xml')])
timer.stop()
print('SAVE_WARNING_DIALOGS_ACKNOWLEDGED=' + str(len(acknowledged)), flush=True)
sys.exit(result)
```

The result files are in `C:\Users\Bargill.lab\AppData\Local\Temp\nv_control_review_g8ual8r2`, including `results.xml`. Exactly **one** informational Save-data-off warning was acknowledged. This adapter clicks only the named informational dialog; it does not bypass assertions, skip tests, replace experiment methods, or approve hardware access.

An earlier unattended run without that timer stalled at `tests/test_zero_field_window_save_toggle.py:127`, which toggles Save data off outside a warning mock. `gui/zero_field_window.py:373` then opens a modal dialog; the incomplete run was interrupted. A preliminary quoted `python -c` wrapper failed with SyntaxError before pytest execution. Neither is being represented as a completed test result. Therefore the result above is a successful complete safe suite **with the documented headless dialog adapter**, not a claim that bare unattended pytest currently completes unassisted.

### Zero Field coverage and remaining gaps

| Requested behavior | Existing evidence |
|---|---|
| Per-point averaging and exact frame/store counts | `test_zero_field_averaging.py:175`, `:388`; deterministic 2-point x 2-average example asserts four snaps, two updates and fluorescence [2,6] |
| Progress and live fluorescence | Same worker tests; exact point update order/count and growing arrays |
| ImageCube validation/metadata | `test_zero_field_averaging.py:230`; `test_zero_field_analysis.py`; metadata/axis mismatch, nested fields and persistence |
| Camera ROI and restoration | `test_zero_field_averaging.py:301`, `:313`; acquisition/ownership/ROI modules |
| Multiple scans, raw files, stop, streaming | `test_zero_field_averaging.py`; running means, incomplete sweeps, direct single-scan output, saved average recovery, memory-only parity |
| Difference image and copy safety | `test_zero_field_averaging.py:98` onward, static display/helper assertions |
| Scaling and colormap | Display-parameter tests in same module; not full renderer/LUT visual tests |
| Pixel inspector and line profile | Numerical helper tests in same module; not a complete interactive mouse/line test |
| ImageViewer integration | Not applicable as named component: no ImageViewer class; pg.ImageView/widget functionality tested indirectly |
| Held-open acquisition | `test_zero_field_software_acquisition.py`; begin/grab/end, copy/requeue, mode switching, fallback, failure/stop, sequential ODMR/Zero Field |
| Timing | `test_zero_field_timing.py`; stage/counts, sign changes, flags, acquisition/metadata parity, CSV output |
| Magnet commands | Averaging tests for zero-other policy/entry vectors; `test_magnet_scpi_traffic.py` for cache invalidation, failed writes, pacing/error polls |

Meaningful missing tests include GUI lock inversion under actual concurrent Qt interactions; guaranteed cleanup when stream metadata/close fails; shutdown while worker is active; local-only DataManager/startup and fresh simulation installation; negative-current/current-range behavior for the current config; manual magnet interference; full HardwareManager(force_sim=True) construction (current wiring test includes source inspection); graphical ROI transform after crop/binning; full interactive display/LUT behavior; ODMR live-plot refresh and fitting robustness; offline sensor-coordinate ROI double-crop, incompatible equal-shaped scan grids, checkpoint provenance, malformed later planes and finite-value integrity claims. Real SDK/SCPI timing/physical acceptance remain outside hardware-independent tests.

The nonallowlisted `test_zero_field_backend.py`, `test_odmr_backend.py`, `test_image_cube.py` and other historical scripts are not additional passing automated coverage simply because their filenames suggest it. Tests are substantial but their passed count does not establish all GUI, instrument or scientific invariants.

## 21. Recent Major Development

The previous report identifies `9439c12` as its code baseline. Exactly five commits follow it: `2d6e03e`, `f6479dd`, `3dbb58d`, `7c87940`, `fa4acea`. Distinguish that narrow comparison from the larger post-July architecture work.

| Area | Since previous report revision `9439c12` | Broader capabilities after `828907b` milestone |
|---|---|---|
| Hardware | SCPI duplicate write removed; cached unchanged-current suppression and invalidation; configurable pacing; optional error queues; camera SDK exposure limits | Held-open external/software acquisition; buffer copy/requeue/freshness checks; finite-shot pulses and persistent relay state |
| Simulation | Exposure-limits and supply-diagnostic parity supporting new paths | Config-selected per-device/full simulation; microwave-camera/pulse wiring; software/external mode state |
| GUI | Zero Field Save data toggle, memory-only status/later Save Data; exposure in ms with SDK bounds | Main acquisition-state integration; Zero Field inspection/display and output directory controls |
| Visualization | No new standalone ImageViewer; existing display architecture retained | Raw/difference caches, explicit scaling/maps/pixel/line helpers |
| Zero Field | Optional no-disk workflow; scan-boundary supply-error checks and paced magnet calls | Default zero non-swept fields and entry metadata; multi-scan running averages; HDF5 streaming/partial-stop handling; direct single-scan output; software-held acquisition; timing |
| ODMR | Exposure-dependent drift-warning threshold | MW switch gating; baseline correction; scan-held external acquisition; measured-cost time estimate; persistent single shots |
| Architecture/data | Output ignore changes; previous report added and remains tracked; no broad rewrite | Shared offline averaging, NPZ/HDF5 and plane APIs, explicit ownership/state, safe test collection |
| Tests | Save-toggle and SCPI regression modules; modified exposure/timing/averaging assertions | Hardware-free allowlist; extensive acquisition, file, timing, baseline and gate tests |

The recent cleanup commits removed/ignored generated outputs, but the working directory still contains ignored artifacts and the local DataManager source. Commit message intent must not replace file-status inspection. Local X/Y/Z calibration edits and untracked ratio/focus diagnostics are newer working-tree facts, not committed feature history.

Historic CLAUDE observations describe real timing stalls, improved switch gating and serial overruns; the code fixes and fixture tests substantiate what software changed. They do not prove current calibration correctness or that every hardware symptom is resolved. July audits and the August report remain useful history but are superseded where current code differs.

## 22. Known Issues / Risks

These are source-backed findings. Severity reflects likely software/measurement impact, not a newly observed hardware failure. No fixes were applied.

| Severity | Component | Evidence | Impact |
|---|---|---|---|
| High | Missing tracked startup dependency | `gui/odmr_window.py:11` imports `data.data_manager`; current local `data/data_manager.py` exists but `git ls-files/ls-tree -- data` empty and ignore rule `data/` applies | Fresh checkout cannot reproduce this workspace's GUI startup, including Zero Field/sim launch |
| High | GUI/worker camera lock inversion | `framework/camera_ownership.py:120` blocks on experiment lock; lease retains it through `:91` resume; `gui/live_view_controller.py:52` waits for GUI Event | Settings action on GUI thread can block worker teardown; prior active live view creates a deadlock rather than clean ownership refusal |
| High | Disk-error path can skip magnet disable | `experiments/zero_field_experiment.py:694` calls writer.update_metadata/finalize/close before cleanup_scan try at `:706` | A file-system/HDF5 error during sweep finally can bypass magnet.disable and leave outputs enabled |
| High | Shutdown does not join experiment threads | `gui/main_window.py:824` closes windows then immediately shuts hardware; `zero_field_window.py:473` only requests stop | Device shutdown can race active acquisition/cleanup; child close refusal does not prevent parent shutdown |
| High | Driver shutdown API mismatch/incomplete cleanup | `hardware/hardware_manager.py:302` calls nonexistent microwave/camera disconnect and suppresses AttributeError; no streamer.close; `main_window.py:840` suppresses shutdown exceptions | Camera/VISA handles and persistent pulse outputs are not reliably closed/de-energized; first earlier failure aborts later cleanup |
| High | Manual magnet access not excluded during experiment | `gui/magnet_window.py:399`, `:582` directly mutate shared magnet/relay; no corresponding lease | Field/current/polarity can change during a scan without matching its recorded sweep values |
| Medium | Calibration command domain unchecked | Current Y offset/ratio -> negative current for requested zero; `magnet_axis.py:151` checks only absolute maximum | Simulator may accept requests the real supply rejects/limits; intended field cannot be inferred from cached command; physical calibration not adjudicated |
| Medium | Cache confirmation is transport-only | `magnet_axis.py:60`, `:168`; optional boundary-only error checks | SCPI-rejected values can be treated as unchanged until invalidation; no measured field/current confirmation |
| Medium | E3632A range not applied | `e3632a.py:64` has range API; initialization has no call | Display/config can show P15V without actually selecting it |
| Medium | Startup partially connects without rollback | `HardwareManager.initialize()` sequential connection path | Later initialization error can leave already-connected devices/resources active |
| Medium | Real dependencies imported in full simulation | `hardware/hardware_manager.py:1`; requirements omits vendor/serial packages | Fresh requirements-only sim environment fails before device selection despite README assertion |
| Medium | Live Stop bypasses ownership | `gui/main_window.py:474` directly camera.stop_stream; exceptions swallowed | Can touch experiment-owned camera rather than following the cooperative lease |
| Medium | SDK disarm errors suppressed | `andor_neo_andor3.py:232` clears flags before stop/flush, catches failures | Software state can say closed while hardware state is uncertain; restore may proceed incorrectly |
| Medium | Zero Field setup cleanup boundaries | `zero_field_experiment.py:422` timing enable/arm precedes inner try at `:449`; disarm precedes timing-disable without nested guarantee | Failed arm/disarm can leave diagnostic/state cleanup incomplete even though outer lease exits |
| Medium | Camera ROI/binning cached-state mismatch | Andor setters assign attributes before SDK success; GUI permits unsupported bins 5-7 (`andor_neo_andor3.py:545`, `:581`; `main_window.py:159`) | Rejected setting can leave requested/cached and actual settings divergent; UI errors interrupt configuration |
| Medium | Graphical ROI coordinate transform | Full-sensor overlay at `main_window.py:674`; cropped/binned `frame.T` display at `:492` lacks sensor transform | Overlay and selected region can be misleading after hardware crop/binning |
| Medium | ODMR output cleanup | `odmr_experiment.py:281`, `odmr_worker.py:378` do not RF-off/reset outputs | Scan completion/error is not a complete microwave-output shutdown |
| Medium | ODMR continuous plot wiring absent | `odmr_window.py:208` only stores data; refresh mode/completion only | User cannot rely on live spectrum painting at each acquired point |
| Medium | HDF5 streaming not atomic | `image_cube.py:514` deletes/recreates metadata_json; loader expects it; flush is not fsync/transaction | Crash can leave missing metadata or preallocated unwritten planes; current recovery is useful but not crash-proof |
| Medium | In-memory partial results lost on worker exception | `zero_field_worker.py:154` emits finished(None) | Memory-only failed run has no GUI recovery cube; disk streaming is recovery source only when enabled |
| Medium | Offline double ROI crop | `analysis/zero_field_averaging.py:345`, `:371` slices acquisition coordinates again | Already-cropped images can yield wrong/empty fluorescence summaries |
| Medium | Offline source compatibility incomplete | `analysis/zero_field_averaging.py:192` compares shape/dtype, takes first metadata | Different axes/field grids/exposures with same shape can be averaged silently |
| Medium | Checkpoint provenance/validation missing | `analysis/zero_field_averaging.py:430` reuses filename; `:485` writes later planes unchecked | Old/malformed checkpoints can be incorporated into a new cube |
| Medium | Offline integrity report overclaims validation | `analysis/zero_field_averaging.py:568` prints literal Pass for finite/shape checks | A report can claim validation not actually performed |
| Medium | Default headless test run modal | `test_zero_field_window_save_toggle.py:127`, window warning `:373` | Unattended suite hangs without the documented dialog interaction |
| Medium | Memory/copy scaling | `zero_field_experiment.py:385`, `:641`; `image_cube.py:94` repeated concatenate | Large ROI/points/averages retain repeat stack and growing cube; streaming alone does not bound RAM or append cost |
| Low | Output filename collisions | Second-resolution names, HDF5 mode w; DataManager folder.mkdir | Fast same-name Zero Field runs can overwrite; ODMR same-second output can raise |
| Low | Generic error/state and stop-axis inconsistency | BaseExperiment.cleanup sets FINISHED; ScanExperiment returns full vector with partial signals | Script callers can see misleading final state or mismatched arrays; Zero Field has its own truncation |
| Low | Mutable data/callback sharing | `ImageCube.__init__/add` reference arrays/dicts; multi-scan callback emits mutable accumulator | External code or delayed consumers can observe/mutate shared data; current display copies protect its own transforms |
| Low | Current GUI controls versus captured config | ZeroFieldWidget.set_running_state only styles button/status | Edited controls may represent next-run values while current scan uses earlier snapshot |
| Low | Stale/duplicate framework paths and docs | Legacy runner, camera, sim factory, ABC, old fitting function; stale docs/config keys | Future work may extend inactive code or rely on superseded assumptions |

The shared acquisition architecture is real and useful; the findings target particular lock/lifetime and validation failures. They do not justify claiming that ownership, ROI, simulation, metadata, or persistence are wholly absent.

## 23. Current Limitations

**Software:** A fresh checkout misses a local-only startup dependency; simulation still imports vendor packages. There is no general device scheduler or complete multi-device ownership. Camera lock inversion and asynchronous shutdown remain. ImageCube is mutable, generic schema checks are modest, and in-memory acquisition scales with full stacks. HDF5 incremental persistence is useful but not fully atomic. There is no standalone reusable ImageViewer or integrated multi-dataset Zero Field comparison UI. ODMR GUI exports scalars rather than raw pixel images and does not redraw each live point.

**Hardware integration:** Real drivers cover the current Andor/SG386/Swabian/E363x rig, not all historical device plans. Global polarity restricts mixed-sign vectors; field/current values are commands/caches. Coil settling is open-loop. Range/command acceptance and per-device close behavior have gaps. The local calibration was not validated physically. Laser analog power, stages, event/lock-in cameras, NI acquisition and Keithley are not implemented. Hardware-specific timing figures depend on rig/ROI and cannot be inferred from simulator success.

**Simulator:** No Zero Field physics, no field-dependent images, no realistic exposure-dependent brightness/noise or transport failure model, no real pulse timeline, no strict supply range acceptance, no camera-SDK timing guarantee, no built-in reproducible seed. It supports software-development control flow and data/UI checks.

**Zero Field analysis:** Raw mean, difference display, repeated-scan averages and offline descriptive normalized plots/contrast exist. A validated physical-center fitter, general delta-F/F convention, robust drift removal, per-pixel field maps, uncertainty propagation, compensation controller and iterative zeroing do not. Offline crop/compatibility/checkpoint checks require attention before treating all generated analysis claims as authoritative. Fixed-axis nonzero biases and X/Y/Z comparisons are not integrated GUI workflows.

## 24. Recommended Immediate Development Focus

1. **Make the real Zero Field acquisition lifecycle dependable.** Address the identified camera/GUI deadlock, manual magnet contention, stream-error cleanup and worker shutdown ordering; make driver closure and command-domain/error checks explicit. Preserve current held-open software acquisition, averaging, raw-image semantics and calibrated command path. Validate these software cases with hardware-free fault injection, then perform a separately authorized rig validation; this review did not authorize or perform it.
2. **Build the next Zero Field analysis step on validated saved data.** Fix sensor-versus-image ROI handling, cross-scan field/acquisition compatibility and checkpoint provenance first; then define a reference/normalization and center-estimation workflow suitable for real scans and axis comparison. Keep derived analysis separate from acquired counts and do not introduce simulator physics as a substitute for experiment interpretation.
3. **Restore a reproducible development baseline.** Include the required DataManager implementation in the tracked software layout, reconcile simulation import dependencies and stale documentation, and remove the safe suite's modal interruption. Add focused regressions for the identified failures rather than redesigning the framework.

## 25. AI Handoff Summary

Baseline: `C:\Lab_Py\nv_control`, branch `dev`, HEAD **fa4acea323f055d832bd8444d6dbc8e59a265ea6**. Original working changes were six calibration values in `config/setupInfo.json` plus untracked `field_ratio.py` and `focus_vs_field.py`. This review changed only `PROJECT_STATUS_REPORT.md`; no hardware access or source/config/test changes. Previous report's code baseline was `9439c12`, broader ownership milestone `828907b`.

Start at `main.py` -> `gui/main_window.py::main/MainWindow`. MainWindow loads `ConfigManager`, builds `HardwareManager`, owns frozen `AcquisitionState` (sensor ROI, requested exposure seconds, binning, ODMR baseline), and registers `LiveViewTimerController` plus camera-state restorer. GUI workers are QObjects on QThreads; they construct injected experiments directly. `controller/experiment_runner.py`, `hardware/camera/andor_neo.py`, `hardware/sim_hardware.py`, unused BaseCamera inheritance and PulsedExperiment stub are not the active architecture.

Hardware: active `AndorNeoAndor3`, `SG386`, `SwabianPulseStreamer`, `E3632A` X/Z and `E3631A` Y behind `Magnet/MagnetAxis`; common relay channel 3; digital laser/MW/detector map 1/2/5 (detector2=6). G -> mT at ZeroField.set_scan_point, then `(abs(B_mT)-offset)/ratio`. Current local X ratio/offset .88904/-.00182, Y .73450/.02133, Z .81593/-.00544; max 5 A each. Do not overwrite these user changes or assume HEAD calibrations. Y requested zero yields negative calculated current; no physical readback/calibration validation. Latest HEAD deduplicates/paces supply writes, 0.05 s after actual current writes, with cache invalidation and opt-in boundary error checks. No stages, Keithley or event-camera driver.

Simulation: `--sim` or per-device type dispatch; active co-located SimCamera/SimMicrowave/SimPulseStreamer/SimPowerSupply. Camera models crop/binning/noise and gated ODMR dip, no magnet response. Eager vendor imports remain, and `data/data_manager.py` is an additional ignored/untracked but required local startup dependency. A passing test run in this environment is not evidence a clean clone starts.

Zero Field: `gui/zero_field_window.py`, `gui/widgets/zero_field_widget.py`, `gui/zero_field_worker.py`, `experiments/zero_field_experiment.py`. GUI defaults -2..+2 G, 5 points, X, 100 ms settling, 1 frame/point; other axes zeroed by default. No fixed-bias GUI entries; backend opt-out is per-sweep entry state. Camera ROI from MainWindow; exposure/binning inherited from camera; one held-open software begin/end over whole multi-scan run. Each point commands magnet, waits, grabs A frames, averages, appends one raw mean image, optionally flushes HDF5, computes full-frame mean through worker, emits image/fluorescence/progress. `ImageCube.add` still concatenates. Multi-scan accumulation uses completed sweeps only; partial first sweep returned and later partial sweep excluded from prior average. Magnet disables each sweep. Display reference is a copied first image of the entire run; image cache/scale/map/profile are display-only. **No custom ImageViewer exists.**

Data: `framework/image_cube.py` mutable data/axes/metadata, NPZ legacy plus compressed/chunked HDF5, shape/plane helpers and `ImageCubeStreamWriter`. Zero Field 3D stack `(field,y,x)`, normally float64; scan axis Magnetic Field/G. Metadata described in section 13; generic experiment_type remains Unknown. Default GUI HDF5 streaming: direct final file for single sweep, per-scan streams and atomic running average for multiscan. Save data off retains memory; later Save Data works. Completion metadata and clean-stop truncation exist but hard-crash/metadata windows remain. Timing CSV independently enabled via `NV_ZFE_TIMING=1`; columns/actual stages in section 14, directory CWD data. No dedicated NumPy averaging timing stage.

Analysis: reusable raw mean helper in `framework/analysis/zero_field.py`; offline `analysis/zero_field_averaging.py` supports NPZ/HDF5 input, NPZ average/checkpoints/reports, mean normalization and descriptive contrasts/derivatives/symmetry, but has ROI/compatibility/checkpoint/integrity issues. Physical zero-field center estimation, drift removal, field maps, iterative compensation absent. Untracked field_ratio/focus scripts are ad hoc diagnostics, not those missing features.

ODMR: GUI `ODMRWorker` drives `acquire_configured_point` on randomized sorted-grid indexes, held-open external acquisition; OFF/ON switch gating channel 2; source stays on; ratio `100*(I_on-baseline)/(I_off-baseline)` averaged over repeats and GUI averages. Safe tests cover gate/baseline/trigger/exposure. Single/double Lorentzian fitter in `analysis/odmr_fit.py`. GUI retains/exports only scalar arrays through local `data/data_manager.py`; generic backend run can form 5D raw pairs but is a different path. Point signal currently stores without redraw; no RF-off cleanup. Do not restore obsolete per-frame microwave power gating.

Validation baseline: **259 passed, 1 skipped, 0 failed/errors, no pytest warnings**, 19 allowlisted modules/260 cases, 17.86 s. See section 20 exact isolated payload; offscreen timer acknowledged one unmocked informational save-off dialog. Bare headless run otherwise hung. Never import excluded diagnostic scripts or use `--hardware` during software-only review. Test artifacts are OS-temp, not repository output.

Resume real Zero Field development at the specific ownership/lifecycle risks: blocking GUI experiment lock plus GUI-event resume wait; file-finalization failure before magnet disable; shared manual magnet access; no worker join on shutdown; wrong driver disconnect APIs; supply command acceptance/calibration-domain gap. Then validate offline data semantics before adding physical-center/compensation analysis. Preserve acquisition-state ownership, full-sensor versus image-local ROI distinction, injection, held-open cameras, persistent relay outputs, raw-count cubes and partial-scan semantics. Do not broadly refactor these working mechanisms or revive inactive legacy layers merely because older documentation suggests them.

Validation checklist: all requested 25 sections are present; Git/structure/architecture/hardware/simulation/acquisition/data/visualization, all experiments, detailed Zero Field GUI/sequence/calibration/metadata/timing/analysis/readiness, ODMR, configuration/tests/history, concrete risks, limitations, three priorities and this handoff are covered. Hardware functionality was assessed from source and documented history, never by connecting instruments.
