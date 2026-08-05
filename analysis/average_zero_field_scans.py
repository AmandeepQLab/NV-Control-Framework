"""CLI: stream-average matching Zero Field ImageCube scan files.

The averaging implementation lives in zero_field_averaging.py, shared with
average_zero_field_planes.py and package_zero_field_average.py. This wrapper
keeps the exact CLI surface run_zero_field_15_scan_average.cmd depends on.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from zero_field_averaging import average_scans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern", type=str)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    average_scans(sorted(Path().glob(args.pattern)), args.output, args.report)


if __name__ == "__main__":
    main()
