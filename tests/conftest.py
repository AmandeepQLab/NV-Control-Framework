"""Hardware-safety gate for pytest collection.

Most files in this directory are ad-hoc scripts that command real lab
hardware (Andor camera via SDK3, SG386 over VISA, Swabian pulse streamer,
Helmholtz E3631A/E3632A power supplies) unconditionally at module level --
merely importing them fires the hardware, whether or not pytest finds a
test function inside. By default, only the known-safe allowlist below is
collected. Pass --hardware to lift the restriction.
"""

import os
import pathlib

TESTS_DIR = pathlib.Path(__file__).parent.resolve()

# Every window confirms before closing (gui/close_confirmation.py). No test
# may block on that dialog: for the whole session it is answered "close" --
# through this environment variable, so the app started in a subprocess by
# a test inherits it, and non-GUI runs need not import Qt here. A test that
# wants Cancel, or to see the question, uses
# gui.close_confirmation.auto_answer(False/True) (it takes precedence) and
# reads gui.close_confirmation.history.
CLOSE_CONFIRMATION_AUTO_ANSWER_ENV = "NV_CONTROL_AUTO_CONFIRM_CLOSE"
os.environ.setdefault(CLOSE_CONFIRMATION_AUTO_ANSWER_ENV, "yes")

# Tests never write into data/ or the real logs/: session logs and the
# camera diagnostics log go to one temporary directory for the whole run
# (inherited by app subprocesses started by tests).
import tempfile  # noqa: E402

_TEST_LOG_DIR = pathlib.Path(tempfile.mkdtemp(prefix="nv_control_test_logs_"))
os.environ["NV_CONTROL_LOG_DIR"] = str(_TEST_LOG_DIR)
os.environ["NV_CONTROL_CAMERA_DIAGNOSTICS_LOG"] = str(_TEST_LOG_DIR / "camera_diagnostics.log")

# ... and that is checked: an audit hook records every file this test
# process opens for writing, and every directory it creates, inside the
# repo's data/ or logs/ (the live measurement may write to data/ at the same
# time, so the directory contents themselves can't be compared).
# pytest_sessionfinish fails the run if anything was recorded. Writes made
# by C libraries (h5py) or by app subprocesses don't pass through Python's
# open(); those rely on the routing above.
import sys  # noqa: E402

_REPO_DIR = TESTS_DIR.parent
_DATA_DIR = os.path.normcase(str(_REPO_DIR / "data"))
_LOGS_DIR = os.path.normcase(str(_REPO_DIR / "logs"))
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
GUARDED_WRITES = []


def guarded_write(event, args):
    """The path if (event, args) writes inside data/ or logs/, else None.
    Inside data/ only (opening data/ itself for listing is fine); logs/ and
    anything in it."""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if path is None or isinstance(path, int):
            return None
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
    elif event == "os.mkdir":
        path, writing = args[0], True
    else:
        return None
    if not writing:
        return None
    try:
        full = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except Exception:
        return None
    if full.startswith(_DATA_DIR + os.sep):
        return full
    if full == _LOGS_DIR or full.startswith(_LOGS_DIR + os.sep):
        return full
    return None


def _audit(event, args):
    if event not in ("open", "os.mkdir"):
        return
    path = guarded_write(event, args)
    if path is not None:
        GUARDED_WRITES.append((event, path))


sys.addaudithook(_audit)

ALLOWED_FILES = frozenset(
    {
        "test_acquisition_state.py",
        "test_andor_binning.py",
        "test_app_exit.py",
        "test_baseline_correction.py",
        "test_camera_ownership.py",
        "test_colormap_selectors.py",
        "test_odmr_acquisition_roi.py",
        "test_odmr_analysis_gui.py",
        "test_odmr_analysis_fit_options.py",
        "test_exposure_propagation.py",
        "test_image_cube_preallocation.py",
        "test_main_window_close_safety.py",
        "test_magnet_scpi_traffic.py",
        "test_magnet_experiment_lock.py",
        "test_magnet_independent_polarity.py",
        "test_magnetic_field_gui_units.py",
        "test_mw_switch_gating.py",
        "test_odmr_external_acquisition.py",
        "test_odmr_fit.py",
        "test_odmr_preliminary_fit_gui.py",
        "test_odmr_magnetic.py",
        "test_odmr_pixel_statistics.py",
        "test_odmr_pixel_fit.py",
        "test_odmr_statistics.py",
        "test_odmr_statistics_gui.py",
        "test_odmr_time_estimate.py",
        "test_paths.py",
        "test_pulse_streamer_single_shot.py",
        "test_roi.py",
        "test_streaming_controller.py",
        "test_zero_field_analysis.py",
        "test_zero_field_averaging.py",
        "test_zero_field_bidirectional.py",
        "test_zero_field_reference.py",
        "test_zero_field_queue.py",
        "test_zero_field_vector.py",
        "test_odmr_window_layout.py",
        "test_odmr_magnet_field.py",
        "test_collapsible_section.py",
        "test_odmr_running_average.py",
        "test_main_window_layout.py",
        "test_count_statistics.py",
        "test_stability_check.py",
        "test_zero_field_averaging_module.py",
        "test_zero_field_software_acquisition.py",
        "test_zero_field_relay_flips.py",
        "test_zero_field_model_fields.py",
        "test_zero_field_timing.py",
        "test_zero_field_window_save_toggle.py",
        "test_zero_field_queue_dock.py",
        "test_gui_robustness.py",
        "test_close_confirmation.py",
        "test_microwave_state.py",
        "test_logging_setup.py",
        "test_zero_field_saved_images.py",
    }
)

# Never gated: not test modules, and ignoring them would break normal
# pytest/package machinery rather than protect anything.
ALWAYS_KEEP = frozenset({"__init__.py", "conftest.py"})


def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help=(
            "Lift the hardware-safety allowlist and collect every file in "
            "tests/, including ad-hoc scripts that command real lab "
            "hardware merely by being imported."
        ),
    )


def _restricted(config) -> bool:
    return not config.getoption("--hardware")


def pytest_ignore_collect(collection_path: pathlib.Path, config):
    if not _restricted(config):
        return False

    if collection_path.is_dir():
        return False

    if collection_path.parent.resolve() != TESTS_DIR:
        # Only gate files directly inside tests/; leave everything else
        # (e.g. this file's own directory listing during rootdir checks)
        # untouched.
        return False

    name = collection_path.name
    if name in ALWAYS_KEEP:
        return False

    if collection_path.suffix != ".py":
        return False

    return name not in ALLOWED_FILES


def pytest_sessionfinish(session, exitstatus):
    """Empty pyqtgraph's view registry before Qt tears the widgets down.

    Widgets kept alive by the GUI tests are destroyed at interpreter exit,
    after this hook. Without this, a named ViewBox's destroyed signal can
    rebuild menus whose QComboBoxes are already deleted and print a
    RuntimeError after the summary (see gui/pyqtgraph_teardown.py). Only
    done if a test imported pyqtgraph, so non-GUI runs never import Qt.
    """
    if "pyqtgraph" in sys.modules:
        from gui.pyqtgraph_teardown import clear_view_registry

        clear_view_registry()

    # No test may write into data/ or logs/ (see guarded_write()).
    if GUARDED_WRITES:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = [f"  {event}: {path}" for event, path in dict.fromkeys(GUARDED_WRITES)]
        message = ("Tests wrote into data/ or logs/ (route them to a temporary "
                   "directory):\n" + "\n".join(lines))
        if reporter is not None:
            reporter.write_line(message, red=True)
        else:
            print(message)
        session.exitstatus = 1


def pytest_report_header(config, start_path):
    if not _restricted(config):
        return None

    excluded = sorted(
        p.name
        for p in TESTS_DIR.glob("*.py")
        if p.name not in ALLOWED_FILES and p.name not in ALWAYS_KEEP
    )
    return (
        f"hardware-safety: {len(excluded)} file(s) in tests/ excluded from "
        f"collection (only {len(ALLOWED_FILES)} known-safe test file(s) "
        f"collected) -- pass --hardware to opt in and collect everything"
    )
