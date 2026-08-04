# NV Control Framework

Python/PyQt6 control software for nitrogen-vacancy (NV) center quantum sensing and wide-field magnetic imaging experiments in diamond.

Current development runs real ODMR (Optically Detected Magnetic Resonance) measurements and zero-field magnetometry scans against lab hardware. The long-term goal is a hardware-independent framework for Rabi, Ramsey, T1, and T2 measurements as well — see [Current status](#current-status) for what actually exists today versus what's planned.

## Hardware

| Role | Device | Interface |
|---|---|---|
| Microwave source | Stanford Research Systems SG386 | VISA over TCP/IP |
| Pulse generator | Swabian Pulse Streamer 8/2 | vendor `pulsestreamer` SDK |
| Camera | Andor Neo 5.5 sCMOS | `andor3` SDK3 wrapper |
| Electromagnet | Helmholtz coils, 3-axis | Keysight/HP E3631A/E3632A power supplies over serial (SCPI) |

Pulse Streamer channel map (`config/setupInfo.json`, `pulseGenerator.channelNames`/`channelValues`):

| Signal | Channel |
|---|---|
| greenLaser | 1 |
| MW | 2 |
| flipMF (magnet polarity relay) | 3 |
| detector | 5 |
| detector2 | 6 |

Analog: `aomPower` on channel 1.

## Current status

**Implemented and running against real hardware:**
- ODMR frequency-sweep acquisition, live plotting, Lorentzian fitting, pulse-sequence preview.
- Zero-field magnetometry: sweep one Helmholtz axis, per-point frame averaging, optional multi-scan averaging with raw-scan persistence, live image + fluorescence plot.
- Manual Helmholtz magnet control (enable/disable, per-axis field, global polarity) from its own window.
- Live camera view with ROI/exposure/binning control.
- A `--sim` launch mode (see [Running](#running)) that drives fully simulated hardware instead — see `CLAUDE.md` if you're modifying how devices are selected.

**Not implemented — GUI placeholders or config stubs only, no working code:**
- Rabi, Ramsey, T1, T2 experiments — four disabled buttons in the main window, no experiment classes exist.
- SmarAct XYZ positioning stages, an Event Camera, and a C4 Lock-In Camera — mentioned only as planned integrations; no driver code exists for any of them.

## Installation

```bash
git clone <repo>
cd nv_control
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`; Git Bash: `source .venv/Scripts/activate`), then:

```bash
pip install -r requirements.txt
```

`requirements.txt` covers the pure-Python and PyPI dependencies (PyQt6, pyqtgraph, numpy, scipy, PyVISA, pytest, etc.). Three additional packages are required to run against real hardware and are **not** in `requirements.txt`:

- `pyserial` (imported as `serial`) — used by the Helmholtz power-supply drivers. Installable from PyPI (`pip install pyserial`).
- `andor3` — the Andor SDK3 Python wrapper used by the camera driver. Vendor-provided, not on PyPI.
- `pulsestreamer` — Swabian Instruments' official SDK for the Pulse Streamer. Vendor-provided, not on PyPI.

None of the three are needed to run in `--sim` mode.

## Running

```bash
python main.py
```

Launches against real hardware, using `config/setupInfo.json` to select and connect every device (microwave, camera, pulse streamer, magnet power supplies).

```bash
python main.py --sim
```

Launches with every device replaced by its simulated equivalent — no real hardware is contacted, and `config/setupInfo.json` is not modified. Useful for development away from the lab; a simulated ODMR sweep produces a real Lorentzian dip against the simulated microwave/camera pair.

## Configuration

Hardware is configured in `config/setupInfo.json` — device addresses, channel mappings, and per-device types (e.g. which power supply model is on which axis). Device type selection follows one consistent pattern across every category (camera, microwave, pulse streamer, power supply): a `type` field per device, with `"sim"` accepted everywhere as an alternative to the real type name. See `CLAUDE.md` for exactly how `hardware/hardware_manager.py` reads this.

## Documentation

- **`CLAUDE.md`** — architecture, the real call chain, hardware-selection mechanism, dead code to avoid building on, and testing safety rules. Read this before making structural changes.
- **`docs/`** — dated technical audits of specific subsystems (currently: the zero-field imaging physics/logic audit and a camera-display data-flow trace), kept separate from the two documents above because they're narrow investigations, not general project documentation.
