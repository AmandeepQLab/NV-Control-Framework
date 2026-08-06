"""Hardware-safety gate for pytest collection.

Most files in this directory are ad-hoc scripts that command real lab
hardware (Andor camera via SDK3, SG386 over VISA, Swabian pulse streamer,
Helmholtz E3631A/E3632A power supplies) unconditionally at module level --
merely importing them fires the hardware, whether or not pytest finds a
test function inside. By default, only the known-safe allowlist below is
collected. Pass --hardware to lift the restriction.
"""

import pathlib

TESTS_DIR = pathlib.Path(__file__).parent.resolve()

ALLOWED_FILES = frozenset(
    {
        "test_acquisition_state.py",
        "test_camera_ownership.py",
        "test_odmr_acquisition_roi.py",
        "test_odmr_external_acquisition.py",
        "test_paths.py",
        "test_roi.py",
        "test_streaming_controller.py",
        "test_zero_field_analysis.py",
        "test_zero_field_averaging.py",
        "test_zero_field_averaging_module.py",
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
