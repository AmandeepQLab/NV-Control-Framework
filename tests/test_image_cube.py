"""
=====================================================
Test ImageCube
-----------------------------------------------------
Basic functionality test for the ImageCube class.

Author:
    Amandeep + ChatGPT
=====================================================
"""

import numpy as np

from framework.image_cube import ImageCube


def main():

    # -------------------------------------------------
    # Create test image
    # -------------------------------------------------

    image = np.random.rand(100, 100)

    cube = ImageCube(
        data=image,
        experiment_type="Test",
    )

    print(cube)
    print(f"Shape : {cube.shape}")
    print(f"Dimensions : {cube.ndim}")

    # -------------------------------------------------
    # Save
    # -------------------------------------------------

    cube.save("test_cube.npz")

    # -------------------------------------------------
    # Load
    # -------------------------------------------------

    cube2 = ImageCube.load("test_cube.npz")

    # -------------------------------------------------
    # Verify
    # -------------------------------------------------

    assert np.array_equal(cube.data, cube2.data)
    assert cube.axes == cube2.axes
    assert cube.metadata == cube2.metadata
    assert cube.experiment_type == cube2.experiment_type

    print("\n✓ ImageCube test passed.")


if __name__ == "__main__":
    main()