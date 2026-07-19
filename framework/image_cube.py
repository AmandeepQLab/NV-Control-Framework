"""
=====================================================
Image Cube
-----------------------------------------------------
Standard container for widefield imaging data.
=====================================================
"""

import numpy as np


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

    # =====================================================
    # LOAD
    # =====================================================

    @staticmethod
    def load(filename):

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
