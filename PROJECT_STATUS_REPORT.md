# NV Control Framework — Project Status Report

Report date: 2026-08-24  
Repository revision inspected: `9439c12` (working tree also contained a pre-existing modification to `config/setupInfo.json` and untracked `rick_null_1.csv`, `rick_null_2.csv`, and `rick_null_3.csv`; none was changed by this review).

## 1. Executive Summary

NV Control Framework is a Python/PyQt6 desktop application for wide-field nitrogen-vacancy (NV) diamond measurements. The active application controls an Andor Neo sCMOS camera, an SRS SG386 microwave source, a Swabian Pulse Streamer, and three Helmholtz-coil power supplies. It presently implements two experiment families: frequency-swept continuous/pulsed-gated ODMR (also labelled ESR in parts of the GUI) and magnetic-field-swept zero-field fluorescence imaging. It also provides live camera viewing and manual three-axis magnet control.

The project is an experimental laboratory application rather than a stable general-purpose framework. Its active ODMR and zero-field paths contain substantial real-hardware-specific work, diagnostics, safety/cleanup logic, and tests, and repository documentation states that both have run on the real rig. At the same time, device interfaces are largely duck-typed rather than enforced, configuration validation is limited, hardware diagnostic scripts are numerous, and scientific reproducibility metadata is incomplete. The framework is both simulation-ready and hardware-ready for its two implemented workflows, but simulation does not establish physical validity.

Rabi, Ramsey, T1, T2/Hahn echo, confocal scanning, event-camera operation, IDS/uEye operation, SmarAct motion, lock-in-camera operation, Keithley control, and automated zero-field optimization are not implemented. Some are mentioned in configuration or represented by disabled buttons only.

The newest architectural developments are: one application-wide acquisition state for exposure/binning/ROI; explicit camera ownership that suspends live view during experiments; held-open Andor external acquisition for ODMR and held-open software-triggered acquisition for zero-field; microwave gating through a Pulse Streamer-controlled switch rather than slow SG386 power writes; persistent pulse-output state preservation; incremental crash-aware HDF5 zero-field saving; cooperative partial-scan handling; multi-scan running averages; and off-by-default stage timing diagnostics derived from observed hardware stalls.

## 2. Repository Structure

Generated/cache directories are omitted.

```text
NV-Control-Framework/
├── main.py
├── README.md
├── CLAUDE.md
├── requirements.txt
├── pytest.ini
├── config/
│   ├── config_manager.py
│   └── setupInfo.json
├── controller/
│   └── experiment_runner.py
├── framework/
│   ├── base_experiment.py
│   ├── scan_experiment.py
│   ├── acquisition_state.py
│   ├── camera_ownership.py
│   ├── fluorescence.py
│   ├── image_cube.py
│   ├── paths.py
│   ├── roi.py
│   └── analysis/zero_field.py
├── experiments/
│   ├── odmr_experiment.py
│   ├── zero_field_experiment.py
│   └── pulsed_experiment.py
├── hardware/
│   ├── hardware_manager.py
│   ├── sim_hardware.py
│   ├── camera/{andor_neo_andor3.py,andor_neo.py,base_camera.py,sim_camera.py,streaming.py}
│   ├── microwave/{sg386.py,sim_microwave.py}
│   ├── pulse_streamer/{swabian_pulse_streamer.py,sim_pulse_streamer.py}
│   ├── power_supply/{base_power_supply.py,base_scpi_power_supply.py,e3631a.py,e3632a.py,sim_power_supply.py}
│   ├── magnet/{magnet.py,magnet_axis.py}
│   └── interfaces/serial_device.py
├── sequencing/
│   ├── pulse_sequence.py
│   ├── timing_rules.py
│   └── test_sequence.py
├── gui/
│   ├── main_window.py
│   ├── odmr_window.py
│   ├── odmr_worker.py
│   ├── zero_field_window.py
│   ├── zero_field_worker.py
│   ├── magnet_window.py
│   ├── live_view_controller.py
│   ├── image_inspection.py
│   ├── pulse_sequence_window.py
│   ├── panels/odmr_panel.py
│   └── widgets/{zero_field_widget.py,pulse_sequence_viewer.py}
├── data/
│   ├── data_manager.py
│   └── diagnostic logs/CSV recordings
├── analysis/
│   ├── odmr_fit.py
│   ├── zero_field_averaging.py
│   ├── average_zero_field_scans.py
│   ├── average_zero_field_planes.py
│   ├── package_zero_field_average.py
│   └── run_zero_field_15_scan_average.cmd
├── docs/
│   ├── zero_field_audit_2026-07.md
│   └── main_camera_zero_field_image_flow_report_2026-07.txt
├── tests/ (76 Python test/diagnostic files plus fixtures)
└── utils/camera_diagnostics.py
```

`gui/` owns user interaction and Qt orchestration. `experiments/` contains the two active experiment engines. `framework/` supplies shared lifecycle, scan, ROI, camera-ownership, fluorescence, and image-cube primitives. `hardware/` contains construction, real drivers, and current simulators. `sequencing/` represents and validates digital pulse timing. `analysis/` provides offline fitting and memory-conscious zero-field averaging/reporting. `data/` contains the ODMR saver and captured diagnostics. `tests/` mixes ordinary automated tests with opt-in real-hardware diagnostic scripts. `docs/` contains focused audits rather than user manuals. `controller/experiment_runner.py` is legacy and is not in the GUI call path.

## 3. Application Entry Points

| Entry point | Launches | Configuration/dependencies | Typical command | Status |
|---|---|---|---|---|
| `main.py` | Delegates to `gui.main_window.main()`; opens the main live-camera/hardware/experiment launcher | `config/setupInfo.json`; real vendor libraries and reachable devices unless `--sim` | `python main.py` or `python main.py --sim` | Primary/current |
| `gui/main_window.py` | Same main window when run as a module/script; parses `--sim` with `parse_known_args` | Same as above | `python -m gui.main_window --sim` | Direct equivalent |
| `analysis/average_zero_field_scans.py` | Offline streaming average of `.npz`/HDF5 zero-field scans plus text report | NumPy, h5py; input scan files | `python analysis/average_zero_field_scans.py ...` | Supported CLI utility |
| `analysis/average_zero_field_planes.py` | Resumable per-plane averaging for large scan sets | NumPy, h5py | `python analysis/average_zero_field_planes.py ...` | Supported CLI utility |
| `analysis/package_zero_field_average.py` | Packages averaged planes and optionally renders a plotted report | NumPy, h5py, matplotlib | `python analysis/package_zero_field_average.py ...` | Supported CLI utility |
| `analysis/run_zero_field_15_scan_average.cmd` | Repository-specific Windows wrapper around offline zero-field averaging | Expected local scan paths/environment | Run the `.cmd` from a Windows shell | Convenience/experiment-specific |
| `sequencing/test_sequence.py` | Constructs/prints a sequence for development | No hardware required | `python sequencing/test_sequence.py` | Developer example |
| Numerous `tests/test_*.py` scripts | Unit tests or manual hardware probes | Varies; hardware collection is excluded unless explicitly opted in | `python -m pytest` (safe subset), `python -m pytest --hardware` only on the rig | Validation/diagnostics, not app entry points |

The main window is the only current integrated application. It constructs all hardware before showing the GUI. In real mode, missing drivers or unreachable instruments can prevent startup. `--sim` overrides all selected device types in memory without modifying JSON.

## 4. Current Software Architecture

The active control chain is:

```text
MainWindow
  ├─ live view QTimer → camera streaming driver → latest frame → ImageView
  ├─ MagnetControlWindow → Magnet → MagnetAxis → SCPI power supplies
  ├─ ODMRWindow → QThread/ODMRWorker → ODMRExperiment
  │                                  ├─ SG386
  │                                  ├─ Pulse Streamer
  │                                  └─ Andor/sim camera
  └─ ZeroFieldWindow → QThread/ZeroFieldWorker → ZeroFieldExperiment
                                             ├─ Magnet/power supplies/relay
                                             └─ Andor/sim camera
```

- **GUI layer:** `MainWindow` owns shared hardware, live view, global camera settings, and child windows. Dedicated ODMR, zero-field, magnet, and pulse-preview windows encapsulate their controls and plots.
- **Experiment/control layer:** `BaseExperiment` provides running/stop state and lifecycle hooks; `ScanExperiment` provides a generic scan loop and `ImageCube`. ODMR and zero-field specialize this, although both override enough behavior that the shared scan abstraction is modest. GUI workers instantiate experiments directly; `ExperimentRunner` is unused.
- **Hardware abstraction:** `HardwareManager` maps JSON `type` values to concrete objects and injects them. Experiments do not import concrete drivers. Interfaces are behavioral/duck-typed; `BaseCamera` exists but active cameras do not inherit it.
- **Drivers:** real camera, microwave, pulse streamer, serial/SCPI supplies, magnet composition; matching current simulator modules provide the methods used by the application.
- **Configuration:** a JSON loader with nested-key lookup; the GUI supplies experiment parameters at run time.
- **Simulation:** `force_sim=True` substitutes all device implementations. The simulated camera is connected to simulated microwave and pulse streamer state to synthesize ODMR contrast.
- **Acquisition:** the Andor driver supports snap, streaming, held-open external triggering, and held-open software triggering. Camera ownership is serialized by a per-camera reentrant lock and live view is suspended/restored around experiments.
- **Processing:** ODMR computes OFF/ON fluorescence and contrast with optional baseline correction; zero-field averages repeat frames and whole scans, then computes mean fluorescence versus field. SciPy fitting is invoked from the ODMR GUI. Offline zero-field tools compute averages, normalization, derivatives, histograms, symmetry summaries, and image differences.
- **Plotting:** pyqtgraph is used interactively; matplotlib is used for offline reports and `ImageCube.show()`.
- **Threading:** live camera streaming uses Python threads; experiments use Qt worker objects moved to `QThread`; the main live image is polled by a GUI `QTimer`.
- **Pulse sequencing:** experiment-neutral `PulseSequence`/`DigitalPulse` objects in nanoseconds are compiled by the Swabian driver. ODMR builds OFF/ON sequences directly.
- **Logging:** Python logging plus optional CSV timing diagnostics and a best-effort camera diagnostic log. Some drivers also print status.
- **Persistence:** ODMR uses a timestamped folder with NPY/CSV/JSON. Zero-field uses `ImageCube`, preferably streamed HDF5, with `.npz` backward compatibility and partial-scan markers.

## 5. Hardware Support

| Device/instrument | Class/module | Real status | Simulation | Connection/API | Capabilities and limitations |
|---|---|---|---|---|---|
| Andor Neo 5.5 sCMOS | `AndorNeoAndor3`, `hardware/camera/andor_neo_andor3.py` | Active; repository records real-rig use | `SimCamera` | Vendor `andor3` SDK3 wrapper | Exposure readback, AOI/ROI, binning, snap, live stream, external-trigger sessions, software-trigger sessions, buffer requeue/copy, timing counts. Vendor dependency is not in requirements; several SDK features are tried defensively; no independent scientific calibration metadata. |
| Older Andor path | `AndorNeo`, `hardware/camera/andor_neo.py` | Legacy/unused | No dedicated counterpart | `pylablib.devices.Andor` | Basic snap/external snap/stream. HardwareManager never constructs it; pylablib is absent from requirements. |
| SRS SG386 microwave source | `SG386`, `hardware/microwave/sg386.py` | Active; documented real use | `SimMicrowave` | PyVISA TCP/IP resource | Connect, frequency/power set/get, RF on/off. SG384 is not separately represented. Fast per-frame gating is delegated to an external switch. |
| Swabian Pulse Streamer 8/2 | `SwabianPulseStreamer` | Active; documented real use | `SimPulseStreamer` | Vendor `pulsestreamer` SDK over IP | Compile/load/run digital sequences, single-shot runs, persistent digital outputs, manual output, close/reset. SDK absent from requirements; observed recovery stalls require configured delays. Analog channel mapping exists in JSON but the active pulse representation is digital-only. |
| Keysight/HP E3631A | `E3631A` | Active on Y axis | `SimPowerSupply` | Serial SCPI through `pyserial` | Output on/off, voltage/current set/query, channel selection. No independent field readback; pyserial is missing from requirements. |
| Keysight/HP E3632A | `E3632A` | Active on X/Z axes | `SimPowerSupply` | Serial SCPI | Output and current/voltage/range control. Same open-loop/readback limitations. |
| Three-axis Helmholtz coil assembly | `Magnet`, `MagnetAxis` | Active; documented real use | Composed over simulated supplies and streamer | Three supplies plus Pulse Streamer relay channel 3 | Field-to-current calibration, vector setting, zero, enable/disable, global polarity relay. Values are commanded/cached, not Hall-probe measurements. Global polarity means mixed-sign vectors have constraints; sign crossing can operate supplies/relay multiple times. |
| Microwave RF switch / gating line | ODMR sequence on configured `MW` channel | Active as part of ODMR | Pulse state modeled | Pulse Streamer digital channel 2 | Fast MW OFF/ON pairing while SG386 remains at fixed power. It is not a standalone driver and switch health is not read back. |
| Green laser/AOM control lines | Pulse channel `greenLaser`; analog map `aomPower` | Digital laser gate used by ODMR sequencing | Modeled digitally | Pulse Streamer digital 1; analog channel listed as 1 | Laser gate timing is implemented. There is no independent laser driver, power feedback, or active analog AOM-power control path in the experiments. |

No IDS/uEye camera, event-camera driver, SmarAct driver, Keithley driver, or dedicated lock-in camera implementation was found. Event camera, MCS2 stage, and laser/AOM structures in JSON are configuration stubs not read by `HardwareManager`.

## 6. Experiment Support

| Experiment | Status and implementation | Acquisition/normalization/processing | GUI, simulation, saving, limitations |
|---|---|---|---|
| ODMR / ESR frequency sweep | Working experimental real-hardware path: `ODMRExperiment`, `ODMRWorker`, `ODMRWindow` | For each frequency, SG386 is tuned; repeats acquire MW-OFF and MW-ON frames using Pulse Streamer-triggered camera gates. Mean fluorescence is computed over the acquired frame; contrast is derived from OFF/ON values with optional camera baseline subtraction. Raw OFF/ON and normalized modes are plotted. Single/double Lorentzian fits are available. | Full GUI and synthetic Lorentzian simulation. Saves frequency, normalized signal, optional OFF/ON arrays, CSV, and config. Hardware timing is sensitive below about 20 ms trigger delay and 10 ms fire delay. No spatial ODMR fit/image cube is saved by the GUI; frames are reduced to scalar means. |
| Zero-field magnetic sweep / wide-field fluorescence imaging | Working experimental real-hardware path: `ZeroFieldExperiment`, `ZeroFieldWorker`, `ZeroFieldWindow` | Sweeps one selected X/Y/Z field axis in gauss (converted internally for magnet commands), optionally zeros other axes, waits for settling, collects `averages` software-triggered frames at each point, stores their mean image, and computes mean fluorescence. Multiple scans form a running per-pixel average. Display may show raw, difference, or scaled images, but stored data remain raw averaged counts. | Full GUI and simulation. Incrementally streams HDF5, supports raw scan retention, running-average persistence, partial-scan metadata, pixel inspector, line profile, and offline analysis. There is no microwave resonance tracking/lock-in detection, automated coil-null optimization, Hall-probe verification, or demonstrated code-level extraction of a physical zero-field spectral feature. Individual within-point repeat frames are discarded after averaging. |
| Wide-field imaging | Implemented as the image modality underlying live view and zero-field scans, not as a separate experiment class | Camera AOI/binning/exposure; cached images; image cube over field | Real and simulated. No stand-alone image-series experiment or spatial ODMR map. |
| Confocal operation | Not implemented | No scan stage, point detector, or confocal acquisition sequence | No GUI or simulator. |
| Rabi | Planned placeholder only | No experiment class or sequence | Disabled main-window button. |
| Ramsey (source label: “Ramsy”) | Planned placeholder only | No experiment class or sequence | Disabled button. |
| T1 | Planned placeholder only | No experiment class or sequence | Disabled button. |
| T2 / Hahn echo | Planned placeholder only | No experiment class or sequence | Disabled button. |

`PulsedExperiment` is an abstract unused stub and is not evidence that pulsed protocols are implemented.

## 7. Zero-Field Experiment Status

The workflow begins in `ZeroFieldWindow`, which reads controls from `ZeroFieldWidget`, freezes the main window's acquisition state, validates/creates the output directory, creates a `QThread` and `ZeroFieldWorker`, and passes camera, magnet, ROI, exposure-related shared state, and scan parameters into `ZeroFieldExperiment`.

The experiment records the entry magnet vector, enables the magnet, optionally zeros non-swept axes, and generates a linear field vector. For each scan point it commands the selected axis through `Magnet.set_vector()`, using calibrated `MagnetAxis.field_to_current()` and the E3631A/E3632A supplies. Negative fields are represented through a global polarity relay on Pulse Streamer channel 3. There is no measured magnetic-field feedback; GUI “Read” reports cached commanded state.

The latest path opens one held software-triggered Andor acquisition for the whole run. At each field it waits the configured settling time, acquires and averages repeated frames, and emits the mean image plus field. A fallback calls `camera.snap()` for camera implementations without the held-open API. Each completed field plane is written to HDF5 and flushed. Multiple complete scans are folded into a running average; incomplete scans are preserved as partial files but excluded from a completed average. The final cube includes field axis values, camera/magnet settings, completion state, scan counts, and timestamps.

The GUI provides output directory, start/stop, axis, start/stop field, points, settling, frames per point, scan averaging, raw-scan retention, optional non-swept-axis zeroing, progress, fluorescence-versus-field plot, image display, color map/manual levels, and raw/difference display. “Save Data” is disabled because saving is automatic/streamed. Stop is cooperative at loop boundaries; an active sleep or SDK wait is not forcibly interrupted.

The Pixel Inspector reads the cached displayed image under the mouse and reports x/y/intensity. Line Profile uses a movable pyqtgraph line ROI and nearest-neighbor sampling from the displayed image versus pixel distance. Therefore difference/manual display settings can affect what is inspected visually; they do not alter raw stored frames. These are GUI exploration tools, not calibrated spatial metrology.

Offline tools can average large `.npz` or HDF5 scan sets plane-by-plane, resume from averaged planes, package a cube, calculate full-frame or ROI fluorescence, normalize it, take a numerical first derivative, compare ±B symmetry, render histograms and field/difference images, and report integrity. This is descriptive analysis; it does not fit a zero-field resonance model.

No automated field-null search, multi-axis optimization, gradient compensation, microwave-assisted zero-field spectrum, lock-in demodulation, or feedback from measured field exists. Before obtaining and defending an actual physical zero-field feature, the project still needs experimentally chosen acquisition physics, calibrated and verified field zero, demonstrated signal-to-noise/repeatability, control measurements, and analysis tied to the expected feature. The current simulation exercises UI/data plumbing and returns synthetic camera imagery; it is not a physical zero-field model.

## 8. Simulation Layer

Simulation exists so the integrated GUI, workers, acquisition state, sequencing, persistence, and error/stop behavior can be developed without contacting laboratory devices. It is selected globally with `python main.py --sim`, or per device by setting supported JSON `type` fields to `sim`. `force_sim` takes precedence without mutating configuration.

Current simulators are `SimCamera`, `SimMicrowave`, `SimPulseStreamer`, and `SimPowerSupply`; the real `Magnet` is composed over simulated supplies/streamer. Their public methods closely mirror active real drivers. HardwareManager explicitly links the simulated camera to microwave and pulse-streamer instances.

Simulated ODMR is the most physics-like model: camera counts include a frequency-dependent Lorentzian dip and inspect the pulse sequence to determine whether MW is asserted, with spatial background/noise. Zero-field scans run end-to-end, but the simulated camera is not coupled to the simulated magnet with a validated NV zero-field response. Consequently zero-field simulation primarily validates scan, GUI, averaging, inspection, stop, and file behavior.

`hardware/sim_hardware.py` contains older duplicate minimal simulator classes and `build_sim_hardware()`; it is not used by HardwareManager. Simulation cannot model SDK buffer faults, serial timing, relay transients, field calibration, optical drift, heating, real noise correlations, or the measured Pulse Streamer stalls.

## 9. GUI Status

The main window initializes hardware, shows a pyqtgraph camera image, and exposes live-view start/stop plus exposure (default 10 ms), binning, baseline counts, and coordinate ROI controls. The main spinbox is the exposure source of truth and is applied to the camera at initialization. ROI uses full-sensor coordinates, is validated centrally, and is represented by an overlay. A timer polls the driver's latest frame while a Python acquisition thread updates it.

Hardware launch controls open the Magnet window; microwave and direct power-supply buttons exist but are disabled. Experiment controls open ODMR and Zero Field. Rabi, “Ramsy”, T1, and T2 buttons are disabled.

ODMR has frequency range/points, power, averaging/repeats, camera/pulse timing controls, normalized versus raw plots, progress/current frequency/time estimate, start/stop, pulse preview, save, and single/double Lorentzian fitting. Its worker emits point, frequency, progress, error, and completion signals. Acquisition is incremental per frequency so the GUI remains responsive.

Zero Field has the controls and visualization described in section 7. It emits live planes and per-scan progress while writing automatically. Pixel/line inspection operates only on the currently cached displayed image.

Experiments run in dedicated `QThread`s. Errors are caught in workers and shown via labels/message boxes/signals; cleanup and thread quit/delete connections are present. Camera ownership prevents the live timer and experiment from acquiring simultaneously. A notable remaining risk is synchronous cross-thread waiting in `LiveViewTimerController`: a worker blocks until the GUI processes suspend/resume signals, so GUI-thread blockage can deadlock or stall acquisition. Manual magnet control is not explicitly disabled while an experiment owns the magnet, so concurrent user commands remain possible.

Features present but not normally exposed include environment/config-selected timing diagnostics (`NV_ODMR_TIMING`, zero-field timing configuration), lower-level camera acquisition counters, HDF5 plane loading, and richer offline zero-field report generation.

## 10. Configuration System

`config/setupInfo.json` is the sole integrated setup file. `ConfigManager` loads JSON and provides unvalidated nested dictionary access. There is no schema, defaults layer, type/range validation, migration, secret store, or friendly missing-key diagnostics. Experiment parameters mostly come from GUI widgets and are passed as dictionaries rather than being persisted in the setup JSON.

Important keys include:

- `dataOutput.zeroFieldDirectory`
- `pulseGenerator.type`, `ipAddress`, `channelNames`, `channelValues`, `analogchannelNames`, `analogchannelValues`
- `spcm.type`
- `frequencyGenerators[0].type`, `address`, `port`, frequency/amplitude limits
- `Helmholtz.X/Y/Z.type`, serial `address`, supply channel/range, `max_current`, `magnetic_field_ratio`, `magnetic_field_offset`
- `Helmholtz.flip_direction.switch.switchChannel`

Device selection accepts real names or `sim`; the CLI can force all-sim. The current file contains real network and COM addresses but no credentials. It also contains unused NI-DAQ, stage/MCS2, EventCamera, laser/AOM, detector, and microwave-switch structures. The EventCamera fragment is syntactically an unusual orphaned continuation after `spcm`, and no code reads it.

Hard-coded or GUI-resident values that merit eventual configuration include camera defaults/ranges, live timer interval, pulse timing defaults, ODMR scan defaults, zero-field scan/display defaults, hardware timeout values, output naming, fit initial assumptions, and known-safe minimum timing delays. Moving them requires validation so unsafe values are not silently accepted.

## 11. Pulse Sequencing

`sequencing/pulse_sequence.py` represents digital pulses as `(channel, start_ns, duration_ns, state)` and validates nonnegative timing/overlap through `timing_rules.py`. Timing units are nanoseconds. The Swabian adapter maps symbolic channel names from JSON to integer channels and compiles each channel into alternating-duration state patterns expected by the vendor SDK.

ODMR directly constructs laser, MW-switch, and detector/camera-gate pulses. MW OFF and ON frames differ by channel-2 assertion. Repeated OFF/ON sequences can be built, and triggered frames are fired with finite `n_runs=1`, avoiding stray repeated triggers. Reset and final output states preserve configured persistent outputs, especially the polarity relay, until `close()` deliberately de-energizes outputs.

Triggering is software dispatch to the Pulse Streamer followed by external camera acquisition. Tunable delays cover pulse lead/tail, camera gate, trigger, fire, reset, and frequency settling. Repository measurements show unsafe-short fire/trigger delays cause bimodal stalls; current defaults incorporate those observations.

`PulseSequenceWindow`/`PulseSequenceViewer` renders channels, timing, colors, and statistics without operating hardware. `sequencing/test_sequence.py` is a textual development aid. Limitations for future pulsed experiments are the digital-only model, lack of reusable protocol composition, no implemented analog waveform/IQ phase support, no Rabi/Ramsey/echo classes, limited formal timing constraints, and direct ODMR ownership of sequence construction.

## 12. Data Flow

An actual ODMR path is:

```text
User presses Start in ODMRWindow
→ window snapshots MainWindow AcquisitionState and panel parameters
→ ODMRWorker is moved to a QThread
→ worker creates ODMRExperiment with injected hardware
→ exclusive_camera_access acquires lock, stops live stream/timer, applies experiment ROI
→ camera external acquisition is armed once for the scan
→ for each frequency: SG386.set_frequency
→ PulseSequence builds/fires one MW-OFF camera gate; Andor returns copied/requeued frame
→ PulseSequence builds/fires one MW-ON camera gate; Andor returns frame
→ repeat/average OFF and ON intensities; apply optional baseline; compute normalized contrast
→ worker emits frequency/point/progress signals
→ GUI plots raw OFF/ON or normalized signal and enables Lorentzian fitting
→ on completion camera acquisition closes, ownership restores main camera state/live view
→ DataManager saves arrays, CSV, and JSON when the user invokes saving
```

Zero Field follows the same GUI→worker→experiment structure but commands magnet vectors, uses held-open software triggering, stores per-field mean images in an HDF5 `ImageCube`, emits live image/scalar fluorescence, and persists during acquisition rather than relying on a final Save button.

## 13. Threading and Concurrency

- ODMR and Zero Field each use a `QObject` worker moved into a fresh `QThread`. Signals carry NumPy arrays and progress back to the GUI thread.
- ODMR advances points incrementally from its worker; Zero Field performs its run in the worker. Stop sets cooperative flags; it cannot cancel blocking SDK waits, serial queries, or settling sleeps immediately.
- Camera live streaming uses `StreamController` plus a Python thread and a stop event. The GUI polls `latest_frame` through a `QTimer`.
- `exclusive_camera_access` uses a weakly keyed per-camera state and `RLock`; it suspends the registered GUI timer, stops streaming, and restores acquisition state/live streaming in `finally`.
- `LiveViewTimerController` marshals timer operations to the GUI thread and waits on a `threading.Event` without a timeout. If the GUI event loop is blocked or closing, a worker may wait indefinitely.
- Andor buffers and `latest_frame` cross thread boundaries; copy-before-requeue mitigates stale-buffer mutation. Exact driver thread-safety still depends on the vendor SDK.
- Child-window close events request stop and wait/clean up, but long hardware calls can delay shutdown. Forced process exit could leave hardware until OS/session cleanup, though HDF5 partial state is designed to remain identifiable.
- Magnet access lacks a shared ownership lock. Manual controls could race with zero-field commands. HardwareManager shutdown could also conflict with a still-running worker if window/thread ordering fails.

Long measurements are most exposed to blocking SDK calls, cooperative-stop latency, GUI-thread handshake waits, device disconnects, manual magnet interference, and unbounded environmental drift rather than CPU contention.

## 14. Data Saving and Reproducibility

ODMR `DataManager.save_odmr()` creates a timestamped directory and stores `frequencies_hz.npy`, `signal.npy`, optionally `i_off.npy`/`i_on.npy`, a CSV with frequency/OFF/ON/normalized columns, and `config.json`. It saves reduced scalar signals, not raw camera frames. Experiment timing diagnostics can separately write CSVs.

Zero-field `ImageCube` stores 3-D image data, axes, experiment type, scan-axis name/unit/values, and JSON metadata in `.h5`/`.hdf5` or legacy `.npz`. HDF5 is gzip-compressed and supports single-plane reading and incremental writes. Streaming metadata records `planes_written` and `scan_complete`; incomplete files are truncated to real planes. Multi-scan runs can retain individual raw sweep cubes and persist/update a running average. Metadata includes acquisition ROI, cached camera exposure/binning, field range/axis, averaging state/counts, power-supply models, magnet entry vectors, completion/stopped state, and UTC timestamps.

Strengths are explicit scan axes, raw zero-field image planes, requested settings, partial-run identity, and crash-conscious persistence. Reproduction is nevertheless incomplete: software commit/hash, full setup-config snapshot, package/vendor SDK versions, device serial/firmware IDs, actual SG386 readback, independent camera readback history, measured field/current/voltage traces, laser power, temperature, sample/optical alignment, calibration provenance, ROI sensor geometry, and timing-health summaries are not consistently embedded. ODMR lacks raw image data and much hardware metadata. Cached settings should not be confused with independent readback.

## 15. Tests and Validation

The repository contains 76 Python test/diagnostic files. `tests/conftest.py` deliberately excludes hardware-facing tests unless `--hardware` is supplied, an important safety boundary.

The ordinary suite covers acquisition state/ROI, camera ownership and streaming controller, image cubes/HDF5/partial writes, paths, fluorescence/baseline correction, simulated ODMR, MW switch gating, exposure propagation, time estimation, pulse compilation/single-shot/persistent outputs, magnet math/polarity/GUI behavior, power-supply backends with fakes, zero-field acquisition/averaging/analysis/timing/stop behavior, and shared-camera ODMR/zero-field coexistence. Qt GUI logic has targeted tests but no full end-to-end visual/UI automation.

Many `test_andor_*`, `test_srs*`, `test_swabian_*`, supply, serial, and backend scripts are manual integration/diagnostic programs that can contact real equipment. Some explore low-level SDK signatures and trigger options rather than asserting a stable API. `tests/test.py`, `test1.py`, duplicated Andor experiments, and direct-device scripts appear historical/diagnostic and should not be read as conventional unit coverage.

No tests cover an IDS/event camera, SmarAct, Keithley, confocal, Rabi/Ramsey/T1/T2, or physical zero-field feature because those implementations do not exist. Gaps include schema validation, full main-window startup/shutdown under injected failures, device dropout/reconnect, concurrent magnet ownership, long-duration soak behavior, scientific calibration, and comparison of simulation with measured physics.

Tests could not be run in the inspected environment: both `pytest -q` and `python -m pytest -q` failed because pytest is not installed/available. No packages were installed, per instruction. Static structure and Git history were inspectable.

## 16. Dependencies

| Group | Dependencies |
|---|---|
| GUI | PyQt6 6.10.2, PyQt6-Qt6, PyQt6_sip, pyqtgraph 0.13.7 |
| Scientific | NumPy 2.0.2, SciPy 1.13.1, matplotlib 3.9.4, h5py 3.16.0 |
| Imaging | opencv-python 4.13.0.92, Pillow 11.3.0; pyqtgraph/matplotlib for display |
| Hardware communication | PyVISA 1.14.1; additionally unlisted `pyserial`, vendor `andor3`, and vendor `pulsestreamer` are required for real hardware |
| Testing | `pytest>=9.0` |
| Development/support | PyYAML, packaging, importlib_resources, typing_extensions, and matplotlib transitive packages |

Potential issues: most packages are exact-pinned while pytest is open-ended; SciPy 1.13.1 predates/support may conflict with some NumPy/Python combinations; the inspected interpreter is Python 3.14 and had no pytest installed; vendor modules and pyserial are absent from requirements; the legacy pylablib driver imports another unlisted dependency; GUI fallback imports in `magnet_window.py` do not make the rest of the PyQt6-only GUI portable to PySide/PyQt5.

## 17. Recent Development Indicators

Git history is available (40 commits). The latest 20 commits, from 2026-08-05 through 2026-08-11, indicate:

1. Tooltips were updated to match measured behavior, suggesting the GUI is being aligned with real-rig findings.
2. Zero-field camera acquisition changed to one held-open software-triggered session, reducing repeated arm/disarm overhead and adding coexistence tests.
3. Zero-field timing instrumentation was added but left off by default across experiment, camera, magnet, and worker layers.
4. Documentation captured measured averaging and stall effects on data quality.
5. ODMR fire/reset defaults moved to 10 ms after observed Pulse Streamer stalls.
6. Exposure drift tolerance was adjusted to the Andor's measured row-time quantization.
7. Exposure propagation was consolidated into a GUI-driven single source of truth.
8. ODMR time estimation was rebuilt from timing recordings and fixtures.
9. Slow SG386 per-frame power settling/gating was removed.
10. MW gating moved to Pulse Streamer channel 2, improving speed and measured contrast.
11. Optional camera-baseline subtraction was added to ODMR contrast.
12. Persistent output states were preserved during reset so the magnet relay does not drop.
13. Pulse sequences were made single-shot and stale Andor buffers guarded.
14. ODMR external acquisition was held open across a scan.
15. Single-scan zero-field acquisition began streaming directly to its final file.
16. Partial scans were truncated/marked and rejected from ordinary averaging rather than padded with fabricated frames.
17. Zero-field files began streaming incrementally to a configurable directory.
18. HDF5 became the zero-field streaming format, with explicit limitations in the older averaging path.

The dominant direction is measurement reliability and data integrity on existing ODMR/zero-field hardware, not adding new experiment types.

## 18. TODOs and Incomplete Work

**Explicit placeholders/plans**

- Disabled Rabi, “Ramsy”, T1, and T2 buttons; no implementation behind them.
- Unused EventCamera, MCS2 stage, NI-DAQ, detector, and analog AOM configuration.
- Magnet “Read” explicitly notes that future code should query actual hardware/Hall probe.
- `PulsedExperiment.build_sequence()` is intentionally abstract and has no subclasses.

**Abstract/intentional unsupported operations**

- `BaseExperiment.run`, `ScanExperiment.set_scan_point/acquire_frame`, camera/power-supply abstract methods, and `.npz` streaming/single-plane reads raise `NotImplementedError` by design. These are not all bugs.

**Temporary/experimental instrumentation**

- ODMR and zero-field stage timing logs are off by default and described as temporary.
- `utils/camera_diagnostics.py` swallows logging errors intentionally.
- HardwareManager prints a “TEMPORARY” hardware summary.
- Checked-in timing CSV/log files are development evidence, not application resources.

**Known incomplete behavior**

- Open-loop magnet/camera setting metadata; no independent field readback.
- Zero-field point repeats are reduced to their mean and not retained.
- Cooperative stop cannot interrupt an in-flight wait.
- Zero-field simulation lacks a field-dependent physics model.
- Pulse abstraction is insufficient for the planned coherent pulsed protocols.
- Configuration has no schema and contains malformed-looking/unused legacy fragments.

Most bare `pass` statements are exception-cleanup fallbacks, fake test methods, or no-op resource closers, not missing feature bodies.

## 19. Dead, Duplicate, or Legacy Code

- `controller/experiment_runner.py`: imported only by `tests/test_odmr.py`; active workers run experiments directly.
- `hardware/camera/andor_neo.py`: older pylablib Andor implementation; HardwareManager uses `AndorNeoAndor3` only.
- `hardware/sim_hardware.py`: older minimal duplicate `SimMicrowave`/`SimPulseStreamer` builder; current simulators are co-located with device packages.
- `hardware/camera/base_camera.py`: unused ABC; active drivers are duck-typed.
- `experiments/pulsed_experiment.py`: unused abstract stub.
- `analysis/__init__.py` contains an older Lorentzian fit while `gui/odmr_window.py` imports the richer `analysis/odmr_fit.py`, creating duplicate fitting concepts.
- Numerous low-level `test_andor_*` and direct SRS/Swabian scripts overlap and appear to record hardware bring-up iterations. They may remain useful diagnostics but are not active application code.
- `sequencing/test_sequence.py`, checked-in `test_cube.npz`, diagnostic logs/CSVs, and the untracked `rick_null_*.csv` are development/measurement artifacts.
- Main-window disabled hardware buttons and unused setup blocks are legacy/planned surface area, not functioning integrations.

There is evidence these paths are unused or superseded, but not enough context to recommend deleting every diagnostic artifact.

## 20. Current Strengths

- Real and simulated hardware are selected centrally and injected; active experiments avoid concrete-driver imports.
- Camera ownership explicitly coordinates live view and experiments and restores state through exception paths.
- Recent Andor acquisition changes reduce rearming and protect buffer copies/requeues.
- Pulse single-shot semantics and persistent output handling encode real safety/measurement lessons.
- Zero-field HDF5 streaming distinguishes complete from partial scans and avoids fabricated data.
- Multi-scan averaging is memory-conscious and offline tools support plane-wise processing of large cubes.
- ROI validation and acquisition-state propagation are centralized and well tested.
- Hardware tests are opt-in, reducing accidental lab actuation.
- Recent commits use measured timing data and fixtures rather than speculative optimization.
- Documentation candidly separates implemented, planned, and legacy components.

## 21. Current Technical Risks

| Rank | Risk | Why |
|---|---|---|
| High | Open-loop magnetic field and incomplete readback | Zero-field conclusions depend on calibrated actual field, but the framework records commanded/cached values and has no Hall-probe feedback. |
| High | Scientific reproducibility gaps | Saved runs do not consistently capture code revision, complete hardware/config state, SDK versions, calibration provenance, environmental conditions, and actual readbacks. |
| High | Blocking hardware calls/cooperative stop | Long SDK, serial, sleep, and GUI-handshake calls cannot be immediately cancelled and may delay safe shutdown. |
| High | Magnet concurrency | Manual magnet control and zero-field experiment share the same object without an ownership lock, allowing conflicting commands. |
| Medium | Simulation/real divergence | ODMR simulation is useful but simplified; zero-field simulation is primarily plumbing and can give false confidence in experimental physics. |
| Medium | Configuration fragility | Raw JSON dictionary access, unused malformed-looking fragments, hard-coded defaults, and no validation make setup errors late and difficult to diagnose. |
| Medium | Driver/interface drift | Active classes do not implement enforced protocols/ABCs; method parity is maintained manually. |
| Medium | Hardware startup coupling | Main startup connects every device eagerly; one unavailable device may prevent unrelated GUI functionality. |
| Medium | Data-path inconsistency | ODMR save is user-triggered and scalar-only; zero-field save is automatic and image-rich. Failure modes and metadata differ substantially. |
| Medium | Timing quirks not structurally enforced | Safe delay defaults reflect measured stalls, but users can select lower values and time estimates do not model the stall regime. |
| Low | Legacy/duplicate code confusion | Multiple camera, simulator, fitting, and runner implementations can mislead future development despite documentation. |
| Low | GUI toolkit inconsistency | One file attempts PySide/PyQt5 fallback while the integrated app requires PyQt6. |

## 22. Current Development Status by Component

| Component | Status | Confidence | Notes |
|---|---|---:|---|
| Main PyQt6 application | Working | High | Integrated real/sim launch path |
| HardwareManager/type selection | Working | High | Eager construction; all-sim override |
| Andor3 camera driver | Hardware-tested / Experimental | High | Extensive real diagnostics and recent acquisition hardening |
| Legacy pylablib Andor driver | Legacy | High | Not constructed |
| SRS SG386 driver | Hardware-tested | High | Basic VISA control; switch handles fast gating |
| Swabian driver/sequences | Hardware-tested / Experimental | High | Single-shot and persistent states; timing quirks remain |
| E3631A/E3632A supplies | Hardware-tested | Medium-High | Real addresses/config and tests; open-loop field semantics |
| Magnet abstraction/manual GUI | Working | High | Three axes plus global polarity |
| Live camera view | Working | High | Threaded stream plus GUI polling |
| Acquisition state/ROI | Working | High | Centralized and tested |
| Camera ownership | Working | High | Tested, but cross-thread wait risk |
| ODMR/ESR | Hardware-tested / Experimental | High | Full acquisition, plotting, fitting, saving |
| Zero-field scan | Hardware-tested / Experimental | High | Full acquisition, imaging, automatic HDF5 persistence |
| Zero-field physical feature extraction | Partial | High | Descriptive fluorescence analysis only |
| Multi-scan averaging | Working | High | In-run and offline memory-conscious paths |
| ImageCube HDF5 | Working | High | Streaming, partial-state, lazy planes |
| ODMR simulation | Working | High | Synthetic Lorentzian response |
| Zero-field simulation | Simulation-only | High | Workflow/UI model, not validated field physics |
| Pulse preview | Working | High | Visualization only |
| Pixel Inspector/Line Profile | Working | High | Operates on displayed cached image |
| Rabi/Ramsey/T1/T2 | Planned | High | Disabled placeholders only |
| Confocal | Planned/absent | High | No code |
| Event camera/SmarAct/NI-DAQ | Planned/config-only | High | No drivers or active imports |
| Automated tests | Working in intended environment | Medium | Could not run here because pytest is absent |
| Configuration validation | Partial | High | JSON loading only |

## 23. Recommended Immediate Next Steps

1. **Establish verified field-zero/readback practice.** This is the largest barrier to physical zero-field work. Add a measured-field/calibration workflow or, minimally, systematic supply readback and calibration provenance to `hardware/magnet/`, `hardware/power_supply/`, `experiments/zero_field_experiment.py`, and saved metadata. Risk to existing behavior: medium; keep commanded-field behavior as an explicit fallback.
2. **Define and run a zero-field physics validation protocol.** Use the existing streamed cubes and offline tools to specify controls, repeatability, expected observable, ROI choice, drift checks, ±B symmetry, and acceptance criteria. Likely files: `analysis/zero_field_averaging.py`, a focused new analysis module/test, and experiment metadata. Risk: low if acquisition remains unchanged.
3. **Add magnet ownership/interlock and bounded cancellation.** Prevent the manual window from changing fields during a zero-field run, and add timeouts/status around GUI timer handshakes and long device waits. Likely files: `framework/` ownership utilities, `gui/magnet_window.py`, `gui/zero_field_window.py`, workers, and drivers. Risk: medium because shutdown/thread timing is sensitive.
4. **Make run metadata self-contained.** Embed Git revision, setup snapshot, dependency/SDK/device identification, requested and actual settings, timing-health summary, and calibration identifiers in both ODMR and zero-field outputs. Likely files: `data/data_manager.py`, `framework/image_cube.py`, both workers/experiments, and HardwareManager. Risk: low to medium; preserve backward-compatible loading.
5. **Formalize configuration validation without redesigning device injection.** Validate types, channel uniqueness, addresses, calibration ranges, and safe timing minima, while explicitly marking unused planned blocks. Likely files: `config/config_manager.py`, `config/setupInfo.json`, GUI parameter collection, and tests. Risk: medium because previously tolerated configurations may fail early; provide precise diagnostics.

Adding Rabi/Ramsey/T1/T2 should follow, not precede, confidence in acquisition timing, metadata, and hardware interlocks needed for reproducible experimental physics.

## 24. Questions for the Project Owner

- Which exact ODMR and zero-field sequences have been physically reproduced, on what sample, and with what acceptance criteria?
- Is an independent Hall probe/gaussmeter available, and what is the provenance/uncertainty of each coil's `magnetic_field_ratio` and offset?
- Are non-swept axes normally meant to preserve bias or be forced to zero for the intended zero-field measurement?
- What physical feature is expected in the present zero-field fluorescence-only sweep, and is microwave excitation intentionally absent?
- Are the untracked `rick_null_*.csv` current experimental data and should they become formal fixtures/analysis inputs?
- Which hardware diagnostic scripts remain operationally useful versus historical bring-up records?
- Is the checked-in `setupInfo.json` expected to be machine-local, and should device addresses/calibrations be separated from shared defaults?
- Should zero-field per-point repeat frames be retained for uncertainty estimation, or is storage volume prohibitive?
- Is the global polarity relay safe to switch under every supply/output state used by scans?
- Which Python version and vendor SDK versions constitute the supported laboratory environment?

## AI Handoff Summary

NV Control Framework is a PyQt6 wide-field NV laboratory application. The current integrated entry point is `main.py`; use `--sim` to replace every device without editing configuration. The active architecture is GUI window → Qt worker thread → injected experiment object → duck-typed hardware driver. `HardwareManager` constructs an SRS SG386, Swabian Pulse Streamer, Andor Neo through `andor3`, and a three-axis Helmholtz magnet using E3631A/E3632A serial SCPI supplies. Real-hardware operation is the default; repository documentation and recent commits record real ODMR and zero-field use.

Only ODMR/ESR and zero-field field-sweep imaging are implemented. ODMR frequency-tunes the SG386, gates MW with Pulse Streamer channel 2, fires single-shot OFF/ON camera sequences, averages fluorescence, optionally subtracts a camera baseline, plots raw or normalized results, fits single/double Lorentzians, and saves scalar arrays/CSV/config. Zero Field sweeps one magnet axis, optionally zeros the other axes, uses one held-open software-triggered camera session, averages frames per point and scans per run, displays fluorescence/images, and automatically streams crash-identifiable HDF5 cubes. Pixel Inspector and Line Profile operate on the displayed image. Rabi, Ramsey, T1, T2, confocal, event-camera, SmarAct, Keithley, IDS/uEye, and lock-in functions are absent or placeholders.

Simulation has current camera, microwave, pulse-streamer, and supply implementations. Simulated ODMR produces a synthetic Lorentzian dip. Simulated zero-field validates plumbing and GUI features but has no validated magnet-dependent NV physics. Do not treat simulation as hardware validation.

The most recent work hardened real acquisition: held-open camera modes, copied/requeued Andor buffers, single-shot pulse firing, persistent relay state, MW switch gating, one exposure source of truth, HDF5 incremental/partial-scan integrity, multi-scan averaging, and timing diagnostics/defaults based on measured stalls. Known safe ODMR defaults are motivated by observed stalls below roughly 20 ms trigger delay and 10 ms fire delay.

The biggest unresolved scientific issue is that magnetic field is commanded open-loop and recorded from cached/calibrated state, not independently measured; current zero-field analysis is descriptive fluorescence-versus-field rather than extraction of a demonstrated physical zero-field feature. Major software risks are incomplete reproducibility metadata, blocking/cooperative cancellation, manual magnet commands racing scans, raw JSON configuration without validation, and real/simulation divergence. The best immediate task is to establish verified field-zero/readback and calibration metadata, then define a controlled zero-field validation protocol using the existing streamed data path.

Inspect these files first for deeper work: `CLAUDE.md`, `main.py`, `gui/main_window.py`, `hardware/hardware_manager.py`, `experiments/odmr_experiment.py`, `experiments/zero_field_experiment.py`, `gui/odmr_worker.py`, `gui/zero_field_worker.py`, `hardware/camera/andor_neo_andor3.py`, `hardware/magnet/magnet.py`, `framework/camera_ownership.py`, `framework/image_cube.py`, `analysis/zero_field_averaging.py`, `config/setupInfo.json`, and `tests/conftest.py`. Avoid building on `controller/experiment_runner.py`, `hardware/camera/andor_neo.py`, `hardware/sim_hardware.py`, or the unused `PulsedExperiment` stub without first deciding whether to revive them.
