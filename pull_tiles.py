"""Utility script for listing Hansen tiles that overlap a target raster.

Example
-------
```
python pull_tiles.py
```
"""

import os
import glob
import sys
import rasterio

# ------------------------------------------------------------------
# CONFIGURATIONS
# ------------------------------------------------------------------
# Where your Hansen tiles live (e.g., "C:/GIS/Data/LEARN/Disturbances/Hansen")
hansen_tile_dir = r"C:\GIS\Data\LEARN\Disturbances\Hansen"

# Path to your target raster (e.g., NLCD), from which we read extent
target_raster_path = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"

# Optional: output text file listing the overlapping tiles
overlap_list_file = r"C:\GIS\Data\LEARN\Disturbances\Hansen\hansen_overlapping_tiles.txt"

# ------------------------------------------------------------------
# 1. READ TARGET RASTER BOUNDS
# ------------------------------------------------------------------
try:
    with rasterio.open(target_raster_path) as target_ds:
        target_bounds = target_ds.bounds  # (left, bottom, right, top)
        target_crs = target_ds.crs
        print(f"Target raster bounds: {target_bounds}")
except Exception as e:
    print(f"Error reading target raster: {e}")
    sys.exit(1)

# ------------------------------------------------------------------
# 2. HELPER: BOUNDING BOX OVERLAP FUNCTION
# ------------------------------------------------------------------
def overlap_bbox(bboxA, bboxB):
    """
    Return True if the bounding boxes overlap;
    each is (left, bottom, right, top).
    """
    Aleft, Abottom, Aright, Atop = bboxA
    Bleft, Bbottom, Bright, Btop = bboxB

    # if one box is to the left of the other
    if Aright < Bleft or Bright < Aleft:
        return False
    # if one box is below the other
    if Atop < Bbottom or Btop < Abottom:
        return False
    return True

# ------------------------------------------------------------------
# 3. LOOP THROUGH TILES, FIND OVERLAPPING
# ------------------------------------------------------------------
tile_paths = sorted(glob.glob(os.path.join(hansen_tile_dir, "*.tif")))
if not tile_paths:
    print(f"No .tif found in {hansen_tile_dir}. Exiting.")
    sys.exit(0)

overlapping_tiles = []
for tile_path in tile_paths:
    try:
        with rasterio.open(tile_path) as src:
            tile_bounds = src.bounds
            tile_crs = src.crs
            # Optionally check CRS mismatch. If tile_crs != target_crs,
            # you might reproject tile_bounds or skip if not relevant.
            # For simplicity, just compare bounding boxes in tile's own coords
            # if tile_crs == target_crs:
            if tile_crs != target_crs:
                # If you want to reproject tile_bounds to target_crs,
                # you'd use rasterio.warp.transform_bounds.
                from rasterio.warp import transform_bounds
                tile_bounds = transform_bounds(tile_crs, target_crs,
                                               tile_bounds.left, tile_bounds.bottom,
                                               tile_bounds.right, tile_bounds.top,
                                               densify_pts=21)

            # Now tile_bounds is in same CRS as target_bounds
            if overlap_bbox(tile_bounds, target_bounds):
                overlapping_tiles.append(tile_path)
    except Exception as e:
        print(f"Warning: could not read tile {tile_path}, skipping. Error: {e}")
        continue

# ------------------------------------------------------------------
# 4. OUTPUT THE RESULT
# ------------------------------------------------------------------
print(f"Found {len(overlapping_tiles)} overlapping Hansen tiles.")
for t in overlapping_tiles:
    print(t)

if overlap_list_file:
    try:
        with open(overlap_list_file, "w") as f:
            for tile in overlapping_tiles:
                f.write(tile + "\n")
        print(f"Tile list written to {overlap_list_file}")
    except Exception as e:
        print(f"Error writing tile list file: {e}")
