"""CLI: checkpointable plane-wise averaging for large zero-field ImageCubes.

The averaging implementation lives in zero_field_averaging.py, shared with
average_zero_field_scans.py and package_zero_field_average.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from zero_field_averaging import average_plane_range


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern")
    parser.add_argument("--planes-dir", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    paths = sorted(Path().glob(args.pattern))
    if not paths:
        raise ValueError("No source scan files found.")
    average_plane_range(paths, args.planes_dir, args.start, args.count)


if __name__ == "__main__":
    main()
