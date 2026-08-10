# CLAUDE.md

Assistant-facing guide to this repository. Last verified against the code: 2026-08-10.

This file documents how the code actually works, not how it was originally intended to work — every claim below was checked against the current source, not carried forward from comments, commit messages, or prior documentation. If you change something this file describes, update this file in the same change.

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
- `gui/magnet_window.py` (`MagnetControlWindow`) — manual coil jogging has no "experiment" concept to route through, so it imports `hardware.magnet.magnet.POSITIVE`/`NEGATIVE` and calls `Magnet`/`MagnetAxis` methods directly.
- `gui/main_window.py`'s live view — `self.camera.get_latest_frame()`/`stop_stream()` are called directly on the driver object for the live preview, since there's no experiment wrapping a live display either. ROI/exposure/binning changes for live view *do* go through `framework.acquisition_state.AcquisitionState` and `framework.camera_ownership`, not raw SDK calls.

**Invariant: experiments never import a concrete `hardware.*` driver class.** `experiments/odmr_experiment.py` and `experiments/zero_field_experiment.py` only call methods on hardware objects handed to them through their constructors (a `hardware` dict for ODMR; explicit `camera`/`magnet` args for Zero Field). Neither has an `import hardware.camera...` or `import hardware.microwave...` line. If you're adding experiment logic and find yourself importing a concrete driver, that's a sign the object should be injected instead — this is what keeps `--sim` and real hardware interchangeable without touching experiment code.

**Camera contention is handled, not ignored.** Any code acquiring images through an experiment must hold `framework.camera_ownership.exclusive_camera_access(camera)` around the whole acquisition (both `ODMRExperiment.run()` and `ZeroFieldExperiment.run()` already do this). It suspends the registered live-view `QTimer` and stops streaming for the duration, then restores both — including on exception. `gui/main_window.py` registers itself via `register_live_view_controller()`/`register_camera_state_restorer()` at startup; don't add a second, unregistered code path that calls `camera.snap()` or `start_stream()` directly.

**MW on/off is gated by a fast switch, not by changing SG386 amplitude.** `ODMRExperiment` gates MW via the ZASWA-2-50DRA+ switch on Pulse Streamer channel 2 (`build_sequence()`'s `mw_on` argument), not by writing the SG386's power per frame — the SG386 stays at one power for the whole scan point. Measured at the rig: the switch's off-state isolation is better than the SG386's own at −100 dBm, and switching to it improved measured ODMR contrast by ~10%. **Do not reintroduce per-frame `set_power()` calls** to gate MW — that regime was deliberately removed (see `experiments/odmr_experiment.py::acquire_frame()`).

**Camera acquisition is held open across a whole ODMR scan, not re-armed per frame.** `ODMRExperiment.begin_camera_acquisition()`/`end_camera_acquisition()` arm/disarm the camera once per scan; `acquire_triggered_frame()` then just waits on the already-armed camera (`grab_external_frame()`) instead of each frame doing its own arm/disarm. The timing-diagnostics CSV header records `acquisition_start_count` specifically so a silent regression back to per-frame arming is visible — it should read 1 per scan; a value equal to the frame count means the held-open path silently fell back.

**Pulse sequences fire single-shot, and persistent digital outputs (e.g. the magnet polarity relay) survive both firing and reset.** `SwabianPulseStreamer.run()` is called with `n_runs=1` for triggered frames so exactly one gate fires per frame — looping sequences (the vendor SDK's own default) previously let the camera capture stray repetitions. `run()`'s `final` state defaults to the *current* `persistent_outputs` dict, not the SDK's all-zero default, because a finite `n_runs` reaching the end of a sequence would otherwise zero every digital channel including the magnet polarity relay's channel regardless of what it was actually commanded to. `reset_outputs()` mirrors this: by default it holds each channel at its `persistent_outputs` value rather than forcing LOW, so it can safely run once per triggered frame without dropping the relay. Only `close()` (`preserve_persistent=False`) actually de-energizes everything, which is correct only at shutdown.

**Exposure has a single source of truth: the Main Window spinbox.** `MainWindow.acquisition_state` is built from `self.exposure_spin.value()` (not `AcquisitionState`'s own dataclass default) and pushed to the camera once, explicitly, at the end of `__init__` — before any user interaction or experiment can run. `AndorNeoAndor3.set_exposure()`/`configure_camera()` then store what `cam.getFloat("ExposureTime")` reads back from the SDK, not the requested value. Before this, four independent exposure defaults existed (the `AcquisitionState` dataclass default, `AndorNeoAndor3`'s own `__init__` default, `SimCamera`'s `__init__` default, and the spinbox's initial value) and nothing pushed the GUI's displayed value to the camera until the user touched the spinbox — a scan run immediately after launch could silently use whatever the driver's own default was, while the GUI displayed something else entirely.

## Hardware device selection

`hardware/hardware_manager.py::HardwareManager.initialize()` builds camera, microwave, and pulse-streamer objects by reading a `type` field from `config/setupInfo.json` (`spcm.type`, `frequencyGenerators[0].type`, `pulseGenerator.type`), mirroring the pattern power supplies already used (`Helmholtz.{X,Y,Z}.type`, dispatched through `create_power_supply()`). Every category accepts `"sim"` as a value alongside its real type name, dispatching to the matching `Sim*` class. Unrecognized type values raise `ValueError` — there is no silent fallback either direction.

`HardwareManager(config_manager, force_sim=False)` — passing `force_sim=True` overrides every device to its simulated class in memory, without touching `config/setupInfo.json`. The app's `--sim` CLI flag (`gui/main_window.py::main()`, parsed with `argparse.parse_known_args()` so Qt's own CLI args are untouched) sets this. Default behavior (`force_sim=False`, no `--sim`) is byte-identical to before this mechanism existed — same classes, same order, same `.connect()` calls.

Simulated classes have full method parity with their real counterparts, not just the methods currently called: `hardware/camera/sim_camera.py::SimCamera`, `hardware/microwave/sim_microwave.py::SimMicrowave`, `hardware/pulse_streamer/sim_pulse_streamer.py::SimPulseStreamer`, `hardware/power_supply/sim_power_supply.py::SimPowerSupply`. `SimCamera` can optionally be wired to the constructed microwave object (`SimCamera(microwave=...)`) so a simulated ODMR sweep produces a real Lorentzian dip — `HardwareManager` does this automatically in sim mode.

## Dead code — do not build on these

Confirmed unused by the running application; each is either an abandoned duplicate or a stub with no subclasses:

- `controller/experiment_runner.py` — only referenced by `tests/test_odmr.py`.
- `hardware/camera/andor_neo.py` (`AndorNeo`, pylablib-based) — a second, older Andor driver. `HardwareManager` only ever constructs `AndorNeoAndor3` (`hardware/camera/andor_neo_andor3.py`).
- `hardware/sim_hardware.py` (`SimMicrowave`, `SimPulseStreamer`, `build_sim_hardware()`) — an earlier, less complete simulator module. `HardwareManager` now wires `hardware/microwave/sim_microwave.py` and `hardware/pulse_streamer/sim_pulse_streamer.py` instead (co-located with their real counterparts, matching the rest of the package layout). This file was deliberately left alone rather than deleted or refactored — treat it as legacy, not a second implementation to keep in sync.
- `experiments/pulsed_experiment.py` (`PulsedExperiment`) — abstract stub, no subclasses anywhere in the repo. `ODMRExperiment` builds its own pulse sequences without inheriting from it.
- `hardware/camera/base_camera.py` (`BaseCamera` ABC) — no camera class actually subclasses it; `AndorNeo`, `AndorNeoAndor3`, and `SimCamera` are independently duck-typed.

## Not implemented — disabled placeholders only

Rabi, Ramsey, T1, and T2 experiments do not exist as code. `gui/main_window.py` has four buttons for them, all `.setEnabled(False)` with no click handler wired up. One of them is misspelled in the source — the button is labeled/named "Ramsy", not "Ramsey". **Do not silently fix this spelling** if you're working nearby; it's recorded here deliberately, not accidentally missed. SmarAct XYZ stages, an Event Camera, and a C4 Lock-In Camera are documented-as-planned only — no driver code exists for any of them (a `stages`/`MCS2` block and a malformed orphaned `"EventCamera"` fragment exist in `config/setupInfo.json`, but nothing reads either key).

## Known open issues (Zero Field experiment)

See `docs/zero_field_audit_2026-07.md` for the full physics/logic audit with per-finding status. These are **known and deliberate-for-now, not bugs to spontaneously fix** if you're working in `experiments/zero_field_experiment.py`:

- Non-swept magnet axes are preserved from whatever was configured before a scan started, not forced to zero — a documented design tradeoff, not an oversight (audit §3, §10.2–10.3).
- Stop is cooperative: an in-flight settling sleep or camera acquisition is not interrupted, only the point loop's next iteration boundary checks the stop flag (audit §10.8).
- No magnetic-field or camera-exposure hardware readback exists anywhere in `hardware/magnet/` or `hardware/power_supply/` — settling is open-loop, and recorded exposure/binning metadata reflects the driver's own cached value, not an independent SDK query (audit §4, §8).
- Per-point repeat frames (the `averages` loop inside `acquire_frame()`) are discarded after averaging — only the mean is kept. This is separate from the multi-*scan* raw-saving feature (`save_raw_scans`), which persists whole sweeps, not individual repeats (audit §10.9).

Camera-contention and ROI-validation findings from the same audit are fixed — see "Architecture and call chain" above.

## Known hardware quirks (ODMR timing)

**`trigger_delay_s` below ~0.02s triggers a reproducible ~400ms per-frame stall, cause unknown.** Confirmed from `data/odmr_timing_*.csv` timing-diagnostics recordings: at `trigger_delay_s = 0.0`, roughly 1 frame in 9 (10.4-10.8% in two independent recorded runs) takes ~400-430ms instead of the normal ~90ms — a distinct second mode in the `repeat_total` distribution, not scattered jitter. At `trigger_delay_s >= 0.02s` the second mode disappears entirely (tightest recorded spread: ~130-133ms, no bimodality). This has not been root-caused — candidates include the Andor SDK3 buffer/trigger-arming not having settled from the previous frame, but this is speculation, not confirmed. **Do not push `trigger_delay_s` toward 0 to save time even though MW switch-gating contrast is indifferent to it** — the stall more than cancels any savings and makes scan duration unpredictable. `gui/odmr_window.py::estimate_odmr_time()` does not model this (see the comment at its `trigger_delay_s`-dependent term) — its estimate for `trigger_delay_s < 0.02s` will run 15-30% low for this reason, not because the formula itself is wrong.

**Andor Neo exposure quantises to a fixed ~3µs offset, not a proportional one.** Measured at the rig: requested 10.000ms → camera held 9.997ms (−3µs); requested 20.000ms → camera held 20.003ms (+3µs). The sensor rounds to the nearest whole row period, so the offset's *magnitude* doesn't scale with the requested exposure. This is why `ODMRExperiment._EXPOSURE_DRIFT_EPSILON_S` (the requested-vs-actual-exposure warning in `configure_acquisition()`) uses an absolute 100µs threshold rather than a relative one — a relative threshold would be the wrong shape even where it happened to pass at these two points.

**Sensor readout ≈ 7.3ms, measured at the ROI currently in use.** Derived from `camera.wait_buffer`'s duration minus the configured trigger delay and exposure; consistent across recorded runs at that ROI. Readout scales with ROI/row count on an sCMOS sensor, so this number is conditional on ROI — re-measure if the acquisition ROI changes materially.

**Camera arm + disarm ≈ 160ms total, paid once per scan.** See "Camera acquisition is held open across a whole ODMR scan" above — this is the `CAMERA_ARM_DISARM_S` constant in `gui/odmr_window.py::estimate_odmr_time()`, derived the same way from recorded timing-diagnostics data.

## Measured acquisition parameters (ODMR)

These are characterized properties of the current rig/sample, not code facts — re-measure before relying on them if the sample, ROI, or laser power changes.

**10ms and 20ms exposure gave statistically equivalent contrast (2.794% vs 2.814%) at ~20,000 counts on a bright ensemble, repeats=5.** 10ms is preferred for scan speed at this signal level. **This is conditional, not universal**: on a dimmer sample the measurement becomes photon-limited and a longer exposure would win instead. Don't treat 10ms as a correct default independent of signal level.

**Camera baseline (dark counts) ≈ 120, against ~20,000 counts of signal.** Baseline subtraction (`baseline_counts`, see `ODMRExperiment.process_frame()`) changes contrast by ~0.6% at this signal level — below the measurement floor documented below (so it's not distinguishable from noise here), but becomes material once the signal drops below roughly 1000 counts, where 120 is no longer a small correction.

**Measurement floor: contrast repeatability is ±5% relative at repeats=5.** Any change claimed to have a smaller effect than that needs repeated runs in a single session, same ROI and laser power, to be credible against this dataset's own noise — a single before/after comparison at this repeats setting cannot distinguish a real few-percent effect from run-to-run noise.

## Testing safety — read before running pytest with `--hardware`

`tests/` contains 64 files. Only 7 are real, hardware-free pytest tests (`test_acquisition_state.py`, `test_camera_ownership.py`, `test_odmr_acquisition_roi.py`, `test_roi.py`, `test_streaming_controller.py`, `test_zero_field_analysis.py`, `test_zero_field_averaging.py`). **The other ~57 are ad-hoc scripts that command real lab hardware — the Andor camera, the SG386 microwave source over VISA, the Swabian pulse streamer, the Helmholtz power supplies — unconditionally at module import time, not just inside a test function.** Several have no `if __name__ == "__main__":` guard at all: importing them fires a relay, streams a pulse, or turns on a power-supply output as a side effect of Python loading the file.

`pytest.ini` (`testpaths = tests`) plus `tests/conftest.py`'s `pytest_ignore_collect` hook gate this: a bare `pytest` run only collects the 7 safe files. The gate is an allowlist, not a denylist — anything not explicitly named in `tests/conftest.py::ALLOWED_FILES` is excluded from collection entirely, so it is never imported. Passing `--hardware` lifts the restriction and collects everything, including every hardware-commanding script.

**Never run `pytest --hardware` (or `python` on any of the ~57 gated files directly) without first confirming real instruments are powered, connected, and safe to command** — this includes verifying no one is near the magnet coils before anything that calls `magnet.enable()`/`set_vector()`, and that the camera/pulse-streamer/microwave are in a state where an unattended trigger or relay toggle is safe. `--collect-only --hardware` is **not** safe either — collection still fully imports every file, which is enough to trigger the unconditional module-level hardware calls in most of them.
