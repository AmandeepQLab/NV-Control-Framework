"""CLI: package checkpointed averaged planes and render a zero-field report.

The packaging/report implementation lives in zero_field_averaging.py, shared
with average_zero_field_scans.py and average_zero_field_planes.py. This
wrapper takes its dataset location as arguments instead of the hardcoded
"zero_field_2026-07-20_10-59-13" prefix the original script used.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from zero_field_averaging import package_averaged_planes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pattern", help="Glob pattern (relative to CWD) matching the raw scan files."
    )
    parser.add_argument("--planes-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--report-dir", type=Path, default=None,
        help="If given, render the plotted markdown report here.",
    )
    args = parser.parse_args()

    scan_paths = sorted(Path().glob(args.pattern))
    if not scan_paths:
        raise ValueError("No source scan files found.")
    package_averaged_planes(scan_paths, args.planes_dir, args.output, args.report_dir)


if __name__ == "__main__":
    main()
