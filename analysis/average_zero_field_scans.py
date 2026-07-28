"""Stream-average matching Zero Field ImageCube scan files.

The implementation processes one field plane at a time so it never holds
multiple ImageCubes, or even a complete ImageCube, in memory.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import numpy as np


def _open_data_member(path):
    archive = zipfile.ZipFile(path)
    member = archive.open("data.npy")
    version = np.lib.format.read_magic(member)
    if version != (1, 0):
        raise ValueError(f"Unsupported NPY version {version} in {path.name}.")
    shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(member)
    if fortran_order or len(shape) != 3:
        raise ValueError(f"Expected a C-contiguous 3D data cube in {path.name}.")
    return archive, member, tuple(shape), np.dtype(dtype)


def _npy_bytes(value):
    buffer = io.BytesIO()
    np.save(buffer, value, allow_pickle=True)
    return buffer.getvalue()


def average_scans(scan_paths, output_path, report_path):
    """Average *scan_paths* incrementally and write the cube and ROI report."""
    scan_paths = [Path(path) for path in scan_paths]
    if not scan_paths:
        raise ValueError("No scan files were supplied.")

    sources = [_open_data_member(path) for path in scan_paths]
    archives, members, shape, dtype = zip(*sources)
    try:
        if any(current_shape != shape[0] or current_dtype != dtype[0]
               for current_shape, current_dtype in zip(shape, dtype)):
            raise ValueError("All scan cubes must have identical shapes and dtypes.")
        shape, dtype = shape[0], dtype[0]
        if dtype != np.dtype("float64"):
            raise ValueError(f"Expected float64 scan data, received {dtype}.")

        with zipfile.ZipFile(scan_paths[0]) as first_archive:
            metadata = np.load(
                io.BytesIO(first_archive.read("metadata.npy")), allow_pickle=True
            ).item()
            axes = first_archive.read("axes.npy")
            experiment_type = first_archive.read("experiment_type.npy")
            scan_axis_name = first_archive.read("scan_axis_name.npy")
            scan_axis_unit = first_archive.read("scan_axis_unit.npy")
            scan_axis_values = np.load(
                io.BytesIO(first_archive.read("scan_axis_values.npy")),
                allow_pickle=True,
            )

        metadata = dict(metadata)
        metadata.update(
            {
                "averaging_enabled": True,
                "num_scans": len(scan_paths),
                "completed_scans": len(scan_paths),
                "raw_scan_filenames": [path.name for path in scan_paths],
            }
        )
        roi = metadata.get("roi")
        if roi is None:
            raise ValueError("The source scans do not contain an ROI in metadata.")
        x0, y0, x1, y1 = roi

        partial_path = output_path.with_suffix(output_path.suffix + ".partial")
        plane_shape = shape[1:]
        plane_bytes = int(np.prod(plane_shape)) * dtype.itemsize
        roi_means = []
        print(f"Averaging {len(scan_paths)} scans with one {plane_shape} plane in memory.")

        with zipfile.ZipFile(
            partial_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as output_archive:
            with output_archive.open("data.npy", "w") as output_data:
                np.lib.format.write_array_header_1_0(
                    output_data,
                    {"descr": dtype.str, "fortran_order": False, "shape": shape},
                )
                for point in range(shape[0]):
                    average = None
                    for scan_index, member in enumerate(members, start=1):
                        payload = member.read(plane_bytes)
                        if len(payload) != plane_bytes:
                            raise ValueError(
                                f"Unexpected end of data in scan {scan_index}, "
                                f"field point {point}."
                            )
                        frame = np.frombuffer(payload, dtype=dtype).reshape(plane_shape)
                        if average is None:
                            average = np.array(frame, dtype=np.float64, copy=True)
                        else:
                            average += (frame - average) / scan_index
                    roi_means.append(float(np.mean(average[y0:y1, x0:x1])))
                    output_data.write(average.tobytes(order="C"))
                    if (point + 1) % 10 == 0 or point + 1 == shape[0]:
                        print(f"Averaged field point {point + 1}/{shape[0]}.")

            output_archive.writestr("axes.npy", axes)
            output_archive.writestr("metadata.npy", _npy_bytes(metadata))
            output_archive.writestr("experiment_type.npy", experiment_type)
            output_archive.writestr("scan_axis_name.npy", scan_axis_name)
            output_archive.writestr("scan_axis_unit.npy", scan_axis_unit)
            output_archive.writestr("scan_axis_values.npy", _npy_bytes(scan_axis_values))

        partial_path.replace(output_path)
    finally:
        for member in members:
            member.close()
        for archive in archives:
            archive.close()

    signals = np.asarray(roi_means)
    lines = [
        "ZERO FIELD 15-SCAN AVERAGING REPORT",
        "=" * 36,
        f"Average file: {output_path.name}",
        f"Source scans: {len(scan_paths)}",
        f"Data shape: {shape}",
        f"ROI used for fluorescence calculation: ({x0}, {y0}, {x1}, {y1})",
        f"Field axis: {metadata['scan_parameters']['field_axis']}",
        f"Field range: {scan_axis_values[0]:.6g} to {scan_axis_values[-1]:.6g} G",
        f"Mean fluorescence range: {signals.min():.6f} to {signals.max():.6f} counts/pixel",
        f"Minimum fluorescence: {signals.min():.6f} counts/pixel at {scan_axis_values[signals.argmin()]:.6g} G",
        f"Maximum fluorescence: {signals.max():.6f} counts/pixel at {scan_axis_values[signals.argmax()]:.6g} G",
        "",
        "Field (G)\tMean fluorescence (counts/pixel)",
    ]
    lines.extend(
        f"{field:.8g}\t{signal:.10g}"
        for field, signal in zip(scan_axis_values, signals)
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Average ImageCube saved: {output_path}")
    print(f"Report saved: {report_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pattern", type=str)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    average_scans(sorted(Path().glob(args.pattern)), args.output, args.report)


if __name__ == "__main__":
    main()
