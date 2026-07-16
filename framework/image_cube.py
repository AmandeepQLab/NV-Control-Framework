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
    ):

        self.data = data

        self.axes = {} if axes is None else axes

        self.metadata = {} if metadata is None else metadata

        self.experiment_type = experiment_type

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
