"""
=====================================================
Image Cube
-----------------------------------------------------
Standard container for widefield imaging data.
=====================================================
"""

import json
import zipfile
from pathlib import Path

import h5py
import numpy as np


# Datasets/attributes not backed by ``ImageCube`` fields (all bookkeeping
# below is derived from these seven names, kept in one place so the HDF5
# save/load pair can't drift apart from each other).
_HDF5_DATA_KEY = "data"
_HDF5_SCAN_AXIS_VALUES_KEY = "scan_axis_values"
_HDF5_METADATA_JSON_KEY = "metadata_json"
_HDF5_AXES_JSON_KEY = "axes_json"

# Name of the ``.npy`` member inside a legacy ``.npz`` archive that holds the
# data array. Kept separate from ``_HDF5_DATA_KEY`` so renaming the HDF5
# dataset can't silently break ``peek_shape``'s ``.npz`` reader — the two
# formats' on-disk names are independent even though they happen to match
# today.
_NPZ_DATA_MEMBER_NAME = "data"


def _decode_h5_string(value):
    """Normalize an h5py-returned string, which may arrive as bytes."""
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _is_hdf5_path(filename):
    return Path(filename).suffix.lower() in (".h5", ".hdf5")


class ImageCube:

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(
        self,
        data,
        axes=None,
        metadata=None,
        experiment_type="Unknown",
        scan_axis_name=None,
        scan_axis_unit=None,
        scan_axis_values=None,
    ):

        self.data = data

        self.axes = {} if axes is None else axes

        self.metadata = {} if metadata is None else metadata

        self.experiment_type = experiment_type

        # Generic scan-axis metadata.  The attributes are deliberately
        # separate from ``axes`` so legacy ImageCube callers remain valid.
        self.scan_axis_name = scan_axis_name
        self.scan_axis_unit = scan_axis_unit
        self.scan_axis_values = (
            [] if scan_axis_values is None else scan_axis_values
        )

    def add(self, frame):
        """Append one acquired frame along the leading scan dimension."""
        frame = np.asarray(frame)

        if self.data is None:
            self.data = frame[np.newaxis, ...]
            return

        # A legacy ImageCube may have been constructed from a single frame
        # rather than an empty acquisition buffer.
        if self.data.shape == frame.shape:
            self.data = np.stack((self.data, frame))
            return

        if self.data.shape[1:] != frame.shape:
            raise ValueError(
                "All ImageCube frames must have the same shape."
            )

        self.data = np.concatenate((self.data, frame[np.newaxis, ...]), axis=0)

    # =====================================================
    # DATA PROPERTIES
    # =====================================================

    @property
    def shape(self):

        return self.data.shape

    @property
    def ndim(self):

        return self.data.ndim

    # =====================================================
    # DISPLAY
    # =====================================================

    def show(self):

        import matplotlib.pyplot as plt

        if self.ndim == 2:

            plt.imshow(self.data)

        elif self.ndim == 3:

            plt.imshow(self.data[0])

        else:

            raise ValueError(
                "ImageCube data must be 2D or 3D to display."
            )

        plt.show()

    # =====================================================
    # SAVE
    # =====================================================

    def save(self, filename):
        """Save to HDF5 (``.h5``/``.hdf5``) or legacy ``.npz`` otherwise."""
        if self.data is None:
            raise ValueError("Cannot save an ImageCube with no data.")
        if _is_hdf5_path(filename):
            self._save_hdf5(filename)
        else:
            self._save_npz(filename)

    def _save_npz(self, filename):

        np.savez(
            filename,
            data=self.data,
            axes=np.array(self.axes, dtype=object),
            metadata=np.array(self.metadata, dtype=object),
            experiment_type=self.experiment_type,
            scan_axis_name=self.scan_axis_name,
            scan_axis_unit=self.scan_axis_unit,
            scan_axis_values=np.array(self.scan_axis_values, dtype=object),
        )

    def _save_hdf5(self, filename):
        data = np.asarray(self.data)

        # One full frame per chunk, matching plane-wise access: reading
        # plane i decompresses exactly chunk i, never its neighbors.  A 2-D
        # cube (a single frame, no scan dimension) has no "plane" to
        # subdivide, so the whole array is one chunk instead.
        if data.ndim >= 3:
            chunk_shape = (1,) + data.shape[1:]
        else:
            chunk_shape = data.shape
        if not chunk_shape or any(dim == 0 for dim in chunk_shape):
            chunk_shape = None

        with h5py.File(filename, "w") as handle:
            dataset_kwargs = {}
            if chunk_shape is not None:
                dataset_kwargs.update(
                    chunks=chunk_shape,
                    compression="gzip",
                    compression_opts=4,
                    shuffle=True,
                )
            handle.create_dataset(_HDF5_DATA_KEY, data=data, **dataset_kwargs)

            # ``np.asarray(None, dtype=np.float64)`` silently yields
            # ``array(nan)`` rather than raising, so a ``None`` cube would
            # round-trip as NaN instead of ``None`` without this sentinel.
            scan_axis_values_is_none = self.scan_axis_values is None
            scan_axis_values = np.asarray(
                [] if scan_axis_values_is_none else self.scan_axis_values,
                dtype=np.float64,
            )
            handle.create_dataset(_HDF5_SCAN_AXIS_VALUES_KEY, data=scan_axis_values)
            handle.attrs["scan_axis_values_is_none"] = scan_axis_values_is_none

            handle.create_dataset(
                _HDF5_METADATA_JSON_KEY,
                data=json.dumps(self.metadata),
                dtype=h5py.string_dtype(encoding="utf-8"),
            )
            handle.create_dataset(
                _HDF5_AXES_JSON_KEY,
                data=json.dumps(self.axes),
                dtype=h5py.string_dtype(encoding="utf-8"),
            )

            # h5py cannot store ``None`` in an attribute at all (raises
            # TypeError), so ``experiment_type`` needs the same is-none
            # sentinel already used for ``scan_axis_name``/``scan_axis_unit``.
            handle.attrs["experiment_type_is_none"] = self.experiment_type is None
            handle.attrs["experiment_type"] = self.experiment_type or ""
            handle.attrs["scan_axis_name_is_none"] = self.scan_axis_name is None
            handle.attrs["scan_axis_name"] = self.scan_axis_name or ""
            handle.attrs["scan_axis_unit_is_none"] = self.scan_axis_unit is None
            handle.attrs["scan_axis_unit"] = self.scan_axis_unit or ""

    # =====================================================
    # LOAD
    # =====================================================

    @staticmethod
    def load(filename):
        """Load from HDF5 (``.h5``/``.hdf5``) or legacy ``.npz`` otherwise."""
        if _is_hdf5_path(filename):
            return ImageCube._load_hdf5(filename)
        return ImageCube._load_npz(filename)

    @staticmethod
    def _load_npz(filename):

        with np.load(filename, allow_pickle=True) as saved:

            return ImageCube(
                data=saved["data"],
                axes=saved["axes"].item(),
                metadata=saved["metadata"].item(),
                experiment_type=saved["experiment_type"].item(),
                scan_axis_name=(
                    saved["scan_axis_name"].item()
                    if "scan_axis_name" in saved else None
                ),
                scan_axis_unit=(
                    saved["scan_axis_unit"].item()
                    if "scan_axis_unit" in saved else None
                ),
                scan_axis_values=(
                    saved["scan_axis_values"].tolist()
                    if "scan_axis_values" in saved else []
                ),
            )

    @staticmethod
    def _load_hdf5(filename):
        with h5py.File(filename, "r") as handle:
            data = handle[_HDF5_DATA_KEY][()]
            scan_axis_values = (
                None if handle.attrs.get("scan_axis_values_is_none", False)
                else handle[_HDF5_SCAN_AXIS_VALUES_KEY][()].tolist()
            )
            metadata = json.loads(
                _decode_h5_string(handle[_HDF5_METADATA_JSON_KEY][()])
            )
            axes = json.loads(_decode_h5_string(handle[_HDF5_AXES_JSON_KEY][()]))

            experiment_type = (
                None if handle.attrs.get("experiment_type_is_none", False)
                else _decode_h5_string(
                    handle.attrs.get("experiment_type", "Unknown")
                )
            )
            scan_axis_name = (
                None if handle.attrs.get("scan_axis_name_is_none", False)
                else _decode_h5_string(handle.attrs.get("scan_axis_name", ""))
            )
            scan_axis_unit = (
                None if handle.attrs.get("scan_axis_unit_is_none", False)
                else _decode_h5_string(handle.attrs.get("scan_axis_unit", ""))
            )

        return ImageCube(
            data=data,
            axes=axes,
            metadata=metadata,
            experiment_type=experiment_type,
            scan_axis_name=scan_axis_name,
            scan_axis_unit=scan_axis_unit,
            scan_axis_values=scan_axis_values,
        )

    # =====================================================
    # SHAPE / PLANE-LEVEL ACCESS (no full-cube load)
    # =====================================================

    @staticmethod
    def peek_shape(filename):
        """Return the on-disk data shape without loading any frame data.

        Supported for both HDF5 and ``.npz`` — the ``.npz`` path reads only
        the ``.npy`` array header from within the zip, the same technique
        ``analysis/average_zero_field_scans.py`` already uses to avoid
        materializing whole scans.
        """
        if _is_hdf5_path(filename):
            with h5py.File(filename, "r") as handle:
                return tuple(handle[_HDF5_DATA_KEY].shape)

        with zipfile.ZipFile(filename) as archive:
            with archive.open(f"{_NPZ_DATA_MEMBER_NAME}.npy") as member:
                version = np.lib.format.read_magic(member)
                if version != (1, 0):
                    raise ValueError(
                        f"Unsupported NPY version {version} in {filename!r}."
                    )
                shape, _, _ = np.lib.format.read_array_header_1_0(member)
        return tuple(shape)

    @staticmethod
    def load_plane(filename, index):
        """Return one frame (``data[index]``) without loading the full cube.

        HDF5 only.  ``.npz`` has no efficient single-plane read path through
        the public numpy API — ``np.load`` on a zip member materializes the
        whole array on first access, so a lazy read would require the same
        manual zip/byte-offset parsing ``analysis/`` scripts do, which is
        out of scope for ``ImageCube`` itself.
        """
        if not _is_hdf5_path(filename):
            raise NotImplementedError(
                "Plane-level reads are only supported for HDF5 (.h5/.hdf5) "
                "files; .npz has no efficient single-plane read path."
            )
        with h5py.File(filename, "r") as handle:
            return handle[_HDF5_DATA_KEY][index, ...]

    # =====================================================
    # STRING REPRESENTATION
    # =====================================================

    def __repr__(self):

        return (
            "ImageCube("
            f"experiment='{self.experiment_type}', "
            f"shape={self.shape}"
            ")"
        )
