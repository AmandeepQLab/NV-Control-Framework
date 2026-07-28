"""Checkpointable plane-wise averaging for large zero-field ImageCubes."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from average_zero_field_scans import _open_data_member


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
    args.planes_dir.mkdir(parents=True, exist_ok=True)
    sources = [_open_data_member(path) for path in paths]
    archives, members, shapes, dtypes = zip(*sources)
    try:
        if len(set(shapes)) != 1 or len(set(dtypes)) != 1:
            raise ValueError("Source cubes do not share a shape and dtype.")
        shape, dtype = shapes[0], dtypes[0]
        plane_shape = shape[1:]
        plane_bytes = int(np.prod(plane_shape)) * dtype.itemsize
        plane_starts = [member.tell() for member in members]
        stop = min(args.start + args.count, shape[0])
        for point in range(args.start, stop):
            plane_path = args.planes_dir / f"plane_{point:03d}.npy"
            if plane_path.exists():
                print(f"Plane {point + 1}/{shape[0]} already exists.", flush=True)
                continue
            average = None
            for index, (member, plane_start) in enumerate(
                zip(members, plane_starts), start=1
            ):
                # The NPY data start is the current position after
                # _open_data_member's header read.  ZipExtFile supports seeks
                # for these uncompressed source members.
                member.seek(plane_start + point * plane_bytes)
                frame = np.frombuffer(member.read(plane_bytes), dtype=dtype).reshape(plane_shape)
                if average is None:
                    average = np.array(frame, dtype=np.float64, copy=True)
                else:
                    average += (frame - average) / index
            np.save(plane_path, average)
            print(f"Completed plane {point + 1}/{shape[0]}.", flush=True)
    finally:
        for member in members:
            member.close()
        for archive in archives:
            archive.close()


if __name__ == "__main__":
    main()
