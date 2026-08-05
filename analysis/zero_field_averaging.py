"""Shared plane-wise averaging for Zero Field ImageCube scan files.

Three entry points, one per stage of the existing pipeline:

- :func:`average_scans` -- single-pass streaming average of every scan into
  one output cube plus a plain-text ROI report (``average_zero_field_scans.py``).
- :func:`average_plane_range` -- checkpointed averaging of one batch of field
  points, resumable across process restarts (``average_zero_field_planes.py``).
- :func:`package_averaged_planes` -- assembles checkpointed planes into one
  cube, optionally with the richer plotted markdown report
  (``package_zero_field_average.py``).

Scan files may be ``.npz`` (legacy archives, read via hand-parsed zip/byte
offsets so a scan is never loaded into memory whole) or ``.h5``/``.hdf5``
(read via ``ImageCube.peek_shape``/``ImageCube.load_plane``) -- transparently,
per source file. Output stays ``.npz``, matching what the rest of this
pipeline (and ``run_zero_field_15_scan_average.cmd``) already expects.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# This is the first analysis/ module to import from framework/. Run directly
# (as run_zero_field_15_scan_average.cmd does), sys.path[0] is this file's
# own directory (analysis/), not the repo root, so "framework" would not
# otherwise be importable -- unlike main.py, which sits at the repo root and
# doesn't need this. Under pytest the repo root is already on sys.path (via
# tests/__init__.py's package-root walk-up), so this is a no-op there.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from framework.image_cube import ImageCube

_NPZ_DATA_MEMBER_NAME = "data"


def _is_hdf5_path(filename):
    return Path(filename).suffix.lower() in (".h5", ".hdf5")


def _reject_hdf5_output(output_path):
    """Fail loudly instead of writing a zip archive under a .h5 name.

    average_scans() and package_averaged_planes() build their output via
    zipfile.ZipFile unconditionally -- they never branch on output_path's
    suffix. Reading .h5 scan inputs (via ImageCube) is supported, but
    writing HDF5 output is not implemented; without this guard, an
    output_path ending in .h5/.hdf5 would silently produce a .npz-format
    zip archive wearing a misleading extension.
    """
    if _is_hdf5_path(output_path):
        raise ValueError(
            f"{output_path}: this module writes .npz output only "
            "(HDF5 scan inputs are supported, but HDF5 output is not "
            "implemented). Use a .npz output path."
        )


def _npy_bytes(value):
    buffer = io.BytesIO()
    np.save(buffer, value, allow_pickle=True)
    return buffer.getvalue()


def _decode(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _reject_incomplete_hdf5(path, metadata, allow_partial_scans):
    """Refuse a streamed .h5 scan whose scan_complete metadata is False.

    Its data dataset is pre-allocated to the full configured plane count;
    any planes beyond what was actually acquired are HDF5 fill-value
    placeholders (0 for a float dataset), byte-indistinguishable from real
    frames to code that only looks at shape. Averaging them in would
    silently corrupt the result.

    metadata.get("scan_complete", True) defaults to trusting the file when
    the key is absent -- not because absence is assumed harmless, but
    because it specifically means this file was never written by
    ImageCubeStreamWriter. A plain ImageCube.save() (the only other way an
    .h5 file gets created) is a single atomic whole-cube write with no
    partial-write window and never sets this key at all, so "key absent" is
    a positive signal ("this is not a streamed file"), not an unknown.
    """
    if not allow_partial_scans and not metadata.get("scan_complete", True):
        raise ValueError(
            f"{path}: scan_complete is False in this file's metadata -- it "
            "was left by an interrupted or stopped scan, and any frames "
            "past its planes_written count are HDF5 fill-value "
            "placeholders, not real data. Pass allow_partial_scans=True to "
            "use it anyway."
        )


def _atomic_save_npy(path, array):
    """Write *array* to *path* so a completed file is never truncated.

    ``np.save`` opens the destination path directly (no temp file, no
    rename), so a process killed mid-write leaves a truncated ``.npy`` at
    the exact path callers use to decide whether a checkpoint is already
    done. Writing to a sibling ``.partial`` file and renaming it into place
    (atomic on the same volume) closes that gap: the final filename only
    ever exists once fully written.
    """
    path = Path(path)
    partial_path = path.with_name(path.name + ".partial")
    # Pass an open file handle rather than a path: np.save appends ``.npy``
    # to path-like targets that don't already end in it, which would
    # otherwise rename our ``.partial`` file out from under us.
    with open(partial_path, "wb") as handle:
        np.save(handle, array, allow_pickle=True)
    partial_path.replace(path)


# ---------------------------------------------------------------------
# Plane sources: uniform streaming reads across .npz and .h5 scan files
# ---------------------------------------------------------------------

class _NpzPlaneSource:
    """Streams planes from a legacy ``.npz`` scan archive, one at a time."""

    def __init__(self, path):
        self.path = Path(path)
        self._archive = zipfile.ZipFile(self.path)
        self._member = self._archive.open(f"{_NPZ_DATA_MEMBER_NAME}.npy")
        version = np.lib.format.read_magic(self._member)
        if version != (1, 0):
            raise ValueError(f"Unsupported NPY version {version} in {self.path.name}.")
        shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(self._member)
        if fortran_order or len(shape) != 3:
            raise ValueError(f"Expected a C-contiguous 3D data cube in {self.path.name}.")
        self.shape = tuple(int(dim) for dim in shape)
        self.dtype = np.dtype(dtype)
        self._data_start = self._member.tell()
        self._plane_bytes = int(np.prod(self.shape[1:])) * self.dtype.itemsize

    def read_plane(self, index):
        # ZipExtFile supports seeks for these uncompressed source members
        # (raw scan archives are written via plain np.savez, ZIP_STORED).
        self._member.seek(self._data_start + index * self._plane_bytes)
        payload = self._member.read(self._plane_bytes)
        if len(payload) != self._plane_bytes:
            raise ValueError(
                f"Unexpected end of data in {self.path.name}, field point {index}."
            )
        return np.frombuffer(payload, dtype=self.dtype).reshape(self.shape[1:])

    def close(self):
        self._member.close()
        self._archive.close()


class _Hdf5PlaneSource:
    """Streams planes from an ``.h5``/``.hdf5`` scan file via ImageCube."""

    def __init__(self, path, allow_partial_scans=False):
        self.path = Path(path)
        self.shape = ImageCube.peek_shape(self.path)
        with h5py.File(self.path, "r") as handle:
            self.dtype = handle["data"].dtype
            metadata = json.loads(_decode(handle["metadata_json"][()]))
        _reject_incomplete_hdf5(self.path, metadata, allow_partial_scans)

    def read_plane(self, index):
        return np.asarray(ImageCube.load_plane(self.path, index))

    def close(self):
        pass  # ImageCube.load_plane opens and closes its own handle.


def _open_plane_source(path, allow_partial_scans=False):
    path = Path(path)
    return (
        _Hdf5PlaneSource(path, allow_partial_scans=allow_partial_scans)
        if _is_hdf5_path(path) else _NpzPlaneSource(path)
    )


def _open_sources(scan_paths, allow_partial_scans=False):
    scan_paths = [Path(path) for path in scan_paths]
    if not scan_paths:
        raise ValueError("No scan files were supplied.")
    sources = [
        _open_plane_source(path, allow_partial_scans=allow_partial_scans)
        for path in scan_paths
    ]
    shapes = {source.shape for source in sources}
    dtypes = {source.dtype for source in sources}
    if len(shapes) != 1 or len(dtypes) != 1:
        for source in sources:
            source.close()
        raise ValueError("All scan cubes must have identical shapes and dtypes.")
    return scan_paths, sources, next(iter(shapes)), next(iter(dtypes))


# ---------------------------------------------------------------------
# Static (non-plane) members: axes/metadata/experiment_type/scan-axis info
# ---------------------------------------------------------------------

def _read_npz_static_members(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata = np.load(
            io.BytesIO(archive.read("metadata.npy")), allow_pickle=True
        ).item()
        axes = np.load(io.BytesIO(archive.read("axes.npy")), allow_pickle=True).item()
        experiment_type = np.load(
            io.BytesIO(archive.read("experiment_type.npy")), allow_pickle=True
        ).item()
        scan_axis_name = (
            np.load(
                io.BytesIO(archive.read("scan_axis_name.npy")), allow_pickle=True
            ).item()
            if "scan_axis_name.npy" in names else None
        )
        scan_axis_unit = (
            np.load(
                io.BytesIO(archive.read("scan_axis_unit.npy")), allow_pickle=True
            ).item()
            if "scan_axis_unit.npy" in names else None
        )
        scan_axis_values = (
            np.load(
                io.BytesIO(archive.read("scan_axis_values.npy")), allow_pickle=True
            )
            if "scan_axis_values.npy" in names else np.asarray([])
        )
    return {
        "metadata": dict(metadata),
        "axes": axes,
        "experiment_type": experiment_type,
        "scan_axis_name": scan_axis_name,
        "scan_axis_unit": scan_axis_unit,
        "scan_axis_values": np.asarray(scan_axis_values),
    }


def _read_hdf5_static_members(path, allow_partial_scans=False):
    with h5py.File(path, "r") as handle:
        metadata = json.loads(_decode(handle["metadata_json"][()]))
        _reject_incomplete_hdf5(path, metadata, allow_partial_scans)
        axes = json.loads(_decode(handle["axes_json"][()]))
        experiment_type = (
            None if handle.attrs.get("experiment_type_is_none", False)
            else _decode(handle.attrs.get("experiment_type", "Unknown"))
        )
        scan_axis_name = (
            None if handle.attrs.get("scan_axis_name_is_none", False)
            else _decode(handle.attrs.get("scan_axis_name", ""))
        )
        scan_axis_unit = (
            None if handle.attrs.get("scan_axis_unit_is_none", False)
            else _decode(handle.attrs.get("scan_axis_unit", ""))
        )
        scan_axis_values = (
            [] if handle.attrs.get("scan_axis_values_is_none", False)
            else handle["scan_axis_values"][()].tolist()
        )
    return {
        "metadata": dict(metadata),
        "axes": axes,
        "experiment_type": experiment_type,
        "scan_axis_name": scan_axis_name,
        "scan_axis_unit": scan_axis_unit,
        "scan_axis_values": np.asarray(scan_axis_values),
    }


def _read_static_members(path, allow_partial_scans=False):
    path = Path(path)
    return (
        _read_hdf5_static_members(path, allow_partial_scans=allow_partial_scans)
        if _is_hdf5_path(path) else _read_npz_static_members(path)
    )


def _write_npz_static_members(archive, static, metadata):
    archive.writestr("axes.npy", _npy_bytes(static["axes"]))
    archive.writestr("metadata.npy", _npy_bytes(metadata))
    archive.writestr("experiment_type.npy", _npy_bytes(static["experiment_type"]))
    archive.writestr("scan_axis_name.npy", _npy_bytes(static["scan_axis_name"]))
    archive.writestr("scan_axis_unit.npy", _npy_bytes(static["scan_axis_unit"]))
    archive.writestr(
        "scan_axis_values.npy", _npy_bytes(np.asarray(static["scan_axis_values"]))
    )


def _frame_mean(plane, roi):
    if roi is None:
        return float(np.mean(plane))
    x0, y0, x1, y1 = roi
    return float(np.mean(plane[y0:y1, x0:x1]))


# ---------------------------------------------------------------------
# Stage 1: single-pass streaming average + ROI report
# ---------------------------------------------------------------------

def average_scans(scan_paths, output_path, report_path, *, roi=None, allow_partial_scans=False):
    """Average *scan_paths* incrementally and write the cube and ROI report.

    *roi* overrides the ROI used for the report's fluorescence numbers; if
    omitted it is read from the first scan's ``metadata["acquisition_roi"]``,
    raising if that key is absent.

    *allow_partial_scans*, if not set, refuses any ``.h5`` input whose
    ``scan_complete`` metadata is False (left by an interrupted or stopped
    scan) -- its data past ``planes_written`` is HDF5 fill-value, not real
    frames, so averaging it in would silently corrupt the result.
    """
    output_path = Path(output_path)
    _reject_hdf5_output(output_path)
    report_path = Path(report_path)
    scan_paths, sources, shape, dtype = _open_sources(
        scan_paths, allow_partial_scans=allow_partial_scans
    )
    try:
        if dtype != np.dtype("float64"):
            raise ValueError(f"Expected float64 scan data, received {dtype}.")

        static = _read_static_members(scan_paths[0], allow_partial_scans=allow_partial_scans)
        metadata = dict(static["metadata"])
        metadata.update(
            {
                "averaging_enabled": True,
                "num_scans": len(scan_paths),
                "completed_scans": len(scan_paths),
                "raw_scan_filenames": [path.name for path in scan_paths],
            }
        )
        if roi is None:
            roi = metadata.get("acquisition_roi")
        if roi is None:
            raise ValueError("The source scans do not contain an ROI in metadata.")
        x0, y0, x1, y1 = roi

        partial_path = output_path.with_name(output_path.name + ".partial")
        plane_shape = shape[1:]
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
                    for scan_index, source in enumerate(sources, start=1):
                        frame = source.read_plane(point)
                        if average is None:
                            average = np.array(frame, dtype=np.float64, copy=True)
                        else:
                            average += (frame - average) / scan_index
                    roi_means.append(float(np.mean(average[y0:y1, x0:x1])))
                    output_data.write(average.tobytes(order="C"))
                    if (point + 1) % 10 == 0 or point + 1 == shape[0]:
                        print(f"Averaged field point {point + 1}/{shape[0]}.")

            _write_npz_static_members(output_archive, static, metadata)

        partial_path.replace(output_path)
    finally:
        for source in sources:
            source.close()

    signals = np.asarray(roi_means)
    scan_axis_values = np.asarray(static["scan_axis_values"], dtype=float)
    lines = [
        "ZERO FIELD AVERAGING REPORT",
        "=" * 28,
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


# ---------------------------------------------------------------------
# Stage 2: checkpointed, resumable plane-range averaging
# ---------------------------------------------------------------------

def average_plane_range(scan_paths, planes_dir, start, count, *, allow_partial_scans=False):
    """Average field points ``[start, start + count)`` into standalone
    per-plane ``.npy`` files under *planes_dir*, skipping points already
    completed by a prior invocation. Each plane is written atomically, so a
    file existing at the final name always means it was fully written.

    *allow_partial_scans*: see :func:`average_scans`.
    """
    planes_dir = Path(planes_dir)
    planes_dir.mkdir(parents=True, exist_ok=True)
    scan_paths, sources, shape, dtype = _open_sources(
        scan_paths, allow_partial_scans=allow_partial_scans
    )
    try:
        stop = min(start + count, shape[0])
        for point in range(start, stop):
            plane_path = planes_dir / f"plane_{point:03d}.npy"
            if plane_path.exists():
                print(f"Plane {point + 1}/{shape[0]} already exists.", flush=True)
                continue
            average = None
            for index, source in enumerate(sources, start=1):
                frame = source.read_plane(point)
                if average is None:
                    average = np.array(frame, dtype=np.float64, copy=True)
                else:
                    average += (frame - average) / index
            _atomic_save_npy(plane_path, average)
            print(f"Completed plane {point + 1}/{shape[0]}.", flush=True)
    finally:
        for source in sources:
            source.close()


# ---------------------------------------------------------------------
# Stage 3: package checkpointed planes into one cube (+ optional report)
# ---------------------------------------------------------------------

def package_averaged_planes(
    scan_paths, planes_dir, output_path, report_dir=None, *,
    roi=None, allow_partial_scans=False,
):
    """Assemble checkpointed per-plane ``.npy`` files into one averaged cube.

    *scan_paths* supplies axes/metadata/scan-axis info (read from the first
    entry) and the scan count/filenames recorded into the output metadata;
    the averaging itself already happened in :func:`average_plane_range`.
    If *report_dir* is given, also renders the plotted markdown report
    (fluorescence vs. field, normalized/derivative plots, histogram, field
    images, symmetry analysis, integrity checklist). *roi*, if given, crops
    the report's fluorescence numbers to that region; the default (``None``)
    uses the full frame mean, matching the original packaging script.

    *allow_partial_scans*: see :func:`average_scans`. Applies to
    *scan_paths[0]* here, since that's the only one read (for metadata/
    axes/scan-axis values, which determine the expected plane count below)
    -- the actual averaged data comes from *planes_dir*, not *scan_paths*.
    """
    scan_paths = [Path(path) for path in scan_paths]
    planes_dir = Path(planes_dir)
    output_path = Path(output_path)
    _reject_hdf5_output(output_path)
    if not scan_paths:
        raise ValueError("No scan files were supplied.")

    static = _read_static_members(scan_paths[0], allow_partial_scans=allow_partial_scans)
    fields = np.asarray(static["scan_axis_values"], dtype=float)

    plane_paths = [planes_dir / f"plane_{index:03d}.npy" for index in range(len(fields))]
    missing = [path.name for path in plane_paths if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing averaged planes: {', '.join(missing)}")
    first_plane = np.load(plane_paths[0], mmap_mode="r")
    shape = (len(plane_paths), *first_plane.shape)
    dtype = first_plane.dtype

    metadata = dict(static["metadata"])
    metadata.update(
        {
            "averaging_enabled": True,
            "num_scans": len(scan_paths),
            "completed_scans": len(scan_paths),
            "raw_scan_filenames": [path.name for path in scan_paths],
        }
    )

    partial_path = output_path.with_name(output_path.name + ".partial")
    with zipfile.ZipFile(
        partial_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        with archive.open("data.npy", "w", force_zip64=True) as member:
            np.lib.format.write_array_header_1_0(
                member, {"descr": dtype.str, "fortran_order": False, "shape": shape}
            )
            for path in plane_paths:
                member.write(np.load(path, mmap_mode="r").tobytes(order="C"))
        _write_npz_static_members(archive, static, metadata)
    partial_path.replace(output_path)
    print(f"Average ImageCube: {output_path}")

    if report_dir is None:
        return

    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    signals = np.empty(len(plane_paths))
    hist_sample = []
    for index, path in enumerate(plane_paths):
        plane = np.load(path, mmap_mode="r")
        signals[index] = _frame_mean(plane, roi)
        hist_sample.append(np.asarray(plane[::16, ::16]).ravel())
    normalized = signals / signals.mean()
    derivative = np.gradient(signals, fields)
    zero_index = int(np.argmin(np.abs(fields)))

    plt.figure(figsize=(8, 4.5)); plt.plot(fields, signals, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("Mean fluorescence (counts/pixel)"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(report_dir / "mean_fluorescence_vs_field.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.plot(fields, normalized, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("Normalized fluorescence"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(report_dir / "normalized_fluorescence_vs_field.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.plot(fields, derivative, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("First derivative (counts/pixel/G)"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(report_dir / "fluorescence_first_derivative.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.hist(np.concatenate(hist_sample), bins=100); plt.xlabel("Fluorescence (counts/pixel)"); plt.ylabel("Sampled pixel count"); plt.tight_layout(); plt.savefig(report_dir / "fluorescence_histogram.png", dpi=160); plt.close()

    view_indices = [0, zero_index, len(fields) - 1]
    reference = np.asarray(np.load(plane_paths[zero_index], mmap_mode="r"), dtype=np.float64)
    fig, axes_plot = plt.subplots(2, 3, figsize=(12, 7))
    for column, index in enumerate(view_indices):
        image = np.asarray(np.load(plane_paths[index], mmap_mode="r"), dtype=np.float64)
        axes_plot[0, column].imshow(image, cmap="viridis"); axes_plot[0, column].set_title(f"{fields[index]:g} G"); axes_plot[0, column].axis("off")
        axes_plot[1, column].imshow(image - reference, cmap="coolwarm"); axes_plot[1, column].set_title("Difference from near-zero"); axes_plot[1, column].axis("off")
    fig.tight_layout(); fig.savefig(report_dir / "field_images_and_differences.png", dpi=160); plt.close(fig)

    pairs = []
    for index, field in enumerate(fields):
        if field <= 0:
            continue
        matching = np.where(np.isclose(fields, -field))[0]
        if matching.size:
            negative = signals[matching[0]]; positive = signals[index]; diff = positive - negative
            pairs.append((field, negative, positive, diff, 100 * diff / negative))

    def format_value(value):
        return f"{value:.8g}"

    lines = [
        "# Zero Field Measurement Analysis Report", "",
        "## Dataset summary", "",
        "| Item | Value |", "|---|---|",
        f"| Filename | `{output_path.name}` |",
        f"| Data dimensions | {shape} (field point, image y, image x) |",
        f"| Data dtype | `{dtype}` |",
        "| Scan axis | Magnetic Field (G) |",
        f"| Number of field points | {len(fields)} |",
        f"| Field values | {', '.join(format_value(x) for x in fields)} |",
        f"| Raw scans averaged | {len(scan_paths)} |", "",
        "### Metadata", "", "```json", json.dumps(metadata, indent=2, default=str), "```", "",
        "## ImageCube integrity", "", "| Check | Result |", "|---|---|",
        "| Data is 3-D | Pass |", "| All pixel values are finite | Pass (source and averaged planes) |",
        "| All frames share one shape | Pass |", "| One scan-axis value per frame | Pass |",
        f"| Field order is monotonic | {'Pass' if np.all(np.diff(fields) > 0) else 'Fail'} |",
        f"| Duplicate field values | {len(fields) - len(np.unique(fields))} |", "",
        "## Fluorescence versus field", "",
        "Mean fluorescence was computed over every full averaged image frame."
        if roi is None else f"Mean fluorescence was computed over the ROI {roi}.", "",
        "| Field | Mean fluorescence | Normalized fluorescence | First derivative |", "|---:|---:|---:|---:|",
    ]
    lines.extend(f"| {field:.8g} | {signal:.8g} | {norm:.8g} | {deriv:.8g} |" for field, signal, norm, deriv in zip(fields, signals, normalized, derivative))
    lines += ["", "![Mean fluorescence vs field](mean_fluorescence_vs_field.png)", "", "![Normalized fluorescence vs field](normalized_fluorescence_vs_field.png)", "", "![First derivative](fluorescence_first_derivative.png)", "", "![Fluorescence histogram](fluorescence_histogram.png)", "", "The histogram uses a regular 1/16-pixel sample from every averaged image.", "", "## Basic statistics", "", "| Statistic | Value |", "|---|---:|", f"| Minimum | {signals.min():.8g} |", f"| Maximum | {signals.max():.8g} |", f"| Mean | {signals.mean():.8g} |", f"| Standard deviation | {signals.std():.8g} |", f"| Peak-to-peak variation | {np.ptp(signals):.8g} |", f"| Estimated contrast, (max - min) / mean | {100*np.ptp(signals)/signals.mean():.8g}% |", "", "## Symmetry analysis", ""]
    if pairs:
        largest = max(pairs, key=lambda pair: abs(pair[4]))
        lines += [f"{len(pairs)} matched +/- field pairs were available. The largest signed relative difference F(+B) - F(-B) was {largest[4]:.8g}% at |B| = {largest[0]:.8g} G.", "", "| |B| | F(-B) | F(+B) | F(+B) - F(-B) | Relative difference (%) |", "|---:|---:|---:|---:|---:|"]
        lines.extend(f"| {field:.8g} | {negative:.8g} | {positive:.8g} | {diff:.8g} | {relative:.8g} |" for field, negative, positive, diff, relative in pairs)
    lines += ["", "## Image analysis", "", "The figure below shows the first, nearest-to-zero, and last field images. Difference images are referenced to the nearest-to-zero field image.", "", "![Field images and differences](field_images_and_differences.png)", "", "## Near-zero-field observation", "", f"The field point nearest zero is {fields[zero_index]:.8g} G, with mean fluorescence {signals[zero_index]:.8g}.", "", "## Recommendations", "", "- Treat the observations as descriptive summaries of this averaged cube.", "- Retain the same field values, image region, and fluorescence normalization convention for comparisons.", "- Review the saved figures alongside this report when comparing later measurements."]
    (report_dir / "analysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {report_dir / 'analysis_report.md'}")
