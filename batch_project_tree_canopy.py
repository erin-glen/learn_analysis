#!/usr/bin/env python3
"""Batch project NLCD tree canopy rasters.

This script projects all GeoTIFFs in an input directory to match the
CRS and alignment of a target raster. The output rasters are written to
``<input_dir>_project``.

Example
-------
python batch_project_tree_canopy.py /path/to/rasters target.tif
"""

import os
import sys
import glob
import numpy as np

try:
    import rasterio
    from rasterio.warp import reproject, Resampling
except ImportError as e:
    sys.stderr.write("rasterio is required for this script. Install with 'pip install rasterio'\n")
    raise


def project_raster(src_path, target_profile, dst_transform, dst_crs, dst_width, dst_height, output_path):
    """Reproject a raster to match target profile."""
    with rasterio.open(src_path) as src:
        data = src.read()
        dest = np.empty(shape=(src.count, dst_height, dst_width), dtype=data.dtype)
        for band in range(src.count):
            reproject(
                source=data[band],
                destination=dest[band],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.nearest,
            )

        profile = target_profile.copy()
        profile.update({
            "count": src.count,
            "dtype": dest.dtype,
        })

        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(dest)


def main(input_dir, target_raster):
    input_dir = os.path.abspath(input_dir)
    out_dir = f"{input_dir}_project"
    os.makedirs(out_dir, exist_ok=True)

    with rasterio.open(target_raster) as tgt:
        dst_crs = tgt.crs
        dst_transform = tgt.transform
        dst_width = tgt.width
        dst_height = tgt.height
        target_profile = tgt.profile

    raster_paths = glob.glob(os.path.join(input_dir, "*.tif"))
    if not raster_paths:
        print("No .tif files found in input directory.")
        return

    for path in raster_paths:
        fname = os.path.basename(path)
        out_path = os.path.join(out_dir, fname)
        print(f"Projecting {fname} -> {out_path}")
        project_raster(path, target_profile, dst_transform, dst_crs, dst_width, dst_height, out_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python batch_project_tree_canopy.py <input_dir> <target_raster>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])

