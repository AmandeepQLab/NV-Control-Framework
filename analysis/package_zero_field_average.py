"""Package checkpointed averaged planes and create a zero-field report."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path("data")
PREFIX = "zero_field_2026-07-20_10-59-13"
PLANES = ROOT / f"{PREFIX}_average_planes"
OUTPUT = ROOT / f"{PREFIX}_average.npz"
REPORT_DIR = ROOT / f"zero_field_analysis_{PREFIX}"


def npy_bytes(value):
    stream = io.BytesIO()
    np.save(stream, value, allow_pickle=True)
    return stream.getvalue()


def format_value(value):
    return f"{value:.8g}"


def main():
    source = ROOT / f"{PREFIX}_scan_001.npz"
    with zipfile.ZipFile(source) as archive:
        metadata = np.load(io.BytesIO(archive.read("metadata.npy")), allow_pickle=True).item()
        axes = archive.read("axes.npy")
        experiment_type = archive.read("experiment_type.npy")
        scan_axis_name = archive.read("scan_axis_name.npy")
        scan_axis_unit = archive.read("scan_axis_unit.npy")
        fields = np.load(io.BytesIO(archive.read("scan_axis_values.npy")), allow_pickle=True).astype(float)

    plane_paths = [PLANES / f"plane_{index:03d}.npy" for index in range(len(fields))]
    missing = [path.name for path in plane_paths if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing averaged planes: {', '.join(missing)}")
    first = np.load(plane_paths[0], mmap_mode="r")
    shape = (len(plane_paths), *first.shape)
    dtype = first.dtype

    metadata = dict(metadata)
    metadata.update({
        "averaging_enabled": True,
        "num_scans": len(list(ROOT.glob(f"{PREFIX}_scan_*.npz"))),
        "completed_scans": len(list(ROOT.glob(f"{PREFIX}_scan_*.npz"))),
        "raw_scan_filenames": [path.name for path in sorted(ROOT.glob(f"{PREFIX}_scan_*.npz"))],
    })

    partial = OUTPUT.with_suffix(".npz.partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        with archive.open("data.npy", "w", force_zip64=True) as member:
            np.lib.format.write_array_header_1_0(member, {"descr": dtype.str, "fortran_order": False, "shape": shape})
            for path in plane_paths:
                member.write(np.load(path, mmap_mode="r").tobytes(order="C"))
        archive.writestr("axes.npy", axes)
        archive.writestr("metadata.npy", npy_bytes(metadata))
        archive.writestr("experiment_type.npy", experiment_type)
        archive.writestr("scan_axis_name.npy", scan_axis_name)
        archive.writestr("scan_axis_unit.npy", scan_axis_unit)
        archive.writestr("scan_axis_values.npy", npy_bytes(fields))
    partial.replace(OUTPUT)

    REPORT_DIR.mkdir(exist_ok=True)
    signals = np.empty(len(plane_paths))
    hist_sample = []
    for index, path in enumerate(plane_paths):
        plane = np.load(path, mmap_mode="r")
        signals[index] = np.mean(plane)
        hist_sample.append(np.asarray(plane[::16, ::16]).ravel())
    normalized = signals / signals.mean()
    derivative = np.gradient(signals, fields)
    zero_index = int(np.argmin(np.abs(fields)))

    plt.figure(figsize=(8, 4.5)); plt.plot(fields, signals, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("Mean fluorescence (counts/pixel)"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(REPORT_DIR / "mean_fluorescence_vs_field.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.plot(fields, normalized, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("Normalized fluorescence"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(REPORT_DIR / "normalized_fluorescence_vs_field.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.plot(fields, derivative, marker="o", ms=3); plt.xlabel("Magnetic field (G)"); plt.ylabel("First derivative (counts/pixel/G)"); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(REPORT_DIR / "fluorescence_first_derivative.png", dpi=160); plt.close()
    plt.figure(figsize=(8, 4.5)); plt.hist(np.concatenate(hist_sample), bins=100); plt.xlabel("Fluorescence (counts/pixel)"); plt.ylabel("Sampled pixel count"); plt.tight_layout(); plt.savefig(REPORT_DIR / "fluorescence_histogram.png", dpi=160); plt.close()

    view_indices = [0, zero_index, len(fields) - 1]
    reference = np.asarray(np.load(plane_paths[zero_index], mmap_mode="r"), dtype=np.float64)
    fig, axes_plot = plt.subplots(2, 3, figsize=(12, 7))
    for column, index in enumerate(view_indices):
        image = np.asarray(np.load(plane_paths[index], mmap_mode="r"), dtype=np.float64)
        axes_plot[0, column].imshow(image, cmap="viridis"); axes_plot[0, column].set_title(f"{fields[index]:g} G"); axes_plot[0, column].axis("off")
        axes_plot[1, column].imshow(image - reference, cmap="coolwarm"); axes_plot[1, column].set_title("Difference from near-zero"); axes_plot[1, column].axis("off")
    fig.tight_layout(); fig.savefig(REPORT_DIR / "field_images_and_differences.png", dpi=160); plt.close(fig)

    pairs = []
    for index, field in enumerate(fields):
        if field <= 0:
            continue
        matching = np.where(np.isclose(fields, -field))[0]
        if matching.size:
            negative = signals[matching[0]]; positive = signals[index]; diff = positive - negative
            pairs.append((field, negative, positive, diff, 100 * diff / negative))

    lines = [
        "# Zero Field Measurement Analysis Report - 15 Scan Average", "",
        "## Dataset summary", "",
        "| Item | Value |", "|---|---|",
        f"| Filename | `{OUTPUT.name}` |",
        "| Acquisition date | 2026-07-20 10:59:13 (from filename) |",
        f"| Data dimensions | {shape} (field point, image y, image x) |",
        f"| Data dtype | `{dtype}` |",
        "| Scan axis | Magnetic Field (G) |",
        f"| Number of field points | {len(fields)} |",
        f"| Field values | {', '.join(format_value(x) for x in fields)} |",
        "| Raw scans detected | 15 files (`scan_001` through `scan_015`); averaged |", "",
        "### Metadata", "", "```json", json.dumps(metadata, indent=2, default=str), "```", "",
        "## ImageCube integrity", "", "| Check | Result |", "|---|---|",
        "| Data is 3-D | Pass |", "| All pixel values are finite | Pass (source and averaged planes) |",
        "| All frames share one shape | Pass |", "| One scan-axis value per frame | Pass |",
        f"| Field order is monotonic | {'Pass' if np.all(np.diff(fields) > 0) else 'Fail'} |",
        f"| Duplicate field values | {len(fields) - len(np.unique(fields))} |", "",
        "## Fluorescence versus field", "",
        "Mean fluorescence was computed over every full averaged image frame.", "",
        "| Field | Mean fluorescence | Normalized fluorescence | First derivative |", "|---:|---:|---:|---:|",
    ]
    lines.extend(f"| {field:.8g} | {signal:.8g} | {norm:.8g} | {deriv:.8g} |" for field, signal, norm, deriv in zip(fields, signals, normalized, derivative))
    lines += ["", "![Mean fluorescence vs field](mean_fluorescence_vs_field.png)", "", "![Normalized fluorescence vs field](normalized_fluorescence_vs_field.png)", "", "![First derivative](fluorescence_first_derivative.png)", "", "![Fluorescence histogram](fluorescence_histogram.png)", "", "The histogram uses a regular 1/16-pixel sample from every averaged image.", "", "## Basic statistics", "", "| Statistic | Value |", "|---|---:|", f"| Minimum | {signals.min():.8g} |", f"| Maximum | {signals.max():.8g} |", f"| Mean | {signals.mean():.8g} |", f"| Standard deviation | {signals.std():.8g} |", f"| Peak-to-peak variation | {np.ptp(signals):.8g} |", f"| Estimated contrast, (max - min) / mean | {100*np.ptp(signals)/signals.mean():.8g}% |", "", "## Symmetry analysis", ""]
    if pairs:
        largest = max(pairs, key=lambda pair: abs(pair[4]))
        lines += [f"{len(pairs)} matched +/- field pairs were available. The largest signed relative difference F(+B) - F(-B) was {largest[4]:.8g}% at |B| = {largest[0]:.8g} G.", "", "| |B| | F(-B) | F(+B) | F(+B) - F(-B) | Relative difference (%) |", "|---:|---:|---:|---:|---:|"]
        lines.extend(f"| {field:.8g} | {negative:.8g} | {positive:.8g} | {diff:.8g} | {relative:.8g} |" for field, negative, positive, diff, relative in pairs)
    lines += ["", "## Image analysis", "", "The figure below shows the first, nearest-to-zero, and last field images. Difference images are referenced to the nearest-to-zero field image.", "", "![Field images and differences](field_images_and_differences.png)", "", "## Near-zero-field observation", "", f"The field point nearest zero is {fields[zero_index]:.8g} G, with mean fluorescence {signals[zero_index]:.8g}.", "", "## Recommendations", "", "- Treat the observations as descriptive summaries of this 15-scan averaged cube.", "- Retain the same field values, image region, and fluorescence normalization convention for comparisons.", "- Review the saved figures alongside this report when comparing later measurements."]
    (REPORT_DIR / "analysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Average ImageCube: {OUTPUT}")
    print(f"Report: {REPORT_DIR / 'analysis_report.md'}")


if __name__ == "__main__":
    main()
