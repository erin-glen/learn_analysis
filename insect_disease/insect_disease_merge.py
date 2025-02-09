import arcpy
import os

arcpy.env.overwriteOutput = True
arcpy.CheckOutExtension("Spatial")

# ------------------------------------------------------------------
# 1. ENV & PATHS
# ------------------------------------------------------------------
# Input folder with per-region rasters (each is 0 or 5)
input_dir = r"C:\GIS\Data\LEARN\Disturbances\ADS\Processed"

# Final output folder
final_dir = r"C:\GIS\Data\LEARN\Disturbances\ADS\Final"
os.makedirs(final_dir, exist_ok=True)

# Example time periods (expand as needed)
time_periods = ["2021_2023"]

# Regions
regions = [1, 2, 3, 4, 5, 6, 8, 9]

# ArcPy environment from NLCD
nlcd_raster = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"
arcpy.env.snapRaster = nlcd_raster
arcpy.env.extent = nlcd_raster
arcpy.env.cellSize = nlcd_raster
arcpy.env.outputCoordinateSystem = nlcd_raster

# ------------------------------------------------------------------
# 2. MERGE PER-REGION RASTERS FOR EACH TIME PERIOD
# ------------------------------------------------------------------
for period in time_periods:
    out_name = f"insect_damage_{period}.tif"
    out_path = os.path.join(final_dir, out_name)

    # Collect the region rasters
    region_rasters = []
    for reg in regions:
        ras_path = os.path.join(input_dir, f"insect_damage_{reg}_{period}.tif")
        if arcpy.Exists(ras_path):
            region_rasters.append(ras_path)
        else:
            print(f"Warning: Missing {ras_path}")

    if not region_rasters:
        print(f"No insect/disease region rasters found for {period}, skipping.")
        continue

    print(f"Merging {len(region_rasters)} region rasters => {out_path}")

    # Mosaic the region-level rasters. They already have values {0,5}.
    # mosaic_method="MAXIMUM" => if overlaps, 5 overrides 0.
    # pixel_type="8_BIT_UNSIGNED" => final also is 0..255 range, so 0 or 5 is safe.
    arcpy.management.MosaicToNewRaster(
        input_rasters=region_rasters,
        output_location=final_dir,
        raster_dataset_name_with_extension=out_name,
        coordinate_system_for_the_raster="",
        pixel_type="8_BIT_UNSIGNED",
        cellsize="",
        number_of_bands="1",
        mosaic_method="MAXIMUM",
        mosaic_colormap_mode="FIRST"
    )

    print(f"Final mosaic saved => {out_path}")

arcpy.CheckInExtension("Spatial")
print("Insect/disease final combination done.")
