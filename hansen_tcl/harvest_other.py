import arcpy
import os
import sys

arcpy.env.overwriteOutput = True

# ---------------------------------------------------------------------
# 1. ENV & PATHS
# ---------------------------------------------------------------------
# Hardcoded tile list for Hansen (the 26 you found overlapping)
tile_list = [
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_130W.tif",
]

# NLCD reference raster
nlcd_raster = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"

# Output folder
output_dir = r"C:\GIS\Data\LEARN\Disturbances\Hansen\Processed"
os.makedirs(output_dir, exist_ok=True)

# ArcPy environment
arcpy.env.snapRaster = nlcd_raster
arcpy.env.extent = nlcd_raster
arcpy.env.cellSize = nlcd_raster
arcpy.env.outputCoordinateSystem = nlcd_raster

# Paths for mosaic / reprojected outputs
hansen_mosaic_raw = os.path.join(output_dir, "hansen_mosaic_raw.tif")
hansen_mosaic_reproj = os.path.join(output_dir, "hansen_mosaic_reproj.tif")

# Hansen year => 1..23 => 2001..2023
time_periods = {
    "2021_2023": [2021, 2022, 2023],
}

print(f"Hardcoded {len(tile_list)} overlapping Hansen tiles:")
for t in tile_list:
    print(t)

# ---------------------------------------------------------------------
# 2. MOSAIC TILES INTO A SINGLE RASTER
# ---------------------------------------------------------------------
print(f"\nMosaicking => {hansen_mosaic_raw}")

arcpy.management.MosaicToNewRaster(
    input_rasters=tile_list,
    output_location=os.path.dirname(hansen_mosaic_raw),
    raster_dataset_name_with_extension=os.path.basename(hansen_mosaic_raw),
    coordinate_system_for_the_raster="",
    pixel_type="8_BIT_UNSIGNED",
    cellsize="",
    number_of_bands="1",
    mosaic_method="LAST",
    mosaic_colormap_mode="FIRST"
)

# ---------------------------------------------------------------------
# 3. REPROJECT TO MATCH NLCD
# ---------------------------------------------------------------------
print(f"Reprojecting => {hansen_mosaic_reproj}")
arcpy.management.ProjectRaster(
    in_raster=hansen_mosaic_raw,
    out_raster=hansen_mosaic_reproj,
    out_coor_system=nlcd_raster,  # or arcpy.Describe(nlcd_raster).spatialReference
    resampling_type="NEAREST",
    cell_size=arcpy.Describe(nlcd_raster).meanCellWidth
)

# ---------------------------------------------------------------------
# 4. EXTRACT TIME PERIODS (0/1)
# ---------------------------------------------------------------------
print("\nExtracting inventory periods from reprojected mosaic...")
from arcpy.sa import Raster, Con

for period_name, years in time_periods.items():
    out_raster = os.path.join(output_dir, f"hansen_{period_name}.tif")
    print(f"  -> {period_name} => {out_raster}")

    # If [2021,2022,2023], that's contiguous => pixel=21..23
    low_val = (min(years) - 2001) + 1
    high_val = (max(years) - 2001) + 1

    # Con((h >= low_val) & (h <= high_val), 1, 0)
    h = Raster(hansen_mosaic_reproj)
    cond = (h >= low_val) & (h <= high_val)
    out_period = Con(cond, 1, 0)
    out_period.save(out_raster)

print("Hansen 'harvest/other' ArcPy processing completed successfully.")
